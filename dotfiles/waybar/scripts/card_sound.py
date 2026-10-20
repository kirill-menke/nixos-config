"""The Sound card, `bar-popup sound`: the default output's volume and mute
and the outputs to pick from, the default input's level and mute, a test
of it, and the inputs to pick from; while something plays, each app's
volume, and while something records, each recording's level and mute.

The outputs also offer the paired Bluetooth headphones and speakers that
aren't connected: a click connects one, turning Bluetooth on first, and
makes it the output once its sound is up. A Bluetooth output says how it
plays (Hi-fi or Call mode, and the codec), and a Bluetooth microphone says
that recording from it puts the headset in call mode. The clean mic
(pipewire/60-clean-mic.conf) is one more input; it says which microphone
it cleans.

The mic test opens the default input only once Test is clicked, and only
until a second click, another input or the card's closing ends it: one
recording, drawn as a level meter under the input's slider, which shows
the level apps get (after that slider and the mute). Recording lists the
apps recording right now, the privacy pill's; a recording's glyph mutes
just that app, and its slider never unmutes it.

It all comes from one `pw-dump --monitor`, started as the card opens: its
first line is the whole graph and each later one what changed, so the card
follows the keys, the bar and pavucontrol as they act, and costs nothing
while nothing changes. An update moves sliders and glyphs in place and
rebuilds a list only when what it shows changes, so it never pulls a row
out from under the pointer. Nothing the card does blocks it: its commands
run in the background, and BlueZ answers asynchronously.
"""

import array
import collections
import json
import math
import signal
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
import cardkit  # noqa: E402
from cardkit import (MOCHA, Latest, Panel, button, label,  # noqa: E402
                     prop, rounded_rect, row, run_async, slider, spawn)
# BlueZ's errors as the Bluetooth card words them.
from card_bluetooth import short_error  # noqa: E402

# Nerd Font glyphs, the same ones the bar uses: the shared ones and the
# card's own.
ICON = cardkit.ICON | {
    "bt-audio": "\U000f00b0",
    "volume": "\U000f057e",
    "muted": "\U000f0581",
    "mixer": "\U000f066a",
    "mic": "\U000f036c",
    "mic-off": "\U000f036d",
    "headset": "\U000f02ce",
    "app": "\U000f08c6",
    "firefox": "\U000f0239",
    "chrome": "\U000f02af",
    "spotify": "\U000f04c7",
    "mpv": "\uf36e",
    "clean-mic": "\U000f147d",  # a waveform: a microphone filtered
    "test": "\U000f040a",       # play: start the mic test
    "test-stop": "\U000f04db",  # stop
}

CSS = b"""
/* Level rows (output, input, apps): the glyph lines up with the list
   rows'. */
.card.sound .level { padding-left: 10px; }
/* Their glyph is the row's mute toggle: sized and coloured like a list
   row's glyph, red while muted. */
.card.sound .mute {
    padding: 0;
    min-width: 24px;
    border-radius: 8px;
    color: @accent;
}
.card.sound .mute.on { color: @red; }
.card.sound .mute:focus { -gtk-outline-radius: 8px; }
/* A paired Bluetooth device that isn't connected: its glyph in overlay1,
   a step under @dim, the bar's colour for what is off or disconnected. */
.card.sound .row.away .icon { color: @overlay1; }
/* The mic test's toggle: its glyph and its word in one flat button, where
   a level row has its mute glyph and its name, so both line up; the word
   in the names' size, not an icon button's. */
.card.sound .test {
    padding: 0;
    border-radius: 8px;
}
.card.sound .test .word { font-size: 14px; }
.card.sound .test:focus { -gtk-outline-radius: 8px; }
"""

NAME_W = 104      # px, the level rows' name column: "Google Chrome" fits
# How long after the card's own last command its change surely is back
# from pw-dump; until then an update could show the state from before it.
SETTLE_MS = 150
POWER_UP_WAIT = 3  # s a power-up may take, as in the Bluetooth card

# The mic test (SND-I11): one pw-cat recording the default input, as an
# app does, so the meter shows the level apps get. Mono, 16 bit, 24 kHz:
# pw-cat writes through stdio's 4 KB buffer, so the card reads 2048
# samples, one peak, about 12 times a second, and draws at most that
# often. (Lab: at 48 kHz it was 23 reads and redraws a second and 5 % of a
# core while the bar moved; at 16 kHz 8, too few for a meter.) 50 ms of
# latency asks for no more wakeups than the graph's default. Its node.name
# keeps it out of Recording, its own row being the meter (the privacy pill
# still shows it, rightly: the microphone is open); application.name is
# what that pill's tooltip says. If the input goes away, the test ends
# instead of moving on to another microphone.
TEST_NODE = "sound-card.mic-test"
TEST_CMD = ["pw-cat", "--record", "--raw", "--rate", "24000",
            "--channels", "1", "--format", "s16", "--latency", "50ms",
            "--media-role", "Test", "-P",
            f"{{ node.name = {TEST_NODE} application.name = \"Mic test\" "
            "node.dont-reconnect = true node.dont-fallback = true }"]
FLOOR_DB = -60.0      # the meter's empty end, dBFS; full is 0
HOT_DB = -3.0         # a peak above this is close to clipping: @warn
RELEASE_DB_S = 20.0   # the meter falls back this fast, dB a second
FRAME_S = 0.08        # and redraws at most 12.5 times a second

# What a remote device offers when it can play our sound (BlueZ's UUIDs):
# headphones, headsets and speakers, not phones or keyboards.
AUDIO_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"

# The BlueZ properties the outputs show; the rest (signal strength while
# scanning, a headset's battery) change often and change nothing here.
BT_SHOWN = {
    "org.bluez.Adapter1": {"Powered"},
    "org.bluez.Device1": {"Alias", "Connected", "Paired", "UUIDs"},
}

