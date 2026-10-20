#!/usr/bin/env python3
"""The laptop keys, with on-screen feedback.

Hyprland binds the volume, mute, mic-mute and brightness keys to
`osd volume up|down|mute`, `osd mic mute` and `osd brightness up|down`, and
the bar's scrolls and clicks run the same commands. Each command makes the
change, then shows the new level as a transient swaync notification with a
progress bar, and below it the device it acted on. Every popup carries the
same synchronous tag, so holding a key updates one popup in place instead
of stacking them, and transient popups stay out of the notification
history.

A held key repeats every 40 ms and a swipe over the bar steps as fast, each
step a run of osd of its own, while a run takes ~100 ms (a monitor over
DDC/CI longer): the steps are queued, and one run at a time applies all
that are waiting (queued()).

Brightness goes to the screen under the pointer: the built-in panel's
backlight, or a monitor's over DDC/CI. With the lid shut the panel is dark
and Hyprland has it disabled, so the keys never reach it then.

Clicks on the bar that switch something with no other sign of it than a
glyph show a popup too: `osd mic on|off` (a click that means one thing,
whatever the state), `osd bluetooth` and `osd dnd` (Do Not Disturb).

`osd mic led`, which Hyprland runs as the session starts, lights the mic-mute
key's LED if WirePlumber has brought the microphone back muted.
`osd mic sync` does the same without waiting, after something that mutes
the input or picks another one without the LED: pavucontrol as it closes.

bar-popup imports this module for hyprland() and the Display card's
backlight row; nightlight for the backlight, LEVELS, is_builtin() and
hyprland(); agents-busy for hyprland().
"""

import fcntl
import html
import json
import os
import re
import socket
import subprocess
import sys
import time

STEP = 5         # % per volume step; levels snap to multiples of it
# Brightness levels, % of a screen's range, about 5 apart in perceived
# lightness (CIE L*). A backlight's light follows its level, the eye's
# sense of it does not: 5 % steps went from the 1 % floor straight to 5 %,
# five times the light, and spent their last steps on changes hard to see.
LEVELS = (1, 2, 3, 4, 6, 8, 11, 14, 18, 22, 27, 33, 39, 47, 55, 64, 74, 85,
          100)
POPUP_MS = 1200  # how long the level popup stays up

SINK, SOURCE = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"
MIC_LED = "platform::micmute"  # the mic-mute key's LED, lit by mic_led()
MIC_WAIT = 30  # s `osd mic led` waits for WirePlumber as the session starts
BACKLIGHT = "/sys/class/backlight"
DRM = "/sys/class/drm"
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")  # queues; Hyprland's

# md-* glyphs, the ones the bar's modules use
VOLUME_ICONS = ("\U000f057f", "\U000f0580", "\U000f057e")  # low, medium, high
MUTED = "\U000f0581"
MIC, MIC_OFF = "\U000f036c", "\U000f036d"
BRIGHTNESS = "\U000f00df"
BLUETOOTH, BLUETOOTH_OFF = "\U000f00af", "\U000f00b2"
DND_ON, DND_OFF = "\U000f0a93", "\U000f009c"  # the bell asleep, the bell


def run(*args):
    """stdout of a command, or None if it fails."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def popup(text, value=None, body=None):
    """Show `text`, a 0-100 bar if `value` is given and `body` (what it
    acted on) on the line below, in place of the last popup. It bypasses
    Do Not Disturb: it answers a key press, it doesn't interrupt."""
    args = ["notify-send", "-a", "osd", "-e", "-t", str(POPUP_MS),
            "-h", "string:x-canonical-private-synchronous:osd",
            "-h", "boolean:SWAYNC_BYPASS_DND:true"]
    if value is not None:
        args += ["-h", f"int:value:{value}"]
    # swaync reads the body as markup, and a device may be "B&O".
    run(*args, text, *([html.escape(body, quote=False)] if body else []))


def queued(kind, action, apply):
    """Apply this step and any others waiting: `apply(actions)` reads the
    level once, takes the steps in order, and writes and shows the result
    once. Runs that overlapped read the same level and lost steps between
    them, and runs that waited for each other kept stepping for a second
    after the key came up. So each run adds its step to the queue, and the
    run holding the worker lock applies the queue until it is empty. A run
    that finds the lock held leaves its step to that one, which looks at
    the queue again after letting go, so no step is left behind."""
    queue = os.path.join(RUNTIME, f"osd-{kind}.queue")
    with open(queue, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)  # let go as the file closes
        fh.write(action + "\n")
    status = 0
    while True:
        with open(os.path.join(RUNTIME, f"osd-{kind}.lock"), "a") as worker:
            try:
                fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return status  # the run holding it takes this step too
            while actions := take(queue):
                status = apply(actions)
        # A step queued since the last take() found the lock still held.
        if not os.path.getsize(queue):
            return status


