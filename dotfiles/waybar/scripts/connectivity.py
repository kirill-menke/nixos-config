#!/usr/bin/env python3
"""What lies past the link, which waybar's network module can't see (it
reads the link alone): the bar's connectivity module, in the device pill
right after the network module.

  connectivity status   the module: "Sign in" while a captive portal
                        (hotel, train, café) holds the connection back, a
                        glyph while the network answers but the internet
                        doesn't, a lock while the internet traffic goes
                        through a VPN; nothing (hidden) otherwise
  connectivity open     its click: the sign-in page behind a portal (the
                        Network card's Open runs this too), else the
                        Network card

NetworkManager checks the internet every 5 minutes (modules/nixos/desktop.nix) and
right after each connect. Its dispatcher sends the module its "signal" on
each change of that verdict and as a link or a VPN comes or goes, so
`status` runs only then and nothing polls.
"""

import functools
import json
import subprocess
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
PORTAL, LIMITED = 2, 3     # NMConnectivityState
ACTIVATED = 2              # NMActiveConnectionState
TIMEOUT_MS = 2000
# After a click on "Sign in": have NetworkManager check again this often,
# this many times at most, so "Sign in" goes as soon as the portal lets go
# rather than at the next 5-minute check.
RECHECK_S, RECHECK_N = 10, 12
# A public address of each family. The route the kernel picks for it
# (`ip route get`, which follows the policy rules, so Tailscale's table 52
# and wg-quick's table count too) names the interface the internet traffic
# leaves by. Nothing is sent.
PROBES = (("-4", "1.1.1.1"), ("-6", "2606:4700:4700::1111"))

# The portal's is text alone: right after the network module, "Sign in"
# says enough.
ICON = {
    "limited": "\U000f0a8e",  # md-web-off
    "vpn": "\U000f033e",      # md-lock
}
# The dim last line every tooltip names its other gestures in (config.jsonc
# has the scheme): small, in the cards' subtext0, as markup can't name CSS
# colours. Behind a portal the click opens the sign-in page, not the card.
HINT = ("<span size='small' foreground='#a6adc8'>"
        "Click: open the sign-in page</span>")


def call(path, iface, method, args=None, reply="(v)"):
    """One synchronous call to NetworkManager, or None: it isn't running
    (nothing here starts it), or it said no."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        return bus.call_sync(NM, path, iface, method, args,
                             GLib.VariantType(reply),
                             Gio.DBusCallFlags.NO_AUTO_START, TIMEOUT_MS,
                             None).unpack()
    except GLib.Error:
        return None


def props(path, iface=NM):
    """The properties one of NetworkManager's objects has on `iface` ({}
    for none, or without NetworkManager)."""
    if not path or path == "/":
        return {}
    got = call(path, PROPERTIES, "GetAll", GLib.Variant("(s)", (iface,)),
               "(a{sv})")
    return got[0] if got else {}


def verdict(mgr):
    """'portal', 'limited' or None. A portal counts only with a check page
    to open, which is what the click shows."""
    state = mgr.get("Connectivity")
    if state == PORTAL and mgr.get("ConnectivityCheckUri"):
        return "portal"
    return "limited" if state == LIMITED else None


def network_name(mgr):
    """The connection the default route goes through, by its profile's
    name (a Wi-Fi network's is its SSID)."""
    return (props(mgr.get("PrimaryConnection"),
                  f"{NM}.Connection.Active").get("Id") or "This network")


@functools.cache
def internet_ifaces():
    """The interfaces the internet traffic leaves by, IPv4 and IPv6 (one
    `ip route get` each); none for a family without a route, or when `ip`
    can't say."""
    found = set()
    for family, address in PROBES:
        try:
            out = subprocess.run(["ip", "-j", family, "route", "get", address],
                                 capture_output=True, text=True,
                                 timeout=TIMEOUT_MS / 1000).stdout
            found.update(route.get("dev") for route in json.loads(out or "[]"))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    found.discard(None)
    return frozenset(found)


def is_vpn(active):
    """A VPN the internet traffic goes through, which is what the lock
    stands for: a VPN plugin's connection (OpenVPN, OpenConnect, ...)
    while it holds the default route, or a WireGuard or tun device (a VPN
    run outside NetworkManager) that the route to the internet leaves by.
    So Tailscale counts only with an exit node: without one its tun device
    carries the tailnet's own addresses and nothing else. Switching an exit
    node on or off is no NetworkManager event, so the lock follows at the
    next one. A tap device is a virtual machine's network card, no VPN."""
    if active.get("Vpn"):
        return bool(active.get("Default") or active.get("Default6"))
    kind = active.get("Type")
    if kind not in ("wireguard", "tun"):
        return False
    device = (active.get("Devices") or ["/"])[0]
    if (kind == "tun"
            and props(device, f"{NM}.Device.Tun").get("Mode") != "tun"):
        return False
    iface = props(device, f"{NM}.Device").get("IpInterface")
    return iface in internet_ifaces()


def vpns(mgr):
    """The names of the VPNs the internet traffic goes through (is_vpn).
    The network module shows none of them: it ignores a VPN routed through
    a table of its own (WireGuard's default route), and takes a tun VPN in
    the main table for Ethernet."""
    names = []
    for path in mgr.get("ActiveConnections", []):
        active = props(path, f"{NM}.Connection.Active")
        if active.get("State") == ACTIVATED and is_vpn(active):
            names.append(active.get("Id") or "VPN")
    return names


def esc(text):
    return GLib.markup_escape_text(text, -1)


def status():
    """The module's JSON. The tooltip is markup (the module runs with
    "escape": false for the hint line), so the names in it are escaped."""
    mgr = props(NM_PATH)
    kind, tunnels = verdict(mgr), vpns(mgr)
    text, about = [], []
    if tunnels:
        text.append(ICON["vpn"])
    if kind == "portal":
        text.append("Sign in")
        about.append(f"{esc(network_name(mgr))} wants you to sign in before "
                     "it lets you online")
    elif kind == "limited":
        text.append(ICON["limited"])
        about.append(f"Connected to {esc(network_name(mgr))}, but not to "
                     "the internet")
    if not text:
        print("{}")  # waybar hides the module
        return 0
    if tunnels:
        about.append(f"VPN: {esc(', '.join(tunnels))}")
    if kind == "portal":
        about.append(HINT)
    # style.css colours the module by its most urgent class.
    classes = (["vpn"] if tunnels else []) + ([kind] if kind else [])
    print(json.dumps({"text": " ".join(text), "tooltip": "\n".join(about),
                      "class": classes}))
    return 0


def spawn(*args):
    subprocess.Popen(args, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_():
    """The page NetworkManager checks: a portal answers it with its
    sign-in page, which is how NetworkManager noticed the portal (as the
    Network card's Open). Then ask again until the portal lets go: its
    next check of its own can be minutes away."""
    mgr = props(NM_PATH)
    if verdict(mgr) != "portal":
        spawn("bar-popup", "network")
        return 0
    spawn("xdg-open", mgr["ConnectivityCheckUri"])
    for _ in range(RECHECK_N):
        time.sleep(RECHECK_S)
        got = call(NM_PATH, NM, "CheckConnectivity", None, "(u)")
        if got is None or got[0] != PORTAL:
            break  # the dispatcher has told the bar already
    return 0


def main(argv):
    commands = {"status": status, "open": open_}
    if len(argv) != 2 or argv[1] not in commands:
        print(f"usage: connectivity {'|'.join(commands)}", file=sys.stderr)
        return 2
    return commands[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
