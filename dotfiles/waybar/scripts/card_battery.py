"""The Battery card, `bar-popup battery`: the charge level and what the
battery is doing, time left and the power going in or out, a warning when
the charger keeps cutting out, the power mode (power-profiles-daemon), a
switch for the charge limit where the battery has one, and the battery's
wear.

The readings come from the battery's sysfs node, worked out the way
waybar's battery module works them out, so the card shows the level the
bar shows. UPower adds its history of the battery's rate, which it samples
every 30 s: the mean of the last 10 minutes gives the time left, counted
down to the level at which UPower hibernates the laptop, and the same
history shows a charger that keeps cutting out. Its change signal says
when to read again: it re-reads the battery every 30 s, and at once when
the charger comes or goes. The charge limit is the firmware's charge mode,
read and written in the battery's sysfs node off the main loop. Without a
battery (the desktop PC) the card says so; the bar's battery module hides
itself there.
"""

import os
import re
import threading
import time
from collections import namedtuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
from cardkit import (CHARGING, DISCHARGING, MODES, PPD, UPOWER,  # noqa: E402
                     Latest, Panel, Segmented, label, prop, read_text,
                     switch_row)

SUPPLY = "/sys/class/power_supply"
# The bar's battery states (config.jsonc): peach at 30 % and below, red at
# 15 % and below while discharging.
WARNING, CRITICAL = 30, 15
# UPower's policy (hosts/laptop/default.nix, services.upower): at PercentageAction
# it takes its critical action, Hibernate here, where logind can; else the
# next one it can, PowerOff when there is no swap to hibernate to. Its
# GetCriticalAction names the one it would take; ENDINGS, as the time
# left's tooltip says it.
UPOWER_CONF = "/etc/UPower/UPower.conf"
ENDINGS = {"Hibernate": "hibernates", "HybridSleep": "hibernates",
           "PowerOff": "shuts down", "Suspend": "suspends",
           "Sleep": "suspends"}
# The time left goes by the mean of UPower's rate samples (one per 30 s)
# over the last AVERAGE_S. One sample swings between 5 and 16 W from one
# minute to the next here: replayed over a day of UPower's history, that
# moved the estimate by a median 49 min within any 5 minutes, the mean of
# 10 minutes by 10. Until MIN_SAMPLES have come in since the charger went
# (2 minutes), the card shows the power alone.
AVERAGE_S = 600
MIN_SAMPLES = 4
# A charger that keeps cutting out: FLAPS charging spells shorter than
# SPELL_S within the last hour (17 Oct: five spells of about a minute in
# 40 minutes, the bar flipping between green and red, then UPower powered
# the laptop off with the charger still in).
FLAPS, SPELL_S = 3, 180

# UPower's display device (cardkit's UPOWER) keeps no history; the battery
# does (object_path). The daemon itself answers GetCriticalAction.
UPOWER_DAEMON = ("/org/freedesktop/UPower", "org.freedesktop.UPower")

# The charge limit, where the firmware has one: Dell's charge modes
# (dell-laptop: the battery's charge_types, which the BIOS keeps). In Fast
# (ExpressCharge, the mode in use by choice: hosts/laptop/default.nix) it charges
# to 100 % as fast as it can; in Custom it stops at the BIOS's stop
# threshold and starts again only below its start threshold
# (charge_control_end/start_threshold, 90 and 50 % here). The switch
# writes one mode or the other, so turning the limit off goes back to Fast
# and the thresholds stay the BIOS's: UPower's own switch,
# EnableChargeThreshold, would write its 75/80 % over them, and 50/100 %
# in Standard mode when turned off (up-device-supply-battery.c). The users
# group may write that one file (hosts/laptop/default.nix); the thresholds stay
# root's. Each read is a call into the firmware, ~6 ms for the mode here,
# so the card reads them off the main loop.
LIMIT_ON, LIMIT_OFF = "Custom", "Fast"
# A reading: the mode in use, the thresholds in % (None where the battery
# has none) and whether this user may write the mode.
Limit = namedtuple("Limit", "mode start end writable")

# md-battery glyphs from empty to full in tens of %: the bar's format-icons,
# and the same with a bolt for charging.
LEVEL_ICONS = ("\U000f008e", "\U000f007a", "\U000f007b", "\U000f007c",
               "\U000f007d", "\U000f007e", "\U000f007f", "\U000f0080",
               "\U000f0081", "\U000f0082", "\U000f0079")
