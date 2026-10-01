"""Remux library files down to English and German subtitle tracks, and make
English (Japanese, for anime) the default audio track.

Why this exists: releases routinely ship thirty-odd subtitle tracks. Before
ffmpeg can transcode anything it must identify every stream in the container,
and subtitle streams are sparse -- a subrip track emits a packet only when a
line of dialogue appears, so ffmpeg keeps reading until it has seen one from
each. On a spinning pool that is several seconds of probing before the first
video frame exists, paid again on every seek. Dropping the tracks nobody here
reads collapses the probe from identifying thirty-six streams to three.

Lossless: video and audio are stream-copied, never re-encoded, so Dolby Vision
and the lossless audio survive untouched. Only the container is rewritten.

English-default audio: dual-audio releases (ITA/ENG BDMux, SPA-ENG Apple TV+
rips) put the foreign dub first and flag it default, so every client that
honours the container -- Jellyfin web, phones, the TV's own player -- starts
in Italian. When the first audio track is not English but an English one
exists, the English track is moved to the front and made the only default.
The other audio tracks are kept, in their original order. Files with no
English audio, or whose language tags are missing, are left alone -- there is
nothing trustworthy to switch to.

Anime is the exception and goes the other way: Japanese first. "Anime" is
what Sonarr/Radarr say it is: Sonarr's anime series type, an Anime genre, or
an Animation genre on a title whose original language is Japanese -- and the
file must actually carry a Japanese track. Genre alone is not enough: the
SpongeBob Movie release ships a Japanese dub too. VibeReel's player follows
the container default this sets for animated titles (preferredAudioLangs in
src/lib/tracks.js). If the *arrs cannot be asked, audio
is left untouched for that run rather than guessed. Both fixes share one remux, so a file that
needs both is rewritten once.

Conservative about replacing anything. A file is only overwritten after the
remux is probed and shown to have the same video and audio stream counts, the
expected subtitle count, and a duration within a second of the original. Any
mismatch leaves the original in place and moves on.

Run by strip-subtitles.timer and the library watcher (see subtitles.nix).
Idempotent: a file with nothing to drop and English audio already first is
skipped without being rewritten,
which is also what stops the watcher from looping on its own output.
"""
import fcntl
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.environ.get("MEDIA_ROOT", "/tank/data/media")
# Held for the whole run. Rewriting a file trips the library watcher, which
# starts this service again; systemd will not run two copies of one unit, but
# a hand-run sweep alongside the unit is not covered by that. Two processes
# sharing a temp path corrupt each other's output -- caught by the verify
# step, but only after wasting a full remux. The lock lives under the media
# root because that is the one path the unit is allowed to write.
LOCK_PATH = os.path.join(ROOT, ".strip-subtitles.lock")
# ISO 639-2/B codes as ffprobe reports them, plus the variants muxers emit.
# "deu" and "ger" are the same language; releases use either.
KEEP = {x.strip().lower() for x in
        os.environ.get("KEEP_LANGS", "eng,en,ger,deu,de").split(",") if x.strip()}
# Audio languages that count as English / Japanese when choosing the default.
ENGLISH = {"eng", "en"}
JAPANESE = {"jpn", "ja"}
# Where to ask which titles are anime. Each app's config.xml arrives as a
# systemd credential (<app>.xml); an unset URL skips that app.
ARRS = [("sonarr", os.environ.get("SONARR_URL", ""), "series"),
        ("radarr", os.environ.get("RADARR_URL", ""), "movie")]
# Set to any non-empty value to report what would change without touching disk.
DRY_RUN = bool(os.environ.get("DRY_RUN", ""))
# Restrict to a single file, for trying this out before a full sweep.
ONLY = os.environ.get("ONLY", "")

EXTS = (".mkv",)


def log(msg):
    print(msg, flush=True)


def probe(path):
    """Return the stream list, or None if the file cannot be read."""
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format",
           "-of", "json", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             check=True).stdout
    except subprocess.CalledProcessError as e:
        log(f"  ! ffprobe failed: {e.stderr.strip().splitlines()[-1:]}")
        return None
    return json.loads(out)


def counts(info):
    """(video, audio, subtitle, duration) for comparing before against after."""
    streams = info.get("streams", [])
    by = {}
    for s in streams:
        by[s["codec_type"]] = by.get(s["codec_type"], 0) + 1
    return by.get("video", 0), by.get("audio", 0), by.get("subtitle", 0), media_duration(info)


