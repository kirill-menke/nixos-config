#!/usr/bin/env python3
"""Evening mode: a warm screen tint on a schedule, applied through hyprsunset,
with the screens turned down while it lasts: the built-in panel's backlight
through logind, external monitors' own brightness over DDC/CI.

`nightlight daemon` runs as a user service and owns the schedule: at the
start time it turns the tint on, at the end time off, and a manual toggle in
between holds until the next of those. Everything else edits the state file
and pokes the daemon, which then applies it:

  nightlight status       JSON for the waybar module on the bar's own screen
                          (WAYBAR_OUTPUT_NAME, which waybar sets for it)
  nightlight toggle       flip it now (holds until the next switch time)
  nightlight pause PID    neutral colours while PID lives (`play` on the TV)
  nightlight resume PID

The waybar module also shows keep awake, the display card's switch: an idle
inhibitor (hypridle honours those) that ends by itself after AWAKE_FOR. It
runs in a transient user unit of its own, AWAKE_UNIT, so that it outlives
the card and the bar, and the card stops it by that name:

  nightlight awake UNTIL  what the unit runs under systemd-inhibit: holds on
                          until UNTIL (epoch s) or until the unit is stopped

bar-popup imports this module for the display card.
"""

import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from itertools import pairwise

import osd  # backlight helpers (next to this file; PYTHONPATH when packaged)

STATE_HOME = (os.environ.get("XDG_STATE_HOME")
              or os.path.expanduser("~/.local/state"))
STATE = os.path.join(STATE_HOME, "nightlight.json")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
PIDFILE = os.path.join(RUNTIME, "nightlight.pid")
# Who paused evening mode: its PID and start time (see paused()).
PAUSE = os.path.join(RUNTIME, "nightlight-pause")
# Daytime brightness to put back, per screen (a monitor under its connector,
# the built-in panel under its backlight's name), the level each was taken
# down to, and while on the "dim" each is down to ("at"), kept apart from
# the settings so the card never writes over it.
DIMMED = os.path.join(STATE_HOME, "nightlight-dimmed.json")
DDC_CACHE = os.path.join(RUNTIME, "nightlight-ddc.json")  # also bar-popup's
# The level each DDC/CI monitor was last read at or set to, a file per
# connector (nightlight-ddc-levels/HDMI-A-1 holds [40, 100]): the bar on that
# monitor shows it, and a read over DDC takes 100 ms or more, too slow for
# the bar. Whoever reads or sets a level keeps it here: the daemon, the
# display card and the brightness keys (osd). A file each, so two writers
# never lose each other's monitor.
DDC_LEVELS = os.path.join(RUNTIME, "nightlight-ddc-levels")
# How evening mode's own writes to the DDC/CI monitors stand, for the display
# card's rows: {connector: "busy"} while it takes one down or back up, and
# "failed" once one didn't answer (it tries again each TICK); the rest are
# left out. Only the daemon writes it (Monitors).
DDC_STATUS = os.path.join(RUNTIME, "nightlight-ddc-status.json")
# Keep awake: its transient unit, and the note the holder in it keeps while
# it holds on (its PID and when it lets go).
AWAKE_UNIT = "keep-awake.service"
AWAKE = os.path.join(RUNTIME, "keep-awake.json")
AWAKE_FOR = 2 * 3600  # s; it ends by itself, so it can't be forgotten

DEFAULTS = {"on": False, "warmth": 3500, "auto": True,
            "start": "20:00", "end": "07:00",
            "dim": 50}  # % brightness while on; 100 leaves it alone
WARMTH_MIN, WARMTH_MAX = 2500, 5500  # K; hyprsunset takes 1000-20000

WAYBAR_SIGNAL = 8  # "signal" of custom/display in the waybar config
TICK = 30          # s between schedule checks
RETRY = 2          # s, first retry while hyprsunset is down; then doubling

ICON_DAY = "\U000f00df"    # md-brightness_6
ICON_NIGHT = "\U000f0594"  # md-weather_night, also paused (style.css greys it)
ICON_AWAKE = "\U000f0176"  # md-coffee, while keep awake lasts
# A tooltip's last line, the gestures a click doesn't show, as every bar
# module has it (config.jsonc): small, in the cards' subtext0. Waybar takes
# this module's tooltip as markup (no "escape").
HINT = "<span size='small' foreground='#a6adc8'>{}</span>"


