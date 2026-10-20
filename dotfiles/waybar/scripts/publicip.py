#!/usr/bin/env python3
"""This machine's public IP addresses, as ifconfig.me sees them.

`publicip copy` copies the public IPv4 address, or on a network without
one the IPv6 address (the waybar network module's right click), and
confirms it with a transient notification; one says at once that a lookup
is under way when the copy needs one. bar-popup imports this module for
the Network card, whose Details show the cache at once and look both
addresses up again when unfolded. The two share one cache, so a copy soon
after that, on the same network, needs no lookup; a lookup made on another
network never counts.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time

HOST = "ifconfig.me"  # GET /ip answers with the bare address
TIMEOUT = 3     # s for both lookups together, name resolution included
TTL = 300       # s a lookup is good for a copy, on the same network
POPUP_MS = 2000

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
CACHE = os.path.join(RUNTIME, "publicip.json")

# Binding the socket to one family's wildcard address before it connects
# makes the connection skip the host's addresses of the other family, like
# curl -4 / -6.
FAMILIES = {4: "0.0.0.0", 6: "::"}


def lookup(version):
    """The public address of IP `version` (4 or 6), or None."""
    # Imported here: they are most of this module's import time, which
    # every bar-popup card pays (the Network card imports it), and only a
    # lookup needs them.
    import http.client
    import ipaddress
    conn = http.client.HTTPSConnection(
        HOST, timeout=TIMEOUT, source_address=(FAMILIES[version], 0))
    try:
        conn.request("GET", "/ip")
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        addr = ipaddress.ip_address(resp.read(64).decode().strip())
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        conn.close()
    return str(addr) if addr.version == version else None


def here():
    """The local addresses connections would leave from now, one per
    family. They change with the network, so a lookup made on another one
    is not taken for this one's."""
    found = []
    for family, probe in ((socket.AF_INET, "192.0.2.1"),
                          (socket.AF_INET6, "2001:db8::1")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 9))  # picks a route, sends nothing
                found.append(sock.getsockname()[0])
        except OSError:
            found.append(None)
    return found


def fetch():
    """Both addresses, looked up side by side and cached; None if neither
    came back within TIMEOUT, which leaves the cache as it was."""
    via = here()
    found = {}

    def look(version):
        found[f"ipv{version}"] = lookup(version)

    threads = [threading.Thread(target=look, args=(v,), daemon=True)
               for v in FAMILIES]
    for thread in threads:
        thread.start()
    # A socket timeout doesn't cover name resolution, so wait on the clock;
    # a lookup still hanging is abandoned (daemon thread).
    end = time.monotonic() + TIMEOUT
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    data = {"ipv4": found.get("ipv4"), "ipv6": found.get("ipv6")}
    if not any(data.values()):
        return None
    tmp = f"{CACHE}.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp, "w") as fh:
            json.dump({**data, "via": via}, fh)
        os.replace(tmp, CACHE)  # readers never see half a file
    except OSError:
        pass
    return data


def cached(ttl=None):
    """The last lookup that found something, if it was made from the
    network we are on now (see here()) and, given a ttl, less than ttl
    seconds ago; else None."""
    try:
        if ttl is not None and time.time() - os.stat(CACHE).st_mtime >= ttl:
            return None
        with open(CACHE) as fh:
            data = json.load(fh)
        if data.get("via") != here():
            return None
        return {"ipv4": data.get("ipv4"), "ipv6": data.get("ipv6")}
    except (OSError, ValueError, AttributeError):
        return None


def notify(summary, body="", ms=POPUP_MS, replace=None):
    """A transient popup that answers the click, in place of the one whose
    id `replace` is; its own id, None if it failed. Like osd's, it bypasses
    Do Not Disturb: it confirms an action, it interrupts nothing."""
    cmd = ["notify-send", "-e", "-p", "-a", "publicip", "-t", str(ms),
           "-h", "boolean:SWAYNC_BYPASS_DND:true"]
    if replace is not None:
        cmd += ["-r", replace]
    try:
        out = subprocess.run(cmd + [summary, body], capture_output=True,
                             text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out.isdigit() else None


def copy():
    """Copy the public IPv4 address to the clipboard, else (an IPv6-only
    network) the IPv6 one, and say so. Without a fresh lookup in the cache
    the copy waits for one, up to TIMEOUT: a popup says so at once, and the
    answer takes its place."""
    data = cached(TTL)
    asked = None
    if not (data and data["ipv4"]):
        asked = notify("Looking up the public IP…", ms=(TIMEOUT + 2) * 1000)
        data = fetch() or data  # a failed lookup leaves the cache's IPv6
    addr = data and (data["ipv4"] or data["ipv6"])
    if not addr:
        notify("No public IP address", "The lookup failed or timed out",
               replace=asked)
        return 1
    try:
        # wl-copy forks to serve the clipboard; that child keeps any pipe
        # open, so no output is captured. A session of its own: waybar
        # ends a click's process group when it reloads, which would take
        # the clipboard's server, and the copied address, with it.
        subprocess.run(["wl-copy", "--", addr], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       start_new_session=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        notify("Couldn't copy the public IP", addr, replace=asked)
        return 1
    notify("Copied public IP" if addr == data["ipv4"] else
           "Copied public IPv6", addr, replace=asked)
    return 0


def main(argv):
    if argv[1:] != ["copy"]:
        print("usage: publicip copy", file=sys.stderr)
        return 2
    return copy()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