def media_duration(info):
    """How long the picture and sound actually run, in seconds.

    The longest video/audio track, from the per-track DURATION tags Matroska
    muxers write. Not the container's own duration: some releases set that to
    the end of their chapter list, which can run past the last frame -- Mad Men
    S01E09 (Kitsune) claims 2854.25 s while every track ends at 2852.3 s, so a
    faithful remux, which reports the real length, looked 2 s short. Falls back
    to the container duration when no track carries the tag.
    """
    ends = []
    for s in info.get("streams", []):
        if s["codec_type"] not in ("video", "audio"):
            continue
        tag = s.get("tags", {}).get("DURATION", "")
        try:
            h, m, sec = tag.split(":")
            ends.append(int(h) * 3600 + int(m) * 60 + float(sec))
        except ValueError:
            pass
    if ends:
        return max(ends)
    try:
        return float(info.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        return 0.0


def language(stream):
    return (stream.get("tags", {}).get("language") or "").lower()


def plan(info):
    """Absolute indices of subtitle streams worth keeping, and how many go."""
    subs = [s for s in info["streams"] if s["codec_type"] == "subtitle"]
    keep = [s["index"] for s in subs if language(s) in KEEP]
    return keep, len(subs) - len(keep)


def is_default(stream):
    return bool(stream.get("disposition", {}).get("default"))


def is_commentary(stream):
    title = (stream.get("tags", {}).get("title") or "").lower()
    return bool(stream.get("disposition", {}).get("comment")) or "commentary" in title


def anime_folders():
    """Library folders of anime titles, or None if an *arr could not be asked."""
    folders = []
    creds = os.environ.get("CREDENTIALS_DIRECTORY", "")
    for app, url, endpoint in ARRS:
        if not url:
            continue
        try:
            with open(os.path.join(creds, f"{app}.xml")) as f:
                key = re.search(r"<ApiKey>([^<]+)</ApiKey>", f.read()).group(1)
            req = urllib.request.Request(f"{url}/api/v3/{endpoint}",
                                         headers={"X-Api-Key": key})
            with urllib.request.urlopen(req, timeout=30) as r:
                titles = json.load(r)
        except Exception as e:
            log(f"! cannot ask {app} which titles are anime ({e}); "
                "leaving audio untouched this run")
            return None
        for t in titles:
            genres = {g.lower() for g in t.get("genres", [])}
            original = (t.get("originalLanguage") or {}).get("name", "")
            japanese_animation = "animation" in genres and original == "Japanese"
            if t.get("seriesType") == "anime" or "anime" in genres or japanese_animation:
                folders.append(t["path"].rstrip("/") + "/")
    return folders


def preferred_langs(path, info, anime):
    """Japanese for anime that has a Japanese track, English for the rest."""
    path = os.path.abspath(path)
    if any(path.startswith(f) for f in anime):
        if any(s["codec_type"] == "audio" and language(s) in JAPANESE
               for s in info["streams"]):
            return JAPANESE
    return ENGLISH


def audio_plan(info, langs=ENGLISH):
    """Absolute audio indices in their new order, or None if nothing to fix.

    Fine as-is when the first audio track is in `langs` and either carries the
    default flag or no track does. Matroska's FlagDefault defaults to 1, so
    many files flag *every* track default; players then take the first, which
    is why "first is preferred" is the test rather than "only it is flagged".
    """
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if not audio:
        return None
    first = audio[0]
    flagged = [s for s in audio if is_default(s)]
    if language(first) in langs and (is_default(first) or not flagged):
        return None
    wanted = [s for s in audio
              if language(s) in langs and not is_commentary(s)]
    if not wanted:
        return None
    # A preferred-language track the release already marked default is its own pick.
    chosen = next((s for s in wanted if is_default(s)), wanted[0])
    return [chosen["index"]] + [s["index"] for s in audio if s is not chosen]


def remux(path, keep_indices, audio_order=None, langs=ENGLISH):
    """Rewrite path with only keep_indices as subtitles and, if audio_order is
    given, the audio tracks in that order with the first as sole default.
    True if replaced."""
    # PID in the name so that even without the lock two runs cannot collide.
    tmp = os.path.join(os.path.dirname(path),
                       f".{os.path.basename(path)}.{os.getpid()}.strip-tmp")
    # -map 0 then -map -0:s takes everything and removes every subtitle;
    # re-adding by absolute index puts back only the wanted ones. Going via
    # the negative map keeps chapters, attachments and data streams that an
    # explicit -map 0:v -map 0:a would silently drop.
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", path,
           "-map", "0", "-map", "-0:s"]
    # Same trick for audio: drop it all, then put it back in the new order.
    if audio_order:
        cmd += ["-map", "-0:a"]
        for i in audio_order:
            cmd += ["-map", f"0:{i}"]
    for i in keep_indices:
        cmd += ["-map", f"0:{i}"]
    if audio_order:
        # +/- edit a single flag and leave the rest (original, comment,
        # visual_impaired...) as the source had them.
        cmd += ["-disposition:a:0", "+default"]
        for n in range(1, len(audio_order)):
            cmd += [f"-disposition:a:{n}", "-default"]
    # The temp name has no .mkv suffix, so the library watcher ignores it
    # while it is being written; that means the format must be named here.
    cmd += ["-c", "copy", "-f", "matroska", tmp]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        log(f"  ! ffmpeg failed: {e.stderr.strip().splitlines()[-1:]}")
        _unlink(tmp)
        return False

    before = probe(path)
    after = probe(tmp)
    if after is None or before is None:
        _unlink(tmp)
        return False

    bv, ba, _, bd = counts(before)
    av, aa, asub, ad = counts(after)
    if (av, aa, asub) != (bv, ba, len(keep_indices)) or abs(ad - bd) > 1.0:
        log(f"  ! verify failed: v{bv}->{av} a{ba}->{aa} "
            f"s->{asub} (want {len(keep_indices)}) dur {bd:.1f}->{ad:.1f}")
        _unlink(tmp)
        return False

    if audio_order and audio_plan(after, langs) is not None:
        log("  ! verify failed: preferred audio is still not the first default")
        _unlink(tmp)
        return False

    # Match the original's mode and owner so Jellyfin keeps read access
    # regardless of whether this ran as root or as the jellyfin user.
    st = os.stat(path)
    shutil.copymode(path, tmp)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except PermissionError:
        pass
    # Same directory, same filesystem, so this is atomic: readers hold the
    # old inode until they close, and nothing ever sees a half-written file.
    os.replace(tmp, path)
    return True


