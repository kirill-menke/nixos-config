"""The Weather card, `bar-popup weather`: weather.py's cache, for where the
laptop is (home, Seeon-Seebruck, without a fix). The DWD's warnings, the
reading now, rain or snow due in the next two hours, the next 24 hours in
3-hour columns over a temperature chart, three days, then the sun, UV and
wind, and the pollen and the air once they are worth a thought.
"""

import math
import threading
import time

import cairo

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
from cardkit import MOCHA, Panel, button, label  # noqa: E402
import weather  # noqa: E402

STEP = 3       # h per column
COLS = 8       # columns: the next 24 hours
CHART_H = 56   # px
RAIN_H = 28    # px, the next two hours' bars

# Nerd Font glyphs (md-*); the conditions' own are weather.py's.
ICON = {
    "sunrise": "\U000f059c",
    "sunset": "\U000f059b",
    "wind": "\U000f059d",
    "uv": "\U000f17fe",
    "alert": "\U000f0026",
    "more": "\U000f0140",
    "umbrella": "\U000f054a",
    "less": "\U000f0143",
    "week": "\U000f03cc",
    "pollen": "\U000f1885",  # md-flower_pollen
    "air": "\U000f0a71",     # md-smog
}

# WMO codes of snowfall. Any other precipitation reads as rain, freezing
# rain and drizzle too: they fall liquid.
SNOWFALL = {71, 73, 75, 77, 85, 86}

# The DWD's pollen levels from 1 on, by name and by the warning level
# whose colour they take: low yellow, moderate orange, high red.
POLLEN = {"1": ("low", "minor"), "1-2": ("low to moderate", "moderate"),
          "2": ("moderate", "moderate"), "2-3": ("moderate to high", "severe"),
          "3": ("high", "severe")}
# The European air quality index's bands from moderate on (good and fair,
# under 40, go unshown), the worst first, likewise: moderate yellow, poor
# orange, very poor red, extremely poor violet.
AIR = ((100, "extremely poor", "extreme"), (80, "very poor", "severe"),
       (60, "poor", "moderate"), (40, "moderate", "minor"))

CSS = b"""
/* Weather card: glyphs coloured by condition, the next 24 hours as 3-hour
   columns over a temperature curve with the chance of rain under it, the
   DWD's warnings as banners in their level's colour. Scoped to the card:
   names like .hour would reach other cards' widgets. */
.card.weather .wicon {
    font-size: 19px;
    color: @sky;
}
.card.weather .wicon.big { font-size: 44px; }
.card.weather .wicon.clear, .card.weather .wicon.storm { color: @yellow; }
.card.weather .wicon.night { color: @lavender; }
.card.weather .wicon.cloud { color: @overlay2; }
.card.weather .wicon.rain { color: @sapphire; }
.card.weather .wicon.snow { color: @text; }
.card.weather .hero { padding: 0 10px 4px; }
.card.weather .now-temp { color: @text; font-size: 32px; font-weight: bold; }
.card.weather .cond { color: @text; }
.card.weather .hour { color: @subtext0; font-size: 12px; }
.card.weather .hour.now { color: @accent; font-weight: bold; }
.card.weather .htemp { color: @text; }
.card.weather .chance { color: @sapphire; font-size: 12px; }
.card.weather .chance.dry { color: @dim; }
.card.weather .days { padding: 0 10px; }
.card.weather .day { color: @text; }
.card.weather .hi { color: @text; font-weight: bold; }
.card.weather .lo { color: @subtext0; }
.card.weather .facts { padding: 6px 10px 0; }
.card.weather .facts .wicon { font-size: 15px; color: @subtext0; }
.card.weather .facts.air { padding-top: 4px; }
.card.weather .alert { border-radius: 12px; padding: 6px 10px; }
.card.weather .alert .head { font-weight: bold; }
/* Both lines under the headline in subtext1: subtext0 has 4.3:1 on the
   yellow banner. */
.card.weather .alert .sub { color: @subtext1; }
.card.weather .alert .chevron { font-size: 15px; }
/* Rain or snow due in the next two hours: a banner shaped like a
   warning's, in the sapphire of the chance of rain rather than a DWD
   colour, as it is the forecast's word, not an official warning. Lines
   under the headline in subtext1, as on the warnings: subtext0 has 4.6:1
   on the tint, too close to call. */
.card.weather .nowcast {
    border-radius: 12px;
    padding: 6px 10px;
    background-color: alpha(@sapphire, 0.1);
}
.card.weather .nowcast .wicon,
.card.weather .nowcast .head { color: @sapphire; }
.card.weather .nowcast .head { font-weight: bold; }
.card.weather .nowcast .sub { color: @subtext1; }
"""

