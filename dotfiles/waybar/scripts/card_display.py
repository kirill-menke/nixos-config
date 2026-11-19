"""The Display card, `bar-popup display`: evening mode (run by
nightlight.py), keep awake (an idle inhibitor that ends by itself), the
built-in panel's backlight and the keyboard's light, each external
monitor's brightness over DDC/CI, and, docked or at a projector with the
lid open, how the panel and the other screens share the picture.
"""

import os
import time
from functools import partial

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
from cardkit import (Latest, Panel, Segmented, button, label,  # noqa: E402
                     read_text, run_async, slider, slider_row, switch_row)
import nightlight  # noqa: E402
import osd  # noqa: E402

TIME_STEP = 30  # min per click on a schedule stepper
BAR_MS = 250    # ms between a drag's bar refreshes; each runs a process
LEDS = "/sys/class/leds"
KBD_NAMES = ("Off", "Low", "High")  # a keyboard light of two steps

# Nerd Font glyphs, the same ones the bar uses.
ICON = {
    "monitor": "\U000f0379",
    "laptop": "\U000f0322",
    "screen-off": "\U000f0d90",
    "night": "\U000f0594",
    "warmth": "\U000f18d6",
    "schedule": "\U000f1a77",
    "coffee": "\U000f0176",
    "minus": "\U000f0374",
    "plus": "\U000f0415",
    "dim": "\U000f0cf2",
    "keyboard": "\U000f030c",
    "extend": "\U000f037a",  # md-monitor_multiple
    "mirror": "\U000f1483",  # md-monitor_share
}

# The Screens chooser: how the built-in panel and the other screens share
# the picture, as hyprland.lua's screen_layout() sets it up: (its key there,
# glyph, name, what it does for the tooltip).
LAYOUTS = (
    ("extend", ICON["extend"], "Extend",
     "Each screen a desktop of its own, side by side"),
    ("mirror", ICON["mirror"], "Mirror",
     "The other screens show the built-in one: for a projector"),
    ("external", ICON["monitor"], "External",
     "Only the other screens; the built-in one goes dark"),
    ("builtin", ICON["laptop"], "Built-in",
     "Only the built-in screen; the others go dark"),
)
SEG_H = 54  # the chooser: a glyph over a name, as the Battery card's modes
LID = "/proc/acpi/button/lid"  # where ACPI says whether the lid is shut

CSS = b"""
/* Scoped to this card, as every card's own rules are: all cards share one
   stylesheet. The schedule's time steppers sit under its name. */
.card.display .steppers { margin: -4px 10px 2px 46px; }
.card.display .iconbtn.mini { font-size: 15px; padding: 0 4px; }
.card.display .time { font-weight: bold; }
/* Off with the schedule, all of them in @dim, as dim as the cards' text
   gets. */
.card.display .steppers:disabled label { color: @dim; }
/* A level on its way, an ellipsis after it: a monitor takes up to 10 s to
   answer over DDC/CI, or never does; Failed in red once it didn't take. */
.card.display .level.pending { color: @subtext0; }
.card.display .level.bad { color: @crit; }
/* The Screens chooser: the name under the glyph in 12 px, as the Battery
   card's power modes, and what went wrong under it. */
.card.display .seg { font-size: 12px; }
.card.display .note { padding: 2px 10px 0; }
"""


def lid_open():
    """Whether the laptop's lid is open, as ACPI has it (hyprland.lua reads
    LID0 too); True without a lid, as on the desktop."""
    try:
        names = os.listdir(LID)
    except OSError:
        return True
    return not any("closed" in read_text(f"{LID}/{name}/state")
                   for name in names)


def screens():
    """(the built-in panel's monitor, the other screens' monitors), as
    `hyprctl -j monitors all` lists them, lit or not (Built-in or External
    leaves some dark), or None where there is nothing to choose: no panel
    (the desktop), no other screen plugged in, or the lid shut (the lid
    switches the panel itself, hyprland.lua). Only screens plugged in to a
    connector count (nightlight.connectors()), not a headless output."""
    mons = osd.hyprland("j/monitors all")
    if not mons or not lid_open():
        return None
    plugged = set(nightlight.connectors())
    mons = [m for m in mons if m.get("name") in plugged]
    panel = [m for m in mons if osd.is_builtin(m["name"])]
    others = [m for m in mons if not osd.is_builtin(m["name"])]
    if len(panel) != 1 or not others:
        return None
    return panel[0], others


