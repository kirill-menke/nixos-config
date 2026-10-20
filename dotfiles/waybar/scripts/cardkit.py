"""What bar-popup's cards are built from.

bar-popup.py is the popup window and the card registry; each card is a
module of its own, card_<name>.py, built from what is here: the card's
size, the glyphs more than one card shows, the palette for what a card
draws itself, the base stylesheet (typeface, window, card frame, titles,
list rows, icon buttons, switches, sliders, segmented choices, focus
rings and tooltips), the command runners, small helpers (prop,
read_text, rounded_rect), the widget helpers (each control
within reach of the keyboard), the controls more than one card has
(Segmented, Arm's two-click confirm, copy_flash, Latest's writes that
never block), and Panel, the class every card subclasses. A card module
keeps the rest to itself: its glyphs, its helpers and its own CSS, which
bar-popup appends to this one. It all ends up in one stylesheet, so a
card's rules also reach other cards' widgets that carry the same class
names.
"""

import math
import os
import re
import subprocess

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

CARD_W = 360    # fixed, so the card can be centred before it is drawn, px
CARD_TOP = 4    # gap under the bar's reserved area, px
EDGE = 8        # closest the card gets to a monitor edge, px
ARM_MS = 3000   # how long a row that acts for good waits for its 2nd click
COPIED_MS = 1200  # how long copy_flash says "Copied"
SEG_RADIUS = 12   # Segmented: the track's corner radius, px
SEG_PAD = 3       # Segmented: gap between the track and the pill, px
SLIDE_US = 220000  # Segmented: how long the pill takes to slide, µs

# Nerd Font glyphs, the same ones the bar uses, that more than one card
# shows; a card adds its own to them in its module.
ICON = {
    "headphones": "\U000f02cb",
    "speaker": "\U000f04c3",
    "tv": "\U000f0502",
}

# The bar's palette, mocha.css: the one copy of the colours, with the
# meanings FixBar gave them (@warn, @dim, …). Installed, it sits next to
# this module in waybar-popups' lib; in the source tree, one directory up.
# The stylesheet below starts with it, so the cards name colours as the
# bar does: by meaning wherever one of mocha.css's fits (@accent for the
# glyphs, fills and focus rings, @crit for an error or an armed row,
# @raised for a hover or a track, …), so a change there reaches every
# card, and by name where a colour is the thing's own (the battery's
# @yellow, a weather warning level's, the sun's). What a card draws itself
# takes them from MOCHA, cairo's 0..1 (r, g, b), and Pango markup from
# PALETTE ("#cba6f7"), the meanings too. There is no second copy to fall
# back on: without the file the card host fails at start, which beats
# cards in stale colours.
def _mocha_css():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "mocha.css"),
                 os.path.join(here, os.pardir, "mocha.css")):
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
    raise FileNotFoundError(
        f"mocha.css is neither next to {here} nor one directory up")


MOCHA_CSS = _mocha_css()
PALETTE = dict(re.findall(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]{6})\s*;",
                          MOCHA_CSS.decode()))
PALETTE.update((alias, PALETTE[name]) for alias, name in re.findall(
    r"@define-color\s+(\w+)\s+@(\w+)\s*;", MOCHA_CSS.decode()))
MOCHA = {name: tuple(int(code[i:i + 2], 16) / 255 for i in (1, 3, 5))
         for name, code in PALETTE.items()}

# power-profiles-daemon's service, object and interface on the system bus,
# and its modes in the selector's order: profile name -> (md-* glyph,
# label). The Battery card switches the mode, the System card names it.
PPD = ("org.freedesktop.UPower.PowerProfiles",
       "/org/freedesktop/UPower/PowerProfiles",
       "org.freedesktop.UPower.PowerProfiles")
MODES = {
    "power-saver": ("\U000f032a", "Power Saver"),  # md-leaf
    "balanced": ("\U000f05d1", "Balanced"),        # md-scale_balance
    "performance": ("\U000f04c5", "Performance"),  # md-speedometer
}

# UPower's composite "display device", the one desktop shells show: its
# energy, rate and time to full cover all of the machine's batteries, and
# its path never changes; and the device states it reports, as its history
# records them too. The Battery card shows it all, the System card the
# power drawn while on battery.
UPOWER = ("org.freedesktop.UPower",
          "/org/freedesktop/UPower/devices/DisplayDevice",
          "org.freedesktop.UPower.Device")
CHARGING, DISCHARGING = 1, 2

