"""Phone remote and media library for mpv.

Serves a small touch UI: the films and TV shows on disk, and transport controls
for whatever is playing.  Tapping a tile wakes the TV, selects the PC input and
starts playback, all via `play`.

Two libraries, switched between with a tab:
    ~/Videos/movies                        one folder (or loose file) per film
    ~/Videos/shows/<Series>/<Season N>/     one folder per series, then seasons

Designed to be added to the iOS home screen: the meta tags below make Safari
open it fullscreen with its own icon and no browser chrome, and a looping silent
audio element keeps a media session alive so the lock screen shows transport
controls.

Deliberately dependency-free (stdlib only) so it can be dropped into a Nix
python3 without a package set.  Anything needing the TV's own API goes through
the `tv` command -- for volume over its unix socket, so that a slider being
dragged is not paying for a fresh SSAP connection per update.
"""

import json
import hashlib
import os
import posixpath
import queue
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
SOCKET_PATH = os.environ.get("MPV_SOCKET", os.path.join(RUNTIME, "mpv-remote.sock"))
TV_SOCKET = os.environ.get("TV_SOCKET", os.path.join(RUNTIME, "tv.sock"))
PORT = int(os.environ.get("MPV_REMOTE_PORT", "8322"))
LIBRARY = os.path.expanduser(os.environ.get("MPV_LIBRARY", "~/Videos/movies"))
SHOWS = os.path.expanduser(os.environ.get("MPV_SHOWS", "~/Videos/shows"))
SOURCES_FILE = os.path.expanduser(
    os.environ.get("MPV_SOURCES", "~/.config/mpv-remote/sources.json"))
NETRC = os.path.join(RUNTIME, "mpv-remote.netrc")
PLAY_BIN = os.environ.get("PLAY_BIN", "play")
TV_BIN = os.environ.get("TV_BIN", "tv")
CACHE = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "mpv-remote"
)
THUMBS = os.path.join(CACHE, "thumbs")

VIDEO_EXT = (".mkv", ".mp4", ".m2ts", ".avi", ".mov", ".webm")

# Catppuccin Mocha, to match the rest of the desktop.
BASE, MANTLE, SURFACE = "#1e1e2e", "#181825", "#313244"
TEXT, SUBTEXT = "#cdd6f4", "#a6adc8"
BLUE, GREEN, RED, YELLOW, LAVENDER = "#89b4fa", "#a6e3a1", "#f38ba8", "#f9e2af", "#b4befe"


# --------------------------------------------------------------------------
# mpv IPC
# --------------------------------------------------------------------------

def mpv_command(command, timeout=1.5):
    """Send one command to mpv and return its reply, or None if mpv is not up."""
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        s.connect(SOCKET_PATH)
        s.sendall((json.dumps({"command": command}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        for line in buf.split(b"\n"):
            if not line.strip():
                continue
            msg = json.loads(line)
            # Skip asynchronous event lines; we want the command reply.
            if "event" in msg:
                continue
            return msg
    except (OSError, ValueError):
        return None
    return None


def get_prop(name):
    reply = mpv_command(["get_property", name])
    if reply and reply.get("error") == "success":
        return reply.get("data")
    return None


# --------------------------------------------------------------------------
# TV volume, via the `tv serve` bridge
# --------------------------------------------------------------------------

def tv_request(req, timeout=8):
    """One line-JSON round trip to `tv serve`.  None if the bridge is down.

    Same shape as mpv_command above.  The bridge keeps a single SSAP
    connection open, so a setVolume costs ~10ms here instead of the ~370ms a
    fresh connection would -- the difference between a slider that tracks your
    finger and one that lurches.
    """
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        s.connect(TV_SOCKET)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.split(b"\n")[0])
    except (OSError, ValueError):
        return None


def tv_volume(req=None):
    """Volume state, or the result of changing it.

    A bare state request deliberately does not ask the bridge to connect: the
    page polls status once a second while it is open, and dialling a
    switched-off TV on every one of those would never stop.
    """
    return tv_request(req or {"cmd": "state"}) or {
        "ok": False, "connected": False, "volume": None, "muted": None,
    }


def downloads_summary():
    """Just enough for the indicator, so it rides along on the status poll
    instead of costing a request of its own every second."""
    with _dl_lock:
        active = next((d for d in _downloads if d["status"] == "active"), None)
        out = {
            "active": 1 if active else 0,
            "queued": sum(1 for d in _downloads if d["status"] == "queued"),
            "failed": sum(1 for d in _downloads if d["status"] == "failed"),
        }
        if active:
            pct = round(active["done"] / active["total"] * 100, 1) if active["total"] else 0
            out.update({
                "name": active["name"], "speed": active["speed"],
                "done": active["done"], "total": active["total"], "pct": pct,
            })
        # Everything still coming down, so the library tabs can show it next to
        # what is already on disk.  Only in-flight items: once a file lands, the
        # rescan puts the real thing in the grid and this would duplicate it.
        out["items"] = [
            {
                "id": d["id"], "name": d["name"], "root": d["root"],
                "series": d["series"], "status": d["status"],
                "done": d["done"], "total": d["total"],
            }
            for d in _downloads if d["status"] in ("queued", "active")
        ]
        return out


def status():
    """Current player state.  running=False whenever mpv is not reachable."""
    idle = {"running": False, "tv": tv_volume(), "dl": downloads_summary()}
    if not os.path.exists(SOCKET_PATH):
        return idle
    if get_prop("mpv-version") is None:
        return idle

    tracks = get_prop("track-list") or []

    def track_label(kind, current_id):
        for t in tracks:
            if t.get("type") == kind and t.get("id") == current_id:
                bits = [b for b in (t.get("lang") or "", t.get("title") or "") if b]
                return " / ".join(bits) if bits else f"track {current_id}"
        return "off" if current_id is None else str(current_id)

    return {
        "running": True,
        "pause": bool(get_prop("pause")),
        "position": get_prop("time-pos") or 0,
        "duration": get_prop("duration") or 0,
        "remaining": get_prop("time-remaining") or 0,
        "title": get_prop("media-title") or "",
        "volume": get_prop("volume"),
        # With TrueHD/DTS-HD bitstreaming mpv hands the stream over untouched,
        # so it cannot apply volume -- the soundbar owns it.  Tell the UI.
        # The format is reported per codec ("spdif-truehd", "spdif-dts-hd", ...),
        # so match the prefix rather than a bare "spdif".
        "passthrough": str(get_prop("audio-out-params/format") or "").startswith("spdif"),
        "sub": track_label("sub", get_prop("sid")),
        "sub_visible": bool(get_prop("sub-visibility")),
        "audio": track_label("audio", get_prop("aid")),
        # Position in the queue `play` was handed, so the player can say what
        # autoplay will do next.
        "playlist_pos": get_prop("playlist-pos"),
        "playlist_count": get_prop("playlist-count") or 0,
        "tv": tv_volume(),
        "dl": downloads_summary(),
    }


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------

_lib_lock = threading.Lock()
_library = {"scanning": False, "movies": [], "shows": []}
# Flat id -> {path, queue} for everything that can be played, so that /api/launch
# does not care whether it was handed a film or an episode.  `queue` is what
# autoplay continues with.
_playable = {}


def ffprobe(path, args):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", *args, path],
            capture_output=True, text=True, timeout=60,
        )
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def pretty_title(path, tag):
    """Prefer the container title; otherwise clean up the release filename."""
    if tag and tag.strip():
        return tag.strip()
    name = os.path.basename(os.path.dirname(path))
    if not name or name == os.path.basename(LIBRARY):
        name = os.path.splitext(os.path.basename(path))[0]
    # Release names look like "Title (2026) (2160p UHD BluRay x265 ... )";
    # keep everything up to the second bracketed group.
    parts = name.split(" (")
    if len(parts) >= 2:
        return (parts[0] + " (" + parts[1]).strip()
    return name


def make_thumb(path, dest, duration):
    """Grab a frame, tone-mapping HDR so the tile is not washed-out grey.

    Samples a few positions and keeps the brightest: films open on black, and a
    single fixed offset regularly lands on an all-dark frame.
    """
    vf = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
          "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
          "format=yuv420p,scale=480:-1")
    best, best_score = None, -1.0
    for frac in (0.25, 0.45, 0.65):
        tmp = dest + f".{int(frac * 100)}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-v", "error",
                 "-ss", str(max(1, duration * frac)), "-i", path,
                 "-vframes", "1", "-vf", vf, "-q:v", "4", "-y", tmp],
                capture_output=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if not os.path.exists(tmp):
            continue
        # File size is a decent proxy for detail/brightness in a JPEG and needs
        # no image library.
        score = os.path.getsize(tmp)
        if score > best_score:
            if best:
                os.unlink(best)
            best, best_score = tmp, score
        else:
            os.unlink(tmp)
    if best:
        shutil.move(best, dest)
        return True
    return False


def probe_movie(path):
    """Everything the tile needs, from the file itself."""
    fmt = ffprobe(path, ["-show_entries", "format=duration,size",
                         "-show_entries", "format_tags=title",
                         "-of", "default=nw=1"])
    vals = {}
    for line in fmt.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k] = v
    duration = float(vals.get("duration") or 0)
    size = int(vals.get("size") or 0)

    v = ffprobe(path, ["-select_streams", "v:0", "-show_entries",
                       "stream=height,color_transfer", "-of", "default=nw=1"])
    vv = {}
    for line in v.splitlines():
        if "=" in line:
            k, val = line.split("=", 1)
            vv[k] = val
    height = int(vv.get("height") or 0)
    trc = vv.get("color_transfer", "")

    hdr = "SDR"
    if trc == "smpte2084":
        hdr = "HDR10"
    elif trc == "arib-std-b67":
        hdr = "HLG"
    if "DOVI configuration record" in ffprobe(path, ["-select_streams", "v:0",
                                                     "-show_streams", "-of", "json"]):
        hdr = "Dolby Vision"

    a = ffprobe(path, ["-select_streams", "a:0", "-show_entries",
                       "stream=codec_name,channels", "-show_entries",
                       "stream_tags=title", "-of", "default=nw=1"])
    av = {}
    for line in a.splitlines():
        if "=" in line:
            k, val = line.split("=", 1)
            av[k] = val
    atitle = av.get("TAG:title") or ""
    codec = (av.get("codec_name") or "").upper().replace("_", " ")
    if "atmos" in atitle.lower():
        audio = f"{codec} Atmos"
    elif codec:
        ch = av.get("channels")
        audio = f"{codec} {ch}ch" if ch else codec
    else:
        audio = ""

    res = f"{height}p" if height else ""
    if height >= 2000:
        res = "4K"

    return {
        "title": pretty_title(path, vals.get("TAG:title")),
        # Kept raw as well: episode names are derived from what the container
        # actually claims, and pretty_title falls back to the folder name,
        # which for an episode is its season folder.
        "tag": (vals.get("TAG:title") or "").strip(),
        "path": path,
        "duration": duration,
        "size": size,
        "res": res,
        "hdr": hdr,
        "audio": audio,
    }


