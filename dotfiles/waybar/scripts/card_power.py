"""The Power card, `bar-popup power`: lock, suspend, log out, restart and
shut down. The bar's power module opens it, and so do SUPER+Escape and
the power key (hyprland.lua, with --key); it sits at the bar's right end
either way.

Log out, Restart and Shut down end the session, so they arm on the first
click (the row turns red and says so) and act on a second one within
cardkit's ARM_MS. The keyboard works it like every card: Tab or the arrow
keys move between the rows, Enter or Space acts like a click, and Esc
closes it (bar-popup). Each row also answers to the letter at its right
end, so SUPER+Escape, then S suspends and R, R restarts, without aiming.

Those three hand the session to hyprshutdown, which asks every window to
close as its close button would, so editors can ask to save and browsers
keep their session. It lists what is still open, with Force quit and
Cancel, and only then ends Hyprland and restarts or powers off.

The line under a name says what the action would run into, read as the
card opens: an app holding off the automatic lock (hypridle skips all its
timeouts while any idle lock is held), one blocking a suspend or a
shutdown, and a reboot that waits (the attention module's check). logind
refuses a blocked suspend or shutdown outright, and with no polkit agent
nothing here could override it, so a blocked row does nothing but look
again. The title row says how long the system has been up.

The chevron at the title row's end opens two restarts for one boot only:
into the firmware's setup, where the BIOS settings are, and into the
generation before the one a restart boots (bootctl lists the boot menu
when the chevron first opens). While it is open they answer to F and P,
arm, and hand over to hyprshutdown as Restart does. Its last step asks
logind for the one boot, with `systemctl reboot --firmware-setup` or
`--boot-loader-entry=`, which call logind's SetRebootToFirmwareSetup or
SetRebootToBootLoaderEntry, open to the active session without a
password. Asked any earlier, a Cancel in hyprshutdown would leave that
boot waiting for some later restart. The chevron shows only where logind
says it can do at least one of them, so not without EFI or systemd-boot.
Both stay hidden because the boot menu offers them for 5 s on every boot
anyway.
"""

import datetime
import json
import re
import shlex
import time
from collections import namedtuple

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
from attention import LABEL, pending_reboot  # noqa: E402
from cardkit import (Arm, Panel, button, label, read_text,  # noqa: E402
                     run_async, spawn)

# Nerd Font glyphs (Material Design icons).
ICON = {
    "lock": "\U000f033e",
    "suspend": "\U000f0904",
    "logout": "\U000f0343",
    "restart": "\U000f0709",
    "poweroff": "\U000f0425",
    "firmware": "\U000f061a",  # md-chip
    "previous": "\U000f02da",  # md-history
    "more": "\U000f0140",      # md-chevron_down, as the Weather card's
    "less": "\U000f0143",      # md-chevron_up
}

CSS = b"""
/* Armed: the next click, Enter or Space ends the session. A 10 % tint,
   hovered or not: any deeper and the red text on it falls under 4.5:1. */
.card.power .row.armed { background-color: alpha(@crit, 0.1); }
.card.power .row.armed .icon,
.card.power .row.armed .name,
.card.power .row.armed .sub { color: @crit; }
.card.power .row.armed .name { font-weight: bold; }
/* Focused while armed, the focus ring in the row's red. */
.card.power .row.armed:focus { outline-color: @crit; }
/* A row's letter: an outlined keycap at its right end in the sub-line's
   grey, lighter on a hovered row (subtext0 has 4.1:1 there), red while
   the row is armed. */
.card.power .key {
    color: @subtext0;
    font-size: 12px;
    border: 1px solid @surface2;
    border-radius: 6px;
    padding: 0 5px;
}
.card.power .row:hover .key { color: @subtext1; }
.card.power .row.armed .key { color: @crit; border-color: @crit; }
/* Worth knowing, but it stops nothing: @warn, the colour the bar's
   attention module gives a pending reboot (7.1:1 here, 5.2:1 hovered). */
.card.power .sub.warn { color: @warn; }
/* The chevron to the restarts for one boot: dimmed like the uptime beside
   it, since what it hides is rarely wanted; text colour once hovered or
   open. */
.card.power .iconbtn.more { color: @subtext0; }
.card.power .iconbtn.more:hover,
.card.power .iconbtn.more.open { color: @text; }
"""

