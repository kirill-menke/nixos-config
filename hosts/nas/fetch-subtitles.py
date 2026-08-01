"""Fetch subtitles from OpenSubtitles for media that has none usable.

Handles several languages (OS_LANGS), each independently: a file carrying an
English text track still gets German fetched, because Jellyfin can only serve
what is actually present.

Run by the fetch-subtitles.timer (see subtitles.nix). Deliberately conservative:
it only touches files that would otherwise force Jellyfin to burn in image-based
subtitles, and it never spends more of the daily quota than it has to.

Why this exists: PGS (image) subtitles cannot be handed to a client as text, so
Jellyfin composites them into the video -- a full re-encode that throws away
Dolby Vision and pins the CPU. A sibling .srt makes the same subtitles free.

Credentials come from the environment (EnvironmentFile in the unit), never from
this file or the repo -- the nixos-config repo is public.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.opensubtitles.com/api/v1"
UA = "kirill-nas v1.0"

KEY = os.environ["OS_API_KEY"]
USER = os.environ["OS_USER"]
PASS = os.environ["OS_PASS"]
ROOT = os.environ.get("MEDIA_ROOT", "/tank/data/media")
# Comma-separated ISO 639-1 codes, tried in order. Each language is fetched
# independently: a file with an English text track still gets German fetched.
LANGS = [x.strip() for x in os.environ.get("OS_LANGS", "en").split(",") if x.strip()]
# Hard cap per run. The free tier allows 20 downloads/day; staying under it
# means a big import trickles in over days instead of erroring out mid-run.
MAX_PER_RUN = int(os.environ.get("OS_MAX_PER_RUN", "10"))
JELLYFIN = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096")
JF_TOKEN = os.environ.get("JELLYFIN_TOKEN", "")

STOP = {"1080p", "2160p", "720p", "bluray", "web", "dl", "webrip", "webdl",
        "x264", "x265", "hevc", "avc", "aac", "ac3", "dts", "hdr", "10bit",
        "remux", "the", "and", "of", "uhd", "truehd", "atmos", "dv"}

# Jellyfin's special-feature folders. Files in these are extras, not episodes:
# they carry no SxxEyy, so any title-based guess is nonsense and matches an
# unrelated subtitle. Skipping them also stops extras eating the daily quota.
EXTRA_DIRS = {"featurettes", "extras", "specials", "behind the scenes",
              "deleted scenes", "interviews", "scenes", "samples", "shorts",
              "trailers", "other"}


def log(msg):
    print(msg, flush=True)


def req(path, params=None, method="GET", body=None, token=None):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Api-Key", KEY)
    r.add_header("User-Agent", UA)
    r.add_header("Accept", "application/json")
    if body:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()[:300]
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            # OpenSubtitles serves its HTML sign-in page when the Api-Key is
            # rejected -- exactly how the Jellyfin plugin fails. Say so plainly
            # rather than surfacing a JSON parse error.
            hint = " (HTML page -- Api-Key rejected?)" if "<html" in raw.lower() else ""
            return e.code, {"error": raw[:120] + hint}


# ffprobe reports ISO 639-2 ("eng", "ger"/"deu"); OpenSubtitles wants 639-1.
ISO2TO1 = {"eng": "en", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr",
           "spa": "es", "ita": "it", "dut": "nl", "nld": "nl", "por": "pt",
           "pol": "pl", "rus": "ru", "swe": "sv", "dan": "da", "nor": "no",
           "fin": "fi", "cze": "cs", "ces": "cs", "hun": "hu", "tur": "tr",
           "jpn": "ja", "kor": "ko", "chi": "zh", "zho": "zh", "ara": "ar"}
TEXT_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}


def text_langs(path):
    """Languages (ISO 639-1) for which the file already has a TEXT subtitle.

    Those are extracted and served as text by Jellyfin at no cost, so fetching
    an external copy would spend quota for nothing. Image tracks (PGS/VobSub)
    deliberately do not count -- they are the whole reason this script exists.

    Returns None if ffprobe fails, meaning "unknown, do not touch this file".
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "s",
             "-show_entries", "stream=codec_name:stream_tags=language",
             "-of", "json", path],
            capture_output=True, text=True, timeout=120).stdout
        streams = json.loads(out).get("streams", [])
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as e:
        log(f"  ffprobe failed on {os.path.basename(path)}: {e}")
        return None
    langs = set()
    for s in streams:
        if s.get("codec_name") not in TEXT_CODECS:
            continue
        raw = (s.get("tags") or {}).get("language", "").lower()
        langs.add(ISO2TO1.get(raw, raw[:2] if raw else "und"))
    return langs


def toks(s):
    return {t for t in re.split(r"[^a-z0-9]+", s.lower())
            if t and t not in STOP and len(t) > 2}


