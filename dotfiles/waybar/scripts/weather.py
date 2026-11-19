#!/usr/bin/env python3
"""The weather where the laptop is, for the waybar weather module and its
card: the forecast from Open-Meteo, and the DWD's official warnings and
its rain radar for the place through Bright Sky; for the card also the air
quality (Open-Meteo) and, at home, the DWD's pollen forecast. GeoClue says
where the laptop is; without a fix (no GeoClue, as on the desktop PC, or
nothing better than a guess from the IP address) the place is home,
Seeon-Seebruck.

`weather status` prints the module's JSON: icon and temperature, with any
warning's headline, the condition and any rain or snow of the next two
hours as tooltip, timed by the radar where it reaches (Germany and its
borders), else by the model. While a warning holds it is classed
`alert-<severity>` (the DWD's minor, moderate, severe, extreme), and from
moderate on the warning glyph stands in for the condition's; while rain or
snow is due within the hour it is classed `soon` and an umbrella stands
there. Once the reading is over half an hour old it is classed `stale`,
and the forecast for this hour stands in for the reading. A new warning of
moderate or worse comes as a desktop notification too, once, from
whichever process fetched it. `weather week` opens meteoblue's week for
the place. bar-popup's weather card imports this module. Both go through
one cache file, so the bar and the card always show the same reading.
Times are in the system's time zone, as the clock shows them.
"""

import bisect
import fcntl
import json
import math
import os
import sys
import time
from datetime import date, datetime, timezone

# Home, and the place whenever GeoClue has no fix of the laptop.
HOME = {"name": "Seeon-Seebruck", "lat": 47.977, "lon": 12.472}
# One per user: these dotfiles serve several, and in /tmp none of them can
# replace another's file (sticky bit) or open it to lock (protected_regular).
# /tmp, not the runtime dir: the last forecast outlives a reboot, for a
# start without network.
CACHE = f"/tmp/waybar-weather-{os.getuid()}.json"
# The lock file also holds how the fetches have been failing (fetch()).
LOCK = CACHE + ".lock"
# s before the forecast, the warnings and the radar are asked again
TTL = 600
# s before the air quality is asked again, as its values are hourly; the
# pollen forecast is asked again once the DWD's next one is due (daily,
# late in the morning), or SLOW after a try that found it late.
SLOW = 3600
STALE = 1800  # s before a reading counts as old: fetches have been failing
SOON = 3600   # s ahead that rain or snow puts the umbrella on the bar
# mm/h that count as rain or snow: more than the traces a model spreads
# around a shower, or the radar's faintest echoes.
WET = 0.4
# mm/h, the DWD's bounds of moderate and heavy rain; light falls below 2.5.
MODERATE, HEAVY = 2.5, 10.0
# °C at and below which rain the radar sees and the model doesn't have
# counts as snow: a rough split, as sleet and wet snow fall around it.
SNOW_AT = 1.0
# A warning whose headline comes back within this long of its end is the
# same weather going on (the DWD's next warning for it, the next storm
# cell's), not news for a notification.
GRACE = 3600
# Offline, every run of every bar tried again, and on a network that
# swallows packets each try waits out two 5 s timeouts. After a failed
# fetch the next waits RETRY s, twice as long after each further failure,
# up to RETRY_MAX; on another network (another Wi-Fi, a cable, back from
# suspend) it tries at once.
RETRY, RETRY_MAX = 60, 900
AGENT = "waybar-weather (a desktop bar's weather module)"

# GeoClue, on the system bus. DESKTOP_ID is the name this program goes by
# there (the NixOS config's services.geoclue2.appConfig); STREET is the
# lowest accuracy level at which it looks at the Wi-Fi networks in sight
# rather than at the IP address alone (gclue-wifi.c).
GEOCLUE = "org.freedesktop.GeoClue2"
DESKTOP_ID = "waybar-weather"
STREET = 6
LOCATE = 8    # s to wait for a fix
# m: a coarser fix is a guess from the IP address, on mobile data often
# another city's.
FINE = 5000
NEAR = 2      # km from home, or from the last fix, that still count as there
# s the last fix stands in while GeoClue finds none (a train between Wi-Fi
# networks), before the place is home again.
KEEP = 7200

# Unix times throughout: Open-Meteo writes local times with the one UTC
# offset in force when it answers, which is an hour off past a DST change.
FORECAST = {
    "timeformat": "unixtime",
    # Four days: a cache from yesterday still holds the next 24 hours and
    # three days from today on.
    "forecast_days": 4,
    "current": "temperature_2m,apparent_temperature,weather_code,is_day,"
               "wind_speed_10m,wind_gusts_10m",
    "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
    # Quarter hours for the next two hours, the one in progress first:
    # DWD's ICON-D2 here, not interpolated hours. Each holds what falls in
    # the 15 minutes up to its time.
    "minutely_15": "precipitation,snowfall,weather_code",
    "forecast_minutely_15": 9,
    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
             "precipitation_probability_max,precipitation_sum,sunrise,sunset,"
             "uv_index_max",
}

# The DWD's rain radar (its RV composite) through Bright Sky: 5-minute
# frames on a 1 km grid, measured up to a few minutes ago, then carried on
# two hours; the 3×3 pixels round the place, of which the one it lies in
# counts. Each frame holds what falls in the 5 minutes up to its time. The
# radar stands in for the model's quarter hours while its frames reach
# RADAR_AHEAD s on: it times a shower to within minutes, where the model
# can be off by tens of them.
RADAR = {"distance": 1000, "format": "plain"}
FRAME = 300
RADAR_AHEAD = 3600

