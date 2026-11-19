"""The Network card, `bar-popup network`: the connection in use, under a
sign-in banner while a captive portal holds it back, its status line
saying "No internet" or "Metered" when NetworkManager does, and on a
Wi-Fi network with a key NetworkManager keeps, a button that unfolds a
QR code a phone joins it by; the other Wi-Fi networks in range, the
strongest six with the rest folded behind More networks, to join (the
password field shows what was typed on request, and keeps it after a
failed try), or to forget if saved; and the connection's addresses,
folded behind Details, where a click copies one, with the latency to the
gateway and to the internet, measured as Details unfolds. The title row
has the Wi-Fi switch and a button that opens nmtui for what the card
leaves out (enterprise and hidden networks, VPNs, static addresses).

What the card shows comes from NetworkManager's object tree on the
system bus, read without blocking and kept current by its change signals
while the card is open, but for the key a QR code shares, which nmcli
reads when asked; what it changes, it changes through nmcli.
"""

import ipaddress
import socket
import struct
import threading
import time
from collections import namedtuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
from cardkit import (MOCHA, Arm, Panel, button, copy_flash,  # noqa: E402
                     hand, label, prop, rounded_rect, row, run_async, spawn,
                     tile)
import publicip  # noqa: E402

WIFI_MAX = 6    # other networks listed while More networks is folded
WIFI_MORE = 10  # how many more unfolding it lists, the strongest of the rest
SCAN_S = 15     # s "Scanning…" waits for a scan to land
PROFILES_MS = 150  # longest the card waits at open for its saved networks
QR_PX = 240     # most a QR code takes, its light margin included, px
QR_QUIET = 4    # that margin, in modules: the quiet zone scanners look for
PROBE_S = 2     # s a latency probe waits for its answer
# Where Details measures the internet's latency to: Cloudflare's resolver,
# anycast, so close to every network, and by address, so DNS takes no
# part in it.
INTERNET = {4: "1.1.1.1", 6: "2606:4700:4700::1111"}

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"

# NetworkManager's D-Bus enums and flags (libnm 1.58).
ETHERNET, WIFI = 1, 2                             # NMDeviceType
PREPARE, ACTIVATED, DEACTIVATING = 40, 100, 110   # NMDeviceState
CONNECTION_ACTIVATED = 2                          # NMActiveConnectionState
PORTAL, LIMITED = 2, 3                            # NMConnectivityState
METERED = {1, 3}                                  # NMMetered: yes, guess-yes
AP_PRIVACY = 0x1                                  # NM80211ApFlags
KEY_PSK, KEY_8021X, KEY_SAE = 0x100, 0x200, 0x400  # NM80211ApSecurityFlags
KEY_OWE, KEY_OWE_TM, KEY_SUITE_B = 0x800, 0x1000, 0x2000

# Nerd Font glyphs, the same ones the bar uses: the Wi-Fi signal in four
# steps, with a lock for the networks that want a password.
ICON = {
    "ethernet": "\U000f0200",
    "wifi": "\U000f0928",
    "wifi3": "\U000f0925",
    "wifi2": "\U000f0922",
    "wifi1": "\U000f091f",
    "wifi-lock": "\U000f092a",
    "wifi3-lock": "\U000f0927",
    "wifi2-lock": "\U000f0924",
    "wifi1-lock": "\U000f0921",
    "connecting": "\U000f092f",
    "offline": "\U000f092d",
    "wifi-off": "\U000f05aa",
    "portal": "\U000f059f",
    "disconnect": "\U000f0338",
    "link": "\U000f0337",
    "settings": "\U000f16c6",
    "forget": "\U000f09e7",
    "folded": "\U000f0142",
    "unfolded": "\U000f0140",
    "show": "\U000f0208",
    "hide": "\U000f0209",
    "qr": "\U000f0432",
}

CSS = b"""
/* Behind a captive portal: a yellow note on what to do, and the button
   that does it. */
.card.network .portal {
    background-color: alpha(@yellow, 0.12);
    border-radius: 12px;
    padding: 6px 10px;
}
.card.network .portal .icon,
.card.network .portal .name { color: @yellow; }
.card.network .signin {
    background-color: @mauve;
    background-image: none;
    color: @crust;
    font-weight: bold;
    border: none;
    box-shadow: none;
    border-radius: 10px;
    padding: 3px 12px;
}
.card.network .signin:hover { background-color: @lavender; }
/* The focus ring in base on the mauve fill, with the button's radius. */
.card.network .signin:focus {
    outline-color: @base;
    -gtk-outline-radius: 10px;
}

/* Forget is quieter than the buttons that join and leave. Armed, its row
   turns red, as the Power card's rows do, with their 10 % tint hovered or
   not: no hover colour behind the red text, and the buttons' hover in
   @raised, where the red glyph keeps 3.9:1. */
.card.network .forget { color: @subtext0; }
.card.network .row.on:not(.armed) .forget { color: @subtext1; }
.card.network .armed {
    background-color: alpha(@crit, 0.1);
    border-radius: 12px;
}
.card.network .armed .row:hover { background-color: transparent; }
.card.network .armed .iconbtn:hover { background-color: @raised; }
.card.network .armed .icon,
.card.network .armed .name,
.card.network .armed .sub,
.card.network .armed .forget { color: @crit; }

/* Details: key/value rows, tighter than list rows; a copied value shows
   green for a moment. */
.card.network .kv { padding: 3px 10px; min-height: 0; }
.card.network .key, .card.network .chevron { color: @subtext0; }
.card.network .row:hover .key,
.card.network .row:hover .chevron { color: @subtext1; }
.card.network .copied { color: @ok; }

/* The QR code under the Wi-Fi row in use, and the line on what it is for.
   The code draws itself, dark on light, as scanners want it. */
.card.network .qr { margin: 6px 0; }

/* The Wi-Fi password, lined up under the network's name, and the button
   that shows what was typed. */
.card.network .password { margin: 0 10px 4px 46px; }
.card.network entry {
    background-color: @raised;
    background-image: none;
    color: @text;
    caret-color: @rosewater;
    border: none;
    box-shadow: none;
    border-radius: 10px;
    padding: 6px 10px;
}
/* GTK draws no focus outline round an entry: the ring is a shadow, shown
   after a click too, as it marks where typing goes. */
.card.network entry:focus { box-shadow: inset 0 0 0 2px @accent; }
"""

# A row: what it stands for (key), its glyph, name and the line under
# the name (red if `bad`); what a click on it joins (ssid, wants a
# password, saved, enterprise); the SSID its Forget button forgets, the
# connection its Disconnect button ends, and the Profile its QR button
# shares. A row on screen changes glyph, sub and bad in place; a change to
# the rest rebuilds its section.
Row = namedtuple("Row", "key glyph name sub bad join forget disconnect share",
                 defaults=(None,))

# A saved Wi-Fi profile: its UUID and SSID, whether a QR code may share it
# (see shareable), and whether the network hides its name, which the code
# then says.
Profile = namedtuple("Profile", "uuid ssid shareable hidden")