def find_movies():
    """One entry per movie: the largest video file in each folder, plus any
    loose video files sitting directly in the library root."""
    found = []
    if not os.path.isdir(LIBRARY):
        return found
    for entry in sorted(os.listdir(LIBRARY)):
        full = os.path.join(LIBRARY, entry)
        if os.path.isdir(full):
            best, best_size = None, -1
            for root, _dirs, files in os.walk(full):
                # Skip the extras folders these releases ship.
                if os.path.relpath(root, full).count(os.sep) > 1:
                    continue
                for f in files:
                    if f.lower().endswith(VIDEO_EXT):
                        p = os.path.join(root, f)
                        try:
                            s = os.path.getsize(p)
                        except OSError:
                            continue
                        if s > best_size:
                            best, best_size = p, s
            if best:
                found.append(best)
        elif entry.lower().endswith(VIDEO_EXT):
            found.append(full)
    return found


def season_number(filename, dirname):
    """Which season an episode belongs to.

    The season folder wins when it carries a number, because that is the
    structure on disk; SxxEyy in the release name is the fallback for episodes
    dropped straight into a series folder.
    """
    if dirname:
        # Season folders are not always "Season 01".  Real ones off the server
        # look like "Another.S01.1080p.BluRay.REMUX.AVC...", where the first
        # run of digits is part of the release name, not the season -- so look
        # for the season marker itself before falling back to any number.
        m = (re.search(r"season[\s._-]*(\d{1,3})", dirname, re.I)
             or re.search(r"\bS(\d{1,2})\b", dirname)
             or re.search(r"(\d+)", dirname))
        if m:
            return int(m.group(1))
    m = re.search(r"S(\d{1,2})[\s._-]*E\d{1,3}", filename, re.I)
    if m:
        return int(m.group(1))
    return 1


def episode_number(filename):
    """Episode number from the release name, 0 when it says nothing."""
    for pattern in (r"S\d{1,2}[\s._-]*E(\d{1,3})",
                    r"\b\d{1,2}x(\d{1,3})\b",
                    r"\bE(?:p(?:isode)?)?[\s._-]*(\d{1,3})\b"):
        m = re.search(pattern, filename, re.I)
        if m:
            return int(m.group(1))
    return 0


def episode_name(tag, series):
    """A real episode title if the file carries one, otherwise nothing.

    Release groups put the series name, the SxxEyy code and the resolution in
    the container title, all of which the tile already shows.  What survives
    stripping those is either a genuine episode name or noise too short to be
    worth a line.
    """
    if not tag:
        return ""
    t = re.sub(r"\bS\d{1,2}[\s._-]*E\d{1,3}\b", " ", tag, flags=re.I)
    t = re.sub(re.escape(series), " ", t, flags=re.I)
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = re.sub(r"[\s._-]+", " ", t).strip(" .-")
    return t if len(t) >= 3 else ""


def find_shows():
    """shows/<Series>/<Season N>/<episode>, as {series: {season: [paths]}}.

    Episodes sitting directly in a series folder are still placed in a season,
    taken from their own name, so a flat dump of files is not simply invisible.
    """
    out = []
    if not os.path.isdir(SHOWS):
        return out
    for series in sorted(os.listdir(SHOWS), key=str.lower):
        sdir = os.path.join(SHOWS, series)
        if not os.path.isdir(sdir):
            continue
        seasons, labels = {}, {}
        for root, _dirs, files in os.walk(sdir):
            rel = os.path.relpath(root, sdir)
            # Series/Season/episode is one level; anything deeper is extras.
            if rel != "." and rel.count(os.sep) >= 1:
                continue
            folder = None if rel == "." else os.path.basename(root)
            for f in files:
                if not f.lower().endswith(VIDEO_EXT):
                    continue
                num = season_number(f, folder)
                seasons.setdefault(num, []).append(os.path.join(root, f))
                # Keep what the folder is actually called: "Specials" reads
                # better than the "Season 1" it would be numbered as.
                if folder and num not in labels:
                    labels[num] = folder
        if seasons:
            out.append((series, seasons, labels))
    return out