# The European air quality index (CAMS through Open-Meteo), by the hour,
# today and tomorrow: a failed ask still leaves the cache the hour now
# falls in.
AIR = {"timeformat": "unixtime", "hourly": "european_aqi", "forecast_days": 2}

# The DWD's pollen forecast for today, tomorrow and the day after, by
# region, one file for Germany (16 KB) issued once a day: region 121,
# "Allgäu/Oberbayern/Bay. Wald", holds home. Its types by their keys in
# the file, in the order they flower, and its levels from none to high in
# half steps ("-1" stands for a missing value).
POLLEN_URL = ("https://opendata.dwd.de/climate_environment/health/alerts/"
              "s31fg.json")
POLLEN_REGION = 121
POLLEN = {"Hasel": "Hazel", "Erle": "Alder", "Esche": "Ash",
          "Birke": "Birch", "Graeser": "Grass", "Roggen": "Rye",
          "Beifuss": "Mugwort", "Ambrosia": "Ragweed"}
POLLEN_LEVELS = ("0", "0-1", "1", "1-2", "2", "2-3", "3")

# Fixed English names: strftime's follow LC_TIME, which is German here.
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
        "Sunday")
# The DWD's warning levels, mildest first.
SEVERITY = ("minor", "moderate", "severe", "extreme")

# md-weather_* glyphs
SUNNY, NIGHT = "\U000f0599", "\U000f0594"
PARTLY, PARTLY_NIGHT = "\U000f0595", "\U000f0f31"
CLOUDY, FOG, HAIL = "\U000f0590", "\U000f0591", "\U000f0592"
RAINY, POURING, SLEET = "\U000f0597", "\U000f0596", "\U000f067f"
SNOWY, SNOWY_HEAVY = "\U000f0598", "\U000f0f36"
STORM = "\U000f067e"  # lightning_rainy
# The bar's stand-ins for the condition: md-umbrella while rain or snow is
# due, md-alert while a warning of moderate or worse holds.
UMBRELLA, ALERT = "\U000f054a", "\U000f0026"

# WMO weather code -> (day glyph, night glyph, description, kind); `kind`
# is the card's CSS colour class.
CODES = {
    0: (SUNNY, NIGHT, "Clear sky", "clear"),
    1: (PARTLY, PARTLY_NIGHT, "Mostly clear", "clear"),
    2: (PARTLY, PARTLY_NIGHT, "Partly cloudy", "cloud"),
    3: (CLOUDY, CLOUDY, "Overcast", "cloud"),
    45: (FOG, FOG, "Foggy", "cloud"),
    48: (FOG, FOG, "Rime fog", "cloud"),
    51: (RAINY, RAINY, "Light drizzle", "rain"),
    53: (RAINY, RAINY, "Drizzle", "rain"),
    55: (RAINY, RAINY, "Dense drizzle", "rain"),
    56: (SLEET, SLEET, "Freezing drizzle", "snow"),
    57: (SLEET, SLEET, "Freezing drizzle", "snow"),
    61: (RAINY, RAINY, "Light rain", "rain"),
    63: (RAINY, RAINY, "Rain", "rain"),
    65: (POURING, POURING, "Heavy rain", "rain"),
    66: (SLEET, SLEET, "Freezing rain", "snow"),
    67: (SLEET, SLEET, "Freezing rain", "snow"),
    71: (SNOWY, SNOWY, "Light snow", "snow"),
    73: (SNOWY, SNOWY, "Snow", "snow"),
    75: (SNOWY_HEAVY, SNOWY_HEAVY, "Heavy snow", "snow"),
    77: (SNOWY, SNOWY, "Snow grains", "snow"),
    80: (RAINY, RAINY, "Rain showers", "rain"),
    81: (RAINY, RAINY, "Rain showers", "rain"),
    82: (POURING, POURING, "Heavy showers", "rain"),
    85: (SNOWY, SNOWY, "Snow showers", "snow"),
    86: (SNOWY_HEAVY, SNOWY_HEAVY, "Heavy snow showers", "snow"),
    95: (STORM, STORM, "Thunderstorm", "storm"),
    96: (HAIL, HAIL, "Thunderstorm with hail", "storm"),
    99: (HAIL, HAIL, "Thunderstorm with hail", "storm"),
}
UNKNOWN = (CLOUDY, CLOUDY, "Unknown", "cloud")
# The codes from the least to the most that matters, for the worst weather
# of a stretch: the codes' numbers don't rise with it (80, slight showers,
# is above 65, heavy rain; 77, snow grains, above 75, heavy snow). Dry,
# fog, drizzle, rain and showers by strength, freezing rain, snow, thunder.
RANK = {code: i for i, code in enumerate((
    0, 1, 2, 3, 45, 48, 51, 53, 55, 61, 80, 63, 81, 65, 82, 56, 57, 66, 67,
    77, 71, 85, 73, 75, 86, 95, 96, 99))}
