"""Remux library files down to English and German subtitle tracks.

Why this exists: releases routinely ship thirty-odd subtitle tracks. Before
ffmpeg can transcode anything it must identify every stream in the container,
and subtitle streams are sparse -- a subrip track emits a packet only when a
line of dialogue appears, so ffmpeg keeps reading until it has seen one from
each. On a spinning pool that is several seconds of probing before the first
video frame exists, paid again on every seek. Dropping the tracks nobody here
reads collapses the probe from identifying thirty-six streams to three.

Lossless: video and audio are stream-copied, never re-encoded, so Dolby Vision
and the lossless audio survive untouched. Only the container is rewritten.

Conservative about replacing anything. A file is only overwritten after the
remux is probed and shown to have the same video and audio stream counts, the
expected subtitle count, and a duration within a second of the original. Any
mismatch leaves the original in place and moves on.

Run by strip-subtitles.timer and the library watcher (see subtitles.nix).
Idempotent: a file with nothing to drop is skipped without being rewritten,
which is also what stops the watcher from looping on its own output.
"""
import fcntl
import glob
import json
import os
import shutil
import subprocess
import sys

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
    try:
        dur = float(info.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        dur = 0.0
    return by.get("video", 0), by.get("audio", 0), by.get("subtitle", 0), dur


def language(stream):
    return (stream.get("tags", {}).get("language") or "").lower()


def plan(info):
    """Absolute indices of subtitle streams worth keeping, and how many go."""
    subs = [s for s in info["streams"] if s["codec_type"] == "subtitle"]
    keep = [s["index"] for s in subs if language(s) in KEEP]
    return keep, len(subs) - len(keep)


def remux(path, keep_indices):
    """Rewrite path with only keep_indices as subtitles. True if replaced."""
    # PID in the name so that even without the lock two runs cannot collide.
    tmp = os.path.join(os.path.dirname(path),
                       f".{os.path.basename(path)}.{os.getpid()}.strip-tmp")
    # -map 0 then -map -0:s takes everything and removes every subtitle;
    # re-adding by absolute index puts back only the wanted ones. Going via
    # the negative map keeps chapters, attachments and data streams that an
    # explicit -map 0:v -map 0:a would silently drop.
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", path,
           "-map", "0", "-map", "-0:s"]
    for i in keep_indices:
        cmd += ["-map", f"0:{i}"]
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

    changed = dropped_total = skipped = failed = 0
    for path in files:
        info = probe(path)
        if info is None:
            failed += 1
            continue
        keep, dropping = plan(info)
        name = os.path.basename(path)
        if dropping == 0:
            skipped += 1
            continue
        log(f"- {name[:70]}: keeping {len(keep)}, dropping {dropping}")
        if DRY_RUN:
            changed += 1
            dropped_total += dropping
            continue
        if remux(path, keep):
            changed += 1
            dropped_total += dropping
        else:
            failed += 1

    suffix = " (dry run)" if DRY_RUN else ""
    log(f"done: {changed} rewritten, {dropped_total} tracks dropped, "
        f"{skipped} already clean, {failed} failed{suffix}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
