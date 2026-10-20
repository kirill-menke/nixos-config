#!/usr/bin/env python3
"""The bar's "needs attention" module: what on this machine wants a hand,
hidden while all is well.

  attention watch     the module itself: one process per bar, which prints
                      the module's JSON at start and whenever it changes
  attention status    that JSON once, for a look by hand
  attention details   systemctl status of each flagged unit, then the
                      other findings (the module's right click and the
                      card's terminal button, in kitty)

Red, with their count:
  - failed or crash-looping systemd units, system and user;
  - a file system as full as the System card's disk meter turns red
    (card_system.FULL), or a /boot without room for one more kernel and
    initrd, which nixos-rebuild copies there for every new kernel;
  - a drive whose own health log says it is failing: an NVMe critical
    warning or ATA SMART, as udisks2 polls them.
Peach, worth a look rather than broken:
  - an app that has kept HOG_CORES or more busy for SUSTAIN while the
    machine runs on its battery, by name after the processor's glyph. The
    bar's cpu module can't see this: its states are the whole machine's
    usage over 5 s, and one app holding 6 of 14 threads reads 45 %, under
    its 70 % warning;
  - what only a reboot brings in. That is a generation the boot menu
    starts instead of the running one (nixos-rebuild boot, which a switch
    inhibitor such as a new D-Bus implementation forces), or a switch's
    new kernel, kernel modules (an NVIDIA driver: the old module stays
    loaded) or kernel parameters;
  - a firmware update fwupd offers for this machine: the BIOS, or any
    other device it can update.
The bar shows the most pressing: the red count, else the app, else the
reboot's glyph, else the firmware's. The tooltip lists them all, one a
line, and ends in HINT.

A check asks both service managers, udisks2 and UPower over D-Bus from
this one process and reads the rest from the file system, so it starts
nothing else. On battery it also samples each process's CPU time in /proc
and leaves the sample in CPU_STATE for the next check to compare with.
With nothing to report the module is {}, which waybar shows as nothing:
the module hides. The Attention card (card_attention.py) imports this
module and lists the same findings; the Power card imports
pending_reboot() for its Restart row.

`attention watch` checks once, then sleeps until something can have
changed (Watcher): a unit's state, as both service managers announce it
once asked to (Subscribe), where the change can add or clear a finding
(a unit failing, about to be restarted automatically, or one on the bar
changing at all); a reload of the system manager, as a switch does; a new
system profile (nixos-rebuild boot or switch); the power cord, for the
CPU-hog check. A burst of these makes one check, SETTLE after its first.
What announces nothing (a disk filling up, a drive's health log, the CPU
sample, a loop's RECENT running out) waits for the check every PERIOD.
Idle, the watcher wakes only for that.

fwupd quits five minutes after its last call (its IdleTimeout) and every
call starts that over, so asking it at each check would keep it running
for good. The watcher asks it when it shows up (its fwupd-refresh.timer
starts it hourly, to see whether LVFS has new metadata) or announces a
change, and keeps the answer in FIRMWARE_CACHE, which every check reads.
Should a day go by without an answer, the watcher starts fwupd itself,
at most once a day.

A restart loop never reaches "failed": the start rate limit (5 starts in
10 s) never trips when RestartSec is a few seconds (hypridle without a
config: 10 s), so systemd restarts such a unit forever. So a service also
counts once systemd has restarted it automatically LOOP times, while the
last restart is recent. The count (NRestarts) covers every automatic
restart since the service was last started explicitly, at boot or by
hand: a run that goes well in between doesn't reset it, so three crashes
hours apart count too, for RECENT after the third.

One-off jobs whose caller already reported the outcome don't count when
they fail: units created at run time (systemd-run jobs, app and terminal
scopes, nixos-rebuild's switch) and per-connection instances of a socket
with Accept=yes (a core dump being saved, a polkit password prompt). Both
stay "failed" until someone resets them.
"""

import json
import os
import re
import subprocess
import sys
import time
from collections import namedtuple

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

SYSTEMD = "org.freedesktop.systemd1"
MANAGER = ("/org/freedesktop/systemd1", SYSTEMD + ".Manager")
UNIT, SERVICE = SYSTEMD + ".Unit", SYSTEMD + ".Service"
SOCKET = SYSTEMD + ".Socket"
PROPERTIES = "org.freedesktop.DBus.Properties"
UDISKS = "org.freedesktop.UDisks2"
UPOWER = "org.freedesktop.UPower"
FWUPD = "org.freedesktop.fwupd"
TIMEOUT_MS = 5000

# `attention watch`: the check for what announces no change (and the CPU
# sample's span), and the s from a signal to its check, by what it was.
PERIOD = 300
SETTLE = 3  # a unit, a reload, the power cord: a burst makes one check
# A switch makes the new profile before it activates it, and the reload
# that follows is the check worth having; a check on the profile alone
# would see its generation wait for a reboot for those seconds.
PROFILE_SETTLE = 30
# fwupd showing up or announcing a change: its timer starts it to refresh
# the metadata, which may still be on its way.
FIRMWARE_SETTLE = 10