def _unlink(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def acquire_lock():
    """Exclusive, non-blocking. None if another run already holds it."""
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def sweep_stale_temps():
    """Remove temp files a killed run left behind. Safe under the lock."""
    stale = glob.glob(os.path.join(ROOT, "**", ".*.strip-tmp"), recursive=True)
    for p in stale:
        _unlink(p)
    if stale:
        log(f"cleaned {len(stale)} temp file(s) from an interrupted run")


def main():
    lock = acquire_lock()
    if lock is None:
        log("another strip run holds the lock; nothing to do")
        return 0
    sweep_stale_temps()

    if ONLY:
        files = [ONLY]
    else:
        files = []
        for dirpath, _, names in os.walk(ROOT):
            for n in sorted(names):
                if n.lower().endswith(EXTS) and not n.startswith("."):
                    files.append(os.path.join(dirpath, n))
        files.sort()

    anime = anime_folders()
    changed = dropped_total = skipped = failed = audio_fixed = 0
    for path in files:
        info = probe(path)
        if info is None:
            failed += 1
            continue
        keep, dropping = plan(info)
        langs = audio_order = None
        if anime is not None:
            langs = preferred_langs(path, info, anime)
            audio_order = audio_plan(info, langs)
        name = os.path.basename(path)
        if dropping == 0 and audio_order is None:
            skipped += 1
            continue
        what = f"keeping {len(keep)}, dropping {dropping}"
        if audio_order is not None:
            what += (", Japanese audio first" if langs is JAPANESE
                     else ", English audio first")
        log(f"- {name[:70]}: {what}")
        if DRY_RUN or remux(path, keep, audio_order, langs):
            changed += 1
            dropped_total += dropping
            audio_fixed += audio_order is not None
        else:
            failed += 1

    suffix = " (dry run)" if DRY_RUN else ""
    log(f"done: {changed} rewritten, {dropped_total} tracks dropped, "
        f"{audio_fixed} audio defaults fixed, "
        f"{skipped} already clean, {failed} failed{suffix}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