# A row: glyph, name, the line under the name, its command, its letter,
# the logind lock kind that holds it off, the verb of "Click again to ..."
# for a row that ends the session, whether the command is only launched,
# and the row's tooltip while nothing holds it off. hyprlock runs until
# the session is unlocked and hyprshutdown until the session ends;
# systemctl suspend answers at once, and the card waits for that to say
# why if logind refuses (polkit wanting a password with no agent to ask
# for it).
Action = namedtuple("Action", "glyph name sub cmd key held verb launch tip",
                    defaults=(None, False, None))

# hyprshutdown gets a scope of its own: the card runs inside a unit that
# stops with the session (waybar.service, when the bar started it), and
# that would take hyprshutdown with it before its -p command ran.
SCOPE = ("systemd-run", "--user", "--scope", "--collect")

ACTIONS = (
    # An idle lock doesn't stop this row, only the automatic lock.
    Action(ICON["lock"], "Lock", "Super+L",
           ("sh", "-c", "pidof hyprlock || hyprlock"), "L", "idle",
           launch=True),
    # hypridle's before_sleep_cmd locks the session on the way down.
    Action(ICON["suspend"], "Suspend", "Locks first",
           ("systemctl", "suspend"), "S", "sleep"),
    Action(ICON["logout"], "Log out", None,
           SCOPE + ("hyprshutdown", "-t", "Logging out…"),
           "O", None, "log out", True),
    Action(ICON["restart"], "Restart", None,
           SCOPE + ("hyprshutdown", "-t", "Restarting…",
                    "-p", "systemctl reboot"),
           "R", "shutdown", "restart", True),
    Action(ICON["poweroff"], "Shut down", None,
           SCOPE + ("hyprshutdown", "-t", "Shutting down…",
                    "-p", "systemctl poweroff"),
           "D", "shutdown", "shut down", True),
)
RESTART = 3

# The chevron's first restart; the second, into a generation, is built
# from the boot menu (into_generation). hyprshutdown runs -p through
# /bin/sh right after it tells Hyprland to exit, as for Restart.
FIRMWARE = Action(
    ICON["firmware"], "Firmware setup", "BIOS / UEFI settings",
    SCOPE + ("hyprshutdown", "-t", "Restarting into firmware setup…",
             "-p", "systemctl reboot --firmware-setup"),
    "F", "shutdown", "restart into it", True,
    "The firmware's settings screen, for one boot: leaving it boots as "
    "usual")

# What logind is asked about the chevron's restarts: "yes", or "na" where
# the firmware or the boot loader can't (no EFI, no systemd-boot), or
# "challenge" where polkit would want a password, which nothing here could
# ask for (no agent). Only "yes" counts.
CAN = {"firmware": "CanRebootToFirmwareSetup",
       "entry": "CanRebootToBootLoaderEntry"}

# A NixOS entry's version in bootctl's list: "Generation 101 NixOS Zokor
# 26.11.20261010.6510408 (Linux 6.18.55), built on 2026-10-19". attention's
# LABEL reads the release and the kernel out of it, as out of boot.json.
GENERATION = re.compile(r"Generation (\d+) ")
BUILT = re.compile(r", built on (\d{4})-(\d\d)-(\d\d)$")
# The generation before the default: its boot entry, its number, NixOS
# release and Linux version, build date (each None where the version
# leaves it out), whether it is the one running, and the default's number.
Generation = namedtuple("Generation",
                        "id number release kernel built running default")

# Arming by key names the key: Space, else Enter (Return, the keypad's).
SPACE = (Gdk.KEY_space, Gdk.KEY_KP_Space)
# A letter with one of these held is a shortcut of something else.
CHORD = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
         | Gdk.ModifierType.MOD4_MASK | Gdk.ModifierType.SUPER_MASK
         | Gdk.ModifierType.META_MASK)

# Names of our own: GTK sets the C library's locale from the session, so
# strftime() would follow LC_TIME, which is German here (card_calendar).
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec")


