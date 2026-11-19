"""iphone-hid -- the PC's keyboard and mouse as a Bluetooth HID device.

Super+I hands every local keyboard and mouse to the (MDM-locked) work iPhone:
the evdev devices are grabbed, so Hyprland stops seeing them, and their events
are re-sent as a classic Bluetooth HID keyboard+mouse combo.  Super+I again
gives them back.  The screen comes over AirPlay (uxplay); the mouse drives
iOS's AssistiveTouch pointer, which must be switched on in the phone's
accessibility settings.

Needs root: L2CAP PSMs 17/19 are privileged, and bluetoothd's input plugin
must be disabled because it listens on those PSMs itself.

Pairing: with no remembered phone the adapter is discoverable and pairable,
and the iPhone pairs to it from Settings -> Bluetooth.  The phone's address
is then kept in /var/lib/iphone-hid/host and the adapter goes quiet again;
later toggles reconnect to it from this side.  Delete the file and restart
the service to pair a different phone.
"""

import asyncio
import os
import pwd
import socket
import struct
import subprocess
import time

import evdev
from dbus_fast import BusType, DBusError, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method
from evdev import ecodes as e

HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"
PSM_CTRL = 0x11
PSM_INTR = 0x13
HOST_FILE = "/var/lib/iphone-hid/host"
ADAPTER = "/org/bluez/hci0"
USER = os.environ.get("IPHONE_HID_USER", "")
# Keep-awake: while the phone is being mirrored and someone is using this
# PC, nudge the pointer often enough to beat the MDM's 5-minute auto-lock.
# Gated on PC activity so the phone still locks once nobody is here.
NUDGE_EVERY = 120
PRESENT_FOR = 180
MIRROR_PORT = 7100  # uxplay -p: the screen-mirroring video stream
# Coalesce mouse motion to ~125 Hz: a 1000 Hz mouse would otherwise queue
# reports faster than the Bluetooth link drains them and the pointer lags.
MOUSE_INTERVAL = 0.008

# Report 1: boot-style keyboard (modifier byte, reserved, 6 key slots).
# Report 2: mouse with 5 buttons, 16-bit relative X/Y, wheel and AC Pan.
DESCRIPTOR = bytes.fromhex(
    "05010906a1018501050719e029e71500250175019508810295017508810195067508"
    "150025650507190029658100c0"
    "05010902a10185020901a1000509190129051500250195057501810295017503"
    "810105010930093116018026ff7f75109502810609381581257f7508950181"
    "06050c0a38021581257f750895018106c0c0"
)

SDP_RECORD = f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001"><sequence><uuid value="0x1124" /></sequence></attribute>
  <attribute id="0x0004"><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x0011" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></attribute>
  <attribute id="0x0005"><sequence><uuid value="0x1002" /></sequence></attribute>
  <attribute id="0x0006"><sequence>
    <uint16 value="0x656e" /><uint16 value="0x006a" /><uint16 value="0x0100" />
  </sequence></attribute>
  <attribute id="0x0009"><sequence>
    <sequence><uuid value="0x1124" /><uint16 value="0x0101" /></sequence>
  </sequence></attribute>
  <attribute id="0x000d"><sequence><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x0013" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></sequence></attribute>
  <attribute id="0x0100"><text value="PC Keyboard and Mouse" /></attribute>
  <attribute id="0x0101"><text value="Keyboard and mouse" /></attribute>
  <attribute id="0x0102"><text value="iphone-hid" /></attribute>
  <attribute id="0x0200"><uint16 value="0x0100" /></attribute>
  <attribute id="0x0201"><uint16 value="0x0111" /></attribute>
  <attribute id="0x0202"><uint8 value="0xc0" /></attribute>
  <attribute id="0x0203"><uint8 value="0x00" /></attribute>
  <attribute id="0x0204"><boolean value="true" /></attribute>
  <attribute id="0x0205"><boolean value="true" /></attribute>
  <attribute id="0x0206"><sequence><sequence>
    <uint8 value="0x22" /><text encoding="hex" value="{DESCRIPTOR.hex()}" />
  </sequence></sequence></attribute>
  <attribute id="0x0207"><sequence><sequence>
    <uint16 value="0x0409" /><uint16 value="0x0100" />
  </sequence></sequence></attribute>
  <attribute id="0x020b"><uint16 value="0x0100" /></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80" /></attribute>
  <attribute id="0x020d"><boolean value="true" /></attribute>
  <attribute id="0x020e"><boolean value="false" /></attribute>