# PipeWire's api.bluez5.codec as the Bluetooth world writes it.
CODECS = {"sbc": "SBC", "sbc_xq": "SBC XQ", "aac": "AAC", "aptx": "aptX",
          "aptx_hd": "aptX HD", "aptx_ll": "aptX LL", "ldac": "LDAC",
          "lc3": "LC3", "opus_05": "Opus", "faststream": "FastStream",
          "msbc": "mSBC", "cvsd": "CVSD", "lc3_swb": "LC3-SWB"}

# Port types (PipeWire's `port.type`: the card's UCM device or mixer path,
# a Bluetooth device's form factor) that tell what a sink plays through.
PORT_ICONS = {
    "speaker": "speaker",
    "hdmi": "tv",
    "headphones": "headphones",
    "headset": "headphones",
}

# Apps by executable: the players the bar's mpris module has glyphs for;
# any other app gets a generic one.
APP_ICONS = {
    "firefox": "firefox",
    "chrome": "chrome",  # Google Chrome
    "chromium": "chrome",
    "spotify": "spotify",
    "mpv": "mpv",
}

# The pickers' classes: devices and the filters in front of them (the
# clean mic), so no Audio/Source/Virtual nodes and no sink monitors, which
# WirePlumber also accepts as the default input.
DEFAULT_KEY = {"Audio/Sink": "default.audio.sink",
               "Audio/Source": "default.audio.source"}

# One node as the card shows it: PipeWire id, name, glyph, level in %,
# muted, whether it is the default, tooltip (what an app plays or records),
# the picker row's status line (how a Bluetooth device plays, which
# microphone a filter cleans), a Bluetooth output's address, and a sink's
# or source's node.name (what the mic test records).
Node = collections.namedtuple(
    "Node", "id name glyph pct muted current tip sub addr target",
    defaults=(None, None, None))
# The default sink and source (None without one), the sinks and sources to
# offer, the streams playing and the streams recording right now, and
# whether testing the default input switches a Bluetooth headset to call
# mode.
Audio = collections.namedtuple(
    "Audio", "sink sinks source sources apps rec switches")


def audio(objs):
    """What the card shows, from PipeWire's objects as pw-dump prints
    them. A sink or source whose ports all report `available: no` has
    nothing plugged in (the laptop's HDMI outputs with no monitor attached;
    picking one would silence the machine), so it is left out unless it is
    the default. node.nick is the short name ("LG TV SSCR2", "KM-Bose").
    A filter's source (the clean mic) comes with the microphone it cleans,
    the one WirePlumber linked to it; with none (a machine without a
    microphone) it could only ever be silent, so it is left out, also as
    the default."""
    ports, active, devices, default, nodes = {}, {}, {}, {}, []
    feeds = {}  # node id -> the ids of the nodes linked into it
    for obj in objs:
        kind = obj.get("type", "").rpartition(":")[2]
        info = obj.get("info") or {}
        params = info.get("params") or {}
        if kind == "Device":
            # A sink or source is one profile device of its card. EnumRoute
            # lists each port with the profile devices it can serve, Route
            # the port each active profile device uses, with its volume.
            for route in params.get("EnumRoute") or []:
                for dev in route.get("devices") or []:
                    ports.setdefault((obj["id"], dev), []).append(route)
            for route in params.get("Route") or []:
                active[obj["id"], route.get("device")] = route
            # WirePlumber's Bluetooth microphone is a loopback node that
            # names its device but not the device's API or address.
            devices[obj["id"]] = info.get("props") or {}
        elif (kind == "Metadata"
              and (obj.get("props") or {}).get("metadata.name") == "default"):
            # The node.names that wpctl's @DEFAULT_AUDIO_SINK@ and
            # @DEFAULT_AUDIO_SOURCE@ resolve to.
            for entry in obj.get("metadata") or []:
                if isinstance(entry.get("value"), dict):
                    default[entry.get("key")] = entry["value"].get("name")
        elif kind == "Node":
            nodes.append(obj)
        elif kind == "Link":
            feeds.setdefault(info.get("input-node-id"), []).append(
                info.get("output-node-id"))
    wiring = Wiring(nodes, feeds)
    found = {cls: [] for cls in DEFAULT_KEY}
    sink = source = None
    apps, rec = [], []
    switches = False
    for obj in nodes:
        info = obj.get("info") or {}
        props = info.get("props") or {}
        cls = props.get("media.class")
        key = (props.get("device.id"), props.get("card.profile.device"))
        pct, muted = level(info, active.get(key))
        if cls in ("Stream/Output/Audio", "Stream/Input/Audio"):
            # Playing or recording right now: a paused (corked) stream
            # idles. A filter's or loopback's half has a link group; it is
            # no app (WirePlumber's Bluetooth loopback even records as
            # Stream/Input/Audio/Internal). A peak meter (pavucontrol's)
            # records a monitor and listens to nobody, and the card's own
            # mic test is the meter under Input.
            if (info.get("state") == "running"
                    and "node.link-group" not in props
                    and not truthy(props.get("stream.monitor"))
                    and props.get("node.name") != TEST_NODE):
                here = Node(
                    obj["id"], props.get("application.name")
                    or props.get("node.description") or props.get("node.name"),
                    app_icon(props.get("application.process.binary", "")),
                    pct, muted, False, props.get("media.name"))
                if cls == "Stream/Output/Audio":
                    apps.append(here)
                else:
                    # A recording's media.name says nothing ("RecordStream");
                    # what it records does.
                    rec.append(here._replace(tip=wiring.heard(obj["id"])))
            continue
        name = props.get("node.name")
        # The defaults are whatever the keys act on, picker class or not.
        is_sink = name == default.get("default.audio.sink")
        is_source = name == default.get("default.audio.source")
        if not (cls in found or is_sink or is_source) or name == "auto_null":
            continue
        routes = ports.get(key, [])
        route = active.get(key) or (routes[0] if len(routes) == 1 else {})
        info_kv = route.get("info") or []  # [count, key, value, key, value...]
        port = dict(zip(info_kv[1::2], info_kv[2::2])).get("port.type")
        title = node_title(props)
        device = devices.get(props.get("device.id")) or {}
        api = props.get("device.api") or device.get("device.api") or ""
        glyph = (sink_icon(title, api, port) if cls == "Audio/Sink"
                 else source_icon(api, port))
        sub = addr = None
        if api == "bluez5":
            if cls == "Audio/Sink":
                sub = bt_sub(props)
                addr = (props.get("api.bluez5.address")
                        or device.get("api.bluez5.address"))
            elif truthy(props.get("bluez5.loopback")):
                # WirePlumber's stand-in for the headset's microphone: an
                # app recording from it moves the headset to its call
                # profile (bluetooth.autoswitch-to-headset-profile).
                sub = "Call mode while in use"
        elif cls == "Audio/Source" and "node.link-group" in props:
            # A filter in front of a microphone: the clean mic.
            mic = wiring.cleans(obj["id"])
            if mic is None:
                continue
            glyph = ICON["clean-mic"]
            sub = f"From {node_title(wiring.props[mic])}"
        here = Node(obj["id"], title, glyph, pct, muted, True, None, sub,
                    addr, name)
        sink = here if is_sink else sink
        if is_source:
            source = here
            switches = wiring.switches(obj["id"])
        if cls not in found:
            continue
        current = name == default.get(DEFAULT_KEY[cls])
        if (routes and not current
                and all(r.get("available") == "no" for r in routes)):
            continue
        found[cls].append(here._replace(current=current))
    return Audio(sink, found["Audio/Sink"], source, found["Audio/Source"],
                 apps, rec, switches)