LOOP = 3      # automatic restarts since the last explicit start: a loop
RECENT = 600  # s: a loop counts while its last restart is younger
# Service sub-states between an exit and the automatic restart. systemd
# counts the restart (NRestarts) only once it queues the job, so in all
# of these but auto-restart-queued the pending one isn't counted yet.
UNCOUNTED = {"dead-before-auto-restart", "failed-before-auto-restart",
             "auto-restart"}
WAITING = UNCOUNTED | {"auto-restart-queued"}

# Why a unit failed (its Result), short enough for the card's sub-line.
RESULTS = {
    "exit-code": "error exit",
    "signal": "killed",
    "core-dump": "crashed",
    "timeout": "timed out",
    "watchdog": "hung (watchdog)",
    "start-limit-hit": "too many starts",
    "oom-kill": "out of memory",
    "protocol": "protocol error",
    "resources": "no resources to start",
}
# Names of our own: the card runs this too, and GTK sets the C library's
# locale from the session, so strftime() would follow LC_TIME, which is
# German here.
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# The boot menu starts the system profile's generation by default; the
# running one is the current system, the running kernel the booted one's.
PROFILE = "/nix/var/nix/profiles/system"
BOOTED, CURRENT = "/run/booted-system", "/run/current-system"
# What a switch can't swap in a running system, as the reboot line names
# it, most telling first. kernel-params is a file in each generation, so
# it is compared by content; the others link to store paths of their own.
BOOT_ONLY = (("kernel", "Reboot to use the new kernel"),
             ("kernel-modules", "Reboot to load the new kernel modules"),
             ("kernel-params", "Reboot to apply the new kernel parameters"))
# boot.json's label: "NixOS Zokor 26.11.20261010.6510408 (Linux 6.18.55)"
LABEL = re.compile(r"(\d+\.\d+)\.\S* \(Linux ([^)]+)\)")

# File systems that can fill up, where they are separate from "/".
MOUNTS = {"/": "Root file system", "/home": "Home", "/nix": "Nix store"}
FULL = 0.95  # used, as df counts it: where the System card's meter is red

# NVMe critical warnings (udisks2's SmartCriticalWarning) as the card says
# them. "temperature" is left out: it clears once the drive cools, and the
# temperature module is the one that watches heat.
NVME = {
    "spare": "spare blocks running out",
    "degraded": "reliability degraded",
    "readonly": "now read-only",
    "volatile_mem": "backup power failed",
    "pmr_readonly": "memory region read-only",
}

# An app hogging the processor on battery: its processes count together,
# grouped as the System card groups them (card_system.app_name).
HOG_CORES = 1.0  # cores it keeps busy, on average, from one sample to the next
SUSTAIN = 300    # s of that in a row before it is flagged
# Each check takes a sample: the watcher's every PERIOD (five minutes),
# and those on a signal in between, so SUSTAIN takes one span. A span
# over MAX_GAP (a suspend, a bar restart) starts every streak over.
MAX_GAP = 450    # s: PERIOD with room for a late check
# s: a check sooner than this after the last sample (the second bar's, or
# one on a signal) takes that sample; over a few seconds an app's share
# is noise, which would end a streak or start one.
MIN_SPAN = 30
TICK = os.sysconf("SC_CLK_TCK")  # /proc's time unit, per s
KTHREAD = 0x00200000  # PF_KTHREAD among /proc/PID/stat's flags
# Programs that run a script named on their command line: their processes
# go by the script's name.
INTERPRETER = re.compile(r"(python|perl|ruby|bash|dash|sh|zsh)[\d.]*")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
CPU_STATE = os.path.join(RUNTIME, "attention-cpu.json")  # the last sample

# fwupd's last answer, written by the watcher and read by every check:
# Update()s, when fwupd gave them ("read") and when the watcher last
# started fwupd itself ("started"). It outlasts a reboot, beside
# nightlight's and pomodoro's state, so "once a day" holds across them.
STATE_HOME = (os.environ.get("XDG_STATE_HOME")
              or os.path.expanduser("~/.local/state"))
FIRMWARE_CACHE = os.path.join(STATE_HOME, "attention-firmware.json")
DAY = 86400
# fwupd answers hourly (its timer) or daily (the watcher starts it), so
# an answer this old means fwupd is gone or broken: it no longer counts.
FIRMWARE_STALE = 2 * DAY
# FwupdDeviceFlags (libfwupd/fwupd-device.rs): fwupdmgr get-updates asks
# for the upgrades of the devices that are updatable (or updatable but
# hidden from software centres) and that LVFS has firmware for.
UPDATABLE = 1 << 1 | 1 << 37
SUPPORTED = 1 << 5
# One update: fwupd's device ID and name, the version it runs and the
# newest on offer; bios: the system firmware (LVFS's X-System category,
# or the uefi-capsule plugin's "computer" icon).
Update = namedtuple("Update", "device name version new bios")

ALERT = "\U000f0026"     # md-alert: something is red, with their count
CPU = "\U000f0ee0"       # md-cpu_64_bit, the bar's cpu glyph: an app hogs it
REBOOT = "\U000f110c"    # md-restart_alert: a reboot is pending
FIRMWARE = "\U000f061a"  # md-chip: only a firmware update waits
# The tooltip's last line, the gestures a click doesn't show, as every bar
# module has it (config.jsonc): small, in the cards' subtext0 (mocha).
HINT = ("<span size='small' foreground='#a6adc8'>"
        "Right-click: details in a terminal</span>")