# Rain, showers and snow, each as (light, moderate, heavy): what falls in
# the next two hours goes by the name of its strength, so the name agrees
# with the strength under it ("Moderate · 2.0 mm"). The radar measures
# drizzle as rain like any other; snow grains are snow.
STRENGTH = {code: kind for codes, kind in (
    ((51, 53, 55, 61, 63, 65), (61, 63, 65)),
    ((80, 81, 82), (80, 81, 82)),
    ((71, 73, 75, 77), (71, 73, 75)),
    ((85, 86), (85, 85, 86))) for code in codes}


def condition(code, is_day=True):
    """(glyph, description, kind) of a WMO weather code."""
    day, night, desc, kind = CODES.get(code, UNKNOWN)
    if is_day:
        return day, desc, kind
    return night, desc, "night" if kind == "clear" else kind


def worst(codes):
    """The code of `codes` that matters most; an unknown one least."""
    return max(codes, key=lambda code: RANK.get(code, -1))


def strength(code, peak):
    """`code` at the strength that `peak` mm/h has by the DWD's bounds:
    light, moderate or heavy rain, showers or snow; freezing rain and
    thunderstorms as they are."""
    kind = STRENGTH.get(code)
    if kind is None:
        return code
    return kind[2 if peak >= HEAVY else 1 if peak >= MODERATE else 0]


def deg(value):
    """Whole degrees; never "-0"."""
    return int(round(value)) or 0


def local(stamp):
    """A Unix time in the system's time zone, as the clock shows it."""
    return datetime.fromtimestamp(stamp, timezone.utc).astimezone()


def zone():
    """The system's time zone by its tz database name, so Open-Meteo's days
    are the clock's; "auto", the place's own, for one without (a POSIX
    rule in TZ)."""
    # Imported here: only a fetch asks.
    from zoneinfo import ZoneInfo
    name = os.environ.get("TZ", "").lstrip(":") or "/etc/localtime"
    if name.startswith("/"):
        name = os.path.realpath(name).partition("/zoneinfo/")[2]
        name = name.removeprefix("posix/").removeprefix("right/")
    try:
        ZoneInfo(name)
    except (ValueError, KeyError, OSError):
        # Not a name, or one the system lacks (ZoneInfoNotFoundError is a
        # KeyError).
        return "auto"
    return name


def when(moment, day=None):
    """'14:45' on `day` (default today), 'Sat 09:00' on any other."""
    text = f"{moment:%H:%M}"
    if moment.date() == (day or local(time.time()).date()):
        return text
    return f"{DAYS[moment.weekday()][:3]} {text}"


def span(alert, now):
    """'DWD · until Sat 09:00' for a warning in force; one still to come
    says from when: 'DWD · from 18:00 until Sat 09:00'. The line under a
    warning's headline in the card, and its notification's body: only
    times, as swaync offers to copy any number of 4 to 8 digits in a
    body, and the DWD's text has heights ("above 1500 m")."""
    parts, day = [], None
    if alert["onset"] is not None and alert["onset"] > now:
        onset = local(alert["onset"])
        parts.append(f"from {when(onset)}")
        day = onset.date()
    if alert["expires"] is not None:
        parts.append(f"until {when(local(alert['expires']), day)}")
    return " · ".join(["DWD", " ".join(parts)] if parts else ["DWD"])


def days(forecast):
    """The date of each daily entry. Open-Meteo stamps a day with its
    midnight at the response's one UTC offset, so the date is the UTC date
    of the stamp plus that offset, on the days after a DST change too."""
    off = forecast["utc_offset_seconds"]
    return [datetime.fromtimestamp(t + off, timezone.utc).date()
            for t in forecast["daily"]["time"]]


def has_today(data):
    return local(time.time()).date() in days(data["forecast"])


def hour(times, now):
    """The index of the forecast hour `now` falls in, or None. By the
    forecast's own hours, which start on the half hour in some zones."""
    i = bisect.bisect_right(times, now) - 1
    return i if 0 <= i < len(times) - 1 else None


def place(data):
    """The place the data is for: home for a cache from before places."""
    spot = data.get("place") if isinstance(data, dict) else None
    if (isinstance(spot, dict) and isinstance(spot.get("lat"), (int, float))
            and isinstance(spot.get("lon"), (int, float))):
        return spot
    return HOME


def name(spot):
    """A place's name, or where it lies: '45.44°N 12.32°E'."""
    if spot.get("name"):
        return spot["name"]
    lat, lon = spot["lat"], spot["lon"]
    return (f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} "
            f"{abs(lon):.2f}°{'E' if lon >= 0 else 'W'}")


def week(spot):
    """The card's link out: meteoblue's week for the place."""
    lat, lon = spot["lat"], spot["lon"]
    return ("https://www.meteoblue.com/en/weather/week/"
            f"{abs(lat):.3f}{'N' if lat >= 0 else 'S'}"
            f"{abs(lon):.3f}{'E' if lon >= 0 else 'W'}")


