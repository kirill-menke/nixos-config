"""Control the LG TV over the network (webOS SSAP).

The TV cannot send its remote's key presses to the PC -- webOS has no such
endpoint, and the Magic Remote talks 2.4 GHz RF straight to the TV -- but the
reverse direction works fine, so the PC can wake it and select its own input.

Volume goes through the TV rather than mpv because the interesting audio path
bitstreams TrueHD/Atmos: mpv hands the stream over untouched and cannot attenuate
it, so the TV (which relays to the soundbar over eARC) owns the volume.  Absolute
setVolume does work on this TV/soundbar pair even though soundOutput is
"external_arc" -- verified 21 -> 25 -> 30 with the value reading back each time.

Usage:
    tv status              power state and current input
    tv on                  wake via wake-on-LAN, wait until reachable
    tv off                 power off (standby)
    tv input HDMI_1        switch input
    tv pc                  on + switch to the PC input (what `play` calls)
    tv volume              print the current volume
    tv volume 25           set the volume
    tv volume +2 / -2      step
    tv volume mute|unmute|toggle
    tv picture             print the current picture mode
    tv picture filmMaker   set it (what `play` does, so films are not smoothed)
"""

import asyncio
import os
import socket
import sys
import time

from aiowebostv import WebOsClient

HOST = os.environ.get("LGTV_HOST", "192.168.0.18")
MAC = os.environ.get("LGTV_MAC", "3c:f0:83:3c:b4:9a")
# HDMI_1 is labelled "PC" on this TV; HDMI_2 is the eARC port with the soundbar.
PC_INPUT = os.environ.get("LGTV_PC_INPUT", "HDMI_1")
KEYFILE = os.path.expanduser("~/.local/state/lgtv-client-key")

# Filmmaker Mode turns off motion interpolation and sharpening and holds the
# source's own colour and gamma, which is the point of matching the refresh rate
# and bitstreaming the audio in the first place.
PICTURE_MODE = os.environ.get("LGTV_PICTURE_MODE", "filmMaker")
# settings/setSystemSettings is the one this TV takes (verified on webOS 24 by
# setting the mode it already held, so nothing on screen moved).  The luna
# spelling is kept as a fallback because webOS has moved system settings
# between services across versions, and `tv picture` reports which one worked.
PICTURE_ENDPOINTS = (
    "settings/setSystemSettings",
    "com.webos.settingsservice/setSystemSettings",
)

# How long a just-issued command's own reply outranks the subscription feed.
# Without this the reply to a set could re-read a not-yet-updated tv_state and
# report the old value.
COMMAND_WINS_FOR = 1.5


def load_key():
    try:
        return open(KEYFILE).read().strip() or None
    except OSError:
        return None


def save_key(key):
    os.makedirs(os.path.dirname(KEYFILE), exist_ok=True)
    with open(KEYFILE, "w") as f:
        f.write(key)
    os.chmod(KEYFILE, 0o600)


def wake():
    """Send wake-on-LAN magic packets.

    Needed because a TV in standby has its WebSocket server down, so power_on()
    over SSAP cannot reach it.  Requires "Mobile TV On" / "Turn on via Wi-Fi"
    on the TV, otherwise the packets are ignored.

    This TV is on Wi-Fi, and a single packet to the global broadcast address
    does not reliably wake it: APs buffer broadcast frames for power-saving
    clients, and the TV only answers ARP (chip-level offload) while its SoC is
    asleep.  Sending unicast *and* subnet broadcast *and* global broadcast, on
    both ports, repeatedly, is what actually works.
    """
    mac = bytes.fromhex(MAC.replace(":", "").replace("-", ""))
    packet = b"\xff" * 6 + mac * 16
    subnet = HOST.rsplit(".", 1)[0] + ".255"
    for host in (HOST, subnet, "255.255.255.255"):
        for port in (9, 7):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                s.sendto(packet, (host, port))
            except OSError:
                pass
            s.close()


async def connect(timeout=3):
    client = WebOsClient(HOST, load_key(), connect_timeout=timeout)
    await client.connect()
    if client.client_key and client.client_key != load_key():
        save_key(client.client_key)
    return client


