"""The System card, `bar-popup system`: what the machine is doing now.

The processor's load per core, grouped by kind of core on an Intel hybrid
chip, with the power mode, its clock, temperature and fan, and how long
heat has throttled it; memory, with how long programs wait for it and what
is in swap; the root disk and the drive's temperature; the network's rates
and its last three minutes; the graphics, with the apps keeping an Intel
GPU busy; the apps using the CPU or the memory, each of which a row can end
(two clicks); uptime and load; on battery, the power drawn. It all comes
from /proc and /sys, sampled every POLL s while the card is open and
written into the same widgets; the power mode comes from
power-profiles-daemon, the power drawn from UPower, the network's samples
from the bar's net-speed.sh, and an NVIDIA card's figures from an
nvidia-smi that samples alongside.
"""

import glob
import json
import math
import os
import re
import select
import shutil
import signal
import socket
import struct
import threading
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
from cardkit import (ARM_MS, DISCHARGING, MOCHA, MODES,  # noqa: E402
                     PALETTE, PPD, UPOWER, Arm, Panel, button, label,
                     prop, read_text, rounded_rect)

POLL = 2      # s between samples while the card is open
# Load is the difference between two samples. The card shows at once and
# takes the pair's first sample once it is drawn, so drawing it stays out
# of the figures; they follow this much later, while Hyprland still fades
# the card in (fadeLayersIn, about 180 ms).
FIRST = 0.12  # s
TOP = 5       # apps listed
# Samples between fan reads, and the drive's temperature with them. A fan's
# speed changes slowly, and reading it can take the firmware a while: 5-8 ms
# of CPU a read on the Dell here. An NVMe drive's temperature is a command
# to the drive, 4 ms, which GIO's worker thread waits for.
FAN_EVERY = 3
# °C where a temperature turns peach (warm) and red (hot): the CPU's as the
# bar's cpu-temp.sh has them, 20 and 10 °C under its limit where the sensor
# gives one (cpu_heat), else these; the GPU's at gpu.sh's: hot glyph from 80,
# red from 90.
CPU_HEAT = (85, 95)
GPU_HEAT = (80, 90)
FULL = (0.85, 0.95)  # the disk meter turns peach, then red
# Memory turns peach and red where the bar's memory figure does (memory.sh):
# peach from 80 % used, or once programs wait for memory 10 % of the time
# (pressure "some", over the last 10 s); red from 92 % used once swap is 80 %
# full too (or there is none), or once all of them wait 7 % ("full").
# Android's lmkd acts on the same counters at the same points. Where there
# is zram, swap is zram alone: a swap file behind it (hibernation's) is a
# last resort on disk, not room. From peach on, the apps list opens sorted
# by memory, the question the bar's colour asks.
MEM_USED = (80, 92)     # % used
MEM_WAIT = (10.0, 7.0)  # % of the time some tasks, all tasks waited
SWAP_FULL = 80          # % used
MIB = 2**20

TICK = os.sysconf("SC_CLK_TCK")  # /proc's time unit, per s
KTHREAD = 0x00200000  # PF_KTHREAD among /proc/PID/stat's flags

# The time heat has throttled the CPU since boot, in ms; Intel only (AMD has
# no thermal_throttle). The kernel times only the spells that begin within
# 10 °C of the CPU's limit (therm_throt.c): its count of throttles is no
# heat signal, the notebook logs some at 45 °C, none of them timed. It is
# the package's; every CPU keeps a copy, and the first one's will do.
THROTTLED = ("/sys/devices/system/cpu/cpu0/thermal_throttle/"
             "package_throttle_total_time_ms")

# Ending an app from its row, two clicks each time (cardkit's Arm): SIGTERM
# to the processes at the top of its tree, which take the rest with them;
# if any of its processes are still there END_WAIT ms later, the row offers
# SIGKILL for those. A second click sooner than the desktop's double-click
# time belongs to the first: a double-click arms, it never ends an app.
END_WAIT = 5000   # ms
NOTE_MS = ARM_MS  # how long a row's note ("Ended", why it can't) stays

# Where the CPU package temperature comes from, first found wins: hwmon
# driver and the labels of the input to read. Intel's coretemp; AMD's
# k10temp, Tdie where the chip has one, else the control temperature Tctl
# (the same on most); zenpower, which some set up instead of k10temp.
CPU_SENSORS = [("coretemp", ("Package id 0",)),
               ("k10temp", ("Tdie", "Tctl")),
               ("zenpower", ("Tdie", "Tctl"))]
GPU_DRIVERS = {"amdgpu", "radeon", "nouveau"}  # their fans are the card's
# An Intel GPU under its DRM card: the render GT's time spent idle (RC6) in
# ms and the clock asked of it in MHz (the actual one reads 0 in RC6), and
# the idle time of the media GT, which decodes and encodes video apart from
# the rendering (Meteor Lake on; before it, video runs on the one GT).
# i915's files, then xe's.
INTEL_GT = [("gt/gt0/rc6_residency_ms", "gt/gt0/rps_cur_freq_mhz",
             "gt/gt1/rc6_residency_ms"),
            ("device/tile0/gt0/gtidle/idle_residency_ms",
             "device/tile0/gt0/freq0/cur_freq",
             "device/tile0/gt1/gtidle/idle_residency_ms")]

# The apps keeping the Intel GPU busy, from the kernel's figures for each
# open GPU file (DRM fdinfo, drm-usage-stats): its busy time per engine.
# Only your own processes' files can be read, and Hyprland's can't: it
# holds cap_sys_nice, which makes it undumpable. Finding the files takes a
# look at every open file of every process: 13-18 ms for 400 processes and
# 5,700 files here at the usual clocks, three or four times that with the
# processor held near 1 GHz. So a thread looks, GPU_LOOK at most per
# sample, the busiest processes first, until it has looked at them all,
# then at those new since, and at them all again every GPU_RESCAN samples;
# the files found are read every sample (3 ms).
GPU_LOOK = 0.010  # s
GPU_RESCAN = 15   # samples, 30 s
GPU_APPS = 3      # apps named
GPU_QUIET = 0.5   # %, less isn't named

# Intel's hybrid processors list their kinds of core as two PMUs, the
# performance cores and the efficient ones; among the efficient ones, the
# low-power island's (Meteor Lake on) have the lowest top clock. The bars
# go in a group per kind, named under it by the longest name that fits.
HYBRID = ("/sys/devices/cpu_core/cpus", "/sys/devices/cpu_atom/cpus")
P_CORES = ("P-cores", "P")
E_CORES = ("E-cores", "E")
LP_CORES = ("LP E-cores", "LP E", "LP")
BARS_H = 24   # px, the bars' height
NAMES_H = 15  # px under them for the groups' names, on a hybrid processor

# One line per card every POLL s, until it is stopped. Line-buffered by
# stdbuf in case nvidia-smi leaves a pipe to stdio's block buffering, which
# would hold its lines back for minutes.
NVIDIA_SMI = ["stdbuf", "-oL", "nvidia-smi", "--format=csv,noheader,nounits",
              f"--loop={POLL}",
              "--query-gpu=index,name,utilization.gpu,temperature.gpu,"
              "memory.used,memory.total,power.draw"]
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
NVIDIA_CACHE = os.path.join(RUNTIME, "bar-popup-nvidia.json")  # its last lines

# The bar's net-speed.sh keeps its last samples on tmpfs, a file per bar
# (waybar runs it once per monitor): a header "IFACE SINCE RX_TOTAL
# TX_TOTAL METERED" (IFACE "-" while there is no default route; SINCE, in
# s since the epoch, when the totals began; the totals in bytes; METERED
# "metered" or "-"), then "T RX TX" per sample, oldest first, T in µs since
# the epoch, the rates in B/s. It links a whole new file into place every
# 2 s, also with no route: one not rewritten for NET_STALE s was left by a
# bar that ended without tidying up, and one missing for a moment is being
# replaced. (net-speed.sh has the writer's side.)
NET_HIST = os.path.join(RUNTIME, "net-speed.*.hist")
NET_SPAN = 180         # s the sparkline shows, all that net-speed.sh keeps
NET_STALE = 6          # s
NET_GAP = 5            # s between two samples that breaks the line
NET_FLOOR = 64 * 1024  # B/s, the least full scale: idle chatter stays flat
SPARK_H = 36           # px

# Programs that run a script named on their command line: their processes
# go by the script's name.
INTERPRETER = re.compile(r"(python|perl|ruby|bash|dash|sh|zsh)[\d.]*")

# Nerd Font glyphs; the processor's and memory's are the bar's.
ICON = {
    "cpu": "\U000f0ee0",     # md-cpu_64_bit
    "memory": "\U000f035b",  # md-memory
    "disk": "\U000f02ca",    # md-harddisk
    "gpu": "\U000f0fb2",     # md-expansion_card_variant, as gpu.sh's
    "uptime": "\U000f0150",  # md-clock_outline
    "load": "\U000f029a",    # md-gauge
    "btop": "\U000f012a",    # md-chart_line
    "sorted": "\U000f0140",  # md-chevron_down, as the Network card's fold
    "net": "\U000f04e2",     # md-swap_vertical
    "power": "\U000f0241",   # md-flash
}