class Section(namedtuple("Section", "extra rows")):
    """A part of the card: its rows and what else it shows (`extra`)."""

    def shape(self):
        """What the section is built from, but for what its rows change in
        place."""
        return self.extra, tuple((r.key, r.name, r.join, r.forget,
                                  r.disconnect, r.share) for r in self.rows)


def ssid_text(raw):
    """An SSID (bytes) as text; None for a hidden network's blank one."""
    raw = bytes(raw)
    return raw.decode("utf-8", "replace") if raw.strip(b"\0") else None


def band(mhz):
    """'5 GHz' for 5220 MHz, as nmcli names the bands."""
    if not mhz:
        return ""
    return "2.4 GHz" if mhz < 4900 else "5 GHz" if mhz < 5925 else "6 GHz"


def security(flags, wpa, rsn):
    """What nmcli's SECURITY column says for an access point: "WPA2",
    "WPA2 WPA3", ... (nmcli 1.58, devices.c)."""
    if flags & AP_PRIVACY and not wpa and not rsn:
        return "WEP"
    return " ".join(word for word, on in (
        ("WPA1", wpa),
        ("WPA2", rsn & (KEY_PSK | KEY_8021X)),
        ("WPA3", rsn & KEY_SAE),
        ("OWE", rsn & KEY_OWE),
        ("OWE-TM", rsn & KEY_OWE_TM and not rsn & KEY_OWE),
        ("802.1X", (wpa | rsn) & KEY_8021X),
        ("WPA-EAP-SUITE-B-192", (wpa | rsn) & KEY_SUITE_B)) if on)


def wants_password(flags, wpa, rsn):
    """nmcli's test for asking for a key: WEP and WPA networks want one,
    open and OWE ones (encrypted without a key) don't."""
    owe = KEY_OWE | KEY_OWE_TM
    return bool(flags & AP_PRIVACY or wpa and not wpa & owe
                or rsn and not rsn & owe)


def enterprise(wpa, rsn):
    """An 802.1X network with no password login (PSK or SAE) beside it:
    nmcli can't set one up (it asks for a password and fails), nmtui can."""
    keys = wpa | rsn
    return bool(keys & (KEY_8021X | KEY_SUITE_B)
                and not keys & (KEY_PSK | KEY_SAE))


def shareable(settings):
    """Whether a QR code may share a saved Wi-Fi profile, from its settings
    (the secrets left out): a WPA or WPA3 Personal network whose key
    NetworkManager keeps itself (psk-flags 0: not a keyring agent's, nor
    asked for each time), so nmcli can read it, under a name a phone takes
    as text. Open and OWE networks have no key to share, enterprise ones a
    login per person; WEP's keys (four slots, or a passphrase only
    NetworkManager hashes) the code can't always carry."""
    sec = settings.get("802-11-wireless-security", {})
    try:
        bytes(settings.get("802-11-wireless", {}).get("ssid", b"")).decode()
    except UnicodeDecodeError:
        return False
    return (sec.get("key-mgmt") in ("wpa-psk", "sae")
            and not sec.get("psk-flags", 0))


def wifi_icon(signal, lock=False):
    suffix = "-lock" if lock else ""
    for level, key in ((75, "wifi"), (50, "wifi3"), (25, "wifi2")):
        if signal >= level:
            return ICON[key + suffix]
    return ICON["wifi1" + suffix]


def link_speed(mbit):
    if mbit <= 0:
        return ""
    return f"{mbit / 1000:g} Gb/s" if mbit >= 1000 else f"{mbit} Mb/s"


def addresses(config):
    """'192.168.1.110/24'-style addresses of an IP4Config or IP6Config."""
    return [f"{a['address']}/{a['prefix']}"
            for a in prop(config, "AddressData", [])]


def nameservers(ip4, ip6):
    """The IPv4 name servers, else (an IPv6-only network) the IPv6 ones."""
    return ([d["address"] for d in prop(ip4, "NameserverData", [])
             if "address" in d]
            or [str(ipaddress.IPv6Address(bytes(raw)))
                for raw in prop(ip6, "Nameservers", [])])


def ping(addr, iface=""):
    """The round trip of one ICMP echo to `addr`, ms, or None if no answer
    comes within PROBE_S. From a ping socket, which needs no root: the
    kernel lets every group open one (net.ipv4.ping_group_range, set so by
    systemd's sysctl defaults, which NixOS installs), fills in the echo's
    identifier and checksum, and hands the socket only its own replies.
    `iface`: the interface a link-local IPv6 address is on (an IPv6
    gateway's), which the echo can't go out without."""
    v6 = ":" in addr
    # Type 8 (128 in ICMPv6), code 0, checksum and identifier (the
    # kernel's), sequence 1, and ping's 56 bytes of data.
    echo = struct.pack("!BBHHH", 128 if v6 else 8, 0, 0, 0, 1) + bytes(56)
    try:
        with socket.socket(socket.AF_INET6 if v6 else socket.AF_INET,
                           socket.SOCK_DGRAM,
                           socket.IPPROTO_ICMPV6 if v6
                           else socket.IPPROTO_ICMP) as sock:
            sock.settimeout(PROBE_S)
            to = ((addr, 0, 0, socket.if_nametoindex(iface) if iface else 0)
                  if v6 else (addr, 0))
            start = time.monotonic()
            sock.sendto(echo, to)
            sock.recv(128)
            return (time.monotonic() - start) * 1000
    except OSError:  # no answer, no route, or ping sockets not allowed
        return None


