#!/usr/bin/env python3
"""Catppuccin Pomodoro popup for waybar.

A gtk-layer-shell overlay anchored under the waybar clock. It offers two
segmented "tab selectors" — focus length (30/45/60) and break length
(10/15/20) — whose accent pill slides smoothly to the chosen option, plus a
Start button that writes the timer state file and collapses the popup.

Run as a single-instance Gtk.Application: launching it again toggles it shut,
so the waybar module can simply re-run `pomodoro-popup` on every click.
"""

import json
import math
import os
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

STATE = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "pomodoro-state.json"
)

FOCUS_OPTS = [30, 45, 60]
BREAK_OPTS = [10, 15, 20]
DEFAULT_FOCUS = 60
DEFAULT_BREAK = 10

SEG_W = 66          # width of one segment, px
SEG_H = 38          # height of the segmented control, px
RADIUS = 12         # corner radius of the track, px
ANIM_US = 220000.0  # slide duration, microseconds

# Catppuccin Mocha, as 0..1 RGB triples for cairo.
SURFACE1 = (0x45 / 255, 0x47 / 255, 0x5A / 255)
MAUVE = (0xCB / 255, 0xA6 / 255, 0xF7 / 255)

CSS = b"""
window { background-color: transparent; }

.card {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 18px;
    padding: 18px 20px;
}

.title {
    color: #cdd6f4;
    font-weight: bold;
    font-size: 15px;
}

.seclabel {
    color: #a6adc8;
    font-size: 12px;
    margin-top: 4px;
}

.seg-btn {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    outline: none;
    color: #bac2de;
    font-weight: bold;
    padding: 0;
    min-height: 38px;
    text-shadow: none;
}

.seg-btn:hover { background: transparent; }
.seg-btn.on { color: #11111b; }

.startbtn {
    background-color: #cba6f7;
    background-image: none;
    color: #11111b;
    font-weight: bold;
    border: none;
    box-shadow: none;
    border-radius: 12px;
    padding: 9px;
    margin-top: 6px;
}

.startbtn:hover { background-color: #b4befe; }

.stopbtn {
    background-color: #f38ba8;
    background-image: none;
    color: #11111b;
    font-weight: bold;
    border: none;
    box-shadow: none;
    border-radius: 12px;
    padding: 9px;
    margin-top: 6px;
}

.stopbtn:hover { background-color: #eba0ac; }

.backdrop { background-color: transparent; }
"""


def ease_out_cubic(t: float) -> float:
    return 1 - pow(1 - t, 3)


def rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Segmented(Gtk.Overlay):
    """A segmented control with a pill that slides between options."""

    def __init__(self, options, default):
        super().__init__()
        self.options = options
        self.index = options.index(default)
        self.pos = float(self.index)      # animated position, in segment units
        self._anim_id = None

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(SEG_W * len(options), SEG_H)
        self.area.connect("draw", self._on_draw)
        self.add(self.area)

        box = Gtk.Box(homogeneous=True)
        self.buttons = []
        for i, opt in enumerate(options):
            btn = Gtk.Button(label=str(opt))
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_can_focus(False)
            btn.get_style_context().add_class("seg-btn")
            btn.connect("clicked", self._on_click, i)
            box.pack_start(btn, True, True, 0)
            self.buttons.append(btn)
        self.add_overlay(box)
        self._refresh_classes()

    def value(self):
        return self.options[self.index]

    def _on_click(self, _btn, i):
        if i == self.index:
            return
        self.index = i
        self._refresh_classes()
        self._animate_to(float(i))

    def _refresh_classes(self):
        for j, btn in enumerate(self.buttons):
            ctx = btn.get_style_context()
            if j == self.index:
                ctx.add_class("on")
            else:
                ctx.remove_class("on")

    def _animate_to(self, target):
        start = self.pos
        t0 = GLib.get_monotonic_time()
        if self._anim_id is not None:
            self.area.remove_tick_callback(self._anim_id)
            self._anim_id = None

        def tick(_area, _clock):
            frac = (GLib.get_monotonic_time() - t0) / ANIM_US
            if frac >= 1.0:
                self.pos = target
                self.area.queue_draw()
                self._anim_id = None
                return GLib.SOURCE_REMOVE
            self.pos = start + (target - start) * ease_out_cubic(frac)
            self.area.queue_draw()
            return GLib.SOURCE_CONTINUE

        self._anim_id = self.area.add_tick_callback(tick)

    def _on_draw(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        seg = w / len(self.options)

        rounded_rect(cr, 0, 0, w, h, RADIUS)
        cr.set_source_rgb(*SURFACE1)
        cr.fill()

        pad = 3
        x = self.pos * seg
        rounded_rect(cr, x + pad, pad, seg - 2 * pad, h - 2 * pad, RADIUS - 2)
        cr.set_source_rgb(*MAUVE)
        cr.fill()
        return False


class PomodoroApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.kirill.pomodoro")
        self.win = None
        self.focus = None
        self.brk = None
        self._mon_w = 1920   # fallback monitor size, overwritten on build
        self._mon_h = 1080

    @staticmethod
    def _pointer_monitor():
        display = Gdk.Display.get_default()
        try:
            _screen, x, y = display.get_default_seat().get_pointer().get_position()
            mon = display.get_monitor_at_point(x, y)
            if mon is not None:
                return mon
        except Exception:
            pass
        return display.get_primary_monitor() or display.get_monitor(0)

    def do_activate(self):
        # Second launch while open -> toggle shut.
        if self.win is not None:
            self.win.destroy()
            return
        self._build_window()

    def _build_window(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        win = Gtk.Window(application=self)
        self.win = win
        win.set_app_paintable(True)
        screen = win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            win.set_visual(visual)

        GtkLayerShell.init_for_window(win)
        GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)

        # Pin the popup to the monitor the pointer is on (the one whose bar
        # was just clicked) and remember its geometry so the transparent
        # backdrop can be sized to fill it.
        monitor = self._pointer_monitor()
        if monitor is not None:
            GtkLayerShell.set_monitor(win, monitor)
            geo = monitor.get_geometry()
            self._mon_w, self._mon_h = geo.width, geo.height

        # Fill the whole output with a transparent backdrop so that a click
        # anywhere outside the card dismisses the popup.
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(win, edge, True)
        GtkLayerShell.set_keyboard_mode(
            win, GtkLayerShell.KeyboardMode.ON_DEMAND
        )

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("card")

        title = Gtk.Label(label="Pomodoro", xalign=0)
        title.get_style_context().add_class("title")
        card.pack_start(title, False, False, 0)

        if self._is_running():
            # A timer is already running: offer only a Stop button.
            stop = Gtk.Button(label="Stop")
            stop.get_style_context().add_class("stopbtn")
            stop.set_can_focus(False)
            stop.connect("clicked", self._on_stop)
            card.pack_start(stop, False, False, 0)
        else:
            card.pack_start(self._section("Focus"), False, False, 0)
            self.focus = Segmented(FOCUS_OPTS, DEFAULT_FOCUS)
            card.pack_start(self.focus, False, False, 0)

            card.pack_start(self._section("Break"), False, False, 0)
            self.brk = Segmented(BREAK_OPTS, DEFAULT_BREAK)
            card.pack_start(self.brk, False, False, 0)

            start = Gtk.Button(label="Start")
            start.get_style_context().add_class("startbtn")
            start.set_can_focus(False)
            start.connect("clicked", self._on_start)
            card.pack_start(start, False, False, 0)

        # The card sits top-right, just under the bar. Wrapping it in an
        # EventBox that swallows button presses stops clicks on the card
        # (its padding/title) from falling through to the backdrop.
        card_wrap = Gtk.EventBox()
        card_wrap.set_halign(Gtk.Align.END)
        card_wrap.set_valign(Gtk.Align.START)
        card_wrap.set_margin_top(42)
        card_wrap.set_margin_end(40)
        card_wrap.connect("button-press-event", lambda *_: True)
        card_wrap.add(card)

        backdrop = Gtk.EventBox()
        backdrop.get_style_context().add_class("backdrop")
        backdrop.connect("button-press-event", self._on_backdrop)
        # Give the backdrop the monitor's size so the window has a concrete
        # allocation and actually maps (an empty EventBox is 0x0).
        backdrop.set_size_request(self._mon_w, self._mon_h)

        overlay = Gtk.Overlay()
        overlay.add(backdrop)
        overlay.add_overlay(card_wrap)

        win.add(overlay)
        win.connect("key-press-event", self._on_key)
        win.connect("destroy", self._on_destroy)
        win.show_all()

    def _section(self, text):
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class("seclabel")
        return label

    def _on_key(self, _win, event):
        if event.keyval == Gdk.KEY_Escape:
            self.win.destroy()
            return True
        return False

    def _on_backdrop(self, _widget, _event):
        # Click outside the card -> dismiss.
        self.win.destroy()
        return True

    def _on_destroy(self, _win):
        self.win = None
        self.quit()

    def _is_running(self):
        try:
            with open(STATE) as fh:
                return json.load(fh).get("status") == "running"
        except (OSError, ValueError):
            return False

    def _on_stop(self, _btn):
        try:
            os.remove(STATE)
        except OSError:
            pass
        self.win.destroy()

    def _on_start(self, _btn):
        now = int(time.time())
        state = {
            "status": "running",
            "phase": "work",
            "end": now + self.focus.value() * 60,
            "work": self.focus.value(),
            "break": self.brk.value(),
        }
        tmp = STATE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE)
        self.win.destroy()


def main():
    app = PomodoroApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