def read_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Per process and thread: the daemon and the card can write one file at
    # once (both detect monitors right after one is plugged in), and the
    # daemon's monitors each have a thread (Monitors).
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)  # readers never see half a file


def load():
    state = dict(DEFAULTS)
    state.update({k: v for k, v in read_json(STATE, {}).items()
                  if k in DEFAULTS})
    return state


def save(state):
    write_json(STATE, state)


def poke():
    """Wake the daemon so it applies the state file now. A pidfile left by
    a daemon that was killed outright stays as it is: its PID is no longer
    the daemon, and the next daemon writes over the file (deleting it here
    could delete the one a daemon starting just then has written)."""
    try:
        with open(PIDFILE) as fh:
            pid = int(fh.read())
        if is_nightlight(pid, b"daemon"):
            os.kill(pid, signal.SIGUSR1)
    except (OSError, ValueError):
        pass


def update(**changes):
    state = load()
    state.update(changes)
    save(state)
    poke()
    return state


def minutes(hhmm):
    h, _, m = hhmm.partition(":")
    return (int(h) * 60 + int(m)) % 1440


def hhmm(mins):
    return f"{mins // 60 % 24:02d}:{mins % 60:02d}"


def in_evening(state, now=None):
    """Whether `now` (minutes since midnight) lies in [start, end); the
    window may wrap past midnight."""
    if now is None:
        t = time.localtime()
        now = t.tm_hour * 60 + t.tm_min
    start, end = minutes(state["start"]), minutes(state["end"])
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def is_nightlight(pid, cmd):
    """Whether process `pid` is `nightlight CMD …` (CMD as bytes), which is
    how a PID read from a file is checked before it is trusted: a process
    killed outright leaves its file behind, and the PID can since have gone
    to another process, which SIGUSR1 would end and which a liveness check
    would take for ours. The script (bin/.nightlight-wrapped for the
    daemon, lib/nightlight.py for the keep awake holder) comes right before
    CMD."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            argv = fh.read().split(b"\0")
    except OSError:
        return False
    return any(b"nightlight" in script and arg == cmd
               for script, arg in pairwise(argv))


def start_time(pid):
    """When process `pid` started (clock ticks since boot), or None if it
    doesn't exist. A PID is reused once its process is gone; a PID with its
    start time stays one process for the whole boot."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            # Field 22, counted after the command name, which can itself
            # hold spaces and parentheses.
            return int(fh.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def paused():
    """Whether the process that paused evening mode still runs. Any process
    can pause (the PC's `play` passes its shell's PID), so there is no
    command line to know it by; the start time noted when it paused tells
    it from a later process that got the same PID, as `play` can be killed
    before it resumes."""
    rec = read_json(PAUSE, {})
    try:
        pid, start = int(rec["pid"]), int(rec["start"])
    except (KeyError, TypeError, ValueError):
        return False
    return start_time(pid) == start


def run(*args, timeout=5):
    """stdout of a command, or None if it fails."""
    try:
        out = subprocess.run(tuple(map(str, args)), capture_output=True,
                             text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def hyprsunset(*args):
    """hyprsunset's reply to ARGS, as `hyprctl hyprsunset ARGS` prints it,
    or None if it isn't running. Sent over its socket as hyprctl sends it,
    rather than through hyprctl: the daemon asks every TICK, and each
    hyprctl took ~40 ms of CPU to start. hyprsunset reads one request per
    read() and keeps the connection for the next, so: one request per
    connection, and one recv for the reply (a few bytes, sent at once)."""
    path = os.path.join(RUNTIME, "hypr",
                        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
                        ".hyprsunset.sock")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(path)
            sock.sendall(" ".join(map(str, args)).encode())
            return sock.recv(1024).decode(errors="replace").strip()
    except OSError:
        return None


def monitor_names():
    """Names of the monitors Hyprland lights: `hyprctl -j monitors all`
    without the disabled ones, since plain `monitors` leaves out a mirror
    (the display card's Screens: Mirror), which still shows a picture.
    Asked over its socket (osd.hyprland); None if it can't be reached."""
    try:
        return {m["name"] for m in osd.hyprland("j/monitors all")
                if not m.get("disabled")}
    except (KeyError, TypeError):
        return None


def apply(state):
    """Bring hyprsunset in line with `state`; False if it can't be reached.
    Only sends what differs: every set is a colour-matrix commit."""
    identity = hyprsunset("identity", "get")
    if identity is None:
        return False
    if state["on"] and not paused():
        if identity != "false" or hyprsunset("temperature") != str(
                state["warmth"]):
            hyprsunset("temperature", state["warmth"])
    elif identity != "true":
        hyprsunset("identity")
    return True


# --- Keep awake: an idle inhibitor in a transient user unit -----------------

def clock(epoch):
    """Local 'HH:MM' of a point in time."""
    t = time.localtime(epoch)
    return hhmm(t.tm_hour * 60 + t.tm_min)


def awake_until():
    """When keep awake ends (epoch s), or None while it's off. Cheap enough
    for every run of the module: one small file and the holder's command
    line in /proc."""
    rec = read_json(AWAKE, {})
    try:
        until, pid = float(rec["until"]), int(rec["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    if until > time.time() and is_nightlight(pid, b"awake"):
        return until
    return None


def awake_command(until):
    """argv that turns keep awake on until `until` (epoch s). A unit of its
    own rather than a child of the card: waybar's unit (KillMode=mixed)
    takes the card's children down with it, so a bar restart would end it.
    systemd-run returns once the inhibitor is held, when the holder (a child
    of systemd-inhibit, hence NotifyAccess=all) reports ready, or failed.
    systemd-inhibit (systemd 261) runs its command without NOTIFY_SOCKET, so
    a shell hands the socket on as NIGHTLIGHT_NOTIFY first; without it the
    unit never got ready and systemd stopped it after its start timeout.
    polkit lets anyone take an idle inhibitor; should a policy ever ask for
    a password instead, it fails rather than popping up a prompt."""
    return ("systemd-run", "--user", "--quiet", "--collect",
            f"--unit={AWAKE_UNIT}",
            f"--description=Keep awake until {clock(until)}",
            "--service-type=notify", "--property=NotifyAccess=all",
            "sh", "-c", 'NIGHTLIGHT_NOTIFY="$NOTIFY_SOCKET" exec "$@"', "sh",
            "systemd-inhibit", "--no-ask-password", "--what=idle",
            "--who=Keep awake",
            f"--why=Display card switch, until {clock(until)}",
            sys.executable, os.path.abspath(__file__), "awake", str(until))


def notify_ready():
    """sd_notify(3) READY=1, by hand: the stdlib has no binding. The socket
    arrives as NIGHTLIGHT_NOTIFY (see awake_command)."""
    addr = (os.environ.get("NOTIFY_SOCKET")
            or os.environ.get("NIGHTLIGHT_NOTIFY", ""))
    if addr.startswith("@"):  # abstract namespace
        addr = "\0" + addr[1:]
    if addr:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"READY=1", addr)


def hold_awake(until):
    """`nightlight awake`, which systemd-inhibit starts once it holds the
    inhibitor and which keeps it held while it runs: note it in AWAKE, tell
    systemd and the bar, then wait for `until` on the wall clock or for the
    unit to be stopped (SIGTERM), and take the note back. The timer is an
    absolute CLOCK_REALTIME one, so time spent suspended counts: a lid
    opened after `until` lets go at once, where a sleep would add the time
    the notebook was asleep."""
    def stop(*_):
        # Once: the unit's stop and systemd-inhibit's death signal (it
        # SIGTERMs its child when it goes) both send one.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        sys.exit()
    signal.signal(signal.SIGTERM, stop)
    try:
        write_json(AWAKE, {"pid": os.getpid(), "until": until})
        notify_ready()
        refresh_waybar()
        timer = os.timerfd_create(time.CLOCK_REALTIME)
        os.timerfd_settime(timer, flags=os.TFD_TIMER_ABSTIME, initial=until)
        os.read(timer, 8)
    finally:
        try:
            os.remove(AWAKE)
        except FileNotFoundError:
            pass
        refresh_waybar()


# --- Brightness: monitors over DDC/CI, the built-in panel's backlight -------

def parse_detect(out):
    """{connector: i2c bus} from `ddcutil detect --terse`, e.g.
    {"DP-1": 7}; ddcutil names connectors like DRM, "card0-DP-1"."""
    buses = {}
    for block in out.split("\n\n"):
        bus = re.search(r"I2C bus:\s*/dev/i2c-(\d+)", block)
        conn = re.search(r"DRM connector:\s*card\d+-(\S+)", block)
        if bus and conn and "Invalid display" not in block:
            buses[conn.group(1)] = int(bus.group(1))
    return buses


def parse_brightness(out):
    """(level, max) from `ddcutil getvcp 10 --brief`."""
    m = re.search(r"VCP 10 C (\d+) (\d+)", out or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def connectors():
    """The DRM connectors with a screen plugged in, e.g. ["DP-1", "eDP-1"],
    named as ddcutil and Hyprland name them: a few small sysfs reads."""
    found = []
    try:
        names = os.listdir("/sys/class/drm")
    except OSError:
        return found
    for name in names:
        card, dash, conn = name.partition("-")  # card1-DP-1
        if not (dash and card.startswith("card")):
            continue  # the card itself, a render node
        try:
            with open(f"/sys/class/drm/{name}/status") as fh:
                if fh.read().strip() == "connected":
                    found.append(conn)
        except OSError:
            continue
    return sorted(found)


def cached_buses():
    """{connector: i2c bus} as the last `ddcutil detect` found them, or
    None if it ran with other screens plugged in, or never. An empty
    answer counts: no monitor here speaks DDC/CI, and asking again would
    take most of a second for nothing."""
    cache = read_json(DDC_CACHE, None)
    try:
        if cache["connected"] == connectors():
            return dict(cache["buses"])
    except (KeyError, TypeError, ValueError):
        pass
    return None


def save_buses(buses, plugged):
    """Keep a detect's answer, for the screens plugged in (connectors())
    as it began."""
    write_json(DDC_CACHE, {"connected": plugged, "buses": buses})


def ddc_buses():
    """{connector: i2c bus}; `ddcutil detect` takes most of a second, so
    the answer is kept until other screens are plugged in or a bus fails
    (dim() drops it then). A detect that failed is not kept."""
    buses = cached_buses()
    if buses is None:
        plugged = connectors()
        out = run("ddcutil", "detect", "--terse", timeout=20)
        buses = parse_detect(out or "")
        if out is not None:
            save_buses(buses, plugged)
    return buses


def ddc(bus, *args):
    return run("ddcutil", "--bus", bus, "--skip-ddc-checks", *args,
               timeout=10)


def ddc_get(bus):
    """(level, max) of the monitor on `bus`, or None."""
    return parse_brightness(ddc(bus, "getvcp", "10", "--brief"))


def ddc_set(bus, level):
    """Whether the monitor on `bus` took `level`."""
    return ddc(bus, "--noverify", "setvcp", "10", level) is not None


def ddc_level(conn):
    """(level, max) the monitor on connector `conn` was last read at or
    set to over DDC/CI (DDC_LEVELS), or None."""
    try:
        level, top = read_json(os.path.join(DDC_LEVELS, conn), None)
        return (int(level), int(top)) if int(top) > 0 else None
    except (TypeError, ValueError):
        return None


def note_ddc_level(conn, level, top):
    """Keep the level of the monitor on connector `conn` for the bar on it
    (ddc_level), after a DDC/CI read or write that worked; the caller then
    has the bar re-read it (refresh_waybar). One that can't be kept only
    costs that bar its %."""
    try:
        write_json(os.path.join(DDC_LEVELS, conn), [level, top])
    except OSError:
        pass


def ddc_status():
    """{connector: "busy" | "failed"}: the DDC/CI monitors evening mode is
    taking down or back up now, or that didn't answer it (DDC_STATUS)."""
    status = read_json(DDC_STATUS, {})
    return status if isinstance(status, dict) else {}


def ddc_read(conn, bus):
    """ddc_get(bus), noted for the bar on the monitor."""
    now = ddc_get(bus)
    if now:
        note_ddc_level(conn, *now)
    return now


def ddc_write(conn, bus, level):
    """ddc_set(bus, level), noted for the bar on the monitor with the max
    that the read before it noted."""
    if not ddc_set(bus, level):
        return False
    now = ddc_level(conn)
    if now:
        note_ddc_level(conn, level, now[1])
    return True


def dim_screen(state, saved, down, name, read, write, floor=0):
    """One screen's turn in evening mode (dim_turn()). Coming on: remember
    its level in `saved` and take it down to state["dim"] % of its range,
    never up, and not under `floor` on the way down, noting in `down` the
    level it is left at. Already down, it goes to a new state["dim"] the
    same way, from the level remembered. Going off: put the remembered
    level back, unless the screen was turned up by hand meanwhile (it reads
    more than the level noted in `down`): that level stays. One turned
    further down for the night, even to the floor, gets its daytime level
    back. read() gives (level, max) or None, write(level) whether it took.
    False if the screen didn't answer and needs another try."""
    now = read()
    if not now:
        return False
    level, top = now
    if state["on"]:
        day = saved.get(name, level)
        target = min(day, max(floor, round(top * state["dim"] / 100)))
        if target != level and not write(target):
            return False
        saved[name], down[name] = day, target
    # A record from before `down` was kept has no level to compare: put
    # the daytime one back, as then.
    elif level <= down.get(name, level) and not write(saved[name]):
        return False
    else:
        del saved[name]
        down.pop(name, None)
    return True


# The daemon's threads take turns at DIMMED: the panel's in its loop, each
# monitor's in a thread of its own (Monitors).
RECORD = threading.Lock()


def record():
    """DIMMED, with the "dim" each screen is down to under "at"; a record
    from before that was kept per screen has one "dim" for all."""
    rec = read_json(DIMMED, {})
    if not isinstance(rec, dict):
        return {}
    if "dim" in rec:
        dim = rec.pop("dim")
        rec.setdefault("at", dict.fromkeys(rec.get("levels", {}), dim))
    return rec


def needs(rec, name, state):
    """Whether screen `name` has a turn to take for `state`, by the record
    `rec`: down, or down to a new "dim", while on; back up while off."""
    levels = rec.get("levels", {})
    if not state["on"]:
        return name in levels
    return name not in levels or rec.get("at", {}).get(name) != state["dim"]


def dim_turn(state, name, read, write, floor=0):
    """Screen `name`'s turn (dim_screen()) on its entries in DIMMED, which
    then go back in among the other screens' as they stand by then: those
    take their turns meanwhile, in other threads. False if it didn't
    answer."""
    with RECORD:
        rec = record()
    saved, down = dict(rec.get("levels", {})), dict(rec.get("down", {}))
    if not dim_screen(state, saved, down, name, read, write, floor):
        return False
    mine = {"levels": saved, "down": down,
            "at": {name: state["dim"]} if state["on"] else {}}
    with RECORD:
        rec = record()
        for key, entries in mine.items():
            rec[key] = {n: v for n, v in rec.get(key, {}).items() if n != name}
            if name in entries:
                rec[key][name] = entries[name]
        write_json(DIMMED, rec)  # bar-popup re-reads the sliders on this
    return True


def dim_panel(state):
    """The built-in panel's turn, in the daemon's loop: one logind call.
    It counts with the lid shut too (the panel disabled): the kernel keeps
    a dark panel's level and lights it up at that level, and as it can't
    be unplugged, its daytime level is never given up like a vanished
    monitor's. False if it needs another try."""
    panel = osd.backlight()
    if not panel:
        return True
    name, level, top = panel
    with RECORD:
        due = needs(record(), name, state)
    return not due or dim_turn(
        state, name, lambda: (level, top),
        lambda raw: raw == level or osd.logind_brightness(
            "backlight", name, raw),
        osd.backlight_floor(top))


class Monitors:
    """Evening mode's DDC/CI monitors, apart from the daemon's loop. A
    ddcutil get or set takes up to 10 s to fail on a monitor that doesn't
    answer (in standby, on another input), a detect up to 20 s, and with
    the daemon waiting on them a toggle, a warmth or a "dim" from the card
    waited too. want(state) returns at once instead, and a thread of its
    own scans: the bus numbers (ddc_buses(), detected afresh after other
    screens are plugged in or a monitor didn't answer), and Hyprland's lit
    monitors, only asked with a known monitor plugged in (undocked the
    scan asks nothing). Then each monitor has its turn in a thread of its
    own (Monitor), so one that doesn't answer holds up no other, and the
    newest state asked for meanwhile replaces any waiting, as the card's
    cardkit.Latest does: the last one lands. The turns stand in DDC_STATUS
    for the card; one that sets or reads a level calls wake(), so the
    daemon shows it on the bars."""

    def __init__(self, wake):
        self.wake = wake
        self.cond = threading.Condition()
        self.state = None   # the newest state asked for, not scanned yet
        self.busy = False   # a scan waiting or under way
        self.monitors = {}  # connector -> its Monitor
        self.status = {}    # connector -> "busy" | "failed", as DDC_STATUS
        write_json(DDC_STATUS, {})  # none left from a daemon before
        threading.Thread(target=self._scans, daemon=True).start()

    def want(self, state):
        with self.cond:
            self.state, self.busy = dict(state), True
            self.cond.notify()

    def note(self, conn, status):
        """Monitor `conn`'s turn as DDC_STATUS has it: "busy", "failed" or
        None for neither."""
        with self.cond:
            if self.status.get(conn) == status:
                return
            if status is None:
                del self.status[conn]
            else:
                self.status[conn] = status
            write_json(DDC_STATUS, self.status)

    def _scans(self):
        while True:
            with self.cond:
                while self.state is None:
                    self.busy = False
                    self.cond.wait()
                state, self.state = self.state, None
            self.scan(state)

    def scan(self, state):
        # Detected afresh only after a screen comes or goes, so a monitor
        # plugged in now is found by the next tick.
        fresh = cached_buses() is None
        buses = ddc_buses()
        with RECORD:
            rec = record()
        # Every monitor that has ever answered, so one asleep right now (and
        # missing from a fresh detect) still counts.
        known = set(rec.get("known", [])) | set(buses)
        # Hyprland's lit monitors only matter for a known monitor that is
        # plugged in; undocked, sysfs says none is and the scan asks nothing
        # (an empty sysfs answer, as from a driver that lists no connectors,
        # doesn't count as "none").
        plugged = connectors()
        if known and (not plugged or known.intersection(plugged)):
            lit = monitor_names()
            if lit is None:
                return  # Hyprland is away: the next tick
        else:
            lit = set()
        targets = lit & known
        # Just plugged in, or found again: the bar on each monitor shows its
        # level (status()), which may have changed while it was away, so
        # each one detected reads it once.
        for conn in sorted(targets | (buses.keys() if fresh else set())):
            if conn not in self.monitors:
                self.monitors[conn] = Monitor(self, conn)
            self.monitors[conn].want(state, buses.get(conn), read=fresh)
        with RECORD:
            rec = record()
            new = dict(rec, known=sorted(known | set(rec.get("known", []))))
            if not state["on"] and not targets & rec.get("levels", {}).keys():
                # Every monitor here is back up: the ones left are unplugged,
                # and there is nothing to put back on them.
                for key in ("levels", "down", "at"):
                    new[key] = {n: v for n, v in rec.get(key, {}).items()
                                if n not in known}
            if new != rec:
                write_json(DIMMED, new)
        if new.get("known") != rec.get("known"):
            self.wake()  # the bars' tooltips: a screen to dim


class Monitor:
    """A DDC/CI monitor's thread in Monitors: it takes the monitor to the
    newest state asked for, one ddcutil call at a time. A turn that doesn't
    answer marks it failed in DDC_STATUS and has the buses detected afresh;
    the same state is tried again a TICK later, a new one at once."""

    def __init__(self, owner, conn):
        self.owner, self.conn = owner, conn
        self.cond = threading.Condition()
        self.job = None     # (state, bus, read): the newest asked for
        self.busy = False   # a job waiting or a turn under way
        self.missed = None  # ((on, dim), when) of the last turn unanswered
        threading.Thread(target=self._turns, daemon=True).start()

    def want(self, state, bus, read=False):
        """Take the monitor on `bus` (None: not detected) to `state`, and
        with `read` read its level for the bar even if there is no turn."""
        with self.cond:
            read = read or (self.job is not None and self.job[2])
            self.job, self.busy = (state, bus, read), True
            self.cond.notify()

    def _turns(self):
        while True:
            with self.cond:
                while self.job is None:
                    self.busy = False
                    self.cond.wait()
                (state, bus, read), self.job = self.job, None
            self.turn(state, bus, read)

    def turn(self, state, bus, read):
        conn, goal = self.conn, (state["on"], state["dim"])
        with RECORD:
            due = needs(record(), conn, state)
        again = self.missed is not None and self.missed[0] == goal
        if due and again and time.monotonic() - self.missed[1] < TICK:
            return  # it didn't answer that a moment ago: still "failed"
        before = ddc_level(conn)
        if not due:
            # Nothing (left) to do for this state, so an earlier miss no
            # longer matters: evening mode has moved on since.
            self.missed = None
            self.settled()
            if read and bus is not None:
                ddc_read(conn, bus)
        else:
            if not again:  # a retry stays "failed" until it takes
                self.owner.note(conn, "busy")
            started = time.monotonic()
            if bus is not None and dim_turn(state, conn,
                                            partial(ddc_read, conn, bus),
                                            partial(ddc_write, conn, bus)):
                self.missed = None
                self.settled()
            else:
                self.missed = (goal, started)
                self.owner.note(conn, "failed")
                try:
                    os.remove(DDC_CACHE)  # detect afresh next time
                except OSError:
                    pass
        if ddc_level(conn) != before:
            self.owner.wake()

    def settled(self):
        """Neither busy nor failed, in DDC_STATUS: unless a newer job waits,
        whose turn says (no flicker while a "dim" drag goes on)."""
        with self.cond:
            if self.job is not None:
                return
        self.owner.note(self.conn, None)


def dim(state, monitors):
    """Evening brightness. Going into evening mode each screen's level is
    remembered and taken down to state["dim"] % (never up); coming out the
    remembered level is put back, unless the screen was turned up in
    between, which then stays. A new state["dim"] while on (the display
    card's slider) takes the screens already down to it. Tracked per
    screen (DIMMED), so one that is asleep, behind a stale bus number or
    refused for now simply has its turn again on the next tick. The
    built-in panel takes one logind call and has its turn here; the
    monitors, over DDC/CI, have theirs in `monitors`' threads (Monitors),
    so this never waits for them. False while the panel is left."""
    done = dim_panel(state)
    monitors.want(state)
    return done


def dimmable():
    """Whether evening mode has a screen to turn down: the built-in panel,
    or a monitor that has answered over DDC/CI."""
    return bool(osd.backlight() or read_json(DIMMED, {}).get("known")
                or cached_buses())


def screen_level(output):
    """(%, what the tooltip calls it) of the screen named `output`, as
    waybar and Hyprland name it, or None where there is none to show: the
    built-in panel's backlight, also while it is dark (screens blanked), so
    the module keeps its width when it lights up again; another monitor's
    level as last read or set over DDC/CI (ddc_level). No output (`nightlight
    status` run by hand): the built-in panel's."""
    if output is None or osd.is_builtin(output):
        panel = osd.backlight()
        if panel:
            return round(100 * panel[1] / panel[2]), "Built-in screen"
        return None
    now = ddc_level(output)
    if now:
        return round(100 * now[0] / now[1]), "Monitor"
    return None


def status(state, output=None):
    """The waybar module on the bar on screen `output` (None: no bar's):
    icon, that screen's level, CSS classes and tooltip. While keep awake
    lasts, a coffee cup stands in for the sun or moon and the tooltip
    starts with until when."""
    if state["auto"]:
        when = (f"until {state['end']}" if in_evening(state)
                else f"from {state['start']}")
        plan = f"\nSchedule {state['start']}–{state['end']}"
    else:
        when, plan = "", "\nSchedule off"
    if state["dim"] < 100 and dimmable():
        plan += f"\nScreens dimmed to at most {state['dim']}% while on"
    if state["on"] and paused():
        cls, text = "paused", "Evening mode paused while a film plays"
    elif state["on"]:
        cls, text = "on", f"Evening mode on · {state['warmth']} K"
        if state["auto"] and in_evening(state):
            text += f" · {when}"
    else:
        cls, text = "off", "Evening mode off"
        if state["auto"] and not in_evening(state):
            text += f" · {when}"
    # The level of the bar's own screen after the glyph, as the volume
    # shows its own: on the laptop's bar the panel's, on a monitor's bar
    # that monitor's. Without one (a monitor that doesn't answer over
    # DDC/CI) the glyph stands alone.
    level, classes = "", [cls]
    shown = screen_level(output)
    if shown:
        pct, what = shown
        level = f" {pct}%"
        text += f"\n{what} {pct}%"
        classes.append("level")  # style.css: as wide as at 100%
    # The moon while the tint is on, and paused too, where style.css greys
    # it: then it stands apart from the sun of evening mode off.
    icon = ICON_DAY if cls == "off" else ICON_NIGHT
    awake = awake_until()
    if awake:
        icon, text = ICON_AWAKE, f"Keeping awake until {clock(awake)}\n{text}"
    # Last, dim: what the module does beyond its click (config.jsonc);
    # scrolling only where the bar's screen has a level for it to step.
    hint = "Right-click: evening mode" + (" · Scroll: brightness"
                                          if shown else "")
    return {"text": icon + level, "class": classes,
            "tooltip": f"{text}{plan}\n{HINT.format(hint)}"}


def show(state, shown):
    """What the bars' modules show now; they re-run the module if it
    differs from `shown`, what they showed last. That is the module (as
    on a bar with no level of its own) and the level of each screen
    plugged in, which is all that differs from one bar to another."""
    bar = (status(state), {conn: screen_level(conn)
                           for conn in connectors()})
    if bar != shown:
        refresh_waybar()
    return bar


def catches(pid, signum):
    """Whether process `pid` has a handler for `signum` (SigCgt mask)."""
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("SigCgt:"):
                    return bool(int(line.split()[1], 16) >> (signum - 1) & 1)
    except (OSError, ValueError):
        pass
    return False


def refresh_waybar():
    """Re-run the module's exec. Real-time signals kill a process that has
    no handler, so only waybar, matched by its exact process name, and only
    once it catches the signal: a bar that is still starting up (it starts
    together with this daemon) runs every exec by itself anyway."""
    sig = signal.SIGRTMIN + WAYBAR_SIGNAL
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/comm") as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if name in ("waybar", ".waybar-wrapped") and catches(pid, sig):
            try:
                os.kill(int(pid), sig)
            except OSError:
                pass


def fail(args):
    """threading.excepthook in the daemon: a thread that fails takes the
    daemon with it, as a failure in its loop does, and systemd starts it
    again (Restart=on-failure), rather than the monitors staying undimmed
    for the rest of the session."""
    threading.__excepthook__(args)  # the traceback, into the journal
    os._exit(1)


def daemon():
    with open(PIDFILE, "w") as fh:
        fh.write(str(os.getpid()))
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
    threading.excepthook = fail
    # Started after the block, which their threads take over: a poke, their
    # own included, is only ever taken by the wait below.
    monitors = Monitors(lambda: os.kill(os.getpid(), signal.SIGUSR1))
    last_phase = last_auto = last_bar = None
    retry = RETRY
    while True:
        state = load()
        phase = in_evening(state)
        # A switch time passed, or the schedule was just turned on or moved:
        # follow it. Between switch times a manual toggle holds.
        if state["auto"] and (phase != last_phase or not last_auto):
            if state["on"] != phase:
                state["on"] = phase
                save(state)
        last_phase, last_auto = phase, state["auto"]
        reached = apply(state)
        last_bar = show(state, last_bar)
        dim(state, monitors)  # the monitors in their own threads
        last_bar = show(state, last_bar)  # the panel's level dim() left
        signal.sigtimedwait({signal.SIGUSR1}, TICK if reached else retry)
        # hyprsunset down: starting with the session or restarting after a
        # crash, so soon at first; then less often, up to every TICK, as it
        # may not come back (not installed).
        retry = RETRY if reached else min(2 * retry, TICK)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "daemon":
        daemon()
    elif cmd == "status":
        print(json.dumps(status(load(), os.environ.get("WAYBAR_OUTPUT_NAME"))))
    elif cmd == "toggle":
        update(on=not load()["on"])
    elif cmd == "pause" and len(argv) > 2 and argv[2].isdigit():
        pid = int(argv[2])
        write_json(PAUSE, {"pid": pid, "start": start_time(pid)})
        poke()
    elif cmd == "resume" and len(argv) > 2 and argv[2].isdigit():
        try:
            if read_json(PAUSE, {})["pid"] == int(argv[2]):
                os.remove(PAUSE)
        except (KeyError, TypeError, OSError):
            pass
        poke()
    elif cmd == "awake" and len(argv) > 2:
        hold_awake(float(argv[2]))
    else:
        print("usage: nightlight daemon|status|toggle|pause PID|resume PID"
              "|awake UNTIL", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