def handshake(addr, port=443):
    """The round trip of a TCP handshake with `addr`, ms, or None if it
    fails or takes over PROBE_S: connect() returns as the SYN-ACK comes
    in. TCP to the HTTPS port, as networks that drop pings to the internet
    still let HTTPS through."""
    try:
        with socket.socket(socket.AF_INET6 if ":" in addr
                           else socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(PROBE_S)
            start = time.monotonic()
            sock.connect((addr, port))
            return (time.monotonic() - start) * 1000
    except OSError:
        return None


def ms_text(ms):
    """'4 ms', or '<1 ms' (a wired network's gateway); 'no reply' for
    None."""
    if ms is None:
        return "no reply"
    return "<1 ms" if ms < 1 else f"{ms:.0f} ms"


def tile_labels(box):
    """The glyph and status line of a cardkit.tile, to change in place."""
    glyph, text = box.get_children()
    return glyph, text.get_children()[-1]


def set_class(widget, name, on):
    ctx = widget.get_style_context()
    (ctx.add_class if on else ctx.remove_class)(name)


def reveal(entry, btn, shown):
    """Show or hide what a password field holds; its eye button `btn`
    then offers the other."""
    entry.set_visibility(shown)
    btn.get_child().set_text(ICON["hide" if shown else "show"])
    btn.set_tooltip_text("Hide password" if shown else "Show password")


def resume(entry, caret):
    """Focus a password field drawn anew mid-typing, the caret where it
    was, nothing selected."""
    entry.grab_focus_without_selecting()
    entry.set_position(caret)
    return GLib.SOURCE_REMOVE


def draw_qr(_area, cr, modules, unit):
    """A QR code: its dark modules, `unit` px each, on a light square
    QR_QUIET modules wider all round. Scanners look for dark on light, and
    the card is dark: crust on text, 13:1."""
    side = unit * (len(modules) + 2 * QR_QUIET)
    rounded_rect(cr, 0, 0, side, side, 2 * unit)
    cr.set_source_rgb(*MOCHA["text"])
    cr.fill()
    for y, line in enumerate(modules, QR_QUIET):
        for x, dark in enumerate(line, QR_QUIET):
            if dark:
                cr.rectangle(x * unit, y * unit, unit, unit)
    cr.set_source_rgb(*MOCHA["crust"])
    cr.fill()
    return False


def nm_error(stderr):
    """Last line of nmcli's complaint, made readable."""
    lines = stderr.strip().splitlines()
    msg = lines[-1].removeprefix("Error: ") if lines else "Failed"
    if "Secrets were required" in msg or "password" in msg.lower():
        return "Wrong or missing password"
    if "No network with SSID" in msg:
        return "Out of range"
    return msg[:60]


class NetworkPanel(Panel):
    title = "Network"
    css = "network"

    def __init__(self, app):
        super().__init__(app)
        self.status = {}          # ssid -> (text, is_error) after a click
        self.password_for = None  # ssid whose password field is open
        self.typing = None        # ssid whose password field is drawn
        self.entry = None         # that field
        self.typed = {}           # ssid -> the password its failed try had
        self.draft = None         # a field rebuilt mid-typing, see fill_wifi
        # Forget's two clicks; the keys are SSIDs.
        self.arm = Arm(self, lambda _old, _new: self._changed())
        self.radio_target = None  # like BluetoothPanel.power_target
        self.scan_from = None     # LastScan when a scan was asked for
        self.details_open = False
        self.more_open = False    # whether More networks is unfolded
        self.more_count = 0       # how many networks it holds
        self.more_label = None    # its title, whose count changes in place
        self.refold = False       # More networks was clicked, see _update
        self.profiles = {}        # settings path -> Profile, Wi-Fi only
        self.waiting = set()      # profiles asked at open that haven't answered
        self.ready = False        # whether the card shows the tree yet
        self.public = None        # publicip's addresses for public_key
        self.public_key = None    # the local addresses they go with
        self.looked_up = False    # whether public_key's lookup has started
        self.qr_for = None        # UUID of the connection whose code shows
        # What its fold shows: ("code", modules) or ("error", text); None
        # while the code is being made.
        self.qr = None
        self.qr_ask = 0           # counts the asks; a late answer is dropped
        self.latency = {}         # "Gateway", "Internet" -> what Details says
        self.latency_key = None   # the addresses it was measured on
        self.latency_run = 0      # counts the measurements, likewise
        self.shapes = {}          # section -> what it was last built from
        self.labels = {"top": {}, "wifi": {}}  # row key -> widgets to update
        self.order = []           # the SSIDs the list shows, in its order
        self.nm = None            # NetworkManager's tree, once it has come
        self._handlers = []
        self._update_id = None
        self._ready_id = None

        # The switch, the network list and Details come and go with the
        # Wi-Fi device and the connection, so the card shows and hides them
        # itself; no_show_all keeps bar-popup's show_all off them.
        self.switch = hand(Gtk.Switch())
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.set_no_show_all(True)
        self.header.pack_end(self.switch, False, False, 0)
        self._switch_id = self.switch.connect("notify::active",
                                              self._on_radio)
        # nmtui does what the card leaves out: enterprise and hidden
        # networks, VPNs, static addresses. Left of the switch; hidden
        # while NetworkManager isn't running, as nmtui needs it.
        self.settings = self.header_button(
            ICON["settings"], "Network settings (nmtui)", "kitty", "nmtui")
        self.settings.get_child().show()  # bar-popup's show_all skips it
        self.settings.set_no_show_all(True)
        # Under the body (the connection in use), each in a box of its own
        # that is rebuilt on its own: a password being typed into the list
        # survives changes to the rest.
        self.wifi = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=4)
        for box in (self.wifi, self.details):
            box.set_no_show_all(True)
            self.pack_start(box, False, False, 0)

        # The tree comes in the background, in a few tens of ms, while the
        # card paints its title row; the rest follows once the tree and the
        # saved profiles are in (_show). DO_NOT_AUTO_START: a card never
        # starts NetworkManager.
        self._cancel = Gio.Cancellable()
        self.connect("destroy", self._on_destroy)
        Gio.DBusObjectManagerClient.new_for_bus(
            Gio.BusType.SYSTEM,
            Gio.DBusObjectManagerClientFlags.DO_NOT_AUTO_START,
            NM, "/org/freedesktop", None, None, self._cancel, self._got_tree)

    def _got_tree(self, _source, result):
        try:
            nm = Gio.DBusObjectManagerClient.new_for_bus_finish(result)
        except GLib.Error:  # no system bus, or the card closed first
            nm = None
        if self.dead:
            return
        if nm is None:
            self._show()  # says NetworkManager is not running
            return
        self.nm = nm
        self._handlers = [
            nm.connect("object-added", self._on_object, True),
            nm.connect("object-removed", self._on_object, False),
            nm.connect("interface-proxy-properties-changed",
                       lambda *_: self._changed()),
            nm.connect("interface-proxy-signal", self._on_signal),
        ]
        self._scan()
        self._recheck()
        self._read_profiles()

    # --- NetworkManager's tree ---------------------------------------------

    def _get(self, path, iface=""):
        """One interface (the manager's own by default) of one of
        NetworkManager's objects, or None."""
        if self.nm is None or not path or path == "/":
            return None
        return self.nm.get_interface(path, f"{NM}.{iface}" if iface else NM)

    def _all(self, iface):
        if self.nm is None:
            return []
        name = f"{NM}.{iface}"
        return [p for o in self.nm.get_objects()
                if (p := o.get_interface(name)) is not None]

    def _wifi_device(self):
        """The Wi-Fi device (the one in use, if there are more), or None."""
        devices = [d for d in self._all("Device")
                   if prop(d, "DeviceType") == WIFI]
        devices.sort(key=lambda d: (prop(d, "ActiveConnection", "/") == "/",
                                    prop(d, "Interface", "")))
        return devices[0] if devices else None

    def _wireless(self, dev):
        return dev and self._get(dev.get_object_path(), "Device.Wireless")

    def _call(self, path, iface, method, args=None, done=None, data=None):
        """Call a method of one of NetworkManager's objects by the bus name,
        not through its proxy: a proxy the tree makes while NetworkManager
        starts up (after a restart) may not know whom to call."""
        self.nm.get_connection().call(
            NM, path, f"{NM}.{iface}" if iface else NM, method, args, None,
            Gio.DBusCallFlags.NONE, -1, None, done, data)

    def _read_profiles(self):
        """Ask every saved profile for its settings at once (the SSID is no
        property). The card shows the tree once they have all answered, a
        few ms, PROFILES_MS at most, so it opens with its saved networks
        marked; a profile that answers later updates it then."""
        for obj in self.nm.get_objects():
            if self._read_profile(obj, self._answered):
                self.waiting.add(obj.get_object_path())
        if self.waiting:
            self._ready_id = GLib.timeout_add(PROFILES_MS,
                                              self._profiles_lapsed)
        else:
            self._show()

    def _answered(self, path):
        self.waiting.discard(path)
        if not self.waiting:
            self._show()

    def _profiles_lapsed(self):
        self._ready_id = None
        self._show()
        return GLib.SOURCE_REMOVE

    def _show(self):
        """Show what the tree says, from now on; the first time at once."""
        if self._ready_id is not None:
            GLib.source_remove(self._ready_id)
            self._ready_id = None
        if not self.ready and not self.dead:
            self.ready = True
            self._update()

    def _read_profile(self, obj, done=None):
        """Ask a saved profile for its settings, then call done(path);
        False if `obj` is no profile."""
        if obj.get_interface(f"{NM}.Settings.Connection") is None:
            return False
        path = obj.get_object_path()
        self._call(path, "Settings.Connection", "GetSettings", None,
                   self._got_profile, (path, done))
        return True

    def _got_profile(self, bus, result, data):
        path, done = data
        try:
            settings = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            settings = None
        if self.dead:
            return  # the card closed meanwhile
        if settings is not None and self.nm.get_object(path) is not None:
            wireless = settings.get("802-11-wireless", {})
            ssid = ssid_text(wireless.get("ssid", b""))
            if ssid is None:
                self.profiles.pop(path, None)
            else:
                self.profiles[path] = Profile(
                    settings["connection"]["uuid"], ssid,
                    shareable(settings), bool(wireless.get("hidden", False)))
        if done is not None:
            done(path)
        self._changed()

    def _on_object(self, _nm, obj, added):
        if added:
            self._read_profile(obj)
        else:
            self.profiles.pop(obj.get_object_path(), None)
        self._changed()

    def _on_signal(self, _nm, obj, proxy, _sender, signal, _params):
        if (signal == "Updated" and proxy.get_interface_name()
                == f"{NM}.Settings.Connection"):
            self._read_profile(obj)  # its SSID may have changed

    def _on_destroy(self, _panel):
        self._cancel.cancel()  # a tree still on its way
        if self._ready_id is not None:
            GLib.source_remove(self._ready_id)
            self._ready_id = None
        for handler in self._handlers:
            self.nm.disconnect(handler)

    # --- What the card shows ----------------------------------------------

    def _changed(self):
        """Update on idle: a scan or a reconnect sends a burst of signals,
        which makes one update. Nothing before _show."""
        if self._update_id is None and self.ready and not self.dead:
            self._update_id = GLib.idle_add(self._update)

    def _update(self):
        """Read the tree and redraw what changed. A row whose glyph or
        status changed changes in place, so a button under the pointer
        stays put (the signal in use changes every few seconds); a section
        whose rows changed is rebuilt, but not the network list while a
        password is being typed into it."""
        self._update_id = None
        if self.dead:
            return GLib.SOURCE_REMOVE
        mgr = self._get(NM_PATH)
        wifi_dev = self._wifi_device()
        hw = prop(mgr, "WirelessHardwareEnabled", True)
        radio = bool(prop(mgr, "WirelessEnabled", False)) and hw
        # nmcli returns before NetworkManager reports the new state; until
        # then the switch stays where it was put, as BluetoothPanel's does.
        if self.radio_target is not None:
            if radio == self.radio_target:
                self.radio_target = None
            else:
                radio = self.radio_target
        self.switch.set_visible(wifi_dev is not None)
        self.switch.set_sensitive(hw)
        self.settings.set_visible(mgr is not None)
        with self.switch.handler_block(self._switch_id):
            self.switch.set_active(radio)

        self.conns, in_use, conn = self._connections(mgr, wifi_dev, radio)
        self._sync("top", self.conns, self._rebuild)
        self.nets = self._networks(wifi_dev, radio, in_use)
        # Not while a password is typed into the field fill_wifi drew,
        # unless More networks was clicked: fill_wifi carries the typing
        # over to the field it draws anew.
        self._sync("wifi", self.nets, self.fill_wifi,
                   frozen=self.typing is not None
                   and self.typing == self.password_for and not self.refold)
        self.refold = False
        if self.more_label is not None:
            self.more_label.set_text(f"More networks · {self.more_count}")
        details = self._details(conn)
        if details != self.shapes.get("details"):
            self.shapes["details"] = details
            self.fill_details()
        return GLib.SOURCE_REMOVE

    def _sync(self, name, section, build, frozen=False):
        """Rebuild a section whose shape changed (unless frozen), else set
        the glyphs and status lines that changed where they are."""
        shape = section and section.shape()
        if shape != self.shapes.get(name) and not frozen:
            self.shapes[name] = shape
            build()
            return
        for r in section.rows if section else ():
            widgets = self.labels[name].get(r.key)
            if widgets is None:
                continue  # not on screen: the list is frozen
            box, glyph, sub = widgets
            if glyph.get_text() != r.glyph:
                glyph.set_text(r.glyph)
            if sub.get_text() != r.sub:
                sub.set_text(r.sub)
            set_class(sub, "bad", r.bad)
            set_class(box, "armed", r.forget is not None
                      and r.forget == self.arm.key)

    def _status(self, ssid, normal):
        """A network's status line and whether it is an error: what a
        click started or what went wrong, an armed Forget, else `normal`."""
        if ssid in self.status:
            return self.status[ssid]
        if ssid == self.arm.key:
            return "Click again to forget", False
        return normal, False

    def _connections(self, mgr, wifi_dev, radio):
        """The top section: a sign-in banner behind a captive portal, then
        the connections in use, the primary one first, with the QR code
        under the one sharing it, or a note on why there is none. Also the
        SSIDs in use, and the active connection Details is about."""
        if mgr is None:
            self._drop_qr()
            note = (ICON["offline"], "NetworkManager is not running", None)
            return Section((False, note, None), []), set(), None
        saved = {p.ssid for p in self.profiles.values()}
        primary = prop(mgr, "PrimaryConnection", "/")
        rows, in_use, conns = [], set(), []
        for dev in self._all("Device"):
            kind, state = prop(dev, "DeviceType"), prop(dev, "State", 0)
            path = prop(dev, "ActiveConnection", "/")
            ac = self._get(path, "Connection.Active")
            # Other kinds of link (a Bluetooth or modem tether, a VPN
            # device, a bridge) count while they hold the default route,
            # so the card never says "Not connected" over a working
            # connection; loopback never holds it.
            if (ac is None or not PREPARE <= state <= DEACTIVATING
                    or kind not in (ETHERNET, WIFI) and path != primary):
                continue
            busy = ("Disconnecting…" if state == DEACTIVATING
                    else "Connecting…" if state < ACTIVATED else None)
            if kind == WIFI:
                item = self._wifi_row(dev, ac, busy, saved)
                in_use.add(item.name)
            elif kind == ETHERNET:
                item = self._wired_row(dev, ac, busy)
            else:
                item = self._other_row(ac, busy)
            rows.append((path != primary, item))
            if state == ACTIVATED:
                conns.append(path)
        rows = [item for _later, item in sorted(rows, key=lambda r: r[0])]
        # The code goes with its connection, and once it can't be shared.
        if all(r.share is None or r.key != self.qr_for for r in rows):
            self._drop_qr()
        portal = (prop(mgr, "Connectivity", 0) == PORTAL
                  and bool(prop(mgr, "ConnectivityCheckUri", "")))
        note = None
        if not rows:
            if wifi_dev is not None and not radio:
                note = (ICON["wifi-off"], "Wi-Fi is off",
                        None if prop(mgr, "WirelessHardwareEnabled", True)
                        else "Blocked by a hardware switch")
            else:
                note = (ICON["offline"], "Not connected", None)
        # Details is about the primary connection (the default route's),
        # else the one connection there is.
        conn = (primary if self._get(primary, "Connection.Active")
                else conns[0] if conns else None)
        return Section((portal, note, self.qr), rows), in_use, conn

    def _wifi_row(self, dev, ac, busy, saved):
        """The Wi-Fi connection in use: its name and "5 GHz · 70% · WPA2"
        from the access point it is on, after what NetworkManager knows
        past the link (_verdict), or what it is busy with."""
        ap = self._get(prop(self._wireless(dev), "ActiveAccessPoint"),
                       "AccessPoint")
        name = ((ssid_text(prop(ap, "Ssid", b"")) if ap else None)
                or prop(ac, "Id", "Wi-Fi"))
        word, alarm = self._verdict(dev)
        if ap is None:
            glyph, about = ICON["wifi"], word or "Connected"
        else:
            strength = prop(ap, "Strength", 0)
            glyph = wifi_icon(strength)
            about = " · ".join(x for x in (
                word, band(prop(ap, "Frequency", 0)), f"{strength}%",
                security(prop(ap, "Flags", 0), prop(ap, "WpaFlags", 0),
                         prop(ap, "RsnFlags", 0))) if x)
        sub, bad = self._status(name, busy or about)
        uuid = prop(ac, "Uuid", "")
        # The QR button, once connected, if the profile's key can be read.
        profile = self.profiles.get(prop(ac, "Connection", "/"))
        share = (profile if not busy and profile is not None
                 and profile.shareable else None)
        return Row(uuid, ICON["connecting"] if busy else glyph, name, sub,
                   bad or alarm and sub == about, None,
                   name if name in saved else None, uuid, share)

    def _wired_row(self, dev, ac, busy):
        wired = self._get(dev.get_object_path(), "Device.Wired")
        word, alarm = self._verdict(dev)
        about = " · ".join(x for x in (
            word, "Ethernet", link_speed(prop(wired, "Speed", 0))) if x)
        return Row(prop(ac, "Uuid", ""), ICON["ethernet"],
                   prop(ac, "Id", "") or "Ethernet", busy or about,
                   alarm and not busy, None, None, None)

    def _other_row(self, ac, busy):
        return Row(prop(ac, "Uuid", ""), ICON["link"],
                   prop(ac, "Id", "") or "Connection", busy or "Connected",
                   False, None, None, None)

    def _verdict(self, dev):
        """What NetworkManager knows past the link, first on the status
        line so it never ellipsizes away: "No internet" (red) while its
        check reaches the network but not the internet, else "Metered" on
        a network that counts the data (NetworkManager's guess or the
        profile's setting). The better of the IPv4 and IPv6 checks counts,
        so an IPv6-only network is online."""
        reach = max(prop(dev, "Ip4Connectivity", 0),
                    prop(dev, "Ip6Connectivity", 0))
        if reach == LIMITED:
            return "No internet", True
        if prop(dev, "Metered", 0) in METERED:
            return "Metered", False
        return None, False

    def _networks(self, wifi_dev, radio, in_use):
        """The list section: the Wi-Fi networks in range but not in use,
        strongest first as the card opens, the first WIFI_MAX of them
        unless More networks is unfolded; None while Wi-Fi is off or
        absent. A row on screen keeps its place, so a scan's new strengths
        never move one under the pointer: the networks a scan finds join
        below the rows on screen. Behind the folded More networks nothing
        is on screen, so there they line up strongest first, a network
        found after the card opened included."""
        if wifi_dev is None or not radio:
            self.order = []
            return None
        wireless = self._wireless(wifi_dev)
        if (self.scan_from is not None
                and prop(wireless, "LastScan", -1) != self.scan_from):
            self.scan_from = None  # the scan landed
        # ssid -> (strength, wants a password, enterprise), its strongest AP
        best = {}
        for path in prop(wireless, "AccessPoints", []):
            ap = self._get(path, "AccessPoint")
            ssid = ssid_text(prop(ap, "Ssid", b"")) if ap else None
            strength = prop(ap, "Strength", 0)
            if (ssid is not None and ssid not in in_use
                    and strength > best.get(ssid, (-1,))[0]):
                wpa, rsn = prop(ap, "WpaFlags", 0), prop(ap, "RsnFlags", 0)
                best[ssid] = (strength,
                              wants_password(prop(ap, "Flags", 0), wpa, rsn),
                              enterprise(wpa, rsn))
        on_screen = self.order[:WIFI_MAX + (WIFI_MORE if self.more_open else 0)]
        kept = [ssid for ssid in on_screen if ssid in best]
        self.order = kept + sorted(
            (ssid for ssid in best if ssid not in kept),
            key=lambda ssid: (-best[ssid][0], ssid.lower()))
        listed = self.order[:WIFI_MAX + WIFI_MORE]
        self.more_count = max(0, len(listed) - WIFI_MAX)
        saved = {p.ssid for p in self.profiles.values()}
        rows = []
        for ssid in listed if self.more_open else listed[:WIFI_MAX]:
            strength, secure, eap = best[ssid]
            kind = ("Saved" if ssid in saved else "Enterprise" if eap
                    else "" if secure else "Open")
            sub, bad = self._status(ssid, " · ".join(
                x for x in (f"{strength}%", kind) if x))
            rows.append(Row(ssid, wifi_icon(strength, secure), ssid, sub, bad,
                            (ssid, secure, ssid in saved, eap),
                            ssid if ssid in saved else None, None))
        heading = "Other networks" if in_use else "Wi-Fi networks"
        empty = (None if rows else "Scanning…" if self.scan_from is not None
                 else "No networks found")
        return Section((heading, empty, self.password_for,
                        self.more_count > 0, self.more_open), rows)

    def _details(self, conn):
        """Details: whether it is unfolded, and then its address rows and
        the latency; None without a connection. Unfolding it looks the
        public addresses up, once per network (until they come, publicip's
        cache stands in if it was made on this network), and measures the
        latency: each time it unfolds, and again for a new network while it
        is, but never for a mere update."""
        ac = self._get(conn, "Connection.Active")
        if ac is None or prop(ac, "State", 0) != CONNECTION_ACTIVATED:
            self.latency_key = None  # back on the same network, it measures
            return None
        ip4 = self._get(prop(ac, "Ip4Config", "/"), "IP4Config")
        ip6 = self._get(prop(ac, "Ip6Config", "/"), "IP6Config")
        v4 = addresses(ip4)
        v6 = [a for a in addresses(ip6)
              if not ipaddress.ip_interface(a).is_link_local]
        key = (tuple(v4), tuple(v6))
        if key != self.public_key:  # another network, or new addresses
            self.public_key, self.looked_up = key, False
            self.public = publicip.cached()
        if not self.details_open:
            self.latency_key = None  # unfolding measures anew
            return False, (), None
        if not self.looked_up:
            self.looked_up = True
            threading.Thread(target=self._lookup, args=(key,),
                             daemon=True).start()
        public = self.public or {}
        # The public IPv6 address is normally one of ours (no NAT): it is
        # shown as ours, and on its own only when it isn't.
        pub6 = public.get("ipv6")
        ours = {a.split("/")[0]: a for a in v6}
        rows = [("IPv4", v4[0] if v4 else None),
                ("Public", public.get("ipv4")),
                ("IPv6", ours.get(pub6) or (v6[0] if v6 else None))]
        if pub6 and pub6 not in ours:
            rows.append(("Public IPv6", pub6))
        gateway = (prop(ip4, "Gateway", "") or prop(ip6, "Gateway", "")
                   or None)
        rows += [("Gateway", gateway),
                 ("DNS", ", ".join(nameservers(ip4, ip6)) or None)]
        if key != self.latency_key:
            self.latency_key = key
            # The internet by the gateway's family (an IPv6-only network's
            # is IPv6), else by the addresses'.
            self._measure(ac, gateway, (6 if ":" in gateway else 4)
                          if gateway else 4 if v4 else 6)
        # One line each: side by side, a slow or silent one would ellipsize.
        return True, tuple(rows), "\n".join(
            f"{name} {text}" for name, text in self.latency.items())

    def _lookup(self, key):
        GLib.idle_add(self._looked_up, key, publicip.fetch())

    def _looked_up(self, key, public):
        # A failed lookup leaves what the cache had for this network.
        if not self.dead and key == self.public_key and public is not None:
            self.public = public
            self._changed()
        return GLib.SOURCE_REMOVE

    def _measure(self, ac, gateway, family):
        """Details' latency: one ICMP echo to the gateway and one TCP
        handshake with the internet (INTERNET), side by side, each in a
        thread of its own, whose answer comes in through _probed. An echo
        to a link-local IPv6 gateway goes out on the connection's device
        (NetworkManager names its interface; the kernel numbers it)."""
        self.latency_run += 1
        self.latency = {}
        probes = []
        if gateway:
            devices = prop(ac, "Devices", [])
            dev = self._get(devices[0], "Device") if devices else None
            iface = prop(dev, "IpInterface", "") or prop(dev, "Interface", "")
            probes.append(("Gateway", ping, (gateway, iface)))
        probes.append(("Internet", handshake, (INTERNET[family],)))
        for name, probe, args in probes:
            self.latency[name] = "…"
            threading.Thread(target=self._probe, daemon=True, args=(
                self.latency_run, name, probe, args)).start()

    def _probe(self, run, name, probe, args):
        # In its thread: a probe waits for its answer, PROBE_S at most.
        GLib.idle_add(self._probed, run, name, ms_text(probe(*args)))

    def _probed(self, run, name, text):
        # An answer to an earlier measurement (on another network, or
        # before Details was folded and unfolded again) is dropped.
        if not self.dead and run == self.latency_run:
            self.latency[name] = text
            self._changed()
        return GLib.SOURCE_REMOVE

    # --- The sections' widgets ---------------------------------------------

    def fill(self):
        """The top: the sign-in banner, the connections in use (the QR
        code under the one sharing it), or a note on why there is none."""
        self.labels["top"] = {}
        portal, note, qr = self.conns.extra
        if portal:
            self.body.pack_start(self._banner(), False, False, 0)
        for r in self.conns.rows:
            self.body.pack_start(self._connection(r), False, False, 0)
            if qr is not None and r.key == self.qr_for:
                self.body.pack_start(self._qr_fold(qr), False, False, 0)
        if note is not None:
            box = tile(*note)
            box.get_style_context().add_class("static")
            self.body.pack_start(box, False, False, 0)

    def _banner(self):
        box = tile(ICON["portal"], "Sign in to network",
                   "No internet until you sign in")
        box.get_style_context().add_class("portal")
        go = button("Open", "signin")
        go.set_tooltip_text("Open the sign-in page in the browser")
        go.set_valign(Gtk.Align.CENTER)
        go.connect("clicked", self._on_sign_in)
        box.pack_end(go, False, False, 0)
        return box

    def _connection(self, r):
        """A connection in use, highlighted like a picked row. A click on
        it does nothing: leaving, forgetting and sharing are the buttons at
        its end."""
        box = Gtk.Box(spacing=4)
        for cls in ("row", "on"):
            box.get_style_context().add_class(cls)
        info = tile(r.glyph, r.name, r.sub, r.bad)
        box.pack_start(info, True, True, 0)
        if r.disconnect is not None:
            leave = button(ICON["disconnect"], "iconbtn")
            leave.set_tooltip_text("Disconnect")
            leave.set_valign(Gtk.Align.CENTER)
            leave.connect("clicked", self._on_disconnect, r.name,
                          r.disconnect)
            box.pack_end(leave, False, False, 0)
        self._forget_button(box, r)
        if r.share is not None:
            share = button(ICON["qr"], "iconbtn")
            share.set_tooltip_text(
                "Hide the QR code" if self.qr is not None
                and r.key == self.qr_for else "Share with a QR code")
            share.set_valign(Gtk.Align.CENTER)
            share.connect("clicked", self._on_qr, r.share)
            box.pack_end(share, False, False, 0)
        self.labels["top"][r.key] = (box, *tile_labels(info))
        return box

    def _forget_button(self, box, r):
        if r.forget is None:
            return
        forget = button(ICON["forget"], "iconbtn", "forget")
        forget.set_tooltip_text("Forget network")
        forget.set_valign(Gtk.Align.CENTER)
        forget.connect("clicked", self._on_forget, r.forget)
        box.pack_end(forget, False, False, 0)
        set_class(box, "armed", r.forget == self.arm.key)

    def _qr_fold(self, content):
        """Under the connection it shares: the QR code, centred, and what
        it is for; or why there is none. The code takes whole pixels per
        module, QR_PX at most, so its edges stay sharp."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("qr")
        kind, value = content
        if kind == "error":
            box.pack_start(label(value, "empty", "bad", xalign=0.5),
                           False, False, 0)
            return box
        modules = len(value) + 2 * QR_QUIET
        unit = max(2, QR_PX // modules)
        code = Gtk.DrawingArea()
        code.set_size_request(unit * modules, unit * modules)
        code.set_halign(Gtk.Align.CENTER)
        code.connect("draw", draw_qr, value, unit)
        box.pack_start(code, False, False, 0)
        box.pack_start(label("Scan with a phone's camera to join", "sub",
                             xalign=0.5), False, False, 0)
        return box

    def fill_wifi(self):
        """The list section: its heading, the networks (More networks
        after the first WIFI_MAX), and the password field under the one
        that asked for it. A field drawn anew while its password is typed
        (More networks was clicked) takes over what the old one held."""
        self.labels["wifi"] = {}
        if self.entry is not None and self.typing == self.password_for:
            self.draft = (self.typing, self.entry.get_text(),
                          self.entry.get_position(),
                          self.entry.get_visibility())
        self.typing = None  # the network whose password field is drawn
        self.entry = None
        self.more_label = None
        for child in self.wifi.get_children():
            child.destroy()
        if self.nets is None:
            self.wifi.hide()
            return
        heading, empty, _password_for, more, more_open = self.nets.extra
        self.wifi.pack_start(label(heading, "seclabel"), False, False, 0)
        for i, r in enumerate(self.nets.rows):
            if i == WIFI_MAX:  # unfolded: the fold's title, then the rest
                self.wifi.pack_start(self._more_row(True), False, False, 0)
            item = Gtk.Box(spacing=4)
            join = row(r.glyph, r.name, r.sub, sub_bad=r.bad)
            join.connect("clicked", self._on_network, *r.join)
            _ssid, _secure, saved, eap = r.join
            if eap and not saved:
                join.set_tooltip_text("Set up in nmtui")
            item.pack_start(join, True, True, 0)
            self._forget_button(item, r)
            self.labels["wifi"][r.key] = (item, *tile_labels(join.get_child()))
            self.wifi.pack_start(item, False, False, 0)
            if r.name == self.password_for:
                self.wifi.pack_start(self._password_field(r.name),
                                     False, False, 0)
                self.typing = r.name
        if more and not more_open:
            self.wifi.pack_start(self._more_row(False), False, False, 0)
        if empty:
            self.wifi.pack_start(label(empty, "empty"), False, False, 0)
        for child in self.wifi.get_children():
            child.show_all()
        self.wifi.show()

    def _more_row(self, unfolded):
        """The fold under the first WIFI_MAX networks, like Details."""
        head = Gtk.Box(spacing=6)
        self.more_label = label(f"More networks · {self.more_count}", "key")
        head.pack_start(self.more_label, False, False, 0)
        head.pack_start(label(ICON["unfolded" if unfolded else "folded"],
                              "chevron"), False, False, 0)
        toggle = button(head, "row", "kv")
        toggle.connect("clicked", self._on_more)
        return toggle

    def _password_field(self, ssid):
        """The password and the button that shows it. After a failed try
        it holds what was typed, all selected (as a focused entry is): a
        key replaces it, or the button shows the slip to mend. Drawn anew
        mid-typing, it goes on where the old one was."""
        box = Gtk.Box(spacing=4)
        box.get_style_context().add_class("password")
        entry = Gtk.Entry(visibility=False, placeholder_text="Password")
        entry.connect("activate", self._on_password, ssid)
        box.pack_start(entry, True, True, 0)
        show = button(ICON["show"], "iconbtn")
        show.set_valign(Gtk.Align.CENTER)
        show.connect("clicked", self._on_show, entry)
        box.pack_end(show, False, False, 0)
        if self.draft is not None and self.draft[0] == ssid:
            _ssid, text, caret, shown = self.draft
            self.draft = None
            entry.set_text(text)
            reveal(entry, show, shown)
            GLib.idle_add(resume, entry, caret)
        else:
            entry.set_text(self.typed.get(ssid, ""))
            reveal(entry, show, False)
            GLib.idle_add(entry.grab_focus)
        self.entry = entry
        return box

    def fill_details(self):
        """The Details row, which folds the addresses away; unfolded, a
        click on one copies it. Under them the latency, which a click
        leaves alone."""
        for child in self.details.get_children():
            child.destroy()
        details = self.shapes["details"]
        if details is None:
            self.details.hide()
            return
        unfolded, values, latency = details
        head = Gtk.Box(spacing=6)
        head.pack_start(label("Details", "key"), False, False, 0)
        head.pack_start(label(ICON["unfolded" if unfolded else "folded"],
                              "chevron"), False, False, 0)
        toggle = button(head, "row", "kv")
        toggle.connect("clicked", self._on_details)
        self.details.pack_start(toggle, False, False, 0)
        if unfolded:
            for key, value in values:
                self.details.pack_start(self._value(key, value),
                                        False, False, 0)
            self.details.pack_start(self._value("Latency", latency, False),
                                    False, False, 0)
        for child in self.details.get_children():
            child.show_all()
        self.details.show()

    def _value(self, key, value, copy=True):
        box = Gtk.Box(spacing=12)
        name = label(key, "key")
        name.set_size_request(78, -1)
        name.set_yalign(0.0)  # level with the first of a value's lines
        box.pack_start(name, False, False, 0)
        # Middle, so both ends of an IPv6 address stay readable.
        val = label(value or "—", ellipsize=Pango.EllipsizeMode.MIDDLE)
        box.pack_start(val, True, True, 0)
        if not copy:  # a row like the others, but no button
            box.get_style_context().add_class("kv")
            return box
        btn = button(box, "row", "kv")
        if value:
            btn.connect("clicked", self._copy, val, value)
        return btn

    # --- Actions -------------------------------------------------------------

    def _recheck(self):
        """Behind a captive portal or without internet, have NetworkManager
        check again: the sign-in may have happened since its last check, or
        the internet come back, and the next check can be minutes away.
        The answer comes in as a change signal."""
        if prop(self._get(NM_PATH), "Connectivity") in (PORTAL, LIMITED):
            self._call(NM_PATH, "", "CheckConnectivity")

    def _scan(self):
        """Ask for a fresh scan of the networks in range; what it finds
        comes in through the change signals."""
        wireless = self._wireless(self._wifi_device())
        if wireless is None or not prop(self._get(NM_PATH), "WirelessEnabled"):
            return
        self.scan_from = token = prop(wireless, "LastScan", -1)
        self._call(wireless.get_object_path(), "Device.Wireless",
                   "RequestScan", GLib.Variant("(a{sv})", ({},)),
                   self._scan_answered, token)
        GLib.timeout_add_seconds(SCAN_S, self._scan_lapsed, token)

    def _scan_answered(self, bus, result, token):
        try:
            bus.call_finish(result)
        except GLib.Error:  # refused, e.g. while the radio comes up
            self._scan_lapsed(token)

    def _scan_lapsed(self, token):
        if self.scan_from == token:
            self.scan_from = None
            self._changed()
        return GLib.SOURCE_REMOVE

    def _on_network(self, _btn, ssid, secure, saved, eap):
        self.arm.disarm()  # acting on another row drops an armed Forget
        if ssid in self.status and not self.status[ssid][1]:
            return  # already connecting
        if eap and not saved:
            # nmtui's editor sets one up: Add, Wi-Fi, security "WPA & WPA2
            # Enterprise".
            self._launch(None, ("kitty", "nmtui", "edit"))
        elif secure and not saved:
            # Click again to fold the password field away.
            self.password_for = None if self.password_for == ssid else ssid
            self.draft = None
            self._changed()
        else:
            self._join(ssid)

    def _on_password(self, entry, ssid):
        if not entry.get_text():
            return
        self.password_for = None
        self.draft = None
        self.typed[ssid] = entry.get_text()  # for a try that fails
        # nmcli takes the key as an argument, so it shows up in the
        # process list for the second or two the connect takes.
        self._join(ssid, "password", entry.get_text())

    def _join(self, ssid, *password):
        """`nmcli device wifi connect`. For a network without a profile,
        nmcli saves one before it tries and keeps it when the try fails (a
        mistyped password, out of range), which leaves a saved network that
        can't connect and that NetworkManager keeps trying by itself: that
        profile goes again, also after the card has closed (bar-popup
        stays running, so the try's end still arrives)."""
        known = None
        if all(p.ssid != ssid for p in self.profiles.values()):
            known = {p.get_object_path()
                     for p in self._all("Settings.Connection")}
        self._nmcli(ssid, "Connecting…", "device", "wifi", "connect", ssid,
                    *password, "ifname",
                    prop(self._wifi_device(), "Interface", ""), known=known)

    def _on_disconnect(self, _btn, ssid, uuid):
        self.arm.disarm()
        if ssid in self.status and not self.status[ssid][1]:
            return  # already disconnecting
        # `connection down` leaves the device free to join another saved
        # network by itself; `device disconnect` would stop all auto-joining
        # until the next manual connect.
        self._nmcli(ssid, "Disconnecting…", "connection", "down", "uuid", uuid)

    def _on_forget(self, _btn, ssid):
        """The first click arms Forget, a second one within ARM_MS deletes
        every saved profile of the network."""
        if ssid in self.status and not self.status[ssid][1]:
            return  # busy with it
        if not self.arm.confirm(ssid):
            return
        self.arm.disarm()
        uuids = [p.uuid for p in self.profiles.values() if p.ssid == ssid]
        if uuids:  # else it went meanwhile
            self._nmcli(ssid, "Forgetting…", "connection", "delete",
                        *(arg for uuid in uuids for arg in ("uuid", uuid)))

    def _on_qr(self, _btn, profile):
        """Unfold the QR code under the connection, or fold it away. Only
        now is the key read: nmcli asks NetworkManager, which gives it to
        whoever may change the profile."""
        self.arm.disarm()
        if self.qr_for == profile.uuid:
            self._drop_qr()
            self._changed()
            return
        self._drop_qr()
        self.qr_for, ask = profile.uuid, self.qr_ask
        # --escape no: the key as it is, a ":" or "\" in it unescaped.
        run_async(lambda ok, out, _err: self._got_key(ask, profile, ok, out),
                  "nmcli", "--show-secrets", "--escape", "no",
                  "--get-values", "802-11-wireless-security.psk",
                  "connection", "show", "uuid", profile.uuid)

    def _drop_qr(self):
        """Fold the QR code away; a key or code still on its way is
        dropped when it comes."""
        if self.qr_for is not None:
            self.qr_for = self.qr = None
            self.qr_ask += 1

    def _got_key(self, ask, profile, ok, out):
        if self.dead or ask != self.qr_ask:
            return  # the card closed, or the code was folded meanwhile
        # nmcli ends the key with a newline, and prints none at all when
        # NetworkManager gives none (not allowed to, or no key saved).
        key = out.removesuffix("\n")
        if not ok or not key:
            self._qr_done(ask, ("error", "Couldn't read the password"))
            return
        # Off the main loop: importing segno takes ~0.1 s, and making a
        # code tens of ms.
        threading.Thread(target=self._encode, args=(ask, profile, key),
                         daemon=True).start()

    def _encode(self, ask, profile, key):
        # Imported for the first code asked for: most opens show none.
        from segno.helpers import make_wifi
        # "WPA", the code's type for every WPA Personal key, sae included.
        code = make_wifi(profile.ssid, key, "WPA", profile.hidden)
        GLib.idle_add(self._qr_done, ask,
                      ("code", tuple(bytes(line) for line in code.matrix)))

    def _qr_done(self, ask, content):
        if not self.dead and ask == self.qr_ask:
            self.qr = content
            self._changed()
        return GLib.SOURCE_REMOVE

    def _nmcli(self, ssid, busy, *args, known=None):
        self.status[ssid] = (busy, False)
        self._changed()
        run_async(lambda ok, _out, err:
                  self._nmcli_done(ssid, ok, err, known), "nmcli", *args)

    def _nmcli_done(self, ssid, ok, err, known):
        if known is not None and not ok:  # a first join, see _join
            self._drop_new_profiles(ssid, known)
        if self.dead:
            return
        if ok:
            self.status.pop(ssid, None)
            self.typed.pop(ssid, None)
        else:
            self.status[ssid] = (nm_error(err), True)
            if "password" in self.status[ssid][0]:
                self.password_for = ssid  # let them type it (again)
        self._changed()

    def _drop_new_profiles(self, ssid, known):
        """Delete the profiles of `ssid` that came after `known`. One the
        card has read leaves it at once; one it hasn't (it came as the card
        closed) is asked for its SSID first."""
        for proxy in self._all("Settings.Connection"):
            path = proxy.get_object_path()
            if path in known:
                continue
            if path not in self.profiles:
                self._call(path, "Settings.Connection", "GetSettings", None,
                           self._drop_unread, ssid)
            elif self.profiles[path].ssid == ssid:
                spawn("nmcli", "connection", "delete", "uuid",
                      self.profiles.pop(path).uuid)

    def _drop_unread(self, bus, result, ssid):
        try:
            settings = bus.call_finish(result).unpack()[0]
        except GLib.Error:  # gone meanwhile
            settings = {}
        if ssid_text(settings.get("802-11-wireless", {})
                     .get("ssid", b"")) == ssid:
            spawn("nmcli", "connection", "delete", "uuid",
                  settings["connection"]["uuid"])

    def _on_radio(self, switch, _pspec):
        self.radio_target = target = switch.get_active()
        if target:
            # NetworkManager scans once the radio is up; until that scan
            # lands, an empty list says it is scanning.
            wireless = self._wireless(self._wifi_device())
            self.scan_from = token = prop(wireless, "LastScan", -1)
            GLib.timeout_add_seconds(SCAN_S, self._scan_lapsed, token)
        run_async(lambda ok, _out, _err: self._radio_done(ok, target),
                  "nmcli", "radio", "wifi", "on" if target else "off")

    def _radio_done(self, ok, target):
        if self.dead:
            return
        if not ok:
            self.radio_target = None  # show the real state again
        else:
            # Normally cleared by _update once NetworkManager reports it.
            GLib.timeout_add_seconds(3, self._radio_lapsed, target)
        self._changed()

    def _radio_lapsed(self, target):
        if self.radio_target == target:
            self.radio_target = None
            self._changed()
        return GLib.SOURCE_REMOVE

    def _on_details(self, _btn):
        self.details_open = not self.details_open
        self._changed()

    def _on_sign_in(self, _btn):
        # As the bar's "Sign in": the page NetworkManager checks, which a
        # portal answers with its sign-in page (how NetworkManager noticed
        # the portal), then a check every 10 s until the portal lets go.
        spawn("connectivity", "open")
        self.app.close()

    def _copy(self, _btn, val, value):
        copy_flash(val, value.split("/")[0])  # no prefix length

    def _on_more(self, _btn):
        self.more_open = not self.more_open
        self.refold = True
        self._changed()

    def _on_show(self, btn, entry):
        reveal(entry, btn, not entry.get_visibility())
        entry.grab_focus_without_selecting()