CSS = b"""
/* System card: a meter under each figure, a bar per core, the network's
   last minutes as a sparkline, the apps using the CPU or the memory as a
   small table, and uptime and load as a footer. */
.card.system .val { color: @text; }
.card.system .meter { padding: 0 10px 2px 46px; }
.card.system levelbar trough {
    background-color: @raised;
    background-image: none;
    border: none;
    border-radius: 3px;
    min-height: 6px;
    padding: 0;
}
.card.system levelbar block {
    border: none;
    border-radius: 3px;
    min-height: 6px;
}
.card.system levelbar block.filled { background-color: @accent; }
.card.system levelbar block.empty { background-color: transparent; }
.card.system levelbar.warn block.filled { background-color: @warn; }
.card.system levelbar.bad block.filled { background-color: @crit; }
/* On battery, the power drawn, beside the title: the Battery card's status
   pill, in the bar's battery colour. */
.card.system .power {
    background-color: @raised;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: bold;
    color: @yellow;
}
/* Worth a look: heat throttling since the card opened, an app that didn't
   quit when asked. */
.card.system .sub.warn { color: @warn; }
/* The network's sparkline lines up with the meters; its scale, the rate at
   its top, stands in the glyph column beside its top edge. */
.card.system .spark { padding: 0 10px 2px 10px; }
.card.system .axis { font-size: 11px; color: @subtext0; }
/* The apps' rows are buttons, tighter than list rows: two clicks end the
   app. Armed, a row is red like the other cards' armed rows (this beats
   .row:hover), and its note says what the next click does. */
.card.system .app { padding: 1px 10px; min-height: 0; }
.card.system .row.armed { background-color: alpha(@crit, 0.1); }
.card.system .row.armed .name, .card.system .row.armed .sub { color: @crit; }
.card.system .row.armed:focus { outline-color: @crit; }
/* The apps list's heading: its title where it always was, then the columns
   it can be sorted by, each heading over its column; the one in force in
   text colour, with a chevron. */
.card.system .apphead { padding-right: 10px; }
.card.system .sort {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    min-height: 0;
    padding: 0;
    margin-top: 4px;
    border-radius: 6px;
    color: @subtext0;
    font-size: 12px;
}
.card.system .sort:hover, .card.system .sort.on { color: @text; }
/* No padding to keep the ring off the text: it goes round the outside. */
.card.system .sort:focus { outline-offset: 2px; -gtk-outline-radius: 6px; }
.card.system .foot { padding: 6px 10px 0; }
.card.system .foot .icon { font-size: 15px; color: @subtext0; }
"""

# @warn and @crit for heat in the status lines' markup; the core bars drawn
# as the stylesheet would, @raised filled by load in @accent, @warn from
# 60 % and @crit from 90 %.
WARM, HOT = PALETTE["warn"], PALETTE["crit"]
TROUGH = MOCHA["raised"]
CORE = [(90, MOCHA["crit"]), (60, MOCHA["warn"]), (0, MOCHA["accent"])]
SUBTEXT = MOCHA["subtext0"]  # the core groups' names, as .sub
# The network: download in sapphire, the bar's net-speed colour, filled on
# the sparkline; upload in the card's accent, as a line over it. On a
# metered link the rates are @warn, as the bar has them.
DOWN, UP = "sapphire", "accent"
METERED = PALETTE["warn"]


def read_int(path):
    try:
        with open(path, "rb") as fh:
            return int(fh.read())
    except (OSError, ValueError):
        return None


# --- What the machine has: looked up once per card ---------------------------

def hwmons():
    """[(driver, directory)] of the hwmon devices, in index order."""
    dirs = glob.glob("/sys/class/hwmon/hwmon[0-9]*")
    dirs.sort(key=lambda d: int(d.rpartition("hwmon")[2]))
    return [(read_text(f"{d}/name"), d) for d in dirs]


def cpu_sensor(found):
    """The CPU package temperature's input file, or None."""
    drivers = {}
    for name, d in found:
        drivers.setdefault(name, d)
    for driver, labels in CPU_SENSORS:
        d = drivers.get(driver)
        if d is None:
            continue
        inputs = {read_text(p.replace("_input", "_label")): p
                  for p in glob.glob(f"{d}/temp[0-9]*_input")}
        return next((inputs[lab] for lab in labels if lab in inputs),
                    f"{d}/temp1_input")
    return None


def cpu_heat(sensor):
    """(warm, hot) °C for the CPU: 20 and 10 °C under the limit its sensor
    gives (coretemp's _crit, TjMax: 105 °C here), else CPU_HEAT. AMD's
    k10temp gives none."""
    limit = read_int(sensor.replace("_input", "_crit")) if sensor else None
    if limit and 80000 <= limit <= 130000:
        return limit / 1000 - 20, limit / 1000 - 10
    return CPU_HEAT


def fan_inputs(found):
    """The rpm inputs of the fans to show: those labelled as the CPU's where
    any are, else all but a graphics card's own. (The Dell here reports its
    one fan twice, as dell_ddv's "CPU Fan" and unlabelled through dell_smm.)"""
    inputs = [p for name, d in found if name not in GPU_DRIVERS
              for p in glob.glob(f"{d}/fan[0-9]*_input")]
    cpu = [p for p in inputs if re.search(
        "cpu|processor", read_text(p.replace("_input", "_label")), re.I)]
    return cpu or inputs


def disk_sensor():
    """(input, warm, hot) for the temperature of the drive the root file
    system is on, the limits in °C from the drive's own _max and _crit (an
    NVMe drive's warning and critical composite temperatures, 82.85 and
    84.85 °C here), or None. Found from the mount's source, which a btrfs
    root's device number wouldn't give: through device mapper (LUKS, LVM)
    to the disk under it, then up to the first device with a hwmon, an
    NVMe controller (or a SATA disk, where drivetemp is loaded)."""
    source = ""
    try:
        with open("/proc/self/mountinfo") as fh:
            for line in fh:
                fields = line.split()
                if fields[4] == "/":  # the last one wins
                    source = fields[fields.index("-") + 2]
    except (OSError, IndexError, ValueError):
        return None
    if not source.startswith("/dev/"):
        return None  # a tmpfs or network root
    d = f"/sys/class/block/{os.path.basename(os.path.realpath(source))}"
    while slaves := glob.glob(f"{d}/slaves/*"):
        d = f"/sys/class/block/{os.path.basename(slaves[0])}"
    d = os.path.realpath(d)
    while d.startswith("/sys/devices/"):
        for hw in sorted(glob.glob(f"{d}/hwmon[0-9]*")
                         + glob.glob(f"{d}/hwmon/hwmon[0-9]*")):
            if os.path.exists(f"{hw}/temp1_input"):
                # An unset limit reads 65261.85 °C: -1, taken as unsigned.
                limits = (read_int(f"{hw}/temp1_{k}") for k in ("max", "crit"))
                return (f"{hw}/temp1_input",
                        *(v / 1000 if v and v < 200000 else math.inf
                          for v in limits))
        d = os.path.dirname(d)
    return None


def intel_gt():
    """(idle counter, clock, power state, the media GT's idle counter or
    None) files of an Intel GPU, and its PCI address (0000:00:02.0), which
    the kernel's figures for its clients go by; or None."""
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        if not card.rpartition("card")[2].isdigit():
            continue  # a connector, card1-eDP-1
        for idle, clock, media in INTEL_GT:
            if os.path.exists(f"{card}/{idle}"):
                media = f"{card}/{media}"
                return (f"{card}/{idle}", f"{card}/{clock}",
                        f"{card}/device/power/runtime_status",
                        media if os.path.exists(media) else None,
                        os.path.basename(os.path.realpath(f"{card}/device")))
    return None


def cpu_list(text):
    """sysfs's '0-3,8' -> {0, 1, 2, 3, 8}."""
    out = set()
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        if lo.isdigit() and (hi or lo).isdigit():
            out.update(range(int(lo), int(hi or lo) + 1))
    return out


def top_clock(cpu):
    """CPU `cpu`'s highest clock in kHz, 0 if unknown."""
    return read_int(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/"
                    "cpuinfo_max_freq") or 0


def core_groups(cpus):
    """[(names, [positions in cpus])], `cpus` being the CPU numbers in the
    bars' order: a group per kind of core on an Intel hybrid processor,
    else one group with no names, which draws as before."""
    big, small = (cpu_list(read_text(p)) for p in HYBRID)
    if not big or not small:
        return [((), list(range(len(cpus))))]
    fastest = max(map(top_clock, small))
    groups = {P_CORES: [], E_CORES: [], LP_CORES: []}
    for i, cpu in enumerate(cpus):
        kind = (P_CORES if cpu in big
                else E_CORES if top_clock(cpu) >= fastest else LP_CORES)
        groups[kind].append(i)
    return [(names, members) for names, members in groups.items() if members]


# --- Counters: a sample ------------------------------------------------------

def uptime():
    """Seconds since boot."""
    return float(read_text("/proc/uptime").split()[0])


def cpu_times():
    """{'cpu': (busy, total), 'cpu0': ...} in ticks since boot, the
    machine's first. Idle counts iowait too; guest time is already in
    user."""
    out = {}
    with open("/proc/stat") as fh:
        for line in fh:
            if not line.startswith("cpu"):
                break
            name, *vals = line.split()
            vals = [int(v) for v in vals[:8]]
            total = sum(vals)
            out[name] = (total - vals[3] - vals[4], total)
    return out


def processes():
    """{pid: (start, cpu ticks, resident pages, comm, kernel thread,
    parent)}, one read of /proc/PID/stat each: the bulk of a sample's
    cost."""
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
            out[int(pid)] = (int(f[19]), int(f[11]) + int(f[12]), int(f[21]),
                             data[data.find(b"(") + 1:rp],
                             bool(int(f[6]) & KTHREAD), int(f[1]))
        except (OSError, IndexError, ValueError):
            continue  # gone in the meantime
    return out


