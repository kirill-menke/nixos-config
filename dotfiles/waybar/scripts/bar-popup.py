#!/usr/bin/env python3
"""Catppuccin popups for the waybar network, bluetooth and sound modules.

`bar-popup network|bluetooth|sound` opens a card in the style of the pomodoro
popup, just under the bar and centred on the click. One process serves all
three panels: launching it again for the panel that is open closes it (a
second click on the same module), for another panel it swaps the card. Esc or
a click anywhere below the bar closes it too.
"""

import json
import re
import subprocess
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell, Pango  # noqa: E402,E501

CARD_W = 360    # fixed, so the card can be centred before it is drawn, px
CARD_TOP = 4    # gap under the bar's reserved area, px
EDGE = 8        # closest the card gets to a monitor edge, px
WIFI_MAX = 6    # nearby networks listed

PUBLIC_IP_CACHE = "/tmp/waybar-network-public-ip"  # written by network.sh

# Nerd Font glyphs, the same ones the bar uses.
ICON = {
    "ethernet": "\U000f0200",
    "wifi": "\U000f0928",
    "wifi3": "\U000f0925",
    "wifi2": "\U000f0922",
    "wifi1": "\U000f091f",
    "bluetooth": "\U000f00af",
    "bt-audio": "\U000f00b0",
    "headphones": "\U000f02cb",
    "speaker": "\U000f04c3",
    "phone": "\U000f03f2",
    "keyboard": "\U000f030c",
    "mouse": "\U000f037d",
    "gamepad": "\U000f02b4",
    "computer": "\U000f0379",
    "tv": "\U000f0502",
    "volume": "\U000f057e",
    "muted": "\U000f0581",
    "add": "\U000f0415",
    "mixer": "\U000f066a",
}