async def cmd_status():
    client = await connect()
    power = await client.get_power_state()
    print(f"power: {power.get('state')}")
    print(f"input: {await client.get_input()}")
    await client.disconnect()
    return 0


async def cmd_on(wait=25):
    """Wake and block until the TV answers, so callers can rely on it being up."""
    try:
        client = await connect(timeout=2)
        await client.disconnect()
        return 0  # already awake
    except Exception:
        pass

    # Keep sending while polling rather than once up front: the TV took
    # several rounds to respond, and a lone burst is easy to miss.
    for i in range(wait):
        if i % 3 == 0:
            wake()
        await asyncio.sleep(1)
        try:
            client = await connect(timeout=2)
            await client.disconnect()
            return 0
        except Exception:
            continue
    print("tv: did not come up (is 'Quick Start+' enabled?)", file=sys.stderr)
    return 1


async def cmd_off():
    client = await connect()
    await client.power_off()
    await client.disconnect()
    return 0


async def cmd_input(target):
    client = await connect()
    current = await client.get_input()
    # get_input() returns an app id like "com.webos.app.hdmi1"; set_input()
    # wants the input id ("HDMI_1").  Compare on the digit rather than parsing.
    if current.replace("com.webos.app.hdmi", "") != target.replace("HDMI_", ""):
        await client.set_input(target)
    await client.disconnect()
    return 0


async def cmd_pc():
    rc = await cmd_on()
    if rc != 0:
        return rc
    return await cmd_input(PC_INPUT)


# --------------------------------------------------------------------------
# Volume and picture mode
# --------------------------------------------------------------------------

class Session:
    """One SSAP connection, carrying a single volume or picture-mode command."""

    def __init__(self):
        self.client = None
        self.lock = asyncio.Lock()
        self.volume = None
        self.muted = None
        self.sound_output = None
        self.last_command = 0.0

    async def ensure(self):
        """Return a live client, connecting or reconnecting as needed."""
        async with self.lock:
            if self.client is not None and self.client.is_connected():
                return self.client
            self.client = None
            self.client = await connect(timeout=4)
            self._absorb()
            return self.client

    def _absorb(self):
        """Take whatever the client's own subscriptions have learned.

        aiowebostv keeps tv_state current from the TV's push feed, so this also
        picks up volume changes made with the Magic Remote.
        """
        st = getattr(self.client, "tv_state", None)
        if st is None:
            return
        if time.monotonic() - self.last_command < COMMAND_WINS_FOR:
            return
        if st.volume is not None:
            self.volume = st.volume
        if st.muted is not None:
            self.muted = st.muted
        if st.sound_output is not None:
            self.sound_output = st.sound_output

    def snapshot(self, error=None):
        connected = self.client is not None and self.client.is_connected()
        if connected:
            self._absorb()
        return {
            "ok": error is None,
            "connected": connected,
            "volume": self.volume,
            "muted": self.muted,
            "sound_output": self.sound_output,
            "error": error,
        }

    async def act(self, req):
        cmd = req.get("cmd", "state")
        try:
            client = await self.ensure()
        except Exception as e:
            return self.snapshot(error=f"{type(e).__name__}: {e}")

        try:
            if cmd == "set":
                await self._set(client, int(req.get("volume", 0)))
            elif cmd == "step":
                await self._step(client, int(req.get("delta", 0)))
            elif cmd == "picture":
                return await self._picture(client, req.get("mode"))
            elif cmd == "mute":
                want = req.get("value", "toggle")
                if want == "toggle":
                    want = not bool(self.muted)
                await client.set_mute(bool(want))
                self.muted = bool(want)
                self.last_command = time.monotonic()
            elif cmd != "state":
                return self.snapshot(error=f"unknown command {cmd!r}")
        except Exception as e:
            # A connection that died between ensure() and here looks exactly
            # like this; drop it rather than try to disconnect it afterwards.
            self.client = None
            return self.snapshot(error=f"{type(e).__name__}: {e}")
        return self.snapshot()

    async def _picture(self, client, mode):
        """Read the picture mode, or set it and say which endpoint took it."""
        if not mode:
            try:
                r = await client.request("settings/getSystemSettings",
                                         {"category": "picture", "keys": ["pictureMode"]})
            except Exception as e:
                return dict(self.snapshot(error=f"{type(e).__name__}: {e}"), picture=None)
            settings = r.get("settings") if isinstance(r, dict) else None
            current = (settings or {}).get("pictureMode")
            return dict(self.snapshot(), picture=current)

        payload = {"category": "picture", "settings": {"pictureMode": mode}}
        errors = []
        for endpoint in PICTURE_ENDPOINTS:
            try:
                r = await client.request(endpoint, payload)
            except Exception as e:
                errors.append(f"{endpoint}: {type(e).__name__}")
                continue
            if isinstance(r, dict) and r.get("returnValue") is False:
                errors.append(f"{endpoint}: {r.get('errorText') or 'refused'}")
                continue
            self.last_command = time.monotonic()
            return dict(self.snapshot(), picture=mode, endpoint=endpoint)
        return dict(self.snapshot(error="; ".join(errors) or "no endpoint accepted it"),
                    picture=None)

    async def _set(self, client, volume):
        volume = max(0, min(100, volume))
        reply = await client.set_volume(volume)
        got = reply.get("volume") if isinstance(reply, dict) else None
        self.volume = got if isinstance(got, int) else volume
        self.last_command = time.monotonic()

    async def _step(self, client, delta):
        if self.volume is not None:
            # One absolute hop instead of |delta| round trips.
            await self._set(client, self.volume + delta)
            return
        for _ in range(abs(delta)):
            reply = await (client.volume_up() if delta > 0 else client.volume_down())
            got = reply.get("volume") if isinstance(reply, dict) else None
            if isinstance(got, int):
                self.volume = got
        self.last_command = time.monotonic()