CSS = MOCHA_CSS + b"""
window { background-color: transparent; }
.backdrop { background-color: transparent; }

/* The bar's typeface, in the variant whose glyphs take their full width,
   for every node: GTK's default, dconf's Noto Sans, isn't installed and
   falls back to DejaVu Sans. Sizes go on .card and the classes instead: a
   size here would beat the one an icon button's label inherits. */
* { font-family: "FantasqueSansM Nerd Font Propo"; }

/* The frame swaync's notifications and panel have too: a 1 px border and
   16 px corners. */
.card {
    background-color: @surface;
    border: 1px solid @raised;
    border-radius: 16px;
    padding: 18px 20px;
    font-size: 14px;
}

/* At least as tall as an icon button or a switch, so the title row is
   28 px in every card and the title sits at the same height in all. */
.title {
    color: @text;
    font-weight: bold;
    font-size: 15px;
    min-height: 28px;
}

.seclabel {
    color: @subtext0;
    font-size: 12px;
    margin-top: 4px;
}

.empty { color: @subtext0; padding: 6px 10px; }

.icon {
    font-size: 19px;
    color: @accent;
}

.row, .iconbtn {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    text-shadow: none;
    border-radius: 12px;
    color: @text;
}

.row, .static { padding: 6px 10px; }
.row:hover, .row.on { background-color: @raised; }
.row.on .name { color: @accent; font-weight: bold; }
.sub { color: @subtext0; font-size: 12px; }
/* Lighter on a hovered or highlighted row: subtext0 has 4.1:1 there, short
   of the 4.5:1 small text needs. */
.row:hover .sub:not(.bad), .row.on .sub:not(.bad) { color: @subtext1; }
.sub.bad, .empty.bad { color: @crit; }

.iconbtn {
    font-size: 19px;
    padding: 2px 8px;
}
.iconbtn:hover { background-color: @raised; }
/* A mute toggle while muted (the Sound card's) in red, but not @crit:
   muted is a state to rest in, and nothing rests in @crit. */
.iconbtn.on { color: @red; }
/* An icon button on a highlighted row (the Network card's disconnect)
   needs a hover lighter than the row itself. */
.row.on .iconbtn:hover { background-color: @surface2; }
/* Held down, a row or an icon button lifts a step past its hover, so a
   click shows at once even when what it starts (a join, a scan) takes
   seconds; Enter and Space flash it too. Armed rows keep their red: their
   hover rules are more specific. */
.row:active, .iconbtn:active { background-color: @surface2; }
.row.on .iconbtn:active { background-color: @overlay0; }

/* Slider rows line their glyph up with the switch and list rows. */
.slider { padding: 0 10px; }

/* Segmented's choices: text (or a glyph over it) on its track and sliding
   pill, dark on the pill. No fade: the theme's 75 ms one, started while a
   card's view is hidden, would only run once it shows, flashing the old
   choice's colours. No minimum height, so a short selector stays short. */
.seg {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    text-shadow: none;
    transition: none;
    padding: 0;
    min-height: 0;
    color: @subtext1;
}
.seg:hover { color: @text; }
.seg.on, .seg.on .icon { color: @crust; }
/* Focused, the chosen one's ring goes on its pill (inset 3 px, radius 10),
   in base: at the cell's edge it would sit 1 px from the @accent pill and
   run into it. */
.seg.on:focus {
    outline-color: @base;
    outline-offset: -5px;
    -gtk-outline-radius: 10px;
}

/* Outlined: off, a ring and knob in overlay1, 3.4:1 on the card (the
   theme's grey track had 1.4:1); on, an @accent track with a knob in
   base, 8.1:1. No margin: the theme's 6 px made a title row with a switch
   taller than the others. */
switch {
    margin: 0;
    background-color: transparent;
    background-image: none;
    border: 2px solid @overlay1;
    border-radius: 10px;
    padding: 0;
    font-size: 0;
}
switch:checked { background-color: @accent; border-color: @accent; }
switch slider {
    background-color: @overlay1;
    background-image: none;
    border: none;
    box-shadow: none;
    border-radius: 50%;
    min-width: 14px;
    min-height: 14px;
    margin: 1px;
}
switch:checked slider { background-color: @base; }

/* The theme pads a fine-tuning scale (knob pressed and held) 1px less on
   each side, which made the whole card jump; pin one size for both. */
scale {
    padding: 17px 12px;
    min-height: 2px;
}
scale trough {
    background-color: @raised;
    background-image: none;
    border: none;
    border-radius: 3px;
    min-height: 6px;
}
scale highlight {
    background-color: @accent;
    background-image: none;
    border: none;
    border-radius: 3px;
}
scale slider {
    background-color: @rosewater;
    background-image: none;
    border: none;
    box-shadow: none;
    border-radius: 50%;
    min-width: 16px;
    min-height: 16px;
    margin: -6px;
}

/* Keyboard focus: an @accent ring, inset on buttons, round the track on a
   slider. GTK draws an outline only while the window shows focus, which
   a key press turns on and a click turns off again (bar-popup), so a
   slider that a click focused shows none. A switch outlines only its
   knob: its ring is a shadow outside the track, and only keys focus it. */
.card button:focus, .card scale:focus trough {
    outline: 2px solid @accent;
    outline-offset: -2px;
    -gtk-outline-radius: 12px;
}
.card scale:focus trough { outline-offset: 5px; -gtk-outline-radius: 10px; }
.card switch:focus { box-shadow: 0 0 0 2px @surface, 0 0 0 4px @accent; }

/* Tooltips as the cards and Waybar's: the theme's are near-black with a
   light theme's text colour. */
tooltip {
    background-color: @surface;
    border: 1px solid @raised;
    border-radius: 12px;
}
tooltip label { color: @text; font-size: 14px; }
"""