def take(queue):
    """The steps waiting in `queue`, which is left empty."""
    with open(queue, "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        actions = fh.read().split()
        fh.truncate(0)
    return actions


def stepped(pct, up):
    """The next multiple of STEP above or below `pct`, within 0-100. A
    level above 100 (pavucontrol's boost) goes down by steps; up leaves it
    be rather than cut it to 100."""
    target = (pct // STEP + 1) * STEP if up else (pct - 1) // STEP * STEP
    return max(0, min(max(100, pct) if up else target, target))


def logind_brightness(subsystem, name, value):
    """Set a backlight or LED through logind, which lets the active
    session's user do it without a udev rule or the video group."""
    return run("busctl", "call", "org.freedesktop.login1",
               "/org/freedesktop/login1/session/auto",
               "org.freedesktop.login1.Session", "SetBrightness", "ssu",
               subsystem, name, str(value)) is not None


def hyprland(request):
    """Hyprland's answer to `request` as `hyprctl -j` gives it (e.g.
    "j/monitors"), over its socket: hyprctl takes 30 ms to start, the
    socket 0.2 ms. None outside Hyprland."""
    path = os.path.join(RUNTIME, "hypr",
                        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
                        ".socket.sock")
    chunks = []
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect(path)
            sock.sendall(request.encode())
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        return json.loads(b"".join(chunks))
    except (OSError, ValueError):
        return None


# --- Audio -----------------------------------------------------------------

def level(node):
    """(percent, muted) of a PipeWire node, or None; wpctl prints
    'Volume: 0.45' or 'Volume: 0.45 [MUTED]'."""
    out = run("wpctl", "get-volume", node)
    m = re.search(r"Volume: ([\d.]+)", out or "")
    if m is None:
        return None
    return round(float(m.group(1)) * 100), "[MUTED]" in out


def device(node):
    """The short name of what `node` stands for now ("Speaker", "KM-Bose",
    "LG TV SSCR2"), as the Sound card names it: the keys act on whatever
    is the default, and the popup says which."""
    props = dict(re.findall(r'^\s*\*?\s*([\w.]+) = "(.*)"$',
                            run("wpctl", "inspect", node) or "", re.M))
    return props.get("node.nick") or props.get("node.description")


def volume(actions):
    """Apply steps ("up", "down", "mute", in order) to the default output:
    one read, at most one write of each, one popup."""
    now = level(SINK)
    if now is None:
        return 1
    pct, muted = now
    for action in actions:
        if action == "mute":
            muted = not muted
        else:
            pct = stepped(pct, action == "up")
            muted = False  # a step unmutes, as the Sound card's slider does
    if pct != now[0]:
        run("wpctl", "set-volume", SINK, f"{pct / 100:.2f}")
    if muted != now[1]:
        run("wpctl", "set-mute", SINK, str(int(muted)))
    name = device(SINK)
    if muted:
        popup(f"{MUTED}  Muted", 0, name)
    else:
        popup(f"{VOLUME_ICONS[min(2, pct * 3 // 101)]}  Volume {pct}%", pct,
              name)
    return 0


def mic_led(muted):
    """Light the mic-mute key's LED while the default input is muted. Its
    kernel trigger follows no ALSA control here, so whatever mutes the
    input or makes another one the default sets it; where there is no such
    LED (the desktop PC) logind refuses and nothing changes."""
    logind_brightness("leds", MIC_LED, int(muted))


def mute_mic(muted):
    """Mute or unmute the default input and mirror it on the LED."""
    run("wpctl", "set-mute", SOURCE, str(int(muted)))
    mic_led(muted)


def mic(actions):
    """Toggle the default input's mute ("mute", the key), or set it ("on",
    "off": a click that means one thing whatever the state, so one on the
    bar's privacy pill can never unmute), and say so."""
    now = level(SOURCE)
    if now is None:
        return 1
    muted = now[1]
    for action in actions:
        muted = not muted if action == "mute" else action == "off"
    mute_mic(muted)  # also when unchanged: it puts the LED right
    popup(f"{MIC_OFF}  Microphone off" if muted else f"{MIC}  Microphone on",
          body=device(SOURCE))
    return 0


def mic_sync(wait=0):
    """Light the LED as the default input's mute stands. pavucontrol mutes
    the input, or makes another one the default, without it, so
    `osd mic sync` runs as it closes. WirePlumber restores a microphone
    muted at logout, while the LED comes up dark: `osd mic led`, which
    Hyprland runs as the session starts, when WirePlumber may not be up
    yet, waits up to `wait` s for the default input."""
    deadline = time.monotonic() + wait
    while (now := level(SOURCE)) is None:
        if time.monotonic() >= deadline:
            return 1
        time.sleep(1)
    mic_led(now[1])
    return 0


# --- Brightness: the built-in panel's backlight, monitors over DDC/CI -------

def is_builtin(connector):
    """Whether a connector, named as DRM's card1-eDP-1 or as Hyprland's
    eDP-1, is a built-in panel's: eDP, LVDS or DSI."""
    return re.match(r"(card\d+-)?(eDP|LVDS|DSI)-", connector) is not None


def panels():
    """The DRM connectors of a built-in panel (eDP, LVDS or DSI) with the
    panel on them, e.g. ["card1-eDP-1"]. The laptop's stays connected with
    its lid shut; a desktop has none, though its firmware may still offer a
    backlight interface (an acpi_video0 that drives nothing)."""
    found = []
    try:
        names = os.listdir(DRM)
    except OSError:
        return found
    for name in names:
        if not (name.startswith("card") and is_builtin(name)):
            continue
        try:
            with open(f"{DRM}/{name}/status") as fh:
                if fh.read().strip() == "connected":
                    found.append(name)
        except OSError:
            continue
    return found


def backlight():
    """(name, level, max) of the built-in panel's backlight, or None, also
    where there is no built-in panel to drive (see panels()). With several
    interfaces the firmware one wins over platform and raw, as in
    systemd-backlight."""
    if not panels():
        return None
    rank = {"firmware": 0, "platform": 1, "raw": 2}
    best = None
    try:
        names = os.listdir(BACKLIGHT)
    except OSError:
        return None
    for name in names:
        def attr(key, name=name):
            with open(f"{BACKLIGHT}/{name}/{key}") as fh:
                return fh.read().strip()
        try:
            entry = (rank.get(attr("type"), 3), name,
                     int(attr("brightness")), int(attr("max_brightness")))
        except (OSError, ValueError):
            continue
        if entry[3] > 0 and (best is None or entry < best):
            best = entry
    return best and best[1:]


def backlight_floor(top):
    """The lowest level to offer: raw interfaces switch the backlight off
    at 0, so keep 1% at least."""
    return max(1, top // 100)


def panel_lit():
    """Whether the built-in panel is lit: it is dark while the laptop's lid
    is shut or the screens are blanked, as DRM calls a connector enabled
    only while it drives a screen."""
    for name in panels():
        try:
            with open(f"{DRM}/{name}/enabled") as fh:
                if fh.read().strip() == "enabled":
                    return True
        except OSError:
            continue
    return False


def brightness_step(raw, top, up, floor=0):
    """`raw`, a level of 0-`top`, at the next of LEVELS up or down that
    shows as another percentage, within `floor`-`top` (0 is the one below
    the lowest). A coarse range (a firmware backlight's 0-15) puts several
    LEVELS on one level, and one set elsewhere may lie between two."""
    shown = round(100 * raw / top)
    for pct in LEVELS if up else LEVELS[::-1] + (0,):
        new = max(floor, round(top * pct / 100))
        if (round(100 * new / top) - shown) * (1 if up else -1) > 0:
            return new
    return raw


def under_pointer(screens):
    """Which of `screens` (Hyprland's monitors, lit ones only) the pointer
    is on; the focused one if Hyprland can't say."""
    pos = hyprland("j/cursorpos") or {}
    for screen in screens if "x" in pos and "y" in pos else ():
        w = screen["width"] / screen["scale"]
        h = screen["height"] / screen["scale"]
        if screen["transform"] % 2:  # turned a quarter
            w, h = h, w
        if (screen["x"] <= pos["x"] < screen["x"] + w
                and screen["y"] <= pos["y"] < screen["y"] + h):
            return screen
    return next((s for s in screens if s.get("focused")), screens[0])


def panel_steps(actions):
    """Take the built-in panel's backlight through `actions`, by LEVELS
    and not under its floor; its new %, or None if it can't be set."""
    panel = backlight()
    if panel is None:
        return None
    name, raw, top = panel
    new = raw
    for action in actions:
        new = brightness_step(new, top, action == "up", backlight_floor(top))
    if new != raw and not logind_brightness("backlight", name, new):
        return None
    return round(100 * new / top)


def monitor_steps(connector, actions):
    """Take a monitor's brightness through `actions` over DDC/CI, by
    LEVELS; its new %, or None if it doesn't answer. nightlight keeps each
    monitor's last level, for the bar and for the next press: a read takes
    as long as a write, and a monitor may still answer with the level
    before the last write."""
    import nightlight  # it imports this module, so not at the top
    bus = nightlight.ddc_buses().get(connector)
    if bus is None:
        return None
    # ddc_read() and ddc_write() keep what they read and set for the bar.
    now = (nightlight.ddc_level(connector)
           or nightlight.ddc_read(connector, bus))
    if not now or not now[1]:
        return None
    level, top = now
    new = level
    for action in actions:
        new = brightness_step(new, top, action == "up")
    if new != level and not nightlight.ddc_write(connector, bus, new):
        return None
    return round(100 * new / top)


def brightness(actions):
    """Apply key presses or scroll notches ("up", "down", in order) to the
    screen under the pointer: one read, one write, one popup, which names
    the screen when there are several. Outside Hyprland only the built-in
    panel, while it is lit."""
    screens = hyprland("j/monitors")
    if screens:
        screen = under_pointer(screens)
        name = screen["name"]
    elif panel_lit():
        screens, name = [], None
    else:
        return 1
    if name is None or is_builtin(name):
        shown, label = panel_steps(actions), "Built-in"
    else:
        shown = monitor_steps(name, actions)
        label = screen.get("model") or name  # as the Display card's rows
    if shown is None:
        return 1
    popup(f"{BRIGHTNESS}  Brightness {shown}%", shown,
          label if len(screens) > 1 else None)
    # The display module gives the level too: have it re-read.
    import nightlight  # it imports this module, so not at the top
    nightlight.refresh_waybar()
    return 0


# --- Switches the bar's clicks flip ------------------------------------------

def bluetooth():
    """Turn Bluetooth on or off (the bar module's right click) and say
    which: a stray click drops the headphones, and the glyph alone is easy
    to miss. bluetoothctl talks to the default adapter, so this does
    nothing where there is none (the PC without Bluetooth)."""
    shown = run("bluetoothctl", "show")
    if not shown or "Powered:" not in shown:
        return 1
    on = "Powered: yes" not in shown
    if run("bluetoothctl", "power", "on" if on else "off") is None:
        if "PowerState: off-blocked" in shown:
            text = "Bluetooth is blocked (rfkill)"
        else:
            text = f"Bluetooth didn't turn {'on' if on else 'off'}"
        popup(f"{BLUETOOTH_OFF if on else BLUETOOTH}  {text}")
        return 1
    popup(f"{BLUETOOTH}  Bluetooth on" if on
          else f"{BLUETOOTH_OFF}  Bluetooth off")
    return 0


def dnd():
    """Flip swaync's Do Not Disturb (the bell's right click) and say which
    way it went: the sleeping bell alone is easy to miss after a stray
    click, and every popup would stay away until it is noticed. This one
    bypasses it, as all of osd's do. -sw: fail at once while swaync is
    down instead of waiting for it."""
    state = (run("swaync-client", "-sw", "-d") or "").strip()
    if state not in ("true", "false"):
        return 1
    popup(f"{DND_ON}  Do Not Disturb on" if state == "true"
          else f"{DND_OFF}  Do Not Disturb off")
    return 0


def main(argv):
    cmd = argv[1:]
    if cmd in (["volume", "up"], ["volume", "down"], ["volume", "mute"]):
        return queued("volume", cmd[1], volume)
    if cmd in (["mic", "mute"], ["mic", "on"], ["mic", "off"]):
        return queued("mic", cmd[1], mic)
    if cmd == ["mic", "led"]:
        return mic_sync(MIC_WAIT)
    if cmd == ["mic", "sync"]:
        return mic_sync()
    if cmd in (["brightness", "up"], ["brightness", "down"]):
        return queued("brightness", cmd[1], brightness)
    if cmd == ["bluetooth"]:
        return bluetooth()
    if cmd == ["dnd"]:
        return dnd()
    print("usage: osd volume up|down|mute | osd mic mute|on|off|led|sync | "
          "osd brightness up|down | osd bluetooth | osd dnd", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