</record>
"""

MODIFIERS = {
    e.KEY_LEFTCTRL: 0x01, e.KEY_LEFTSHIFT: 0x02,
    e.KEY_LEFTALT: 0x04, e.KEY_LEFTMETA: 0x08,
    e.KEY_RIGHTCTRL: 0x10, e.KEY_RIGHTSHIFT: 0x20,
    e.KEY_RIGHTALT: 0x40, e.KEY_RIGHTMETA: 0x80,
}
META = {e.KEY_LEFTMETA, e.KEY_RIGHTMETA}
BUTTONS = {
    e.BTN_LEFT: 0x01, e.BTN_RIGHT: 0x02, e.BTN_MIDDLE: 0x04,
    e.BTN_SIDE: 0x08, e.BTN_EXTRA: 0x10,
}

# evdev key code -> HID keyboard usage (USB HID Usage Tables, page 0x07).
KEYMAP = {getattr(e, "KEY_" + c): 0x04 + i
          for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
KEYMAP.update({getattr(e, f"KEY_{n}"): 0x1D + n for n in range(1, 10)})
KEYMAP.update({getattr(e, f"KEY_F{n}"): 0x39 + n for n in range(1, 13)})
KEYMAP.update({getattr(e, f"KEY_KP{n}"): 0x58 + n for n in range(1, 10)})
KEYMAP.update({
    e.KEY_0: 0x27, e.KEY_ENTER: 0x28, e.KEY_ESC: 0x29,
    e.KEY_BACKSPACE: 0x2A, e.KEY_TAB: 0x2B, e.KEY_SPACE: 0x2C,
    e.KEY_MINUS: 0x2D, e.KEY_EQUAL: 0x2E, e.KEY_LEFTBRACE: 0x2F,
    e.KEY_RIGHTBRACE: 0x30, e.KEY_BACKSLASH: 0x31, e.KEY_SEMICOLON: 0x33,
    e.KEY_APOSTROPHE: 0x34, e.KEY_GRAVE: 0x35, e.KEY_COMMA: 0x36,
    e.KEY_DOT: 0x37, e.KEY_SLASH: 0x38, e.KEY_CAPSLOCK: 0x39,
    e.KEY_SYSRQ: 0x46, e.KEY_SCROLLLOCK: 0x47, e.KEY_PAUSE: 0x48,
    e.KEY_INSERT: 0x49, e.KEY_HOME: 0x4A, e.KEY_PAGEUP: 0x4B,
    e.KEY_DELETE: 0x4C, e.KEY_END: 0x4D, e.KEY_PAGEDOWN: 0x4E,
    e.KEY_RIGHT: 0x4F, e.KEY_LEFT: 0x50, e.KEY_DOWN: 0x51, e.KEY_UP: 0x52,
    e.KEY_NUMLOCK: 0x53, e.KEY_KPSLASH: 0x54, e.KEY_KPASTERISK: 0x55,
    e.KEY_KPMINUS: 0x56, e.KEY_KPPLUS: 0x57, e.KEY_KPENTER: 0x58,
    e.KEY_KP0: 0x62, e.KEY_KPDOT: 0x63,
    # The extra ISO key left of Z (<>| on German keyboards).
    e.KEY_102ND: 0x64, e.KEY_COMPOSE: 0x65,
})


def log(msg):
    print(msg, flush=True)


def mirroring():
    """True while some peer has the uxplay mirror stream open."""
    port = f":{MIRROR_PORT:04X} "
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as f:
                for line in f:
                    fields = line.split()
                    # fields[1] is local addr:port, fields[3] state (01 = ESTABLISHED)
                    if (fields[1] + " ").endswith(port) and fields[3] == "01":
                        return True
        except OSError:
            pass
    return False


class Agent(ServiceInterface):
    """Accept every pairing request without a code ("Just Works").

    Only reachable while the adapter is pairable, which is only while no
    phone is remembered yet.
    """

    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        pass

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        return "0000"

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        pass

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        pass

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        log(f"pairing confirmed for {device}")

    @method()
    def RequestAuthorization(self, device: "o"):
        pass

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        # HID runs over our own sockets and needs no authorization; what
        # lands here is the iPhone trying to use the PC as a speaker or
        # hands-free set, which would steal its audio.
        raise DBusError("org.bluez.Error.Rejected", f"{uuid} not offered")

    @method()
    def Cancel(self):
        pass


class Profile(ServiceInterface):
    """Carries the HID SDP record.  The L2CAP channels are our own sockets."""

    def __init__(self):
        super().__init__("org.bluez.Profile1")

    @method()
    def Release(self):
        pass

    @method()
    def NewConnection(self, device: "o", fd: "h", props: "a{sv}"):
        os.close(fd)

    @method()
    def RequestDisconnection(self, device: "o"):
        pass


def l2cap_socket():
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                      socket.BTPROTO_L2CAP)
    s.setblocking(False)
    return s


class Bridge:
    def __init__(self, loop):
        self.loop = loop
        self.devs = {}
        self.pressed = set()
        # pc -> to_phone (waits for every key to be released, so Hyprland
        # does not keep a stuck Super) -> phone -> to_pc (same) -> pc
        self.mode = "pc"
        self.ctrl = self.intr = None
        self.connecting = None
        self.dx = self.dy = self.wheel = self.pan = 0
        self.last_kb = b""
        self.last_mouse = 0.0
        self.flush_handle = None
        self.props = None
        self.last_input = 0.0
        self.last_nudge = 0.0

    # -- notifications -----------------------------------------------------

    def notify(self, msg):
        log(msg)
        if not USER:
            return
        uid = pwd.getpwnam(USER).pw_uid
        env = dict(os.environ,
                   DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{uid}/bus")
        p = subprocess.Popen(
            ["runuser", "-u", USER, "--", "notify-send", "-a", "iPhone",
             "-t", "2500", "iPhone", msg],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.loop.run_in_executor(None, p.wait)

    # -- bluetooth ---------------------------------------------------------

    async def setup_bluez(self):
        bus = await MessageBus(bus_type=BusType.SYSTEM,
                               negotiate_unix_fd=True).connect()
        bus.export("/iphone_hid/agent", Agent())
        bus.export("/iphone_hid/profile", Profile())
        root = bus.get_proxy_object(
            "org.bluez", "/org/bluez",
            await bus.introspect("org.bluez", "/org/bluez"))
        agents = root.get_interface("org.bluez.AgentManager1")
        await agents.call_register_agent("/iphone_hid/agent",
                                         "NoInputNoOutput")
        await agents.call_request_default_agent("/iphone_hid/agent")
        await root.get_interface("org.bluez.ProfileManager1") \
            .call_register_profile("/iphone_hid/profile", HID_UUID, {
                "ServiceRecord": Variant("s", SDP_RECORD),
                "Role": Variant("s", "server"),
                "RequireAuthentication": Variant("b", False),
                "RequireAuthorization": Variant("b", False),
            })
        adapter = bus.get_proxy_object(
            "org.bluez", ADAPTER, await bus.introspect("org.bluez", ADAPTER))
        self.props = adapter.get_interface("org.freedesktop.DBus.Properties")
        await self.set_adapter("Powered", True)
        await self.set_pairing(not os.path.exists(HOST_FILE))

    async def set_adapter(self, prop, value):
        await self.props.call_set("org.bluez.Adapter1", prop, Variant(
            "u" if prop.endswith("Timeout") else "b", value))

    async def set_pairing(self, on):
        if on:
            await self.set_adapter("DiscoverableTimeout", 0)
            await self.set_adapter("PairableTimeout", 0)
        await self.set_adapter("Pairable", on)
        await self.set_adapter("Discoverable", on)
        log("pairing mode " + ("on: pair from the iPhone's Bluetooth "
                               "settings" if on else "off"))

    async def serve(self):
        ctrl_l, intr_l = l2cap_socket(), l2cap_socket()
        for s, psm in ((ctrl_l, PSM_CTRL), (intr_l, PSM_INTR)):
            s.bind((socket.BDADDR_ANY, psm))
            s.listen(1)
        while True:
            ctrl, peer = await self.loop.sock_accept(ctrl_l)
            addr = peer[0]
            log(f"{addr} opened the control channel")
            try:
                intr, _ = await asyncio.wait_for(
                    self.loop.sock_accept(intr_l), 10)
            except asyncio.TimeoutError:
                log("no interrupt channel followed; dropping")
                ctrl.close()
                continue
            await self.attach(ctrl, intr, addr)

    async def connect_out(self, quiet=False):
        try:
            with open(HOST_FILE) as f:
                addr = f.read().strip()
        except FileNotFoundError:
            self.notify("No iPhone paired yet: pair it in its Bluetooth "
                        "settings first")
            return
        socks = []
        try:
            for psm in (PSM_CTRL, PSM_INTR):
                s = l2cap_socket()
                socks.append(s)
                await asyncio.wait_for(
                    self.loop.sock_connect(s, (addr, psm)), 8)
        except (OSError, asyncio.TimeoutError) as err:
            for s in socks:
                s.close()
            if quiet:
                log(f"keep-awake reconnect failed: {err or 'timeout'}")
                return
            self.notify(f"iPhone not reachable ({err or 'timeout'}): is it "
                        "unlocked with Bluetooth on?")
            return
        await self.attach(socks[0], socks[1], addr)

    async def attach(self, ctrl, intr, addr):
        self.close_link()
        ctrl.setblocking(False)
        intr.setblocking(False)
        self.ctrl, self.intr = ctrl, intr
        self.last_kb = b""
        if not os.path.exists(HOST_FILE):
            with open(HOST_FILE, "w") as f:
                f.write(addr + "\n")
            await self.set_pairing(False)
        self.loop.create_task(self.read_channel(ctrl, True))
        self.loop.create_task(self.read_channel(intr, False))
        self.notify("iPhone connected. Super+I hands it keyboard and mouse")

    async def read_channel(self, sock, is_ctrl):
        try:
            while True:
                data = await self.loop.sock_recv(sock, 64)
                if not data:
                    break
                if is_ctrl:
                    self.handle_ctrl(data)
        except OSError:
            pass
        if sock is self.ctrl or sock is self.intr:
            self.close_link()
            if self.mode != "pc":
                self.release_devices()
            self.notify("iPhone disconnected; keyboard and mouse are back")

    def handle_ctrl(self, data):
        kind, param = data[0] >> 4, data[0] & 0x0F
        if kind == 0x1:  # HID_CONTROL; 5 = virtual cable unplug
            if param == 5:
                self.close_link()
            return
        if kind == 0x4:  # GET_REPORT
            rid = data[1] if len(data) > 1 else 0
            if rid == 1:
                reply = b"\xa1\x01" + bytes(8)
            elif rid == 2:
                reply = b"\xa1\x02" + bytes(7)
            else:
                reply = b"\x02"  # handshake: invalid report id
        elif kind == 0x6:  # GET_PROTOCOL -> report protocol
            reply = b"\xa0\x01"
        elif kind == 0x8:  # GET_IDLE
            reply = b"\xa0\x00"
        elif kind in (0x5, 0x7, 0x9):  # SET_REPORT/PROTOCOL/IDLE
            reply = b"\x00"
        else:
            reply = b"\x03"  # handshake: unsupported request
        try:
            self.ctrl.send(reply)
        except OSError:
            pass

    def close_link(self):
        for s in (self.ctrl, self.intr):
            if s:
                s.close()
        self.ctrl = self.intr = None

    def send(self, report):
        if not self.intr:
            return
        try:
            self.intr.send(report)
        except BlockingIOError:
            pass  # link busy; the next report carries the current state
        except OSError:
            self.close_link()

    # -- input devices -----------------------------------------------------

    async def keep_awake(self):
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            idle = now - self.last_input >= PRESENT_FOR
            too_soon = now - self.last_nudge < NUDGE_EVERY
            if self.mode != "pc" or idle or too_soon or not mirroring():
                continue
            # Also paces reconnect attempts when the phone is unreachable.
            self.last_nudge = now
            if not self.intr:
                if self.connecting:
                    continue
                self.connecting = self.loop.create_task(
                    self.connect_out(quiet=True))
                try:
                    await self.connecting
                finally:
                    self.connecting = None
                if not self.intr:
                    continue
            # A lone Shift tap: it types nothing and triggers nothing, but
            # resets iOS's idle timer.  A one-count pointer jiggle does not
            # (tested: the phone locked 5 minutes after the last real input
            # despite two of them).
            self.send(b"\xa1\x01\x02" + bytes(7))
            self.send(b"\xa1\x01" + bytes(8))
            log("keep-awake nudge")

    async def scan_forever(self):
        while True:
            self.scan()
            await asyncio.sleep(3)

    def scan(self):
        for path in evdev.list_devices():
            if path in self.devs:
                continue
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
            except OSError:
                continue
            keys = caps.get(e.EV_KEY, [])
            rel = caps.get(e.EV_REL, [])
            is_mouse = e.REL_X in rel and e.BTN_LEFT in keys
            # No phys path means a uinput device (ydotoold and the like):
            # software input, which must keep reaching the PC.
            if not dev.phys or (e.KEY_A not in keys and not is_mouse):
                dev.close()
                continue
            log(f"using {path}: {dev.name}")
            self.devs[path] = dev
            self.loop.add_reader(dev.fd, self.on_readable, dev)
            if self.mode in ("phone", "to_pc"):
                self.grab(dev)

    def drop(self, dev):
        log(f"lost {dev.path}")
        self.loop.remove_reader(dev.fd)
        self.devs.pop(dev.path, None)
        try:
            dev.close()
        except OSError:
            pass

    def grab(self, dev):
        try:
            dev.grab()
        except OSError as err:
            log(f"cannot grab {dev.path}: {err}")

    def grab_devices(self):
        for dev in self.devs.values():
            self.grab(dev)
        self.mode = "phone"
        self.notify("Keyboard and mouse → iPhone (Super+I to return)")

    def release_devices(self):
        for dev in self.devs.values():
            try:
                dev.ungrab()
            except OSError:
                pass
        self.mode = "pc"

    def on_readable(self, dev):
        try:
            events = list(dev.read())
        except BlockingIOError:
            return
        except OSError:
            self.drop(dev)
            return
        for ev in events:
            self.on_event(ev)

    def on_event(self, ev):
        self.last_input = time.monotonic()
        if ev.type == e.EV_KEY:
            if ev.value == 2:
                return  # autorepeat: iOS repeats held keys itself
            if ev.value:
                self.pressed.add(ev.code)
            else:
                self.pressed.discard(ev.code)
            if ev.code == e.KEY_I and ev.value and self.pressed & META:
                self.toggle()
            elif self.mode == "phone":
                if ev.code in BUTTONS:
                    self.flush_mouse()
                else:
                    self.send_keyboard()
        elif ev.type == e.EV_REL and self.mode == "phone":
            if ev.code == e.REL_X:
                self.dx += ev.value
            elif ev.code == e.REL_Y:
                self.dy += ev.value
            # Negated: iOS applies its own "natural" scrolling to pointer
            # wheels, so passing the PC's direction through comes out inverted.
            elif ev.code == e.REL_WHEEL:
                self.wheel -= ev.value
            elif ev.code == e.REL_HWHEEL:
                self.pan -= ev.value
        elif ev.type == e.EV_SYN and self.mode == "phone":
            self.schedule_mouse()
        self.settle()

    def toggle(self):
        if self.mode == "pc":
            self.mode = "to_phone"
        elif self.mode == "to_phone":
            self.mode = "pc"  # pressed again while still connecting
        elif self.mode == "phone":
            self.send(b"\xa1\x01" + bytes(8))
            self.send(b"\xa1\x02" + bytes(7))
            self.mode = "to_pc"

    def settle(self):
        if self.pressed:
            return
        if self.mode == "to_pc":
            self.release_devices()
            self.notify("Keyboard and mouse → PC")
        elif self.mode == "to_phone":
            if self.intr:
                self.grab_devices()
            elif not self.connecting:
                self.connecting = self.loop.create_task(self.connect_then())

    async def connect_then(self):
        try:
            await self.connect_out()
        finally:
            self.connecting = None
        if self.mode == "to_phone":
            if self.intr:
                self.settle()
            else:
                self.mode = "pc"

    # -- reports -----------------------------------------------------------

    def send_keyboard(self):
        mods, keys = 0, []
        for code in self.pressed:
            if code in MODIFIERS:
                mods |= MODIFIERS[code]
            elif code in KEYMAP and len(keys) < 6:
                keys.append(KEYMAP[code])
        report = bytes([0xA1, 0x01, mods, 0] + keys + [0] * (6 - len(keys)))
        if report != self.last_kb:
            self.last_kb = report
            self.send(report)

    def schedule_mouse(self):
        if self.flush_handle:
            return
        wait = self.last_mouse + MOUSE_INTERVAL - time.monotonic()
        if wait <= 0:
            self.flush_mouse()
        else:
            self.flush_handle = self.loop.call_later(wait, self.flush_mouse)

    def flush_mouse(self):
        if self.flush_handle:
            self.flush_handle.cancel()
            self.flush_handle = None
        buttons = 0
        for code, bit in BUTTONS.items():
            if code in self.pressed:
                buttons |= bit
        x = max(-32767, min(32767, self.dx))
        y = max(-32767, min(32767, self.dy))
        wheel = max(-127, min(127, self.wheel))
        pan = max(-127, min(127, self.pan))
        self.dx -= x
        self.dy -= y
        self.wheel -= wheel
        self.pan -= pan
        self.last_mouse = time.monotonic()
        self.send(struct.pack("<BBBhhbb", 0xA1, 0x02, buttons, x, y,
                              wheel, pan))


async def main():
    loop = asyncio.get_running_loop()
    bridge = Bridge(loop)
    await bridge.setup_bluez()
    loop.create_task(bridge.scan_forever())
    loop.create_task(bridge.keep_awake())
    await bridge.serve()


if __name__ == "__main__":
    asyncio.run(main())