def run(*args):
    """stdout of a quick command, or "" if it fails."""
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def run_async(callback, *args):
    """Run a slow command without freezing the card;
    callback(ok, stdout, stderr) fires from the main loop."""
    try:
        proc = Gio.Subprocess.new(list(args),
                                  Gio.SubprocessFlags.STDOUT_PIPE
                                  | Gio.SubprocessFlags.STDERR_PIPE)
    except GLib.Error as err:
        callback(False, "", err.message)
        return

    def done(proc, result):
        try:
            _, out, err = proc.communicate_utf8_finish(result)
        except GLib.Error as e:
            callback(False, "", e.message)
            return
        callback(proc.get_successful(), out or "", err or "")
    proc.communicate_utf8_async(None, None, done)


def spawn(*args):
    """Start a program that outlives the popup."""
    subprocess.Popen(args, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def prop(proxy, name, default=None):
    """A D-Bus proxy's cached property, unpacked, or `default` while it has
    none (no proxy yet, the service gone, a property it lacks)."""
    value = proxy.get_cached_property(name) if proxy is not None else None
    return default if value is None else value.unpack()


def read_text(path, default=""):
    """A small file's content (a sysfs or /proc value), stripped, or
    `default` if it can't be read."""
    try:
        with open(path) as fh:
            return fh.read().strip()
    except (OSError, ValueError):
        return default


def rounded_rect(cr, x, y, w, h, r):
    """A rectangle with corners of radius r (at most half a side) as
    cairo's current path, to fill or stroke."""
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


# label -> (its restore timer, its own text, the flash) while it flashes
_COPIED = {}


def copy_flash(value, text, flash="Copied"):
    """Put `text` on the clipboard and show `flash` in the label `value`,
    with the class "copied", for COPIED_MS; then the label's own text
    again. Another copy meanwhile starts the time over; a text the card
    sets meanwhile stays."""
    # wl-copy forks to serve the clipboard, so it outlives the card.
    spawn("wl-copy", "--", text)
    timer, own, shown = _COPIED.pop(value, (None, None, None))
    if timer is not None:
        GLib.source_remove(timer)
    if value.get_text() != shown:  # not flashing, or the card wrote over it
        own = value.get_text()
    value.set_text(flash)
    value.get_style_context().add_class("copied")
    _COPIED[value] = (GLib.timeout_add(COPIED_MS, _uncopy, value, flash),
                      own, flash)


def _uncopy(value, flash):
    _timer, own, _flash = _COPIED.pop(value)
    value.get_style_context().remove_class("copied")
    if value.get_text() == flash:  # else the card has written over it
        value.set_text(own)
    return GLib.SOURCE_REMOVE


class Latest:
    """Writes of a value that changes faster than it can be written (a
    slider's level): one write at a time and none on the main loop; a value
    asked for meanwhile replaces any other waiting, so the last one always
    lands, also after the card has closed (the host stays). write(value,
    done) starts a write and calls done(ok) once it is over, from the main
    loop; Latest.command builds one that runs a command. landed(value, ok),
    if given, runs after each write: check the card's dead flag there
    before touching its widgets. While busy, a write is in flight, so a
    reading taken meanwhile may predate it."""

    def __init__(self, write, landed=None):
        self.write = write
        self.landed = landed
        self.value = None    # the last value asked for
        self.busy = False    # a write in flight
        self._again = False  # a newer value waits for it

    @classmethod
    def command(cls, argv, landed=None):
        """A Latest that runs the command argv(value) gives, with
        run_async; ok is its exit status."""
        return cls(lambda value, done: run_async(
            lambda ok, _out, _err: done(ok), *argv(value)), landed)

    def set(self, value):
        self.value = value
        if self.busy:
            self._again = True
        else:
            self._start()

    def _start(self):
        value, self.busy, self._again = self.value, True, False
        self.write(value, lambda ok: self._done(value, ok))

    def _done(self, value, ok):
        again = self._again and self.value != value
        self.busy = self._again = False
        if self.landed is not None:
            self.landed(value, ok)
        if again and not self.busy:
            self._start()


def label(text, *classes, xalign=0.0, ellipsize=None):
    lab = Gtk.Label(label=text, xalign=xalign)
    for cls in classes:
        lab.get_style_context().add_class(cls)
    if ellipsize is not None:
        lab.set_ellipsize(ellipsize)
    return lab


def hand(widget):
    """Hand pointer while over `widget`; GTK3 CSS has no `cursor`. Set on
    the toplevel, which the widget's own input window inherits it from."""
    def set_cursor(w, _event, name):
        win = w.get_toplevel().get_window()
        if win is not None:
            win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), name)
                           if name else None)
        return False
    widget.connect("enter-notify-event", set_cursor, "pointer")
    widget.connect("leave-notify-event", set_cursor, None)
    return widget