# One finding. kind: failed, restarting, disk, drive, hog, reboot or
# firmware; text: its tooltip line; name and sub: its two lines on the
# card; key: the unit's name, the full file system's mount point, the
# hogging app or the device's fwupd ID; user: whether the user's service
# manager runs the unit.
Item = namedtuple("Item", "kind text name sub key user",
                  defaults=(None, False))
RED = ("failed", "restarting", "drive", "disk")


def read_properties(bus, reads):
    """{key: (object path, interface, property)} -> {key: value}. The
    calls go out together and are answered in one round trip; a property
    that can't be read is left out. The answers come in on a main context
    of their own, so the card, calling from GTK's main loop, sees no other
    event handled meanwhile."""
    values, pending = {}, len(reads)

    def done(conn, result, key):
        nonlocal pending
        pending -= 1
        try:
            values[key] = conn.call_finish(result).unpack()[0]
        except GLib.Error:
            pass

    context = GLib.MainContext.new()
    context.push_thread_default()
    try:
        for key, (path, iface, name) in reads.items():
            bus.call(SYSTEMD, path, "org.freedesktop.DBus.Properties", "Get",
                     GLib.Variant("(ss)", (iface, name)),
                     GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE,
                     TIMEOUT_MS, None, done, key)
        while pending:
            context.iteration(True)
    finally:
        context.pop_thread_default()
    return values


def result_iface(name):
    """The interface with a unit's Result: Service for a .service, Mount
    for a .mount... Targets, devices and slices have none."""
    return f"{SYSTEMD}.{name.rsplit('.', 1)[-1].capitalize()}"


def clock(usec):
    """A timestamp as the time of day, with the weekday unless today."""
    if not usec:
        return ""
    when = time.localtime(usec / 1e6)
    hm = f"{when.tm_hour:02d}:{when.tm_min:02d}"
    if when[:3] == time.localtime()[:3]:
        return hm
    return f"{DAYS[when.tm_wday]} {hm}"


def save(path, data):
    """`data` as JSON at `path`, whole: replaced in one step, should
    another bar's process or the card read it meanwhile."""
    tmp = f"{path}.{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError as err:
        print(f"attention: {err}", file=sys.stderr)


def flagged(bus_type, user):
    """Items for the failed and crash-looping units of one service
    manager; [] if it can't be reached."""
    try:
        bus = Gio.bus_get_sync(bus_type)
        units = bus.call_sync(
            SYSTEMD, *MANAGER, "ListUnits", None,
            GLib.VariantType("(a(ssssssouso))"), Gio.DBusCallFlags.NONE,
            TIMEOUT_MS, None).unpack()[0]
    except GLib.Error as err:
        print(f"attention: {err.message}", file=sys.stderr)
        return []
    # (name, description, load, active, sub, following, path, job...)
    paths = {name: path for name, _, _, _, _, _, path, *_ in units}
    failed = {name: path for name, _, _, active, sub, _, path, *_ in units
              if active == "failed" and sub == "failed"}
    services = {name: (path, sub)
                for name, _, _, active, sub, _, path, *_ in units
                if name.endswith(".service")
                and (active in ("active", "activating", "reloading")
                     or sub in WAITING)}
    first = read_properties(bus, {
        **{(name, "transient"): (path, UNIT, "Transient")
           for name, path in failed.items()},
        **{(name, "triggers"): (path, UNIT, "TriggeredBy")
           for name, path in failed.items()},
        **{(name, "since"): (path, UNIT, "StateChangeTimestamp")
           for name, path in failed.items()},
        **{(name, "result"): (path, result_iface(name), "Result")
           for name, path in failed.items()},
        **{(name, "restarts"): (path, SERVICE, "NRestarts")
           for name, (path, _) in services.items()},
    })
    loops = {}
    for name, (path, sub) in services.items():
        restarts = first.get((name, "restarts"), 0) + (sub in UNCOUNTED)
        if restarts >= LOOP:
            loops[name] = (path, sub, restarts)
    sockets = {trigger for name in failed
               for trigger in first.get((name, "triggers"), [])
               if trigger.endswith(".socket") and trigger in paths}
    # A loop that is between runs right now is current; a running one
    # only while its last (automatic) start is recent.
    second = read_properties(bus, {
        **{(name, "started"): (path, SERVICE, "ExecMainStartTimestamp")
           for name, (path, sub, _) in loops.items() if sub not in WAITING},
        **{(socket, "accept"): (paths[socket], SOCKET, "Accept")
           for socket in sockets},
    })
    owner, scope = (" (user)", "User") if user else ("", "System")
    out = []
    # A unit that can't be read any more has been cleaned up meanwhile.
    for name in failed:
        if (first.get((name, "transient")) is not False
                or any(second.get((trigger, "accept"))
                       for trigger in first.get((name, "triggers"), []))):
            continue
        why = RESULTS.get(first.get((name, "result")), "")
        when = clock(first.get((name, "since"), 0))
        out.append(Item(
            "failed", " · ".join(filter(None, (f"{name}{owner} failed",
                                               why, when))),
            name, " · ".join(filter(None, (scope, why, when))), name, user))
    now = time.time() * 1e6
    for name, (_, sub, restarts) in loops.items():
        if (sub in WAITING
                or now - second.get((name, "started"), 0) < RECENT * 1e6):
            out.append(Item(
                "restarting",
                f"{name}{owner} keeps restarting ({restarts} restarts)",
                name, f"{scope} · restarted {restarts} times", name, user))
    return out


