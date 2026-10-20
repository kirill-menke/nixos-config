"""The Attention card, `bar-popup attention`: what the bar's attention
module flags (attention.py), one row each, with what can be done about it
from here.

A unit's row opens this boot's log of it in a terminal, at its end. A user
unit's row also has its remedies at the end: a failed one restarts or is
dismissed (reset-failed), a crash-looping one stops. Restart and Stop arm
on the first click and act on a second within cardkit.ARM_MS, as Forget
does on the Network and Bluetooth cards; Dismiss only clears the flag, so
it acts at once. System units only show their log: changing one takes
root, and no polkit agent runs to ask for the password. A pending reboot
restarts the machine, armed like the Power card's Restart. A full root
file system and an app hogging the processor open the System card, where
the disk's meter and the apps using the CPU are. A firmware update opens
fwupdmgr's list of updates, with their release notes, in a terminal that
stays open for `fwupdmgr update`. After an action the card checks again;
the bar's module (attention watch) follows the unit's change by itself.

The check asks both service managers, udisks2 and UPower over D-Bus, which
takes 70-170 ms, so it runs on a thread of its own, as the Weather card
fetches: the card opens at once, its rows follow. It never asks fwupd:
the firmware rows are fwupd's last answer, as the bar's module keeps it.
"""

import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
import attention  # noqa: E402
import cardkit  # noqa: E402
from cardkit import (Arm, Panel, button, label, row, run_async,  # noqa: E402
                     spawn, tile)

# Nerd Font glyphs (Material Design icons): the shared ones and the card's.
ICON = cardkit.ICON | {
    "failed": "\U000f13ce",      # md-cog_off
    "restarting": "\U000f04e7",  # md-sync_alert
    "drive": "\U000f104c",       # md-harddisk_remove
    "disk": "\U000f02ca",        # md-harddisk
    "hog": attention.CPU,        # md-cpu_64_bit, as on the bar
    "reboot": attention.REBOOT,  # md-restart_alert, as on the bar
    "firmware": attention.FIRMWARE,  # md-chip, as on the bar
    "restart": "\U000f0709",     # md-restart, as the Power card's
    "stop": "\U000f04db",        # md-stop
    "dismiss": "\U000f0156",     # md-close
    "terminal": "\U000f018d",    # md-console
}

# A user unit's remedies by its kind, left to right: (remedy, tooltip,
# what its row says while armed; None acts on the first click).
REMEDIES = {
    "failed": (("restart", "Restart", "Click again to restart"),
               ("dismiss", "Dismiss (reset-failed)", None)),
    "restarting": (("stop", "Stop the loop", "Click again to stop it"),),
}
VERB = {"restart": "restart", "stop": "stop", "dismiss": "reset-failed"}
BUSY = {"restart": "Restarting…", "stop": "Stopping…",
        "dismiss": "Dismissing…", "reboot": "Restarting the machine…"}
REBOOT_ARMED = "Click again to restart the machine"

# The terminal view the module's right click opens, for all of it at once.
DETAILS = ("kitty", "--class=attention-details", "--title=Needs attention",
           "-o", "window_padding_width=12", "--hold", "attention", "details")
# fwupd's updates with their release notes, in the same kind of window; it
# stays open for `fwupdmgr update`, which asks before it installs anything.
FIRMWARE = ("kitty", "--class=attention-details", "--title=Firmware updates",
            "-o", "window_padding_width=12", "--hold", "fwupdmgr",
            "get-updates")

CSS = b"""
/* The glyphs in the bar module's colours: @crit for what is broken, @warn
   for what is worth a look (a pending reboot, an app hogging the CPU, a
   firmware update). */
.card.attention .icon { color: @crit; }
.card.attention .reboot .icon,
.card.attention .hog .icon,
.card.attention .firmware .icon { color: @warn; }
/* The remedies are quieter than the rows, as Forget is. */
.card.attention .remedy { color: @subtext0; }
/* Armed: the row turns red with the Network card's 10 % tint, with no
   highlight or hover colour behind the red text. */
.card.attention .armed {
    background-color: alpha(@crit, 0.1);
    border-radius: 12px;
}
.card.attention .armed .row:hover { background-color: transparent; }
.card.attention .armed .iconbtn:hover { background-color: @raised; }
.card.attention .armed .icon,
.card.attention .armed .name,
.card.attention .armed .sub,
.card.attention .armed .remedy { color: @crit; }
"""


def key(item):
    """What tells a row again after a check: its kind and unit or name."""
    return item.kind, item.key or item.name, item.user


def tip(item):
    """A clickable row's tooltip: where the click leads."""
    if item.kind in ("disk", "hog"):
        return "The System card"
    if item.kind == "firmware":
        return "Its release notes in a terminal"
    if item.kind == "reboot":
        return None
    text = "Its log in a terminal"
    if not item.user:
        text += ("\nChanging it takes root: "
                 f"sudo systemctl restart {item.key}")
    return text