def truthy(value):
    """A boolean property as pw-dump prints it: JSON or a string."""
    return value in (True, "true")


def node_title(props):
    """A sink's, source's or filter's name as the card shows it."""
    return (props.get("node.nick") or props.get("node.description")
            or props.get("node.name"))


class Wiring:
    """Which nodes are linked into which (pw-dump's Link objects), for what
    the card says of a recording (what it records), of a filter (which
    microphone it cleans) and of the mic test (whether it switches a
    Bluetooth headset to call mode)."""

    def __init__(self, nodes, feeds):
        self.props = {obj["id"]: (obj.get("info") or {}).get("props") or {}
                      for obj in nodes}
        self.feeds = feeds
        # The Bluetooth devices whose real microphone is up: in call mode
        # (its HFP source), or one that records in hi-fi too.
        self.bt_live = {p.get("device.id") for p in self.props.values()
                        if p.get("device.api") == "bluez5"
                        and p.get("media.class") == "Audio/Source"
                        and not truthy(p.get("bluez5.loopback"))}

    def peer(self, nid):
        """The node linked into node nid (one stream's links all come from
        one node), or None."""
        return next((p for p in self.feeds.get(nid, ()) if p in self.props),
                    None)

    def cleans(self, nid):
        """The microphone filter source nid cleans: what its group's
        capture stream records (the group's input that isn't an echo
        reference, which records a monitor), or None."""
        group = (self.props.get(nid) or {}).get("node.link-group")
        if group is None:
            return None
        for cap, props in self.props.items():
            if (props.get("node.link-group") == group
                    and props.get("media.class") == "Stream/Input/Audio"
                    and not truthy(props.get("stream.monitor"))):
                return self.peer(cap)
        return None

    def switches(self, nid):
        """Whether recording from node nid switches a Bluetooth headset to
        call mode: nid is WirePlumber's stand-in for its microphone, or a
        filter cleaning that, and the real microphone isn't up yet."""
        for _hop in range(4):  # a filter in front of a filter, at most
            props = self.props.get(nid) or {}
            if truthy(props.get("bluez5.loopback")):
                return props.get("device.id") not in self.bt_live
            nid = self.cleans(nid)
            if nid is None:
                break
        return False

    def heard(self, nid):
        """A recording's tooltip: what it records."""
        peer = self.peer(nid)
        if peer is None:
            return None
        props = self.props[peer]
        if props.get("media.class") == "Audio/Sink":
            return f"Records what {node_title(props)} plays"
        return f"Records {node_title(props)}"


def level(info, route):
    """(percent, muted) of a node as wpctl get-volume reads them
    (WirePlumber's mixer-api): a device's node from its route, any other
    from its own Props; the first channel, on wpctl's cubic scale."""
    candidates = [route.get("props") or {}] if route else []
    candidates += (info.get("params") or {}).get("Props") or []
    for props in candidates:
        if "mute" in props and "channelVolumes" in props:
            vols = props["channelVolumes"]
            linear = vols[0] if vols else props.get("volume", 1.0)
            return round(max(linear, 0) ** (1 / 3) * 100), bool(props["mute"])
    return 0, False


def bt_sub(props):
    """How a Bluetooth output plays: Hi-fi (A2DP, LE Audio) or Call mode
    (HSP/HFP: mono and narrow-band; WirePlumber switches to it while an app
    records from the headset), and the codec."""
    codec = props.get("api.bluez5.codec") or ""
    codec = CODECS.get(codec, codec.upper())
    profile = props.get("api.bluez5.profile") or ""
    mode = "Call mode" if profile.startswith("headset") else "Hi-fi"
    return f"{mode} · {codec}" if codec else mode


def sink_icon(name, api, port):
    """Bluetooth by API (its port type is only the form factor), then by
    port type; the name is a guess for the rest (line out, virtual sinks)."""
    if api == "bluez5":
        return ICON["bt-audio"]
    if port in PORT_ICONS:
        return ICON[PORT_ICONS[port]]
    if "TV" in name or "HDMI" in name:
        return ICON["tv"]
    return ICON["volume"]