def read():
    """Whatever the cache holds, or None."""
    try:
        with open(CACHE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def cached():
    """The cache if its forecast covers today, however old: offline after
    midnight, yesterday's forecast still holds today's hours."""
    data = read()
    try:
        if data is not None and has_today(data):
            return data
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    return None


def fresh():
    try:
        return time.time() - os.stat(CACHE).st_mtime < TTL
    except OSError:
        return False


def reading(data):
    """When the current reading was taken: the start of its 15 minutes."""
    return local(data["forecast"]["current"]["time"])


def stale(data):
    """Whether the reading is over STALE old. Online the cache is fetched
    again every TTL, so by then fetches have been failing for a while."""
    return time.time() - data["forecast"]["current"]["time"] > STALE


def estimate(data, now=None):
    """{"temp", "code", "is_day", "forecast"} for now: the reading while it
    is fresh, else the hourly forecast for this moment. Hours after the
    last reading the forecast is the better guess: a laptop suspended at
    lunchtime and opened at night without a network showed the afternoon's
    16° when the night had 12°. The reading again if the forecast ends
    before now."""
    cur = data["forecast"]["current"]
    held = {"temp": cur["temperature_2m"], "code": cur["weather_code"],
            "is_day": cur["is_day"], "forecast": False}
    if not stale(data):
        return held
    now = time.time() if now is None else now
    hourly = data["forecast"]["hourly"]
    times, temps = hourly["time"], hourly["temperature_2m"]
    i = hour(times, now)
    if i is None or None in (temps[i], temps[i + 1]):
        return held
    frac = (now - times[i]) / (times[i + 1] - times[i])
    k = i if frac < 0.5 else i + 1
    return {"temp": temps[i] + (temps[i + 1] - temps[i]) * frac,
            "code": hourly["weather_code"][k], "is_day": hourly["is_day"][k],
            "forecast": True}


def nowcast(data, now=None):
    """Rain or snow in the next two hours, or None while it stays dry, or
    once the cache is too old to tell: {"desc", "start", "end", "steps",
    "step", "radar", "peak", "mm", "cm"}. Timed by the radar's 5-minute
    frames while they reach RADAR_AHEAD on, else by the model's quarter
    hours. start is None while it is already falling, end None if it lasts
    past the window; steps are (start, mm/h, cm of snow) for each `step` s
    from the one in progress. desc, peak (mm/h), mm and cm are those of the
    spell the times are for, the first."""
    if stale(data):
        return None
    now = time.time() if now is None else now
    quarters = data["forecast"].get("minutely_15") or {}
    model = [(t - 900, (mm or 0) * 4, cm or 0, code or 0)
             for t, mm, cm, code in zip(
                 quarters.get("time", ()), quarters.get("precipitation", ()),
                 quarters.get("snowfall", ()),
                 quarters.get("weather_code", ()))
             if t > now][:8]
    wet_model = [q for q in model if q[1] >= WET]
    radar = data.get("radar") or {}
    frames = [(t - FRAME, (mm or 0) * 3600 / FRAME) for t, mm in zip(
        radar.get("time", ()), radar.get("precipitation", ())) if t > now]
    # Two hours at most, in an even number of frames: the card's middle
    # tick then falls on the edge of one.
    frames = frames[:7200 // FRAME]
    frames = frames[:len(frames) // 2 * 2]
    if frames and frames[-1][0] + FRAME - now >= RADAR_AHEAD:
        # Rain or snow the radar can't tell apart: as the model has it
        # where it has either, else by the temperature. Snow in cm at
        # Open-Meteo's 7 cm to 10 mm of water.
        snowy = (any(q[2] > 0 for q in wet_model) if wet_model else
                 data["forecast"]["current"]["temperature_2m"] <= SNOW_AT)
        steps = [(s, rate, rate * FRAME / 3600 * 0.7 if snowy else 0)
                 for s, rate in frames]
        step = FRAME
    elif len(model) >= 4:  # less than an hour left says too little
        steps, step = [q[:3] for q in model], 900
    else:
        return None

    def next_spell(i):
        """(first, stop) of the first spell from step i on, or None: from a
        wet step to where a quarter hour stays dry (a quarter, or three
        frames: the radar's showers come with gaps of a few minutes), or
        to where the dry steps that end the window begin."""
        first = next((j for j in range(i, len(steps))
                      if steps[j][1] >= WET), None)
        if first is None:
            return None
        need, run = 900 // step, 0
        for j in range(first + 1, len(steps)):
            run = run + 1 if steps[j][1] < WET else 0
            if run == need:
                return first, j + 1 - need
        return first, len(steps) - run

    # A spell counts from what a quarter hour at WET brings, 0.1 mm: less
    # is the edge of a shower that passes in five minutes.
    found = next_spell(0)
    while found and (sum(s[1] for s in steps[found[0]:found[1]]) * step
                     < WET * 900):
        found = next_spell(found[1])
    if found is None:
        return None
    first, stop = found
    end = steps[stop][0] if stop < len(steps) else None
    spell = steps[first:stop]
    peak = max(s[1] for s in spell)
    # Named by the model's worst while it falls ("Rain showers",
    # "Thunderstorm"), else by its worst in the window, as the radar may
    # time it apart from the model, else plain rain or snow; at the
    # strength the timing has.
    lo, hi = spell[0][0], end or steps[-1][0] + step
    named = [q[3] for q in wet_model if q[3] >= 51]
    named = [q[3] for q in wet_model
             if q[3] >= 51 and q[0] < hi and q[0] + 900 > lo] or named
    cm = sum(s[2] for s in spell)
    code = worst(named) if named else 71 if cm > 0 else 61
    return {"desc": condition(strength(code, peak))[1],
            "start": None if lo <= now else lo, "end": end,
            "steps": steps, "step": step, "radar": step == FRAME,
            "peak": peak, "mm": sum(s[1] for s in spell) * step / 3600,
            "cm": cm}


def nowcast_text(cast):
    """'Rain showers from 23:00 to 23:45', 'Light rain until 23:30', or
    'Light rain for the next two hours'. Times without a weekday: two
    hours ahead, past midnight too, can't be taken for another day."""
    desc, start, end = cast["desc"], cast["start"], cast["end"]

    def hm(stamp):
        return f"{local(stamp):%H:%M}"

    if start is None:
        if end is None:
            return f"{desc} for the next two hours"
        return f"{desc} until {hm(end)}"
    if end is None:
        return f"{desc} from {hm(start)}"
    return f"{desc} from {hm(start)} to {hm(end)}"


def get(url):
    """The JSON answer at url, or None if there is none. Asked for gzip:
    Bright Sky's radar answers no request that doesn't take it, and the
    others' answers come smaller."""
    # Imported here, as most of this module's start-up time goes to them
    # and most runs (a cache hit) never fetch.
    import gzip
    import http.client
    import urllib.request
    import zlib
    request = urllib.request.Request(
        url, headers={"User-Agent": AGENT, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
        return json.loads(body)
    except (OSError, EOFError, ValueError, zlib.error,
            http.client.HTTPException):
        return None


def gather(asks):
    """{name: get(url)} for {name: url}, all asked at once: a fetch takes
    as long as its slowest source rather than all of them in turn, and on
    a network that swallows packets their timeouts run side by side."""
    import threading
    answers = {}

    def ask(name, url):
        answers[name] = get(url)

    threads = [threading.Thread(target=ask, args=item, daemon=True)
               for item in asks.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return answers


def urls(spot, now):
    """Each source's URL for a place: the forecast, the DWD's warnings (for
    the municipality the point lies in) and its radar (from the frame that
    has just ended: by default Bright Sky's window ends an hour ahead), the
    air quality, and the pollen forecast (all of Germany's)."""
    from urllib.parse import urlencode
    point = {"latitude": spot["lat"], "longitude": spot["lon"]}
    near = {"lat": spot["lat"], "lon": spot["lon"]}
    tz = zone()
    since = datetime.fromtimestamp(now // FRAME * FRAME, timezone.utc)
    return {
        "forecast": "https://api.open-meteo.com/v1/forecast?"
                    + urlencode({**point, "timezone": tz, **FORECAST}),
        "alerts": "https://api.brightsky.dev/alerts?" + urlencode(near),
        "radar": "https://api.brightsky.dev/radar?"
                 + urlencode({**near, **RADAR, "date": since.isoformat()}),
        "air": "https://air-quality-api.open-meteo.com/v1/air-quality?"
               + urlencode({**point, "timezone": tz, **AIR}),
        "pollen": POLLEN_URL,
    }


def warning(alert):
    """A Bright Sky alert as the cache keeps it, times as Unix times; None
    for a test, an all-clear (the end of a warning) or a garbled one."""
    try:
        headline = alert.get("headline_en") or alert.get("headline_de")
        if (alert.get("status") == "test"
                or alert.get("response_type") == "allclear"
                or alert.get("severity") not in SEVERITY or not headline):
            return None
        onset, expires = (
            datetime.fromisoformat(alert[key]).timestamp() if alert.get(key)
            else None for key in ("onset", "expires"))
        return {"id": alert.get("id"), "severity": alert["severity"],
                "headline": headline,
                "description": alert.get("description_en") or "",
                "instruction": alert.get("instruction_en") or "",
                "onset": onset, "expires": expires}
    except (AttributeError, TypeError, ValueError):
        return None


def cell(reply):
    """The name of the DWD warn cell in a Bright Sky answer: the
    municipality ("Seeon-Seebruck"), or the city for one of its quarters
    ("München" for München-Altstadt-Lehel, short "M-Altst./Lehel"); None
    outside Germany, where Bright Sky has no cells."""
    loc = reply.get("location") if isinstance(reply, dict) else None
    if not isinstance(loc, dict):
        return None
    full, district = loc.get("name") or "", loc.get("district") or ""
    if district and full.startswith(district):
        return district
    return loc.get("name_short") or full or None


def frames(reply):
    """The radar at the place, {"time", "precipitation"}: when each
    frame's 5 minutes end, and the mm that fall in them at the pixel the
    place lies in (Bright Sky's pixels are centred on whole positions),
    as far as the frames run unbroken; None for an answer without any."""
    out = {"time": [], "precipitation": []}
    try:
        pos = reply["latlon_position"]
        for frame in reply["radar"]:
            grid = frame["precipitation_5"]
            row = grid[min(max(round(pos["y"]), 0), len(grid) - 1)]
            value = row[min(max(round(pos["x"]), 0), len(row) - 1)]
            end = int(datetime.fromisoformat(frame["timestamp"]).timestamp())
            if (not isinstance(value, int) or value < 0
                    or out["time"] and end != out["time"][-1] + FRAME):
                break
            out["time"].append(end)
            out["precipitation"].append(value / 100)  # in 0.01 mm
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None
    return out if out["time"] else None


def series(reply):
    """The air quality index by the hour, {"time", "european_aqi"}, from
    Open-Meteo's answer, or None."""
    try:
        hourly = reply["hourly"]
        times, index = hourly["time"], hourly["european_aqi"]
        if (times and len(times) == len(index)
                and all(isinstance(t, int) for t in times)):
            return {"time": times, "european_aqi": index}
    except (KeyError, TypeError):
        pass
    return None


def region(reply, now):
    """Home's region in the DWD's pollen file, {"issued", "next",
    "levels"}: the date its "today" is, when the next file is due (Unix
    time; SLOW on while the DWD is late with it), and each type's levels
    today, tomorrow and the day after; None for a file without them. Its
    times are German ones, "2026-10-18 11:00 Uhr"."""
    from zoneinfo import ZoneInfo
    try:
        part = next(p for p in reply["content"]
                    if p.get("partregion_id") == POLLEN_REGION)
        levels = {kind: [str(part["Pollen"][kind].get(day, "-1"))
                         for day in ("today", "tomorrow", "dayafter_to")]
                  for kind in POLLEN if kind in part["Pollen"]}
        issued = datetime.strptime(reply["last_update"][:10], "%Y-%m-%d")
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError):
        return None
    try:
        due = datetime.strptime(
            reply["next_update"].removesuffix(" Uhr"), "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
    except (AttributeError, KeyError, TypeError, ValueError):
        due = 0  # ZoneInfoNotFoundError is a KeyError
    return {"issued": issued.date().isoformat(),
            "next": max(due, now + SLOW), "levels": levels}


def km(a, b):
    """The distance between two places; close enough for a few km."""
    mid = math.radians((a["lat"] + b["lat"]) / 2)
    return 6371 * math.hypot(math.radians(b["lat"] - a["lat"]),
                             math.radians(b["lon"] - a["lon"]) * math.cos(mid))


def locate():
    """(lat, lon) where GeoClue puts the laptop, or None: no GeoClue (the
    desktop PC), no fix within LOCATE s, or one coarser than FINE. A client
    of its own for one fix, deleted again, so GeoClue stops looking; on a
    main context of its own, so it works in the card's fetch thread too."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        return None
    props = "org.freedesktop.DBus.Properties"
    manager = "/org/freedesktop/GeoClue2/Manager"
    ctx = GLib.MainContext.new()
    ctx.push_thread_default()
    loop = GLib.MainLoop.new(ctx, False)
    pending = Gio.Cancellable.new()
    found = []
    bus = client = watch = None

    def call(path, iface, method, args=None, reply=None):
        return bus.call_sync(GEOCLUE, path, iface, method, args,
                             GLib.VariantType.new(reply) if reply else None,
                             Gio.DBusCallFlags.NONE, 2000, None)

    def updated(_bus, _sender, _path, _iface, _signal, params):
        try:
            fix = call(params.unpack()[1], props, "GetAll",
                       GLib.Variant("(s)", (GEOCLUE + ".Location",)),
                       "(a{sv})").unpack()[0]
            if fix["Accuracy"] <= FINE:
                found.append((fix["Latitude"], fix["Longitude"]))
                loop.quit()
                return
            # Coarse, and no better coming without Wi-Fi: stop waiting.
            level = call(manager, props, "Get",
                         GLib.Variant("(ss)", (GEOCLUE + ".Manager",
                                               "AvailableAccuracyLevel")),
                         "(v)").unpack()[0]
            if level < STREET:
                loop.quit()
        except (GLib.Error, KeyError, TypeError):
            loop.quit()

    def started(_bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error:  # refused (no agent), or cancelled at the end
            loop.quit()

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        client = call(manager, GEOCLUE + ".Manager", "CreateClient",
                      reply="(o)").unpack()[0]
        for key, value in (("DesktopId", GLib.Variant("s", DESKTOP_ID)),
                           ("RequestedAccuracyLevel",
                            GLib.Variant("u", STREET))):
            call(client, props, "Set",
                 GLib.Variant("(ssv)", (GEOCLUE + ".Client", key, value)))
        watch = bus.signal_subscribe(
            GEOCLUE, GEOCLUE + ".Client", "LocationUpdated", client, None,
            Gio.DBusSignalFlags.NONE, updated)
        # Async: without an agent GeoClue answers Start only after seconds.
        bus.call(GEOCLUE, client, GEOCLUE + ".Client", "Start", None, None,
                 Gio.DBusCallFlags.NONE, LOCATE * 1000, pending, started)
        timer = GLib.timeout_source_new(LOCATE * 1000)
        timer.set_callback(lambda *_: loop.quit())
        timer.attach(ctx)
        loop.run()
        timer.destroy()
    except GLib.Error:
        pass
    finally:
        pending.cancel()
        if watch is not None:
            bus.signal_unsubscribe(watch)
        if client is not None:
            try:
                call(manager, GEOCLUE + ".Manager", "DeleteClient",
                     GLib.Variant("(o)", (client,)))
            except GLib.Error:
                pass
        # Run what the cancel and the unsubscribe still queue on this
        # context: left undone, the card's process, which stays, would
        # keep it, and the context, for good.
        for _ in range(100):
            if not ctx.iteration(False):
                break
        ctx.pop_thread_default()
    return found[0] if found else None


def where(fix, last):
    """The place to fetch for, from GeoClue's fix and the cache's place:
    home near home, the last place near that one (named already), else the
    fix itself, its name to come from Bright Sky. Without a fix the last
    place while its fix is under KEEP old, else home."""
    now = time.time()
    last = last if isinstance(last.get("fix"), (int, float)) else None
    if fix is None:
        return last if last and now - last["fix"] < KEEP else HOME
    # Rounded to about 100 m: no closer than the weather needs.
    here = {"lat": round(fix[0], 3), "lon": round(fix[1], 3)}
    if km(here, HOME) < NEAR:
        return HOME
    if last and km(here, last) < NEAR:
        return dict(last, fix=now)
    return dict(here, name=None, fix=now)


def network():
    """The default routes: what changes with the network (another Wi-Fi, a
    cable, back from suspend); "" with none."""
    routes = []
    try:
        with open("/proc/net/route") as fh:
            for line in fh.readlines()[1:]:
                f = line.split()
                if len(f) > 7 and f[1] == f[7] == "00000000":
                    routes.append(f"{f[0]} {f[2]}")
        with open("/proc/net/ipv6_route") as fh:
            for line in fh:
                f = line.split()
                if len(f) == 10 and f[1] == "00" and f[9] != "lo":
                    routes.append(f"{f[9]} {f[4]}")
    except OSError:
        pass
    return " ".join(sorted(routes))


def failures(lock):
    """{"fails", "next", "net"} from the lock file: how many fetches in a
    row have failed, when to try again, on which network; None after a
    good one."""
    lock.seek(0)
    try:
        state = json.loads(lock.read())
        if (isinstance(state, dict) and isinstance(state.get("fails"), int)
                and isinstance(state.get("next"), (int, float))
                and isinstance(state.get("net"), str)):
            return state
    except ValueError:
        pass
    return None


def order(alert):
    """Warnings' order: the worst first, then the soonest."""
    return -SEVERITY.index(alert["severity"]), alert["onset"] or 0


def news(memory, alerts, now):
    """The warnings of moderate or worse in `alerts` that are news, one per
    headline, worst first, and the memory to keep: {headline: end} of each
    warning seen, until GRACE past its end or, one without an end (None),
    until it is gone. By headline: the DWD reissues a warning that goes on
    under a new id with the same headline, and one that grows worse (STORM
    GUSTS to SEVERE GUSTS) under a new one; one that drops out of an
    answer for a while is no news when it comes back."""
    held = sorted((a for a in alerts
                   if a["expires"] is None or a["expires"] > now), key=order)
    seen = {h for h, end in memory.items() if end is None or end + GRACE > now}
    keep = {h: end for h, end in memory.items()
            if end is not None and end + GRACE > now}
    new, ends = {}, {}
    for a in held:
        h = a["headline"]
        if h not in seen and h not in new and a["severity"] != "minor":
            new[h] = a
        # The last end of its periods; None if one has none.
        if a["expires"] is None or ends.get(h, 0) is None:
            ends[h] = None
        else:
            ends[h] = max(a["expires"], ends.get(h, 0))
    keep.update(ends)
    return list(new.values()), keep


def announce(warnings, now):
    """A desktop notification for each warning: the headline over when it
    holds. Normal for moderate ones (orange: storm gusts, heavy rain),
    which come several times a year; critical for severe and extreme ones
    (red, violet), which swaync shows through Do Not Disturb and keeps
    until dismissed."""
    import subprocess
    for alert in warnings:
        urgency = "normal" if alert["severity"] == "moderate" else "critical"
        try:
            subprocess.Popen(
                ["notify-send", "-a", "Weather", "-i", "weather-severe-alert",
                 "-u", urgency, "--", alert["headline"], span(alert, now)],
                start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def fetch(urgent=False):
    """Ask the sources, for where the laptop is, all at once, and merge
    their answers into the cache: the forecast, the warnings and the radar
    every time; the air quality once it is SLOW old, and at home the
    pollen forecast once the DWD's next one is due. A source that fails
    keeps what the cache had from it for the same place, and a forecast
    that fails for a new place keeps the cache as it is. Returns the data
    if it covers today, else None. Under a lock, and not at all if another
    process fetched while this one waited for it: the bar on each monitor
    and the card share the cache, and the bars' timers run in step; so a
    new warning is announced once. Nor while the fetches keep failing on
    this network and the next try isn't due, unless `urgent` (the card
    opened: someone wants to see)."""
    with open(LOCK, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if fresh():
            return cached()
        failed, net = failures(lock), network()
        if (not urgent and failed and failed["net"] == net
                and time.time() < failed["next"]):
            return None
        old = read()
        if not isinstance(old, dict):
            old = {}
        last = place(old)
        # No route out, no fix: GeoClue would wait out LOCATE for nothing.
        spot = where(locate() if net else None, last)
        same = (spot["lat"], spot["lon"]) == (last["lat"], last["lon"])
        home = km(spot, HOME) < NEAR
        now = time.time()
        air = old.get("air") if same else None
        pollen = old.get("pollen")
        asks = ["forecast", "alerts", "radar"]
        if not (isinstance(air, dict) and now - air.get("at", 0) < SLOW):
            asks.append("air")
        # The region is home's.
        if home and not (isinstance(pollen, dict)
                         and now < pollen.get("next", 0)):
            asks.append("pollen")
        url = urls(spot, now)
        got = gather({name: url[name] for name in asks})
        forecast = got.get("forecast")
        # An error reply is JSON too; only keep a real forecast.
        if not (isinstance(forecast, dict) and forecast.get("current")
                and forecast.get("hourly") and forecast.get("daily")):
            forecast = None
        reply = got.get("alerts")
        alerts = None
        if isinstance(reply, dict) and isinstance(reply.get("alerts"), list):
            alerts = [w for w in map(warning, reply["alerts"]) if w]
        if "fix" in spot:
            spot = dict(spot, name=cell(reply) or spot.get("name"))
        # The radar's grid reaches far past Germany, its measurements only
        # a little way: out there its zeros say nothing. So only where
        # Bright Sky has a DWD warn cell for the place, in Germany.
        radar = frames(got.get("radar")) if home or cell(reply) else None
        index = series(got.get("air"))
        if index is not None:
            air = dict(index, at=now)
        pollen = region(got.get("pollen"), now) or pollen
        data = {"place": spot,
                "forecast": forecast or (old.get("forecast") if same
                                         else None),
                "alerts": (alerts if alerts is not None
                           else old.get("alerts", []) if same else []),
                "radar": radar or (old.get("radar") if same else None),
                "air": air,
                "pollen": pollen if isinstance(pollen, dict) else None}
        # What has been announced, for this place; a cache from before
        # notifications has the warnings it held as seen.
        memory = old.get("announced") if same else {}
        if not isinstance(memory, dict):
            memory = news({}, old.get("alerts") or [], now)[1]
        hot, data["announced"] = news(memory, data["alerts"], now)
        lock.seek(0)
        lock.truncate()
        if data["forecast"] is None or (forecast is None and alerts is None):
            fails = (failed["fails"] if failed and failed["net"] == net
                     else 0) + 1
            lock.write(json.dumps({
                "fails": fails, "net": net,
                "next": time.time() + min(RETRY_MAX,
                                          RETRY * 2 ** (fails - 1))}))
            return None
        tmp = f"{CACHE}.{os.getpid()}"
        try:
            with open(tmp, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp, CACHE)  # readers never see half a file
        except OSError:
            pass
        else:
            # Once the cache holds them as announced: a process that can't
            # write it would announce them again at every try.
            announce(hot, now)
    try:
        return data if has_today(data) else None
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


def load():
    """Fresh data when possible, else the last that covers today, else
    None."""
    if fresh():
        data = cached()
        if data is not None:
            return data
    return fetch() or cached()


def alerts(data):
    """The cached warnings that have not expired, worst first, then the
    soonest."""
    now = time.time()
    held = [a for a in data.get("alerts") or []
            if a["expires"] is None or a["expires"] > now]
    return sorted(held, key=order)


def air(data, now):
    """The European air quality index for the hour now falls in, or
    None."""
    try:
        index = data["air"]
        i = bisect.bisect_right(index["time"], now) - 1
        if i < 0 or now - index["time"][i] >= 3600:
            return None
        value = index["european_aqi"][i]
    except (IndexError, KeyError, TypeError):
        return None
    return value if isinstance(value, (int, float)) else None


def pollen(data, day):
    """[(type, level)] of the DWD's pollen forecast for `day` (a date) from
    level 1 on, the strongest first, a tie in the order they flower; []
    without one for that day, and away from home, as its region is
    home's."""
    if km(place(data), HOME) >= NEAR:
        return []
    try:
        forecast = data["pollen"]
        k = (day - date.fromisoformat(forecast["issued"])).days
        if not 0 <= k <= 2:
            return []
        found = [(english, forecast["levels"][kind][k])
                 for kind, english in POLLEN.items()
                 if kind in forecast["levels"]]
    except (IndexError, KeyError, TypeError, ValueError):
        return []
    low = POLLEN_LEVELS.index("1")
    return sorted(((english, level) for english, level in found
                   if level in POLLEN_LEVELS[low:]),
                  key=lambda entry: -POLLEN_LEVELS.index(entry[1]))


def status(data):
    """The waybar module."""
    if data is None:
        return {"text": f"{CLOUDY} --", "tooltip": "Weather unavailable"}
    est = estimate(data)
    glyph, desc, _ = condition(est["code"], est["is_day"])
    held = alerts(data)
    # The same warning comes again for each period it holds.
    lines = list(dict.fromkeys(a["headline"] for a in held)) + [desc]
    classes = [f"alert-{held[0]['severity']}"] if held else []
    cast = nowcast(data)
    if cast is not None:
        lines.append(nowcast_text(cast))
        if cast["start"] is not None and cast["start"] - time.time() <= SOON:
            glyph = UMBRELLA
            classes.append("soon")
    # A glyph as well as the colour from orange on: yellow on the bar's sky
    # was easy to miss, and a colour alone says nothing to a quick glance.
    # Yellow ones (frost, gusts, fog) are common enough to leave as colour.
    if held and held[0]["severity"] != "minor":
        glyph = ALERT
    if stale(data):
        classes.append("stale")
        lines.append(f"Forecast · no reading since {when(reading(data))}"
                     if est["forecast"] else f"as of {when(reading(data))}")
    return {"text": f"{glyph} {deg(est['temp'])}°C",
            "tooltip": "\n".join(lines), "class": classes}


def main(argv):
    if argv[1:] == ["status"]:
        print(json.dumps(status(load())), flush=True)
        return 0
    if argv[1:] == ["week"]:
        # The card's link out, for the place its data is for.
        try:
            os.execvp("xdg-open", ["xdg-open", week(place(read()))])
        except OSError as err:
            print(f"weather: {err}", file=sys.stderr)
            return 1
    print("usage: weather status|week", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