def layout_of(panel, others):
    """The key of the layout in LAYOUTS that Hyprland shows now."""
    if any(m.get("mirrorOf", "none") != "none" for m in others):
        return "mirror"
    if panel.get("disabled"):
        return "external"
    if all(m.get("disabled") for m in others):
        return "builtin"
    return "extend"


def keyboard_light():
    """(name, level, max) of the keyboard's backlight, an LED the kernel
    names <vendor>::kbd_backlight (dell::kbd_backlight here), or None.
    Dell's driver asks the firmware for the level on each read, so it
    follows the light key, which the firmware handles by itself."""
    try:
        names = sorted(os.listdir(LEDS))
    except OSError:
        return None
    for name in names:
        if not name.endswith("::kbd_backlight"):
            continue
        try:
            with open(f"{LEDS}/{name}/brightness") as fh:
                level = int(fh.read())
            with open(f"{LEDS}/{name}/max_brightness") as fh:
                top = int(fh.read())
        except (OSError, ValueError):
            continue
        if top > 0:
            return name, level, top
    return None


def percent(level, top):
    return f"{round(100 * level / top)}%"


def kbd_text(level, top):
    if top == len(KBD_NAMES) - 1:
        return KBD_NAMES[level]
    return percent(level, top)


def dim_step(pct):
    """The Dim to slider's step for `pct`: the nearest of osd.LEVELS (one
    set before they were its steps can lie between two)."""
    return min(range(len(osd.LEVELS)),
               key=lambda i: abs(osd.LEVELS[i] - pct))