def button(child, *classes):
    """A flat button. Tab and the arrow keys reach it, Enter or Space
    presses it; a click presses it without taking the focus, so the
    pointer never leaves a focus ring behind."""
    btn = hand(Gtk.Button())
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_focus_on_click(False)
    for cls in classes:
        btn.get_style_context().add_class(cls)
    btn.add(child if isinstance(child, Gtk.Widget)
            else label(child, xalign=0.5))
    return btn


def tile(icon, name, sub=None, sub_bad=False):
    """Glyph, name and an optional status line under it."""
    end = Pango.EllipsizeMode.END
    box = Gtk.Box(spacing=12)
    glyph = label(icon, "icon", xalign=0.5)
    glyph.set_size_request(24, -1)
    box.pack_start(glyph, False, False, 0)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    text.set_valign(Gtk.Align.CENTER)
    text.pack_start(label(name, "name", ellipsize=end), False, False, 0)
    if sub:
        line = label(sub, "sub", ellipsize=end)
        if sub_bad:
            line.get_style_context().add_class("bad")
        text.pack_start(line, False, False, 0)
    box.pack_start(text, True, True, 0)
    return box


def row(icon, name, sub=None, on=False, sub_bad=False):
    """A clickable full-width list row; `on` highlights it."""
    btn = button(tile(icon, name, sub, sub_bad), "row")
    if on:
        btn.get_style_context().add_class("on")
    return btn


# A horizontal GtkScale takes ↑/↓ as well as ←/→ to change its value; in
# a card ↑/↓ go on to the row above or below, as from every other row.
VERTICAL = {Gdk.KEY_Up: Gtk.DirectionType.UP,
            Gdk.KEY_KP_Up: Gtk.DirectionType.UP,
            Gdk.KEY_Down: Gtk.DirectionType.DOWN,
            Gdk.KEY_KP_Down: Gtk.DirectionType.DOWN}


def slider(lo, hi, step=None):
    """A slider over lo..hi, in whole numbers, that ←/→, Page Up/Down and
    a wheel notch all move by `step`, 5 % of the range unless given. (GTK
    turns a wheel notch into a page, 10 steps by default.)"""
    step = step or (hi - lo) / 20
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
    scale.get_adjustment().set_step_increment(step)
    scale.get_adjustment().set_page_increment(step)
    scale.set_draw_value(False)
    scale.connect("key-press-event", _leave_vertically)
    return hand(scale)


def _leave_vertically(scale, event):
    direction = VERTICAL.get(event.keyval)
    if direction is None:
        return False
    scale.emit("move-focus", direction)
    return True