CHARGING_ICONS = ("\U000f089f", "\U000f089c", "\U000f0086", "\U000f0087",
                  "\U000f0088", "\U000f089d", "\U000f0089", "\U000f089e",
                  "\U000f008a", "\U000f008b", "\U000f0085")
ICON = {
    "health": "\U000f05f6",  # md-heart_pulse
    "cycles": "\U000f04e6",  # md-sync
    "temp": "\U000f050f",    # md-thermometer
    "limit": "\U000f1211",   # md-battery_heart_variant
}

# The power modes' selector: a glyph over a label, px.
SEG_H = 54

CSS = b"""
/* Battery card. The level's glyph and the status pill take the bar's
   colours for the battery: yellow, @warn from 30 % and @crit from 15 %
   while discharging, @ok on the charger. */
.card.battery .charge { padding: 0 10px 4px; }
.card.battery .gauge { font-size: 44px; }
.card.battery .percent { color: @text; font-size: 32px; font-weight: bold; }
.card.battery .status {
    background-color: @raised;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: bold;
}
.card.battery .gauge, .card.battery .status { color: @yellow; }
.card.battery .ok { color: @ok; }
.card.battery .warn { color: @warn; }
.card.battery .bad { color: @crit; }
/* The power modes on cardkit's selector: too long for one line each, so
   the label goes under the glyph, in 12 px. */
.card.battery .seg { font-size: 12px; }
.card.battery .note { padding: 2px 10px 0; }
/* The battery's wear as a footer, like the Weather card's facts. */
.card.battery .wear { padding: 6px 10px 0; }
.card.battery .wear .icon { font-size: 15px; color: @subtext0; }
/* cardkit draws a switch the same either way; one this user may not flip
   (no write access to the charge mode) fades. */
.card.battery switch:disabled { opacity: 0.4; }
"""


def battery_path():
    """The battery's sysfs directory, or None. As for waybar's battery
    module, a battery is a supply of type Battery that doesn't power a
    device of its own (a mouse, a game pad)."""
    try:
        names = sorted(os.listdir(SUPPLY))
    except OSError:
        return None
    for name in names:
        path = f"{SUPPLY}/{name}"
        if (read_text(f"{path}/type") == "Battery"
                and read_text(f"{path}/scope") != "Device"):
            return path
    return None


def parse_uevent(text):
    """A battery's uevent as a dict, keys without the POWER_SUPPLY_ prefix,
    or None if the battery isn't there. Reading uevent takes all values in
    one ACPI query, ~25 ms on the notebook."""
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        values[key.removeprefix("POWER_SUPPLY_")] = value
    return values if values and values.get("PRESENT") != "0" else None


def number(bat, key):
    """An integer reading, 0 if it is missing or garbled."""
    try:
        return int(bat.get(key, 0))
    except ValueError:
        return 0


