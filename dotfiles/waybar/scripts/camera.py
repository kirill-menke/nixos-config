#!/usr/bin/env python3
"""The bar's camera module: which apps have the webcam open.

  camera watch    JSON lines for the waybar module, for as long as the bar
                  runs (config.jsonc gives it no "interval")

Waybar's privacy module sees the microphone and screen shares because
they are PipeWire streams. Most apps open the webcam themselves, through
V4L2 (Chrome, Signal and Discord, all Chromium inside, and Firefox unless
media.webrtc.camera.allow-pipewire is set), which PipeWire never sees. So
this watches the camera's device nodes instead: inotify reports every open
and close of a /dev/video*, and /dev itself reports a camera plugged in or
out (a USB webcam on the desktop PC) and the access udev and logind grant
to one. Between events it sleeps in one blocking read, so it costs nothing
while the camera is idle. After the last event of a burst it waits SETTLE
seconds and then lists the processes that hold a /dev/video* open
(/proc/<pid>/fd): a browser opens every camera for a moment just to list
them, which must not flash the module.

An app is named after the program it runs (/proc/<pid>/exe), so a helper
process counts as its app. Electron apps all run one electron binary; for
them it is the app.asar their main process was given (Signal's lies in
.../signal-desktop/). An app that uses the camera through PipeWire leaves
PipeWire holding the device; its name then comes from the PipeWire stream
linked to that camera. The privacy module shows that stream as a screen
share as well (it counts every video stream an app reads), which is
PipeWire's doing, not a share.

With no holder it prints {}, which hides the module (as attention does).
"""

import ctypes
import json
import os
import re
import select
import struct
import subprocess
import sys
import time

DEV = "/dev"
PROC = "/proc"
VIDEO = re.compile(r"video\d+")
SETTLE = 0.4  # s of quiet after an open or close before the holders count
BURST = 2.0   # s after a burst's first event they count anyway, should
              # the opens and closes never pause

CAMERA = "\U000f05a0"  # md-webcam

# inotify(7)
IN_ATTRIB, IN_CLOSE_WRITE, IN_CLOSE_NOWRITE, IN_OPEN = 0x04, 0x08, 0x10, 0x20
IN_CREATE, IN_DELETE = 0x100, 0x200
EVENT = struct.Struct("iIII")  # wd, mask, cookie, length of the name

# Programs (the executable's file name, NixOS's ".x-wrapped" stripped) by
# the names the bar uses elsewhere; any other shows as it is.
APPS = {
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "firefox": "Firefox",
    "signal-desktop": "Signal",
    "discord": "Discord",
}
# Programs that hold the camera for the apps reading it through PipeWire:
# PipeWire itself for its V4L2 nodes, WirePlumber for libcamera's.
BROKERS = {"pipewire", "wireplumber"}

libc = ctypes.CDLL(None, use_errno=True)


def events(buf):
    """(watch, mask, name) of each inotify event in `buf`."""
    off = 0
    while off < len(buf):
        wd, mask, _cookie, size = EVENT.unpack_from(buf, off)
        name = buf[off + EVENT.size:off + EVENT.size + size]
        yield wd, mask, name.rstrip(b"\0").decode(errors="replace")
        off += EVENT.size + size


def watch_devices(fd, watched):
    """Watch the open and close of each /dev/video* not yet watched. One
    the user may not read yet (udev sets its access just after creating
    it) can't be watched; /dev reports the change, and this runs again."""
    for name in os.listdir(DEV):
        if VIDEO.fullmatch(name) and name not in watched:
            wd = libc.inotify_add_watch(
                fd, os.path.join(DEV, name).encode(),
                IN_OPEN | IN_CLOSE_WRITE | IN_CLOSE_NOWRITE)
            if wd >= 0:
                watched[name] = wd


def holders():
    """{pid: {/dev/video* paths it holds open}}. Only the user's own
    processes can be read, which is every app here."""
    found = {}
    for pid in os.listdir(PROC):
        if not pid.isdigit():
            continue
        fds = os.path.join(PROC, pid, "fd")
        try:
            names = os.listdir(fds)
        except OSError:  # gone, or another user's
            continue
        for name in names:
            try:
                target = os.readlink(os.path.join(fds, name))
            except OSError:
                continue
            # A camera unplugged while open reads "/dev/video4 (deleted)"
            # and shows nothing any more.
            folder, _, node = target.rpartition("/")
            if folder == DEV and VIDEO.fullmatch(node):
                found.setdefault(int(pid), set()).add(target)
    return found