def started(pid):
    """When process `pid` started, in clock ticks after boot (what tells
    it from a later process given the same pid), or None if it is gone."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        return int(data[data.rfind(b")") + 2:].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def gt_idle(gt):
    """The GTs' idle times in ms, the render GT's and the media GT's (None
    without one), or None while the GPU is suspended: asleep it is idle
    anyway, and reading its counters might wake it."""
    if gt is None or read_text(gt[2]) != "active":
        return None
    return read_int(gt[0]), read_int(gt[3]) if gt[3] else None


def meminfo():
    """/proc/meminfo in bytes: {'MemTotal': ..., 'MemAvailable': ...}."""
    info = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0]) * 1024
    return info


def pressure(resource):
    """(some, full): the % of the last 10 s in which some, or all, tasks
    waited for `resource` ('memory', 'cpu'); None where the kernel keeps
    no such figures (booted with psi=0)."""
    out = [0.0, 0.0]
    try:
        with open(f"/proc/pressure/{resource}") as fh:
            for line in fh:
                kind, avg10 = line.split()[:2]
                out[kind == "full"] = float(avg10.partition("=")[2])
    except (OSError, IndexError, ValueError):
        return None
    return tuple(out)


def swaps():
    """{'zram': (size, used), 'disk': (size, used)} in bytes: zram's swap
    devices, compressed in RAM, and the others, on disk. A kind there is
    none of is left out."""
    out = {}
    try:
        with open("/proc/swaps") as fh:
            next(fh, None)  # the heading
            for line in fh:
                f = line.split()
                kind = "zram" if f[0].startswith("/dev/zram") else "disk"
                total, used = out.get(kind, (0, 0))
                out[kind] = (total + int(f[2]) * 1024,
                             used + int(f[3]) * 1024)
    except (OSError, IndexError, ValueError):
        pass
    return out


def memory_tone(info, swap, some, full):
    """'bad', 'warn' or '': where the bar's memory figure is red, peach or
    neither (memory.sh, with its whole percentages; see MEM_USED)."""
    total, avail = info["MemTotal"], info["MemAvailable"]
    used = 100 * (total - avail) // total
    # zram where there is any, else the swap there is; none counts as full.
    room, taken = swap.get("zram") or swap.get("disk") or (0, 0)
    swap_full = not room or 100 * taken // room >= SWAP_FULL
    if full >= MEM_WAIT[1] or (used >= MEM_USED[1] and swap_full):
        return "bad"
    if used >= MEM_USED[0] or some >= MEM_WAIT[0]:
        return "warn"
    return ""


class Sample:
    """The machine's counters at one moment; the load over a while is the
    difference between two."""

    def __init__(self, gt):
        # Uptime before the process list: a process the last sample missed
        # is new only if it started after that sample began (see _apps).
        self.up = uptime()
        self.cpus = cpu_times()
        self.procs = processes()
        self.at = time.monotonic()
        self.gt_idle = gt_idle(gt)


def net_history():
    """What a bar's net-speed.sh last wrote: ((iface, since, rx total, tx
    total, metered), [(t, rx, tx), ...]), t in s since the epoch. Of the
    files rewritten within NET_STALE s the one with the most samples (a
    bar that came with a monitor has fewer); None if there is none, or if
    the one there is was being replaced just then."""
    best, now = None, time.time()
    for path in glob.glob(NET_HIST):
        try:
            if os.stat(path).st_mtime < now - NET_STALE:
                continue  # left by a bar that ended without tidying up
            with open(path) as fh:
                head, *rows = fh.read().splitlines()
            fields = head.split()
            iface, since, rx_total, tx_total = fields[:4]
            head = (iface, int(since), int(rx_total), int(tx_total),
                    fields[4:5] == ["metered"])
            samples = [(int(t) / 1e6, int(rx), int(tx))
                       for t, rx, tx in map(str.split, rows)]
        except (OSError, ValueError):
            continue
        if best is None or len(samples) > len(best[1]):
            best = head, samples
    return best


def link_kind(iface):
    """'Wi-Fi', 'Ethernet', or the interface's own name (a VPN's, say)."""
    if os.path.isdir(f"/sys/class/net/{iface}/wireless"):
        return "Wi-Fi"
    if read_text(f"/sys/class/net/{iface}/type") == "1":  # ARPHRD_ETHER
        return "Ethernet"
    return iface


def drm_fds(pid):
    """The files process `pid` has open on a GPU (/dev/dri/*), as fd
    numbers; none if it has none, or if they aren't ours to see."""
    base = f"/proc/{pid}/fd"
    try:
        names = os.listdir(base)
    except OSError:
        return []
    out = []
    for fd in names:
        try:
            if os.readlink(f"{base}/{fd}").startswith("/dev/dri/"):
                out.append(fd)
        except OSError:
            continue  # closed in the meantime
    return out


def drm_usage(pid, fd):
    """(the GPU's PCI address, client id, {engine: (busy, total,
    capacity)}) from a GPU file's fdinfo, or None if it is closed or says
    nothing of the kind. Busy is ns of the engine's time, total None
    (i915, amdgpu), or busy cycles out of the engine's total cycles (xe);
    capacity is how many engines the name stands for (video: 2 here)."""
    try:
        with open(f"/proc/{pid}/fdinfo/{fd}", "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    keys = dict(line.partition(b":")[::2] for line in data.split(b"\n"))
    pdev, client = keys.get(b"drm-pdev"), keys.get(b"drm-client-id")
    if pdev is None or client is None:
        return None
    engines = {}
    try:
        for key, value in keys.items():
            if key.startswith(b"drm-engine-capacity-"):
                continue
            if key.startswith(b"drm-engine-"):
                name, busy, total = key[11:], value.split()[0], None
            elif key.startswith(b"drm-cycles-"):
                name, busy = key[11:], value
                total = int(keys.get(b"drm-total-cycles-" + name, b"0"))
            else:
                continue
            capacity = int(keys.get(b"drm-engine-capacity-" + name, b"1"))
            engines[name.decode()] = (int(busy), total, max(1, capacity))
    except ValueError:
        return None
    return pdev.strip(), client.strip(), engines


class GpuClients:
    """Your processes' clients of one GPU, with their busy time per engine
    (fdinfo). scan() looks through processes' open files for the GPU's,
    for GPU_LOOK at most a call, and reads the GPU files found on each
    call. It runs in a thread, one call at a time; the card works the
    shares out from two calls' readings (gpu_use)."""

    def __init__(self, pdev):
        self.pdev = pdev.encode()
        self.holders = {}  # pid -> its GPU files, of this GPU or another
        self.seen = set()  # the pids looked through
        self.calls = 0

    def scan(self, pids):
        """{client id: (pid, when read in ns, {engine: (busy, total,
        capacity)})} for this GPU's clients. `pids` are the last sample's
        processes, but the kernel's threads (they have no files), with the
        CPU time each has used: the busiest are looked through first. A
        client several processes share (a GPU file inherited, or handed
        over) goes to the lowest pid, as a rule the one that opened it."""
        if self.calls % GPU_RESCAN == 0:
            self.seen = set()  # all again, a few at a time
        self.calls += 1
        self.seen &= pids.keys()
        end = time.monotonic() + GPU_LOOK
        for pid in sorted(pids.keys() - self.seen, key=pids.get,
                          reverse=True):
            if time.monotonic() > end:
                break  # the rest next time
            self.seen.add(pid)
            fds = drm_fds(pid)
            if fds:
                self.holders[pid] = fds
            else:
                self.holders.pop(pid, None)
        clients = {}
        for pid in sorted(self.holders):
            if pid not in pids:
                del self.holders[pid]
                continue
            for fd in self.holders[pid]:
                at = time.monotonic_ns()
                usage = drm_usage(pid, fd)
                if usage is not None and usage[0] == self.pdev:
                    clients.setdefault(usage[1], (pid, at, usage[2]))
        return clients


def gpu_use(old, new):
    """{pid: {engine: % of its time}} between two of GpuClients.scan()'s
    readings, a process's clients added up; a client new since `old` waits
    for the next reading."""
    out = {}
    for client, (pid, at, engines) in new.items():
        was = old.get(client)
        if was is None or was[0] != pid or at <= was[1]:
            continue
        use = out.setdefault(pid, {})
        for name, (busy, total, capacity) in engines.items():
            before = was[2].get(name)
            if before is None:
                continue
            if total is None:
                part = (busy - before[0]) / (at - was[1])
            elif total > before[1]:
                part = (busy - before[0]) / (total - before[1])
            else:
                continue
            use[name] = use.get(name, 0.0) + max(0.0, 100 * part / capacity)
    return out


# --- Ending an app -----------------------------------------------------------

def compositor():
    """The pid of the Wayland compositor the card is on, at the other end
    of its socket (SO_PEERCRED): Hyprland, or the lab's sway. None if its
    socket can't be reached."""
    path = os.path.join(RUNTIME, os.environ.get("WAYLAND_DISPLAY",
                                                "wayland-0"))
    size = struct.calcsize("3i")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.connect(path)
            creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                    size)
    except OSError:
        return None
    return struct.unpack("3i", creds)[0] or None


def lineage(pid):
    """`pid` and the processes it runs under, up to pid 1."""
    out = []
    while pid and pid > 1 and pid not in out:
        out.append(pid)
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                data = fh.read()
            pid = int(data[data.rfind(b")") + 2:].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return out


def ours(pid):
    """Whether process `pid` is the user's, so this card may signal it."""
    try:
        return os.stat(f"/proc/{pid}").st_uid == os.getuid()
    except OSError:
        return False


def open_process(pid, start):
    """A pidfd for `pid` while it is the process that started at `start`,
    or None: a pid that came free since the sample may belong to another
    process now, and a pidfd keeps signals from going astray."""
    try:
        fd = os.pidfd_open(pid)
    except OSError:
        return None
    if started(pid) != start:
        os.close(fd)
        return None
    return fd


def exited(fd):
    """Whether the process behind pidfd `fd` has ended: it reads as ready
    from then on."""
    return bool(select.select([fd], [], [], 0)[0])


# --- Naming apps -------------------------------------------------------------

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


def held(pids):
    """Bytes the processes hold for themselves: their private memory,
    resident less what they share (libraries, shared memory), as GNOME
    System Monitor counts it, so an app's processes add up without counting
    a library twice; and what of it went to swap, so an app that sits idle
    while memory is short, swapped out, still shows what it holds. One read
    of /proc/PID/status each, about 20 µs."""
    kib = 0
    for pid in pids:
        try:
            fd = os.open(f"/proc/{pid}/status", os.O_RDONLY)
            try:
                data = os.read(fd, 4096)
            finally:
                os.close(fd)
            for key in (b"\nRssAnon:", b"\nVmSwap:"):
                at = data.find(key)
                if at >= 0:  # a zombie has neither
                    kib += int(data[at + len(key):data.index(b"kB", at)])
        except (OSError, ValueError):
            continue  # gone in the meantime
    return kib * 1024


# --- Formatting --------------------------------------------------------------

def size(n):
    """'1.7 GiB', '78 GiB', '795 MiB': a decimal only under 10."""
    for unit, scale in (("TiB", 2**40), ("GiB", 2**30), ("MiB", 2**20)):
        if n >= scale:
            break
    else:
        unit, scale = "KiB", 2**10
    value = n / scale
    return f"{value:.1f} {unit}" if value < 9.95 else f"{value:.0f} {unit}"


def of(used, total):
    """'7.9 / 15.1 GiB', '143 / 233 GiB': both in the total's unit."""
    unit, scale = ("TiB", 2**40) if total >= 2**40 else ("GiB", 2**30)
    digits = 1 if total / scale < 100 else 0
    return f"{used / scale:.{digits}f} / {total / scale:.{digits}f} {unit}"


def percent(value):
    """A decimal under 10 %, where most apps are."""
    return f"{value:.1f}%" if value < 9.95 else f"{value:.0f}%"


def since(seconds):
    """'1 d 5 h', '5 h 12 min', '12 min'."""
    days, rest = divmod(int(seconds), 86400)
    hours, mins = divmod(rest // 60, 60)
    if days:
        return f"{days} d {hours} h"
    return f"{hours} h {mins} min" if hours else f"{mins} min"


def spell(ms):
    """'12.4 s', '3 min': how long heat has throttled, put as cpu-temp.sh
    puts it in the bar's tooltip (tenths cut, whole minutes)."""
    if ms >= 60000:
        return f"{ms // 60000} min"
    return f"{ms // 1000}.{ms // 100 % 10} s"


def rate(bps):
    """'31 KiB/s', '1.5 MiB/s', '640 B/s'."""
    return f"{size(bps)}/s" if bps >= 1024 else f"{bps} B/s"


def brief(bps):
    """'64K', '512K', '4.8M', '12M', '1.1G': a rate as the bar puts it,
    for the sparkline's scale."""
    if bps < 999.5 * 2**10:
        return f"{bps / 2**10:.0f}K"
    if bps < 999.5 * 2**20:
        value = bps / 2**20
        return f"{value:.1f}M" if value < 9.95 else f"{value:.0f}M"
    return f"{bps / 2**30:.1f}G"


def heat(celsius, limits):
    """The temperature as markup: @warn when warm, @crit when hot."""
    text = f"{celsius:.0f} °C"
    warm, hot = limits
    colour = HOT if celsius >= hot else WARM if celsius >= warm else None
    return f'<span foreground="{colour}">{text}</span>' if colour else text


def number(text):
    try:
        return int(text)
    except ValueError:
        return None  # "[N/A]", "[Not Supported]"


def decimal(text):
    try:
        return float(text)
    except ValueError:
        return None  # "[N/A]", "[Not Supported]"


# --- Widgets -----------------------------------------------------------------

class Reading(Gtk.Box):
    """A figure kept current: glyph and name with the figure at the right
    end, a status line under them as wide as the card allows, and a meter
    under that unless `meter` is False."""

    def __init__(self, icon, name, meter=True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        end = Pango.EllipsizeMode.END
        top = Gtk.Box(spacing=12)
        top.get_style_context().add_class("static")
        glyph = label(icon, "icon", xalign=0.5)
        glyph.set_size_request(24, -1)
        top.pack_start(glyph, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        line = Gtk.Box(spacing=12)
        line.pack_start(label(name, "name", ellipsize=end), True, True, 0)
        self.value = label("", "val", xalign=1.0)
        line.pack_end(self.value, False, False, 0)
        text.pack_start(line, False, False, 0)
        self.sub = label("", "sub", ellipsize=end)
        # One line, cut short by the ellipsis, except for an error, which
        # drops it (update). Wrapping from the start makes the card size its
        # height for its width when first shown, so an error that comes
        # later gets all its lines.
        self.sub.set_line_wrap(True)
        self.sub.set_lines(1)
        text.pack_start(self.sub, False, False, 0)
        top.pack_start(text, True, True, 0)
        self.pack_start(top, False, False, 0)
        self.meter = None
        if meter:
            self.meter = Gtk.LevelBar()
            for offset in ("low", "high", "full"):  # no theme colours
                self.meter.remove_offset_value(offset)
            self.track = under(self.meter)
            # Hidden under an error by update(), and kept so by show_all().
            self.track.show_all()
            self.track.set_no_show_all(True)
            self.pack_start(self.track, False, False, 0)

    def update(self, value, sub, fill=0.0, limits=None, bad=False, tone=None):
        """Put up new figures: `value` and `sub` are markup, `fill` the
        meter's 0..1, whose `limits` (warn, bad) colour it, unless `tone`
        ('warn', 'bad' or '') says the colour itself. A `bad` status line
        is an error in red, wrapped rather than cut so that all of it shows,
        and the meter makes way for it."""
        self.value.set_markup(value)
        self.sub.set_markup(sub)
        self.sub.set_ellipsize(Pango.EllipsizeMode.NONE if bad
                               else Pango.EllipsizeMode.END)
        context = self.sub.get_style_context()
        (context.add_class if bad else context.remove_class)("bad")
        if self.meter is None:
            return
        self.track.set_visible(not bad)
        fill = max(0.0, min(1.0, fill))
        self.meter.set_value(fill)
        if tone is None:
            warn, worst = limits or (2, 2)
            tone = "bad" if fill >= worst else "warn" if fill >= warn else ""
        context = self.meter.get_style_context()
        for cls in ("warn", "bad"):
            (context.add_class if cls == tone else context.remove_class)(cls)


def bar(cr, x0, x1, load):
    """One core's bar between x0 and x1: @raised, filled from the bottom
    by its load in @accent, @warn from 60 % and @crit from 90 %."""
    rounded_rect(cr, x0, 0, x1 - x0, BARS_H, 2)
    cr.set_source_rgb(*TROUGH)
    cr.fill_preserve()
    filled = round(BARS_H * min(load, 100) / 100)
    if filled:
        cr.save()
        cr.clip()
        cr.rectangle(x0, BARS_H - filled, x1 - x0, filled)
        cr.set_source_rgb(*next(c for limit, c in CORE if load >= limit))
        cr.fill()
        cr.restore()
    cr.new_path()


def under(widget):
    """`widget` lined up under a Reading's name, across to its figure."""
    box = Gtk.Box()
    box.get_style_context().add_class("meter")
    box.pack_start(widget, True, True, 0)
    return box


def hidden(widget):
    """`widget` hidden, all in it shown: the card's show_all() leaves it
    hidden until the card shows it."""
    widget.show_all()
    widget.set_no_show_all(True)
    widget.hide()
    return widget


def set_class(widget, cls, on):
    context = widget.get_style_context()
    (context.add_class if on else context.remove_class)(cls)


# --- The card ----------------------------------------------------------------

class SystemPanel(Panel):
    """The processor, memory, disk, network and graphics, the apps using
    the CPU or the memory now, uptime and load, sampled every POLL s while
    the card is open and updated in place; on battery, the power drawn, in
    the title row. The card shows before its first sample: what needs no
    pair of samples at once, the rest as it fades in."""

    title = "System"
    css = "system"

    def __init__(self, app):
        super().__init__(app)
        self.header_button(ICON["btop"], "Open btop", "kitty", "btop")
        # Left of btop, on battery: the power drawn (_power_line).
        self.power = label("", "power", xalign=0.5)
        self.power.set_valign(Gtk.Align.CENTER)
        self.power.set_no_show_all(True)
        self.header.pack_end(self.power, False, False, 0)
        self.gpus = {}       # NVIDIA card index -> Reading
        self.gpu_lines = {}  # NVIDIA card index -> nvidia-smi's last line
        self.gpu_error = ""
        # First, so that its first lines come in while the card opens.
        self.nvidia = self._start_nvidia()
        found = hwmons()
        self.sensor = cpu_sensor(found)
        self.cpu_heat = cpu_heat(self.sensor)
        self.fans = fan_inputs(found)
        self.drive = disk_sensor()  # the root drive's temperature, limits
        self.drive_temp = None      # its °C as last read
        self.throttle_base = None   # heat throttling's ms as the card opened
        self.clocks = {  # CPU number -> its clock's file
            int(re.search(r"cpu(\d+)/cpufreq", p).group(1)): p
            for p in glob.glob(
                "/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")}
        self.gt = intel_gt()
        # Who keeps the Intel GPU busy: readings of its clients, taken in a
        # thread, each measured against the one before.
        self.gpu_clients = GpuClients(self.gt[4]) if self.gt else None
        self.gpu_reading = None
        self.gpu_scanning = False
        self.cpus = sorted(int(n[3:]) for n in cpu_times() if n != "cpu")
        self.groups = core_groups(self.cpus)  # the bars', by kind of core
        self.tops = {c: top_clock(c) for c in self.cpus}  # kHz, for their tip
        self.names = {}     # (pid, start) -> app, so a process is named once
        self.cores = []     # load per core in %, for the bars
        self.shares = {}    # app -> its CPU share, half of it last sample's
        self.listed = None  # the last sample's apps, to sort them again
        self.count = 0      # samples taken
        self.rpm = None     # the fan's speed as last read
        self.cpu_value, self.cpu_bits = "", []  # the processor's figures
        self.prev = None    # the last sample
        # The network: the bar's samples as last read and when, kept a
        # moment while the file is being replaced; what the sparkline shows.
        self.net_read, self.net_seen = None, 0.0
        self.net_shown, self.net_top = [], NET_FLOOR
        # Ending an app: the row awaiting its 2nd click, keyed (what, app),
        # what being "end" or "force"; when it was armed, to tell a
        # double-click, and how many processes the click would reach.
        self.arm = Arm(self, self._on_arm)
        self.armed_at, self.arm_count = 0.0, 0
        self.ending = {}  # app -> {"stage", its processes' pidfds, timer}
        self.notes = {}   # app -> (note, tone, timer): "Ended", why not
        self.keep = None  # the pids never to signal, found on the 1st click
        self.row_apps = [None] * TOP     # the app each row shows
        self.row_figs = [(0.0, 0)] * TOP  # and its CPU share and memory
        self._poll_id = None
        # The power mode heads the processor's status line: it is what
        # holds the clocks down. UPower brings the power drawn on battery.
        # The proxies follow them; opening a card never starts a daemon.
        self.ppd = self.upower = None
        flags = Gio.DBusProxyFlags.DO_NOT_AUTO_START
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, flags, None, *PPD,
                                  None, self._ppd_ready)
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, flags, None, *UPOWER,
                                  None, self._upower_ready)
        self.fill()
        # What needs no pair of samples goes up at once, memory first: while
        # it is short, the apps list opens sorted by memory.
        self._memory()
        self.sort = "memory" if self.mem_tone else "cpu"
        self._show_sort()
        self._throttle()
        self._disk()
        self._read_drive()
        self._network()
        self.uptime.set_text(f"up {since(uptime())}")
        self._load()
        self.connect_after("draw", self._drawn)
        self.connect("destroy", self._closed)

    def fill(self):
        end = Pango.EllipsizeMode.END
        self.cpu = Reading(ICON["cpu"], "Processor", meter=False)
        self.body.pack_start(self.cpu, False, False, 0)
        self.bars = Gtk.DrawingArea()
        self.bars.connect("draw", self._draw_cores)
        self._size_bars()
        self.body.pack_start(under(self.bars), False, False, 0)
        # Under the bars once the kernel has timed heat throttling.
        self.throttled = label("", "sub", ellipsize=end)
        self.throttled.set_tooltip_text(
            "How long the processor has run slower to cool down. Peach: it "
            "has done so since this card opened")
        self.throttle_row = hidden(under(self.throttled))
        self.body.pack_start(self.throttle_row, False, False, 0)
        self.memory = Reading(ICON["memory"], "Memory")
        self.body.pack_start(self.memory, False, False, 0)
        self.disk = Reading(ICON["disk"], "Disk")
        self.body.pack_start(self.disk, False, False, 0)

        # The network: the bar's net-speed samples, so the sparkline shows
        # the last NET_SPAN s the moment the card opens; hidden without a
        # bar's. Its scale, the rate at its top, in the glyph column.
        self.net = Reading(ICON["net"], "Network", meter=False)
        self.spark = Gtk.DrawingArea()
        self.spark.set_size_request(-1, SPARK_H)
        self.spark.connect("draw", self._draw_net)
        self.spark_top = label("", "axis", xalign=1.0)
        self.spark_top.set_valign(Gtk.Align.START)
        self.spark_top.set_size_request(30, -1)  # +6 spacing: the meters' 46
        spark = Gtk.Box(spacing=6)
        spark.get_style_context().add_class("spark")
        spark.pack_start(self.spark_top, False, False, 0)
        spark.pack_start(self.spark, True, True, 0)
        self.net_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.net_box.pack_start(self.net, False, False, 0)
        self.net_box.pack_start(spark, False, False, 0)
        self.body.pack_start(hidden(self.net_box), False, False, 0)

        # Graphics: a row per card, Intel's from sysfs, NVIDIA's as
        # nvidia-smi reports them, opening on what it said last time. The
        # box shows only once it has a row, so no gap stays where there is
        # nothing to show.
        self.gpu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.gpu_box.set_no_show_all(True)
        self.body.pack_start(self.gpu_box, False, False, 0)
        self.igpu = self._gpu_row("Intel Graphics") if self.gt else None
        if self.igpu is not None:
            # Under its meter, the apps keeping it busy (_gpu_scanned).
            self.gpu_apps = label("", "sub", ellipsize=end)
            self.gpu_apps.set_tooltip_text(
                "Your apps' share of the GPU's busiest engine each uses "
                "(3D, video, copy) over the last 2 s. The compositor's own "
                "share can't be read")
            line = under(self.gpu_apps)
            self.igpu.pack_start(line, False, False, 0)
            line.show_all()
        if self.nvidia:
            try:
                with open(NVIDIA_CACHE) as fh:
                    cached = json.load(fh)
            except (OSError, ValueError):
                cached = []
            for line in cached if isinstance(cached, list) else []:
                self._nvidia_line(str(line), cached=True)

        # The apps list, by CPU or by memory: the headings of its columns
        # sort it, at once, by the last sample.
        head = Gtk.Box(spacing=12)
        head.get_style_context().add_class("apphead")
        self.list_title = label("", "seclabel")
        head.pack_start(self.list_title, True, True, 0)
        self.sorts = {}
        # As wide as their columns, so each sits over its own.
        for key, text, chars in (("memory", "Memory", 9), ("cpu", "CPU", 5)):
            btn = button(label(text, xalign=1.0), "sort")
            btn.get_child().set_width_chars(chars)
            btn.set_tooltip_text(f"Sort by {text if key == 'cpu' else key}")
            btn.connect("clicked", self._on_sort, key)
            head.pack_end(btn, False, False, 0)
            self.sorts[key] = (btn, text)
        self.body.pack_start(head, False, False, 0)
        # Each app a row that two clicks end (_on_app); a note in place of
        # its figures says what the next click does, or how it went.
        self.rows = []
        for i in range(TOP):
            box = Gtk.Box(spacing=12)
            name = label("", "name", ellipsize=end)
            share = label("", "sub", xalign=1.0)
            share.set_width_chars(5)
            mem = label("", "sub", xalign=1.0)
            mem.set_width_chars(9)
            note = label("", "sub", xalign=1.0)
            note.set_no_show_all(True)
            box.pack_start(name, True, True, 0)
            box.pack_end(mem, False, False, 0)
            box.pack_end(share, False, False, 0)
            box.pack_end(note, False, False, 0)
            row = button(box, "row", "app")
            row.connect("clicked", self._on_app, i)
            self.body.pack_start(row, False, False, 0)
            self.rows.append((row, name, share, mem, note))

        foot = Gtk.Box(spacing=6)
        foot.get_style_context().add_class("foot")
        self.uptime, self.load = label("", "sub"), label("", "sub")
        for glyph, text in ((ICON["uptime"], self.uptime),
                            (ICON["load"], self.load)):
            item = Gtk.Box(spacing=6)
            item.pack_start(label(glyph, "icon"), False, False, 0)
            item.pack_start(text, False, False, 0)
            foot.pack_start(item, True, False, 0)
        self.body.pack_start(foot, False, False, 0)

    def _size_bars(self):
        """The bars' height, with room for the groups' names under them on
        a hybrid processor."""
        self.bars.set_size_request(
            -1, BARS_H + (NAMES_H if len(self.groups) > 1 else 0))

    def _gpu_row(self, name):
        """A graphics card's Reading, shown at once: rows can come after
        the card is on screen."""
        row = Reading(ICON["gpu"], name)
        self.gpu_box.pack_start(row, False, False, 0)
        row.show_all()
        self.gpu_box.show()
        return row

    def _show_sort(self):
        self.list_title.set_text("Using the most memory"
                                 if self.sort == "memory"
                                 else "Using the CPU now")
        for key, (btn, text) in self.sorts.items():
            on = key == self.sort
            btn.get_child().set_text(f"{text} {ICON['sorted']}" if on
                                     else text)
            context = btn.get_style_context()
            (context.add_class if on else context.remove_class)("on")

    def _on_sort(self, _btn, key):
        """Sorting is doing something else: an armed row and the rows'
        notes go, and the list takes the new order at once."""
        self.sort = key
        self._show_sort()
        for app in list(self.notes):
            self._unnote(app, relist=False)
        self.arm.disarm()
        if self.listed is not None:
            self._list()

    # --- Sampling ------------------------------------------------------------

    def _drawn(self, _panel, _cr):
        """On screen: the pair's first sample once this frame is out, so
        drawing it stays out of the figures."""
        self.disconnect_by_func(self._drawn)
        self._poll_id = GLib.idle_add(self._begin)
        return False

    def _begin(self):
        self.prev = Sample(self.gt)
        self._scan_gpu()
        self._poll_id = GLib.timeout_add(int(FIRST * 1000), self._first)
        return GLib.SOURCE_REMOVE

    def _first(self):
        self._sample()
        self._poll_id = GLib.timeout_add_seconds(POLL, self._sample)
        return GLib.SOURCE_REMOVE

    def _sample(self):
        cur = Sample(self.gt)
        prev, self.prev = self.prev, cur
        if self.count % FAN_EVERY == 0:
            if self.fans:
                self.rpm = max((r for r in map(read_int, self.fans)
                                if r is not None), default=None)
            if self.count:  # the first read went out as the card opened
                self._read_drive()
        self.count += 1
        self._processor(prev, cur)
        self._throttle()
        self._memory()
        self._disk()
        self._network()
        if self.igpu is not None:
            self._intel(prev, cur)
            self._scan_gpu()
        for app in list(self.ending):
            self._reap(app)
        self._apps(prev, cur)
        self.uptime.set_text(f"up {since(cur.up)}")
        self._load()
        return GLib.SOURCE_CONTINUE

    def _load(self):
        """The load average against the threads there are, which is what it
        is measured against: 7.7 is light on 14, heavy on 4. The tooltip
        adds the longer averages and how long tasks waited for a CPU."""
        one, five, fifteen = os.getloadavg()
        threads = len(self.cpus)
        self.load.set_text(f"load {one:.1f} of {threads}")
        tip = (f"Load average {one:.1f} · {five:.1f} · {fifteen:.1f} over "
               f"1, 5 and 15 min, on {threads} threads")
        waited = pressure("cpu")
        if waited is not None:
            tip += (f"\nTasks waited for a CPU {percent(waited[0])} of the "
                    "last 10 s")
        self.load.set_tooltip_text(tip)

    def _processor(self, prev, cur):
        def load(name):
            busy0, total0 = prev.cpus.get(name, (0, 0))
            busy1, total1 = cur.cpus[name]
            return (100 * (busy1 - busy0) / (total1 - total0)
                    if total1 > total0 else 0.0)

        cores = sorted((n for n in cur.cpus if n != "cpu"),
                       key=lambda n: int(n[3:]))
        cpus = [int(n[3:]) for n in cores]
        if cpus != self.cpus:  # a CPU came or went
            self.cpus, self.groups = cpus, core_groups(cpus)
            self.tops = {c: top_clock(c) for c in cpus}
            self._size_bars()
        self.cores = [load(n) for n in cores]
        self.bars.queue_draw()
        bits = []
        clock = {cpu: read_int(path) for cpu, path in self.clocks.items()}
        clocks = [k for k in clock.values() if k]
        if clocks:
            bits.append(f"{sum(clocks) / len(clocks) / 1e6:.1f} GHz")
        temp = read_int(self.sensor) if self.sensor else None
        if temp is not None:
            bits.append(heat(temp / 1000, self.cpu_heat))
        if self.rpm is not None:
            # "rpm" says fan: the word would push the line past the card's
            # width now that the power mode heads it.
            bits.append(f"{self.rpm} rpm" if self.rpm else "fan off")
        self.cpu_value, self.cpu_bits = f"{load('cpu'):.0f}%", bits
        self._cpu_line()
        self._groups_tooltip(clock)

    def _ppd_ready(self, _source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return  # no system bus: no mode to show
        if self.dead:
            return
        self.ppd = proxy  # kept, or its signals go with it
        proxy.connect("g-properties-changed", self._cpu_line)
        proxy.connect("notify::g-name-owner", self._cpu_line)
        self._cpu_line()

    def _cpu_line(self, *_):
        """The processor's figure and status line: the power mode while
        power-profiles-daemon runs, then the clock, temperature and fan."""
        if self.dead:
            return
        mode = None
        if self.ppd is not None and self.ppd.get_name_owner():
            mode = MODES.get(prop(self.ppd, "ActiveProfile"))
        bits = ([mode[1]] if mode else []) + self.cpu_bits
        self.cpu.update(self.cpu_value, " · ".join(bits))

    def _upower_ready(self, _source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return  # no system bus: no power to show
        if self.dead:
            return
        self.upower = proxy  # kept, or its signals go with it
        proxy.connect("g-properties-changed", self._power_line)
        proxy.connect("notify::g-name-owner", self._power_line)
        self._power_line()

    def _power_line(self, *_):
        """On battery, the power drawn beside the title: UPower's reading,
        which it takes from the battery as the bar's tooltip does, every
        30 s and at once when the charger comes or goes, and announces. On
        the charger it would be the charge, so it hides; with no battery
        (the desktop PC) it never shows."""
        if self.dead:
            return
        watts = prop(self.upower, "EnergyRate", 0.0)
        drawing = (self.upower is not None and self.upower.get_name_owner()
                   and prop(self.upower, "State") == DISCHARGING
                   and watts > 0)
        if drawing:
            self.power.set_text(f"{ICON['power']} {watts:.1f} W")
            self.power.set_tooltip_text(
                f"Drawing {watts:.1f} W from the battery")
        self.power.set_visible(bool(drawing))

    def _groups_tooltip(self, clock):
        """Each kind of core's load and clock, on a hybrid processor: the
        machine's average clock in the status line counts slow cores and
        fast ones alike."""
        if len(self.groups) < 2:
            return
        lines = []
        for names, members in self.groups:
            cpus = [self.cpus[i] for i in members]
            busy = sum(self.cores[i] for i in members) / len(members)
            now = [clock[c] for c in cpus if clock.get(c)]
            top = max(self.tops.get(c, 0) for c in cpus)
            ghz = (f", {sum(now) / len(now) / 1e6:.1f} of {top / 1e6:.1f} GHz"
                   if now and top else "")
            lines.append(f"{names[0]}: {len(cpus)} threads at {busy:.0f}%"
                         + ghz)
        self.bars.set_tooltip_text("\n".join(lines))

    def _throttle(self):
        """Heat throttling since boot, under the bars once the kernel has
        timed any; peach once there has been more since the card opened,
        which is heat holding the processor back now. The line goes up
        with the card, so nothing under it moves."""
        ms = read_int(THROTTLED)
        if self.throttle_base is None:
            self.throttle_base = ms or 0
        if ms:
            self.throttled.set_text(
                f"Throttled by heat for {spell(ms)} since boot")
            set_class(self.throttled, "warn", ms > self.throttle_base)
        self.throttle_row.set_visible(bool(ms))

    def _memory(self):
        """Used is what isn't available, as on the bar, and the meter takes
        the bar's colours (memory_tone). Once programs wait for memory long
        enough to colour the bar, how much of the time they wait follows
        what is available, in that colour; short of that it isn't worth the
        room. zram holds swapped-out memory compressed, in RAM, at a
        fraction of its size, so the line says what is in zram, not swap."""
        info = meminfo()
        total, avail = info["MemTotal"], info["MemAvailable"]
        some, full = pressure("memory") or (0.0, 0.0)
        swap = swaps()
        self.mem_tone = memory_tone(info, swap, some, full)
        bits = [f"{size(avail)} available"]
        if some >= MEM_WAIT[0] or full >= MEM_WAIT[1]:
            colour = HOT if full >= MEM_WAIT[1] else WARM
            bits.append(f'<span foreground="{colour}">pressure '
                        f'{int(some + 0.5)}%</span>')  # half up, as the bar's
        zram, disk = swap.get("zram"), swap.get("disk")
        if zram is None and disk is None:
            bits.append("no swap")
        elif zram is None:
            bits.append(f"swap {size(disk[1])} used" if disk[1] >= MIB
                        else "swap unused")
        else:
            bits.append(f"{size(zram[1])} in zram" if zram[1] >= MIB
                        else "zram empty")
            if disk is not None and disk[1] >= MIB:  # once zram is full
                bits.append(f"{size(disk[1])} on disk")
        self.memory.update(of(total - avail, total), " · ".join(bits),
                           (total - avail) / total, tone=self.mem_tone)

    def _disk(self):
        """The root file system as df counts it: full is when nothing is
        left for users, the blocks kept back for root aside. Then the
        temperature of the drive under it, peach and red at the drive's own
        warning and critical limits."""
        st = os.statvfs("/")
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        free = st.f_bavail * st.f_frsize
        bits = [f"{size(free)} free"]
        if self.drive_temp is not None:
            bits.append(heat(self.drive_temp, self.drive[1:]))
        self.disk.update(of(used, st.f_blocks * st.f_frsize), " · ".join(bits),
                         used / (used + free) if used + free else 0.0, FULL)

    def _read_drive(self):
        """The drive's temperature, read in GIO's worker thread: an NVMe
        drive's is a command to the drive, which the card doesn't wait
        for."""
        if self.drive is not None:
            Gio.File.new_for_path(self.drive[0]).load_contents_async(
                None, self._drive_read)

    def _drive_read(self, file, result):
        try:
            celsius = int(file.load_contents_finish(result)[1]) / 1000
        except (GLib.Error, ValueError):
            celsius = None
        if self.dead:
            return
        self.drive_temp = celsius
        self._disk()

    def _network(self):
        """The default route's rates and this link's totals, as the bar's
        net-speed.sh last wrote them, and its last NET_SPAN s for the
        sparkline. A file being replaced reads as missing for a moment, so
        the last reading holds for NET_STALE s; after that, with no bar's
        samples, the row hides."""
        got, now = net_history(), time.time()
        if got is not None:
            self.net_read, self.net_seen = got, now
        elif now - self.net_seen > NET_STALE:
            self.net_read = None
        self.net_box.set_visible(self.net_read is not None)
        if self.net_read is None:
            return
        (iface, start, rx_total, tx_total, metered), samples = self.net_read
        self.net_shown = [s for s in samples if s[0] > now - NET_SPAN]
        self.net_top = max([NET_FLOOR] + [max(rx, tx)
                                          for _, rx, tx in self.net_shown])
        self.spark_top.set_text(brief(self.net_top))
        self.spark.set_tooltip_text(
            f"The last {NET_SPAN // 60} minutes: download filled, upload as "
            f"a line, up to {rate(self.net_top)} at the top")
        self.spark.queue_draw()
        if iface == "-":
            self.net.update("", "No default route")
            self.net.set_tooltip_text(None)
            return
        value = ""
        if samples and samples[-1][0] > now - NET_STALE:
            _, rx, tx = samples[-1]
            value = (f'<span foreground="{PALETTE[DOWN]}">↓</span> {rate(rx)}'
                     f'  <span foreground="{PALETTE[UP]}">↑</span> {rate(tx)}')
            if metered:  # as the bar has it, @warn
                value = f'<span foreground="{METERED}">{value}</span>'
        stamp = "%a %H:%M" if now - start > 20 * 3600 else "%H:%M"
        self.net.update(value, GLib.markup_escape_text(
            f"{link_kind(iface)} · since "
            f"{time.strftime(stamp, time.localtime(start))}: "
            f"↓ {size(rx_total)} ↑ {size(tx_total)}"))
        self.net.set_tooltip_text(
            f"{iface} · metered: NetworkManager has it as a link whose data "
            "may cost" if metered else iface)

    def _draw_net(self, area, cr):
        """The last NET_SPAN s, newest at the right: download filled, upload
        a line over it, both on one scale whose top (the larger peak,
        NET_FLOOR at least) the label beside it names. A gap in the samples
        (a suspend, no route) breaks the lines."""
        w, h = area.get_allocated_width(), area.get_allocated_height()
        rounded_rect(cr, 0, 0, w, h, 3)
        cr.set_source_rgb(*TROUGH)
        cr.fill_preserve()
        cr.clip()
        samples = self.net_shown
        if not samples:
            return False
        top, end = self.net_top, time.time()
        runs = [[samples[0]]]
        for before, sample in zip(samples, samples[1:]):
            if sample[0] - before[0] > NET_GAP:
                runs.append([])
            runs[-1].append(sample)

        def point(t, value):
            return (w * (1 - (end - t) / NET_SPAN),
                    h - 1 - (h - 4) * value / top)

        def line(points, colour):
            cr.move_to(*points[0])
            for x, y in points[1:]:
                cr.line_to(x, y)
            cr.set_source_rgb(*MOCHA[colour])
            cr.stroke()

        cr.set_line_width(1.5)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for run in (r for r in runs if len(r) > 1):
            down = [point(t, rx) for t, rx, _ in run]
            cr.move_to(down[0][0], h)
            for x, y in down:
                cr.line_to(x, y)
            cr.line_to(down[-1][0], h)
            cr.close_path()
            cr.set_source_rgba(*MOCHA[DOWN], 0.35)
            cr.fill()
            line(down, DOWN)
            line([point(t, tx) for t, _, tx in run], UP)
        return False

    def _intel(self, prev, cur):
        """Busy is the time a GT spent out of RC6, its idle state: the
        render GT's is the figure, the media GT's joins the status line as
        video. A GPU suspended at either end of the while counts as idle."""
        busy = [0.0, 0.0]
        if prev.gt_idle is not None and cur.gt_idle is not None:
            span = (cur.at - prev.at) * 1000
            for i, (was, now) in enumerate(zip(prev.gt_idle, cur.gt_idle)):
                if was is not None and now is not None:
                    idle = (now - was) / span
                    busy[i] = max(0.0, min(100.0, 100 * (1 - idle)))
        if cur.gt_idle is None:
            sub = "asleep"
        else:
            mhz = read_int(self.gt[1])
            # With GuC's frequency control (SLPC, Meteor Lake on) the clock
            # asked for reads 0 while RC6 holds the engine: idle, not blank.
            bits = [f"{mhz} MHz" if mhz else "idle"]
            if self.gt[3]:
                bits.append(f"video {busy[1]:.0f}%")
            sub = " · ".join(bits)
        self.igpu.update(f"{busy[0]:.0f}%", sub, busy[0] / 100)

    def _scan_gpu(self):
        """A reading of the Intel GPU's clients, in a thread (GpuClients),
        over the last sample's processes; none while the last one is still
        out."""
        if self.gpu_clients is None or self.gpu_scanning:
            return
        self.gpu_scanning = True
        clients = self.gpu_clients
        pids = {pid: p[1] for pid, p in self.prev.procs.items() if not p[4]}

        def work():
            reading = None
            try:
                reading = clients.scan(pids)
            finally:
                GLib.idle_add(self._gpu_scanned, reading)
        threading.Thread(target=work, daemon=True).start()

    def _gpu_scanned(self, reading):
        """The busiest apps under the Intel row's meter: each app's busiest
        engine, its processes' clients added up, as a share of that
        engine's time since the last reading."""
        self.gpu_scanning = False
        if self.dead or reading is None:
            return GLib.SOURCE_REMOVE
        old, self.gpu_reading = self.gpu_reading, reading
        if old is None:
            return GLib.SOURCE_REMOVE  # the first: a share takes two
        names = {pid: name for (pid, _), name in self.names.items()}
        apps = {}
        for pid, engines in gpu_use(old, reading).items():
            name = names.get(pid)  # None: new since the sample, named next
            if name is not None:
                totals = apps.setdefault(name, {})
                for engine, part in engines.items():
                    totals[engine] = totals.get(engine, 0.0) + part
        busy = sorted(((min(100.0, max(e.values(), default=0.0)), name)
                       for name, e in apps.items()), reverse=True)
        shown = [f"{name} {percent(part)}" for part, name in busy[:GPU_APPS]
                 if part >= GPU_QUIET]
        # The compositor's share can't be read: "your apps", not "no app".
        self.gpu_apps.set_text(" · ".join(shown) if shown
                               else "None of your apps is busy on it")
        return GLib.SOURCE_REMOVE

    def _apps(self, prev, cur):
        """The apps' processes' CPU time since the last sample, as shares of
        the whole machine like the processor's figure. Each share is the
        mean of this sample's and the share before, as btop's "cpu lazy"
        sorting has it, so the rows reshuffle less: a spike rises and fades
        over a few samples. An app's first share counts whole, as does every
        share of the first full sample: the opening FIRST s are too short to
        keep."""
        start_ticks = prev.up * TICK
        apps, names = {}, {}
        for pid, (start, ticks, rss, comm, kthread, _) in cur.procs.items():
            old = prev.procs.get(pid)
            if old is not None and old[0] == start:
                ticks -= old[1]
            elif start < start_ticks:
                ticks = 0  # not new, just missed: its time isn't all recent
            key = (pid, start)
            name = names[key] = (self.names.get(key)
                                 or app_name(pid, comm, kthread))
            app = apps.setdefault(name, [0, 0, []])
            app[0] += ticks
            app[1] += rss
            if not kthread:  # no memory of their own to count
                app[2].append(pid)
        self.names = names
        span = cur.cpus["cpu"][1] - prev.cpus["cpu"][1]
        keep = self.count > 2  # self.count: samples so far, this one too
        shares = {}
        for name in self.shares.keys() | apps.keys():
            now = (100 * apps[name][0] / span
                   if name in apps and span > 0 else 0.0)
            old = self.shares.get(name) if keep else None
            share = now if old is None else (old + now) / 2
            if name in apps or share >= 0.05:
                shares[name] = share
        self.shares = shares
        self.listed = apps
        self._list()

    def _list(self):
        """The TOP apps: by CPU, ties to the bigger app, or by the memory
        they hold, which for that is read for every app (about 4 ms). While
        a row is armed or has a note, the rows keep their apps, so a second
        click lands on the app the first was for; an app being ended keeps
        a row, the last if it would drop out, until it has gone."""
        apps, shares = self.listed, self.shares
        if self.arm.key is not None or self.notes:
            top = [n for n in self.row_apps if n is not None]
            mem = {n: held(apps[n][2]) if n in apps else 0 for n in top}
        else:
            if self.sort == "memory":
                mem = {name: held(app[2]) for name, app in apps.items()}
                top = sorted(apps,
                             key=lambda n: (-mem[n], -shares.get(n, 0.0)))
            else:
                top = sorted(shares, key=lambda n: (-shares[n],
                                                    -apps.get(n, (0, 0))[1]))
                mem = {n: held(apps[n][2]) if n in apps else 0
                       for n in top[:TOP]}
            pinned = [n for n in self.ending if n not in top[:TOP]]
            top = top[:TOP - len(pinned)] + pinned
            for n in pinned:
                mem[n] = held(apps[n][2]) if n in apps else 0
        self.row_apps = (top + [None] * TOP)[:TOP]
        self.row_figs = [(shares.get(n, 0.0), mem.get(n, 0)) if n else (0.0, 0)
                         for n in self.row_apps]
        self._paint_rows()

    def _paint_rows(self):
        for (row, title, share, mem, note), app, (cpu, held_) in zip(
                self.rows, self.row_apps, self.row_figs):
            text, tone, tip = self._row_note(app)
            title.set_text(app or "")
            note.set_text(text)
            set_class(note, "warn", tone == "warn")
            note.set_visible(bool(text))
            share.set_visible(not text)
            mem.set_visible(not text)
            share.set_text(percent(cpu) if app else "")
            mem.set_text(size(held_) if app and held_ else "")
            key = self.arm.key
            set_class(row, "armed", key is not None and key[1] == app)
            row.set_tooltip_text(tip)
            row.set_sensitive(app is not None)

    def _row_note(self, app):
        """What a row says in place of its figures, if anything, how
        (None, or "warn"), and its tooltip."""
        if app is None:
            return "", None, None
        key, n = self.arm.key, self.arm_count
        them = "it" if n == 1 else f"all {n}"
        if key == ("end", app):
            return (f"Click again to end {them}", None,
                    f"Asks {app} to quit (SIGTERM), as closing it would: "
                    f"{n} process{'es' if n > 1 else ''}")
        if key == ("force", app):
            return (f"Click again to force {them}", None,
                    f"Stops {app} at once (SIGKILL): what it hasn't saved is "
                    f"lost. {n} process{'es' if n > 1 else ''}")
        state = self.ending.get(app)
        if state is not None and state["stage"] == "ending":
            return "Ending…", None, f"Asked {app} to quit"
        if state is not None:
            left = len(state["fds"])
            return ("Didn't quit · force?", "warn",
                    f"{left} of {app}'s processes didn't quit within "
                    f"{END_WAIT // 1000} s of being asked. Click, then click "
                    "again, to stop them at once (SIGKILL)")
        if app in self.notes:
            text, tone, _timer = self.notes[app]
            return text, tone, None
        return "", None, f"Click twice to end {app}"

    # --- Ending an app -------------------------------------------------------

    def _on_app(self, _row, i):
        """A click on row i (or Enter, Space): the first arms it, a second
        within ARM_MS ends the app; once an app hasn't quit when asked, the
        same two clicks force it to. A second click sooner than a
        double-click's is part of the first."""
        app = self.row_apps[i]
        state = self.ending.get(app)
        if app is None or (state is not None and state["stage"] == "ending"):
            return  # nothing there, or on its way out
        key = ("force" if state is not None else "end", app)
        if key == self.arm.key:
            double = Gtk.Settings.get_default().props.gtk_double_click_time
            if time.monotonic() - self.armed_at < double / 1000:
                return
        else:
            why, self.arm_count = self._reach(key)
            if why is not None:
                self.arm.disarm()
                self._note(app, why)
                return
            self.armed_at = time.monotonic()
        if not self.arm.confirm(key):
            return  # armed: the row says what the next click does
        self.arm.disarm()
        (self._end if key[0] == "end" else self._force)(app)

    def _on_arm(self, _old, _new):
        self._paint_rows()

    def _reach(self, key):
        """(why not, None if it may) and how many processes a click on
        `key` would end. Never pid 1, the compositor, this card's host, or
        what either runs under (start-hyprland, the user's systemd): ending
        those ends the session. With no compositor to be found, nothing:
        btop is a click away. Only the user's own processes."""
        what, app = key
        if what == "force":
            return None, len(self.ending[app]["fds"])
        pids = [pid for pid, _, _ in self._pids(app)]
        if not pids:
            return "Gone already", 0
        if self.keep is None:
            shell = compositor()
            self.keep = ({1} | set(lineage(os.getpid()))
                         | set(lineage(shell))) if shell else set()
        if not self.keep:
            return "Can't check it: use btop", 0
        if os.getpid() in pids:
            return "This card runs in it", 0
        if self.keep.intersection(pids):
            return "The session needs it", 0
        mine = sum(map(ours, pids))
        return (None, mine) if mine else ("Not yours to end", 0)

    def _pids(self, app):
        """[(pid, start, parent)] of the app's processes, last sample."""
        procs = self.prev.procs
        return [(pid, start, procs[pid][5])
                for (pid, start), name in self.names.items()
                if name == app and pid in procs]

    def _end(self, app):
        """SIGTERM to the app's processes at the top of its tree, those
        whose parent isn't one of them: they close the rest, as when the
        app is quit. A pidfd for each of its processes tells when they have
        all gone; if some are still there END_WAIT later, the row offers to
        force them."""
        procs = {pid: (start, parent) for pid, start, parent in self._pids(app)
                 if ours(pid)}
        fds = {}
        for pid, (start, _) in procs.items():
            fd = open_process(pid, start)
            if fd is not None:
                fds[pid] = fd
        asked = 0
        for pid, fd in fds.items():
            if procs[pid][1] not in procs:
                try:
                    signal.pidfd_send_signal(fd, signal.SIGTERM)
                    asked += 1
                except OSError:
                    pass  # gone just now
        if not asked:
            for fd in fds.values():
                os.close(fd)
            self._note(app, "Gone already")
            return
        self.ending[app] = {"stage": "ending", "fds": fds,
                            "timer": GLib.timeout_add(END_WAIT, self._stuck,
                                                      app)}
        self._paint_rows()

    def _stuck(self, app):
        """END_WAIT after SIGTERM: what is still there gets the offer."""
        state = self.ending.get(app)
        if state is not None:
            state["timer"] = None
            self._reap(app)
        if app in self.ending:
            self.ending[app]["stage"] = "stuck"
            self._paint_rows()
        return GLib.SOURCE_REMOVE

    def _reap(self, app):
        """Let go of the pidfds of the app's processes that have ended; once
        all have, the row says so for a moment."""
        fds = self.ending[app]["fds"]
        for pid, fd in list(fds.items()):
            if exited(fd):
                os.close(fd)
                del fds[pid]
        if not fds:
            self._drop(app)
            self._note(app, "Ended")

    def _force(self, app):
        """SIGKILL to what is left of the app."""
        fds = self.ending[app]["fds"]
        for fd in fds.values():
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except OSError:
                pass  # gone just now
        self._drop(app)
        self._note(app, "Forced to quit")

    def _drop(self, app):
        state = self.ending.pop(app)
        if state["timer"] is not None:
            GLib.source_remove(state["timer"])
        for fd in state["fds"].values():
            os.close(fd)

    def _note(self, app, text, tone=None):
        """`text` in place of the app's figures for NOTE_MS; the rows keep
        their apps meanwhile."""
        if app in self.notes:
            GLib.source_remove(self.notes[app][2])
        self.notes[app] = (text, tone,
                           GLib.timeout_add(NOTE_MS, self._unnote, app))
        self._paint_rows()

    def _unnote(self, app, relist=True):
        _text, _tone, timer = self.notes.pop(app)
        if not relist:
            GLib.source_remove(timer)
        elif self.listed is not None:
            self._list()  # the rows may take their order again
        return GLib.SOURCE_REMOVE

    def _draw_cores(self, area, cr):
        """A bar per core filled from the bottom by its load, in a group per
        kind of core with its name under it; the gaps narrow on machines
        with many cores. Empty bars until the first sample."""
        loads = self.cores or [0.0] * len(self.cpus)
        if not loads:
            return False
        w = area.get_allocated_width()
        n = len(loads)
        groups = self.groups
        gap = max(1, min(3, w // (4 * n)))
        split = 3 * gap  # between two groups, on top of a gap
        step = (w - split * (len(groups) - 1) + gap) / n
        x = 0.0
        for names, members in groups:
            left = x
            for i in members:
                bar(cr, round(x), round(x + step) - gap, loads[i])
                x += step
            if names:
                self._name(area, cr, names, round(left), round(x) - gap)
            x += split
        return False

    @staticmethod
    def _name(area, cr, names, left, right):
        """The longest of `names` that fits between left and right, centred
        under the bars as small as a status line."""
        layout = area.create_pango_layout("")
        font = Pango.FontDescription.from_string(
            "FantasqueSansM Nerd Font Propo")
        font.set_absolute_size(12 * Pango.SCALE)
        layout.set_font_description(font)
        for text in names:
            layout.set_text(text, -1)
            width = layout.get_pixel_size()[0]
            if width <= right - left:
                break
        cr.move_to(left + (right - left - width) / 2, BARS_H + 1)
        cr.set_source_rgb(*SUBTEXT)
        PangoCairo.show_layout(cr, layout)

    # --- NVIDIA --------------------------------------------------------------

    def _start_nvidia(self):
        """nvidia-smi, sampling on its own every POLL s while the card is
        open: one process instead of one per sample, whose lines are read
        as they come. None where there is no nvidia-smi."""
        if shutil.which("nvidia-smi") is None:
            return None
        try:
            proc = Gio.Subprocess.new(
                NVIDIA_SMI, Gio.SubprocessFlags.STDOUT_PIPE
                | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error:
            return None
        self.gpu_cancel = Gio.Cancellable()
        stream = Gio.DataInputStream.new(proc.get_stdout_pipe())
        stream.read_line_async(GLib.PRIORITY_DEFAULT, self.gpu_cancel,
                               self._nvidia_read)
        return proc

    def _nvidia_read(self, stream, result):
        try:
            line, _ = stream.read_line_finish_utf8(result)
        except GLib.Error:
            return  # cancelled: the card has closed
        if self.dead:
            return
        if line is None:
            self._nvidia_ended()
            return
        self._nvidia_line(line)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, self.gpu_cancel,
                               self._nvidia_read)

    def _nvidia_line(self, line, cached=False):
        """One card's line: index, name, load %, °C, VRAM used and total in
        MiB, power drawn in W. Anything else is nvidia-smi saying what went
        wrong, unless it is `cached`: what an older card put in the cache,
        in its own format, is passed over."""
        fields = [f.strip() for f in line.split(",")]
        if len(fields) != 7 or not fields[0].isdigit():
            if not cached:
                self.gpu_error = self.gpu_error or line.strip()
            return
        index, name = fields[:2]
        util, temp, used, total = map(number, fields[2:6])
        watts = decimal(fields[6])
        self.gpu_lines[index] = line
        row = self.gpus.get(index)
        if row is None:
            row = self._gpu_row(name.removeprefix("NVIDIA "))
            self.gpus[index] = row
        bits = []
        if temp is not None:
            bits.append(heat(temp, GPU_HEAT))
        if used is not None and total:
            bits.append(f"VRAM {of(used * 2**20, total * 2**20)}")
        if watts is not None:
            bits.append(f"{watts:.0f} W")
        row.update(f"{util}%" if util is not None else "", " · ".join(bits),
                   (util or 0) / 100)

    def _nvidia_ended(self):
        """nvidia-smi quit by itself, e.g. on a driver that no longer
        matches its library after an update: say so on the cards' rows."""
        why = GLib.markup_escape_text(self.gpu_error or "nvidia-smi stopped")
        if not self.gpus:
            self.gpus["-"] = self._gpu_row("NVIDIA graphics")
        for row in self.gpus.values():
            row.update("", why, bad=True)
        self.gpu_lines.clear()  # nothing to open on next time

    def _closed(self, _panel):
        if self._poll_id is not None:
            GLib.source_remove(self._poll_id)
        for app in list(self.ending):
            self._drop(app)  # an app on its way out goes on by itself
        for app in list(self.notes):
            self._unnote(app, relist=False)
        if self.nvidia is None:
            return
        self.gpu_cancel.cancel()
        self.nvidia.send_signal(signal.SIGTERM)
        lines = [self.gpu_lines[i] for i in sorted(self.gpu_lines, key=int)]
        tmp = f"{NVIDIA_CACHE}.{os.getpid()}"
        try:
            with open(tmp, "w") as fh:
                json.dump(lines, fh)
            os.replace(tmp, NVIDIA_CACHE)  # readers never see half a file
        except OSError:
            pass