# A warning's banner in the DWD's colour for its level: yellow, orange,
# red, and violet for extreme, in mocha's shades. A 10 % tint; hovered,
# 22 % over the darker base rather than over the card: deeper over the
# card, the tint took the red and violet headlines under 4.5:1. Pollen
# and air levels take the same colours, on the card: from 5.4:1 (red) up.
LEVELS = {"minor": "yellow", "moderate": "peach", "severe": "red",
          "extreme": "mauve"}
CSS += "".join(f"""
.card.weather .alert.{level} {{ background-color: alpha(@{colour}, 0.1); }}
.card.weather .alert.{level}:hover {{
    background-color: mix(@base, @{colour}, 0.22);
}}
.card.weather .alert.{level} .wicon,
.card.weather .alert.{level} .head,
.card.weather .level.{level} {{ color: @{colour}; }}
""" for level, colour in LEVELS.items()).encode()


def rgba(cr, name, alpha=1.0):
    cr.set_source_rgba(*MOCHA[name], alpha)


class WeatherPanel(Panel):
    """The DWD's warnings, the reading now, rain or snow due in the next
    two hours, the next 24 hours, three days, then the sun, UV and wind,
    and pollen and air quality while they are worth a thought; the title
    row names the place and says when the reading was taken, or that the
    card is offline.

    Opens on the cache the bar module keeps fresh; only when it is not does
    it fetch in the background and fill in again, the warnings that were
    opened staying open."""

    title = "Weather"
    css = "weather"

    def __init__(self, app):
        super().__init__(app)
        # `weather week` reads the place from the cache when clicked, so a
        # fetch for another place while the card is open moves it too.
        self.header_button(ICON["week"], "The week on meteoblue",
                           "weather", "week")
        self.stamp = label("", "sub")
        self.stamp.set_valign(Gtk.Align.CENTER)
        self.header.pack_end(self.stamp, False, False, 0)
        self.shown = set()  # ids of the warnings opened to their text
        self.data = weather.cached()
        self.failed = False
        if self.data is None or not weather.fresh():
            threading.Thread(target=self._fetch, daemon=True).start()
        self.fill()

    def _fetch(self):
        # Urgent: someone opened the card, so try even while the bar's
        # fetches are backing off.
        GLib.idle_add(self._fetched, weather.fetch(urgent=True))

    def _fetched(self, data):
        if not self.dead:
            if data is not None and data != self.data:
                self.data = data
                self.refresh()
            elif data is None and self.data is None:
                self.failed = True
                self.refresh()
        return GLib.SOURCE_REMOVE

    def fill(self):
        self._stamp()
        if self.data is None:
            # Empty while the first fetch runs; it takes well under a second.
            if self.failed:
                self.body.pack_start(label("Weather unavailable", "empty"),
                                     False, False, 0)
            return
        now = time.time()
        for alert in weather.alerts(self.data):
            self.body.pack_start(self._alert(alert, now), False, False, 0)
        self.body.pack_start(self._hero(now), False, False, 0)
        cast = weather.nowcast(self.data, now)
        if cast is not None:
            self.body.pack_start(self._nowcast(cast, now), False, False, 0)
        hours = self._hours(now)
        if 0 in hours:
            self.body.pack_start(label("Next 24 hours", "seclabel"),
                                 False, False, 0)
            self.body.pack_start(self._columns(hours, now), False, False, 0)
        days = self._days(now)
        if days is not None:
            self.body.pack_start(label("3-day forecast", "seclabel"),
                                 False, False, 0)
            self.body.pack_start(days, False, False, 0)
        day = self._sun_day(now)
        self.body.pack_start(self._facts(day), False, False, 0)
        air = self._air(now, day)
        if air is not None:
            self.body.pack_start(air, False, False, 0)

    def _stamp(self):
        """The place and the reading's time. Once that is old the fetches
        have been failing for a while: 'Offline · last 11:45', in red."""
        ctx = self.stamp.get_style_context()
        if self.data is None:
            self.stamp.set_text("")
            return
        at = weather.when(weather.reading(self.data))
        old = weather.stale(self.data)
        place = weather.name(weather.place(self.data))
        self.stamp.set_text(f"Offline · last {at}" if old
                            else f"{place} · {at}")
        (ctx.add_class if old else ctx.remove_class)("bad")

    def _alert(self, alert, now):
        """A warning in its level's colour: the headline over 'DWD · until
        Sat 09:00'; a click opens it to the DWD's description and advice."""
        top = Gtk.Box(spacing=10)
        top.pack_start(label(ICON["alert"], "wicon", xalign=0.5),
                       False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        for line, cls in ((alert["headline"], "head"),
                          (weather.span(alert, now), "sub")):
            lab = label(line, cls)
            lab.set_line_wrap(True)
            text.pack_start(lab, False, False, 0)
        top.pack_start(text, True, True, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(top, False, False, 0)
        details = "\n\n".join(part for part in (alert["description"],
                                                alert["instruction"]) if part)
        if not details:
            for cls in ("alert", alert["severity"]):
                box.get_style_context().add_class(cls)
            return box
        chevron = label("", "wicon", "chevron")
        top.pack_end(chevron, False, False, 0)
        more = label(details, "sub", "details")
        more.set_line_wrap(True)
        more.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # Left out of the card's show_all(); a click shows it.
        more.set_no_show_all(True)
        box.pack_start(more, False, False, 0)
        self._open(alert["id"] in self.shown, more, chevron)
        btn = button(box, "row", "alert", alert["severity"])
        btn.connect("clicked", self._on_alert, alert["id"], more, chevron)
        return btn

    def _on_alert(self, _btn, key, more, chevron):
        shown = not more.get_visible()
        (self.shown.add if shown else self.shown.discard)(key)
        self._open(shown, more, chevron)

    @staticmethod
    def _open(shown, more, chevron):
        more.set_visible(shown)
        chevron.set_text(ICON["less" if shown else "more"])

    def _hero(self, now):
        """The reading: glyph, temperature, and the condition in full,
        wrapped if it must, over how it feels. Offline, the forecast for
        this hour instead (weather.estimate), which says so."""
        est = weather.estimate(self.data, now)
        glyph, desc, kind = weather.condition(est["code"], est["is_day"])
        box = Gtk.Box(spacing=12)
        box.get_style_context().add_class("hero")
        box.pack_start(label(glyph, "wicon", "big", kind, xalign=0.5),
                       False, False, 0)
        box.pack_start(label(f"{weather.deg(est['temp'])}°", "now-temp"),
                       False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        cond = label(desc, "cond")
        cond.set_line_wrap(True)
        text.pack_start(cond, False, False, 0)
        if est["forecast"]:
            sub = "Forecast for now"
        else:
            feels = weather.deg(self.data["forecast"]["current"]
                                ["apparent_temperature"])
            sub = f"Feels like {feels}°"
        text.pack_start(label(sub, "sub", ellipsize=Pango.EllipsizeMode.END),
                        False, False, 0)
        box.pack_start(text, True, True, 0)
        return box

    def _nowcast(self, cast, now):
        """Rain or snow in the next two hours, the bar tooltip's line as the
        headline over how heavy it gets and how much falls, and that the
        times are the radar's where they are, then a bar per step (the
        radar's 5 minutes, the model's quarter hours), on the 24-hour
        chart's terms: the past dimmed, a dashed line at the time."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class("nowcast")
        top = Gtk.Box(spacing=10)
        top.pack_start(label(ICON["umbrella"], "wicon", xalign=0.5),
                       False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        head = label(weather.nowcast_text(cast), "head")
        head.set_line_wrap(True)
        text.pack_start(head, False, False, 0)
        sub = self._amount(cast)
        if cast["radar"]:
            sub += " · DWD radar"
        text.pack_start(label(sub, "sub"), False, False, 0)
        top.pack_start(text, True, True, 0)
        box.pack_start(top, False, False, 0)
        chart = Gtk.DrawingArea()
        chart.set_size_request(-1, RAIN_H)
        chart.connect("draw", self._draw_rain, cast, now)
        box.pack_start(chart, False, False, 0)
        # The steps' starts at the chart's left edge, its middle and its
        # right edge.
        t0, span = cast["steps"][0][0], len(cast["steps"]) * cast["step"]
        ticks = Gtk.Box(homogeneous=True)
        for frac in (0.0, 0.5, 1.0):
            ticks.pack_start(label(f"{weather.local(t0 + frac * span):%H:%M}",
                                   "sub", xalign=frac), True, True, 0)
        box.pack_start(ticks, False, False, 0)
        return box

    @staticmethod
    def _amount(cast):
        """'Moderate · 2.0 mm', or for snow 'Light · 1.5 cm': the spell's
        heaviest step by the DWD's bounds, and all that falls in it; the
        amount alone where the headline says as much ("Light snow from
        23:00")."""
        peak = cast["peak"]
        word = ("Heavy" if peak >= weather.HEAVY else
                "Moderate" if peak >= weather.MODERATE else "Light")
        amount = (f"{cast['cm']:.1f} cm" if cast["cm"] > 0
                  else f"{cast['mm']:.1f} mm")
        if cast["desc"].split()[0] == word:
            return amount
        return f"{word} · {amount}"

    def _draw_rain(self, area, cr, cast, now):
        """A bar per step, its height the square root of the rate against
        heavy rain's 10 mm/h, so light rain shows too: 0.4 mm/h a fifth,
        moderate from the dotted line half way up. Snow in the text
        colour."""
        steps = cast["steps"]
        w, h = area.get_allocated_width(), area.get_allocated_height()
        slot = w / len(steps)
        # 4 px between the quarter hours' bars, 2 px between the radar's
        # three times as many.
        gap = 2 if cast["radar"] else 4
        rgba(cr, "surface2")
        cr.rectangle(0, h - 1, w, 1)
        cr.fill()
        mid = h - 1 - math.sqrt(weather.MODERATE / weather.HEAVY) * (h - 1)
        cr.set_line_width(1)
        cr.set_dash([2, 3])
        cr.move_to(0, round(mid) + 0.5)
        cr.line_to(w, round(mid) + 0.5)
        cr.stroke()
        cr.set_dash([])
        for i, (_, rate, cm) in enumerate(steps):
            if rate <= 0:
                continue
            bh = max(2, min(1, math.sqrt(rate / weather.HEAVY)) * (h - 1))
            rgba(cr, "text" if cm > 0 else "sapphire", 0.85)
            cr.rectangle(i * slot + gap / 2, h - 1 - bh, slot - gap, bh)
            cr.fill()
        # The time: the past dimmed, a dashed line.
        nx = (now - steps[0][0]) / (len(steps) * cast["step"]) * w
        rgba(cr, "surface", 0.55)
        cr.rectangle(0, 0, nx, h)
        cr.fill()
        rgba(cr, "accent", 0.8)
        cr.set_line_width(1)
        cr.set_dash([3, 3])
        cr.move_to(nx + 0.5, 0)
        cr.line_to(nx + 0.5, h)
        cr.stroke()
        return False

    def _start(self, now):
        """The start of the forecast's hour that now falls in: the hours
        start on the half hour in some time zones. The clock's hour where
        the forecast ends."""
        times = self.data["forecast"]["hourly"]["time"]
        i = weather.hour(times, now)
        return times[i] if i is not None else int(now) // 3600 * 3600

    def _hours(self, now):
        """{k: (temp, chance, code, is_day)} for the hours the chart draws,
        k counted from the start of this one, from 2 before to past the
        right edge, so the curve runs off the chart on both sides. Found by
        Unix time, so a DST day's repeated or skipped hour lands where it
        belongs."""
        hourly = self.data["forecast"]["hourly"]
        start = self._start(now)
        out = {}
        for t, temp, chance, code, is_day in zip(
                hourly["time"], hourly["temperature_2m"],
                hourly["precipitation_probability"], hourly["weather_code"],
                hourly["is_day"]):
            k = (t - start) // 3600
            if -2 <= k < COLS * STEP and None not in (temp, code):
                out[k] = (temp, chance or 0, code, is_day)
        return out

    def _columns(self, hours, now):
        """Per column the hour ('Now' first, the weekday where a new day
        begins), the glyph of its worst weather (weather.worst: the codes'
        numbers don't rise with it), the temperature at its hour and its
        highest chance of rain; the chart spans them all. Column c stands
        for hour STEP*c, drawn at its centre so each temperature sits on its
        curve point, and covers the hours drawn above it, STEP*c-1 to
        STEP*c+1: the "Now" column holds the dot that marks the time."""
        start = self._start(now)
        grid = Gtk.Grid(column_homogeneous=True)
        day = weather.local(start).date()
        for c in range(COLS):
            k = STEP * c
            if k not in hours:
                continue
            temp, _, _, is_day = hours[k]
            slot = [hours[j] for j in range(k - STEP // 2, k + STEP // 2 + 1)
                    if j >= 0 and j in hours]
            moment = weather.local(start + k * 3600)
            if c == 0:
                text = "Now"
            elif moment.date() != day:
                # Two letters, as over the calendar's days: "Sun" over a
                # weather glyph read as sunshine.
                text = weather.DAYS[moment.weekday()][:2]
            else:
                text = f"{moment:%H}"
            day = moment.date()
            hour = label(text, "hour", xalign=0.5)
            if c == 0:
                hour.get_style_context().add_class("now")
            glyph, _, kind = weather.condition(
                weather.worst(e[2] for e in slot), is_day)
            chance = max(e[1] for e in slot)
            chance_lab = label(f"{chance}%", "chance", xalign=0.5)
            if chance < 10:
                chance_lab.get_style_context().add_class("dry")
            grid.attach(hour, c, 0, 1, 1)
            grid.attach(label(glyph, "wicon", kind, xalign=0.5), c, 1, 1, 1)
            grid.attach(label(f"{weather.deg(temp)}°", "htemp", xalign=0.5),
                        c, 2, 1, 1)
            grid.attach(chance_lab, c, 4, 1, 1)
        chart = Gtk.DrawingArea()
        chart.set_size_request(-1, CHART_H)
        chart.connect("draw", self._draw_chart, hours, (now - start) / 3600)
        grid.attach(chart, 0, 3, COLS, 1)
        return grid

    def _draw_chart(self, area, cr, hours, now):
        """`now` in hours from the start of this one."""
        w, h = area.get_allocated_width(), area.get_allocated_height()

        def x(k):
            return (k + STEP / 2) / (COLS * STEP) * w

        # The chance of rain: one bar per hour from this one on, up to 40%
        # of the height for a certain 100%.
        bar_w = w / (COLS * STEP)
        rgba(cr, "sapphire", 0.35)
        for k, (_, chance, _, _) in hours.items():
            bh = chance / 100 * h * 0.4
            if k >= 0 and bh > 0:
                cr.rectangle(x(k) - bar_w / 2 + 1, h - bh, bar_w - 2, bh)
        cr.fill()

        # Temperature, coldest hour at the bottom of the band, warmest at
        # the top; flat across the middle when it holds still.
        keys = sorted(hours)
        temps = [hours[k][0] for k in keys]
        lo, hi = min(temps), max(temps)
        top, bottom = 6, h - 10

        def y(temp):
            if hi == lo:
                return (top + bottom) / 2
            return bottom - (temp - lo) / (hi - lo) * (bottom - top)

        # Held flat from the left edge when the forecast starts with this
        # hour; it ends where the readings do (an old cache's last day).
        pts = [(x(k), y(hours[k][0])) for k in keys]
        if pts[0][0] > 0:
            pts.insert(0, (0, pts[0][1]))
        cr.rectangle(0, 0, w, h)
        cr.clip()

        # Monotone cubic (Fritsch-Carlson) tangents: smooth through every
        # reading, but flat at a peak or trough, so it never overshoots
        # into a temperature the day doesn't reach.
        n = len(pts)
        slope = [(y1 - y0) / (x1 - x0) if x1 > x0 else 0.0
                 for (x0, y0), (x1, y1) in zip(pts, pts[1:])]
        tan = ([slope[0]] + [0.0 if a * b <= 0 else 2 / (1 / a + 1 / b)
                             for a, b in zip(slope, slope[1:])]
               + [slope[-1]]) if n > 1 else [0.0]

        def curve():
            cr.move_to(*pts[0])
            for i in range(n - 1):
                (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                d = (x1 - x0) / 3
                cr.curve_to(x0 + d, y0 + tan[i] * d,
                            x1 - d, y1 - tan[i + 1] * d, x1, y1)

        curve()
        cr.line_to(pts[-1][0], h)
        cr.line_to(pts[0][0], h)
        cr.close_path()
        fill = cairo.LinearGradient(0, top, 0, h)
        r, g, b = MOCHA["peach"]
        fill.add_color_stop_rgba(0, r, g, b, 0.30)
        fill.add_color_stop_rgba(1, r, g, b, 0.0)
        cr.set_source(fill)
        cr.fill()
        curve()
        rgba(cr, "peach")
        cr.set_line_width(2)
        cr.stroke()

        # Now: the past is dimmed, a dashed line and a dot mark the time,
        # inside the "Now" column.
        nx = x(now)
        rgba(cr, "surface", 0.55)
        cr.rectangle(0, 0, nx, h)
        cr.fill()
        rgba(cr, "accent", 0.8)
        cr.set_line_width(1)
        cr.set_dash([3, 3])
        cr.move_to(nx + 0.5, 0)
        cr.line_to(nx + 0.5, h)
        cr.stroke()
        cr.set_dash([])
        # Straight between this hour's reading and the next (the curve's
        # dot need not sit exactly on it).
        t0 = hours[0][0]
        t1 = hours.get(1, hours[0])[0]
        ny = y(t0 + (t1 - t0) * now)
        rgba(cr, "surface")
        cr.arc(nx, ny, 5, 0, 2 * math.pi)
        cr.fill()
        rgba(cr, "accent")
        cr.arc(nx, ny, 3.5, 0, 2 * math.pi)
        cr.fill()
        return False

    def _days(self, now):
        """Three days, a row each: the glyph of the day's worst weather, its
        high and low, and the chance of rain or snow with the amount
        expected. From today, or past today's sunset from tomorrow: what is
        left of today is the night, which the 24 hours above already hold,
        and its row showed the afternoon's rain."""
        forecast = self.data["forecast"]
        daily = forecast["daily"]
        today = weather.local(now).date()
        first = next((i for i, t in enumerate(daily["sunset"]) if t > now),
                     0)
        # Open-Meteo's daily code is the day's highest number, which isn't
        # its worst weather (weather.worst); the hours' codes tell.
        hourly = {}
        for t, code in zip(forecast["hourly"]["time"],
                           forecast["hourly"]["weather_code"]):
            if code is not None:
                hourly.setdefault(weather.local(t).date(), []).append(code)
        grid = Gtk.Grid(column_spacing=10, row_spacing=2)
        grid.get_style_context().add_class("days")
        row = 0
        for i, day in enumerate(weather.days(forecast)):
            code, hi, lo = (daily[key][i] for key in (
                "weather_code", "temperature_2m_max", "temperature_2m_min"))
            if i < first or day < today or None in (code, hi, lo):
                continue
            code = weather.worst([code] + hourly.get(day, []))
            glyph, _, kind = weather.condition(code)
            chance = daily["precipitation_probability_max"][i] or 0
            amount = daily["precipitation_sum"][i] or 0
            # A dry day (codes below 51) goes by its temperature, so a
            # freezing one doesn't read "Rain 5%".
            snow = code in SNOWFALL or (code < 51 and hi <= 0)
            text = f"{'Snow' if snow else 'Rain'} {chance}%"
            if amount >= 0.1:
                text += f" · {amount:.1f} mm"
            name = "Today" if day == today else weather.DAYS[day.weekday()]
            grid.attach(label(glyph, "wicon", kind, xalign=0.5), 0, row, 1, 1)
            grid.attach(label(name, "day"), 1, row, 1, 1)
            grid.attach(label(f"{weather.deg(hi)}°", "hi", xalign=1.0),
                        2, row, 1, 1)
            grid.attach(label(f"{weather.deg(lo)}°", "lo", xalign=1.0),
                        3, row, 1, 1)
            chance_lab = label(text, "chance",
                               ellipsize=Pango.EllipsizeMode.END)
            chance_lab.set_hexpand(True)
            if chance < 10:
                chance_lab.get_style_context().add_class("dry")
            grid.attach(chance_lab, 4, row, 1, 1)
            row += 1
            if row == 3:
                break
        return grid if row else None

    def _sun_day(self, now):
        """The day the sun times are for, today until the sun has set, then
        tomorrow: (its index in the daily forecast, its date, "" for today
        or else what says which day: "Tomorrow", further on the weekday),
        or None past the forecast's last sunset."""
        forecast = self.data["forecast"]
        i = next((i for i, t in enumerate(forecast["daily"]["sunset"])
                  if t > now), None)
        if i is None:
            return None
        date, today = weather.days(forecast)[i], weather.local(now).date()
        if date == today:
            return i, date, ""
        return i, date, ("Tomorrow" if (date - today).days == 1
                         else weather.DAYS[date.weekday()])

    def _facts(self, day):
        """Sunrise and sunset of `day` (_sun_day), which says which day once
        it isn't today, with that day's UV once it is worth a thought (3 and
        up); then the wind now, steady–gusts."""
        forecast = self.data["forecast"]
        daily, cur = forecast["daily"], forecast["current"]
        items = []
        if day is not None:
            i, _, ahead = day
            rise = f"{weather.local(daily['sunrise'][i]):%H:%M}"
            items += [(ICON["sunrise"], [label(f"{ahead} {rise}".strip(),
                                               "sub")]),
                      (ICON["sunset"], [label(
                          f"{weather.local(daily['sunset'][i]):%H:%M}",
                          "sub")])]
            uv = round(daily["uv_index_max"][i] or 0)
            if uv >= 3:
                items.append((ICON["uv"], [label(f"UV {uv}", "sub")]))
        speed = weather.deg(cur["wind_speed_10m"])
        gusts = weather.deg(cur["wind_gusts_10m"])
        items.append((ICON["wind"], [label(
            f"{speed}–{gusts} km/h" if gusts > speed else f"{speed} km/h",
            "sub")]))
        return self._row(items, "facts")

    def _air(self, now, day):
        """Pollen and the air, each only once worth a thought, in the
        colours of the warnings' levels: the DWD's pollen forecast for
        home's region on `day`, the sun times' (tomorrow's once the sun has
        set, which it says), its two strongest types from level 1 on
        ("Birch 2–3 · Grass 1"), all of them in the tooltip; the European
        air quality index from 40, moderate, on ("AQI 45"). A row under
        the facts', which has no room left; None while neither shows."""
        items = []
        found = weather.pollen(self.data, day[1]) if day is not None else []
        if found:
            ahead = day[2]
            words = [label(ahead, "sub")] if ahead else []
            for n, (kind, level) in enumerate(found[:2]):
                if n:
                    words.append(label("·", "sub"))
                words.append(label(f"{kind} {level.replace('-', '–')}",
                                   "sub", "level", POLLEN[level][1]))
            on = ("today" if not ahead else "tomorrow"
                  if ahead == "Tomorrow" else f"on {ahead}")
            tip = "\n".join([f"Pollen {on}, by the DWD"] + [
                f"{kind}: {POLLEN[level][0]}" for kind, level in found])
            items.append((ICON["pollen"], words, tip))
        index = weather.air(self.data, now)
        band = next((band for band in AIR
                     if index is not None and index >= band[0]), None)
        if band is not None:
            items.append((ICON["air"], [label(f"AQI {round(index)}", "sub",
                                              "level", band[2])],
                          f"European air quality index: {band[1]}"))
        return self._row(items, "facts", "air") if items else None

    @staticmethod
    def _row(items, *classes):
        """Items spread over a row, each a glyph and its labels, with a
        tooltip if one is given: (glyph, labels[, tooltip])."""
        box = Gtk.Box(spacing=6)
        for cls in classes:
            box.get_style_context().add_class(cls)
        for glyph, labels, *tip in items:
            item = Gtk.Box(spacing=5)
            item.pack_start(label(glyph, "wicon"), False, False, 0)
            for lab in labels:
                item.pack_start(lab, False, False, 0)
            if tip:
                item.set_tooltip_text(tip[0])
            box.pack_start(item, True, False, 0)
        return box