def _exe(pid):
    """The file process `pid` runs, or None once it's gone."""
    try:
        exe = os.readlink(os.path.join(PROC, str(pid), "exe"))
    except OSError:
        return None
    return exe.removesuffix(" (deleted)")  # the store path was collected


def _parent(pid):
    """The parent of process `pid`, or None once it's gone."""
    try:
        with open(os.path.join(PROC, str(pid), "stat"), "rb") as fh:
            # "pid (comm) state ppid ...", where comm may hold ") "
            return int(fh.read().rpartition(b")")[2].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _asar(pid, exe):
    """The app folder of the .asar that `pid`, or its nearest ancestor
    running the same `exe`, was given: an Electron app's main process,
    which starts the helper that opens the camera."""
    while pid is not None and pid > 1 and _exe(pid) == exe:
        try:
            with open(os.path.join(PROC, str(pid), "cmdline"), "rb") as fh:
                args = fh.read().split(b"\0")[1:]
        except OSError:
            return None
        for arg in args:
            if arg.endswith(b".asar"):
                folder = os.path.dirname(os.fsdecode(arg))
                if os.path.basename(folder) == "resources":  # outside Nix
                    folder = os.path.dirname(folder)
                return os.path.basename(folder)
        pid = _parent(pid)
    return None


def app(pid):
    """(program, name) of the app process `pid` belongs to, program being
    the key into APPS and BROKERS; None once the process is gone."""
    exe = _exe(pid)
    if exe is None:
        return None
    program = os.path.basename(exe)
    if program == "electron":
        program = _asar(pid, exe) or program
    program = program.removeprefix(".").removesuffix("-wrapped")
    return program.lower(), APPS.get(program.lower(), program)


def pipewire_readers(devices):
    """The apps PipeWire passes the picture of `devices` (/dev/video*
    paths) to: the streams linked to their V4L2 nodes, or to a libcamera
    node, which names no device."""
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True,
                             timeout=3).stdout
        objects = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return set()
    props, links = {}, []
    for obj in objects:
        info = obj.get("info") or {}
        if obj.get("type") == "PipeWire:Interface:Node":
            props[obj.get("id")] = info.get("props") or {}
        elif obj.get("type") == "PipeWire:Interface:Link":
            links.append((info.get("output-node-id"),
                          info.get("input-node-id")))
    cameras = {node for node, p in props.items()
               if p.get("api.v4l2.path") in devices
               or p.get("device.api") == "libcamera"}
    readers = (props.get(reader, {}) for source, reader in links
               if source in cameras)
    return {p.get("application.name") or p.get("node.name") or "An app"
            for p in readers}


def status():
    """The module's JSON: {} while no app has the camera."""
    apps, piped = set(), set()
    for pid, devices in holders().items():
        found = app(pid)
        if found is None:  # closed the camera and quit since
            continue
        program, name = found
        if program in BROKERS:
            piped |= devices
        else:
            apps.add(name)
    if piped:
        # With no stream linked, PipeWire still holds the camera after its
        # last reader went, until WirePlumber suspends the node.
        apps |= ({f"{a} (through PipeWire)" for a in pipewire_readers(piped)}
                 or {"PipeWire"})
    if not apps:
        return {}
    return {"text": CAMERA,
            "tooltip": "Camera in use: " + ", ".join(sorted(apps))}


def watch():
    fd = libc.inotify_init1(os.O_CLOEXEC)
    if fd < 0:
        return 1
    # Cameras plugged in or out, and the access udev and logind grant them
    dev_wd = libc.inotify_add_watch(fd, DEV.encode(),
                                    IN_CREATE | IN_DELETE | IN_ATTRIB)
    watched = {}  # /dev/video* name -> its watch
    shown = None
    while True:
        watch_devices(fd, watched)
        line = json.dumps(status())
        if line != shown:
            try:
                print(line, flush=True)
            except BrokenPipeError:  # the bar is gone
                return 0
            shown = line
        # Sleep until a camera opens, closes, comes or goes; then wait out
        # the rest of the burst, until SETTLE s pass without an event (or
        # BURST s since its first).
        buf = os.read(fd, 4096)
        end = time.monotonic() + BURST
        while True:
            for wd, mask, name in events(buf):
                if wd == dev_wd and mask & IN_DELETE:
                    watched.pop(name, None)  # its watch went with it
            wait = min(SETTLE, end - time.monotonic())
            if wait <= 0 or not select.select([fd], [], [], wait)[0]:
                break
            buf = os.read(fd, 4096)


def main(argv):
    if argv[1:] == ["watch"]:
        return watch()
    print("usage: camera watch", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
