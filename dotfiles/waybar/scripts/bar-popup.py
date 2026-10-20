#!/usr/bin/env python3
"""Catppuccin popup cards for the waybar modules.

`bar-popup <name>` opens the card PANELS has under that name, just under
the bar and centred on the click; `bar-popup <name> --key`, which the key
bindings run, opens it at the bar's right end, as the pointer may be
anywhere then. Asked for the card that is open, it closes it (a second
click on the same module); for another card it swaps the card.
`bar-popup close` closes any card: the bell runs it as it opens swaync's
panel, and opening a card closes that panel, so one of them shows at a
time. Tab, Shift+Tab and the arrow keys move between the card's controls,
Enter or Space works the one with the focus ring, and Esc or a click
anywhere else, on any monitor, closes the card.

One process serves all the cards, and it stays once started (a user
service starts it with the session): the `bar-popup` command is a shell
script, bar-popup.sh, that hands the request over with busctl in a few ms,
where starting Python for that took ~0.5 s a click. Only while none runs
does the script start this file, installed as bar-popup-host, which then
opens the card from its own command line.

This file is the windows and the card registry. Each card is a module of
its own, card_<name>.py: a Panel subclass, its own CSS (bytes) and what
only it needs; cardkit.py has what they share. A new card takes one
import and one PANELS entry here: the entry's key is its bar-popup
argument, and its CSS joins the stylesheet by itself.
"""

import json
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
import cardkit  # noqa: E402
import card_attention  # noqa: E402
import card_battery  # noqa: E402
import card_bluetooth  # noqa: E402
import card_calendar  # noqa: E402
import card_display  # noqa: E402
import card_network  # noqa: E402
import card_pomodoro  # noqa: E402
import card_power  # noqa: E402
import card_sound  # noqa: E402
import card_system  # noqa: E402
import card_weather  # noqa: E402
import osd  # noqa: E402
from cardkit import CARD_TOP, CARD_W, EDGE, run, run_async  # noqa: E402

PANELS = {
    "network": card_network.NetworkPanel,
    "bluetooth": card_bluetooth.BluetoothPanel,
    "sound": card_sound.SoundPanel,
    "display": card_display.DisplayPanel,
    "weather": card_weather.WeatherPanel,
    "calendar": card_calendar.CalendarPanel,
    "pomodoro": card_pomodoro.PomodoroPanel,
    "power": card_power.PowerPanel,
    "battery": card_battery.BatteryPanel,
    "system": card_system.SystemPanel,
    "attention": card_attention.AttentionPanel,
}

# The layer namespaces Hyprland's layer rules match (hyprland.lua). The
# card's surface is kept out of screen shares and screenshots, which paint
# such a surface black over its whole size: that is why the card has a
# surface of its own, apart from the transparent ones over each monitor
# that catch the clicks beside it.
NAMESPACE = "bar-popup"
BACKDROP = "bar-popup-backdrop"

# The base stylesheet, then each card's own in PANELS order: of two rules
# that match alike, the later one wins, so a card can restyle the base.
CSS = cardkit.CSS + b"".join(
    sys.modules[panel.__module__].CSS for panel in PANELS.values())


def cursor():
    """Global cursor position from Hyprland: a Wayland client cannot read
    the pointer before it has a surface under it. Its socket answers in
    ~0.1 ms; hyprctl, ~30 ms to start, only where the socket can't be
    reached."""
    pos = osd.hyprland("j/cursorpos")
    try:
        if pos is None:
            pos = json.loads(run("hyprctl", "-j", "cursorpos"))
        return pos["x"], pos["y"]
    except (ValueError, KeyError, TypeError):
        return None


def monitor_at(point):
    display = Gdk.Display.get_default()
    if point is not None:
        for i in range(display.get_n_monitors()):
            mon = display.get_monitor(i)
            geo = mon.get_geometry()
            if (geo.x <= point[0] < geo.x + geo.width
                    and geo.y <= point[1] < geo.y + geo.height):
                return mon
    return display.get_primary_monitor() or display.get_monitor(0)


def focusable(widget):
    """The controls in `widget` that the keyboard can reach now, in the
    order of the widget tree."""
    found = []
    if (widget.get_can_focus() and widget.get_mapped()
            and widget.is_sensitive()):
        found.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found += focusable(child)
    return found