def level(bat):
    """The charge in %, worked out as waybar 0.15 does: charge (or energy)
    now over full, rounded to the nearest whole number (battery.cpp, its
    default weighted average, then round()). Halves go up, as C's round()
    takes them, not to even as Python's does. The firmware's own capacity
    can still differ from it by one."""
    for unit in ("CHARGE", "ENERGY"):
        full = number(bat, f"{unit}_FULL")
        if full > 0 and f"{unit}_NOW" in bat:
            now = number(bat, f"{unit}_NOW")
            return min(100, (200 * now + full) // (2 * full))
    return min(100, number(bat, "CAPACITY"))


def watts(bat):
    """The power going into or out of the battery, as waybar's {power}."""
    if "POWER_NOW" in bat:
        return abs(number(bat, "POWER_NOW")) / 1e6
    return abs(number(bat, "CURRENT_NOW")) * number(bat, "VOLTAGE_NOW") / 1e12


def wear(bat):
    """The footer: {key: text} for health, charge cycles and temperature,
    each only if the battery reports it."""
    facts = {}
    for unit in ("CHARGE", "ENERGY"):
        design = number(bat, f"{unit}_FULL_DESIGN")
        if design > 0 and f"{unit}_FULL" in bat:
            health = 100 * number(bat, f"{unit}_FULL") / design
            facts["health"] = f"Health {health:.0f}%"
            break
    cycles = number(bat, "CYCLE_COUNT")
    if cycles > 0:
        facts["cycles"] = f"{cycles} cycle{'s' if cycles > 1 else ''}"
    if "TEMP" in bat:  # tenths of a degree
        facts["temp"] = f"{number(bat, 'TEMP') / 10:.0f} °C"
    return facts


def shape(bat):
    """What the card lays out for a reading: nothing for no battery, else
    the footer's facts."""
    return None if bat is None else tuple(wear(bat))


def duration(seconds):
    """'2 h 5 min', '1 h' or '40 min'."""
    hours, mins = divmod(max(1, round(seconds / 60)), 60)
    if not hours:
        return f"{mins} min"
    return f"{hours} h {mins} min" if mins else f"{hours} h"


def object_path(name):
    """UPower's object for the battery whose sysfs node is `name`, named
    as upowerd names it (up_device_compute_object_path): battery_BAT0."""
    return ("/org/freedesktop/UPower/devices/battery_"
            + name.translate(str.maketrans("-.:@", "_xo_")))


def reserve(conf):
    """The charge in % at which UPower takes its critical action, from its
    config's text (PercentageAction), which the time left counts down to:
    the last 2 % are never there to use. 0 where UPower goes by time left
    instead (UsePercentageForPolicy=false)."""
    keys = {}
    for line in conf.splitlines():
        key, _, value = line.partition("=")
        keys[key.strip()] = value.strip()
    if keys.get("UsePercentageForPolicy", "true").lower() in ("false", "0"):
        return 0.0
    try:
        return float(keys.get("PercentageAction", "2"))
    except ValueError:
        return 2.0


def since_unplugged(rates, now):
    """UPower's discharge rates (W) from the last AVERAGE_S, back to when
    the charger last went; `rates` oldest first. UPower records a 0 at
    each change of state, which isn't a reading."""
    out = []
    for t, watts, state in reversed(rates):
        if now - t > AVERAGE_S or state != DISCHARGING:
            break
        if watts > 0:
            out.append(watts)
    return out


def short_spells(rates, now):
    """Charging spells shorter than SPELL_S that began in the last hour:
    a charger that cuts out, as a loose plug or a too-weak charger does;
    `rates` oldest first."""
    spells, start = 0, None
    for t, _, state in rates:
        if state == CHARGING:
            if start is None:
                start = t
        elif start is not None:
            if t - start < SPELL_S and now - start <= 3600:
                spells += 1
            start = None
    return spells


def charge_modes(text):
    """A battery's charge_types as (the mode in use, the modes on offer),
    or None without one in use: "Trickle [Fast] Standard Adaptive Custom"
    gives ("Fast", ["Trickle", "Fast", "Standard", "Adaptive", "Custom"]).
    The kernel brackets the mode in use; "Long Life" is the one name it
    has with a space in it."""
    active, modes = None, []
    for item in re.findall(r"\[[^]]*\]|Long Life|\S+", text):
        mode = item.strip("[]")
        if item.startswith("["):
            active = mode
        modes.append(mode)
    return None if active is None else (active, modes)


def read_limit(path):
    """The charge limit of the battery at sysfs `path`, a Limit, or None
    where it has no Custom and Fast modes to switch between. Slow: each
    file is a firmware call (LIMIT_ON)."""
    modes = charge_modes(read_text(f"{path}/charge_types"))
    if modes is None or not {LIMIT_ON, LIMIT_OFF} <= set(modes[1]):
        return None
    thresholds = []
    for end in ("start", "end"):
        text = read_text(f"{path}/charge_control_{end}_threshold")
        thresholds.append(int(text) if text.isdigit() else None)
    return Limit(modes[0], *thresholds,
                 os.access(f"{path}/charge_types", os.W_OK))


def limit_text(limit):
    """The line under the switch's name: how the battery charges now."""
    if limit.mode == LIMIT_ON:
        if limit.end is None:
            return "Stops at the BIOS's limit"
        again = ("" if limit.start is None
                 else f", resumes below {limit.start}%")
        return f"Stops at {limit.end}%{again}"
    if limit.mode in ("Fast", "Standard"):
        return f"Charges to 100%, in {limit.mode} mode"
    return f"Charges in {limit.mode} mode"


class BatteryPanel(Panel):
    """The charge with its status pill, time left and power, a warning when
    the charger keeps cutting out, the power mode, the charge limit's
    switch and the battery's wear. Laid out at once from sysfs; UPower's
    history (the time left) and the active mode follow over D-Bus a moment
    later, the charge limit from a worker thread. While the card is open,
    both daemons' change signals update it in place."""

    title = "Battery"
    css = "battery"

    def __init__(self, app):
        super().__init__(app)
        self.status = label("", "status", xalign=0.5)
        self.status.set_valign(Gtk.Align.CENTER)
        self.status.set_no_show_all(True)
        self.header.pack_end(self.status, False, False, 0)
        self.upower = None
        self.ppd = None
        self.ppd_known = False  # PPD's proxy is ready, daemon or not
        self.pending = None     # the mode asked for, until PPD answers
        self.mode_error = None
        self.reads = 0          # battery reads started; the latest one counts
        self.bus = None         # the system bus, once UPower's proxy is up
        self.rates = []         # UPower's (t, W, state) of the last hour
        self.reserve = reserve(read_text(UPOWER_CONF))
        self.ending = None      # what UPower does there, e.g. "hibernates"
        # The charge limit: its last reading (a Limit, None where there is
        # none), once the first is in; whether the last write failed. One
        # write of the mode at a time, ending on the switch's last
        # position, also once the card has closed.
        self.limit = None
        self.limit_known = False
        self.limit_failed = False
        self.limit_writer = Latest(self._write_limit, self._limit_written)
        # Opening a card never starts a daemon.
        flags = Gio.DBusProxyFlags.DO_NOT_AUTO_START
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, flags, None, *UPOWER,
                                  None, self._upower_ready)
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, flags, None, *PPD,
                                  None, self._ppd_ready)
        self.fill()

    def fill(self):
        # Read at once, so the card opens with its figures.
        self.path = battery_path()
        self.bat = self.path and parse_uevent(
            read_text(f"{self.path}/uevent"))
        self.shape = shape(self.bat)
        self.facts = {}
        self.selector = None
        self.limit_switch = None
        if self.bat is None:
            self.status.hide()
            self.body.pack_start(label("No battery", "empty"),
                                 False, False, 0)
        else:
            self.body.pack_start(self._charge(), False, False, 0)
            # A charger that keeps cutting out, in red under the charge;
            # wrapped as the mode's errors are, below.
            self.flap_note = label("", "sub", "bad", "note")
            self.flap_note.set_line_wrap(True)
            self.flap_note.set_max_width_chars(36)
            self.flap_note.set_no_show_all(True)
            self.body.pack_start(self.flap_note, False, False, 0)
        modes = self._offered()
        if modes:
            self.body.pack_start(label("Power mode", "seclabel"),
                                 False, False, 0)
            self.selector = Segmented([(m, MODES[m]) for m in modes],
                                      self._pick, height=SEG_H)
            self.body.pack_start(self.selector, False, False, 0)
            self.mode_note = label("", "sub", "bad", "note")
            # D-Bus errors can be long. A rebuild adds this label to a card
            # already shown, whose box may then measure it without a width:
            # 36 chars stay just inside the card, so that plain measure has
            # at least as many lines as the wrapped text.
            self.mode_note.set_line_wrap(True)
            self.mode_note.set_max_width_chars(36)
            self.mode_note.set_no_show_all(True)
            self.body.pack_start(self.mode_note, False, False, 0)
        if self.bat is not None:
            # Under the power mode, which stays where it was: the switch
            # is flipped once in days, the mode many times a day.
            if self._limit_offered():
                self.body.pack_start(self._limit_row(), False, False, 0)
            if self.shape:
                self.body.pack_start(self._wear(), False, False, 0)
            self._show_battery()
        self._show_mode(slide=False)
        self._show_limit()

    def _charge(self):
        box = Gtk.Box(spacing=14)
        box.get_style_context().add_class("charge")
        self.gauge = label("", "gauge", xalign=0.5)
        # As wide as the widest glyph (with the bolt), so the figures beside
        # it stay put when the charger comes or goes.
        self.gauge.set_size_request(48, -1)
        box.pack_start(self.gauge, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        self.percent = label("", "percent")
        text.pack_start(self.percent, False, False, 0)
        self.when = label("", "sub", ellipsize=Pango.EllipsizeMode.END)
        text.pack_start(self.when, False, False, 0)
        box.pack_start(text, True, True, 0)
        return box

    def _wear(self):
        box = Gtk.Box(spacing=6)
        box.get_style_context().add_class("wear")
        for key in self.shape:
            item = Gtk.Box(spacing=6)
            item.pack_start(label(ICON[key], "icon"), False, False, 0)
            self.facts[key] = label("", "sub")
            item.pack_start(self.facts[key], False, False, 0)
            box.pack_start(item, True, False, 0)
        return box

    def _limit_row(self):
        row, self.limit_line, self.limit_switch = switch_row(
            ICON["limit"], "Charge limit")
        # Shortened, should a line run long, rather than widening the card.
        self.limit_line.set_ellipsize(Pango.EllipsizeMode.END)
        self.limit_row = row
        self._limit_id = self.limit_switch.connect("notify::active",
                                                   self._on_limit)
        if not self.limit_known:
            self._read_limit()
        return row

    # --- The battery (sysfs, with UPower's history) -----------------------

    def _upower_ready(self, _source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return  # no system bus: the readings stay as they are
        if self.dead:
            return
        self.upower = proxy
        self.bus = proxy.get_connection()
        proxy.connect("g-properties-changed", self._reread)
        proxy.connect("notify::g-name-owner", self._reread)
        if self.bat is not None:
            # The reading just taken, then UPower's rates for the time left
            # and the action it would take at the reserve, which it asks
            # logind about first.
            self._show_battery()
            self._fetch_history()
            self.bus.call(UPOWER[0], *UPOWER_DAEMON, "GetCriticalAction",
                          None, GLib.VariantType("(s)"),
                          Gio.DBusCallFlags.NO_AUTO_START, 2000, None,
                          self._action_done)

    def _action_done(self, bus, result):
        try:
            action = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            return  # no answer: the tooltip names no action
        if self.dead:
            return
        self.ending = ENDINGS.get(action)
        if self.bat is not None:
            self._show_battery()

    def _reread(self, *_):
        """UPower has read the battery: read it too, and UPower's rates. The
        ACPI query runs in GIO's worker thread, so the card never waits on
        it."""
        if self.dead:
            return
        if self.path is None:
            if battery_path() is not None:
                self.refresh()  # a battery turned up
            return
        self._fetch_history()
        self.reads += 1
        Gio.File.new_for_path(f"{self.path}/uevent").load_contents_async(
            None, self._read_done, self.reads)

    def _read_done(self, uevent, result, n):
        """Show the reading in place, unless the battery went or now reports
        other facts: then lay the card out again."""
        try:
            bat = parse_uevent(uevent.load_contents_finish(result)[1].decode())
        except GLib.Error:
            bat = None
        if self.dead or n != self.reads:
            return  # a later read is on its way
        if shape(bat) != self.shape:
            self.refresh()
        else:
            self.bat = bat
            self._show_battery()

    def _show_battery(self):
        bat = self.bat
        pct = level(bat)
        status = bat.get("STATUS", "Unknown")
        if status == "Discharging":
            mood = ("bad" if pct <= CRITICAL
                    else "warn" if pct <= WARNING else None)
        elif status in ("Charging", "Full", "Not charging"):
            mood = "ok"
        else:
            mood = None
        # The bar's glyph: waybar picks format-icons[pct / (100 / 11)] in
        # whole numbers, the full battery from 90 % up.
        icons = CHARGING_ICONS if status == "Charging" else LEVEL_ICONS
        self.gauge.set_text(icons[min(pct // 9, 10)])
        self.percent.set_text(f"{pct}%")
        when, until = self._when(status)
        self.when.set_text(when)
        self.when.set_tooltip_text(until)
        self.status.set_text(status)
        self.status.show()
        for widget in (self.gauge, self.status):
            ctx = widget.get_style_context()
            for cls in ("ok", "warn", "bad"):
                if cls == mood:
                    ctx.add_class(cls)
                else:
                    ctx.remove_class(cls)
        for key, text in wear(bat).items():
            self.facts[key].set_text(text)
        flaps = short_spells(self.rates, time.time())
        self.flap_note.set_text(
            f"The charger cut out {flaps} times in the last hour. Check its "
            "plug, or use a stronger charger." if flaps >= FLAPS else "")
        self.flap_note.set_visible(flaps >= FLAPS)

    def _steady(self):
        """(s until UPower's critical action, mean W) from UPower's rates of
        the last AVERAGE_S, or None until MIN_SAMPLES came in since the
        charger went. On the morning of 17 Oct, replayed up to UPower's
        power-off at 2 %, this was off by a median 5 min, UPower's own
        TimeToEmpty (the energy over the last sample, down to 0 %) by 17."""
        recent = since_unplugged(self.rates, time.time())
        full = prop(self.upower, "EnergyFull", 0.0)
        if len(recent) < MIN_SAMPLES or full <= 0:
            return None
        rate = sum(recent) / len(recent)
        energy = prop(self.upower, "Energy", 0.0)
        left = max(0.0, energy - self.reserve / 100 * full) / rate * 3600
        return left, rate

    def _when(self, status):
        """The line under the level, and its tooltip. Discharging: the time
        left, and the mean power it goes by. Charging: UPower's time to full
        and its rate, both from one reading (steady there: Fast charging
        holds near 59 W). The power alone until there is either."""
        rate = f"{watts(self.bat):.1f} W"
        if status == "Discharging":
            steady = self._steady()
            if steady is None:
                return f"Drawing {rate}", None
            left, mean = steady
            return f"{duration(left)} left at {mean:.1f} W", self._until()
        if status == "Charging":
            left = prop(self.upower, "TimeToFull", 0)
            charge = prop(self.upower, "EnergyRate", 0.0)
            if left > 0 and charge > 0:
                return f"Full in {duration(left)} at {charge:.1f} W", None
            return f"Charging at {rate}", None
        if status in ("Full", "Not charging"):
            return "Plugged in", None
        return rate, None

    def _until(self):
        """What the time left counts down to, and from what: UPower's
        critical action, named once it has said which it would take."""
        mean = f"at the mean power of the last {AVERAGE_S // 60} minutes"
        if not self.reserve:
            return f"Until the battery is empty, {mean}"
        end = f"{self.reserve:g}%"
        if self.ending:
            return f"Until the laptop {self.ending} at {end}, {mean}"
        return f"Until the charge is down to {end}, {mean}"

    # --- UPower's history: the time left and the charger check ------------

    def _fetch_history(self):
        """UPower's rates of the last hour: one small D-Bus call each time
        UPower reads the battery (30 s), only while the card is open."""
        if self.bus is None or self.path is None:
            return
        # A resolution above the points there are (one per 30 s, one per
        # change of state): upowerd averages neighbours down to it, states
        # too.
        self.bus.call(UPOWER[0], object_path(os.path.basename(self.path)),
                      UPOWER[2], "GetHistory",
                      GLib.Variant("(suu)", ("rate", 3600, 5000)),
                      GLib.VariantType("(a(udu))"),
                      Gio.DBusCallFlags.NO_AUTO_START, 2000, None,
                      self._history_done)

    def _history_done(self, bus, result):
        try:
            points = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            points = []
        if self.dead:
            return
        # Oldest first: upowerd hands the newest first, its docs aside.
        self.rates = sorted(points)
        if self.bat is not None:
            self._show_battery()

    # --- The power mode (power-profiles-daemon) ----------------------------

    def _offered(self):
        """The modes PPD offers, in MODES order: power-saver and balanced
        always, performance where a driver supports it. Until its proxy is
        ready, the usual three, so the card is laid out as it will stay."""
        if not self.ppd_known:
            return list(MODES)
        if self.ppd is None or self.ppd.get_name_owner() is None:
            return []
        offered = {p.get("Profile") for p in prop(self.ppd, "Profiles", [])}
        return [mode for mode in MODES if mode in offered]

    def _ppd_ready(self, _source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            proxy = None
        if self.dead:
            return
        self.ppd, self.ppd_known = proxy, True
        if proxy is not None:
            proxy.connect("g-properties-changed", self._ppd_changed)
            proxy.connect("notify::g-name-owner", self._ppd_changed)
        self._ppd_changed()

    def _ppd_changed(self, *_):
        """Follow the daemon, whoever changed the mode: the pill slides to
        it. Other modes on offer, or the daemon coming or going, rebuild."""
        if self.dead:
            return
        if self._offered() != (self.selector.keys if self.selector else []):
            self.refresh()
        else:
            self._show_mode()

    def _show_mode(self, slide=True):
        if self.selector is None:
            return
        self.selector.select(self.pending or prop(self.ppd, "ActiveProfile"),
                             slide)
        self.mode_note.set_text(self.mode_error or "")
        self.mode_note.set_visible(self.mode_error is not None)

    def _pick(self, mode):
        """Ask PPD for `mode`. The pill moves at once and back if the daemon
        refuses; its polkit policy lets the active session's user switch
        without a password."""
        if self.ppd is None or self.ppd.get_name_owner() is None:
            return
        if mode == (self.pending or prop(self.ppd, "ActiveProfile")):
            return
        self.pending, self.mode_error = mode, None
        self._show_mode()
        args = GLib.Variant("(ssv)", (PPD[2], "ActiveProfile",
                                      GLib.Variant("s", mode)))
        self.ppd.call("org.freedesktop.DBus.Properties.Set", args,
                      Gio.DBusCallFlags.NONE, 5000, None, self._picked, mode)

    def _picked(self, proxy, result, mode):
        try:
            proxy.call_finish(result)
            error = None
        except GLib.Error as err:
            if err.matches(Gio.DBusError.quark(), Gio.DBusError.ACCESS_DENIED):
                error = "Not allowed to change the power mode"
            else:
                error = err.message.rsplit(": ", 1)[-1][:60]
        # A later click owns the pill now; its own answer settles it.
        if self.dead or mode != self.pending:
            return
        self.pending, self.mode_error = None, error
        self._show_mode()

    # --- The charge limit (the firmware's charge mode) --------------------

    def _limit_offered(self):
        """Whether the battery has a charge limit to switch: as its reading
        says, once that is in; until then, wherever it has the file, so the
        card opens laid out as it will stay."""
        if self.bat is None:
            return False
        if self.limit_known:
            return self.limit is not None
        return os.path.exists(f"{self.path}/charge_types")

    def _read_limit(self):
        """read_limit() in a worker thread; _limit_read() shows it."""
        path = self.path

        def work():
            GLib.idle_add(self._limit_read, read_limit(path))
        threading.Thread(target=work, daemon=True).start()

    def _limit_read(self, limit):
        # A write started since: its own reading follows once it is in.
        if self.dead or self.limit_writer.busy:
            return GLib.SOURCE_REMOVE
        offered = self._limit_offered()
        self.limit, self.limit_known = limit, True
        if self._limit_offered() != offered:
            self.refresh()  # no limit after all: laid out without it
        else:
            self._show_limit()
        return GLib.SOURCE_REMOVE

    def _show_limit(self):
        """The switch at the mode in use and, under its name, how the
        battery charges; greyed out until the first reading is in, and
        where this user may not write the mode."""
        if self.limit_switch is None:
            return
        limit = self.limit
        self.limit_switch.set_sensitive(limit is not None and limit.writable)
        if limit is None:
            return
        with self.limit_switch.handler_block(self._limit_id):
            self.limit_switch.set_active(limit.mode == LIMIT_ON)
        denied = (None if limit.writable
                  else "Not allowed to change the limit")
        if self.limit_failed:
            text = denied or "The firmware refused the change"
        else:
            text = limit_text(limit)
        self.limit_line.set_text(text)
        ctx = self.limit_line.get_style_context()
        (ctx.add_class if self.limit_failed else ctx.remove_class)("bad")
        self.limit_row.set_tooltip_text(denied)

    def _on_limit(self, switch, _pspec):
        """The switch flipped: the line says at once how the battery will
        charge, and the mode is written; _limit_written() reads what
        took."""
        mode = LIMIT_ON if switch.get_active() else LIMIT_OFF
        self.limit = self.limit._replace(mode=mode)
        self.limit_failed = False
        self._show_limit()
        self.limit_writer.set(mode)

    def _write_limit(self, mode, done):
        """Latest's write: `mode` into charge_types from a worker thread, as
        the firmware takes a moment; done(ok) back on the main loop."""
        path = f"{self.path}/charge_types"

        def work():
            try:
                with open(path, "w") as fh:
                    fh.write(mode)
            except OSError:
                GLib.idle_add(done, False)
            else:
                GLib.idle_add(done, True)
        threading.Thread(target=work, daemon=True).start()

    def _limit_written(self, mode, ok):
        """A write is in: read what took, once the switch's last position
        is written (an earlier one would only flash up)."""
        if self.dead or mode != self.limit_writer.value:
            return
        self.limit_failed = not ok
        self._read_limit()
