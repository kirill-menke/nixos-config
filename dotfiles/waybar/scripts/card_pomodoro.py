"""The Pomodoro card, `bar-popup pomodoro`: start a focus session, or see
the one that is on and pause, resume or stop it.

Idle, the card offers the focus length (30/45/60 min) and the break length
(10/15/20 min) as two segmented selectors, whose mauve pill slides to the
chosen one, and Start. While a timer runs or is paused, it shows the
phase's glyph (for focus the bar's pie, which empties by eighths), the time
left with a meter under it that drains by the second, and when the phase
ends (or that it is paused), with Pause (Resume) and Stop. Each button runs
`pomodoro-bar start FOCUS BREAK | toggle | stop`, which changes the timer
and wakes every bar, and closes the card: from there the bar shows the
timer. In both views, four dots at the title row's end count the sessions
of the set; the break after the fourth is the long one, and the line under
the time says so.

pomodoro-bar owns the timer. The card reads its state file as the bar
does, each time pomodoro-bar replaces it and at every whole second while
the card is open: it counts the seconds the bar rounds down to whole
minutes, and a pause, stop, phase end or scroll from the bar shows here as
it does there. A scroll (`pomodoro-bar add`) moves the time left, the end
and the meter, and the line under the time says by how much ("+5 min").
After a reboot, with no state file yet, the selectors start from the
lengths last started with, which pomodoro-bar keeps in
$XDG_STATE_HOME/pomodoro.json with the set's count. Both views are one
size, so whatever the timer does, the card keeps its height and the
buttons their place.
"""

import json
import math
import os
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
from cardkit import (MOCHA, Panel, Segmented, button, label,  # noqa: E402
                     rounded_rect)

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
STATE = os.path.join(RUNTIME, "pomodoro-state.json")
# pomodoro-bar's lengths last started with and the set's count, which
# outlast a reboot.
KEPT = os.path.join(os.environ.get("XDG_STATE_HOME")
                    or os.path.expanduser("~/.local/state"),
                    "pomodoro.json")
# pomodoro-bar's (see there): lengths in minutes, times in epoch seconds;
# "left" is what a paused phase has left and "length" the phase's whole
# length, in s; "done" counts the set's sessions done, and "since" is when
# the timer last went idle.
DEFAULTS = {"status": "idle", "phase": "work", "end": 0, "left": 0,
            "length": 0, "work": 60, "break": 10, "done": 0, "since": 0}
# pomodoro-bar's sets: SET sessions, the last one followed by a break LONG
# times the usual one, at most LONG_MAX minutes.
SET = 4
LONG = 3
LONG_MAX = 30

FOCUS_OPTS = (30, 45, 60)  # minutes
BREAK_OPTS = (10, 15, 20)

# The glyphs for a break and a pause, the bar's (pomodoro-bar), where focus
# counts down on a pie that empties by eighths.
ICON = {
    "break": "\uf0f4",   # nf-fa-coffee
    "paused": "\uf04c",  # nf-fa-pause
}
PIE = [chr(0xF0A9E + i) for i in range(8)]  # nf-md-circle_slice_1 .. _8

# Each phase in the bar's colour for it: the glyph's (CSS below) and the
# meter's.
COLOUR = {"work": "mauve", "break": "ok", "paused": "yellow"}
METER_H = 6   # px: the time-left meter, as thick as a slider's trough
DOT = 8       # px: a dot of the set, across
DOT_GAP = 6   # px between two

CSS = b"""
/* Pomodoro card. The selectors' minutes in bold, on cardkit's Segmented. */
.card.pomodoro .seg { font-weight: bold; }
/* Start and Pause/Resume in mauve, Stop in red. */
.card.pomodoro .action {
    background-color: @mauve;
    background-image: none;
    border: none;
    box-shadow: none;
    text-shadow: none;
    border-radius: 12px;
    padding: 9px;
    margin-top: 6px;
    color: @crust;
    font-weight: bold;
}
.card.pomodoro .action:hover { background-color: @lavender; }
.card.pomodoro .action.stop { background-color: @red; }
.card.pomodoro .action.stop:hover { background-color: @maroon; }
/* cardkit's focus ring is mauve, which these fills would hide. */
.card.pomodoro .action:focus { outline-color: @base; }
/* The timer, laid out as the Battery card's charge: the phase's glyph in
   the bar's colour for it, and the time left. */
.card.pomodoro .timer { padding: 0 10px; }
.card.pomodoro .gauge { font-size: 44px; }
.card.pomodoro .gauge.work { color: @mauve; }
.card.pomodoro .gauge.break { color: @ok; }
.card.pomodoro .gauge.paused { color: @yellow; }
.card.pomodoro .clock { color: @text; font-size: 32px; font-weight: bold; }
"""