def names(widget):
    """The name lines (cardkit.tile's .name labels) in `widget`."""
    if isinstance(widget, Gtk.Label):
        return ([widget.get_text()]
                if widget.get_style_context().has_class("name") else [])
    if isinstance(widget, Gtk.Container):
        return [n for child in widget.get_children() for n in names(child)]
    return []


def focus_key(widget):
    """What tells a control again once its card has rebuilt it: its kind
    and the name its row shows. A list row has a name line of its own; a
    control beside one (a network's Forget, a switch) goes by its tooltip
    and the name line of the row it sits in."""
    kind = type(widget).__name__
    own = names(widget)
    if own:
        return kind, tuple(own)
    return kind, widget.get_tooltip_text(), tuple(names(widget.get_parent()))


class PopupApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.kirill.barpopup",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None      # the card's window
        self.name = None     # its PANELS key
        self.backdrops = []  # the click catchers, one per monitor
        self.press = None    # the card window's pointer-press gesture
        self.focused = None  # (focus_key, place) of the focused control
        # What bar-popup.sh calls once this process runs, through
        # org.freedesktop.Application.ActivateAction: "toggle" with a
        # card's name for a click, "key" for a key binding, and "close".
        toggle = Gio.SimpleAction.new("toggle", GLib.VariantType.new("s"))
        toggle.connect("activate",
                       lambda _action, name: self.toggle(name.get_string()))
        key = Gio.SimpleAction.new("key", GLib.VariantType.new("s"))
        key.connect("activate", lambda _action, name: self.toggle(
            name.get_string(), key=True))
        close = Gio.SimpleAction.new("close", None)
        close.connect("activate", lambda *_: self.close())
        for action in (toggle, key, close):
            self.add_action(action)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # Stay without a window, so the next click finds Python, GTK and
        # the cards loaded: an application with no window and no hold
        # quits.
        self.hold()

    def do_command_line(self, cmdline):
        """bar-popup-host's own arguments: those bar-popup.sh passes on
        while no host runs, or a second host's, which GApplication hands
        over to this one."""
        args = cmdline.get_arguments()[1:]
        if args == ["close"]:
            self.close()
            return 0
        if not args or args[0] not in PANELS or args[1:] not in ([],
                                                                 ["--key"]):
            # printerr() is printf-style, so it has no Python binding; the
            # literal one (GLib 2.80) reaches the stderr of the process that
            # ran the command, which usually isn't this one.
            cmdline.printerr_literal(
                f"usage: bar-popup {'|'.join(PANELS)} [--key] | close\n")
            return 2
        self.toggle(args[0], key=args[1:] == ["--key"])
        return 0

    def toggle(self, name, key=False):
        """A click on the module `name` (key: its key binding): its card
        opens, or closes if it is the one open; another card makes way."""
        if name not in PANELS:
            print(f"bar-popup: no card {name!r}", file=sys.stderr)
        elif self.win is not None and name == self.name:
            self.close()  # second click on the same module
        else:
            self.open(name, key)

    def open(self, name, key=False):
        # Where the pointer is now, before anything else takes time.
        point = cursor()
        # One popup at a time: swaync's panel goes (a no-op while closed).
        run_async(lambda *_: None,
                  "swaync-client", "--close-panel", "--skip-wait")
        monitor = monitor_at(point)
        geo = monitor.get_geometry()

        card = PANELS[name](self)
        win = self._layer(monitor, NAMESPACE)
        # Hyprland hands an on-demand layer surface the keyboard focus as
        # it maps, so a card opened by a click or a key binding takes keys
        # at once.
        GtkLayerShell.set_keyboard_mode(
            win, GtkLayerShell.KeyboardMode.ON_DEMAND)
        # Centre the card on the click, kept inside the monitor, or, opened
        # by a key, put it at the bar's right end. Anchored to the top
        # left with no exclusive zone of its own, the surface sits in the
        # space under the bar, and the margins place it there.
        x = (point[0] - geo.x if point and not key
             else geo.width) - CARD_W // 2
        GtkLayerShell.set_anchor(win, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(win, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_margin(win, GtkLayerShell.Edge.TOP, CARD_TOP)
        GtkLayerShell.set_margin(win, GtkLayerShell.Edge.LEFT,
                                 max(EDGE, min(x, geo.width - CARD_W - EDGE)))
        # As big as the card, also when a rebuild makes it shorter: a
        # resizable window only ever grows.
        win.set_resizable(False)
        win.add(card)
        win.connect("key-press-event", self._on_key)
        # The backdrops first, so the card's surface goes over them; a
        # swap keeps them.
        if not self.backdrops:
            self._backdrops()
        win.show_all()
        self._keyboard(win, card)

        # The new card first, then the old one goes: no frame without a
        # card in between.
        old, self.win, self.name = self.win, win, name
        if old is not None:
            old.destroy()

    def _layer(self, monitor, namespace):
        """A transparent window on the overlay layer of `monitor`."""
        win = Gtk.Window(application=self)
        win.set_app_paintable(True)
        visual = win.get_screen().get_rgba_visual()
        if visual is not None:
            win.set_visual(visual)
        GtkLayerShell.init_for_window(win)
        GtkLayerShell.set_namespace(win, namespace)
        GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_monitor(win, monitor)
        return win

    def _backdrops(self):
        """A window over each monitor that closes the card on a press, so
        a click anywhere but on the card closes it, on any monitor. Each
        is anchored to every edge but has no exclusive zone of its own, so
        it fills the output *below* the bar: the bar stays clickable,
        which is what lets a second click toggle or swap cards. No
        keyboard: the card keeps it."""
        display = Gdk.Display.get_default()
        for i in range(display.get_n_monitors()):
            win = self._layer(display.get_monitor(i), BACKDROP)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(win, edge, True)
            catcher = Gtk.EventBox()
            catcher.get_style_context().add_class("backdrop")
            catcher.connect("button-press-event", lambda *_: self.close())
            win.add(catcher)
            win.show_all()
            self.backdrops.append(win)

    def close(self):
        if self.win is not None:
            win, self.win, self.name = self.win, None, None
            win.destroy()
        for backdrop in self.backdrops:
            backdrop.destroy()
        self.backdrops = []

    def _on_key(self, _win, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _keyboard(self, win, card):
        """GTK gives the first control the focus as the window shows; take
        it back, so that the first key says where to start (Tab: the first
        control, Shift+Tab the last, ↓ the top one, ↑ the bottom one) and a
        stray Enter does nothing. GTK shows the focus ring from the first
        key press on; a press of the pointer hides it again and drops the
        focus, so the ring never stays on one control while the pointer
        works another. (A slider still takes the focus when clicked,
        without a ring, so the arrow keys go on from there.)"""
        win.set_focus(None)
        self.focused = None
        # In the capture phase the window sees a press before the control
        # under the pointer does; claiming nothing, it leaves it to that
        # control. A GTK 3 widget holds no reference to its gestures.
        self.press = Gtk.GestureMultiPress.new(win)
        self.press.set_button(0)  # any button
        self.press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.press.connect("pressed", self._on_press)
        win.connect("set-focus", self._on_set_focus, card)

    @staticmethod
    def _on_press(gesture, _n, _x, _y):
        win = gesture.get_widget()
        win.set_focus_visible(False)
        win.set_focus(None)

    def _on_set_focus(self, win, widget, card):
        """Keep track of the focus. A card that rebuilds a list, or hides a
        button, takes the focused control with it; GTK then leaves nothing
        focused, and _refocus finds the place again."""
        if widget is not None:
            chain = focusable(card)
            self.focused = ((focus_key(widget), chain.index(widget))
                            if widget in chain else None)
        elif win.get_focus() is not None:  # still the one losing it
            GLib.idle_add(self._refocus, win, card, win.get_focus())

    def _refocus(self, win, card, old):
        """After the rebuild, while the keyboard is in use: the control
        with the same key (the nearest, of several), else the one now at
        the old place."""
        if (not card.dead and win.get_focus() is None
                and win.get_focus_visible() and self.focused is not None
                and not (old.is_ancestor(card) and old.get_mapped())):
            key, at = self.focused
            chain = focusable(card)
            same = [i for i, w in enumerate(chain) if focus_key(w) == key]
            if same:
                chain[min(same, key=lambda i: abs(i - at))].grab_focus()
            elif chain:
                chain[min(at, len(chain) - 1)].grab_focus()
        return GLib.SOURCE_REMOVE


def main():
    return PopupApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