CSS = b"""
window { background-color: transparent; }
.backdrop { background-color: transparent; }

.card {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 18px;
    padding: 18px 20px;
}
.card.bluetooth { padding: 10px 20px 8px; }
.card.sound { padding-bottom: 10px; }

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

.empty { color: #a6adc8; padding: 6px 10px; }

/* The Propo variant gives icons their real width; in the plain one they
   overhang a narrower advance, which throws off centring. */
.icon {
    font-family: "FantasqueSansM Nerd Font Propo";
    font-size: 19px;
    color: #cba6f7;
}

.row, .iconbtn {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    outline: none;
    text-shadow: none;
    border-radius: 12px;
    color: #cdd6f4;
}

.row, .static { padding: 6px 10px; }
.kv { padding: 3px 10px; min-height: 0; }
.row:hover, .row.on { background-color: #45475a; }
.row.on .name { color: #cba6f7; font-weight: bold; }
.sub { color: #a6adc8; font-size: 12px; }
.sub.bad, .empty.bad { color: #f38ba8; }
.key { color: #a6adc8; }
.copied { color: #a6e3a1; }

.iconbtn {
    font-family: "FantasqueSansM Nerd Font Propo";
    font-size: 19px;
    padding: 2px 8px;
}
.iconbtn:hover { background-color: #45475a; }
.iconbtn.on { color: #f38ba8; }

.pill {
    background-color: #45475a;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: bold;
}
.pill.ok { color: #a6e3a1; }
.pill.bad { color: #f38ba8; }

switch {
    background-color: #45475a;
    background-image: none;
    border: none;
    border-radius: 10px;
    padding: 0;
    font-size: 0;
}
switch:checked { background-color: #cba6f7; }
switch slider {
    background-color: #cdd6f4;
    background-image: none;
    border: none;
    box-shadow: none;
    border-radius: 50%;
    min-width: 14px;
    min-height: 14px;
    margin: 2px;
}

entry {
    background-color: #45475a;
    background-image: none;
    color: #cdd6f4;
    caret-color: #f5e0dc;
    border: none;
    box-shadow: none;
    border-radius: 10px;
    padding: 6px 10px;
    margin: 0 10px 4px 46px;
}

scale trough {
    background-color: #45475a;
    background-image: none;
    border: none;
    border-radius: 3px;
    min-height: 6px;
}
scale highlight {
    background-color: #cba6f7;
    background-image: none;
    border: none;
    border-radius: 3px;
}
scale slider {
    background-color: #f5e0dc;
    background-image: none;
    border: none;
    box-shadow: none;
    border-radius: 50%;
    min-width: 16px;
    min-height: 16px;
    margin: -6px;
}
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


def label(text, *classes, xalign=0.0, ellipsize=None):
    lab = Gtk.Label(label=text, xalign=xalign)
    for cls in classes:
        lab.get_style_context().add_class(cls)
    if ellipsize is not None:
        lab.set_ellipsize(ellipsize)
    return lab


def button(child, *classes):
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_can_focus(False)
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


class Panel(Gtk.Box):
    """A card: a title row with icon buttons, and a body that refresh()
    rebuilds."""

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

    def fill(self):
        raise NotImplementedError

    def refresh(self):
        """Rebuild the body on idle, so a row can trigger it from its own
        click handler; repeated calls coalesce into one rebuild."""
        if self._refresh_id is None and not self.dead:
            self._refresh_id = GLib.idle_add(self._rebuild)

    def _rebuild(self):
        self._refresh_id = None
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


# --- Network ---------------------------------------------------------------

def nm_fields(*args):
    """`nmcli -t` output as (key, value) pairs; nmcli escapes ':' in
    values as '\\:', and numbers repeated keys like IP4.DNS[1]."""
    pairs = []
    for line in run("nmcli", "-t", *args).splitlines():
        key, _, value = line.partition(":")
        pairs.append((re.sub(r"\[\d+\]$", "", key),
                      value.replace("\\:", ":")))
    return pairs


def link_speed(dev):
    try:
        with open(f"/sys/class/net/{dev}/speed") as fh:
            mbit = int(fh.read())
    except (OSError, ValueError):
        return None
    if mbit <= 0:
        return None
    return f"{mbit / 1000:g} Gb/s" if mbit >= 1000 else f"{mbit} Mb/s"


def nm_split(line):
    """Fields of one `nmcli -t` line, unescaping '\\:' inside values."""
    return [f.replace("\\:", ":").replace("\\\\", "\\")
            for f in re.split(r"(?<!\\):", line)]


WIFI_LIST = ("nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
             "device", "wifi", "list")


def wifi_networks(listing):
    """The strongest access point per SSID from WIFI_LIST output, the one
    in use first, then by signal."""
    nets = {}
    for line in listing.splitlines():
        in_use, ssid, signal, security = (nm_split(line) + [""] * 4)[:4]
        if not ssid:
            continue  # hidden network
        net = nets.setdefault(ssid, {"ssid": ssid, "signal": 0,
                                     "secure": False, "in_use": False})
        if signal.isdigit():
            net["signal"] = max(net["signal"], int(signal))
        net["secure"] |= security.strip() not in ("", "--")
        net["in_use"] |= in_use == "*"
    return sorted(nets.values(),
                  key=lambda n: (not n["in_use"], -n["signal"]))[:WIFI_MAX]


def saved_ssids():
    """SSIDs that have a saved NetworkManager profile."""
    names = [f[0] for f in map(nm_split, run(
        "nmcli", "-t", "-f", "NAME,TYPE", "connection", "show").splitlines())
        if len(f) > 1 and f[1] == "802-11-wireless"]
    return {nm_split(run("nmcli", "-g", "802-11-wireless.ssid", "connection",
                         "show", name).strip())[0] for name in names}


def wifi_icon(signal):
    for level, key in ((75, "wifi"), (50, "wifi3"), (25, "wifi2")):
        if signal >= level:
            return ICON[key]
    return ICON["wifi1"]


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

    def __init__(self, app):
        super().__init__(app)
        self.pill = label("", "pill", xalign=0.5)
        self.pill.set_valign(Gtk.Align.CENTER)
        self.header.pack_end(self.pill, False, False, 0)
        self.fill()
        self.wifi_dev = next(
            (f[0] for f in map(nm_split, run(
                "nmcli", "-t", "-f", "DEVICE,TYPE", "device").splitlines())
             if len(f) > 1 and f[1] == "wifi"), None)
        if self.wifi_dev:
            self._wireless_section()

    def fill(self):
        dev = kind = None
        for line in run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE",
                        "device").splitlines():
            d, t, state = (line.split(":") + ["", ""])[:3]
            if state == "connected" and t in ("ethernet", "wifi"):
                dev, kind = d, t
                break

        self.pill.set_text("Connected" if dev else "Disconnected")
        ctx = self.pill.get_style_context()
        ctx.remove_class("bad" if dev else "ok")
        ctx.add_class("ok" if dev else "bad")
        if dev is None:
            self.body.pack_start(label("No active connection", "empty"),
                                 False, False, 0)
            return

        info = {}
        for key, value in nm_fields(
                "-f", "GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,"
                "IP6.ADDRESS", "device", "show", dev):
            info.setdefault(key, []).append(value)
        first = {k: v[0] for k, v in info.items() if v and v[0]}

        about = [first.get("GENERAL.CONNECTION", dev), dev]
        if kind == "wifi":
            for active, ssid, signal in (
                    (line.split(":") + ["", ""])[:3] for line in run(
                        "nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device",
                        "wifi").splitlines()):
                if active == "yes":
                    about = [ssid, f"{signal}%", dev]
        elif speed := link_speed(dev):
            about.append(speed)
        glyph = ICON["wifi" if kind == "wifi" else "ethernet"]
        info_tile = tile(glyph, about[0], " · ".join(about[1:]))
        info_tile.get_style_context().add_class("static")
        self.body.pack_start(info_tile, False, False, 0)

        ipv6 = next((a for a in info.get("IP6.ADDRESS", [])
                     if not a.lower().startswith("fe80")), None)
        try:
            with open(PUBLIC_IP_CACHE) as fh:
                pub4, _, pub6 = fh.read().strip().partition("|")
        except OSError:
            pub4 = pub6 = ""

        self.body.pack_start(label("Local", "seclabel"), False, False, 0)
        self._value("IPv4", first.get("IP4.ADDRESS"))
        self._value("IPv6", ipv6)
        self._value("Gateway", first.get("IP4.GATEWAY"))
        self._value("DNS", ", ".join(info.get("IP4.DNS", [])))
        self.body.pack_start(label("Public", "seclabel"), False, False, 0)
        self._value("IPv4", pub4)
        self._value("IPv6", pub6)

    def _value(self, key, value):
        box = Gtk.Box(spacing=12)
        name = label(key, "key")
        name.set_size_request(70, -1)
        box.pack_start(name, False, False, 0)
        # Middle, so both ends of an IPv6 address stay readable.
        val = label(value or "—", ellipsize=Pango.EllipsizeMode.MIDDLE)
        box.pack_start(val, True, True, 0)
        btn = button(box, "row", "kv")
        if value:
            btn.connect("clicked", self._copy, val, value)
        self.body.pack_start(btn, False, False, 0)

    # --- Wireless: nearby networks, connect/disconnect, radio switch -----

    def _wireless_section(self):
        head = Gtk.Box(spacing=8)
        head.pack_start(label("Wireless", "seclabel"), True, True, 0)
        self.wifi_switch = Gtk.Switch()
        self.wifi_switch.set_valign(Gtk.Align.CENTER)
        head.pack_end(self.wifi_switch, False, False, 0)
        self.pack_start(head, False, False, 0)
        # A box of its own: a finished scan rebuilds the list, but neither
        # the IPs above nor a password that is being typed.
        self.wifi = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pack_start(self.wifi, False, False, 0)
        self.wifi_status = {}   # ssid -> (status text, is_error)
        self.password_for = None
        self.saved = saved_ssids()
        self.nets = wifi_networks(run(*WIFI_LIST, "--rescan", "no"))
        self.scanning = False
        self._radio_id = self.wifi_switch.connect("notify::active",
                                                  self._on_radio)
        self.fill_wifi()
        self._rescan()

    def fill_wifi(self):
        for child in self.wifi.get_children():
            child.destroy()
        on = run("nmcli", "radio", "wifi").strip() == "enabled"
        with self.wifi_switch.handler_block(self._radio_id):
            self.wifi_switch.set_active(on)
        if not on:
            text = "Wi-Fi is off"
        elif not self.nets:
            text = "Scanning…" if self.scanning else "No networks found"
        else:
            text = None
        if text:
            self.wifi.pack_start(label(text, "empty"), False, False, 0)
        for net in self.nets if on else []:
            ssid = net["ssid"]
            if ssid in self.wifi_status:
                sub, bad = self.wifi_status[ssid]
            elif net["in_use"]:
                sub, bad = f"Connected · {net['signal']}%", False
            else:
                kind = ("Saved" if ssid in self.saved
                        else "Secured" if net["secure"] else "Open")
                sub, bad = f"{net['signal']}% · {kind}", False
            btn = row(wifi_icon(net["signal"]), ssid, sub, on=net["in_use"],
                      sub_bad=bad)
            btn.connect("clicked", self._on_network, net)
            self.wifi.pack_start(btn, False, False, 0)
            if ssid == self.password_for:
                entry = Gtk.Entry(visibility=False,
                                  placeholder_text="Password")
                entry.connect("activate", self._on_password, ssid)
                self.wifi.pack_start(entry, False, False, 0)
                GLib.idle_add(entry.grab_focus)
        self.wifi.show_all()

    def _rescan(self):
        self.scanning = True
        run_async(self._scan_done, *WIFI_LIST, "--rescan", "yes")

    def _scan_done(self, ok, out, _err):
        if self.dead:
            return
        self.scanning = False
        if ok:
            self.nets = wifi_networks(out)
        if self.password_for is None:
            self.fill_wifi()

    def _on_network(self, _btn, net):
        ssid = net["ssid"]
        if ssid in self.wifi_status and not self.wifi_status[ssid][1]:
            return  # already connecting/disconnecting
        if net["in_use"]:
            self._nmcli(ssid, "Disconnecting…",
                        "device", "disconnect", self.wifi_dev)
        elif net["secure"] and ssid not in self.saved:
            # Click again to fold the password field away.
            self.password_for = None if self.password_for == ssid else ssid
            self.fill_wifi()
        else:
            self._nmcli(ssid, "Connecting…", "device", "wifi", "connect",
                        ssid, "ifname", self.wifi_dev)

    def _on_password(self, entry, ssid):
        if not entry.get_text():
            return
        self.password_for = None
        # nmcli takes the key as an argument, so it shows up in the
        # process list for the second or two the connect takes.
        self._nmcli(ssid, "Connecting…", "device", "wifi", "connect", ssid,
                    "password", entry.get_text(), "ifname", self.wifi_dev)

    def _nmcli(self, ssid, busy, *args):
        self.wifi_status[ssid] = (busy, False)
        self.fill_wifi()
        run_async(lambda ok, _out, err: self._nmcli_done(ssid, ok, err),
                  "nmcli", *args)

    def _nmcli_done(self, ssid, ok, err):
        if self.dead:
            return
        if ok:
            self.wifi_status.pop(ssid, None)
        else:
            self.wifi_status[ssid] = (nm_error(err), True)
            if "password" in self.wifi_status[ssid][0]:
                self.password_for = ssid  # let them type it (again)
        self.saved = saved_ssids()
        self.nets = wifi_networks(run(*WIFI_LIST, "--rescan", "no"))
        self.fill_wifi()
        self.refresh()  # the connection and IPs above may have changed

    def _on_radio(self, switch, _pspec):
        state = "on" if switch.get_active() else "off"
        run_async(self._radio_done, "nmcli", "radio", "wifi", state)

    def _radio_done(self, _ok, _out, _err):
        if self.dead:
            return
        if self.wifi_switch.get_active():
            self._rescan()
        self.fill_wifi()

    def _copy(self, _btn, val, value):
        spawn("wl-copy", "--", value.split("/")[0])  # no prefix length
        val.set_text("Copied")
        val.get_style_context().add_class("copied")

        def restore():
            val.set_text(value)
            val.get_style_context().remove_class("copied")
        GLib.timeout_add(1200, restore)


# --- Bluetooth -------------------------------------------------------------

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
}


def prop(proxy, name, default=None):
    value = proxy.get_cached_property(name) if proxy else None
    return default if value is None else value.unpack()


def short_error(err):
    """'GDBus.Error:org.bluez.Error.Failed: br-connection-page-timeout'
    -> something a person can read."""
    msg = err.message.rsplit(": ", 1)[-1]
    if "page-timeout" in msg or "Host is down" in msg:
        return "Not in range or switched off"
    return msg[:60]


class BluetoothPanel(Panel):
    title = "Bluetooth"
    css = "bluetooth"

    def __init__(self, app):
        super().__init__(app)
        self.pending = {}   # device path -> (status text, is_error)
        self.power_error = None
        self.switch = Gtk.Switch()
        self.switch.set_valign(Gtk.Align.CENTER)
        self.header.pack_end(self.switch, False, False, 0)
        self._switch_id = self.switch.connect("notify::active",
                                              self._on_power)
        try:
            self.bluez = Gio.DBusObjectManagerClient.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusObjectManagerClientFlags.NONE,
                "org.bluez", "/", None, None, None)
        except GLib.Error:
            self.bluez = None
        else:
            for signal in ("object-added", "object-removed",
                           "interface-proxy-properties-changed"):
                self.bluez.connect(signal, lambda *_: self.refresh())
        self.header_button(ICON["add"], "Pair new device", "blueman-manager")
        self.fill()

    def _proxies(self, iface):
        if self.bluez is None:
            return []
        return [p for o in self.bluez.get_objects()
                if (p := o.get_interface(iface)) is not None]

    def fill(self):
        adapters = self._proxies("org.bluez.Adapter1")
        adapter = adapters[0] if adapters else None
        powered = bool(prop(adapter, "Powered", False))
        with self.switch.handler_block(self._switch_id):
            self.switch.set_active(powered)
        self.switch.set_sensitive(adapter is not None)
        if adapter is None:
            text = "Bluetooth service is not running"
        elif not powered:
            text = "Bluetooth is off"
        else:
            text = None
        if self.power_error:
            self.body.pack_start(label(self.power_error, "empty", "bad"),
                                 False, False, 0)
        if text:
            self.body.pack_start(label(text, "empty"), False, False, 0)
            return

        batteries = {p.get_object_path(): prop(p, "Percentage")
                     for p in self._proxies("org.bluez.Battery1")}
        devices = [d for d in self._proxies("org.bluez.Device1")
                   if prop(d, "Paired", False)]
        devices.sort(key=lambda d: (not prop(d, "Connected", False),
                                    str(prop(d, "Alias", "")).lower()))
        for dev in devices:
            path = dev.get_object_path()
            connected = bool(prop(dev, "Connected", False))
            if path in self.pending:
                sub, bad = self.pending[path]
            elif connected:
                sub, bad = "Connected", False
                if batteries.get(path) is not None:
                    sub += f" · {batteries[path]}%"
            else:
                sub, bad = "Not connected", False
            glyph = ICON[DEVICE_ICONS.get(prop(dev, "Icon", ""), "bluetooth")]
            btn = row(glyph, prop(dev, "Alias", path), sub, on=connected,
                      sub_bad=bad)
            btn.connect("clicked", self._toggle, dev, connected)
            self.body.pack_start(btn, False, False, 0)
        if not devices:
            self.body.pack_start(label("No paired devices", "empty"),
                                 False, False, 0)

    def _toggle(self, _btn, dev, connected):
        path = dev.get_object_path()
        if path in self.pending and not self.pending[path][1]:
            return  # already connecting/disconnecting
        self.pending[path] = (
            "Disconnecting…" if connected else "Connecting…", False)
        dev.call("Disconnect" if connected else "Connect", None,
                 Gio.DBusCallFlags.NONE, 30000, None, self._done, path)
        self.refresh()

    def _done(self, dev, result, path):
        try:
            dev.call_finish(result)
            self.pending.pop(path, None)
        except GLib.Error as err:
            self.pending[path] = (short_error(err), True)
        self.refresh()

    def _on_power(self, switch, _pspec):
        adapters = self._proxies("org.bluez.Adapter1")
        if not adapters:
            return
        args = GLib.Variant("(ssv)", ("org.bluez.Adapter1", "Powered",
                                      GLib.Variant("b", switch.get_active())))
        adapters[0].call("org.freedesktop.DBus.Properties.Set", args,
                         Gio.DBusCallFlags.NONE, 5000, None,
                         self._power_done, None)

    def _power_done(self, adapter, result, _data):
        try:
            adapter.call_finish(result)
            self.power_error = None
        except GLib.Error as err:
            self.power_error = short_error(err)
        self.refresh()


# --- Sound -----------------------------------------------------------------

def sinks():
    """(id, friendly name, device.api) of every sink; node.nick is the
    short name ("LG TV SSCR2", "KM-Bose")."""
    try:
        dump = json.loads(run("pw-dump") or "[]")
    except ValueError:
        return []
    out = []
    for obj in dump:
        props = (obj.get("info") or {}).get("props") or {}
        if (props.get("media.class") != "Audio/Sink"
                or props.get("node.name") == "auto_null"):
            continue
        name = (props.get("node.nick") or props.get("node.description")
                or props.get("node.name"))
        out.append((obj["id"], name, props.get("device.api", "")))
    return out


def default_sink():
    first = run("wpctl", "inspect", "@DEFAULT_AUDIO_SINK@").partition("\n")[0]
    m = re.search(r"id (\d+)", first)
    return int(m.group(1)) if m else None


def volume():
    """(percent, muted) of the default sink; wpctl prints
    'Volume: 0.45' or 'Volume: 0.45 [MUTED]'."""
    out = run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")
    m = re.search(r"Volume: ([\d.]+)", out)
    return (round(float(m.group(1)) * 100) if m else 0, "[MUTED]" in out)


def sink_icon(name, api):
    if api == "bluez5":
        return ICON["bt-audio"]
    if "TV" in name or "HDMI" in name:
        return ICON["tv"]
    return ICON["volume"]


class SoundPanel(Panel):
    """Volume of the default sink plus the sink picker. Picking the sink is
    worth the primary click because `play` keys its whole behaviour (Atmos
    bitstreaming, 4K/HDR mode switch) off which sink is default."""

    title = "Sound"
    css = "sound"

    def __init__(self, app):
        super().__init__(app)
        self.mute = button("", "iconbtn")
        self.mute.set_valign(Gtk.Align.CENTER)
        self.mute.connect("clicked", self._on_mute)
        self.header.pack_end(self.mute, False, False, 0)
        self.header_button(ICON["mixer"], "Mixer", "pavucontrol")

        slider = Gtk.Box(spacing=12)
        self.scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_can_focus(False)
        self.pct = label("", xalign=1.0)
        self.pct.set_width_chars(4)
        slider.pack_start(self.scale, True, True, 0)
        slider.pack_start(self.pct, False, False, 0)
        self.pack_start(slider, False, False, 0)
        self.reorder_child(slider, 1)
        self._scale_id = self.scale.connect("value-changed", self._on_scale)
        self._volume_id = None

        self.fill()

    def fill(self):
        pct, muted = volume()
        with self.scale.handler_block(self._scale_id):
            self.scale.set_value(pct)
        self.pct.set_text(f"{pct}%")
        self.mute.get_child().set_text(ICON["muted" if muted else "volume"])
        ctx = self.mute.get_style_context()
        (ctx.add_class if muted else ctx.remove_class)("on")

        self.body.pack_start(label("Output", "seclabel"), False, False, 0)
        current = default_sink()
        found = sinks()
        for sid, name, api in found:
            btn = row(sink_icon(name, api), name, on=sid == current)
            btn.connect("clicked", self._on_sink, sid)
            self.body.pack_start(btn, False, False, 0)
        if not found:
            self.body.pack_start(label("No outputs", "empty"),
                                 False, False, 0)

    def _on_sink(self, _btn, sid):
        # Running streams follow the default sink in PipeWire.
        run("wpctl", "set-default", str(sid))
        self.refresh()

    def _on_mute(self, _btn):
        run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle")
        self.refresh()

    def _on_scale(self, scale):
        self.pct.set_text(f"{int(scale.get_value())}%")
        # Dragging fires far more often than wpctl needs to run.
        if self._volume_id is None:
            self._volume_id = GLib.timeout_add(40, self._apply_volume)

    def _apply_volume(self):
        self._volume_id = None
        level = self.scale.get_value() / 100
        run("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level:.2f}")
        if self.mute.get_style_context().has_class("on"):
            run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0")
            self.mute.get_child().set_text(ICON["volume"])
            self.mute.get_style_context().remove_class("on")
        return GLib.SOURCE_REMOVE


# --- The popup window ------------------------------------------------------

PANELS = {
    "network": NetworkPanel,
    "bluetooth": BluetoothPanel,
    "sound": SoundPanel,
}


def cursor():
    """Global cursor position from Hyprland: a Wayland client cannot read
    the pointer before it has a surface under it."""
    try:
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


class PopupApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.kirill.barpopup",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None
        self.name = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        name = args[0] if args else ""
        if name not in PANELS:
            cmdline.printerr(f"usage: bar-popup {'|'.join(PANELS)}\n")
            return 2
        if self.win is not None and name == self.name:
            self.close()  # second click on the same module
        else:
            self.open(name)
        return 0

    def open(self, name):
        point = cursor()
        monitor = monitor_at(point)
        geo = monitor.get_geometry()

        win = Gtk.Window(application=self)
        win.set_app_paintable(True)
        visual = win.get_screen().get_rgba_visual()
        if visual is not None:
            win.set_visual(visual)
        GtkLayerShell.init_for_window(win)
        GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_monitor(win, monitor)
        # Anchored to every edge but with no exclusive zone of its own, so
        # it fills the output *below* the bar: the bar stays clickable,
        # which is what lets a second click toggle or switch panels.
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(win, edge, True)
        GtkLayerShell.set_keyboard_mode(
            win, GtkLayerShell.KeyboardMode.ON_DEMAND)

        card = PANELS[name](self)
        # Centre the card on the click, kept inside the monitor. The
        # EventBox swallows presses so clicks on the card don't reach the
        # backdrop behind it.
        x = (point[0] - geo.x if point else geo.width) - CARD_W // 2
        card_wrap = Gtk.EventBox()
        card_wrap.set_halign(Gtk.Align.START)
        card_wrap.set_valign(Gtk.Align.START)
        card_wrap.set_margin_top(CARD_TOP)
        card_wrap.set_margin_start(
            max(EDGE, min(x, geo.width - CARD_W - EDGE)))
        card_wrap.connect("button-press-event", lambda *_: True)
        card_wrap.add(card)

        backdrop = Gtk.EventBox()
        backdrop.get_style_context().add_class("backdrop")
        backdrop.connect("button-press-event", lambda *_: self.close())
        # A concrete size so the window maps (an empty EventBox is 0x0).
        backdrop.set_size_request(geo.width, geo.height)

        overlay = Gtk.Overlay()
        overlay.add(backdrop)
        overlay.add_overlay(card_wrap)
        win.add(overlay)
        win.connect("key-press-event", self._on_key)
        win.show_all()

        # Swap only after the new window exists: dropping to zero windows
        # in between would end the application.
        old, self.win, self.name = self.win, win, name
        if old is not None:
            old.destroy()

    def close(self):
        if self.win is not None:
            win, self.win, self.name = self.win, None, None
            win.destroy()

    def _on_key(self, _win, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False


def main():
    return PopupApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