def load():
    """The timer, read as pomodoro-bar reads it: defaults fill in missing
    keys, and no state file (or one that can't be read) is the idle timer,
    at the lengths last started with and the set's count."""
    state = dict(DEFAULTS)
    try:
        with open(STATE) as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        saved = None
    if isinstance(saved, dict):
        state.update(saved)
    else:
        state.update(kept())
    return state


def kept():
    """What pomodoro-bar keeps across reboots, read as it reads it: the
    lengths, and the set's "done" and "since" where they can be read; {}
    while there are no lengths to read."""
    try:
        with open(KEPT) as fh:
            saved = json.load(fh)
        work, brk = saved["work"], saved["break"]
    except (OSError, ValueError, TypeError, KeyError):
        return {}
    if not all(type(n) is int and n >= 0 for n in (work, brk)):
        return {}
    found = {"work": work, "break": brk}
    for key, most in (("done", SET), ("since", None)):
        n = saved.get(key)
        if type(n) is int and 0 <= n and (most is None or n <= most):
            found[key] = n
    return found


def mmss(seconds):
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# From here to pie(): pomodoro-bar's reading of the timer, the same here
# as there, so the card shows what the bar shows.
def span(state):
    """The phase's whole length in s, as a scroll has moved it."""
    return state["length"] or 60 * state[
        "work" if state["phase"] == "work" else "break"]


def long_break(state):
    """The break after a set's last session, in minutes."""
    return max(state["break"], min(LONG * state["break"], LONG_MAX))


def is_long(state):
    return state["phase"] == "break" and state["done"] >= SET


def set_done(state, now):
    """The sessions done in the set that a session started at `now` joins:
    none once the set is complete or the timer has stood idle longer than a
    long break."""
    if state["done"] >= SET or (
            state["status"] == "idle"
            and now - state["since"] > long_break(state) * 60):
        return 0
    return state["done"]