def guess_query(path):
    """Derive search terms from the on-disk layout, or None if unsure.

    shows/<Series>/<Season N>/<... SxxEyy ...>.mkv  -> series + season/episode
    movies/<Title (Year)>/<file>.mkv                -> title

    Returning None is deliberate and important: a wrong guess does not fail
    loudly, it silently downloads an unrelated subtitle and burns quota. Better
    to skip and leave the file alone.
    """
    name = os.path.basename(path)
    rel = os.path.relpath(path, ROOT)
    parts = rel.split(os.sep)

    # Anything inside a special-feature folder is an extra, not a numbered
    # episode -- no reliable way to identify it, so do not try.
    if any(p.lower() in EXTRA_DIRS for p in parts[:-1]):
        return None

    m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", name)
    if len(parts) >= 2 and parts[0] == "shows":
        # Under shows/ an episode number is required. Without one this is an
        # extra or a stray file, and guessing it as a film title matches
        # garbage -- which is exactly what happened to the AHS featurettes.
        if not m:
            return None
        series = re.sub(r"[._]", " ", parts[1])
        series = re.sub(r"\s*\(?\d{4}\)?\s*$", "", series).strip()
        return {"query": series,
                "season_number": int(m.group(1)),
                "episode_number": int(m.group(2))}

    title = parts[1] if len(parts) >= 2 else os.path.splitext(name)[0]
    title = re.sub(r"[._]", " ", title)
    title = re.split(r"\s*\(", title)[0]
    title = re.split(r"\b(1080p|2160p|720p|BluRay|WEB)\b", title, flags=re.I)[0]
    title = title.strip()
    return {"query": title} if len(title) >= 2 else None


def score(cand, mytoks):
    a = cand["attributes"]
    overlap = len(toks(a.get("release") or "") & mytoks)
    return (overlap,
            1 if a.get("from_trusted") else 0,
            0 if a.get("hearing_impaired") else 1,
            a.get("download_count") or 0)


def refresh_jellyfin():
    if not JF_TOKEN:
        log("no JELLYFIN_TOKEN set, skipping library refresh")
        return
    r = urllib.request.Request(f"{JELLYFIN}/Library/Refresh", method="POST")
    r.add_header("Authorization",
                 f'MediaBrowser Token="{JF_TOKEN}", Client="subfetch", '
                 f'Device="nas", DeviceId="subfetch", Version="1.0"')
    try:
        with urllib.request.urlopen(r, timeout=30) as f:
            log(f"jellyfin refresh -> HTTP {f.status}")
    except OSError as e:
        log(f"jellyfin refresh failed: {e}")


def main():
    wanted = []
    for dirpath, _, files in os.walk(ROOT):
        for f in sorted(files):
            if not f.endswith((".mkv", ".mp4")):
                continue
            full = os.path.join(dirpath, f)
            if guess_query(full) is None:
                continue  # extras and anything unidentifiable
            have = text_langs(full)
            if have is None:
                continue  # ffprobe failed; leave it alone
            base = os.path.splitext(f)[0]
            for lang in LANGS:
                if lang in have:
                    continue  # already has a text track in this language
                if os.path.exists(os.path.join(dirpath, f"{base}.{lang}.srt")):
                    continue
                wanted.append((full, lang))

    if not wanted:
        log("nothing to do: every file already has a text track or sibling .srt "
            f"for {'/'.join(LANGS)}")
        return 0

    per_lang = {la: sum(1 for _, x in wanted if x == la) for la in LANGS}
    breakdown = ", ".join(f"{la}:{n}" for la, n in per_lang.items() if n)
    log(f"{len(wanted)} subtitle(s) wanted ({breakdown}); "
        f"fetching up to {MAX_PER_RUN} this run")

    st, d = req("login", method="POST", body={"username": USER, "password": PASS})
    if st != 200:
        log(f"login failed: HTTP {st} {json.dumps(d)[:160]}")
        return 1
    token = d["token"]
    log(f"login ok, quota today: {d.get('user', {}).get('allowed_downloads')}")

    got = 0
    for path, lang in wanted:
        if got >= MAX_PER_RUN:
            log(f"reached per-run cap ({MAX_PER_RUN}); the rest will follow next run")
            break
        base = os.path.splitext(os.path.basename(path))[0]
        q = guess_query(path)
        if q is None:
            continue
        st, d = req("subtitles", dict(q, languages=lang))
        time.sleep(0.5)
        cands = [c for c in d.get("data", []) if c["attributes"].get("files")]
        if not cands:
            log(f"  [{lang}] {base[:48]}: none available")
            continue
        best = max(cands, key=lambda c: score(c, toks(base)))
        fid = best["attributes"]["files"][0]["file_id"]

        st, dl = req("download", method="POST", body={"file_id": fid}, token=token)
        time.sleep(0.6)
        if st != 200 or "link" not in dl:
            log(f"  [{lang}] {base[:48]}: download failed HTTP {st} {json.dumps(dl)[:90]}")
            if st == 406:  # quota exhausted
                log("  daily quota exhausted; stopping")
                break
            continue
        dest = os.path.join(os.path.dirname(path), f"{base}.{lang}.srt")
        tmp = dest + ".part"
        try:
            rq = urllib.request.Request(dl["link"], headers={"User-Agent": UA})
            with urllib.request.urlopen(rq, timeout=90) as f:
                data = f.read()
            if not data.lstrip().startswith((b"1", b"\xef\xbb\xbf1", b"WEBVTT")):
                log(f"  [{lang}] {base[:48]}: response is not a subtitle, skipping")
                continue
            # Write via a .part file then rename, so a crash mid-download can
            # never leave a truncated .srt that later runs would treat as done.
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            got += 1
            log(f"  [{lang}] {base[:48]}: ok ({len(data)}B, {dl.get('remaining')} left today)")
        except OSError as e:
            log(f"  [{lang}] {base[:48]}: fetch error {e}")
            if os.path.exists(tmp):
                os.unlink(tmp)
        time.sleep(0.5)

    log(f"fetched {got} subtitle file(s)")
    if got:
        refresh_jellyfin()
    return 0


if __name__ == "__main__":
    sys.exit(main())