# --- A reboot that waits -------------------------------------------------------

def release(system):
    """(NixOS release, Linux version) from a generation's boot.json."""
    try:
        with open(os.path.join(system, "boot.json")) as fh:
            label = json.load(fh)["org.nixos.bootspec.v1"]["label"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    match = LABEL.search(label)
    return match.groups() if match else None


def changes(old, new):
    """What a reboot from `old` to `new` changes, twice: the tooltip's
    'NixOS 26.05 → 26.11 · Linux 6.18.17 → 6.18.55' and the card's
    'New: NixOS 26.11 · Linux 6.18.55'; ("", "") if nothing it can name."""
    a, b = release(old), release(new)
    differ = [(what, x, y) for what, x, y in zip(("NixOS", "Linux"), a, b)
              if x != y] if a and b else []
    if not differ:
        return "", ""
    return (" · ".join(f"{what} {x} → {y}" for what, x, y in differ),
            "New: " + " · ".join(f"{what} {y}" for what, _, y in differ))


def differs(old, new, part):
    try:
        if part == "kernel-params":
            with (open(os.path.join(old, part)) as a,
                  open(os.path.join(new, part)) as b):
                return a.read() != b.read()
        return (os.path.realpath(os.path.join(old, part))
                != os.path.realpath(os.path.join(new, part)))
    except OSError:
        return False


def pending_reboot():
    """The reboot Item, or None: nothing waits for one, or not NixOS. Only
    reads files (about 0.6 ms), so the Power card calls it on opening."""
    try:
        booted = os.path.realpath(BOOTED, strict=True)
        current = os.path.realpath(CURRENT, strict=True)
    except OSError:
        return None
    try:
        default = os.path.realpath(PROFILE, strict=True)
        number = os.readlink(PROFILE).split("-")[1]  # system-98-link
    except (OSError, IndexError):
        default = number = None
    if default and default != current:
        # A current system that is no generation at all is a test build
        # (nixos-rebuild test): a reboot leaves it for the profile's.
        built = {os.path.realpath(entry.path)
                 for entry in os.scandir(os.path.dirname(PROFILE))
                 if entry.name.startswith("system-")}
        if current not in built:
            back = f"a reboot goes back to generation {number}"
            return Item("reboot", f"Running a test build: {back}",
                        "Running a test build", back.capitalize())
        name = f"Generation {number} waits for a reboot"
        change, new = changes(booted, default)
        return Item("reboot", " · ".join(filter(None, (name, change))), name,
                    new or "Built, not running yet")
    for part, name in BOOT_ONLY:
        if differs(booted, current, part):
            change, new = changes(booted, current)
            return Item("reboot", " · ".join(filter(None, (name, change))),
                        name, new or "Switched to since the last boot")
    return None


# --- Disks and drives ----------------------------------------------------------

def size(n):
    """'1.7 GiB', '78 GiB', '795 MiB' (card_system.size)."""
    for unit, scale in (("TiB", 2**40), ("GiB", 2**30), ("MiB", 2**20)):
        if n >= scale:
            break
    else:
        unit, scale = "KiB", 2**10
    value = n / scale
    return f"{value:.1f} {unit}" if value < 9.95 else f"{value:.0f} {unit}"


def disks():
    """Items for full file systems: MOUNTS at FULL of their blocks or
    inodes, and a separate /boot that can't take another kernel."""
    out, seen = [], set()
    for mount, name in MOUNTS.items():
        try:
            dev, st = os.stat(mount).st_dev, os.statvfs(mount)
        except OSError:
            continue
        if dev in seen:  # a directory on a file system already checked
            continue
        seen.add(dev)
        used, free = st.f_blocks - st.f_bfree, st.f_bavail
        if used + free and used / (used + free) >= FULL:
            full = f"{name} {used / (used + free):.0%} full"
            left = f"{size(free * st.f_frsize)} left"
            out.append(Item("disk", f"{full} · {left}", full, left, mount))
        elif st.f_files and 1 - st.f_favail / st.f_files >= FULL:
            full, left = f"{name} out of inodes", f"{st.f_favail} files left"
            out.append(Item("disk", f"{full} · {left}", full, left, mount))
    try:
        boot = os.statvfs("/boot")
        separate = os.stat("/boot").st_dev not in seen
        # What the next kernel takes there: the running ones' size.
        need = sum(os.stat(os.path.realpath(os.path.join(CURRENT, part)))
                   .st_size for part in ("kernel", "initrd"))
    except OSError:
        return out
    left = boot.f_bavail * boot.f_frsize
    if separate and left < need:
        full = "/boot has no room for another kernel"
        room = f"{size(left)} left · {size(need)} needed"
        out.append(Item("disk", f"{full} · {room}", full, room, "/boot"))
    return out


def drives():
    """Items for drives whose health log says they are failing, as udisks2
    reads it (every 10 min); [] where udisks2 isn't running."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        reply = bus.call_sync(
            UDISKS, "/org/freedesktop/UDisks2",
            "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
            GLib.VariantType("(a{oa{sa{sv}}})"),
            Gio.DBusCallFlags.NO_AUTO_START, TIMEOUT_MS, None)
    except GLib.Error:
        return []
    # The reply holds every block device and partition with all their
    # properties: unpacked whole it took ten times as long as the call
    # (40 ms against 4), so only the drives' few values are looked up.
    out = []
    objects = reply.get_child_value(0)
    for i in range(objects.n_children()):
        ifaces = objects.get_child_value(i).get_child_value(1)

        def value(iface, name, default=None, ifaces=ifaces):
            props = ifaces.lookup_value(UDISKS + iface, None)
            found = props.lookup_value(name, None) if props else None
            return default if found is None else found.unpack()

        if ifaces.lookup_value(UDISKS + ".Drive", None) is None:
            continue
        warnings = [NVME[w] for w in value(".NVMe.Controller",
                                           "SmartCriticalWarning", [])
                    if w in NVME]
        if value(".Drive.Ata", "SmartFailing", False):
            warnings.append("SMART predicts failure")
        if warnings:
            name = value(".Drive", "Model") or value(".Drive", "Id")
            name, what = name or "A drive", ", ".join(warnings)
            out.append(Item("drive", f"{name}: {what}", name,
                            f"{what[0].upper()}{what[1:]} · back up now"))
    return out


# --- An app hogging the processor on battery -----------------------------------

def on_battery():
    """Whether UPower says the machine runs on its battery: never where it
    has none (the desktop PC) or UPower doesn't run."""
    try:
        reply = Gio.bus_get_sync(Gio.BusType.SYSTEM).call_sync(
            UPOWER, "/org/freedesktop/UPower",
            "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (UPOWER, "OnBattery")),
            GLib.VariantType("(v)"), Gio.DBusCallFlags.NO_AUTO_START,
            TIMEOUT_MS, None)
    except GLib.Error:
        return False
    return reply.unpack()[0] is True


# Naming apps: card_system.py's unwrap, script and app_name, copied, as
# that module brings GTK with it, which the bar's status run can't pay for.

def unwrap(name):
    """'.kitty-wrapped' -> 'kitty': Nix wrappers add a dot and -wrapped (or
    -unwrapped), and comm, cut at 15 characters, may hold just the start of
    the suffix ('.Hyprland-wrapp')."""
    if not name.startswith("."):
        return name
    stem, dash, tail = name[1:].rpartition("-")
    if dash and stem and ("wrapped".startswith(tail)
                          or "unwrapped".startswith(tail)):
        return stem
    return name[1:]


def script(pid):
    """The name of the script an interpreter runs: its first argument that
    isn't an option, or the module after -m. None for inline code (-c)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            args = iter(fh.read().split(b"\0")[1:])
    except OSError:
        return None
    for arg in args:
        if arg in (b"-c", b"-e"):
            return None
        if arg == b"-m":
            return next(args, b"").decode(errors="replace") or None
        if arg and not arg.startswith(b"-"):
            name = unwrap(os.path.basename(arg.decode(errors="replace")))
            return os.path.splitext(name)[0] or None
    return None


def app_name(pid, comm, kthread):
    """What a process is part of, so an app's processes count as one: its
    program file, or the script an interpreter runs; the kernel's threads
    together. Another user's processes, and those that keep their program
    file to themselves, go by the name they give themselves."""
    if kthread:
        return "Kernel"
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return unwrap(comm.decode(errors="replace"))
    # A program updated under it reads "/nix/store/...-foo/bin/foo (deleted)".
    name = unwrap(os.path.basename(exe.removesuffix(" (deleted)")))
    if INTERPRETER.fullmatch(name):
        return script(pid) or name
    return name


def processes(names):
    """{pid: [start, cpu ticks, app]}, one read of /proc/PID/stat each, as
    the System card samples; `names` is the last sample's, so a process is
    named (a readlink, maybe its command line) only once."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            fd = os.open(f"/proc/{pid}/stat", os.O_RDONLY)
            try:
                data = os.read(fd, 4096)
            finally:
                os.close(fd)
            # comm may hold spaces and parentheses; the fields after it don't
            rp = data.rfind(b")")
            f = data[rp + 2:].split(maxsplit=22)
            start, ticks = int(f[19]), int(f[11]) + int(f[12])
            kthread = bool(int(f[6]) & KTHREAD)
        except (OSError, IndexError, ValueError):
            continue  # gone in the meantime
        old = names.get(int(pid))
        out[int(pid)] = [start, ticks, old[2] if old and old[0] == start
                         else app_name(pid, data[data.find(b"(") + 1:rp],
                                       kthread)]
    return out


def uptime():
    """Seconds since boot, the time asleep included, as /proc counts the
    processes' start times."""
    with open("/proc/uptime") as fh:
        return float(fh.read().split()[0])


def last_sample():
    """CPU_STATE: {"up": uptime, "procs": processes(), "hogs": {app:
    [uptime its streak began, cores it kept busy lately]}}, or None."""
    try:
        with open(CPU_STATE) as fh:
            state = json.load(fh)
        return {"up": float(state["up"]),
                "procs": {int(pid): p for pid, p in state["procs"].items()},
                "hogs": {app: (float(since), float(cores))
                         for app, (since, cores) in state["hogs"].items()}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def sample(last, up):
    """A new sample at uptime `up`, saved for the next check. An app's
    streak goes on while it has kept HOG_CORES busy since `last`, the
    sample before."""
    procs = processes(last["procs"] if last else {})
    spent = {}  # app -> cpu ticks since `last`
    if last:
        for pid, (start, ticks, app) in procs.items():
            old = last["procs"].get(pid)
            if old is not None and old[0] == start:
                ticks -= old[1]
            elif start < last["up"] * TICK:
                ticks = 0  # missed last time, not new: its time isn't recent
            spent[app] = spent.get(app, 0) + ticks
    streaks = {}
    for app, ticks in spent.items():
        cores = ticks / TICK / (up - last["up"])
        if cores >= HOG_CORES and app != "Kernel":
            streaks[app] = (last["hogs"].get(app, (last["up"],))[0], cores)
    state = {"up": up, "procs": procs, "hogs": streaks}
    save(CPU_STATE, state)
    return state


def hogs(sampling=True):
    """Items for the apps that have kept HOG_CORES busy for SUSTAIN on
    battery, the busiest first. `sampling`: a check of the watcher's (or
    `attention status`) takes a sample; the card and the terminal view
    report the last one."""
    if not on_battery():
        if sampling:
            try:
                os.remove(CPU_STATE)  # a later check on battery starts afresh
            except OSError:
                pass
        return []
    up = uptime()
    last = last_sample()
    # A gap over MAX_GAP ends every streak.
    if last is not None and not 0 <= up - last["up"] <= MAX_GAP:
        last = None
    if sampling and (last is None or up - last["up"] >= MIN_SPAN):
        last = sample(last, up)
    if last is None:
        return []
    out = []
    for app, (since, cores) in sorted(last["hogs"].items(),
                                      key=lambda kv: -kv[1][1]):
        if last["up"] - since < SUSTAIN - 1:  # a second's jitter
            continue
        busy = f"{cores:.1f} cores"
        minutes = f"{(last['up'] - since) / 60:.0f} min"
        out.append(Item("hog", f"{app} has kept {busy} busy for {minutes}"
                        " on battery", f"{app} keeps {busy} busy",
                        f"For {minutes}, on battery", app))
    return out

# --- Firmware updates -----------------------------------------------------------

def lookup(dictionary, key, default=None):
    """A value of an a{sv} GVariant, unpacked; `default` where it has none."""
    found = dictionary.lookup_value(key, None)
    return default if found is None else found.unpack()


def fwupd_updates(bus):
    """[Update] fwupd offers now, asked as fwupdmgr get-updates asks it;
    None while it isn't running (NO_AUTO_START: asking never starts it)."""
    def call(method, args=None):
        return bus.call_sync(FWUPD, "/", FWUPD, method, args,
                             GLib.VariantType("(aa{sv})"),
                             Gio.DBusCallFlags.NO_AUTO_START, TIMEOUT_MS,
                             None).get_child_value(0)

    try:
        devices = call("GetDevices")
    except GLib.Error:
        return None
    out = []
    for i in range(devices.n_children()):
        device = devices.get_child_value(i)
        flags = lookup(device, "Flags", 0)
        if not flags & UPDATABLE or not flags & SUPPORTED:
            continue
        try:
            releases = call("GetUpgrades", GLib.Variant(
                "(s)", (lookup(device, "DeviceId", ""),)))
        except GLib.Error as err:
            if not (Gio.DBusError.get_remote_error(err) or "").startswith(
                    FWUPD + "."):
                return None  # gone meanwhile: no answer, not "none"
            continue  # NothingToDo: the newest runs, or one waits for a reboot
        if not releases.n_children():
            continue
        newest = releases.get_child_value(0)  # fwupd sorts the newest first
        out.append(Update(
            lookup(device, "DeviceId", ""), lookup(device, "Name", "A device"),
            lookup(device, "Version", ""), lookup(newest, "Version", "?"),
            "X-System" in lookup(newest, "Categories", [])
            or "computer" in lookup(device, "Icon", [])))
    return out


def firmware_cache():
    """FIRMWARE_CACHE: {"read": when fwupd last answered, "started": when
    the watcher last started it (both s since the epoch), "updates":
    [Update]}; zeros and none before there is one."""
    try:
        with open(FIRMWARE_CACHE) as fh:
            cache = json.load(fh)
        return {"read": float(cache["read"]),
                "started": float(cache["started"]),
                "updates": [Update(**u) for u in cache["updates"]]}
    except (OSError, ValueError, KeyError, TypeError):
        return {"read": 0.0, "started": 0.0, "updates": []}


def save_firmware(cache):
    save(FIRMWARE_CACHE, {**cache, "updates": [
        u._asdict() for u in cache["updates"]]})


def firmware():
    """Items for the updates fwupd offered when it last answered, the BIOS
    first; none once that answer is older than FIRMWARE_STALE. Only reads
    FIRMWARE_CACHE, so the card and `attention status` never wake fwupd."""
    cache = firmware_cache()
    if time.time() - cache["read"] > FIRMWARE_STALE:
        return []
    out = []
    for u in sorted(cache["updates"], key=lambda u: (not u.bios, u.name)):
        name = ("BIOS update available" if u.bios
                else f"{u.name} update available")
        change = f"{u.version} → {u.new}" if u.version else f"Version {u.new}"
        out.append(Item("firmware", f"{name} · {change}", name, change,
                        u.device))
    return out



# --- The module ------------------------------------------------------------------

def check(sampling=True):
    """[Item]: red ones first (units: failed before restarting, system
    before user), then the CPU hogs, the reboot, the firmware updates
    last. `sampling`: as hogs()."""
    units = [item for user, bus_type in ((False, Gio.BusType.SYSTEM),
                                         (True, Gio.BusType.SESSION))
             for item in flagged(bus_type, user)]
    units.sort(key=lambda i: (i.kind != "failed", i.user, i.key))
    pending = pending_reboot()
    return (units + drives() + disks() + hogs(sampling)
            + ([pending] if pending else []) + firmware())


def status(items):
    """The waybar module: {} hides it. Waybar takes its text and tooltip
    as markup (the module doesn't "escape", for HINT), so whatever comes
    from the system is escaped here."""
    if not items:
        return {}
    red = sum(item.kind in RED for item in items)
    hogging = [item.key for item in items if item.kind == "hog"]
    if red:
        text = f"{ALERT} {red}"
    elif hogging:
        more = f" +{len(hogging) - 1}" if len(hogging) > 1 else ""
        text = f"{CPU} {GLib.markup_escape_text(hogging[0])}{more}"
    elif any(item.kind == "reboot" for item in items):
        text = REBOOT
    else:
        text = FIRMWARE
    lines = [GLib.markup_escape_text(item.text) for item in items]
    return {"text": text, "tooltip": "\n".join(lines + [HINT]),
            "class": sorted({item.kind for item in items})}


def details(items):
    """The terminal view: each flagged unit's status with its latest log
    lines, then the other findings."""
    if not items:
        print("Nothing needs attention.")
    for item in items:
        if item.kind in ("failed", "restarting"):
            scope = ["--user"] if item.user else []
            subprocess.run(["systemctl", *scope, "status", "--no-pager",
                            "--full", "--lines=30", "--", item.key])
            print(flush=True)  # before the next systemctl writes
        else:
            print(item.text, flush=True)


# --- attention watch -----------------------------------------------------------

def unit_name(path):
    """A unit's name from its object path, whose last part systemd escapes
    byte by byte as _xx (bus_label_escape), each "_" included."""
    label = path.rsplit("/", 1)[-1].encode()
    return re.sub(rb"_([0-9a-f]{2})", lambda m: bytes([int(m[1], 16)]),
                  label).decode(errors="replace")


class Watcher:
    """The module kept up to date on GLib's main loop: checked at start, on
    what can change a finding and every PERIOD, and printed when the JSON
    differs from the last."""

    def __init__(self):
        self.printed = None  # the module's last JSON
        self.units = {}      # (unit, user) -> its finding's kind, on the bar
        self.pending, self.due = 0, 0.0  # the next signalled check, when
        self.asking = 0      # the coming question to fwupd
        self.fwupd = False   # whether fwupd runs
        self.system = None
        # Held here: a bus is a singleton that GLib lets go with its last
        # reference, and its subscriptions with it (and every check would
        # connect anew).
        self.buses = []
        for bus_type, user in ((Gio.BusType.SYSTEM, False),
                               (Gio.BusType.SESSION, True)):
            try:
                bus = Gio.bus_get_sync(bus_type)
            except GLib.Error as err:
                print(f"attention: {err.message}", file=sys.stderr)
                continue
            self.buses.append(bus)
            if not user:
                self.system = bus
            bus.signal_subscribe(
                SYSTEMD, PROPERTIES, "PropertiesChanged", None, UNIT,
                Gio.DBusSignalFlags.NONE, self._unit_changed, user)
            try:
                # A service manager sends its units' signals only to the
                # buses where some client asked for them.
                bus.call_sync(SYSTEMD, *MANAGER, "Subscribe", None, None,
                              Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
            except GLib.Error as err:
                print(f"attention: {err.message}", file=sys.stderr)
        if self.system is not None:
            for sender, iface, member, path, arg0, handler in (
                    (SYSTEMD, MANAGER[1], "Reloading", MANAGER[0], None,
                     self._reloaded),
                    (UPOWER, PROPERTIES, "PropertiesChanged",
                     "/org/freedesktop/UPower", UPOWER, self._power_changed),
                    (FWUPD, FWUPD, "Changed", "/", None, self._fwupd_changed)):
                self.system.signal_subscribe(sender, iface, member, path, arg0,
                                             Gio.DBusSignalFlags.NONE, handler)
            # Not started by this: it says whether fwupd runs, and when that
            # changes.
            Gio.bus_watch_name_on_connection(
                self.system, FWUPD, Gio.BusNameWatcherFlags.NONE,
                self._fwupd_appeared, self._fwupd_vanished)
        # nixos-rebuild boot or switch: a new link, then "system" moved to it
        try:
            self.profiles = Gio.File.new_for_path(
                os.path.dirname(PROFILE)).monitor_directory(
                    Gio.FileMonitorFlags.NONE, None)
            self.profiles.connect("changed", self._profile_changed)
        except GLib.Error:
            self.profiles = None  # not NixOS
        GLib.timeout_add_seconds(PERIOD, self._tick)
        self.update()

    def update(self):
        """Check, and print the module if it changed."""
        items = check()
        self.units = {(item.key, item.user): item.kind for item in items
                      if item.kind in ("failed", "restarting")}
        line = json.dumps(status(items))
        if line != self.printed:
            print(line, flush=True)
            self.printed = line

    def soon(self, delay=None):
        """A check in `delay` s (SETTLE), unless one is due sooner."""
        delay = SETTLE if delay is None else delay
        due = time.monotonic() + delay
        if self.pending:
            if self.due <= due:
                return
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add_seconds(delay, self._settled)
        self.due = due

    def _settled(self):
        self.pending = 0
        self.update()
        return GLib.SOURCE_REMOVE

    def _tick(self):
        self.daily()
        self.update()
        return GLib.SOURCE_CONTINUE

    def _unit_changed(self, _bus, _sender, path, _iface, _signal, params,
                      user):
        """A unit's state changed: a check if that can add or clear a
        finding."""
        changed = params.get_child_value(1)
        state = tuple(changed.lookup_value(name, None)
                      for name in ("ActiveState", "SubState"))
        if any(value is None for value in state):
            self.soon()  # it didn't say: check
            return
        state = tuple(value.get_string() for value in state)
        kind = self.units.get((unit_name(path), user))
        if kind == "restarting":
            # Each round of its loop changes it; what matters is the end,
            # stopped or failed for good. The PERIOD's check updates the
            # count of restarts.
            wanted = state in (("inactive", "dead"), ("failed", "failed"))
        else:
            # A failed one on the bar restarted, reset or stopped; one not
            # on it failed or is about to be restarted (a loop to be).
            wanted = (kind is not None or state == ("failed", "failed")
                      or state[1] in WAITING)
        if wanted:
            self.soon()

    def _reloaded(self, *_):
        self.soon()  # a switch: units, and the system running, may be new

    def _power_changed(self, _bus, _sender, _path, _iface, _signal, params):
        # (a GLib.Variant of False is false itself: "is not None")
        if params.get_child_value(1).lookup_value("OnBattery",
                                                  None) is not None:
            self.soon()  # the CPU-hog check begins or ends

    def _profile_changed(self, _monitor, file, _other, _event):
        if file.get_basename().startswith("system"):
            self.soon(PROFILE_SETTLE)

    # --- fwupd ---------------------------------------------------------------

    def _fwupd_appeared(self, _bus, _name, _owner):
        self.fwupd = True
        self.ask(FIRMWARE_SETTLE)

    def _fwupd_vanished(self, _bus, _name):
        self.fwupd = False  # its last answer stands
        self.daily()  # at start: none yet today?

    def _fwupd_changed(self, *_):
        self.ask(FIRMWARE_SETTLE)  # new metadata, a device, an update done

    def ask(self, delay):
        """fwupd's updates in `delay` s, saved, and the module checked."""
        if not self.asking:
            self.asking = GLib.timeout_add_seconds(delay, self._ask)

    def _ask(self):
        self.asking = 0
        updates = fwupd_updates(self.system)
        if updates is not None:
            save_firmware({**firmware_cache(), "read": time.time(),
                           "updates": updates})
            self.update()
        return GLib.SOURCE_REMOVE

    def daily(self):
        """Once fwupd's answer is a day old: asked for again while fwupd
        runs anyway, else fwupd started (its name showing up then asks),
        if the watcher didn't start it within the day."""
        cache, now = firmware_cache(), time.time()
        if now - cache["read"] < DAY or self.system is None:
            return
        if self.fwupd:
            self.ask(0)
        elif now - cache["started"] >= DAY:
            save_firmware({**cache, "started": now})
            self.system.call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "StartServiceByName",
                GLib.Variant("(su)", (FWUPD, 0)), GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE, 180_000, None, self._started)

    def _started(self, bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error as err:
            # Where fwupd isn't installed: no line for the journal each day.
            if (Gio.DBusError.get_remote_error(err)
                    != "org.freedesktop.DBus.Error.ServiceUnknown"):
                print(f"attention: fwupd: {err.message}", file=sys.stderr)


def watch():
    """attention watch: the Watcher until waybar ends it (SIGTERM, which
    it also sends should it die: PR_SET_PDEATHSIG). 1 after a callback
    raised, for waybar to start a new one ("restart-interval")."""
    loop = GLib.MainLoop()
    failed = []

    def fail(*exc):
        # PyGObject reports an exception in a callback through here and
        # goes on, but a timer whose callback raised is gone: end rather
        # than watch on half blind.
        sys.__excepthook__(*exc)
        failed.append(exc)
        loop.quit()

    sys.excepthook = fail
    watcher = Watcher()  # noqa: F841 (its callbacks hold it anyway)
    loop.run()
    return 1 if failed else 0


def main(argv):
    if argv[1:] == ["watch"]:
        return watch()
    if argv[1:] == ["status"]:
        print(json.dumps(status(check())), flush=True)
        return 0
    if argv[1:] == ["details"]:
        details(check(sampling=False))
        return 0
    print("usage: attention watch | attention status | attention details",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