def pie(state, left):
    """The bar's pie with `left` s to go: the eighths left, off the minutes
    the bar shows."""
    eighths = -(-(left // 60) * 480 // max(60, span(state)))
    return PIE[min(max(eighths, 1), 8) - 1]


def moved(state):
    """What scrolls on the bar have added to the phase or taken from it, as
    "+5 min" or "-10 min", to the nearest minute; "" for nothing."""
    planned = 60 * (state["work"] if state["phase"] == "work"
                    else long_break(state) if is_long(state)
                    else state["break"])
    change = span(state) - planned
    minutes = (abs(change) + 30) // 60
    return f"{'+' if change > 0 else '-'}{minutes} min" if minutes else ""


def face(state, now):
    """What the timer view shows at `now`, in whole seconds, as the bar
    counts them (it shows whole minutes): (the glyph's class, the glyph,
    the time left, the line under it, the toggle's label, the share of the
    phase left), or None for the idle timer. As on the bar, a phase other
    than focus is a break."""
    work = state["phase"] == "work"
    name = "Focus" if work else "Long break" if is_long(state) else "Break"
    whole = max(1, span(state))
    if state["status"] == "paused":
        left = state["left"]
        line = " · ".join(filter(None, (name, moved(state), "paused")))
        return ("paused", ICON["paused"], mmss(left), line, "Resume",
                min(1.0, left / whole))
    if state["status"] != "running":
        return None
    # 00:00 once the phase has run out, until a bar moves the timer on.
    left = max(0, state["end"] - now)
    end = time.strftime("%H:%M", time.localtime(state["end"]))
    line = " · ".join(filter(None, (name, moved(state), f"ends {end}")))
    if work:
        return ("work", pie(state, left), mmss(left), line, "Pause",
                min(1.0, left / whole))
    return ("break", ICON["break"], mmss(left), line, "Pause",
            min(1.0, left / whole))


def sessions(state, now):
    """The set's dots at `now`: (sessions done, whether one is under way,
    their tooltip)."""
    if state["status"] == "idle":
        done, under_way = set_done(state, now), False
    else:
        done, under_way = state["done"], state["phase"] == "work"
    long = long_break(state)
    if done >= SET:
        tip = f"Set: all {SET} sessions done · a {long} min break"
    else:
        tip = (f"Set: {done} of {SET} sessions done · a {long} min break "
               "after the last")
    return done, under_way, tip


class PomodoroPanel(Panel):
    """The selectors and Start while the timer is idle; the phase, the time
    left and its meter, the phase's end, Pause (Resume) and Stop while it
    runs or is paused; the set's dots in the title row."""

    title = "Pomodoro"
    css = "pomodoro"

    def __init__(self, app):
        super().__init__(app)
        # Drawn, so they keep to the title row's height: the sessions done
        # filled, the one under way a mauve ring, those to come rings in
        # overlay1, as an off switch's.
        self.sessions = Gtk.DrawingArea()
        self.sessions.set_size_request(SET * DOT + (SET - 1) * DOT_GAP,
                                       DOT + 2)
        self.sessions.set_valign(Gtk.Align.CENTER)
        self.sessions.connect("draw", self._draw_sessions)
        self.header.pack_end(self.sessions, False, False, 0)
        self.sessions_shown = None  # sessions() on show
        self.fill()
        # pomodoro-bar replaces the state file on every change, which
        # inotify reports at once: a pause, stop, phase end or scroll from
        # the bar shows here as it shows there.
        self.monitor = Gio.File.new_for_path(STATE).monitor_file(
            Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", lambda *_: self._show())
        self._tick()
        self.connect("destroy", self._closed)

    def _closed(self, _card):
        GLib.source_remove(self._tick_id)
        self.monitor.cancel()

    def fill(self):
        # Both views in one stack, which is as tall as the taller one: the
        # card doesn't change size when the timer starts or stops.
        self.stack = Gtk.Stack()
        self.stack.add_named(self._idle_view(), "idle")
        self.stack.add_named(self._timer_view(), "timer")
        self.stack.show_all()  # it can only switch to a child that shows
        self.body.pack_start(self.stack, False, False, 0)
        self.shown = None  # the face on show; None: the idle view
        self.key, self.fraction = "work", 0.0  # what the meter shows
        self._preset(load())

    def _idle_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(label("Focus", "seclabel"), False, False, 0)
        self.focus = Segmented([(m, str(m)) for m in FOCUS_OPTS])
        box.pack_start(self.focus, False, False, 0)
        box.pack_start(label("Break", "seclabel"), False, False, 0)
        self.brk = Segmented([(m, str(m)) for m in BREAK_OPTS])
        box.pack_start(self.brk, False, False, 0)
        start = button("Start", "action")
        start.connect("clicked", self._start)
        box.pack_end(start, False, False, 0)
        return box

    def _timer_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        # The time and its meter, in the middle of the room the selectors
        # take in the idle view, which keeps the card at its height.
        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        middle.set_valign(Gtk.Align.CENTER)
        info = Gtk.Box(spacing=14)
        info.get_style_context().add_class("timer")
        self.gauge = label("", "gauge", xalign=0.5)
        # As wide as the widest glyph (the cup, 46 px), so the time beside
        # it stays put from focus to break to pause.
        self.gauge.set_size_request(48, -1)
        info.pack_start(self.gauge, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        self.clock = label("", "clock")
        text.pack_start(self.clock, False, False, 0)
        self.when = label("", "sub")
        text.pack_start(self.when, False, False, 0)
        info.pack_start(text, True, True, 0)
        middle.pack_start(info, False, False, 0)
        # The share of the phase left, draining by the second where the pie
        # goes by eighths; in line with the glyph and the time (.timer's
        # 10 px).
        self.meter = Gtk.DrawingArea()
        self.meter.set_size_request(-1, METER_H)
        self.meter.set_margin_start(10)
        self.meter.set_margin_end(10)
        self.meter.connect("draw", self._draw_meter)
        middle.pack_start(self.meter, False, False, 0)
        box.pack_start(middle, True, True, 0)
        # Side by side, each half the width: Pause and Resume take the
        # same room.
        actions = Gtk.Box(spacing=8, homogeneous=True)
        self.toggle = button("Pause", "action")
        self.toggle.connect("clicked", self._launch,
                            ("pomodoro-bar", "toggle"))
        actions.pack_start(self.toggle, True, True, 0)
        stop = button("Stop", "action", "stop")
        stop.connect("clicked", self._launch, ("pomodoro-bar", "stop"))
        actions.pack_start(stop, True, True, 0)
        box.pack_end(actions, False, False, 0)
        return box

    def _preset(self, state):
        """The selectors at the last session's lengths, which the state
        keeps (or, after a reboot, pomodoro-bar's pomodoro.json), or at
        pomodoro-bar's defaults."""
        for selector, key in ((self.focus, "work"), (self.brk, "break")):
            minutes = state[key]
            if minutes not in selector.keys:
                minutes = DEFAULTS[key]
            selector.select(minutes, slide=False)

    def _tick(self):
        """Show the timer now and again at the next whole second, when the
        countdown turns (the bar counts the same seconds, in whole minutes)."""
        self._tick_id = GLib.timeout_add(
            1000 - int(time.time() * 1000) % 1000, self._tick)
        self._show()
        return GLib.SOURCE_REMOVE

    def _show(self):
        """The timer as the state file has it, at this second."""
        state = load()
        now = int(time.time())
        dot = sessions(state, now)
        if dot != self.sessions_shown:
            self.sessions.set_tooltip_text(dot[2])
            self.sessions.queue_draw()
            self.sessions_shown = dot
        shown = face(state, now)
        if shown == self.shown:
            return
        if shown is None:
            # Stopped or over: the lengths it ran with, to go again.
            self._preset(state)
            self.stack.set_visible_child_name("idle")
        else:
            self.key, glyph, left, line, toggle, self.fraction = shown
            self.gauge.set_text(glyph)
            ctx = self.gauge.get_style_context()
            for cls in COLOUR:
                if cls == self.key:
                    ctx.add_class(cls)
                else:
                    ctx.remove_class(cls)
            self.clock.set_text(left)
            self.when.set_text(line)
            self.toggle.get_child().set_text(toggle)
            self.meter.queue_draw()
            self.stack.set_visible_child_name("timer")
        self.shown = shown

    def _draw_meter(self, area, cr):
        """The share of the phase left, from the left end, in the phase's
        colour on the selectors' track colour; the last of it a dot."""
        w, h = area.get_allocated_width(), area.get_allocated_height()
        rounded_rect(cr, 0, 0, w, h, h / 2)
        cr.set_source_rgb(*MOCHA["raised"])
        cr.fill()
        if self.fraction > 0:
            rounded_rect(cr, 0, 0, max(h, w * self.fraction), h, h / 2)
            cr.set_source_rgb(*MOCHA[COLOUR[self.key]])
            cr.fill()
        return False

    def _draw_sessions(self, area, cr):
        """The set's dots, as sessions() gave them last."""
        if self.sessions_shown is None:
            return False
        done, under_way, _tip = self.sessions_shown
        h = area.get_allocated_height()
        r = DOT / 2
        for i in range(SET):
            x = r + i * (DOT + DOT_GAP)
            cr.new_sub_path()
            if i < done:
                cr.arc(x, h / 2, r, 0, 2 * math.pi)
                cr.set_source_rgb(*MOCHA["mauve"])
                cr.fill()
            else:
                # A 1.5 px ring just inside the dot's edge.
                cr.set_line_width(1.5)
                cr.arc(x, h / 2, r - 0.75, 0, 2 * math.pi)
                cr.set_source_rgb(*MOCHA["mauve" if i == done and under_way
                                         else "overlay1"])
                cr.stroke()
        return False

    def _start(self, btn):
        self._launch(btn, ("pomodoro-bar", "start", str(self.focus.key),
                           str(self.brk.key)))