def uptime():
    """('Up 27 min', 'Since Sat 17 Oct, 22:04'). /proc/uptime counts the
    time asleep too, as uptime(1) does."""
    up = float(read_text("/proc/uptime").split()[0])
    boot = time.localtime(time.time() - up)
    since = (f"Since {DAYS[boot.tm_wday]} {boot.tm_mday} "
             f"{MONTHS[boot.tm_mon - 1]}, {boot.tm_hour:02}:{boot.tm_min:02}")
    minutes = int(up // 60)
    hours, rest = divmod(minutes, 60)
    days = hours // 24
    text = (f"Up {minutes} min" if hours == 0
            else f"Up {hours} h {rest} min" if days == 0
            else f"Up {days} day" + ("s" if days > 1 else ""))
    return text, since


def block_locks(callback):
    """callback({kind: [(who, why), ...]}) with the block locks logind
    holds now ("idle", "sleep", "shutdown", ...), each holder once, in
    the order they were taken; {} if logind can't be asked. Delay locks
    don't stop anything for long (Chrome saving its state at shutdown), so
    they are left out. One D-Bus call, answered in the main loop."""
    def done(bus, result):
        try:
            reply = bus.call_finish(result)
        except GLib.Error:
            callback({})
            return
        found = {}
        for what, who, why, mode, _uid, _pid in reply.unpack()[0]:
            if mode != "block":
                continue
            for kind in what.split(":"):
                holders = found.setdefault(kind, [])
                if (who, why) not in holders:
                    holders.append((who, why))
        callback(found)

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error:
        callback({})
        return
    bus.call("org.freedesktop.login1", "/org/freedesktop/login1",
             "org.freedesktop.login1.Manager", "ListInhibitors", None,
             GLib.VariantType("(a(ssssuu))"), Gio.DBusCallFlags.NONE,
             2000, None, done)


def reboot_into(callback):
    """callback({"firmware": bool, "entry": bool}): whether logind would,
    for this session and without a password, restart into the firmware's
    setup and into a boot menu entry of our choosing (CAN). Two D-Bus
    calls, answered in the main loop; False for any it can't answer."""
    found = dict.fromkeys(CAN, False)
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error:
        callback(found)
        return
    left = len(CAN)

    def done(bus, result, what):
        nonlocal left
        try:
            found[what] = bus.call_finish(result).unpack()[0] == "yes"
        except GLib.Error:
            pass
        left -= 1
        if left == 0:
            callback(found)

    for what, method in CAN.items():
        bus.call("org.freedesktop.login1", "/org/freedesktop/login1",
                 "org.freedesktop.login1.Manager", method, None,
                 GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 2000,
                 None, done, what)


def boot_menu(callback):
    """callback(entries): the boot menu as `bootctl list` has it, newest
    first; [] if bootctl can't read it (an ESP that only root may read,
    as a desktop's may be; this one's is mode 755). Read only, about 25 ms,
    off the main loop."""
    def done(ok, out, _err):
        try:
            entries = json.loads(out) if ok else []
        except ValueError:
            entries = []
        callback(entries if isinstance(entries, list) else [])
    run_async(done, "bootctl", "list", "--json=short")


def previous_generation(entries):
    """The generation before the one a restart boots (the default entry),
    from boot_menu()'s entries: the newest older one, as `nixos-rebuild
    switch --rollback` would pick it, and of the same title, so that a
    specialisation goes back to its own. None if the default is no NixOS
    generation, or nothing older is in the menu."""
    gens = []
    for entry in entries:
        if not (isinstance(entry, dict) and entry.get("type") == "type1"
                and isinstance(entry.get("id"), str)):
            continue
        match = GENERATION.match(entry.get("version") or "")
        if match:
            gens.append((int(match.group(1)), entry))
    default = next(((n, e) for n, e in gens if e.get("isDefault")), None)
    if default is None:
        return None
    older = [(n, e) for n, e in gens if n < default[0]
             and e.get("title") == default[1].get("title")]
    if not older:
        return None
    number, entry = max(older, key=lambda found: found[0])
    version = entry["version"]
    release = LABEL.search(version)
    built = BUILT.search(version)
    try:
        day = datetime.date(*map(int, built.groups())) if built else None
    except ValueError:
        day = None
    return Generation(entry["id"], number,
                      *(release.groups() if release else (None, None)),
                      day, bool(entry.get("isSelected")), default[0])


def into_generation(gen):
    """The chevron's row that restarts into generation `gen` for one boot.
    When that is the one running now (after a `nixos-rebuild boot`), the
    row says so: the restart then puts the new generation off for a boot."""
    sub = "The one running now" if gen.running else "The previous one"
    if gen.kernel:
        sub += f" · Linux {gen.kernel}"
    built = gen.built and (f"built {DAYS[gen.built.weekday()]} "
                           f"{gen.built.day} {MONTHS[gen.built.month - 1]}")
    facts = " · ".join(filter(None, (
        gen.release and f"NixOS {gen.release}",
        gen.kernel and f"Linux {gen.kernel}", built)))
    tip = "\n".join(filter(None, (
        facts, f"For one boot: the restart after it boots generation "
               f"{gen.default} again")))
    return Action(
        ICON["previous"], f"Generation {gen.number}", sub,
        SCOPE + ("hyprshutdown", "-t",
                 f"Restarting into generation {gen.number}…", "-p",
                 "systemctl reboot --boot-loader-entry="
                 + shlex.quote(gen.id)),
        "P", "shutdown", "restart into it", True, tip)


def holder(who, why):
    return f"{who}: {why}" if why else who


class PowerPanel(Panel):
    """The ACTIONS as rows, worked by the pointer or the keyboard, and the
    restarts for one boot behind the chevron."""

    title = "Power"
    css = "power"

    def __init__(self, app):
        super().__init__(app)
        self.arm = Arm(self, self._on_arm)  # the row awaiting a 2nd click
        self._how = "Click"   # what that row's line says to do again
        self.running = False  # a command is out, its answer pending
        self.key = None       # the key pressing a row now, if not the pointer
        self.held = set()     # keycodes of the row letters held down
        self.window = None    # the toplevel whose keys the letters come from
        self._hooks = []      # its key handlers
        self.locks = {}       # block_locks()'s answer
        self.reboot = None    # pending_reboot()'s
        self.can = {}         # reboot_into()'s
        self.opened = False   # the chevron was opened: its rows are coming
        # The chevron, at the very end of the title row, shown once logind
        # says it can restart into something; bar-popup's show_all skips it.
        self.more = button(ICON["more"], "iconbtn", "more")
        self.more.set_valign(Gtk.Align.CENTER)
        self.more.get_child().show()
        self.more.set_no_show_all(True)
        self.more.connect("clicked", self._on_more)
        self.header.pack_end(self.more, False, False, 0)
        self.up = label("", "sub")
        self.up.set_valign(Gtk.Align.CENTER)
        self.header.pack_end(self.up, False, False, 0)
        # Every row as tall as one with a sub-line, so that arming a row,
        # which gives it one, moves nothing.
        self.same_height = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.VERTICAL)
        self.connect("destroy", lambda *_: self._unhook())
        self.connect("hierarchy-changed", self._on_toplevel)
        self.fill()
        self.load()

    def fill(self):
        self.rows = []     # (button, sub-line label), in self.actions' order
        self.actions = []  # ACTIONS, then the chevron's rows as they come
        for action in ACTIONS:
            self.body.pack_start(self._add(action), False, False, 0)
        # The chevron's rows go under a heading of their own, hidden until
        # it opens (bar-popup's show_all skips the box).
        self.section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=4)
        self.section.set_no_show_all(True)
        heading = label("Restart once into", "seclabel")
        heading.show()
        self.section.pack_start(heading, False, False, 0)
        self.body.pack_start(self.section, False, False, 0)

    def _add(self, action):
        """The row for `action`, after those there are."""
        i = len(self.rows)
        btn, sub = self._row(action)
        # Enter or Space on the focused row: GTK emits "activate", then
        # "clicked" once the key is let go.
        btn.connect("activate", self._on_key)
        btn.connect("clicked", lambda _btn: self._activate(i))
        self.rows.append((btn, sub))
        self.actions.append(action)
        return btn

    def do_focus(self, direction):
        """↓ as the first key starts at the top row, Lock, as before the
        title row had the chevron: GTK would start at the topmost control,
        the chevron, and ↓ ↓ Enter would lock instead of suspending."""
        if (direction == Gtk.DirectionType.DOWN
                and self.get_focus_child() is None):
            self.rows[0][0].grab_focus()
            return True
        return Gtk.Box.do_focus(self, direction)

    def load(self):
        """What the sub-lines and the title row tell: the files at once,
        logind's locks and what the chevron offers when it answers (well
        before a click)."""
        text, since = uptime()
        self.up.set_text(text)
        self.up.set_tooltip_text(since)
        self.reboot = pending_reboot()
        self._settle()
        block_locks(self._got_locks)
        reboot_into(self._got_can)

    def _got_locks(self, locks):
        if self.dead:
            return
        self.locks = locks
        armed = self.arm.key
        if armed is not None and self._rest(armed)[1] == "bad":
            self.arm.disarm()  # held off since it was armed: say by whom
        self._settle()

    def _got_can(self, can):
        if self.dead:
            return
        self.can = can
        into = (["firmware setup"] if can["firmware"] else []) + (
            ["the previous generation"] if can["entry"] else [])
        self.more.set_tooltip_text("Restart once into " + " or ".join(into))
        self.more.set_visible(bool(into))

    def _on_more(self, _btn):
        """Open or close the chevron's restarts. The first opening adds
        firmware setup at once and asks bootctl for the generation."""
        shown = not self.section.get_visible()
        if not self.opened:
            self.opened = True
            if self.can.get("firmware"):
                self._add_more(FIRMWARE)
            if self.can.get("entry"):
                boot_menu(self._got_menu)
        armed = self.arm.key
        if not shown and armed is not None and armed >= len(ACTIONS):
            self.arm.disarm()  # out of sight, out of reach of its 2nd click
        self.section.set_visible(shown)
        self.more.get_child().set_text(ICON["less" if shown else "more"])
        ctx = self.more.get_style_context()
        (ctx.add_class if shown else ctx.remove_class)("open")

    def _got_menu(self, entries):
        """bootctl's answer: the generation's row, or, if the chevron has
        nothing at all to offer, why."""
        if self.dead:
            return
        gen = previous_generation(entries)
        if gen is not None:
            self._add_more(into_generation(gen))
        elif len(self.rows) == len(ACTIONS):  # nothing to offer: say why
            listed = any(isinstance(entry, dict) and entry.get("type")
                         == "type1" for entry in entries)
            none = label("No older generation in the boot menu" if listed
                         else "The boot menu can't be read", "empty")
            none.show()
            self.section.pack_start(none, False, False, 0)

    def _add_more(self, action):
        """A row of the chevron's, under those already there."""
        btn = self._add(action)
        btn.show_all()
        self.section.pack_start(btn, False, False, 0)
        i = len(self.rows) - 1
        self._set_sub(i, *self._rest(i))  # held off already, maybe

    def _settle(self):
        """Every row that is neither armed nor busy shows its resting line."""
        for i in range(len(self.rows)):
            if i != self.arm.key and not self.running:
                self._set_sub(i, *self._rest(i))

    def _rest(self, i):
        """Row i's line while it isn't armed and hasn't failed: (text, kind,
        tooltip), kind being None, "warn" or "bad" (held off)."""
        action = self.actions[i]
        holders = self.locks.get(action.held, []) if action.held else []
        tip = "\n".join(holder(who, why) for who, why in holders)
        if holders and action.held == "idle":
            names = list(dict.fromkeys(who for who, _ in holders))
            return (f"Auto-lock paused by {', '.join(names)}", "warn",
                    f"{tip}\nNo automatic lock, screen-off or suspend until "
                    + ("it lets go" if len(names) == 1 else "they let go"))
        if holders:
            return f"Blocked by {holder(*holders[0])}", "bad", tip
        if i == RESTART and self.reboot:
            return self.reboot.name, "warn", self.reboot.text
        return action.sub, None, action.tip

    def _row(self, action):
        """cardkit.row's layout, keeping the sub-line that arming and
        errors rewrite, and the row's letter at its right end."""
        end = Pango.EllipsizeMode.END
        box = Gtk.Box(spacing=12)
        glyph = label(action.glyph, "icon", xalign=0.5)
        glyph.set_size_request(24, -1)
        box.pack_start(glyph, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        text.pack_start(label(action.name, "name", ellipsize=end),
                        False, False, 0)
        sub = label(action.sub or "", "sub", ellipsize=end)
        sub.set_no_show_all(True)
        sub.set_visible(bool(action.sub))
        text.pack_start(sub, False, False, 0)
        box.pack_start(text, True, True, 0)
        key = label(action.key, "key", xalign=0.5)
        key.set_valign(Gtk.Align.CENTER)
        box.pack_end(key, False, False, 0)
        btn = button(box, "row")
        self.same_height.add_widget(btn)
        return btn, sub

    def _on_toplevel(self, _widget, _previous):
        """The letters reach the window, not the rows: until a key moves
        the focus onto one, nothing in the card has it (bar-popup)."""
        top = self.get_toplevel()
        top = top if isinstance(top, Gtk.Window) else None
        if top is not self.window:
            self._unhook()
            if top is not None:
                self.window = top
                self._hooks = [
                    top.connect("key-press-event", self._on_letter),
                    top.connect("key-release-event", self._on_letter_up)]

    def _unhook(self):
        """Off the window: one that outlives this card must not hand its
        keys to it."""
        for hook in self._hooks:
            self.window.disconnect(hook)
        self.window, self._hooks = None, []

    def _letter(self, char):
        """The row with the letter `char`, of those that show: the
        chevron's answer only while it is open."""
        return next((i for i, action in enumerate(self.actions)
                     if action.key == char and self.rows[i][0].get_mapped()),
                    None)

    def _on_letter(self, win, event):
        """A row's letter acts like a click on its row. Read in the first
        layout, so it is the same key whatever layout is on; held down, it
        presses once, and arming a row and acting on it take two presses,
        not one press and its key repeat."""
        if self.dead or event.state & CHORD:
            return False
        keymap = Gdk.Keymap.get_for_display(win.get_display())
        found, keyval, *_ = keymap.translate_keyboard_state(
            event.hardware_keycode, Gdk.ModifierType(0), 0)
        char = Gdk.keyval_to_unicode(Gdk.keyval_to_upper(keyval))
        i = self._letter(chr(char)) if found and char else None
        if i is None:
            return False
        if event.hardware_keycode not in self.held:
            self.held.add(event.hardware_keycode)
            if self.actions[i].verb:
                self.rows[i][0].grab_focus()  # Enter then acts on it too
            self.key = self.actions[i].key
            self._activate(i)
        return True

    def _on_letter_up(self, _win, event):
        self.held.discard(event.hardware_keycode)
        return False

    def _on_key(self, _btn):
        """Enter or Space on a row: which one, for the armed line to say."""
        event = Gtk.get_current_event()
        keyval = event.get_keyval()[1] if event else None
        self.key = "Space" if keyval in SPACE else "Enter"

    def _activate(self, i):
        """A click on row i, or its letter, Enter or Space on it."""
        key, self.key = self.key, None
        if self.running:
            return
        if self._rest(i)[1] == "bad":
            # logind would refuse, and to Restart or Shut down only once
            # hyprshutdown had closed every app. Look again instead: the
            # app may have let go since the card opened.
            if self.arm.key == i:
                self.arm.disarm()
            block_locks(self._got_locks)
            return
        action = self.actions[i]
        if action.verb:
            self._how = f"Press {key}" if key else "Click"
            if not self.arm.confirm(i):
                return  # armed now: the next click acts
        else:
            self.arm.disarm()  # acting on another row drops the arming
        if action.launch:
            spawn(*action.cmd)
            self.app.close()
            return
        if self.arm.key != i:
            self._set_sub(i, *self._rest(i))  # an earlier error goes
        self.running = True  # an armed row stays armed while it runs
        run_async(lambda ok, out, err: self._done(i, ok, out, err),
                  *action.cmd)

    def _done(self, i, ok, out, err):
        if self.dead:
            return
        self.running = False
        if ok:
            self.app.close()
            return
        self.arm.disarm()
        error = err.strip() or out.strip() or "Failed"
        # systemctl says "Call to SuspendWithFlags failed: <reason>".
        self._set_sub(i, error.splitlines()[0].rsplit(": ", 1)[-1], "bad",
                      error)

    def _set_sub(self, i, text, kind=None, tooltip=None):
        """The line under row i's name, in peach for a "warn" and red for
        a "bad" kind, with the whole story in the row's tooltip."""
        btn, sub = self.rows[i]
        sub.set_text(text or "")
        sub.set_visible(bool(text))
        btn.set_tooltip_text(tooltip)
        ctx = sub.get_style_context()
        for cls in ("warn", "bad"):
            (ctx.add_class if cls == kind else ctx.remove_class)(cls)

    def _on_arm(self, old, new):
        """The arming moved (cardkit.Arm): the row that lost it gets its
        own line back, the one that got it turns red and says how to act."""
        if old is not None:
            self.rows[old][0].get_style_context().remove_class("armed")
            self._set_sub(old, *self._rest(old))
        if new is not None:
            self.rows[new][0].get_style_context().add_class("armed")
            self._set_sub(new, f"{self._how} again to "
                          f"{self.actions[new].verb}")