def cache_entry(path, cached, fresh):
    """Probe one file, reusing cached metadata and its thumbnail.

    Cache key includes size and mtime so a replaced file is re-probed.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(f"{path}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()
    entry = cached.get(key)
    if not entry:
        entry = probe_movie(path)
    entry = dict(entry, id=key)
    thumb = os.path.join(THUMBS, key + ".jpg")
    if not os.path.exists(thumb):
        make_thumb(path, thumb, entry.get("duration") or 0)
    entry["thumb"] = os.path.exists(thumb)
    fresh[key] = entry
    # A copy, because callers decorate episodes with season/episode fields and
    # rewrite the title -- writing any of that back into the cache would make
    # the next scan derive names from the last scan's output.
    return dict(entry)


def scan_library():
    """Probe both libraries, publishing progress as it goes."""
    os.makedirs(THUMBS, exist_ok=True)
    meta_file = os.path.join(CACHE, "library.json")
    try:
        cached = json.load(open(meta_file))
    except (OSError, ValueError):
        cached = {}

    fresh, playable = {}, {}

    movies = []
    for path in find_movies():
        entry = cache_entry(path, cached, fresh)
        if not entry:
            continue
        movies.append(entry)
        playable[entry["id"]] = {"path": path, "queue": []}
        with _lib_lock:
            _library["movies"] = sorted(movies, key=lambda m: m["title"].lower())

    shows = []
    for series, seasons, labels in find_shows():
        out_seasons = []
        for num in sorted(seasons):
            paths = sorted(seasons[num], key=lambda p: (episode_number(os.path.basename(p)),
                                                        os.path.basename(p).lower()))
            episodes = []
            for path in paths:
                entry = cache_entry(path, cached, fresh)
                if not entry:
                    continue
                name = os.path.basename(path)
                entry["episode"] = episode_number(name)
                entry["season"] = num
                entry["series"] = series
                entry["name"] = episode_name(entry.get("tag"), series)
                entry["title"] = f"E{entry['episode']:02d}" if entry["episode"] else name
                episodes.append(entry)
            if not episodes:
                continue
            # Autoplay continues through the season it started in.  It stops at
            # the season boundary on purpose: `play` matches the display mode,
            # HDR and passthrough to the first file it is given, and a new
            # season is exactly where those are liable to change.
            for i, ep in enumerate(episodes):
                playable[ep["id"]] = {
                    "path": ep["path"],
                    "queue": [e["path"] for e in episodes[i + 1:]],
                }
            out_seasons.append({
                "season": num,
                "title": labels.get(num) or f"Season {num}",
                "episodes": episodes,
            })
        if not out_seasons:
            continue
        first = out_seasons[0]["episodes"][0]
        shows.append({
            "id": hashlib.sha1(series.encode()).hexdigest(),
            "title": series,
            "seasons": out_seasons,
            "count": sum(len(s["episodes"]) for s in out_seasons),
            # The first episode's frame stands in for the series: nothing in
            # these releases ships a poster.
            "thumb": first["id"] if first.get("thumb") else "",
        })
        with _lib_lock:
            _library["shows"] = list(shows)

    with _lib_lock:
        _playable.clear()
        _playable.update(playable)

    try:
        json.dump(fresh, open(meta_file, "w"))
    except OSError:
        pass
    # Drop thumbnails for files that are gone.
    for f in os.listdir(THUMBS):
        if f[:-4] not in fresh:
            try:
                os.unlink(os.path.join(THUMBS, f))
            except OSError:
                pass


def rescan(force=False):
    with _lib_lock:
        if _library["scanning"]:
            return
        _library["scanning"] = True
    if force:
        shutil.rmtree(CACHE, ignore_errors=True)

    def run():
        try:
            scan_library()
        finally:
            with _lib_lock:
                _library["scanning"] = False
    threading.Thread(target=run, daemon=True).start()


def desktop_env():
    """Environment `play` needs when started from a systemd user service.

    The service does not inherit HYPRLAND_INSTANCE_SIGNATURE or WAYLAND_DISPLAY,
    so hyprctl and mpv would both fail.  Both are discoverable from the runtime
    directory.
    """
    env = dict(os.environ)
    hypr = os.path.join(RUNTIME, "hypr")
    try:
        sigs = sorted(os.listdir(hypr),
                      key=lambda d: os.path.getmtime(os.path.join(hypr, d)))
        if sigs:
            env["HYPRLAND_INSTANCE_SIGNATURE"] = sigs[-1]
    except OSError:
        pass
    if "WAYLAND_DISPLAY" not in env:
        for cand in sorted(os.listdir(RUNTIME)):
            if cand.startswith("wayland-") and not cand.endswith(".lock"):
                env["WAYLAND_DISPLAY"] = cand
                break
    return env


def tv_action(action):
    """Fire `tv on|off` without blocking the request.

    `tv on` polls for up to ~25s while it re-sends wake packets, which is far
    too long to hold an HTTP response open on a phone.
    """
    if action not in ("on", "off"):
        return False
    try:
        subprocess.Popen(
            [TV_BIN, action],
            env=desktop_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def tv_status():
    """Ask the TV where it stands.  Reports off whenever it is unreachable,
    since a TV in standby refuses the WebSocket rather than answering it."""
    try:
        out = subprocess.run(
            [TV_BIN, "status"], env=desktop_env(),
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return {"power": "unknown", "input": ""}
    power, inp = "off", ""
    for line in out.stdout.splitlines():
        if line.startswith("power:"):
            power = line.split(":", 1)[1].strip()
        elif line.startswith("input:"):
            inp = line.split(":", 1)[1].strip().replace("com.webos.app.", "")
    return {"power": power, "input": inp}


def launch(item_id, autoplay=True):
    """Start a film or episode.

    Autoplay is handed to `play` as extra arguments rather than driven from
    here: mpv advances its own playlist at end of file, which keeps working
    whether or not this page is still open, and needs no end-of-file watcher.
    """
    with _lib_lock:
        item = _playable.get(item_id)
    if not item:
        return False
    paths = [item["path"]] + (item["queue"] if autoplay else [])
    try:
        subprocess.Popen(
            [PLAY_BIN, *paths],
            env=desktop_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# Remote sources
# --------------------------------------------------------------------------

_sources = []


def load_sources():
    """Read the configured sources and lay down a netrc for curl.

    Credentials sit in a mode-0600 file rather than in home.nix, because
    everything home-manager renders lands in /nix/store, which is world
    readable.  Writing them to a netrc instead of passing -u also keeps them
    out of curl's command line, where any `ps` would show them.
    """
    global _sources
    try:
        with open(SOURCES_FILE) as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        _sources = []
        return
    # One server is one source, with a catalogue per library: the Shows/Movies
    # tabs inside it pick which remote tree is listed *and* where a download
    # lands, so browsing films and filing them under shows cannot drift apart.
    _sources = []
    for s in loaded if isinstance(loaded, list) else []:
        catalogs = s.get("catalogs")
        if not isinstance(catalogs, dict) or not catalogs:
            # Older single-catalogue shape: one url plus the library it feeds.
            if not s.get("url"):
                continue
            catalogs = {s.get("dest") or "shows": s["url"]}
        catalogs = {k: (v if v.endswith("/") else v + "/")
                    for k, v in catalogs.items() if isinstance(v, str) and v}
        if catalogs:
            _sources.append(dict(s, catalogs=catalogs))

    lines, seen = [], set()
    for s in _sources:
        for url in s["catalogs"].values():
            host = urllib.parse.urlsplit(url).hostname
            if host and s.get("user") and host not in seen:
                seen.add(host)
                lines.append("machine %s login %s password %s\n"
                             % (host, s["user"], s.get("password", "")))
    try:
        fd = os.open(NETRC, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.writelines(lines)
    except OSError:
        pass


def source_by_id(sid):
    return next((s for s in _sources if s.get("id") == sid), None)


def remote_url(source, kind, relpath):
    """Absolute URL for a path inside one of a source's catalogues.

    None if the source has no such catalogue, or if the path escapes it: the
    path arrives from the browser, so it is normalised and then checked against
    the root rather than trusted -- "../.." must not become a request for
    someone else's directory.
    """
    root = source.get("catalogs", {}).get(kind)
    if not root:
        return None
    clean = posixpath.normpath("/" + (relpath or "")).lstrip("/")
    if clean in (".", ""):
        clean = ""
    url = urllib.parse.urljoin(root, urllib.parse.quote(clean))
    return url if url.startswith(root) else None


def curl(args, timeout=30):
    try:
        return subprocess.run(["curl", "-sS", "--fail", "--location",
                               "--netrc-file", NETRC, *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def remote_list(source, kind, relpath):
    """Directory listing, from Caddy's file browser -- which serves JSON when
    asked for it, so there is no HTML index to scrape."""
    url = remote_url(source, kind, relpath)
    if url is None:
        return None
    if not url.endswith("/"):
        url += "/"
    out = curl(["-H", "Accept: application/json", url])
    if out is None or out.returncode != 0:
        return None
    try:
        raw = json.loads(out.stdout)
    except ValueError:
        return None
    entries = []
    for e in raw if isinstance(raw, list) else []:
        name = (e.get("name") or "").rstrip("/")
        if not name or name.startswith("."):
            continue
        entries.append({
            "name": name,
            "dir": bool(e.get("is_dir")),
            "size": e.get("size") or 0,
            "video": name.lower().endswith(VIDEO_EXT),
        })
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return entries


def remote_files(source, kind, relpath, depth=0):
    """Every video file at or under a remote path.

    Capped at three levels, which covers series/season/episode; without a cap a
    symlink loop on the far end would walk forever.
    """
    entries = remote_list(source, kind, relpath)
    if entries is None:
        return []
    found = []
    for e in entries:
        child = posixpath.join(relpath, e["name"]) if relpath else e["name"]
        if e["dir"]:
            if depth < 3:
                found.extend(remote_files(source, kind, child, depth + 1))
        elif e["video"]:
            found.append((child, e["size"]))
    return found


# --------------------------------------------------------------------------
# Downloads
# --------------------------------------------------------------------------

_dl_lock = threading.Lock()
_downloads = []
_dl_procs = {}
_dl_queue = queue.Queue()
_dl_seq = 0


def public_downloads():
    with _dl_lock:
        return [{k: v for k, v in d.items() if k != "cancel"} for d in _downloads]


def remote_size(url):
    out = curl(["-I", "-o", "/dev/null", "-w", "%{size_download} %{header_json}", url])
    if out is None or out.returncode != 0:
        return 0
    try:
        headers = json.loads(out.stdout.split(" ", 1)[1])
    except (ValueError, IndexError):
        return 0
    value = headers.get("content-length") or headers.get("Content-Length") or []
    try:
        return int(value[-1] if isinstance(value, list) else value)
    except (TypeError, ValueError):
        return 0


def enqueue(source, kind, relpath, size=0):
    """Queue one remote file for download.  Returns its record.

    The catalogue it came from is also where it goes: films browsed under
    Movies land in the movie library, episodes under Shows in the show one.
    """
    global _dl_seq
    url = remote_url(source, kind, relpath)
    if url is None:
        return None
    root = LIBRARY if kind == "movies" else SHOWS
    parts = [p for p in relpath.split("/") if p and p not in (".", "..")]
    if not parts:
        return None
    dest = os.path.join(root, *parts)
    with _dl_lock:
        # Asking twice for the same file is a double tap, not a second copy.
        for d in _downloads:
            if d["dest"] == dest and d["status"] in ("queued", "active"):
                return d
        _dl_seq += 1
        item = {
            "id": _dl_seq, "name": parts[-1], "path": relpath, "dest": dest,
            "url": url, "total": size or 0, "done": 0, "speed": 0.0,
            "status": "queued", "error": "", "cancel": False,
            # Which library tab should show this while it is coming down, and
            # what to caption it with -- for an episode that is its series
            # folder, which is more use on a tile than the release filename.
            "root": "movies" if kind == "movies" else "shows",
            "series": parts[0] if len(parts) > 1 else "",
        }
        _downloads.append(item)
    _dl_queue.put(item)
    return item


def run_download(item):
    part = item["dest"] + ".part"
    try:
        os.makedirs(os.path.dirname(item["dest"]), exist_ok=True)
    except OSError as e:
        with _dl_lock:
            item["status"], item["error"] = "failed", str(e)
        return

    total = remote_size(item["url"])
    with _dl_lock:
        if item["cancel"]:
            item["status"] = "cancelled"
            return
        if total:
            item["total"] = total
        item["status"] = "active"

    # -C - resumes a half-finished .part instead of starting the 10 GB again.
    # The file only takes its real name once curl succeeds, so a partial
    # download is never picked up by the library scan.
    proc = subprocess.Popen(
        ["curl", "-sS", "--fail", "--location", "--netrc-file", NETRC,
         "-C", "-", "-o", part, item["url"]],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    _dl_procs[item["id"]] = proc

    mark_t, mark_b = time.monotonic(), 0
    while proc.poll() is None:
        time.sleep(0.5)
        try:
            done = os.path.getsize(part)
        except OSError:
            done = 0
        now = time.monotonic()
        with _dl_lock:
            item["done"] = done
            if now - mark_t >= 1.0:
                item["speed"] = max(0.0, (done - mark_b) / (now - mark_t))
                mark_t, mark_b = now, done
    err = (proc.stderr.read() if proc.stderr else "") or ""
    _dl_procs.pop(item["id"], None)

    with _dl_lock:
        cancelled = item["cancel"]
    if cancelled:
        with _dl_lock:
            item["status"], item["speed"] = "cancelled", 0.0
        return
    if proc.returncode != 0:
        with _dl_lock:
            item["status"] = "failed"
            item["speed"] = 0.0
            item["error"] = err.strip()[:200] or f"curl exited {proc.returncode}"
        return
    try:
        os.replace(part, item["dest"])
    except OSError as e:
        with _dl_lock:
            item["status"], item["error"] = "failed", str(e)
        return
    with _dl_lock:
        item["status"] = "done"
        item["done"] = item["total"] or item["done"]
        item["speed"] = 0.0
    # It is a real file now, so let it show up in the library it landed in.
    rescan()


def dl_worker():
    while True:
        item = _dl_queue.get()
        try:
            with _dl_lock:
                skip = item["cancel"]
            if skip:
                with _dl_lock:
                    item["status"] = "cancelled"
            else:
                run_download(item)
        except Exception as e:  # a worker that dies takes every later download with it
            with _dl_lock:
                item["status"], item["error"] = "failed", f"{type(e).__name__}: {e}"
        finally:
            _dl_queue.task_done()


# Enough of an mkv for ffprobe to find the track headers, so `play` still
# matches the refresh rate, HDR and passthrough to the file rather than
# silently falling back to defaults.
MIN_PLAYABLE = 32 * 1024 * 1024


def launch_download(dl_id):
    """Play a file that is still coming down.

    The download outruns playback by some margin, so there is usually no reason
    to wait for it.  mpv is handed the .part; when curl finishes, the rename to
    the real name does not disturb the open file descriptor, so playback carries
    on through it without noticing.
    """
    with _dl_lock:
        item = next((d for d in _downloads if d["id"] == dl_id), None)
    if not item:
        return False, "unknown download"
    part = item["dest"] + ".part"
    path = item["dest"] if os.path.exists(item["dest"]) else part
    if not os.path.exists(path):
        return False, "nothing on disk yet"
    if path is part and os.path.getsize(path) < MIN_PLAYABLE:
        return False, "too little downloaded to start"
    try:
        subprocess.Popen(
            [PLAY_BIN, path],
            env=desktop_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, ""
    except OSError as e:
        return False, str(e)


def cancel_download(dl_id):
    with _dl_lock:
        item = next((d for d in _downloads if d["id"] == dl_id), None)
        if not item or item["status"] not in ("queued", "active"):
            return False
        item["cancel"] = True
        # Something not started yet has no process to kill and would otherwise
        # sit there saying "queued" until the worker reached it -- which, behind
        # a 10 GB download, can be an hour.  Retire it now; the worker skips
        # anything already flagged when it eventually dequeues it.
        if item["status"] == "queued":
            item["status"] = "cancelled"
    proc = _dl_procs.get(dl_id)
    if proc:
        proc.terminate()
    return True


def cancel_many(queued_only):
    """Cancel everything in flight, or only what has not started yet."""
    with _dl_lock:
        targets = [
            d for d in _downloads
            if d["status"] == "queued" or (not queued_only and d["status"] == "active")
        ]
        for d in targets:
            d["cancel"] = True
            if d["status"] == "queued":
                d["status"] = "cancelled"
        ids = [d["id"] for d in targets]
    for dl_id in ids:
        proc = _dl_procs.get(dl_id)
        if proc:
            proc.terminate()
    return len(ids)


def clear_downloads():
    """Drop everything that is no longer moving."""
    with _dl_lock:
        _downloads[:] = [d for d in _downloads if d["status"] in ("queued", "active")]


# --------------------------------------------------------------------------
# Static assets
# --------------------------------------------------------------------------

def make_icon(size=180):
    """A dark tile with a play triangle, as a PNG.

    Generated rather than shipped because iOS apple-touch-icon must be a PNG
    (it ignores SVG), and this keeps the whole remote a single file.
    """
    bg = (30, 30, 46)
    fg = (137, 180, 250)
    rows = []
    cx, cy = size * 0.42, size * 0.5
    h = size * 0.32
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dy = abs(y - cy)
            inside = (x >= cx - h * 0.55) and (dy <= h) and \
                     ((x - (cx - h * 0.55)) / (h * 1.5) <= (1 - dy / h))
            row += bytes(fg if inside else bg)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


ICON = make_icon()
# iOS renders large Now Playing artwork as a grey box until the fullscreen
# player is opened; a small image is the reliable choice.
ICON_SMALL = make_icon(96)


def make_silence(seconds=5, rate=8000):
    """A silent mono WAV.

    iOS only surfaces lock screen / Control Center transport controls while the
    page has an *actually playing* audio element -- Media Session metadata on
    its own is ignored.  So the remote loops this silence to hold the media
    session open.  Kept 8-bit/8 kHz so it is a few KB.
    """
    data = b"\x80" * (rate * seconds)  # 0x80 is silence for unsigned 8-bit PCM
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8)
            + b"data" + struct.pack("<I", len(data)) + data)


SILENCE = make_silence()

MANIFEST = json.dumps({
    "name": "mpv Remote",
    "short_name": "mpv",
    "start_url": "/",
    "display": "standalone",
    "background_color": BASE,
    "theme_color": BASE,
    "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png"}],
})

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<!-- These make iOS "Add to Home Screen" open fullscreen with our own icon. -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="mpv">
<meta name="theme-color" content="__BASE__">
<link rel="apple-touch-icon" href="/icon.png">
<link rel="manifest" href="/manifest.json">
<title>mpv Remote</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; background: __BASE__; color: __TEXT__;
    font: 16px/1.4 -apple-system, system-ui, sans-serif;
    /* Bottom padding clears the floating dock, which is fixed and would
       otherwise sit on top of the last row of tiles. */
    padding: max(12px, env(safe-area-inset-top)) 12px
             calc(84px + env(safe-area-inset-bottom));
    min-height: 100vh; user-select: none; -webkit-user-select: none;
  }
  .dock { position:fixed; left:12px; right:12px; z-index:8; display:flex; gap:4px;
          bottom:calc(10px + env(safe-area-inset-bottom));
          background:__SURFACE__; border-radius:18px; padding:6px;
          box-shadow:0 10px 30px rgba(0,0,0,.5); }
  .dock button { flex:1; border:0; background:none; color:__SUBTEXT__; border-radius:13px;
                 font-family:inherit; font-size:11px; padding:8px 4px 7px;
                 display:flex; flex-direction:column; align-items:center; gap:3px; }
  .dock button.on { background:__BASE__; color:__BLUE__; font-weight:600; }
  .dock .ic { font-size:17px; line-height:1; }
  .hdr { display:flex; align-items:center; gap:10px; margin-bottom:12px; }
  .hdr h1 { font-size:17px; margin:0; flex:1; font-weight:600; }
  .ghost { background:__SURFACE__; border:0; color:__TEXT__; border-radius:10px;
           padding:9px 13px; font-size:14px; font-family:inherit; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; }
  .tile { background:__SURFACE__; border:0; border-radius:14px; overflow:hidden;
          padding:0; text-align:left; color:__TEXT__; font-family:inherit; }
  .tile:active { transform: scale(0.97); }
  .tile img, .tile .noimg { width:100%; aspect-ratio:16/9; object-fit:cover; display:block;
                            background:__MANTLE__; }
  .tile .noimg { display:flex; align-items:center; justify-content:center; color:__SUBTEXT__; }
  .tile .meta { padding:9px 10px 11px; }
  .tile .t { font-size:14px; font-weight:600; line-height:1.25;
             display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
  .badges { display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; }
  .b { font-size:10px; padding:2px 6px; border-radius:5px; background:__BASE__; color:__SUBTEXT__; }
  .b.hdr10, .b.dv { background:__YELLOW__; color:__MANTLE__; font-weight:600; }
  .b.atmos { background:__GREEN__; color:__MANTLE__; font-weight:600; }
  .tabs { display:flex; gap:8px; margin-bottom:12px; }
  .tabs button { flex:1; border:0; border-radius:12px; padding:11px; font-size:15px;
                 font-family:inherit; background:__SURFACE__; color:__SUBTEXT__; }
  .tabs button.on { background:__BLUE__; color:__MANTLE__; font-weight:600; }
  .chips { display:flex; gap:8px; overflow-x:auto; padding-bottom:10px; }
  .chip { border:0; border-radius:999px; padding:8px 14px; font-size:14px; white-space:nowrap;
          font-family:inherit; background:__SURFACE__; color:__SUBTEXT__; flex:none; }
  .chip.on { background:__LAVENDER__; color:__MANTLE__; font-weight:600; }
  .tile .sub { font-size:11px; color:__SUBTEXT__; margin-top:3px; white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; }
  .vol { display:flex; align-items:center; gap:10px; margin-top:12px; }
  .vol input[type=range] { flex:1; }
  .vol input[type=range]:disabled { opacity:.35; }
  .vol .vbtn { border:0; border-radius:12px; background:__SURFACE__; color:__TEXT__;
               font-size:15px; padding:10px 13px; font-family:inherit; }
  .vol .vbtn.on { background:__YELLOW__; color:__MANTLE__; }
  .vol .vval { min-width:2.6em; text-align:right; font-variant-numeric:tabular-nums;
               font-size:14px; color:__SUBTEXT__; }
  .next { font-size:12px; color:__SUBTEXT__; text-align:center; margin-top:10px; }
  .lbl { font-size:12px; color:__SUBTEXT__; margin:2px 0 6px; }
  .dltile .noimg { flex-direction:column; gap:4px; font-size:13px; font-weight:600;
                   color:__BLUE__; }
  .dltile .noimg small { font-weight:400; color:__SUBTEXT__; font-size:11px; }
  .rows { display:flex; flex-direction:column; gap:8px; }
  .row2 { display:flex; align-items:center; gap:10px; width:100%; border:0; text-align:left;
          background:__SURFACE__; border-radius:12px; padding:13px 14px;
          color:__TEXT__; font-family:inherit; font-size:14px; }
  .row2:active { background:__LAVENDER__; color:__MANTLE__; }
  .row2 .nm { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .row2 .sz { color:__SUBTEXT__; font-size:12px; font-variant-numeric:tabular-nums; flex:none; }
  .row2:active .sz { color:__MANTLE__; }
  .crumbs { font-size:13px; color:__SUBTEXT__; margin-bottom:10px; word-break:break-word; }
  .bar { height:6px; border-radius:3px; background:__BASE__; overflow:hidden; margin-top:8px; }
  .bar i { display:block; height:100%; background:__BLUE__; transition:width .3s; }
  .dl { background:__SURFACE__; border-radius:12px; padding:12px 14px; font-size:13px; }
  .dl .t2 { display:flex; justify-content:space-between; gap:10px; align-items:baseline; }
  .dl .t2 span:first-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .dl .t2 span:last-child { flex:none; color:__SUBTEXT__; font-variant-numeric:tabular-nums; }
  .dl.failed .bar i { background:__RED__; }
  .dl.done .bar i { background:__GREEN__; }
  .pull { position:fixed; top:0; left:0; right:0; z-index:10; display:flex;
          justify-content:center; pointer-events:none;
          transform:translateY(-44px); opacity:0; }
  .pull .ring { width:24px; height:24px; margin-top:12px; border-radius:50%;
                border:3px solid __SURFACE__; border-top-color:__BLUE__; }
  .pull.on .ring { animation:sp 0.9s linear infinite; }
  .wake { position:fixed; inset:0; display:none; align-items:center; justify-content:center;
          background:__BASE__; text-align:center; padding:30px; z-index:9; }
  .wake.on { display:flex; }
  .wake .box { max-width:280px; }
  .wake h2 { font-size:17px; margin:0 0 10px; font-weight:600; }
  .wake p { font-size:13px; color:__SUBTEXT__; margin:0; line-height:1.5; }
  .wake .spin { width:26px; height:26px; margin:0 auto 16px; border-radius:50%;
                border:3px solid __SURFACE__; border-top-color:__BLUE__;
                animation:sp 0.9s linear infinite; }
  @keyframes sp { to { transform: rotate(360deg); } }
  .tvbar { display:flex; align-items:center; gap:8px; margin-bottom:12px; }
  .tvbar .state { flex:1; font-size:13px; color:__SUBTEXT__; }
  .tvbar .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
                background:__SURFACE__; margin-right:6px; vertical-align:1px; }
  .tvbar .dot.on { background:__GREEN__; }
  .tvbar button { border:0; border-radius:10px; padding:9px 14px; font-size:14px;
                  font-family:inherit; background:__SURFACE__; color:__TEXT__; }
  .tvbar button:active { background:__LAVENDER__; color:__MANTLE__; }
  .empty, .offline { text-align:center; color:__SUBTEXT__; padding:40px 10px; }
  .title { font-size: 15px; color: __SUBTEXT__; text-align: center;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .times { display:flex; justify-content:space-between; font-variant-numeric:tabular-nums;
           font-size:13px; color:__SUBTEXT__; }
  input[type=range] { width:100%; accent-color:__BLUE__; height:34px; }
  .row { display:flex; gap:10px; margin-top:12px; }
  .row button { flex:1; border:0; border-radius:14px; background:__SURFACE__; color:__TEXT__;
                font-size:17px; padding:18px 8px; font-family:inherit; touch-action:manipulation; }
  .row button:active { background:__LAVENDER__; color:__MANTLE__; }
  .primary { background:__BLUE__ !important; color:__MANTLE__ !important; font-weight:600; font-size:20px !important; }
  .info { display:flex; justify-content:space-between; gap:10px; font-size:13px;
          color:__SUBTEXT__; padding:0 4px; margin-top:10px; }
  .info span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .toast { position:fixed; left:50%; transform:translateX(-50%); bottom:calc(20px + env(safe-area-inset-bottom));
           background:__SURFACE__; padding:12px 18px; border-radius:12px; font-size:14px; opacity:0;
           transition:opacity .2s; pointer-events:none; }
  .toast.on { opacity:1; }
</style>
</head>
<body>
<div id="app"><div class="offline">loading…</div></div>
<!-- Shown whenever the PC stops answering.  The poll behind it is also what
     wakes the machine: the NIC wakes on a unicast packet, and every retry is
     one, so this screen resolves itself once S3 has been left. -->
<div class="wake" id="wake"><div class="box">
  <div class="spin"></div>
  <h2>PC not responding</h2>
  <p>Waking it and reconnecting…<br>If it was asleep this takes a few seconds.</p>
</div></div>
<!-- Fixed, so the top-level sections are one thumb-reach away from any view
     rather than only from the library screen. -->
<div class="pull" id="pull"><div class="ring"></div></div>
<nav class="dock" id="dock">
  <button data-tab="movies"><span class="ic">🎬</span>Movies</button>
  <button data-tab="shows"><span class="ic">📺</span>Shows</button>
  <button data-tab="sources"><span class="ic">☁️</span>Sources</button>
  <button data-tab="tv"><span class="ic">📡</span>TV</button>
</nav>
<div class="toast" id="toast"></div>
<audio id="keepalive" src="/silent.wav" loop preload="auto" playsinline></audio>
<script>
const $ = id => document.getElementById(id);
let scrubbing = false, dur = 0, view = "library", built = "", lib = null;
/* library: the grid of films or series.  series: one show's seasons and
   episodes.  player: transport for whatever is on. */
let tab = localStorage.getItem("tab") || "movies";
let autoplay = localStorage.getItem("autoplay") !== "0";
let seriesId = null, seasonNum = null;
/* source browsing: which source, where in it, and where downloads land */
let srcId = null, srcKind = null, srcPath = "", srcData = null, sources = null;
let srcKinds = null, srcName = "";
/* The slider must not be yanked around by the 1s status poll while a finger is
   on it, and each drag update is still a round trip to the TV. */
let volDrag = false, volSentAt = 0;
/* Starts true so the very first poll always runs setOffline and settles it. */
let offline = null;
/* In-flight downloads, mirrored into the library tabs.  dlKey is the set of
   them, so the grid is only rebuilt when something is added or changes state. */
let dlItems = [], dlKey = "";
/* Whether mpv is up, so the "Now playing" shortcut can stay hidden until it
   actually leads somewhere. */
let playing = false;

const fmt = s => {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return (h ? h + ":" + String(m).padStart(2,"0") : String(m)) + ":" + String(x).padStart(2,"0");
};

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("on");
  setTimeout(() => t.classList.remove("on"), 2200);
}

/* ---- iOS lock screen / Control Center -------------------------------
   iOS shows transport controls only while this page is itself playing
   audio, so a silent loop is kept running to hold the media session open.
   It needs a user gesture to start, hence the first-tap hook below. */
const keepalive = $("keepalive");
let sessionReady = false, lastTitle = null;

function keepAudioAlive() { if (keepalive.paused) keepalive.play().catch(() => {}); }

function startSession() {
  if (sessionReady) return;
  keepalive.play().then(() => {
    sessionReady = true;
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    // Keep the silent loop running in every handler: iOS pauses it when the
    // user hits pause, which would tear down the Now Playing entry.
    ms.setActionHandler("play", () => { keepAudioAlive(); cmd("set_property","pause",false); });
    ms.setActionHandler("pause", () => { keepAudioAlive(); cmd("set_property","pause",true); });
    ms.setActionHandler("seekbackward", () => { keepAudioAlive(); cmd("seek",-10); });
    ms.setActionHandler("seekforward", () => { keepAudioAlive(); cmd("seek",30); });
    ms.setActionHandler("previoustrack", () => { keepAudioAlive(); cmd("seek",-30); });
    ms.setActionHandler("nexttrack", () => { keepAudioAlive(); cmd("seek",30); });
    try {
      ms.setActionHandler("seekto", d => { keepAudioAlive(); if (d.seekTime != null) cmd("seek", d.seekTime, "absolute"); });
    } catch (e) {}
  }).catch(() => {});
}

function updateSession(s) {
  if (!sessionReady || !("mediaSession" in navigator)) return;
  const ms = navigator.mediaSession;
  const title = s.title || "mpv";
  if (title !== lastTitle) {
    lastTitle = title;
    ms.metadata = new MediaMetadata({
      title: title, artist: s.audio || "", album: "mpv Remote",
      // 96x96: larger artwork shows as a grey box on the iOS lock screen.
      artwork: [{ src: "/icon96.png", sizes: "96x96", type: "image/png" }]
    });
  }
  ms.playbackState = s.pause ? "paused" : "playing";
  // setPositionState throws on inconsistent values, which would break the
  // whole status update -- guard rather than trust mpv's numbers.
  try {
    if (s.duration > 0 && s.position >= 0 && s.position <= s.duration)
      ms.setPositionState({ duration: s.duration, position: s.position, playbackRate: 1 });
  } catch (e) {}
  keepAudioAlive();
}

async function cmd(...c) {
  try { await fetch("/api/cmd", {method:"POST", body: JSON.stringify(c)}); } catch (e) {}
  setTimeout(refresh, 120);
}

/* ---- top-level navigation ----
   The dock is fixed and lives outside #app, so the three sections are reachable
   from any view.  Views that hang off a section (a series, a source browser,
   the download list) keep that section lit rather than clearing the dock. */
const SECTION_NAMES = {movies: "Movies", shows: "Shows", sources: "Sources", tv: "TV"};

/* "Now playing" is only a way *back* to something already running, so it stays
   out of the way until there is something to go back to. */
function syncNowPlaying() {
  document.querySelectorAll("[data-np]").forEach(b => {
    b.hidden = !playing;
    b.onclick = () => { view = "player"; built = ""; refresh(); };
  });
}

/* ---- pull to refresh ----
   What a pull means depends on where you are: rescan the library, re-list the
   remote folder you are looking at, re-ask the TV.  There is no refresh button
   anywhere, so this is the only path -- it has to cover every view. */
const PULL_TRIGGER = 70;
let pullStart = 0, pulling = false, pullDist = 0, pullBusy = false;

function setPull(d) {
  const p = $("pull");
  if (!p) return;
  if (!d) {
    p.style.transform = "translateY(-44px)";
    p.style.opacity = "0";
    return;
  }
  p.style.transform = `translateY(${Math.min(d, 110) - 44}px)`;
  p.style.opacity = String(Math.min(1, d / PULL_TRIGGER));
}

async function refreshCurrent() {
  if (pullBusy) return;
  pullBusy = true;
  const p = $("pull");
  if (p) { p.classList.add("on"); p.style.opacity = "1"; p.style.transform = "translateY(16px)"; }
  try {
    if (view === "library" && tab === "sources") {
      sources = null;
      await loadSources();
    } else if (view === "library") {
      await fetch("/api/rescan", {method:"POST", body: "{}"});
      await loadLibrary();
      setTimeout(loadLibrary, 1500);
    } else if (view === "source") {
      await openSource(srcId, srcKind, srcPath);
    } else if (view === "series") {
      await loadLibrary();
    } else if (view === "downloads") {
      await renderDownloads();
    } else if (view === "tv") {
      tvRefresh();
      await loadPicture();
    } else {
      await refresh();
    }
  } catch (e) {}
  setTimeout(() => {
    pullBusy = false;
    if (p) p.classList.remove("on");
    setPull(0);
  }, 600);
}

function dockSection() {
  if (view === "series") return "shows";
  if (view === "source" || view === "downloads") return "sources";
  if (view === "tv") return "tv";
  if (view === "library") return tab;
  return null;
}

function syncDock() {
  const on = dockSection();
  document.querySelectorAll("#dock button").forEach(b =>
    b.classList.toggle("on", b.dataset.tab === on));
}

function goTab(t) {
  built = "";
  if (t === "tv") { view = "tv"; renderTv(); return; }
  tab = t;
  localStorage.setItem("tab", t);
  view = "library";
  renderLibrary();
}

/* ---- library ---- */
const esc = s => String(s == null ? "" : s).replace(/</g, "&lt;");

async function loadLibrary() {
  try {
    const r = await fetch("/api/library", {cache:"no-store"});
    lib = await r.json();
  } catch (e) { lib = {scanning:false, movies:[], shows:[]}; }
  if (view === "library") renderLibrary();
  else if (view === "series") renderSeries();
}

/* One tile shape for films, series and episodes: a frame grab, a title, and
   whatever badges apply. */
function tile(id, thumbId, title, sub, badges) {
  return `
    <button class="tile" data-id="${id}">
      ${thumbId ? `<img src="/thumb/${thumbId}.jpg" loading="lazy" alt="">`
                : `<div class="noimg">no preview</div>`}
      <div class="meta">
        <div class="t">${esc(title)}</div>
        ${sub ? `<div class="sub">${esc(sub)}</div>` : ""}
        <div class="badges">${badges.join("")}</div>
      </div>
    </button>`;
}

function mediaBadges(m) {
  const b = [];
  if (m.res) b.push(`<span class="b">${esc(m.res)}</span>`);
  if (m.hdr && m.hdr !== "SDR")
    b.push(`<span class="b ${m.hdr === "Dolby Vision" ? "dv" : "hdr10"}">${m.hdr === "Dolby Vision" ? "DV" : esc(m.hdr)}</span>`);
  if (/atmos/i.test(m.audio || "")) b.push(`<span class="b atmos">Atmos</span>`);
  b.push(`<span class="b">${fmt(m.duration)}</span>`);
  return b;
}

function renderLibrary() {
  if (built !== "library") {
    $("app").innerHTML = `
      <div class="hdr"><h1 id="libtitle"></h1>
        <button class="ghost" data-np hidden>Now playing</button></div>
      <div id="grid" class="grid"></div>`;
    built = "library";
  }
  // Each section is titled after the dock entry that leads to it.
  $("libtitle").textContent = SECTION_NAMES[tab] || "Library";
  syncDock();
  syncNowPlaying();
  const g = $("grid");
  if (tab === "sources") {
    g.className = "rows";
    if (!sources) { g.innerHTML = `<div class="empty">loading…</div>`; loadSources(); return; }
    if (!sources.length) {
      g.innerHTML = `<div class="empty">no sources configured<br><br>~/.config/mpv-remote/sources.json</div>`;
      return;
    }
    g.innerHTML = sources.map(s =>
      `<button class="row2" data-id="${esc(s.id)}"><span class="nm">${esc(s.name)}</span>
        <span class="sz">${s.kinds.length} catalog${s.kinds.length > 1 ? "s" : ""} ›</span></button>`).join("") +
      `<div style="margin-top:14px"><button class="row2" id="dlview">
        <span class="nm">Downloads</span><span class="sz">›</span></button></div>`;
    g.querySelectorAll(".row2[data-id]").forEach(b => b.onclick = () => {
      const s = sources.find(x => x.id === b.dataset.id);
      srcKinds = s.kinds; srcName = s.name;
      openSource(s.id, s.kinds[0], "");
    });
    $("dlview").onclick = () => { view = "downloads"; built = ""; renderDownloads(); };
    return;
  }
  g.className = "grid";
  const items = !lib ? [] : (tab === "movies" ? lib.movies : lib.shows) || [];
  // Things still coming down show up alongside what is already here, so the
  // tab answers "what have I got" rather than "what finished downloading".
  const coming = dlItems.filter(d => d.root === tab);
  if (!items.length && !coming.length) {
    g.innerHTML = `<div class="empty">${lib && lib.scanning ? "scanning…" : "nothing here yet"}</div>`;
    return;
  }
  const dlHtml = coming.map(dlTile).join("");
  const wireDl = () => g.querySelectorAll(".dltile").forEach(b =>
    b.onclick = () => playPartial(+b.dataset.dl));
  if (tab === "movies") {
    g.innerHTML = dlHtml + items.map(m =>
      tile(m.id, m.thumb ? m.id : "", m.title, "", mediaBadges(m))).join("");
    g.querySelectorAll(".tile[data-id]").forEach(b => b.onclick = () => launch(b.dataset.id));
  } else {
    g.innerHTML = dlHtml + items.map(s => tile(s.id, s.thumb, s.title, "", [
      `<span class="b">${s.seasons.length} season${s.seasons.length > 1 ? "s" : ""}</span>`,
      `<span class="b">${s.count} episode${s.count > 1 ? "s" : ""}</span>`,
    ])).join("");
    g.querySelectorAll(".tile[data-id]").forEach(b => b.onclick = () => {
      seriesId = b.dataset.id; seasonNum = null;
      view = "series"; built = ""; renderSeries();
    });
  }
  wireDl();
}

/* A tile for something not fully here yet.  Tapping it plays what has arrived
   so far, which is usually enough: the download runs well ahead of playback. */
function dlTile(d) {
  const pct = d.total ? Math.min(100, d.done / d.total * 100) : 0;
  const label = d.status === "active" ? Math.round(pct) + "%" : "queued";
  return `
    <button class="tile dltile" data-dl="${d.id}">
      <div class="noimg">${label}<small>${d.status === "active" ? "downloading" : "waiting"}</small></div>
      <div class="meta">
        <div class="t">${esc(d.name)}</div>
        ${d.series ? `<div class="sub">${esc(d.series)}</div>` : ""}
        <div class="bar"><i style="width:${pct}%"></i></div>
      </div>
    </button>`;
}

/* Progress moves every second; rebuilding the whole grid that often would
   fight with scrolling, so only the changed bits are touched -- and the grid
   is rebuilt only when the set of downloads itself changes. */
function syncDlTiles(items) {
  dlItems = items || [];
  const ids = dlItems.map(d => d.id + ":" + d.status).join(",");
  if (ids !== dlKey) {
    dlKey = ids;
    if (view === "library" && tab !== "sources") renderLibrary();
    return;
  }
  dlItems.forEach(d => {
    const el = document.querySelector(`.dltile[data-dl="${d.id}"]`);
    if (!el) return;
    const pct = d.total ? Math.min(100, d.done / d.total * 100) : 0;
    const bar = el.querySelector(".bar i");
    if (bar) bar.style.width = pct + "%";
    const badge = el.querySelector(".noimg");
    if (badge && d.status === "active") badge.childNodes[0].nodeValue = Math.round(pct) + "%";
  });
}

async function playPartial(id) {
  toast("starting what has downloaded…");
  try {
    const r = await fetch("/api/launch", {method:"POST", body: JSON.stringify({dl: id})});
    const res = await r.json();
    if (!res.ok) { toast(res.error || "cannot play that yet"); return; }
    view = "player"; built = "";
    setTimeout(refresh, 800);
  } catch (e) { toast("cannot play that yet"); }
}

/* ---- one series: its seasons, then that season's episodes ---- */
function currentSeries() {
  return ((lib && lib.shows) || []).find(s => s.id === seriesId) || null;
}

function renderSeries() {
  syncDock();
  const s = currentSeries();
  if (!s) { view = "library"; built = ""; renderLibrary(); return; }
  if (seasonNum === null) seasonNum = s.seasons[0].season;
  const season = s.seasons.find(x => x.season === seasonNum) || s.seasons[0];

  if (built !== "series:" + seriesId) {
    $("app").innerHTML = `
      <div class="hdr"><button class="ghost" id="bk">‹ Shows</button>
        <h1 id="stitle"></h1>
        <button class="ghost" id="ap"></button></div>
      <div class="chips" id="chips"></div>
      <div id="grid" class="grid"></div>`;
    $("bk").onclick = () => { view = "library"; built = ""; renderLibrary(); };
    $("ap").onclick = () => {
      autoplay = !autoplay;
      localStorage.setItem("autoplay", autoplay ? "1" : "0");
      renderSeries();
      toast(autoplay ? "autoplay on" : "autoplay off");
    };
    built = "series:" + seriesId;
  }
  $("stitle").textContent = s.title;
  $("ap").textContent = autoplay ? "Autoplay ✓" : "Autoplay";

  $("chips").innerHTML = s.seasons.map(x =>
    `<button class="chip ${x.season === season.season ? "on" : ""}" data-s="${x.season}">${esc(x.title)}</button>`
  ).join("");
  $("chips").querySelectorAll(".chip").forEach(c => c.onclick = () => {
    seasonNum = +c.dataset.s; renderSeries();
  });

  const g = $("grid");
  g.innerHTML = season.episodes.map(e =>
    tile(e.id, e.thumb ? e.id : "", e.title, e.name, mediaBadges(e))).join("");
  g.querySelectorAll(".tile").forEach(b => b.onclick = () => launch(b.dataset.id));
}

/* ---- the TV section ----
   Everything that drives the television lives here and nowhere else, and it is
   all present whether or not something is playing: the TV is a device in its
   own right, not an attribute of the current film. */
const INPUTS = [
  ["HDMI_1", "PC"], ["HDMI_2", "HDMI 2"], ["HDMI_3", "HDMI 3"], ["HDMI_4", "HDMI 4"],
];
const PICTURE_MODES = [
  ["filmMaker", "Filmmaker"], ["cinema", "Cinema"],
  ["normal", "Standard"], ["vivid", "Vivid"],
];

function renderTv() {
  syncDock();
  if (built !== "tv") {
    $("app").innerHTML = `
      <div class="hdr"><h1>TV</h1>
        <button class="ghost" data-np hidden>Now playing</button></div>
      <div class="tvbar">
        <span class="state" id="tvstate"><span class="dot"></span>TV …</span>
        <button id="tvon">Turn on</button><button id="tvoff">Turn off</button></div>

      <div class="lbl">Volume</div>
      <div class="vol">
        <button class="vbtn" id="vmute">Mute</button>
        <input type="range" id="vol" min="0" max="100" step="1" value="0" disabled>
        <span class="vval" id="vval">--</span></div>

      <div class="lbl" style="margin-top:18px">Input</div>
      <div class="chips">${INPUTS.map(([id, name]) =>
        `<button class="chip" data-input="${id}">${name}</button>`).join("")}</div>

      <div class="lbl" style="margin-top:12px">Picture mode</div>
      <div class="chips">${PICTURE_MODES.map(([id, name]) =>
        `<button class="chip" data-pic="${id}">${name}</button>`).join("")}</div>
      <div class="next" id="picnow"></div>`;
    $("tvon").onclick = () => tvPower("on");
    $("tvoff").onclick = () => tvPower("off");
    document.querySelectorAll("[data-input]").forEach(b => b.onclick = async () => {
      toast("switching input…");
      try {
        const r = await (await fetch("/api/tv", {method:"POST",
          body: JSON.stringify({input: b.dataset.input})})).json();
        if (!r.ok) toast(r.error || "could not switch input");
      } catch (e) { toast("could not switch input"); }
      setTimeout(tvRefresh, 1500);
    });
    document.querySelectorAll("[data-pic]").forEach(b => b.onclick = async () => {
      try {
        const r = await (await fetch("/api/picture", {method:"POST",
          body: JSON.stringify({mode: b.dataset.pic})})).json();
        if (!r.ok) { toast(r.error ? "TV refused that mode" : "TV not reachable"); return; }
        showPicture(r.picture);
        toast("picture: " + b.textContent);
      } catch (e) { toast("could not set that"); }
    });
    wireVolume();
    built = "tv";
    tvRefresh();
    loadPicture();
  }
  syncNowPlaying();
}

function showPicture(mode) {
  const el = $("picnow");
  if (el) el.textContent = mode ? "currently: " + mode : "";
  document.querySelectorAll("[data-pic]").forEach(b =>
    b.classList.toggle("on", b.dataset.pic === mode));
}

async function loadPicture() {
  try {
    const r = await (await fetch("/api/picture", {method:"POST", body:"{}"})).json();
    showPicture(r.picture);
  } catch (e) {}
}

/* ---- TV power ----
   Status is fetched on demand, never polled: each query opens a WebSocket to
   the TV and takes ~1.7s, which is far too slow for the 1s status loop. */
async function tvRefresh() {
  const el = $("tvstate");
  if (!el) return;
  try {
    const t = await (await fetch("/api/tv", {cache:"no-store"})).json();
    const on = t.power === "Active";
    el.innerHTML = `<span class="dot ${on ? "on" : ""}"></span>TV ${on ? "on" : "off"}` +
                   (on && t.input ? ` · ${t.input}` : "");
  } catch (e) { el.innerHTML = `<span class="dot"></span>TV ?`; }
}

async function tvPower(action) {
  toast(action === "on" ? "waking the TV…" : "turning the TV off…");
  try { await fetch("/api/tv", {method:"POST", body: JSON.stringify({action})}); } catch (e) {}
  // `tv on` re-sends wake packets for up to ~25s, so check back after it has
  // had a chance rather than immediately.
  setTimeout(tvRefresh, action === "on" ? 12000 : 6000);
  setTimeout(tvRefresh, action === "on" ? 26000 : 12000);
}

/* ---- remote sources ----
   Browsing is a plain proxy of the far end's directory listing; nothing is
   mirrored locally until you ask for a file. */
const bytes = n => {
  if (!n) return "";
  const u = ["B","KB","MB","GB","TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (n < 10 && i ? n.toFixed(1) : Math.round(n)) + " " + u[i];
};

async function loadSources() {
  try { sources = await (await fetch("/api/sources", {cache:"no-store"})).json(); }
  catch (e) { sources = []; }
  if (view === "library" && tab === "sources") renderLibrary();
}

async function openSource(id, kind, path) {
  srcId = id; srcKind = kind || srcKind; srcPath = path || ""; srcData = null;
  view = "source"; built = "";
  renderSource();
  try {
    const q = `id=${encodeURIComponent(id)}&kind=${encodeURIComponent(srcKind || "")}` +
              `&path=${encodeURIComponent(srcPath)}`;
    srcData = await (await fetch("/api/source?" + q, {cache:"no-store"})).json();
    if (srcData.kind) srcKind = srcData.kind;
  } catch (e) { srcData = {error:"unreachable"}; }
  if (view === "source") renderSource();
}

function renderSource() {
  syncDock();
  const parts = srcPath ? srcPath.split("/").filter(Boolean) : [];
  const up = parts.slice(0, -1).join("/");
  const kinds = (srcData && srcData.kinds) || srcKinds || [];
  const label = k => k === "movies" ? "Movies" : "Shows";
  $("app").innerHTML = `
    <div class="hdr"><button class="ghost" id="bk">‹ ${parts.length ? "Back" : "Sources"}</button>
      <h1>${esc(parts.length ? parts[parts.length-1] : (srcName || "Browse"))}</h1></div>
    ${kinds.length > 1 ? `<div class="tabs">${kinds.map(k =>
      `<button data-kind="${k}">${label(k)}</button>`).join("")}</div>` : ""}
    <div class="crumbs">/${parts.map(esc).join(" / ")}</div>
    ${parts.length ? `<div class="rows" style="margin-bottom:10px">
      <button class="row2" id="dlall"><span class="nm">Download everything here</span>
        <span class="sz">↓</span></button></div>` : ""}
    <div class="rows" id="list"></div>`;
  $("bk").onclick = () => {
    if (parts.length) openSource(srcId, srcKind, up);
    else { view = "library"; built = ""; renderLibrary(); }
  };
  // Each catalogue is a different tree, so switching starts at its root rather
  // than trying to carry the current path across.
  document.querySelectorAll("[data-kind]").forEach(b => {
    b.classList.toggle("on", b.dataset.kind === srcKind);
    b.onclick = () => openSource(srcId, b.dataset.kind, "");
  });
  if ($("dlall")) $("dlall").onclick = () => download(srcPath, true);

  const l = $("list");
  if (!srcData) { l.innerHTML = `<div class="empty">loading…</div>`; return; }
  if (srcData.error || !srcData.entries) {
    l.innerHTML = `<div class="empty">could not read that folder</div>`;
    return;
  }
  if (!srcData.entries.length) { l.innerHTML = `<div class="empty">empty</div>`; return; }
  l.innerHTML = srcData.entries.map((e, i) => `
    <button class="row2" data-i="${i}">
      <span class="nm">${e.dir ? "📁 " : ""}${esc(e.name)}</span>
      <span class="sz">${e.dir ? "›" : bytes(e.size)}</span>
    </button>`).join("");
  l.querySelectorAll(".row2").forEach(b => b.onclick = () => {
    const e = srcData.entries[+b.dataset.i];
    const child = srcPath ? srcPath + "/" + e.name : e.name;
    if (e.dir) openSource(srcId, srcKind, child);
    else if (e.video) download(child, false, e.size);
    else toast("not a video file");
  });
}

async function download(path, isDir, size) {
  toast(isDir ? "queueing…" : "queued for download");
  try {
    const r = await fetch("/api/download", {method:"POST", body: JSON.stringify(
      {id: srcId, kind: srcKind, path, dir: !!isDir, size: size || 0})});
    const res = await r.json();
    if (!res.ok) toast("could not queue that");
    else if (isDir) toast(`queued ${res.queued} file${res.queued === 1 ? "" : "s"}`);
  } catch (e) { toast("could not queue that"); }
}

/* ---- downloads ---- */
async function renderDownloads() {
  syncDock();
  if (built !== "downloads") {
    $("app").innerHTML = `
      <div class="hdr"><button class="ghost" id="bk">‹ Library</button>
        <h1>Downloads</h1><button class="ghost" id="clr">Clear</button></div>
      <div class="row" style="margin-top:0">
        <button id="cq">Cancel queued</button><button id="ca">Cancel all</button></div>
      <div class="rows" id="dls" style="margin-top:12px"><div class="empty">loading…</div></div>`;
    $("bk").onclick = () => { view = "library"; built = ""; renderLibrary(); };
    $("clr").onclick = async () => {
      await fetch("/api/download/clear", {method:"POST"});
      renderDownloads();
    };
    // Queued-only leaves the one that is already moving alone, which is the
    // usual want after queueing a whole season by mistake.
    $("cq").onclick = () => bulkCancel({queued: true}, "queued downloads");
    $("ca").onclick = () => bulkCancel({all: true}, "all downloads");
    built = "downloads";
  }
  let list = [];
  try { list = await (await fetch("/api/downloads", {cache:"no-store"})).json(); } catch (e) {}
  const el = $("dls");
  if (!el) return;
  if (!list.length) { el.innerHTML = `<div class="empty">nothing downloaded yet</div>`; return; }
  el.innerHTML = list.slice().reverse().map(d => {
    const pct = d.total ? Math.min(100, d.done / d.total * 100) : (d.status === "done" ? 100 : 0);
    const right = d.status === "active"
      ? `${bytes(d.speed)}/s · ${Math.round(pct)}%`
      : d.status;
    return `<div class="dl ${d.status}">
      <div class="t2"><span>${esc(d.name)}</span><span>${esc(right)}</span></div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="t2" style="margin-top:6px">
        <span>${d.total ? bytes(d.done) + " / " + bytes(d.total) : ""}${d.error ? " · " + esc(d.error) : ""}</span>
        <span>${d.status === "active" ? `<button class="ghost" data-play="${d.id}">Play now</button> ` : ""}${
          d.status === "active" || d.status === "queued"
          ? `<button class="ghost" data-cancel="${d.id}">Cancel</button>` : ""}</span>
      </div></div>`;
  }).join("");
  el.querySelectorAll("[data-cancel]").forEach(b => b.onclick = async () => {
    await fetch("/api/download/cancel", {method:"POST", body: JSON.stringify({id: +b.dataset.cancel})});
    renderDownloads();
  });
  el.querySelectorAll("[data-play]").forEach(b =>
    b.onclick = () => playPartial(+b.dataset.play));
}

async function bulkCancel(body, what) {
  try {
    const r = await fetch("/api/download/cancel", {method:"POST", body: JSON.stringify(body)});
    const res = await r.json();
    toast(res.cancelled ? `cancelled ${res.cancelled}` : `no ${what} to cancel`);
  } catch (e) { toast("could not cancel"); }
  renderDownloads();
}

/* ---- TV volume ----
   The TV owns the volume because the interesting audio path bitstreams
   TrueHD/Atmos: mpv passes that through untouched and cannot attenuate it.
   Requests go to the `tv serve` bridge, which keeps one connection open. */
function wireVolume() {
  const sl = $("vol");
  if (!sl) return;
  sl.addEventListener("input", () => {
    volDrag = true;
    $("vval").textContent = sl.value;
    // Send while dragging so the volume tracks the finger, but not on every
    // pixel: each one is still a message to the TV.
    const now = Date.now();
    if (now - volSentAt > 120) { volSentAt = now; postVolume({volume: +sl.value}); }
  });
  // Always finish with the value actually let go of, which the throttle above
  // may have skipped.
  sl.addEventListener("change", () => postVolume({volume: +sl.value}));
  $("vmute").onclick = () => postVolume({mute: "toggle"});
  // Opening a view with a slider is the one moment worth dialling the TV for.
  fetch("/api/volume", {cache:"no-store"}).then(r => r.json()).then(applyVolume).catch(() => {});
}

async function postVolume(body) {
  try {
    const r = await fetch("/api/volume", {method:"POST", body: JSON.stringify(body)});
    const tv = await r.json();
    if (!tv.ok) toast("TV not reachable");
    applyVolume(tv);
  } catch (e) {}
  volDrag = false;
}

function applyVolume(tv) {
  const sl = $("vol");
  if (!sl) return;
  const live = !!(tv && tv.connected && tv.volume !== null && tv.volume !== undefined);
  sl.disabled = !live;
  if (live && !volDrag) {
    sl.value = tv.volume;
    $("vval").textContent = tv.volume;
  } else if (!live) {
    $("vval").textContent = "--";
  }
  $("vmute").classList.toggle("on", !!(tv && tv.muted));
}

async function launch(id) {
  toast("starting on the TV…");
  try {
    const r = await fetch("/api/launch", {method:"POST", body: JSON.stringify({id, autoplay})});
    const res = await r.json();
    if (!res.ok) toast("could not start that");
    else if (res.takeover) toast("switching…");
  } catch (e) {}
  view = "player"; built = "";
  setTimeout(refresh, 800);
}

/* ---- player ---- */
function renderPlayer(s) {
  if (!s.running) {
    if (built !== "idle") {
      $("app").innerHTML = `
        <div class="hdr"><h1>Now playing</h1>
          <button class="ghost" id="bk">Library</button></div>
        <div class="offline">Nothing is playing.<br><br>Pick something from the library.</div>`;
      $("bk").onclick = () => { view = "library"; built = ""; renderLibrary(); };
      built = "idle";
    }
    return;
  }
  if (built !== "player") {
    $("app").innerHTML = `
      <div class="hdr"><h1>Now playing</h1><button class="ghost" id="bk">Library</button></div>
      <div class="title" id="ttl"></div>
      <input type="range" id="seek" min="0" max="1000" value="0">
      <div class="times"><span id="cur">0:00</span><span id="rem">-0:00</span></div>
      <div class="row">
        <button id="b30">&#8722;30s</button><button id="b10">&#8722;10s</button>
        <button id="f10">+10s</button><button id="f30">+30s</button></div>
      <div class="row"><button class="primary" id="pp">Play</button></div>
      <div class="row"><button id="sub">Subtitles</button><button id="aud">Audio</button></div>
      <div class="info"><span id="isub"></span><span id="iaud"></span></div>
      <div class="next" id="next"></div>
      <div class="row"><button id="quit">Stop playback</button></div>`;
    $("bk").onclick  = () => { view = "library"; built = ""; renderLibrary(); };
    $("b30").onclick = () => cmd("seek", -30);
    $("b10").onclick = () => cmd("seek", -10);
    $("f10").onclick = () => cmd("seek", 10);
    $("f30").onclick = () => cmd("seek", 30);
    $("pp").onclick  = () => cmd("cycle", "pause");
    $("sub").onclick = () => cmd("cycle", "sub");
    $("aud").onclick = () => cmd("cycle", "audio");
    $("quit").onclick = () => { if (confirm("Stop playback?")) cmd("quit"); };
    const sk = $("seek");
    sk.addEventListener("input", () => scrubbing = true);
    sk.addEventListener("change", () => { cmd("seek", sk.value/1000*dur, "absolute"); scrubbing = false; });
    wireVolume();
    built = "player";
  }
  dur = s.duration || 0;
  $("ttl").textContent = s.title || "";
  $("pp").textContent = s.pause ? "Play" : "Pause";
  if (!scrubbing && dur > 0) $("seek").value = Math.round(s.position/dur*1000);
  $("cur").textContent = fmt(s.position);
  $("rem").textContent = "-" + fmt(s.remaining);
  $("isub").textContent = "sub: " + (s.sub_visible ? s.sub : "off");
  $("iaud").textContent = "audio: " + s.audio;
  const left = (s.playlist_count || 0) - ((s.playlist_pos == null ? 0 : s.playlist_pos) + 1);
  $("next").textContent = left > 0 ? `${left} more queued — plays automatically` : "";
}

/* The PC suspends, and the remote is served by the PC -- so "unreachable" is
   the normal state of a sleeping machine rather than an error worth hiding.
   The NIC wakes on a unicast packet, so simply continuing to poll is what
   brings it back; nothing else has to send anything. */
function setOffline(off) {
  if (off === offline) return;
  offline = off;
  $("wake").classList.toggle("on", off);
  if (!off) {
    // It may have been asleep for hours: rebuild from scratch rather than
    // trusting anything still on screen.
    built = "";
    lib = null;
    loadLibrary();
  }
}

async function refresh() {
  let s = null;
  try {
    const r = await fetch("/api/status", {cache:"no-store"});
    if (r.ok) s = await r.json();
  } catch (e) {}
  setOffline(!s);
  if (!s) return;
  if (!!s.running !== playing) { playing = !!s.running; syncNowPlaying(); }
  updateSession(s);
  if (view === "player") renderPlayer(s);
  // After renderPlayer, so the slider exists the first time round.  A no-op on
  // any view that has none.
  applyVolume(s.tv);
  syncDlTiles(s.dl && s.dl.items);
  // The pill only carries the one that is moving; the list view wants them all.
  if (view === "downloads") renderDownloads();
}

// iOS refuses to start audio without a user gesture, so the media session can
// only be armed on the first tap.  Not {once:true}: an early tap can be
// rejected, and startSession() is a no-op once it has succeeded.
document.querySelectorAll("#dock button").forEach(b =>
  b.onclick = () => goTab(b.dataset.tab));

/* Passive listeners: the gesture rides along with the page's own rubber-band
   rather than fighting it, which is what makes it feel native on iOS. */
document.addEventListener("touchstart", e => {
  if (window.scrollY > 0 || e.touches.length !== 1 || pullBusy) { pulling = false; return; }
  pullStart = e.touches[0].clientY;
  pulling = true;
  pullDist = 0;
}, {passive: true});

document.addEventListener("touchmove", e => {
  if (!pulling) return;
  pullDist = e.touches[0].clientY - pullStart;
  // Scrolling up mid-gesture means they meant to scroll, not to refresh.
  if (pullDist <= 0) { pulling = false; setPull(0); return; }
  setPull(pullDist);
}, {passive: true});

document.addEventListener("touchend", () => {
  if (!pulling) return;
  pulling = false;
  if (pullDist >= PULL_TRIGGER) refreshCurrent();
  else setPull(0);
}, {passive: true});

document.addEventListener("pointerdown", startSession);
document.addEventListener("visibilitychange", () => { if (!document.hidden && sessionReady) keepAudioAlive(); });

loadLibrary();
refresh();
setInterval(refresh, 1000);
setInterval(() => { if (view === "library" && lib && lib.scanning) loadLibrary(); }, 3000);
</script>
</body>
</html>
""".replace("__BASE__", BASE).replace("__MANTLE__", MANTLE).replace("__SURFACE__", SURFACE) \
   .replace("__TEXT__", TEXT).replace("__SUBTEXT__", SUBTEXT).replace("__BLUE__", BLUE) \
   .replace("__RED__", RED).replace("__YELLOW__", YELLOW).replace("__LAVENDER__", LAVENDER) \
   .replace("__GREEN__", GREEN)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, body, ctype, code=200, cache=False):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send(PAGE, "text/html; charset=utf-8")
        elif path == "/icon.png":
            self._send(ICON, "image/png", cache=True)
        elif path == "/icon96.png":
            self._send(ICON_SMALL, "image/png", cache=True)
        elif path == "/silent.wav":
            self._send(SILENCE, "audio/wav", cache=True)
        elif path == "/manifest.json":
            self._send(MANIFEST, "application/manifest+json")
        elif path == "/api/status":
            self._send(json.dumps(status()), "application/json")
        elif path == "/api/library":
            with _lib_lock:
                self._send(json.dumps(_library), "application/json")
        elif path == "/api/tv":
            self._send(json.dumps(tv_status()), "application/json")
        elif path == "/api/volume":
            # Explicit ask, so this one may dial the TV.
            self._send(json.dumps(tv_volume({"cmd": "state", "connect": True})),
                       "application/json")
        elif path == "/api/sources":
            self._send(json.dumps([
                {"id": s.get("id"), "name": s.get("name") or s.get("id"),
                 "kinds": list(s["catalogs"])} for s in _sources
            ]), "application/json")
        elif path == "/api/source":
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            source = source_by_id((q.get("id") or [""])[0])
            rel = (q.get("path") or [""])[0]
            if not source:
                self._send('{"error":"no such source"}', "application/json", 404)
                return
            kinds = list(source["catalogs"])
            kind = (q.get("kind") or [""])[0] or kinds[0]
            if kind not in source["catalogs"]:
                self._send('{"error":"no such catalog"}', "application/json", 404)
                return
            entries = remote_list(source, kind, rel)
            if entries is None:
                self._send('{"error":"listing failed"}', "application/json", 502)
                return
            self._send(json.dumps({
                "id": source.get("id"), "kind": kind, "kinds": kinds,
                "path": rel, "entries": entries,
            }), "application/json")
        elif path == "/api/downloads":
            self._send(json.dumps(public_downloads()), "application/json")
        elif path.startswith("/thumb/"):
            name = os.path.basename(path)
            # Basename only, so a crafted path cannot escape the cache dir.
            full = os.path.join(THUMBS, name)
            if name.endswith(".jpg") and os.path.exists(full):
                self._send(open(full, "rb").read(), "image/jpeg", cache=True)
            else:
                self._send("not found", "text/plain", 404)
        else:
            self._send("not found", "text/plain", 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        if self.path == "/api/cmd":
            try:
                command = json.loads(raw)
            except ValueError:
                self._send('{"error":"bad json"}', "application/json", 400)
                return
            reply = mpv_command(command)
            self._send(json.dumps(reply or {"error": "mpv unreachable"}), "application/json")
        elif self.path == "/api/launch":
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            item_id = body.get("id")
            # Whether this replaces a running player or starts one decides the
            # wording of the toast, and only `play` can tell for certain -- but
            # it has already detached by the time it knows, so ask here.
            running = bool(status().get("running"))
            if body.get("dl") is not None:
                ok, err = launch_download(body["dl"])
            elif item_id:
                ok, err = launch(item_id, autoplay=body.get("autoplay", True)), ""
            else:
                ok, err = False, "bad request"
            self._send(json.dumps({"ok": ok, "takeover": running, "error": err}),
                       "application/json")
        elif self.path == "/api/volume":
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            if "volume" in body:
                req = {"cmd": "set", "volume": body["volume"]}
            elif "delta" in body:
                req = {"cmd": "step", "delta": body["delta"]}
            elif "mute" in body:
                req = {"cmd": "mute", "value": body["mute"]}
            else:
                req = {"cmd": "state", "connect": True}
            self._send(json.dumps(tv_volume(req)), "application/json")
        elif self.path == "/api/tv":
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            if body.get("input"):
                # Through the bridge rather than `tv input`, so it lands on the
                # connection that is already open instead of dialling again.
                self._send(json.dumps(tv_volume(
                    {"cmd": "input", "value": body["input"]})), "application/json")
                return
            self._send(json.dumps({"ok": tv_action(body.get("action"))}),
                       "application/json")
        elif self.path == "/api/picture":
            try:
                mode = json.loads(raw).get("mode")
            except ValueError:
                mode = None
            self._send(json.dumps(tv_volume({"cmd": "picture", "mode": mode})),
                       "application/json")
        elif self.path == "/api/download":
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            source = source_by_id(body.get("id"))
            rel = body.get("path") or ""
            if not source or not rel:
                self._send('{"ok":false,"error":"bad request"}', "application/json", 400)
                return
            kind = body.get("kind") or list(source["catalogs"])[0]
            if kind not in source["catalogs"]:
                self._send('{"ok":false,"error":"no such catalog"}', "application/json", 400)
                return
            if body.get("dir"):
                # A whole season or series in one tap: enumerate it remotely,
                # then queue each file so progress stays per-file.
                targets = remote_files(source, kind, rel)
            else:
                targets = [(rel, body.get("size") or 0)]
            queued = [enqueue(source, kind, p, size) for p, size in targets]
            queued = [q for q in queued if q]
            self._send(json.dumps({"ok": bool(queued), "queued": len(queued)}),
                       "application/json")
        elif self.path == "/api/download/cancel":
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            if body.get("all") or body.get("queued"):
                n = cancel_many(queued_only=bool(body.get("queued")))
                self._send(json.dumps({"ok": True, "cancelled": n}), "application/json")
                return
            try:
                dl_id = int(body.get("id"))
            except (ValueError, TypeError):
                dl_id = None
            ok = cancel_download(dl_id) if dl_id is not None else False
            self._send(json.dumps({"ok": ok}), "application/json")
        elif self.path == "/api/download/clear":
            clear_downloads()
            self._send('{"ok":true}', "application/json")
        elif self.path == "/api/rescan":
            # Incremental by default.  A pull-to-refresh is a casual gesture and
            # happens often -- mostly to pick up a download that just landed --
            # whereas force drops every cached probe and thumbnail and leaves
            # the grid empty for as long as it takes to redo them.
            try:
                force = bool(json.loads(raw).get("force"))
            except ValueError:
                force = False
            rescan(force=force)
            self._send('{"ok":true}', "application/json")
        else:
            self._send("not found", "text/plain", 404)

    def log_message(self, *args):
        pass  # journal is noisy enough with one line per poll


if __name__ == "__main__":
    load_sources()
    # One worker, so downloads run one at a time.  These are 6-12 GB files over
    # one link; running them in parallel only makes every one of them slower.
    threading.Thread(target=dl_worker, daemon=True).start()
    rescan()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