def slider_row(icon, name, scale, value):
    """Glyph, name, slider and its value, aligned with the switch rows."""
    box = Gtk.Box(spacing=12)
    box.get_style_context().add_class("slider")
    glyph = label(icon, "icon", xalign=0.5)
    glyph.set_size_request(24, -1)
    box.pack_start(glyph, False, False, 0)
    title = label(name, ellipsize=Pango.EllipsizeMode.END)
    # Nine characters wide whatever the name, so every slider starts at
    # the same x; a longer name (a DDC monitor's model) shortens, whole
    # in the tooltip.
    title.set_width_chars(9)
    title.set_max_width_chars(9)
    if len(name) > 9:
        title.set_tooltip_text(name)
    box.pack_start(title, False, False, 0)
    box.pack_start(scale, True, True, 0)
    value.set_width_chars(6)
    value.set_xalign(1.0)
    box.pack_start(value, False, False, 0)
    return box


def switch_row(icon, name, sub=True):
    """Glyph, name and a switch at the end, aligned with the slider and
    list rows; with `sub`, a status line under the name, for the card to
    fill. Gives (row, the line or None, the switch)."""
    box = Gtk.Box(spacing=12)
    box.get_style_context().add_class("static")
    glyph = label(icon, "icon", xalign=0.5)
    glyph.set_size_request(24, -1)
    box.pack_start(glyph, False, False, 0)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    text.set_valign(Gtk.Align.CENTER)
    text.pack_start(label(name, "name"), False, False, 0)
    line = label("", "sub") if sub else None
    if line:
        text.pack_start(line, False, False, 0)
    box.pack_start(text, True, True, 0)
    switch = hand(Gtk.Switch())
    switch.set_valign(Gtk.Align.CENTER)
    box.pack_end(switch, False, False, 0)
    return box, line, switch


class Arm:
    """The two clicks of a row that acts for good (power off, forget a
    device or a network): the first arms it, a second on the same row
    within `ms` acts. Arming one row drops another's arming, and so does
    disarm(), which the card calls when anything else is done.
    on_change(old, new) runs on each change of the armed row (rows go by
    keys of the card's choosing; None: none), so the card can restyle both;
    the timer ends with the card."""

    def __init__(self, card, on_change, ms=ARM_MS):
        self.key = None  # the armed row's
        self._on_change = on_change
        self._ms = ms
        self._timer = None
        card.connect("destroy", lambda *_: self._stop())

    def confirm(self, key):
        """A click on row `key`. True if it is the armed one: the card acts,
        and the row stays armed, without timing out, until disarm(). Else
        it arms the row and gives False."""
        self._stop()
        if key == self.key:
            return True
        old, self.key = self.key, key
        self._timer = GLib.timeout_add(self._ms, self._lapse)
        self._on_change(old, key)
        return False

    def disarm(self):
        self._stop()
        if self.key is not None:
            old, self.key = self.key, None
            self._on_change(old, None)

    def _lapse(self):
        self._timer = None
        self.disarm()
        return GLib.SOURCE_REMOVE

    def _stop(self):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None


class Segmented(Gtk.Box):
    """A few choices in one row: an accent pill on a dark track under the
    chosen one slides over when the choice changes. `choices` are (key,
    text) pairs, or (key, (glyph, text)) for a glyph over its text. Each
    choice is a button (class "seg"): Tab or ←/→ reach it, Enter or Space
    picks it. A pick calls on_pick(key), and the card moves the pill with
    select(), at once or once the change has taken; without on_pick, a
    pick moves the pill itself. The box draws the track and the pill under
    its buttons, so it takes their size wherever it is packed; `height` is
    the track's, px."""

    def __init__(self, choices, on_pick=None, height=38):
        super().__init__(homogeneous=True)
        self.keys = [key for key, _ in choices]
        self.index = None  # the chosen choice's; None: no pill
        self.pos = None    # where the pill is drawn, in choices
        self._slide_id = None
        on_pick = on_pick or self.select
        self.buttons = []
        for key, text in choices:
            if isinstance(text, tuple):
                glyph, text = text
                tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=2)
                tile.set_valign(Gtk.Align.CENTER)
                tile.pack_start(label(glyph, "icon", xalign=0.5),
                                False, False, 0)
                tile.pack_start(label(text, xalign=0.5), False, False, 0)
                btn = button(tile, "seg")
            else:
                btn = button(str(text), "seg")
            btn.connect("clicked", lambda _b, k=key: on_pick(k))
            self.pack_start(btn, True, True, 0)
            self.buttons.append(btn)
        self.set_size_request(-1, height)
        # Before the box's own handler, which draws the buttons on top.
        self.connect("draw", self._draw)

    @property
    def key(self):
        """The chosen choice's key, None for none."""
        return None if self.index is None else self.keys[self.index]

    def select(self, key, slide=True):
        """Put the pill under `key` (none for None), sliding it over from
        where it was."""
        index = self.keys.index(key) if key in self.keys else None
        if index == self.index:
            return  # there already, or on its way
        self.index = index
        for i, btn in enumerate(self.buttons):
            ctx = btn.get_style_context()
            (ctx.add_class if i == index else ctx.remove_class)("on")
        if self._slide_id is not None:
            self.remove_tick_callback(self._slide_id)
            self._slide_id = None
        if (not slide or index is None or self.pos is None
                or not self.get_mapped()):
            self.pos = index
            self.queue_draw()
            return
        start, t0 = self.pos, GLib.get_monotonic_time()

        def step(box, _clock):
            done = min(1.0, (GLib.get_monotonic_time() - t0) / SLIDE_US)
            self.pos = start + (index - start) * (1 - (1 - done) ** 3)
            box.queue_draw()
            if done < 1.0:
                return GLib.SOURCE_CONTINUE
            self._slide_id = None
            return GLib.SOURCE_REMOVE
        self._slide_id = self.add_tick_callback(step)

    def _draw(self, box, cr):
        w, h = box.get_allocated_width(), box.get_allocated_height()
        rounded_rect(cr, 0, 0, w, h, SEG_RADIUS)
        cr.set_source_rgb(*MOCHA["raised"])
        cr.fill()
        if self.pos is not None:
            seg = w / len(self.keys)
            rounded_rect(cr, self.pos * seg + SEG_PAD, SEG_PAD,
                         seg - 2 * SEG_PAD, h - 2 * SEG_PAD, SEG_RADIUS - 2)
            cr.set_source_rgb(*MOCHA["accent"])
            cr.fill()
        return False