def logind_write(subsystem, name):
    """A LevelRow's write for a backlight or an LED that logind sets: it
    writes the sysfs file for the active session's user, so no udev rule
    or video group is needed."""
    def write(level, done):
        def finish(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error:
                done(False)
                return
            done(True)
        Gio.bus_get_sync(Gio.BusType.SYSTEM).call(
            "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
            "org.freedesktop.login1.Session", "SetBrightness",
            GLib.Variant("(ssu)", (subsystem, name, level)), None,
            Gio.DBusCallFlags.NONE, -1, None, finish)
    return write


def ddc_write(bus):
    """A LevelRow's write for the monitor on i2c `bus`: its brightness
    (VCP 10) over DDC/CI, ~150 ms a write, and seconds for one that
    doesn't answer or that waits for evening mode's own on the same bus
    (ddcutil holds a lock across its instances)."""
    def write(level, done):
        run_async(lambda ok, _out, _err: done(ok), "ddcutil", "--bus",
                  str(bus), "--skip-ddc-checks", "--noverify", "setvcp",
                  "10", str(level))
    return write


class LevelRow:
    """A slider row over a level that write(level, done) sets (a screen's
    brightness, the keyboard's light): one write is in flight and a drag
    ends on the last value, also once the card has closed (Latest). The
    value says how it stands: "40%…" while the level is on its way, and
    Failed, in red, once the last one didn't take, until the level really
    changes or another takes; wrote(level) follows each that takes.
    show() puts a level set from elsewhere (evening mode, the keys) on the
    slider, not while a write of the card's own is under way, and
    evening() shows evening mode's own write to a monitor the same way."""

    def __init__(self, card, level, top, write, icon, title, text=percent,
                 lo=0, step=None, wrote=None):
        self.card, self.top, self.text = card, top, text
        self.level = level    # as last read or set
        self.wrote = wrote
        self.asked = None     # the card's last level, until it has landed
        self.failed = False   # that one didn't take
        self.dimming = None   # evening mode's write: "busy", "failed", None
        self.latest = Latest(write, self._landed)
        self.scale = slider(lo, top, step)
        self.scale.set_value(level)
        self.value = label("", "level")
        self._scale_id = self.scale.connect("value-changed", self._on_scale)
        self.row = slider_row(icon, title, self.scale, self.value)
        self._render()

    @property
    def touched(self):
        """Whether the card has set a level since it opened: a reading
        begun before then can be older than that."""
        return self.latest.value is not None

    def _on_scale(self, scale):
        self.asked = int(scale.get_value())
        self.latest.set(self.asked)
        self._render()

    def _landed(self, level, ok):
        if ok:
            self.level = level
            if self.wrote:
                self.wrote(level)
        # What came of the last level asked for; an earlier one's outcome
        # would only flash up before it, as the next write starts at once.
        if level == self.asked:
            self.asked, self.failed = None, not ok
        if not self.card.dead:
            self._render()

    def show(self, level):
        if self.latest.busy or level == self.level:
            return
        self.level, self.asked, self.failed = level, None, False
        with self.scale.handler_block(self._scale_id):
            self.scale.set_value(level)
        self._render()

    def evening(self, status):
        """Evening mode's own write to this monitor, as the daemon has it
        (nightlight.ddc_status()): "busy", "failed" or None."""
        if status != self.dimming:
            self.dimming = status
            self._render()

    def _render(self):
        pending = self.asked is not None or self.dimming == "busy"
        failed = not pending and (self.failed or self.dimming == "failed")
        if failed:
            self.value.set_text("Failed")
            self.value.set_tooltip_text(
                "It didn't take that level" if self.failed else
                "Evening mode got no answer from it, and tries again")
        else:
            level = self.level if self.asked is None else self.asked
            self.value.set_text(self.text(level, self.top)
                                + ("…" if pending else ""))
            self.value.set_tooltip_text(None)
        ctx = self.value.get_style_context()
        (ctx.add_class if pending else ctx.remove_class)("pending")
        (ctx.add_class if failed else ctx.remove_class)("bad")


class DisplayPanel(Panel):
    """Evening mode (warm tint and lower brightness on a schedule, run by
    `nightlight daemon`), keep awake (nightlight.awake_command()), the
    built-in panel's backlight and the keyboard's light through logind,
    each external monitor's own brightness over DDC/CI, and the screens'
    layout (hyprland.lua's screen_layout())."""

    title = "Display"
    css = "display"

    def __init__(self, app):
        super().__init__(app)
        self.header_button(
            ICON["screen-off"], "Turn the screens off", "sh", "-c",
            # A second's wait, so that the key that pressed the button is
            # up again: any key wakes the screens (hyprland.lua,
            # key_press_enables_dpms). Moving the mouse doesn't.
            "sleep 1; hyprctl dispatch 'hl.dsp.dpms({ action = \"off\" })'")
        self.state = nightlight.load()
        # The monitors that have answered evening mode over DDC/CI: one
        # plugged in while the card is open gets its row once it has.
        self.known = nightlight.read_json(nightlight.DIMMED, {}).get("known")
        self._warmth_id = None
        self._dim_id = None
        # (connector, i2c bus) -> its monitor's row, left to right
        self.ddc_rows = {}
        self.awake = None        # keep awake's end as shown; None: off
        self.awake_want = False  # the switch's last position
        self.awake_busy = False  # a start or stop in flight
        self.fill()
        # The daemon flips evening mode at the switch times and turns the
        # screens down with it, the bar's right click toggles it, the keys
        # move the levels of the screens and the keyboard's light, and keep
        # awake runs out; follow all of it while the card is open.
        self._poll_id = GLib.timeout_add_seconds(1, self._poll)
        self._bar_id = None  # a bar refresh due within BAR_MS
        self.connect("destroy", self._closed)

    def _closed(self, _panel):
        GLib.source_remove(self._poll_id)
        if self._bar_id is not None:  # the bar still shows the old level
            GLib.source_remove(self._bar_id)
            self._refresh_bar()

    def bar_later(self):
        """Have the bars' display modules re-read their screen's level,
        which they show: within BAR_MS, so a drag refreshes them every
        BAR_MS at most (each refresh runs `nightlight status`), or at once
        if the card has closed meanwhile."""
        if self.dead:
            nightlight.refresh_waybar()
        elif self._bar_id is None:
            self._bar_id = GLib.timeout_add(BAR_MS, self._refresh_bar)

    def _refresh_bar(self):
        self._bar_id = None
        nightlight.refresh_waybar()
        return GLib.SOURCE_REMOVE

    def fill(self):
        # Evening mode: on/off, then how warm, then when.
        evening, self.evening_sub, self.evening_switch = switch_row(
            ICON["night"], "Evening mode")
        self.body.pack_start(evening, False, False, 0)
        self._evening_id = self.evening_switch.connect(
            "notify::active", self._on_evening)

        # 250 K a key press or a wheel notch: twelve across the range.
        self.warmth = slider(nightlight.WARMTH_MIN, nightlight.WARMTH_MAX, 250)
        self.warmth_val = label("")
        self.body.pack_start(slider_row(ICON["warmth"], "Warmth",
                                        self.warmth, self.warmth_val),
                             False, False, 0)
        self._warmth_scale_id = self.warmth.connect("value-changed",
                                                    self._on_warmth)
        # How far evening mode turns the screens down, where it has one to
        # turn down: the panel or a DDC/CI monitor. A new level takes them
        # there at once if it is on (the daemon does). On the brightness
        # keys' levels (osd.LEVELS): even steps to the eye, a key press or
        # a wheel notch each, down to the 1 % the keys reach too, where
        # the panel sits in the evening.
        self.dim = None
        if nightlight.dimmable():
            self.dim = slider(0, len(osd.LEVELS) - 1, 1)
            self.dim_val = label("")
            self.body.pack_start(slider_row(ICON["dim"], "Dim to", self.dim,
                                            self.dim_val), False, False, 0)
            self._dim_scale_id = self.dim.connect("value-changed",
                                                  self._on_dim)

        schedule, _, self.auto_switch = switch_row(
            ICON["schedule"], "Schedule", sub=False)
        self.body.pack_start(schedule, False, False, 0)
        self._auto_id = self.auto_switch.connect("notify::active",
                                                 self._on_auto)
        self.steppers = Gtk.Box(spacing=4)
        self.steppers.get_style_context().add_class("steppers")
        self.start_val = self._stepper("start")
        self.steppers.pack_start(label("to", "sub"), False, False, 6)
        self.end_val = self._stepper("end")
        self.body.pack_start(self.steppers, False, False, 0)
        self.sync()

        # Keep awake: ends by itself after nightlight.AWAKE_FOR.
        awake, self.awake_sub, self.awake_switch = switch_row(
            ICON["coffee"], "Keep awake")
        self.body.pack_start(awake, False, False, 0)
        self._awake_id = self.awake_switch.connect("notify::active",
                                                   self._on_awake)
        self._show_awake(nightlight.awake_until())

        self.body.pack_start(label("Brightness", "seclabel"), False, False, 0)
        # The laptop's own screen and keyboard light, while its lid is open:
        # a desktop has neither.
        laptop = osd.panel_lit()
        panel = osd.backlight() if laptop else None
        light = keyboard_light() if laptop else None
        self.panel = self.kbd = None
        if panel:
            name, level, top = panel
            self.panel = LevelRow(
                self, level, top, logind_write("backlight", name),
                ICON["laptop"], "Built-in", lo=osd.backlight_floor(top),
                wrote=lambda _level: self.bar_later())  # the bar shows it
            self.body.pack_start(self.panel.row, False, False, 0)
        if light:
            # Off, Low, High here (max_brightness 2): a step each.
            name, level, top = light
            self.kbd = LevelRow(self, level, top, logind_write("leds", name),
                                ICON["keyboard"], "Keyboard", kbd_text,
                                step=max(1, top // 20))
            self.body.pack_start(self.kbd.row, False, False, 0)
        # A row for each monitor that answers over DDC/CI (_find_monitors).
        self.monitors = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.pack_start(self.monitors, False, False, 0)
        self.builtin = panel is not None
        self._find_monitors()
        # Docked or at a projector, the lid open: how the panel and the
        # other screens share the picture. Not on the desktop (no panel),
        # nor with the lid shut, where the lid decides (hyprland.lua).
        self.layout = None
        found = screens()
        if found:
            self._screens(*found)

    def _stepper(self, key):
        """− HH:MM +, moving the schedule's `key` time by TIME_STEP."""
        value = label("", "time", xalign=0.5)
        value.set_width_chars(5)
        for glyph, step in ((ICON["minus"], -TIME_STEP), (None, 0),
                            (ICON["plus"], TIME_STEP)):
            if glyph is None:
                self.steppers.pack_start(value, False, False, 0)
                continue
            btn = button(glyph, "iconbtn", "mini")
            btn.connect("clicked", self._on_step, key, step)
            self.steppers.pack_start(btn, False, False, 0)
        return value

    # --- evening mode ----------------------------------------------------

    def sync(self):
        """Show self.state."""
        st = self.state
        with self.evening_switch.handler_block(self._evening_id):
            self.evening_switch.set_active(st["on"])
        with self.auto_switch.handler_block(self._auto_id):
            self.auto_switch.set_active(st["auto"])
        with self.warmth.handler_block(self._warmth_scale_id):
            self.warmth.set_value(self._kelvin(st["warmth"]))
        self.warmth_val.set_text(f"{st['warmth']} K")
        if self.dim:
            with self.dim.handler_block(self._dim_scale_id):
                self.dim.set_value(dim_step(st["dim"]))
            self.dim_val.set_text(f"{st['dim']}%")
        self.start_val.set_text(st["start"])
        self.end_val.set_text(st["end"])
        self.steppers.set_sensitive(st["auto"])

        evening = nightlight.in_evening(st)
        if st["on"] and nightlight.paused():
            sub = "Paused while a film plays on the TV"
        elif st["on"]:
            sub = f"On until {st['end']}" if st["auto"] and evening else "On"
        elif st["auto"]:
            sub = "Off for tonight" if evening else f"From {st['start']}"
        else:
            sub = "Off"
        self.evening_sub.set_text(sub)

    def _write(self, **changes):
        """Save to the state file and wake the daemon, which applies it.
        Turning the schedule on or moving it means following it right away,
        as the daemon would."""
        st = self.state
        st.update(changes)
        if st["auto"] and ({"auto", "start", "end"} & changes.keys()):
            st["on"] = changes["on"] = nightlight.in_evening(st)
        nightlight.update(**changes)
        self.sync()

    def _poll(self):
        current = nightlight.load()
        if current != self.state:
            self.state = current
            self.sync()
        if self.panel:
            panel = osd.backlight()
            if panel:
                self.panel.show(panel[1])
        if self.kbd:
            light = keyboard_light()
            if light:
                self.kbd.show(light[1])
        # A monitor's level as its bar has it: the keys, evening mode or
        # another card set it, or a reading of ours found it; and evening
        # mode's own write to it, on its way or not answered.
        status = nightlight.ddc_status()
        for (conn, _bus), row in self.ddc_rows.items():
            now = nightlight.ddc_level(conn)
            if now:
                row.show(now[0])
            row.evening(status.get(conn))
        # Evening mode has found a monitor (plugged in just now): its row.
        known = nightlight.read_json(nightlight.DIMMED, {}).get("known")
        if known != self.known:
            self.known = known
            self._find_monitors()
        if not self.awake_busy:
            until = nightlight.awake_until()
            if until != self.awake:
                self._show_awake(until)
        return GLib.SOURCE_CONTINUE

    def _on_evening(self, switch, _pspec):
        self._write(on=switch.get_active())

    def _on_auto(self, switch, _pspec):
        self._write(auto=switch.get_active())

    def _on_step(self, _btn, key, step):
        mins = nightlight.minutes(self.state[key]) + step
        self._write(**{key: nightlight.hhmm(mins % 1440)})

    @staticmethod
    def _kelvin(value):
        """Slider position <-> K, both ways: further right is warmer, which
        is fewer K, and the fill grows from the left like the others."""
        return nightlight.WARMTH_MIN + nightlight.WARMTH_MAX - int(value)

    def _on_warmth(self, scale):
        self.warmth_val.set_text(f"{self._kelvin(scale.get_value())} K")
        # Every write is a colour-matrix commit; a drag needs far fewer.
        if self._warmth_id is None:
            self._warmth_id = GLib.timeout_add(60, self._apply_warmth)

    def _apply_warmth(self):
        self._warmth_id = None
        self._write(warmth=self._kelvin(self.warmth.get_value()))
        return GLib.SOURCE_REMOVE

    def _on_dim(self, scale):
        self.dim_val.set_text(f"{osd.LEVELS[int(scale.get_value())]}%")
        # The daemon takes the screens there, a monitor over DDC/CI in
        # ~150 ms; a drag needs far fewer writes than it makes.
        if self._dim_id is None:
            self._dim_id = GLib.timeout_add(60, self._apply_dim)

    def _apply_dim(self):
        self._dim_id = None
        self._write(dim=osd.LEVELS[int(self.dim.get_value())])
        return GLib.SOURCE_REMOVE

    # --- keep awake ------------------------------------------------------

    def _show_awake(self, until):
        """Keep awake on until `until` (epoch s), or off for None."""
        self.awake = until
        with self.awake_switch.handler_block(self._awake_id):
            self.awake_switch.set_active(until is not None)
        self._awake_line(until)

    def _awake_line(self, until, failed=None):
        """The sub-line: what it does, until when, or that it didn't take."""
        if failed:
            text = failed
        elif until is None:
            hours = nightlight.AWAKE_FOR // 3600
            text = f"Holds off lock and sleep for {hours} h"
        else:
            text = f"Screen stays on until {nightlight.clock(until)}"
        self.awake_sub.set_text(text)
        ctx = self.awake_sub.get_style_context()
        if failed:
            ctx.add_class("bad")
        else:
            ctx.remove_class("bad")

    def _on_awake(self, switch, _pspec):
        # One start or stop in flight; flipping it again meanwhile ends on
        # the last position, as a slider drag does.
        self.awake_want = switch.get_active()
        if not self.awake_busy:
            self._set_awake()

    def _set_awake(self):
        on = self.awake_want
        self.awake_busy = True
        if on:
            until = int(time.time()) + nightlight.AWAKE_FOR
            cmd = nightlight.awake_command(until)
        else:
            until = None
            cmd = ("systemctl", "--user", "stop", nightlight.AWAKE_UNIT)
        self._awake_line(until)  # at once; _awake_set() checks it took
        run_async(lambda *_: self._awake_set(on), *cmd)

    def _awake_set(self, on):
        """Both commands return once done: systemd-run when the inhibitor
        is held, systemctl when the holder is gone."""
        self.awake_busy = False
        if self.dead:
            return
        if self.awake_want != on:  # flipped back meanwhile
            self._set_awake()
            return
        until = nightlight.awake_until()
        self._show_awake(until)
        if (until is not None) != on:
            self._awake_line(until, "Couldn't keep the screen on" if on
                             else "Couldn't turn it off")

    # --- brightness over DDC/CI -----------------------------------------

    def _find_monitors(self):
        """Each monitor's brightness. Its row comes at once, at the level
        last read or set (nightlight.ddc_level, which the bars show too),
        so the card opens at its full height; then ddcutil reads them all
        afresh, ~100 ms a monitor, and each row takes what it finds.
        `ddcutil detect` takes most of a second, so its bus numbers are
        kept until other screens are plugged in (nightlight.cached_buses),
        and looked up again when a read fails."""
        buses = nightlight.cached_buses()
        if buses is None:
            self._detect()
            return
        self._show_monitors(buses, {conn: nightlight.ddc_level(conn)
                                    for conn in buses}, read=False)
        self._read_all(buses, cached=True)

    def _detect(self):
        plugged = nightlight.connectors()
        run_async(lambda ok, out, _err: self._detected(ok, out, plugged),
                  "ddcutil", "detect", "--terse")

    def _detected(self, ok, out, plugged):
        if self.dead:
            return
        buses = nightlight.parse_detect(out)
        if ok:  # a detect that failed is tried again on the next open
            try:
                nightlight.save_buses(buses, plugged)
            except OSError:
                pass
        self._read_all(buses, cached=False)

    def _read_all(self, buses, cached):
        if not buses:
            self._show_monitors({}, {})
            return
        found = {}

        def got(ok, out, _err, conn):
            if self.dead:
                return
            found[conn] = nightlight.parse_brightness(out) if ok else None
            # On its row at once, if it has one: a monitor that takes
            # seconds to answer holds up no other.
            key = conn, buses[conn]
            if found[conn] and key in self.ddc_rows:
                self._note([key], found)
                if not self.ddc_rows[key].touched:
                    self.ddc_rows[key].show(found[conn][0])
            if len(found) < len(buses):
                return
            if cached and None in found.values():
                # Stale bus numbers (a monitor moved ports): look again.
                self._detect()
            else:
                self._show_monitors(buses, found)

        for conn, bus in buses.items():
            run_async(lambda ok, out, err, conn=conn: got(ok, out, err, conn),
                      "ddcutil", "--bus", str(bus), "--skip-ddc-checks",
                      "getvcp", "10", "--brief")

    def _show_monitors(self, buses, found, read=True):
        """A row for each monitor in `buses` that `found` has a (level, max)
        for and that is lit (a mirror too: it shows a picture); `read`:
        found afresh over DDC/CI, not as last noted. The same monitors on
        the same buses as shown already keep their rows, which take the new
        level (not after a write of the card's own, which the reading can
        predate), so a reading never rebuilds the row under the pointer."""
        hypr = {m["name"]: m for m in osd.hyprland("j/monitors all") or []
                if not m.get("disabled")}
        # Left to right, the way they stand on the desk.
        shown = sorted((c for c in buses if found.get(c) and c in hypr),
                       key=lambda c: (hypr[c]["x"], hypr[c]["y"]))
        keys = [(conn, buses[conn]) for conn in shown]
        if read:
            self._note(keys, found)
        if keys and keys == list(self.ddc_rows):
            for (conn, _bus), row in self.ddc_rows.items():
                if not row.touched:
                    row.show(found[conn][0])
            return
        for child in self.monitors.get_children():
            child.destroy()
        self.ddc_rows = {}
        status = nightlight.ddc_status()
        for conn, bus in keys:
            level, top = found[conn]
            row = LevelRow(self, level, top, ddc_write(bus), ICON["monitor"],
                           hypr[conn].get("model") or conn,
                           wrote=partial(self._ddc_wrote, conn, top))
            row.evening(status.get(conn))
            self.ddc_rows[conn, bus] = row
            self.monitors.pack_start(row.row, False, False, 0)
        if read and not keys and not self.builtin:
            self.monitors.pack_start(label("No monitor answers DDC/CI",
                                           "empty"), False, False, 0)
        self.monitors.show_all()

    def _note(self, keys, found):
        """Keep the levels just read for the bars, as each monitor's bar
        shows its own; not one the card has set since, as the reading can
        predate that."""
        noted = False
        for conn, bus in keys:
            row = self.ddc_rows.get((conn, bus))
            if row is not None and row.touched:
                continue
            if found[conn] != nightlight.ddc_level(conn):
                nightlight.note_ddc_level(conn, *found[conn])
                noted = True
        if noted:
            self.bar_later()

    def _ddc_wrote(self, conn, top, level):
        """A monitor took a level the card set: its bar shows it."""
        nightlight.note_ddc_level(conn, level, top)
        self.bar_later()

    # --- the screens' layout --------------------------------------------

    def _screens(self, panel, others):
        """The Screens chooser, its pill on the layout Hyprland shows now
        (screens(): the panel's and the other screens' monitors)."""
        self.layout_now = layout_of(panel, others)
        self.layout_others = [m["name"] for m in others]
        self.layout_busy = False  # a switch in flight
        self.body.pack_start(label("Screens", "seclabel"), False, False, 0)
        self.layout = Segmented(
            [(key, (glyph, name)) for key, glyph, name, _tip in LAYOUTS],
            self._on_layout, height=SEG_H)
        for btn, (*_, tip) in zip(self.layout.buttons, LAYOUTS):
            btn.set_tooltip_text(tip)
        self.layout.select(self.layout_now, slide=False)
        self.body.pack_start(self.layout, False, False, 0)
        # What went wrong, in red under it; wrapped, as the Battery card's
        # mode errors are (36 chars stay inside the card).
        self.layout_note = label("", "sub", "bad", "note")
        self.layout_note.set_line_wrap(True)
        self.layout_note.set_max_width_chars(36)
        self.layout_note.set_no_show_all(True)
        self.body.pack_start(self.layout_note, False, False, 0)

    def _on_layout(self, key):
        """Switch to layout `key` through hyprland.lua, which knows each
        screen's rule, naming the other screens: Lua there sees only the
        lit ones. The names are DRM connectors (screens()), safe in a Lua
        string. One switch at a time."""
        if key == self.layout_now or self.layout_busy:
            return
        self.layout.select(key)  # the pill slides over at once
        self.layout_busy = True
        self.layout_note.hide()
        names = ", ".join(f'"{name}"' for name in self.layout_others)
        run_async(self._layout_set, "hyprctl", "eval",
                  f'screen_layout("{key}", {{ {names} }})')

    def _layout_set(self, ok, out, err):
        """hyprctl prints ok, or an error and exits 7 (a Lua error, say from
        a hyprland.lua without screen_layout()). Done, the card closes: its
        own screen may have gone dark or become a mirror."""
        self.layout_busy = False
        if self.dead:
            return
        if ok:
            self.app.close()
            return
        self.layout.select(self.layout_now)  # back to how they stand
        self.layout_note.set_text("Couldn't switch the screens")
        self.layout_note.set_tooltip_text((out or err).strip() or None)
        self.layout_note.show()