def source_icon(api, port):
    """Bluetooth by API, a wired headset's microphone by port type; any
    other input is a microphone."""
    if api == "bluez5":
        return ICON["bt-audio"]
    return ICON["headset" if port == "headset" else "mic"]


def app_icon(binary):
    """An app's glyph by its executable; NixOS wraps some as .x-wrapped."""
    name = binary.removeprefix(".").removesuffix("-wrapped").lower()
    return ICON[APP_ICONS.get(name, "app")]


def picks(nodes):
    """What a picker's rows show of `nodes`; nothing else rebuilds them."""
    return [(n.id, n.name, n.glyph, n.current, n.sub) for n in nodes]


def show_mute(btn, muted, glyph, muted_glyph):
    """A mute toggle's look: the muted glyph in red (.on), or the plain one."""
    btn.get_child().set_text(muted_glyph if muted else glyph)
    ctx = btn.get_style_context()
    (ctx.add_class if muted else ctx.remove_class)("on")


def percent():
    """A level label as wide as "100%": its slider keeps its length as the
    level changes, and every slider ends at the same x."""
    lab = label("", xalign=1.0)
    lab.set_size_request(lab.create_pango_layout("100%").get_pixel_size()[0],
                         -1)
    return lab


class Graph:
    """PipeWire's objects, kept current by one `pw-dump --monitor`. Its
    first line is the whole graph; each later line holds the objects that
    changed, each whole with all its params, or {"id": N, "info": null} for
    one that went. A metadata object lists only the entries that changed,
    and an entry's removal isn't reported at all (pw-dump.c, metadata_dump):
    the card reads only the two defaults, which WirePlumber replaces while
    there is any device and drops only when there is none, when the stale
    name matches no node. The lines are read as they come, on the main
    loop: pw-dump blocks once 64 KB wait unread, and the graph would go
    stale. on_change() runs after each line, and once if pw-dump never
    delivers one."""

    def __init__(self, on_change):
        self.objects = {}   # id -> the object as pw-dump printed it last
        self.ready = False  # the first line is in, or none will come
        self._on_change = on_change
        self._cancel = Gio.Cancellable()
        try:
            self._proc = Gio.Subprocess.new(
                ["pw-dump", "--monitor", "--raw", "--no-colors"],
                Gio.SubprocessFlags.STDOUT_PIPE
                | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self._proc = None
            self.ready = True
            return
        self._lines = Gio.DataInputStream.new(self._proc.get_stdout_pipe())
        self._lines.set_buffer_size(1 << 18)  # the whole graph: ~150 KB
        self._next()

    def stop(self):
        self._cancel.cancel()
        if self._proc is not None:
            self._proc.send_signal(signal.SIGTERM)

    def _next(self):
        self._lines.read_line_async(GLib.PRIORITY_DEFAULT, self._cancel,
                                    self._read)

    def _read(self, lines, result):
        try:
            line, _length = lines.read_line_finish(result)
        except GLib.Error:
            return  # stopped: the card closed
        if line is None:
            # pw-dump ended (no PipeWire): the card keeps what it shows, or
            # says there is nothing.
            if not self.ready:
                self.ready = True
                self._on_change()
            return
        try:
            changed = json.loads(line)
        except ValueError:
            changed = []
        for obj in changed if isinstance(changed, list) else []:
            self._merge(obj)
        self.ready = True
        self._on_change()
        self._next()

    def _merge(self, obj):
        oid = obj.get("id")
        if "type" not in obj:  # {"id": N, "info": null}: it went
            self.objects.pop(oid, None)
            return
        old = self.objects.get(oid)
        if old is not None and "metadata" in obj:
            entries = {(e.get("subject"), e.get("key")): e
                       for e in old.get("metadata") or []}
            entries.update(((e.get("subject"), e.get("key")), e)
                           for e in obj.get("metadata") or [])
            obj["metadata"] = list(entries.values())
        self.objects[oid] = obj


class Level(Gtk.Box):
    """One node's level row: its glyph, which mutes it (the muted glyph in
    red while muted), its name in a fixed column so the sliders line up,
    the slider and the level. update() refreshes it in place."""

    def __init__(self, on_mute, on_slide, muted_glyph, glyph=None):
        # No spacing between the name and the slider: the slider's own
        # padding keeps them apart, and its trough gets the room.
        super().__init__()
        self.get_style_context().add_class("level")
        # glyph None: each node's own (an app's)
        self.glyph, self.muted_glyph = glyph, muted_glyph
        self.node = None
        self.muted = False
        self.mute = button("", "iconbtn", "mute")
        self.mute.set_valign(Gtk.Align.CENTER)
        self.mute.set_margin_end(12)  # as a list row's glyph
        self.mute.connect("clicked", lambda _btn: on_mute(self))
        # Exactly NAME_W: an ellipsizing label asks for no more than its
        # max width in chars, and the size request makes up the rest.
        self.name = label("", ellipsize=Pango.EllipsizeMode.END)
        self.name.set_max_width_chars(1)
        self.name.set_size_request(NAME_W, -1)
        self.scale = slider(0, 100)
        self.pct = percent()
        self.pct.set_margin_start(12)  # as slider rows space their value
        self._slide_id = self.scale.connect(
            "value-changed", lambda scale: on_slide(self, scale.get_value()))
        self.pack_start(self.mute, False, False, 0)
        self.pack_start(self.name, False, False, 0)
        self.pack_start(self.scale, True, True, 0)
        self.pack_start(self.pct, False, False, 0)

    def update(self, node):
        self.node = node
        self.name.set_text(node.name)
        self.name.set_tooltip_text(node.tip)
        with self.scale.handler_block(self._slide_id):
            self.scale.set_value(node.pct)
        self.pct.set_text(f"{node.pct}%")
        self.set_muted(node.muted)

    def set_muted(self, muted):
        self.muted = muted
        show_mute(self.mute, muted, self.glyph or self.node.glyph,
                  self.muted_glyph)
        # Say what a click on the glyph does, as the header button does.
        self.mute.set_tooltip_text("Unmute" if muted else "Mute")


class MicTest:
    """The mic test's recording: one pw-cat on the default input while the
    test runs. Its samples are read as they come, on the main loop (4 KB,
    about 12 times a second), and only their peak is kept: on_level(dB) at
    most every FRAME_S, after a fall-back of RELEASE_DB_S, so the bar drops
    smoothly between words. on_end() if pw-cat ends by itself (the input
    went away, PipeWire stopped). stop() ends it; were the card host to die
    instead, pw-cat would end at its next write, on the closed pipe (lab:
    SIGPIPE). Raises GLib.Error if pw-cat can't start."""

    def __init__(self, source, on_level, on_end):
        self.source = source.id  # the input under test
        self.db = FLOOR_DB
        self._at = self._drawn = time.monotonic()
        self._odd = b""  # a sample split between two reads
        self._on_level, self._on_end = on_level, on_end
        self._cancel = Gio.Cancellable()
        self._proc = Gio.Subprocess.new(
            [*TEST_CMD, "--target", source.target, "-"],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        self._out = self._proc.get_stdout_pipe()
        self._next()

    def stop(self):
        self._cancel.cancel()
        self._proc.send_signal(signal.SIGTERM)

    def _next(self):
        self._out.read_bytes_async(8192, GLib.PRIORITY_DEFAULT, self._cancel,
                                   self._read)

    def _read(self, out, result):
        try:
            data = out.read_bytes_finish(result).get_data()
        except GLib.Error:
            return  # stopped
        if self._cancel.is_cancelled():
            return  # stopped as this read landed
        if not data:
            self._on_end()
            return
        data = self._odd + data
        whole = len(data) & ~1
        self._odd = data[whole:]
        if whole:
            samples = array.array("h", data[:whole])
            peak = max(max(samples), -min(samples))
            db = 20 * math.log10(peak / 32768) if peak else FLOOR_DB
            now = time.monotonic()
            self.db = max(db, self.db - RELEASE_DB_S * (now - self._at),
                          FLOOR_DB)
            self._at = now
            if now - self._drawn >= FRAME_S:
                self._drawn = now
                self._on_level(self.db)
        self._next()


class TestRow(Gtk.Box):
    """The mic test under the input's level row: a toggle, its glyph and
    word where a level row has its mute glyph and name, and the meter where
    it has its slider, the trough as long and as thick. The meter's place
    takes a note instead while one is due (what a test would switch, why
    the last one stopped)."""

    def __init__(self, on_toggle):
        super().__init__()
        self.get_style_context().add_class("level")
        face = Gtk.Box(spacing=12)  # as the level rows' mute glyph margin
        self.glyph = label("", "icon", xalign=0.5)
        self.glyph.set_size_request(24, -1)
        self.word = label("", "word")
        self.word.set_size_request(NAME_W, -1)
        face.pack_start(self.glyph, False, False, 0)
        face.pack_start(self.word, False, False, 0)
        self.toggle = button(face, "iconbtn", "test")
        self.toggle.set_valign(Gtk.Align.CENTER)
        self.toggle.connect("clicked", lambda _btn: on_toggle())
        self.meter = Gtk.DrawingArea()
        self.meter.connect("draw", self._draw)
        self.note = label("", "sub", ellipsize=Pango.EllipsizeMode.END)
        self.note.set_margin_start(12)  # where the trough starts
        self.view = Gtk.Stack()
        self.view.add_named(self.meter, "meter")
        self.view.add_named(self.note, "note")
        self.pack_start(self.toggle, False, False, 0)
        self.pack_start(self.view, True, True, 0)
        # The trough ends where the slider's above does: before the level
        # label and its margin.
        self._end = 12 + percent().get_size_request()[0] + 12
        self.db = None    # the level shown; None: no test
        self._px = None   # its fill, px, as last drawn
        self.state = None

    def show(self, running, note=None, bad=False):
        """Idle (with a note, if any) or running; changes only what
        differs, so an update leaves a running meter alone."""
        if self.state == (running, note, bad):
            return
        self.state = (running, note, bad)
        self.glyph.set_text(ICON["test-stop" if running else "test"])
        self.word.set_text("Stop" if running else "Test")
        self.toggle.set_tooltip_text(
            "Stop the test" if running
            else "Show this input's level as apps hear it")
        self.note.set_text(note or "")
        ctx = self.note.get_style_context()
        (ctx.add_class if bad else ctx.remove_class)("bad")
        self.view.set_visible_child_name("note" if note and not running
                                         else "meter")
        self.db = FLOOR_DB if running else None
        self._px = None
        self.meter.queue_draw()

    def set_level(self, db):
        self.db = db
        px = self._fill(self.meter.get_allocated_width())
        if px != self._px:  # a redraw only for a change on screen
            self.meter.queue_draw()

    def _fill(self, width):
        if self.db is None:
            return 0
        span = max(width - 12 - self._end, 0)
        return round(span * (self.db - FLOOR_DB) / -FLOOR_DB)

    def _draw(self, area, cr):
        width, height = area.get_allocated_width(), area.get_allocated_height()
        span = max(width - 12 - self._end, 0)
        top = (height - 6) / 2
        rounded_rect(cr, 12, top, span, 6, 3)
        cr.set_source_rgb(*MOCHA["raised"])  # the slider's trough
        cr.fill()
        self._px = self._fill(width)
        if self._px > 0:
            rounded_rect(cr, 12, top, self._px, 6, 3)
            cr.set_source_rgb(*MOCHA["warn" if self.db > HOT_DB else "ok"])
            cr.fill()
        return False


class SoundPanel(Panel):
    """The default output's level and the output picker, with the paired
    Bluetooth audio devices to connect; the default input's level, its
    test and the input picker; each recording app's level; each playing
    app's level. A level row's glyph mutes it. Picking the output is worth
    the primary click because `play` (the desktop PC's) keys its whole
    behaviour (Atmos bitstreaming, 4K/HDR mode switch) off which sink is
    default."""

    title = "Sound"
    css = "sound"

    def __init__(self, app):
        super().__init__(app)
        # pavucontrol mutes and switches the default input without the
        # mic-mute LED: osd puts the LED right as pavucontrol closes.
        self.header_button(ICON["mixer"], "Mixer", "sh", "-c",
                           "pavucontrol; osd mic sync")

        self.pending = 0      # the card's own commands still running
        self.quiet_at = 0.0   # monotonic time its last one is surely back
        self.levels = {}      # node id -> Latest, writing its slider's level
        self.unmute = set()   # node ids whose next level write unmutes them
        self._update_id = None
        self.test = None       # the MicTest running, if any
        self.test_note = None  # (text, bad): why the last test stopped
        # BlueZ, for the paired Bluetooth audio devices the outputs offer:
        # None until it answers, and for good without Bluetooth (the
        # desktop PC may have none). Asked without starting bluetoothd.
        self.bluez = None
        self.bt_pending = {}     # device path -> (status text, is_error)
        self.bt_waiting = set()  # clicked while Bluetooth was off
        self.bt_timer = None     # gives up on a power-up never reported
        self.want = None         # address whose output becomes the default
        Gio.DBusObjectManagerClient.new_for_bus(
            Gio.BusType.SYSTEM,
            Gio.DBusObjectManagerClientFlags.DO_NOT_AUTO_START,
            "org.bluez", "/", None, None, None, self._bluez_ready, None)
        # The keys, the bar and pavucontrol change all of this too; follow
        # them while the card is open.
        self.graph = Graph(self._changed)
        self.connect("destroy", self._on_destroy)
        self.fill()

    def fill(self):
        self.outputs = self._section()
        self.inputs = self._section()
        self.recording = self._section()
        self.playing = self._section()
        self.state = None   # the Audio shown
        self.out = None     # the output's Level row
        self.mic = None     # the input's Level row
        self.tester = None  # the input's TestRow
        self.rec_rows = []  # the recordings' Level rows, in state.rec order
        self.app_rows = []  # the apps' Level rows, in self.state.apps order
        # What each list shows: an update rebuilds one only when it changes.
        self.outputs_shown = self.inputs_shown = None
        self.rec_shown = self.apps_shown = None
        self._changed()

    def _on_destroy(self, _card):
        self.graph.stop()
        # The microphone closes with the card.
        if self.test is not None:
            self.test.stop()
            self.test = None
        if self._update_id is not None:
            GLib.source_remove(self._update_id)
            self._update_id = None

    def _section(self):
        """A part of the body that hides while it has nothing to show."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_no_show_all(True)
        self.body.pack_start(box, False, False, 0)
        return box

    @staticmethod
    def _set_rows(box, title, rows):
        """Replace a section's rows under its title; empty, it hides."""
        for child in box.get_children():
            child.destroy()
        if rows:
            box.pack_start(label(title, "seclabel"), False, False, 0)
            for widget in rows:
                box.pack_start(widget, False, False, 0)
            for child in box.get_children():
                child.show_all()
        box.set_visible(bool(rows))

    def _show(self, new):
        """Bring the card up to `new`: values in place, a list rebuilt only
        when what it shows changes."""
        self.state = new
        away = self._away(new)
        outputs = self._offered(new.sinks, bool(away))
        shown = (new.sink is not None, picks(outputs), away)
        if shown != self.outputs_shown:
            self.outputs_shown = shown
            self.out = None
            if new.sink:
                self.out = Level(self._on_row_mute, self._on_row_level,
                                 ICON["muted"], glyph=ICON["volume"])
            rows = [self.out] if self.out else []
            rows += [self._pick(n, self._on_sink) for n in outputs]
            rows += [self._away_row(*dev) for dev in away]
            self._set_rows(self.outputs, "Output",
                           rows or [label("No outputs", "empty")])
        if self.out:
            self.out.update(new.sink)

        offered = self._offered(new.sources)
        shown = (new.source is not None, picks(offered))
        if shown != self.inputs_shown:
            self.inputs_shown = shown
            self.mic = self.tester = None
            if new.source:
                self.mic = Level(self._on_row_mute, self._on_row_level,
                                 ICON["mic-off"], glyph=ICON["mic"])
                self.tester = TestRow(self._on_test)
            rows = [self.mic, self.tester] if self.mic else []
            rows += [self._pick(n, self._on_source) for n in offered]
            self._set_rows(self.inputs, "Input", rows)
        if self.mic:
            self.mic.update(new.source)
        self._show_test(new)

        # Who is listening: each one muted on its own records silence, the
        # input's mute above silences them all.
        shown = [a.id for a in new.rec]
        if shown != self.rec_shown:
            self.rec_shown = shown
            self.rec_rows = [Level(self._on_row_mute, self._on_row_level,
                                   ICON["mic-off"]) for _ in new.rec]
            self._set_rows(self.recording, "Recording", self.rec_rows)
        for widget, node in zip(self.rec_rows, new.rec):
            widget.update(node)

        shown = [a.id for a in new.apps]
        if shown != self.apps_shown:
            self.apps_shown = shown
            self.app_rows = [Level(self._on_row_mute, self._on_row_level,
                                   ICON["muted"]) for _ in new.apps]
            self._set_rows(self.playing, "Apps", self.app_rows)
        for widget, node in zip(self.app_rows, new.apps):
            widget.update(node)
        self._adopt(new)

    @staticmethod
    def _offered(nodes, more=False):
        """The outputs or inputs to pick from: none when the only one is
        the one in use, which the level row above names already, and
        nothing `more` (a paired headset) joins the list."""
        return ([] if len(nodes) == 1 and nodes[0].current and not more
                else nodes)

    def _pick(self, node, handler):
        btn = row(node.glyph, node.name, node.sub, on=node.current)
        btn.connect("clicked", handler, node)
        return btn

    # --- reading ---------------------------------------------------------

    def _changed(self):
        """Show the graph as it now stands, on idle: the lines one change
        brings (a stream starting adds a node, its ports, its links) make
        one update."""
        if self._update_id is None and not self.dead:
            self._update_id = GLib.idle_add(self._update)

    def _update(self):
        self._update_id = None
        if self.dead or not self.graph.ready:
            return GLib.SOURCE_REMOVE
        # The card shows its own changes as it makes them: an update from
        # before one has come back would undo it on screen for a moment,
        # and a slider held still mid-drag mustn't move under the pointer.
        # Look again once they are through.
        wait = self.quiet_at - time.monotonic()
        if self.pending or wait > 0 or Gtk.grab_get_current() is not None:
            self._update_id = GLib.timeout_add(
                max(SETTLE_MS, int(wait * 1000)), self._update)
            return GLib.SOURCE_REMOVE
        self._show(audio(self.graph.objects.values()))
        return GLib.SOURCE_REMOVE

    # --- writing ---------------------------------------------------------

    def _command(self, *args, then=None):
        """Run a command in the background (wpctl takes about 35 ms),
        then(ok) once it is over; the card looks again after it."""
        self.pending += 1

        def done(ok, _out, _err):
            self.pending -= 1
            self.quiet_at = time.monotonic() + SETTLE_MS / 1000
            if then is not None:
                then(ok)
            self._changed()  # also shows the truth if it failed
        run_async(done, *args)

    def _on_sink(self, _btn, node):
        # Running streams follow the default sink in PipeWire.
        self._command("wpctl", "set-default", str(node.id))

    def _on_source(self, _btn, node):
        # The key's LED shows whether the default input is muted: osd sets
        # it from the new one's mute.
        self._command("wpctl", "set-default", str(node.id),
                      then=lambda _ok: spawn("osd", "mic", "sync"))

    def _on_row_mute(self, row):
        muted = not row.muted
        row.set_muted(muted)
        if row is self.mic:
            # The mic-mute key's path, without its popup: the default
            # input, then osd sets the key's LED from its mute.
            self._command("wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@",
                          str(int(muted)),
                          then=lambda _ok: spawn("osd", "mic", "sync"))
        else:
            self._command("wpctl", "set-mute", str(row.node.id),
                          str(int(muted)))

    def _on_row_level(self, row, value):
        pct = int(value)
        row.pct.set_text(f"{pct}%")
        node = row.node.id
        # The output's or an app's level unmutes it, as the volume keys do;
        # a muted microphone stays off until it is unmuted, and so does a
        # muted recording.
        if row.muted and row is not self.mic and row not in self.rec_rows:
            row.set_muted(False)
            self.unmute.add(node)
        # A drag moves the slider far more often than wpctl can follow:
        # one write at a time, and the last level always lands.
        writer = self.levels.get(node)
        if writer is None:
            writer = self.levels[node] = Latest(
                lambda pct, done: self._set_level(node, pct, done))
        writer.set(pct)

    def _set_level(self, node, pct, done):
        """One slider write: the level, then the unmute it may owe."""
        unmute = node in self.unmute
        self.unmute.discard(node)

        def level_set(ok):
            if unmute:
                self._command("wpctl", "set-mute", str(node), "0", then=done)
            else:
                done(ok)
        self._command("wpctl", "set-volume", str(node), f"{pct / 100:.2f}",
                      then=level_set)

    # --- mic test --------------------------------------------------------

    def _on_test(self):
        """Test starts a recording of the default input, Stop ends it."""
        if self.test is not None:
            self._stop_test()
        elif self.state is not None and self.state.source is not None:
            self.test_note = None
            try:
                self.test = MicTest(self.state.source, self._test_level,
                                    self._test_ended)
            except GLib.Error:
                self.test_note = (self.state.source.id,
                                  "Couldn't start the test", True)
        if self.state is not None:
            self._show_test(self.state)

    def _show_test(self, new):
        """A test is of the input it began on: another input, or none,
        ends it. Idle, the row says what a test would switch, or why the
        last one on this input stopped."""
        source = new.source.id if new.source is not None else None
        if self.test is not None and self.test.source != source:
            self._stop_test()
        if self.tester is None:
            return
        if self.test is not None:
            self.tester.show(True)
        elif self.test_note is not None and self.test_note[0] == source:
            self.tester.show(False, *self.test_note[1:])
        elif new.switches:
            # The headset stays in call mode for as long as the test runs.
            self.tester.show(False, "Switches to call mode")
        else:
            self.tester.show(False)

    def _stop_test(self):
        self.test.stop()
        self.test = None

    def _test_level(self, db):
        if self.tester is not None and not self.dead:
            self.tester.set_level(db)

    def _test_ended(self):
        """pw-cat ended by itself: the input went away, or PipeWire did."""
        if self.test is None:
            return
        source, self.test = self.test.source, None
        if not self.dead:
            self.test_note = (source, "The test stopped", True)
            if self.state is not None:
                self._show_test(self.state)

    # --- Bluetooth -------------------------------------------------------

    def _bluez_ready(self, _source, result, _data):
        try:
            manager = Gio.DBusObjectManagerClient.new_for_bus_finish(result)
        except GLib.Error:
            return  # no system bus: the outputs offer only what plays
        if self.dead:
            return
        self.bluez = manager
        # Devices paired, removed or connected elsewhere (the Bluetooth
        # card, the headset itself) change the outputs too.
        for name in ("object-added", "object-removed", "interface-added",
                     "interface-removed"):
            manager.connect(name, lambda *_: self._changed())
        manager.connect("interface-proxy-properties-changed",
                        self._bt_changed)
        self._changed()

    def _away(self, new):
        """The paired devices that can play sound but have no output now
        (not connected, or not yet): [(path, name, status, failed)]."""
        if self.bluez is None:
            return []
        objs = self.bluez.get_objects()
        adapters = [a for o in objs
                    if (a := o.get_interface("org.bluez.Adapter1"))]
        powered = bool(adapters) and bool(prop(adapters[0], "Powered", False))
        playing = {s.addr for s in new.sinks if s.addr}
        found = []
        for obj in objs:
            dev = obj.get_interface("org.bluez.Device1")
            if (dev is None or not prop(dev, "Paired", False)
                    or AUDIO_SINK_UUID not in (prop(dev, "UUIDs") or [])
                    or prop(dev, "Address") in playing):
                continue
            path = dev.get_object_path()
            if path in self.bt_pending:
                sub, bad = self.bt_pending[path]
            elif prop(dev, "Connected", False):
                sub, bad = "Connected", False  # its sound isn't up (yet)
            elif powered:
                sub, bad = "Not connected", False
            else:
                sub, bad = "Turn on and connect", False
            found.append((path, str(prop(dev, "Alias", path)), sub, bad))
        return sorted(found, key=lambda dev: dev[1].lower())

    def _away_row(self, path, name, sub, bad):
        btn = row(ICON["bt-audio"], name, sub, sub_bad=bad)
        btn.get_style_context().add_class("away")
        if bad:
            btn.set_tooltip_text(sub)  # in case it doesn't fit
        btn.connect("clicked", self._on_away, path)
        return btn

    def _on_away(self, _btn, path):
        """Connect a paired device, turning Bluetooth on first if it is
        off; _adopt makes its output the default once it appears. What is
        under way finishes even if the card closes meanwhile."""
        dev = self.bluez.get_interface(path, "org.bluez.Device1")
        if dev is None or not self.bt_pending.get(path, ("", True))[1]:
            return  # gone, or already on its way
        self.want = prop(dev, "Address")
        adapter = self.bluez.get_interface(prop(dev, "Adapter", "/"),
                                           "org.bluez.Adapter1")
        if adapter is not None and not prop(adapter, "Powered", False):
            self.bt_waiting.add(path)
            self.bt_pending[path] = ("Turning Bluetooth on…", False)
            args = GLib.Variant("(ssv)", ("org.bluez.Adapter1", "Powered",
                                          GLib.Variant("b", True)))
            adapter.call("org.freedesktop.DBus.Properties.Set", args,
                         Gio.DBusCallFlags.NONE, 5000, None,
                         self._bt_powered, None)
        else:
            self._bt_connect(dev)
        self._changed()

    def _bt_powered(self, adapter, result, _data):
        try:
            adapter.call_finish(result)
        except GLib.Error as err:
            self._bt_give_up(short_error(err))
        else:
            # BlueZ reports Powered a moment later, and _bt_changed connects
            # the devices waiting for it; they give up if it never does.
            if self.bt_waiting and self.bt_timer is None:
                self.bt_timer = GLib.timeout_add_seconds(POWER_UP_WAIT,
                                                         self._bt_overdue)
        self._changed()

    def _bt_overdue(self):
        self.bt_timer = None
        self._bt_give_up("Bluetooth didn't turn on")
        self._changed()
        return GLib.SOURCE_REMOVE

    def _bt_give_up(self, text):
        """The adapter isn't coming up: say why on the rows waiting."""
        for path in self.bt_waiting:
            self.bt_pending[path] = (text, True)
        self.bt_waiting.clear()
        self.want = None

    def _bt_changed(self, _manager, _obj, proxy, changed, _invalidated):
        iface = proxy.get_interface_name()
        changed = changed.unpack()
        if not BT_SHOWN.get(iface, set()) & changed.keys():
            return
        if iface == "org.bluez.Adapter1" and changed.get("Powered"):
            if self.bt_timer is not None:
                GLib.source_remove(self.bt_timer)
                self.bt_timer = None
            waiting, self.bt_waiting = self.bt_waiting, set()
            for path in waiting:
                dev = self.bluez.get_interface(path, "org.bluez.Device1")
                if dev is None:
                    self.bt_pending.pop(path, None)
                else:
                    self._bt_connect(dev)
        elif iface == "org.bluez.Device1" and "Connected" in changed:
            path = proxy.get_object_path()
            if self.bt_pending.get(path, ("", False))[1]:
                del self.bt_pending[path]  # an error from before, moot now
        self._changed()

    def _bt_connect(self, dev):
        path = dev.get_object_path()
        self.bt_pending[path] = ("Connecting…", False)
        dev.call("Connect", None, Gio.DBusCallFlags.NONE, 30000, None,
                 self._bt_done, path)
        self._changed()

    def _bt_done(self, dev, result, path):
        try:
            dev.call_finish(result)
        except GLib.Error as err:
            if "Error.AlreadyConnected" in err.message:
                self.bt_pending.pop(path, None)  # it connected by itself
            else:
                self.bt_pending[path] = (short_error(err), True)
                if self.want == prop(dev, "Address"):
                    self.want = None
        else:
            self.bt_pending.pop(path, None)
            if not prop(dev, "Trusted", False):
                # As the Bluetooth card does: BlueZ then lets the headset
                # connect by itself the next time it is switched on.
                args = GLib.Variant("(ssv)", ("org.bluez.Device1", "Trusted",
                                              GLib.Variant("b", True)))
                dev.call("org.freedesktop.DBus.Properties.Set", args,
                         Gio.DBusCallFlags.NONE, 5000, None, None)
        self._changed()

    def _adopt(self, new):
        """The device clicked has its output now: make it the default
        (with another output picked by hand before, WirePlumber wouldn't)."""
        if self.want is None:
            return
        sink = next((s for s in new.sinks if s.addr == self.want), None)
        if sink is None:
            return
        self.want = None
        if not sink.current:
            self._command("wpctl", "set-default", str(sink.id))