class AttentionPanel(Panel):
    """attention.check()'s findings as rows, worked by the pointer or the
    keyboard."""

    title = "Needs attention"
    css = "attention"

    def __init__(self, app):
        super().__init__(app)
        self.header_button(ICON["terminal"], "Details in a terminal",
                           *DETAILS)
        # (row key, remedy) waiting for its 2nd click
        self.arm = Arm(self, lambda _old, _new: self.refresh())
        self.status = {}    # row key -> (sub-line, is_error) after a click
        self.items = None   # until the first check lands
        self._check()
        self.fill()

    def _check(self, done=None):
        """attention.check() off the main loop. `done`: the row whose action
        just went well, whose busy line goes once the check lands."""
        def work():
            if not self.dead:
                # The bar's last CPU sample, not one of its own: the card's
                # would cut the span of the bar's next one short.
                GLib.idle_add(self._checked, attention.check(sampling=False),
                              done)
        threading.Thread(target=work, daemon=True).start()

    def _checked(self, items, done):
        if not self.dead:
            self.items = items
            self.status.pop(done, None)  # a restarted unit's row goes
            self.refresh()
        return GLib.SOURCE_REMOVE

    def fill(self):
        if self.items is None:
            return  # empty while the first check runs, well under a second
        for item in self.items:
            self.body.pack_start(self._row(item), False, False, 0)
        if not self.items:
            self.body.pack_start(label("Nothing needs attention", "empty"),
                                 False, False, 0)

    def _row(self, item):
        """A finding's row, its remedies at the end."""
        k = key(item)
        sub, bad = self.status.get(k, (item.sub, False))
        armed = self.arm.key is not None and self.arm.key[0] == k
        if armed:
            sub, bad = self._armed_text(item), False
        box = Gtk.Box(spacing=4)
        box.get_style_context().add_class(item.kind)
        if armed:
            box.get_style_context().add_class("armed")
        click = self._click(item)
        if click is None:
            line = tile(ICON[item.kind], item.name, sub, sub_bad=bad)
            line.get_style_context().add_class("static")
        else:
            line = row(ICON[item.kind], item.name, sub, sub_bad=bad)
            line.connect("clicked", click, item)
            line.set_tooltip_text(tip(item))
        if bad:
            line.set_tooltip_text(sub)  # in case it doesn't fit
        box.pack_start(line, True, True, 0)
        for remedy, hint, _ in reversed(REMEDIES.get(item.kind, ())
                                        if item.user else ()):
            btn = button(ICON[remedy], "iconbtn", "remedy")
            btn.set_tooltip_text(hint)
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", self._on_remedy, item, remedy)
            box.pack_end(btn, False, False, 0)
        return box

    def _click(self, item):
        if item.kind in ("failed", "restarting"):
            return self._on_log
        if item.kind == "reboot":
            return self._on_reboot
        if item.kind == "firmware":
            return self._on_firmware
        if item.kind == "hog" or (item.kind == "disk" and item.key == "/"):
            return self._on_system
        return None

    def _armed_text(self, item):
        if item.kind == "reboot":
            return REBOOT_ARMED
        return next(text for remedy, _, text in REMEDIES[item.kind]
                    if remedy == self.arm.key[1])

    # --- Clicks -------------------------------------------------------------

    def _on_log(self, _btn, item):
        """This boot's log of the unit, at its end, in a terminal like the
        details view; it closes with the pager."""
        scope = ["--user"] if item.user else []
        spawn("kitty", "--class=attention-details", f"--title={item.key}",
              "journalctl", *scope, "--boot", "--pager-end", "--unit",
              item.key)
        self.app.close()

    def _on_system(self, _btn, _item):
        # Swapped on idle: the click is still being handled in this card.
        GLib.idle_add(lambda: self.app.open("system") and False)

    def _on_firmware(self, _btn, _item):
        spawn(*FIRMWARE)
        self.app.close()

    def _on_reboot(self, _btn, item):
        self._arm_or_run(item, "reboot", ("systemctl", "reboot"))

    def _on_remedy(self, _btn, item, remedy):
        cmd = ("systemctl", "--user", VERB[remedy], "--", item.key)
        if remedy == "dismiss":
            self.arm.disarm()
            self._run(item, remedy, cmd)
        else:
            self._arm_or_run(item, remedy, cmd)

    def _arm_or_run(self, item, remedy, cmd):
        k = key(item)
        if k in self.status and not self.status[k][1]:
            return  # already on its way
        if self.arm.confirm((k, remedy)):
            self.arm.disarm()
            self._run(item, remedy, cmd)

    def _run(self, item, remedy, cmd):
        k = key(item)
        self.status[k] = (BUSY[remedy], False)
        self.refresh()
        run_async(lambda ok, out, err: self._done(k, ok, out, err), *cmd)

    def _done(self, k, ok, out, err):
        if self.dead:
            return
        if ok:
            self._check(done=k)
            return
        # systemctl: "Failed to restart x.service: Unit x.service not
        # found." -> the part after the colon, all of it in the tooltip
        error = (err.strip() or out.strip() or "Failed").splitlines()[0]
        self.status[k] = (error.rsplit(": ", 1)[-1], True)
        self.refresh()
