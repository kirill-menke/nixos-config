"""The Bluetooth card, `bar-popup bluetooth`: the adapter's power switch
and the paired devices, all through BlueZ on the system bus. A click on a
device connects it. A connected one is highlighted, as the Network card's
connection in use is, takes no clicks and has a Disconnect button at its
end where the others have Forget (a second click confirms). While
Bluetooth is off the paired devices stay listed: a click turns it on and
connects that device as soon as BlueZ reports the adapter powered.

The + in the title row searches for new devices, turning Bluetooth on
first, and lists them under the paired ones; a click pairs one, trusts it
and connects it. While it searches or pairs, the card is BlueZ's pairing
agent: with no agent BlueZ keeps the adapter from bonding (its
AlwaysPairable is off), and the agent shows the code a phone or a
keyboard asks for.
"""

import collections

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
import cardkit  # noqa: E402
from cardkit import (Arm, Panel, button, hand, label, prop, row,  # noqa: E402
                     tile)

# Nerd Font glyphs, the same ones the bar uses: the shared ones and the
# card's own.
ICON = cardkit.ICON | {
    "bluetooth": "\U000f00af",
    "phone": "\U000f03f2",
    "keyboard": "\U000f030c",
    "mouse": "\U000f037d",
    "gamepad": "\U000f02b4",
    "computer": "\U000f0379",
    "add": "\U000f0415",
    "close": "\U000f0156",
    "disconnect": "\U000f0338",  # the Network card's
    "forget": "\U000f09e7",
}

CSS = b"""
/* Forget and Cancel are quieter than the rows. Armed, the device's row
   turns red, as the Network card's do, with their 10 % tint: no hover
   colour behind the red text, and the button's hover in @raised. */
.card.bluetooth .forget, .card.bluetooth .cancel { color: @subtext0; }
.card.bluetooth .armed {
    background-color: alpha(@crit, 0.1);
    border-radius: 12px;
}
.card.bluetooth .armed .row:hover { background-color: transparent; }
.card.bluetooth .armed .iconbtn:hover { background-color: @raised; }
.card.bluetooth .armed .icon,
.card.bluetooth .armed .name,
.card.bluetooth .armed .sub,
.card.bluetooth .armed .forget { color: @crit; }
/* A pairing code to compare or type, in yellow: the one line that must
   be read. */
.card.bluetooth .sub.code { color: @yellow; }
"""

DEVICE_ICONS = {
    "audio-headset": "headphones",
    "audio-headphones": "headphones",
    "audio-card": "speaker",
    "phone": "phone",
    "input-keyboard": "keyboard",
    "input-mouse": "mouse",
    "input-tablet": "mouse",
    "input-gaming": "gamepad",
    "computer": "computer",
    "video-display": "tv",
}

# The properties the card shows. BlueZ reports much more (a playing
# headset's track position and volume, signal strength while searching),
# and a rebuild between press and release would swallow a click.
SHOWN = {
    "org.bluez.Adapter1": {"Powered", "Discovering"},
    "org.bluez.Device1": {"Alias", "Connected", "Icon", "Paired", "Name"},
    "org.bluez.Battery1": {"Percentage"},
}

# BlueZ answers a power-up only once the controller is up and reports
# Powered right after; devices waiting for it give up after this long.
POWER_UP_WAIT = 3  # s
NEARBY_MAX = 6  # new devices listed, as the Network card's WIFI_MAX
# Pairing waits for a person: a code to compare, or one to type on a
# keyboard. BlueZ gives up on its side first (its agent timeout is 60 s).
PAIR_MS = 90000

AGENT_PATH = "/com/kirill/barpopup/agent"
AGENT_IFACE = Gio.DBusNodeInfo.new_for_xml("""<node>
<interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="RequestPinCode">
    <arg type="o" direction="in"/><arg type="s" direction="out"/>
  </method>
  <method name="DisplayPinCode">
    <arg type="o" direction="in"/><arg type="s" direction="in"/>
  </method>
  <method name="RequestPasskey">
    <arg type="o" direction="in"/><arg type="u" direction="out"/>
  </method>
  <method name="DisplayPasskey">
    <arg type="o" direction="in"/><arg type="u" direction="in"/>
    <arg type="q" direction="in"/>
  </method>
  <method name="RequestConfirmation">
    <arg type="o" direction="in"/><arg type="u" direction="in"/>
  </method>
  <method name="RequestAuthorization">
    <arg type="o" direction="in"/>
  </method>
  <method name="AuthorizeService">
    <arg type="o" direction="in"/><arg type="s" direction="in"/>
  </method>
  <method name="Cancel"/>
</interface>
</node>""").interfaces[0]