async def tv_request(req):
    """One command on a connection of its own."""
    session = Session()
    try:
        return await session.act(req)
    finally:
        if session.client is not None:
            await session.client.disconnect()


def cmd_picture(arg):
    req = {"cmd": "picture"} if arg is None else {"cmd": "picture", "mode": arg}
    resp = asyncio.run(tv_request(req))
    if not resp.get("ok"):
        print(f"tv: {resp.get('error') or 'picture mode unavailable'}", file=sys.stderr)
        return 1
    if arg is None:
        print(f"picture: {resp.get('picture')}")
    else:
        print(f"picture: {resp.get('picture')} (via {resp.get('endpoint')})")
    return 0


def cmd_volume(arg):
    if arg is None:
        req = {"cmd": "state"}
    elif arg in ("mute", "unmute", "toggle"):
        req = {"cmd": "mute", "value": {"mute": True, "unmute": False}.get(arg, "toggle")}
    elif arg[0] in "+-" and arg[1:].isdigit():
        req = {"cmd": "step", "delta": int(arg)}
    elif arg.isdigit():
        req = {"cmd": "set", "volume": int(arg)}
    else:
        print(f"tv: bad volume argument {arg!r}", file=sys.stderr)
        return 2

    resp = asyncio.run(tv_request(req))
    if not resp.get("ok"):
        print(f"tv: {resp.get('error') or 'volume unavailable'}", file=sys.stderr)
        return 1
    print(f"volume: {resp.get('volume')}{' (muted)' if resp.get('muted') else ''}")
    return 0


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        if action == "status":
            return asyncio.run(cmd_status())
        if action == "on":
            return asyncio.run(cmd_on())
        if action == "off":
            return asyncio.run(cmd_off())
        if action == "pc":
            return asyncio.run(cmd_pc())
        if action == "volume":
            return cmd_volume(sys.argv[2] if len(sys.argv) > 2 else None)
        if action == "picture":
            # No argument reads it back, which is how to check the set worked.
            return cmd_picture(sys.argv[2] if len(sys.argv) > 2 else None)
        if action == "input":
            if len(sys.argv) < 3:
                print("usage: tv input HDMI_1", file=sys.stderr)
                return 2
            return asyncio.run(cmd_input(sys.argv[2]))
        print(__doc__.strip(), file=sys.stderr)
        return 2
    except Exception as e:
        # Never hard-fail a caller like `play`: a TV that is unplugged or on a
        # different network should not stop a movie from starting.
        print(f"tv: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


sys.exit(main())
