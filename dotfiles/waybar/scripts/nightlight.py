#!/usr/bin/env python3
"""Evening mode: a warm screen tint on a schedule, applied through hyprsunset,
with the monitors' own brightness turned down over DDC/CI while it lasts.

`nightlight daemon` runs as a user service and owns the schedule: at the
start time it turns the tint on, at the end time off, and a manual toggle in
between holds until the next of those. Everything else edits the state file
and pokes the daemon, which then applies it:

  nightlight status       JSON for the waybar module
  nightlight toggle       flip it now (holds until the next switch time)
  nightlight pause PID    neutral colours while PID lives (`play` on the TV)
  nightlight resume PID

bar-popup imports this module for the display card.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time

STATE_HOME = (os.environ.get("XDG_STATE_HOME")
              or os.path.expanduser("~/.local/state"))
STATE = os.path.join(STATE_HOME, "nightlight.json")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
PIDFILE = os.path.join(RUNTIME, "nightlight.pid")
PAUSE = os.path.join(RUNTIME, "nightlight-pause")  # holds the pausing PID
# Daytime brightness to put back, kept apart from the settings so the card
# never writes over it.
DIMMED = os.path.join(STATE_HOME, "nightlight-dimmed.json")
DDC_CACHE = os.path.join(RUNTIME, "nightlight-ddc.json")  # also bar-popup's

DEFAULTS = {"on": False, "warmth": 3500, "auto": True,
            "start": "20:00", "end": "07:00",
            "dim": 50}  # % brightness while on; 100 leaves it alone
WARMTH_MIN, WARMTH_MAX = 2500, 5500  # K; hyprsunset takes 1000-20000

WAYBAR_SIGNAL = 8  # "signal" of custom/display in the waybar config
TICK = 30          # s between schedule checks
RETRY = 2          # s, while hyprsunset is not up yet

ICON_DAY = "\U000f00df"    # md-brightness_6
ICON_NIGHT = "\U000f0594"  # md-weather_night


def read_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
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
    """Wake the daemon so it applies the state file now."""
    try:
        with open(PIDFILE) as fh:
            os.kill(int(fh.read()), signal.SIGUSR1)
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


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def paused():
    try:
        with open(PAUSE) as fh:
            return alive(int(fh.read()))
    except (OSError, ValueError):
        return False


def run(*args, timeout=5):
    """stdout of a command, or None if it fails."""
    try:
        out = subprocess.run(tuple(map(str, args)), capture_output=True,
                             text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def hyprsunset(*args):
    """Reply of `hyprctl hyprsunset ARGS`, or None if it isn't running."""
    out = run("hyprctl", "hyprsunset", *args)
    return None if out is None else out.strip()


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


# --- Brightness over DDC/CI ------------------------------------------------

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


def ddc_buses():
    """{connector: i2c bus}; `ddcutil detect` takes most of a second, so
    the answer is kept for the session and dropped when a bus fails."""
    buses = read_json(DDC_CACHE, None)
    if not buses:
        buses = parse_detect(run("ddcutil", "detect", "--terse",
                                 timeout=20) or "")
        write_json(DDC_CACHE, buses)
    return buses


def ddc(bus, *args):
    return run("ddcutil", "--bus", bus, "--skip-ddc-checks", *args,
               timeout=10)


def dim(state):
    """Evening brightness. Going into evening mode each monitor's level is
    remembered and taken down to state["dim"] % (never up); coming out it
    is put back. Tracked per monitor, so one that is asleep or behind a
    stale bus number is simply tried again on the next tick. Only runs
    ddcutil when a monitor is left to do; False while one is."""
    rec = read_json(DIMMED, {})
    saved = dict(rec.get("levels", {}))
    try:
        enabled = {m["name"] for m in json.loads(
            run("hyprctl", "-j", "monitors") or "")}
    except (ValueError, KeyError, TypeError):
        return False
    if "known" not in rec:
        ddc_buses()  # first run: find out which monitors speak DDC/CI
    # Every monitor that has ever answered, so one asleep right now (and
    # missing from a fresh detect) still counts.
    known = set(rec.get("known", [])) | set(read_json(DDC_CACHE, None) or ())
    targets = enabled & known
    todo = sorted(targets - saved.keys() if state["on"]
                  else targets & saved.keys())
    left = False
    if todo:
        buses = ddc_buses()
        for conn in todo:
            bus = buses.get(conn)
            if state["on"]:
                now = bus is not None and parse_brightness(
                    ddc(bus, "getvcp", "10", "--brief"))
                target = now and min(now[0],
                                     round(now[1] * state["dim"] / 100))
                if now and ddc(bus, "--noverify", "setvcp", "10",
                               target) is not None:
                    saved[conn] = now[0]
                    continue
            elif bus is not None and ddc(bus, "--noverify", "setvcp", "10",
                                         saved[conn]) is not None:
                del saved[conn]
                continue
            left = True
        if left:
            try:
                os.remove(DDC_CACHE)  # detect afresh next time
            except OSError:
                pass
    if not state["on"] and not left:
        saved = {}  # whatever is left is unplugged; nothing to restore
    new = {"levels": saved, "known": sorted(known)}
    if new != rec:
        write_json(DIMMED, new)  # bar-popup re-reads the sliders on this
    return not left


def status(state):
    """The waybar module: icon, CSS class and tooltip."""
    if state["auto"]:
        when = (f"until {state['end']}" if in_evening(state)
                else f"from {state['start']}")
        plan = f"\nSchedule {state['start']}–{state['end']}"
    else:
        when, plan = "", "\nSchedule off"
    if state["dim"] < 100:
        plan += f"\nScreens at {state['dim']}% brightness while on"
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
    return {"text": ICON_NIGHT if cls == "on" else ICON_DAY,
            "class": cls, "tooltip": text + plan}


def refresh_waybar():
    """Re-run the module's exec. Real-time signals kill a process that has
    no handler, so match waybar by its exact process name."""
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/comm") as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if name in ("waybar", ".waybar-wrapped"):
            try:
                os.kill(int(pid), signal.SIGRTMIN + WAYBAR_SIGNAL)
            except OSError:
                pass


def daemon():
    with open(PIDFILE, "w") as fh:
        fh.write(str(os.getpid()))
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
    last_phase = last_auto = last_bar = None
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
        bar = status(state)
        if bar != last_bar:
            refresh_waybar()
            last_bar = bar
        dim(state)  # last: ddcutil takes a while, the tint shouldn't wait
        signal.sigtimedwait({signal.SIGUSR1}, TICK if reached else RETRY)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "daemon":
        daemon()
    elif cmd == "status":
        print(json.dumps(status(load())))
    elif cmd == "toggle":
        update(on=not load()["on"])
    elif cmd == "pause" and len(argv) > 2:
        with open(PAUSE, "w") as fh:
            fh.write(argv[2])
        poke()
    elif cmd == "resume" and len(argv) > 2:
        try:
            with open(PAUSE) as fh:
                mine = fh.read().strip() == argv[2]
            if mine:
                os.remove(PAUSE)
        except OSError:
            pass
        poke()
    else:
        print("usage: nightlight daemon|status|toggle|pause PID|resume PID",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