# BlueZ's failure reasons (src/error.h) and error names as a person would
# put them, matched in this order against the lower-cased message.
REASONS = (
    ("page-timeout", "Not in range or switched off"),
    ("host is down", "Not in range or switched off"),
    ("key-missing", "Pairing lost: forget it and pair again"),
    ("refused", "The device refused the connection"),
    ("aborted-by-remote", "The device dropped the connection"),
    ("busy", "Busy, try again in a moment"),
    ("inprogress", "Busy, try again in a moment"),
    ("authenticationrejected", "The device refused to pair"),
    ("authenticationcanceled", "Pairing cancelled"),
    ("authenticationfailed", "Pairing failed: try again in pairing mode"),
    ("connectionattemptfailed", "Not in range or not in pairing mode"),
    ("timeout", "No answer, try again"),
    ("profileunavailable", "Nothing to connect (audio service down?)"),
    ("not-powered", "Bluetooth is off"),
    ("notready", "Bluetooth is off"),
    ("rfkill", "Bluetooth is blocked (rfkill)"),
)

# One device row as the card shows it; the card rebuilds only when one of
# these, or the rest of Model, changes. Model.adapter is None until BlueZ
# has answered, then whether it has an adapter.
Row = collections.namedtuple("Row",
                             "path glyph name sub bad on armed busy code")
Model = collections.namedtuple(
    "Model", "switch sensitive error adapter paired searching nearby empty")


def short_error(err):
    """'GDBus.Error:org.bluez.Error.Failed: br-connection-page-timeout'
    -> something a person can read."""
    msg = err.message.lower()
    for key, text in REASONS:
        if key in msg:
            return text
    reason = err.message.rsplit(": ", 1)[-1]
    if reason.startswith(("br-connection-", "le-connection-")):
        # "br-connection-sdp-search" -> "Failed (sdp search)"
        return f"Failed ({reason.split('-', 2)[2].replace('-', ' ')})"
    return reason[:60]


def grouped(code):
    """A six-digit passkey as people read it off a screen: '482 913'."""
    code = f"{code:06d}" if isinstance(code, int) else str(code)
    return f"{code[:3]} {code[3:]}" if len(code) == 6 else code


class Agent:
    """BlueZ's pairing agent, one per bar-popup process: a connection
    exports one object per path, and BlueZ keeps one agent per client. A
    card uses it while it searches or pairs, and that may outlast the
    card (a pairing goes on after it closed) into the next one; the last
    card done with it unregisters it, and BlueZ's previous default agent,
    if any, takes over again."""

    def __init__(self):
        self.conn = None
        self.reg = None
        self.users = []  # the cards using it, oldest first

    def use(self, card, conn):
        if card not in self.users:
            self.users.append(card)
        if self.reg is not None:
            return
        try:
            self.reg = conn.register_object_with_closures2(
                AGENT_PATH, AGENT_IFACE, self._on_call, None, None)
        except GLib.Error:
            return  # pairing still works for devices that need no code
        self.conn = conn
        # The default agent, so BlueZ makes the adapter bondable for it
        # (adapter_set_io_capability) and asks it about the codes.
        # DisplayYesNo: a phone shows a code to compare, a keyboard one to
        # type, a headset none.
        for method, args in (("RegisterAgent",
                              ("(os)", (AGENT_PATH, "DisplayYesNo"))),
                             ("RequestDefaultAgent", ("(o)", (AGENT_PATH,)))):
            conn.call("org.bluez", "/org/bluez", "org.bluez.AgentManager1",
                      method, GLib.Variant(*args), None,
                      Gio.DBusCallFlags.NONE, 5000, None, None, None)

    def drop(self, card):
        if card in self.users:
            self.users.remove(card)
        if self.users or self.reg is None:
            return
        self.conn.call("org.bluez", "/org/bluez", "org.bluez.AgentManager1",
                       "UnregisterAgent", GLib.Variant("(o)", (AGENT_PATH,)),
                       None, Gio.DBusCallFlags.NONE, 5000, None, None, None)
        self.conn.unregister_object(self.reg)
        self.conn = self.reg = None

    def _on_call(self, _conn, _sender, _path, _iface, method, params, inv):
        """BlueZ's questions, for the card pairing that device; anything
        else (a stranger asking to pair, a paired device that isn't
        trusted asking to connect) is refused."""
        args = params.unpack()
        if method == "Cancel":
            # BlueZ dropped its question (timed out, or the device gave up).
            for card in self.users:
                card.agent_cancelled()
            inv.return_value(None)
        elif method == "Release":  # bluetoothd is going away
            inv.return_value(None)
        elif not any(card.agent_asked(method, args, inv)
                     for card in reversed(self.users)):
            inv.return_dbus_error("org.bluez.Error.Rejected", "Rejected")