class Panel(Gtk.Box):
    """A card: a title row with icon buttons, and a body that refresh()
    rebuilds. A card is built anew for each open and destroyed when it
    closes or another takes its place (dead turns True), so it starts
    from what is true now; the process stays, so a write or a call still
    under way finishes after that. Never block in __init__ or fill(): the
    card paints only once they return. Read what takes time (a command, a
    D-Bus call, a second sample) with run_async, an async call or
    GLib.timeout_add, and fill it in when it lands."""

    title = ""
    css = ""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.app = app
        # Async callbacks can land after the card was swapped out.
        self.dead = False
        self.connect("destroy", lambda *_: setattr(self, "dead", True))
        self.get_style_context().add_class("card")
        if self.css:
            self.get_style_context().add_class(self.css)
        self.set_size_request(CARD_W, -1)
        self.header = Gtk.Box(spacing=8)
        self.header.pack_start(label(self.title, "title"), True, True, 0)
        self.pack_start(self.header, False, False, 0)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pack_start(self.body, False, False, 0)
        self._refresh_id = None

    def do_get_preferred_width(self):
        # The card's window gives a card its natural width, and a label
        # that ellipsizes or wraps still asks for its whole text on one line
        # there, so a long SSID, device name or warning would widen the card
        # past CARD_W. Capped, such labels shorten or wrap instead.
        minimum, natural = Gtk.Box.do_get_preferred_width(self)
        return minimum, max(minimum, min(natural, CARD_W))

    def do_get_request_mode(self):
        # GTK caches a box's request mode when it is first shown: a card
        # that opened with no wrapping label stayed CONSTANT_SIZE, so one
        # added later (a weather warning, a long error) was measured as a
        # single line and the rows below it were cut off. A card is a
        # column at a fixed width, so height-for-width is always right.
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def fill(self):
        raise NotImplementedError

    def refresh(self):
        """Rebuild the body on idle, so a row can trigger it from its own
        click handler; repeated calls coalesce into one rebuild."""
        if self._refresh_id is None and not self.dead:
            self._refresh_id = GLib.idle_add(self._rebuild)

    def _rebuild(self):
        self._refresh_id = None
        if self.dead:  # swapped out or closed since refresh() queued this
            return GLib.SOURCE_REMOVE
        for child in self.body.get_children():
            child.destroy()
        self.fill()
        self.body.show_all()
        return GLib.SOURCE_REMOVE

    def header_button(self, glyph, tooltip, *cmd):
        """An icon button that launches `cmd`, left of what is already
        packed at the end of the title row."""
        btn = button(glyph, "iconbtn")
        btn.set_tooltip_text(tooltip)
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", self._launch, cmd)
        self.header.pack_end(btn, False, False, 0)
        return btn

    def _launch(self, _btn, cmd):
        spawn(*cmd)
        self.app.close()
