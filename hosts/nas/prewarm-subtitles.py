"""Have Jellyfin extract new files' embedded subtitles before anyone presses Play.

Why this exists: Jellyfin cannot serve one embedded subtitle track on its own.
The first request for any of a file's subtitle tracks makes it extract them in
one ffmpeg pass over the whole file, and the player gets nothing until that
pass ends. On this pool -- one spinning disk -- that was nine minutes for a
28 GB 4K remux (Spider-Man: No Way Home, 2026-10-10), and the pass starved the
video stream reading from the same disk, so the film buffered as well.
Jellyfin keeps what it extracted, so the wait is paid once per file -- but
every new download pays it, and so does every file strip-subtitles rewrites
(a changed file loses its extracted tracks).

So this pays it ahead of time. For every library file that changed recently
it asks Jellyfin for each embedded text or PGS track, exactly as a player
would, and reads the answer to the end. A track already extracted comes back
at once; the first one that is not starts the pass.

It never starts a request while anything is playing, because the pass is the
very disk load that made playback stall. A pass already running when someone
presses Play cannot be paused (it is Jellyfin's ffmpeg), so files go one at a
time, newest first, and every one is preceded by that check.

Run by prewarm-subtitles.timer and after every successful strip-subtitles run
(see subtitles.nix). A file is remembered by size and mtime in
$STATE_DIRECTORY, so an unchanged file is never asked about twice -- not even
one whose extraction failed, which would otherwise re-read the whole file on
every run.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

JELLYFIN = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096")
TOKEN = os.environ.get("JELLYFIN_TOKEN", "")
# Files changed this recently are warmed. Older ones still extract on first
# play, as before. ALL=1 sweeps the whole library instead: a full read of
# every file, about nine hours for this pool, so only ever overnight.
WINDOW_DAYS = float(os.environ.get("WINDOW_DAYS", "3"))
ALL = bool(os.environ.get("ALL", ""))
STATE = os.path.join(os.environ.get("STATE_DIRECTORY", "."), "warmed.json")
# Set to any non-empty value to list what would be warmed without asking.
DRY_RUN = bool(os.environ.get("DRY_RUN", ""))
# Re-checked before every request while something plays.
POLL_SECONDS = 60
# The request blocks until the whole pass is done: a 60 GB remux at this
# pool's ~55 MB/s is about twenty minutes.
PASS_TIMEOUT = 3600
# Codec as Jellyfin reports it -> the format to ask for. The track's own
# format, so Jellyfin hands back the extracted file without converting it.
# VobSub and DVB are absent on purpose: clients get those burned in.
FORMATS = {"subrip": "srt", "srt": "srt", "text": "srt", "mov_text": "srt",
           "ass": "ass", "ssa": "ssa", "webvtt": "vtt",
           "pgssub": "pgssub", "pgs": "pgssub"}


def log(msg):
    print(msg, flush=True)


def get(path, params=None, timeout=60):
    url = f"{JELLYFIN}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    r = urllib.request.Request(url)
    r.add_header("Authorization",
                 f'MediaBrowser Token="{TOKEN}", Client="prewarm-subtitles", '
                 f'Device="nas", DeviceId="prewarm-subtitles", Version="1.0"')
    return urllib.request.urlopen(r, timeout=timeout)


def get_json(path, params=None):
    with get(path, params) as r:
        return json.load(r)


def playing():
    """What is playing right now, on any session, as "user: title"."""
    # Sessions idle for 16 minutes are ghosts (a TV switched off mid-film).
    sessions = get_json("/Sessions", {"activeWithinSeconds": 960})
    return [f"{s.get('UserName', '?')}: {s['NowPlayingItem'].get('Name', '?')}"
            for s in sessions if s.get("NowPlayingItem")]


def wait_until_idle():
    waited = False
    while now := playing():
        if not waited:
            log(f"  waiting for playback to stop ({', '.join(now)})")
            waited = True
        time.sleep(POLL_SECONDS)
    if waited:
        log("  nothing playing any more, going on")


def fingerprint(path):
    """size:mtime of the file, or None if it is not there."""
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return None
    return f"{st.st_size}:{int(st.st_mtime)}"


def changed_items(state):
    """(mtime, item id, path) of every recently changed file not yet warmed, newest first."""
    items = get_json("/Items", {"Recursive": "true", "IncludeItemTypes": "Movie,Episode",
                                "IsMissing": "false", "Fields": "Path"})["Items"]
    cutoff = time.time() - WINDOW_DAYS * 86400
    out = []
    for it in items:
        path = it.get("Path")
        fp = fingerprint(path)
        if fp is None:
            continue
        mtime = int(fp.split(":")[1])
        # A single-version item's one media source shares its id.
        if (ALL or mtime >= cutoff) and state.get(it["Id"], "").split("|")[0] != fp:
            out.append((mtime, it["Id"], path))
    out.sort(reverse=True)
    return out


def extractable(source):
    def embedded(s):
        return s.get("Type") == "Subtitle" and not s.get("IsExternal")
    return [s for s in source.get("MediaStreams") or []
            if embedded(s) and (s.get("Codec") or "").lower() in FORMATS]


def fetch_track(item_id, source_id, stream):
    """Ask for one track and read it to the end. Returns its size in bytes."""
    fmt = FORMATS[stream["Codec"].lower()]
    size = 0
    with get(f"/Videos/{item_id}/{source_id}/Subtitles/{stream['Index']}/0/Stream.{fmt}",
             timeout=PASS_TIMEOUT) as r:
        while chunk := r.read(1 << 20):
            size += len(chunk)
    return size


def warm(item_id, source, state):
    """Warm one media source. False only if Jellyfin could not be reached."""
    name = os.path.basename(source.get("Path") or item_id)[:60]
    fp = fingerprint(source.get("Path"))
    if fp is None:
        return True
    if state.get(source["Id"], "").split("|")[0] == fp:
        return True
    # Jellyfin re-probes a changed file a minute or so after it lands. Until
    # then its track list describes the old file (strip-subtitles renumbers
    # tracks), so leave the file for the next run.
    if source.get("Size") not in (None, int(fp.split(":")[0])):
        log(f"- {name}: Jellyfin has not rescanned it yet, next run")
        return True
    tracks = extractable(source)
    if not tracks:
        state[source["Id"]] = fp
        return True
    if DRY_RUN:
        log(f"- {name}: would warm {len(tracks)} track(s)")
        return True
    log(f"- {name}: {len(tracks)} track(s)")
    start = time.monotonic()
    for s in tracks:
        try:
            wait_until_idle()
            fetch_track(item_id, source["Id"], s)
        except urllib.error.HTTPError as e:
            if e.code != 500:
                # Not an extraction error (a stale track list, Jellyfin starting
                # up): nothing was read, so the next run simply tries again.
                log(f"  ! track {s['Index']} ({s['Codec']}): HTTP {e.code}, next run")
                return True
            # The pass ran and failed. Remembered for this version of the
            # file: retrying would re-read all of it on every run.
            log(f"  ! track {s['Index']} ({s['Codec']}): extraction failed, not retried until the file changes")
            state[source["Id"]] = fp + "|failed"
            return True
        except OSError as e:
            log(f"  ! Jellyfin unreachable: {e}")
            return False
    log(f"  done in {time.monotonic() - start:.0f} s")
    state[source["Id"]] = fp
    return True


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=0, sort_keys=True)
    os.replace(tmp, STATE)


def main():
    if not TOKEN:
        log("JELLYFIN_TOKEN is not set; it is required here")
        return 1
    state = load_state()
    try:
        todo = changed_items(state)
    except OSError as e:
        log(f"Jellyfin unreachable: {e}")
        return 1
    window = "whole library" if ALL else f"last {WINDOW_DAYS:g} days"
    log(f"{len(todo)} changed file(s) to check ({window})")
    for _, item_id, _ in todo:
        try:
            sources = get_json("/Items", {"Ids": item_id, "Fields": "MediaSources"})["Items"]
        except OSError as e:
            log(f"Jellyfin unreachable: {e}")
            return 1
        for source in (sources[0].get("MediaSources") or []) if sources else []:
            if not warm(item_id, source, state):
                save_state(state)
                return 1
        if not DRY_RUN:
            save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