AGENT = Agent()


class BluetoothPanel(Panel):
    title = "Bluetooth"
    css = "bluetooth"

    def __init__(self, app):
        super().__init__(app)
        self.pending = {}   # device path -> (status text, is_error)
        # Devices clicked while Bluetooth was off, connected once the
        # adapter reports Powered (_on_changed).
        self.waiting = set()
        self.power_error = None
        self.power_target = None  # Powered value asked for, until reported
        self.power_timer = None
        # Forget's first click arms the row, a second one within ARM_MS
        # removes the device; anything else done in the card disarms it.
        self.arm = Arm(self, lambda _old, _new: self.refresh())
        # Searching: the + was clicked. The card asks BlueZ to discover
        # once the adapter is up and lists what it finds in `order`, which
        # only grows, so a row never moves under the pointer.
        self.searching = False
        self.discovering = False  # our StartDiscovery went out
        self.search_error = None
        self.order = []
        # Devices the card pairs, from the click until the Connect after
        # the pairing has answered: the agent says yes for these only.
        self.pairing = set()
        self.cancelled = set()  # of those, the ones whose × was clicked
        self.legacy = set()  # of those, the ones that asked for a PIN
        # path -> ("confirm", passkey, invocation) or ("type", code, None):
        # what the agent was asked, until the pairing ends.
        self.prompts = {}
        self.model = None
        self.bluez = None
        self.loading = True  # until BlueZ's objects have arrived

        self.switch = hand(Gtk.Switch())
        self.switch.set_valign(Gtk.Align.CENTER)
        self.header.pack_end(self.switch, False, False, 0)
        self._switch_id = self.switch.connect("notify::active",
                                              self._on_power)
        self.search = button(ICON["add"], "iconbtn")
        self.search.set_valign(Gtk.Align.CENTER)
        self.search.connect("clicked", self._on_search)
        self.header.pack_end(self.search, False, False, 0)
        # Asked for without waiting: the card shows at once and fills in
        # when BlueZ's objects arrive. DO_NOT_AUTO_START: a card never
        # starts BlueZ, as the Network card never starts NetworkManager.
        Gio.DBusObjectManagerClient.new_for_bus(
            Gio.BusType.SYSTEM,
            Gio.DBusObjectManagerClientFlags.DO_NOT_AUTO_START,
            "org.bluez", "/", None, None, None, self._bluez_ready, None)
        self.connect("destroy", self._on_destroy)
        self.fill()

    def _bluez_ready(self, _source, result, _data):
        self.loading = False
        try:
            bluez = Gio.DBusObjectManagerClient.new_for_bus_finish(result)
        except GLib.Error:
            bluez = None  # no system bus: shown as BlueZ not running
        if self.dead:
            return
        self.bluez = bluez
        if bluez is not None:
            # interface-added: a headset's Battery1 joins its device
            # object a moment after it connected.
            for signal in ("object-added", "object-removed",
                           "interface-added", "interface-removed"):
                bluez.connect(signal, lambda *_: self.refresh())
            bluez.connect("interface-proxy-properties-changed",
                          self._on_changed)
        self.refresh()

    def _proxies(self, iface):
        if self.bluez is None:
            return []
        return [p for o in self.bluez.get_objects()
                if (p := o.get_interface(iface)) is not None]

    def _adapter(self):
        adapters = self._proxies("org.bluez.Adapter1")
        return adapters[0] if adapters else None

    def _device(self, path):
        return (self.bluez.get_interface(path, "org.bluez.Device1")
                if self.bluez is not None else None)

    def _powered(self):
        """Whether BlueZ reports the adapter on (the switch may be ahead)."""
        return bool(prop(self._adapter(), "Powered", False))

    # --- what the card shows -------------------------------------------

    def _settle_power(self):
        """BlueZ sends a burst of property changes while it powers up or
        down; rebuilding from those would yank the switch back to the old
        state and forth again, so it stays where it was put until BlueZ
        reports the new state (or gives up, see _power_done)."""
        if (self.power_target is not None
                and self._powered() == self.power_target):
            self.power_target = None

    def _model(self):
        if self.bluez is None:
            return Model(False, False, None, None if self.loading else False,
                         (), False, (), None)
        adapter = self._adapter()
        powered = bool(prop(adapter, "Powered", False))
        switch = powered if self.power_target is None else self.power_target
        if adapter is None:
            return Model(switch, False, self.power_error, False, (), False,
                         (), None)
        batteries = {p.get_object_path(): prop(p, "Percentage")
                     for p in self._proxies("org.bluez.Battery1")}
        devices = self._proxies("org.bluez.Device1")
        paired = [d for d in devices if prop(d, "Paired", False)]
        paired.sort(key=lambda d: (not prop(d, "Connected", False),
                                   str(prop(d, "Alias", "")).lower()))
        rows = tuple(self._paired_row(d, powered, batteries) for d in paired)
        nearby, empty = (), None
        if self.searching:
            nearby, empty = self._nearby(devices, powered, adapter)
        return Model(switch, True, self.power_error, True, rows,
                     self.searching, nearby, empty)

    def _paired_row(self, dev, powered, batteries):
        path = dev.get_object_path()
        connected = bool(prop(dev, "Connected", False))
        armed = path == self.arm.key and not connected
        if path in self.pending:
            sub, bad = self.pending[path]
        elif armed:
            sub, bad = "Click again to forget", False
        elif connected:
            sub, bad = "Connected", False
            if batteries.get(path) is not None:
                sub += f" · {batteries[path]}%"
        elif powered:
            sub, bad = "Not connected", False
        else:
            sub, bad = "Turn on and connect", False
        glyph = ICON[DEVICE_ICONS.get(prop(dev, "Icon", ""), "bluetooth")]
        return Row(path, glyph, prop(dev, "Alias", path), sub, bad,
                   connected, armed, False, False)

    def _nearby(self, devices, powered, adapter):
        """The devices a search found, not paired yet: those that told
        their name (most of what a search finds is nameless: phones,
        watches and trackers advertising to their own apps), first come
        first listed, the strongest of a batch first."""
        found = {d.get_object_path(): d for d in devices
                 if not prop(d, "Paired", False)
                 and prop(d, "Name") and not prop(d, "Blocked", False)}
        self.order = [p for p in self.order if p in found]
        new = sorted((p for p in found if p not in self.order),
                     key=lambda p: -prop(found[p], "RSSI", -127))
        self.order += new[:max(0, NEARBY_MAX - len(self.order))]
        rows = []
        for path in self.order:
            dev = found[path]
            prompt = self.prompts.get(path)
            if prompt and prompt[0] == "confirm":
                sub, bad = f"Click if it shows {grouped(prompt[1])}", False
            elif prompt:
                sub, bad = (f"Type {grouped(prompt[1])} on it, then Enter",
                            False)
            elif path in self.pending:
                sub, bad = self.pending[path]
            else:
                sub, bad = "Pair and connect", False
            glyph = ICON[DEVICE_ICONS.get(prop(dev, "Icon", ""), "bluetooth")]
            rows.append(Row(path, glyph, prop(dev, "Alias", path), sub, bad,
                            False, prompt is not None, path in self.pairing,
                            prompt is not None))
        if rows:
            empty = None
        elif self.search_error:
            empty = self.search_error
        elif not powered or self.power_target:
            empty = "Turning Bluetooth on…"
        elif prop(adapter, "Discovering", False):
            empty = "Searching… Put the device in pairing mode"
        else:
            empty = "Not searching"
        return tuple(rows), empty

    def refresh(self):
        """Rebuild only when what the card shows changed: while it
        searches, BlueZ adds devices and updates them all the time."""
        if self.dead:
            return
        self._settle_power()
        if self._model() != self.model:
            super().refresh()

    def fill(self):
        self._settle_power()
        self.model = model = self._model()
        with self.switch.handler_block(self._switch_id):
            self.switch.set_active(model.switch)
        self.switch.set_sensitive(model.sensitive)
        self.search.get_child().set_text(
            ICON["close" if model.searching else "add"])
        self.search.set_tooltip_text(
            "Stop searching" if model.searching else "Pair new device")
        self.search.set_sensitive(bool(model.adapter))
        if model.error:
            # One line: the card is capped at CARD_W, an error from D-Bus
            # can be longer, and a wrapping label added after the card is
            # shown gets one line's height (GTK keeps the request mode it
            # cached). The tooltip has all of it.
            err = label(model.error, "empty", "bad",
                        ellipsize=Pango.EllipsizeMode.END)
            err.set_tooltip_text(model.error)
            self.body.pack_start(err, False, False, 0)
        if model.adapter is None:
            return  # BlueZ's answer is on its way
        if not model.adapter:
            self.body.pack_start(label("Bluetooth service is not running",
                                       "empty"), False, False, 0)
            return
        for r in model.paired:
            self.body.pack_start(
                self._connected(r) if r.on else
                self._item(r, self._on_connect, ICON["forget"], "forget",
                           "Forget device", self._on_forget),
                False, False, 0)
        if not model.paired:
            self.body.pack_start(label("No paired devices", "empty"),
                                 False, False, 0)
        if model.searching:
            self.body.pack_start(label("Nearby devices", "seclabel"),
                                 False, False, 0)
            for r in model.nearby:
                cancel = r.busy or r.armed
                self.body.pack_start(
                    self._item(r, self._on_nearby,
                               ICON["close"] if cancel else None, "cancel",
                               "Cancel pairing", self._on_cancel),
                    False, False, 0)
            if model.empty:
                self.body.pack_start(label(model.empty, "empty"),
                                     False, False, 0)

    def _connected(self, r):
        """A connected device, highlighted like the Network card's
        connection in use. A click on it does nothing: leaving is the
        button at its end (Forget comes back once it is disconnected)."""
        box = Gtk.Box(spacing=4)
        for cls in ("row", "on"):
            box.get_style_context().add_class(cls)
        box.pack_start(tile(r.glyph, r.name, r.sub, r.bad), True, True, 0)
        if r.bad:
            box.set_tooltip_text(r.sub)  # in case it doesn't fit
        leave = button(ICON["disconnect"], "iconbtn")
        leave.set_tooltip_text("Disconnect")
        leave.set_valign(Gtk.Align.CENTER)
        leave.connect("clicked", self._on_disconnect, r.path)
        box.pack_end(leave, False, False, 0)
        return box

    def _item(self, r, on_click, end_glyph, end_class, end_tip, on_end):
        """A device's row, with a button at its end if `end_glyph`."""
        btn = row(r.glyph, r.name, r.sub, on=r.on, sub_bad=r.bad)
        if r.bad:
            btn.set_tooltip_text(r.sub)  # in case it doesn't fit
        if r.code:  # the status line: the tile's text box, its last label
            text = btn.get_child().get_children()[-1]
            text.get_children()[-1].get_style_context().add_class("code")
        btn.connect("clicked", on_click, r.path)
        item = Gtk.Box(spacing=4)
        item.pack_start(btn, True, True, 0)
        if end_glyph:
            end = button(end_glyph, "iconbtn", end_class)
            end.set_tooltip_text(end_tip)
            end.set_valign(Gtk.Align.CENTER)
            end.connect("clicked", on_end, r.path)
            item.pack_end(end, False, False, 0)
        if r.armed and end_class == "forget":
            item.get_style_context().add_class("armed")
        return item

    # --- BlueZ's changes -------------------------------------------------

    def _on_changed(self, _manager, _obj, proxy, changed, _invalidated):
        iface = proxy.get_interface_name()
        changed = changed.unpack()
        if not SHOWN.get(iface, set()) & changed.keys():
            return
        if iface == "org.bluez.Adapter1" and "Powered" in changed:
            # Whatever went wrong before the change no longer applies.
            self.power_error = None
            self.pending = {p: s for p, s in self.pending.items() if not s[1]}
            if self._powered():
                self._connect_waiting()
                if self.searching and not self.discovering:
                    self._discover()
            else:
                self.discovering = False  # BlueZ ends it with the power
        elif iface == "org.bluez.Device1" and "Connected" in changed:
            path = proxy.get_object_path()
            if self.pending.get(path, ("", False))[1]:
                del self.pending[path]  # an error from before, moot now
        self.refresh()

    # --- paired devices --------------------------------------------------

    def _on_connect(self, _btn, path):
        self.arm.disarm()  # acting on a row drops an armed Forget
        dev = self._device(path)
        if (dev is None or prop(dev, "Connected", False)
                or (path in self.pending and not self.pending[path][1])):
            return  # gone, connected meanwhile, or already on its way
        if self._powered() and self.power_target is not False:
            self._call(dev, "Connect")
        else:
            # Off, or on its way up: connect once BlueZ reports it powered.
            self.waiting.add(path)
            self.pending[path] = ("Turning Bluetooth on…", False)
            self.switch.set_active(True)  # _on_power powers it up
            self.refresh()

    def _on_disconnect(self, _btn, path):
        self.arm.disarm()
        dev = self._device(path)
        if dev is None or (path in self.pending and not self.pending[path][1]):
            return  # gone, or already on its way
        self._call(dev, "Disconnect")

    def _connect_waiting(self):
        """The adapter is up: connect what was clicked while it wasn't."""
        waiting, self.waiting = self.waiting, set()
        for path in waiting:
            dev = self._device(path)
            if dev is None:  # removed meanwhile
                self.pending.pop(path, None)
            else:
                self._call(dev, "Connect")

    def _give_up_waiting(self, text):
        """The adapter isn't coming up: say why on the waiting devices'
        rows, or with no text (switched off again) just let them go."""
        for path in self.waiting:
            if text:
                self.pending[path] = (text, True)
            else:
                self.pending.pop(path, None)
        self.waiting.clear()

    def _call(self, dev, method):
        path = dev.get_object_path()
        self.pending[path] = (
            "Connecting…" if method == "Connect" else "Disconnecting…", False)
        dev.call(method, None, Gio.DBusCallFlags.NONE, 30000, None,
                 self._done, (path, method))
        self.refresh()

    def _done(self, dev, result, data):
        path, method = data
        try:
            dev.call_finish(result)
            self.pending.pop(path, None)
            # Also once the card has closed: bar-popup stays, so a device
            # connected just before still gets its trust.
            if method == "Connect" and not prop(dev, "Trusted", False):
                self._trust(dev)
        except GLib.Error as err:
            if "Error.AlreadyConnected" in err.message:
                self.pending.pop(path, None)  # it connected by itself
            else:
                self.pending[path] = (short_error(err), True)
        if path in self.pairing:  # the Connect that follows a pairing
            self.pairing.discard(path)
            self._agent_done()
        self.refresh()

    @staticmethod
    def _trust(dev):
        """Trust a device the card connected or paired: BlueZ then lets it
        connect by itself later (a headset as it is switched on), where an
        untrusted one needs an agent to say yes, and none runs then."""
        args = GLib.Variant("(ssv)", ("org.bluez.Device1", "Trusted",
                                      GLib.Variant("b", True)))
        dev.call("org.freedesktop.DBus.Properties.Set", args,
                 Gio.DBusCallFlags.NONE, 5000, None, None)

    def _on_forget(self, _btn, path):
        """The first click arms Forget, a second one within ARM_MS removes
        the device with its pairing; the + pairs it again."""
        dev = self._device(path)
        if dev is None or (path in self.pending and not self.pending[path][1]):
            return  # gone, or busy with it
        if not self.arm.confirm(path):
            return  # armed: the row asks for the second click
        self.arm.disarm()
        adapter = self.bluez.get_interface(prop(dev, "Adapter", "/"),
                                           "org.bluez.Adapter1")
        if adapter is None:
            return
        self.pending[path] = ("Forgetting…", False)
        adapter.call("RemoveDevice", GLib.Variant("(o)", (path,)),
                     Gio.DBusCallFlags.NONE, 5000, None, self._forgot, path)
        self.refresh()

    def _forgot(self, adapter, result, path):
        try:
            adapter.call_finish(result)
            self.pending.pop(path, None)  # the row goes with the device
        except GLib.Error as err:
            self.pending[path] = (short_error(err), True)
        self.refresh()

    # --- new devices -----------------------------------------------------

    def _on_search(self, _btn):
        self.arm.disarm()
        if self.searching:
            self._stop_search()
            self.refresh()
            return
        self.searching = True
        self.search_error = None
        AGENT.use(self, self.bluez.get_connection())
        if self._powered() and self.power_target is not False:
            self._discover()
        else:
            self.switch.set_active(True)  # _on_changed discovers once up
        self.refresh()

    def _discover(self):
        adapter = self._adapter()
        if adapter is None:
            return
        self.discovering = True
        adapter.call("StartDiscovery", None, Gio.DBusCallFlags.NONE, 5000,
                     None, self._discovering, None)

    def _discovering(self, adapter, result, _data):
        try:
            adapter.call_finish(result)
        except GLib.Error as err:
            if "InProgress" not in err.message:  # already ours: fine
                self.discovering = False
                self.search_error = short_error(err)
        self.refresh()

    def _stop_search(self):
        """Discovery costs radio time, and a headset streaming meanwhile
        can stutter: it lasts only while the list is open."""
        self.searching = False
        self.order = []
        if self.discovering:
            self.discovering = False
            adapter = self._adapter()
            if adapter is not None:
                adapter.call("StopDiscovery", None, Gio.DBusCallFlags.NONE,
                             5000, None, None)
        self._agent_done()

    def _agent_done(self):
        """Let the agent go once neither a search nor a pairing needs it."""
        if not self.searching and not self.pairing:
            AGENT.drop(self)

    def _on_nearby(self, _btn, path):
        prompt = self.prompts.get(path)
        if prompt and prompt[0] == "confirm":
            # The codes match: tell BlueZ yes, and the pairing goes on.
            self.prompts.pop(path)
            prompt[2].return_value(None)
            self.pending[path] = ("Pairing…", False)
            self.refresh()
            return
        if path in self.pairing or path in self.prompts:
            return
        dev = self._device(path)
        if dev is None:
            return
        self.pairing.add(path)
        self.pending[path] = ("Pairing…", False)
        dev.call("Pair", None, Gio.DBusCallFlags.NONE, PAIR_MS, None,
                 self._paired, path)
        self.refresh()

    def _paired(self, dev, result, path):
        self._drop_prompt(path)
        cancelled = path in self.cancelled
        legacy = path in self.legacy
        self.cancelled.discard(path)
        self.legacy.discard(path)
        try:
            dev.call_finish(result)
        except GLib.Error as err:
            if "AlreadyExists" not in err.message:  # paired meanwhile: on
                # A pairing cancelled with its × ends without a word: BlueZ
                # reports it as failed (a refused code is an auth failure).
                if cancelled:
                    self.pending.pop(path, None)
                elif legacy:
                    self.pending[path] = ("Wants a PIN: pair it in "
                                          "bluetoothctl", True)
                else:
                    self.pending[path] = (short_error(err), True)
                self.pairing.discard(path)
                self._agent_done()
                self.refresh()
                return
        # Trusted, so it may connect by itself from now on; then connected
        # (still pairing until _done hears the Connect).
        self._trust(dev)
        self._call(dev, "Connect")

    def _on_cancel(self, _btn, path):
        if path not in self.pairing:
            return
        self.cancelled.add(path)
        prompt = self.prompts.get(path)
        if prompt and prompt[0] == "confirm":
            self._drop_prompt(path)  # answers BlueZ no
            return
        dev = self._device(path)
        if dev is not None:
            dev.call("CancelPairing", None, Gio.DBusCallFlags.NONE, 5000,
                     None, None)

    def _drop_prompt(self, path):
        prompt = self.prompts.pop(path, None)
        if prompt and prompt[2] is not None:
            prompt[2].return_dbus_error("org.bluez.Error.Rejected",
                                        "Not confirmed")
        self.refresh()

    # --- the pairing agent's questions ---------------------------------

    def agent_asked(self, method, args, inv):
        """Answer the agent's question if it is about a device this card
        pairs; False leaves it to another card, or to a refusal."""
        path = args[0] if args else None
        if path not in self.pairing:
            return False
        if method == "RequestConfirmation" and not self.dead:
            self.prompts[path] = ("confirm", args[1], inv)
            self.refresh()
            return True  # answered by a click on the row, or Cancel
        if method in ("DisplayPasskey", "DisplayPinCode"):
            self.prompts[path] = ("type", args[1], None)
            self.refresh()
            inv.return_value(None)
            return True
        if method in ("RequestAuthorization", "AuthorizeService"):
            inv.return_value(None)
            return True
        # A device from before Bluetooth 2.1 that wants a PIN typed in
        # (RequestPinCode, RequestPasskey): the card has no field for one,
        # and _paired says so. A closed card can't show a code to compare.
        if method in ("RequestPinCode", "RequestPasskey"):
            self.legacy.add(path)
        inv.return_dbus_error("org.bluez.Error.Rejected", "Rejected")
        return True

    def agent_cancelled(self):
        """BlueZ dropped the code it asked to compare: the answer goes
        nowhere now."""
        for path, prompt in list(self.prompts.items()):
            if prompt[0] == "confirm":
                self._drop_prompt(path)

    def _on_destroy(self, _panel):
        """The card closed or gave way to another one, in a bar-popup that
        lives on: no code is left to compare, and the search stops; a
        pairing goes on, and the agent with it while it answers for one
        (_done or _paired let it go)."""
        for path in list(self.prompts):
            self._drop_prompt(path)
        self._stop_search()

    # --- the switch -------------------------------------------------------

    def _on_power(self, switch, _pspec):
        adapter = self._adapter()
        if adapter is None:
            return
        self.power_target = switch.get_active()
        if not self.power_target:
            self._give_up_waiting(None)
            if self.searching:
                self._stop_search()
        args = GLib.Variant("(ssv)", ("org.bluez.Adapter1", "Powered",
                                      GLib.Variant("b", self.power_target)))
        adapter.call("org.freedesktop.DBus.Properties.Set", args,
                     Gio.DBusCallFlags.NONE, 5000, None,
                     self._power_done, None)
        self.refresh()

    def _power_done(self, adapter, result, _data):
        try:
            adapter.call_finish(result)
            self.power_error = None
        except GLib.Error as err:
            self.power_target = None  # show the real state again
            # Said where it was asked for: on the rows waiting for the
            # adapter, in the search list, else under the title.
            if self.waiting or self.searching:
                self._give_up_waiting(short_error(err))
                if self.searching:
                    self.search_error = short_error(err)
            else:
                self.power_error = short_error(err)
        else:
            # Normally refresh() clears the target once BlueZ reports the
            # new state; this catches the case where it never does.
            if self.power_timer is not None:
                GLib.source_remove(self.power_timer)
            self.power_timer = GLib.timeout_add_seconds(POWER_UP_WAIT,
                                                        self._power_overdue)
        self.refresh()

    def _power_overdue(self):
        self.power_timer = None
        self.power_target = None
        self._give_up_waiting("Bluetooth didn't turn on")
        if self.searching and not self._powered():
            self.search_error = "Bluetooth didn't turn on"
        self.refresh()
        return GLib.SOURCE_REMOVE
