"""The Calendar card, `bar-popup calendar`: the month in weeks from Monday
with their ISO week numbers, today in mauve, Sundays and Bavaria's public
holidays in red and a dot under every day with an event, the picked day,
then the next three events and the next two holidays. ‹ › and scrolling
turn the month; away from today's month a button turns back to it. A
click on a day picks it: the row under the grid names it in full, with
its holiday and how far off it is, and a click on that row copies the
day as 2026-10-16. From the keyboard the days are one stop for Tab, where
the arrow keys move the pick and the month follows it, Page Up and Page
Down (Home and End too) turn the month, T goes back to today and Enter
copies. A day's tooltip names its holiday and its events.

The events are the NAS's calendars, which vdirsyncer mirrors into VDIR
every 5 minutes. They are read once and kept while the card host runs,
so the card opens without reading them again; a sync that changed them
shows the next time the card opens, or within 30 s while it is open. A
machine without VDIR (the laptop) has no Events section and no dots,
and a file that can't be read is left out.
"""

import calendar
import collections
import datetime as dt
import glob
import os
import sys

import gi
import icalendar
import recurring_ical_events

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

# Next to this file; on PYTHONPATH when packaged.
from cardkit import Panel, button, copy_flash, hand, label  # noqa: E402

# Names of our own: GTK sets the C library's locale from the session, so
# strftime() would follow LC_TIME, which is German here ("Oktober").
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")

WEEKS = 6  # rows in every month, so turning months never resizes the card
EVENT_ROWS = 3  # rows under "Events", filled or not, for the same reason
HORIZON = 12    # months the Events rows look ahead
DAY = dt.timedelta(days=1)

# The calendars vdirsyncer mirrors from the NAS's CalDAV server (Radicale):
# <account>/<collection>/<uid>.ics, one event per file, together with any
# occurrences of it that were moved or changed.
VDIR = os.path.join(GLib.get_user_data_dir(), "calendars")

# Scrolling, in GDK's smooth-scroll units: a wheel turns a month per notch
# (one unit or a little more each), a touchpad or trackpoint a month per
# swipe. Their units follow the finger's travel, which differs by device
# and compositor, so a swipe counts once however far it goes. It ends when
# the fingers lift, or after SWIPE_GAP should the compositor not say so.
NOTCH = 1.0      # a notch, or hi-res wheel steps adding up to one
SWIPE = 2.0      # to tell a swipe from a finger settling
SWIPE_GAP = 300  # ms without scrolling

# The keys on the days: the arrows move the pick a day or a week either
# way, Page Up/Down a month; T picks today, Enter or Space copies, as
# Enter or Space works the control with the focus ring everywhere else.
# Home and End turn the month as well: hyprland.lua remaps the PgUp and
# PgDn keys to Home and End for every app, so that is what they send here.
# Today, Home's usual job in a calendar, moves to T, as in Google Calendar.
STEPS = {Gdk.KEY_Left: -1, Gdk.KEY_KP_Left: -1,
         Gdk.KEY_Right: 1, Gdk.KEY_KP_Right: 1,
         Gdk.KEY_Up: -7, Gdk.KEY_KP_Up: -7,
         Gdk.KEY_Down: 7, Gdk.KEY_KP_Down: 7}
PAGES = {Gdk.KEY_Page_Up: -1, Gdk.KEY_KP_Page_Up: -1,
         Gdk.KEY_Home: -1, Gdk.KEY_KP_Home: -1,
         Gdk.KEY_Page_Down: 1, Gdk.KEY_KP_Page_Down: 1,
         Gdk.KEY_End: 1, Gdk.KEY_KP_End: 1}
TODAY = (Gdk.KEY_t, Gdk.KEY_T)
COPY = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space, Gdk.KEY_KP_Space)

ICON = {
    "prev": "\U000f0141",   # md-chevron_left
    "next": "\U000f0142",   # md-chevron_right
    "today": "\U000f00f6",  # md-calendar_today
    "copy": "\U000f018f",   # md-content_copy
}

CSS = b"""
/* Calendar card: week numbers dim at the left, Saturdays a shade dimmer
   than weekdays, Sundays and holidays red, days of the months either side
   faint (@dim); today an @accent tile. Week numbers in subtext0, 5.65:1
   on the card: @dim's 4.45:1 is short of the 4.5:1 small text needs. */
.card.calendar .weekday { color: @subtext0; font-size: 12px; }
.card.calendar .week { color: @subtext0; font-size: 12px; }
.card.calendar .week.now { color: @accent; font-weight: bold; }
.card.calendar .day {
    color: @text;
    padding: 4px 0 3px;
    border-radius: 10px;
}
.card.calendar .day.sat { color: @subtext0; }
.card.calendar .day.off { color: @red; }
.card.calendar .day.other { color: @dim; }
.card.calendar .day.today {
    background-color: @accent;
    color: @crust;
    font-weight: bold;
}
/* A day with events: a dot under the number, @accent, faint on the
   months either side, @crust on today's tile. Every day has the dot's
   room, so the grid is as tall in every month. */
.card.calendar .dot {
    min-width: 4px;
    min-height: 4px;
    margin-top: 2px;
    border-radius: 2px;
}
.card.calendar .day .dot.on { background-color: @accent; }
.card.calendar .day.other .dot.on { background-color: @dim; }
.card.calendar .day.today .dot.on { background-color: @crust; }
.card.calendar .holidays,
.card.calendar .events { padding: 2px 10px 0; }
.card.calendar .holidays .date { color: @red; font-weight: bold; }
.card.calendar .events .date { color: @accent; font-weight: bold; }
/* "No events", dimmed: subtext0, as @dim is short of 4.5:1 (above). */
.card.calendar .events .date.none { color: @subtext0; font-weight: normal; }
/* The picked day, which the arrow keys move as well: an @accent ring, in
   base on today's @accent tile (which shows it only while the grid has
   the keyboard: unringed, the tile itself says it). */
.card.calendar .day.cursor { box-shadow: inset 0 0 0 2px @accent; }
.card.calendar .day.today.cursor { box-shadow: inset 0 0 0 2px @base; }
/* The picked day's row: the copy glyph dim like the Network card's
   chevron, lighter on hover; a copy shows green for a moment, as there,
   hovered too (the row is under the pointer right after the click). */
.card.calendar .pick { padding: 4px 10px; }
.card.calendar .pick .name { font-weight: bold; }
.card.calendar .copyglyph { color: @subtext0; font-size: 16px; }
.card.calendar .row:hover .copyglyph { color: @subtext1; }
.card.calendar .sub.copied,
.card.calendar .row:hover .sub.copied { color: @ok; }
"""


def easter(year):
    """Easter Sunday of a Gregorian year: the anonymous algorithm (Meeus,
    Astronomical Algorithms, ch. 8), exact for every year from 1583."""
    golden = year % 19
    century, rest = divmod(year, 100)
    leap_c, mod_c = divmod(century, 4)
    lunar = (century - (century + 8) // 25 + 1) // 3
    epact = (19 * golden + century - leap_c - lunar + 15) % 30
    leap_y, mod_y = divmod(rest, 4)
    weekday = (32 + 2 * mod_c + 2 * leap_y - epact - mod_y) % 7
    shift = (golden + 11 * epact + 22 * weekday) // 451
    month, day = divmod(epact + weekday - 7 * shift + 114, 31)
    return dt.date(year, month, day + 1)


def holidays(year):
    """{date: name} of Bavaria's public holidays in `year`. Assumption Day
    is one only where most people are Catholic, which is true here."""
    easter_day = easter(year)

    def after_easter(days):
        return easter_day + dt.timedelta(days=days)

    return {
        dt.date(year, 1, 1): "New Year's Day",
        dt.date(year, 1, 6): "Epiphany",
        after_easter(-2): "Good Friday",
        after_easter(1): "Easter Monday",
        dt.date(year, 5, 1): "Labour Day",
        after_easter(39): "Ascension Day",
        after_easter(50): "Whit Monday",
        after_easter(60): "Corpus Christi",
        dt.date(year, 8, 15): "Assumption Day",
        dt.date(year, 10, 3): "German Unity Day",
        dt.date(year, 11, 1): "All Saints' Day",
        dt.date(year, 12, 25): "Christmas Day",
        dt.date(year, 12, 26): "St Stephen's Day",
    }


def add_months(day, months):
    """`day` that many months on (back, if < 0): the same date, or the
    month's last day where it is shorter."""
    years, month = divmod(day.month - 1 + months, 12)
    year, month = day.year + years, month + 1
    return dt.date(year, month,
                   min(day.day, calendar.monthrange(year, month)[1]))


def ahead(days):
    """How far a day `days` after today is: "today", "in 3 days", "2 days
    ago"."""
    if -1 <= days <= 1:
        return ("yesterday", "today", "tomorrow")[days + 1]
    return f"in {days} days" if days > 0 else f"{-days} days ago"


def short(day):
    """A day as its rows show it: "Mon 24 Nov"."""
    return f"{DAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1][:3]}"


def clock(moment):
    """A time of day as 18:00, whatever LC_TIME says."""
    return f"{moment.hour:02d}:{moment.minute:02d}"


# One occurrence of an event: the days it covers (`last` included), its
# start and end in local time (None all day), title and location (or None).
Event = collections.namedtuple(
    "Event", "first last begin end title location path")

# The calendars as last read, kept while the card host runs: the VDIR
# signature they were read at, (path, query) for each file, and the
# occurrences of each year as far as it has been looked at.
_store = {"signature": None, "calendars": [], "years": {}}


def vdir_signature():
    """The mtimes of VDIR and of its account and collection directories.
    vdirsyncer writes each file under another name and renames it into
    place, so every event added, changed or deleted changes its
    directory's mtime: a few stat()s tell a sync that changed something,
    without reading a file."""
    dirs = []
    try:
        root = os.stat(VDIR).st_mtime_ns
        for account in os.scandir(VDIR):
            if account.name.startswith(".") or not account.is_dir():
                continue
            dirs.append((account.name, "", account.stat().st_mtime_ns))
            for coll in os.scandir(account.path):
                if not coll.name.startswith(".") and coll.is_dir():
                    dirs.append((account.name, coll.name,
                                 coll.stat().st_mtime_ns))
    except OSError:  # no VDIR here, or a directory gone mid-sync
        root = None
    return root, tuple(sorted(dirs))


def skip(path, err):
    print(f"calendar: skipping {path}: {err}", file=sys.stderr)


def reload_calendars():
    """Read VDIR again if a sync changed it since; True if it did. A file
    that can't be read is left out, so one bad event never costs the rest
    (any exception: icalendar's and recurring_ical_events' differ)."""
    signature = vdir_signature()
    if signature == _store["signature"]:
        return False
    calendars = []
    pattern = os.path.join(glob.escape(VDIR), "*", "*", "*.ics")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "rb") as fh:
                ical = icalendar.Calendar.from_ical(fh.read())
            calendars.append((path, recurring_ical_events.of(
                ical, skip_bad_series=True)))
        except Exception as err:  # noqa: BLE001
            skip(path, err)
    _store.update(signature=signature, calendars=calendars, years={})
    return True


def local(moment):
    """A datetime in local time, as the bar shows the clock: the zone the
    system is in, none fixed here. One without a zone (a "floating" time)
    is local already."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone().replace(tzinfo=None)


def occurrence(event, path):
    """An Event of one expanded VEVENT; None if it was cancelled."""
    if str(event.get("STATUS", "")).upper() == "CANCELLED":
        return None
    title = " ".join(str(event.get("SUMMARY", "")).split()) or "(No title)"
    location = str(event.get("LOCATION", "")).strip() or None
    start, end = event.start, event.end
    if isinstance(start, dt.datetime):
        begin = local(start)
        end = max(begin, local(end))
        # Ending at midnight, it ends with the day before.
        last = (end - dt.timedelta(microseconds=1)).date()
        return Event(begin.date(), max(begin.date(), last), begin, end,
                     title, location, path)
    # All day: DTEND is the day after the last (icalendar makes it the
    # day after DTSTART when the event has none).
    return Event(start, max(start, end - DAY), None, None,
                 title, location, path)


def year_events(year):
    """The occurrences that touch `year`, expanded once per read of VDIR.
    The day either side covers zones apart from ours."""
    years = _store["years"]
    if year not in years:
        lo, hi = dt.date(year, 1, 1), dt.date(year + 1, 1, 1)
        found = set()
        for path, query in _store["calendars"]:
            try:
                for vevent in query.between(lo - DAY, hi + DAY):
                    event = occurrence(vevent, path)
                    if event and event.first < hi and event.last >= lo:
                        found.add(event)
            except Exception as err:  # noqa: BLE001
                skip(path, err)
        years[year] = found
    return years[year]


def order(event):
    """Sort key: by start, an all-day event before the day's timed ones."""
    return (event.begin or dt.datetime.combine(event.first, dt.time()),
            event.title)


def events_between(lo, hi):
    """The events on the days from `lo` to before `hi`, by start. One that
    spans New Year is in both years' sets, once in the union."""
    found = set()
    for year in range(lo.year, (hi - DAY).year + 1):
        found.update(event for event in year_events(year)
                     if event.first < hi and event.last >= lo)
    return sorted(found, key=order)


def describe(event):
    """An event's tooltip: its title, when, and where if it says."""
    if event.begin is None:
        when = ("All day" if event.first == event.last
                else f"{short(event.first)} – {short(event.last)}")
    elif event.first != event.last:
        when = (f"{short(event.first)} {clock(event.begin)} – "
                f"{short(event.end.date())} {clock(event.end)}")
    elif event.end > event.begin:
        when = f"{clock(event.begin)}–{clock(event.end)}"
    else:
        when = clock(event.begin)
    return "\n".join(filter(None, (event.title, when, event.location)))


def mark(widget, cls, on):
    """Add or remove one style class."""
    ctx = widget.get_style_context()
    if on:
        ctx.add_class(cls)
    else:
        ctx.remove_class(cls)


class CalendarPanel(Panel):
    """The month in the title row and as a grid, a dot under each day with
    an event, the picked day, then the next events and the next public
    holidays. The events listed start from the picked day while it is in
    the month shown, else from today, or from the 1st of a month still to
    come (as the holidays do); one under way counts as on that day. The
    grid is built once with six weeks and the lists with their rows;
    turning the month or picking a day only relabels them, so nothing
    moves or flickers, and the card is as tall in every month."""

    css = "calendar"

    def __init__(self, app):
        super().__init__(app)
        self.today = dt.date.today()
        self.month = self.today.replace(day=1)
        self.picked = self.today  # the day the row under the grid names
        self._named = None        # the day that row names; see _show_cursor
        self._listed = None       # the day the Events rows start from
        self.scrolled = 0.0    # smooth scrolling not yet turned into months
        self.swiped = False    # this swipe has turned its month
        self.scroll_time = 0   # ms, of the last scroll event
        # The title row's label, which Panel packs first: it names the month.
        self.heading = self.header.get_children()[0]
        for glyph, tip, months in ((ICON["next"], "Next month", 1),
                                   (ICON["prev"], "Previous month", -1)):
            btn = button(glyph, "iconbtn")
            btn.set_tooltip_text(tip)
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda _btn, n=months: self.turn(n))
            self.header.pack_end(btn, False, False, 0)
        # Left of ‹ ›, so they stay put when it comes and goes.
        self.back = button(ICON["today"], "iconbtn")
        self.back.set_tooltip_text("Back to today")
        self.back.set_valign(Gtk.Align.CENTER)
        # Only _relabel shows or hides it: the window's show_all() passes
        # it over, and its glyph with it, so that is shown here.
        self.back.show_all()
        self.back.set_no_show_all(True)
        self.back.connect("clicked", lambda _btn: self.pick(self.today))
        self.header.pack_end(self.back, False, False, 0)
        self.connect("parent-set", self._catch_scroll)
        reload_calendars()
        self.fill()
        # Past midnight, today moves on, and a sync may have changed the
        # events. A check every 30 s rather than one timer for midnight: a
        # timer's clock stands still in suspend.
        self._tick_id = GLib.timeout_add_seconds(30, self._tick)
        self.connect("destroy", lambda *_: GLib.source_remove(self._tick_id))

    def fill(self):
        grid = Gtk.Grid(column_homogeneous=True, row_spacing=2)
        for col, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            grid.attach(label(name, "weekday", xalign=0.5), col + 1, 0, 1, 1)
        self.weeks, self.cells, self.numbers, self.dots = [], [], [], []
        for row in range(1, WEEKS + 1):
            week = label("", "week", xalign=0.5)
            grid.attach(week, 0, row, 1, 1)
            self.weeks.append(week)
            for col in range(1, 8):
                # The number, and under it the dot that marks a day with
                # events, in a box that takes the day's classes, so that
                # today's tile and the ring go round both.
                number = label("", xalign=0.5)
                dot = Gtk.Box(halign=Gtk.Align.CENTER)
                dot.get_style_context().add_class("dot")
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                cell.get_style_context().add_class("day")
                cell.pack_start(number, False, False, 0)
                cell.pack_start(dot, False, False, 0)
                # The boxes have no window to take a click; an input-only
                # box round them does.
                box = hand(Gtk.EventBox(visible_window=False))
                box.add(cell)
                box.connect("button-press-event", self._on_day,
                            len(self.cells))
                grid.attach(box, col, row, 1, 1)
                self.cells.append(cell)
                self.numbers.append(number)
                self.dots.append(dot)
        # One stop for Tab, then the arrow keys move the pick.
        grid.set_can_focus(True)
        grid.connect("key-press-event", self._on_key)
        grid.connect("notify::has-focus", lambda *_: self._show_cursor())
        self.grid = grid
        self.body.pack_start(grid, False, False, 0)
        self.body.pack_start(self._pick_row(), False, False, 0)

        # Only where vdirsyncer mirrors the calendars: the laptop has no
        # Events section, rather than one that never lists anything.
        self.agenda = []
        if os.path.isdir(VDIR):
            self.body.pack_start(label("Events", "seclabel"),
                                 False, False, 0)
            self.agenda = self._list("events", EVENT_ROWS)
        self.body.pack_start(label("Public holidays", "seclabel"),
                             False, False, 0)
        self.upcoming = self._list("holidays", 2)
        self._relabel()

    def _list(self, cls, rows):
        """Rows of day, title and how far off, packed into the body; gives
        the three labels of each row."""
        grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        grid.get_style_context().add_class(cls)
        labels = []
        for row in range(rows):
            date = label("", "date")
            name = label("", ellipsize=Pango.EllipsizeMode.END)
            name.set_hexpand(True)
            when = label("", "sub", xalign=1.0)
            for col, widget in enumerate((date, name, when)):
                grid.attach(widget, col, row, 1, 1)
            labels.append((date, name, when))
        self.body.pack_start(grid, False, False, 0)
        return labels

    def _pick_row(self):
        """The row under the grid: the picked day in full, then its holiday
        and how far off it is; a click copies the day."""
        end = Pango.EllipsizeMode.END
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_valign(Gtk.Align.CENTER)
        self.pick_name = label("", "name", ellipsize=end)
        self.pick_sub = label("", "sub", ellipsize=end)
        text.pack_start(self.pick_name, False, False, 0)
        text.pack_start(self.pick_sub, False, False, 0)
        box = Gtk.Box(spacing=12)
        box.pack_start(text, True, True, 0)
        glyph = label(ICON["copy"], "copyglyph", xalign=0.5)
        glyph.set_valign(Gtk.Align.CENTER)
        box.pack_start(glyph, False, False, 0)
        self.pick_row = button(box, "row", "pick")
        self.pick_row.connect("clicked", lambda _btn: self._copy())
        return self.pick_row

    def turn(self, months):
        """Show the month `months` after the one shown (before, if < 0)."""
        years, month = divmod(self.month.month - 1 + months, 12)
        self.turn_to(dt.date(self.month.year + years, month + 1, 1))

    def turn_to(self, day):
        """Show the month `day` falls in."""
        self.month = day.replace(day=1)
        self._relabel()

    def pick(self, day):
        """Pick `day`, turning to its month if another is shown."""
        self.picked = day
        if day.replace(day=1) == self.month:
            self._show_cursor()
        else:
            self.turn_to(day)

    def _relabel(self):
        self.heading.set_text(f"{MONTHS[self.month.month - 1]} "
                              f"{self.month.year}")
        self.back.set_visible(self.month != self.today.replace(day=1))
        off = holidays(self.month.year)
        first = self.month - dt.timedelta(days=self.month.weekday())
        end = first + 7 * WEEKS * DAY
        on = collections.defaultdict(list)  # day: the lines of its events
        for event in events_between(first, end):
            day = max(event.first, first)
            while day <= event.last and day < end:
                # The time on the day it starts; the days after, the title.
                timed = event.begin is not None and day == event.first
                on[day].append(f"{clock(event.begin)} {event.title}"
                               if timed else event.title)
                day += DAY
        for row, week in enumerate(self.weeks):
            monday = first + dt.timedelta(weeks=row)
            week.set_text(str(monday.isocalendar()[1]))
            mark(week, "now", 0 <= (self.today - monday).days < 7)
            for col in range(7):
                day = monday + dt.timedelta(days=col)
                i = 7 * row + col
                cell = self.cells[i]
                self.numbers[i].set_text(str(day.day))
                mark(self.dots[i], "on", day in on)
                ours = day.month == self.month.month
                mark(cell, "other", not ours)
                mark(cell, "today", day == self.today)
                mark(cell, "sat", ours and col == 5)
                mark(cell, "off", ours and (col == 6 or day in off))
                lines = [off[day]] if ours and day in off else []
                cell.set_tooltip_text("\n".join(lines + on.get(day, []))
                                      or None)
        self._show_cursor()

        # From today on, or from the month shown if that is still to come.
        start = max(self.today, self.month)
        nxt = sorted((day, title) for year in (start.year, start.year + 1)
                     for day, title in holidays(year).items() if day >= start)
        for (date, name, when), (day, title) in zip(self.upcoming, nxt):
            date.set_text(short(day))
            name.set_text(title)
            when.set_text(ahead((day - self.today).days))

    def _list_events(self):
        """Fill the Events rows from the picked day, while it is in the
        month shown, else as the holidays: from today, or from the 1st of
        a month still to come. An event under way counts as on that day,
        and so goes first."""
        if self.picked.replace(day=1) == self.month:
            start = self.picked
        else:
            start = max(self.today, self.month)
        if start == self._listed:
            return
        self._listed = start
        coming = sorted(events_between(start, add_months(start, HORIZON)),
                        key=lambda event: (max(event.first, start),
                                           order(event)))
        for i, (date, name, when) in enumerate(self.agenda):
            event = coming[i] if i < len(coming) else None
            # "No events" in the date column, so it starts where dates do.
            mark(date, "none", event is None)
            if event is None:
                for widget in (date, name, when):
                    widget.set_text("")
                    widget.set_tooltip_text(None)
                if not coming and i == 0:
                    date.set_text("No events")
                continue
            day = max(event.first, start)
            date.set_text(short(day))
            name.set_text(f"{clock(event.begin)} {event.title}"
                          if event.begin and event.first == day
                          else event.title)
            when.set_text(ahead((day - self.today).days))
            for widget in (date, name, when):
                widget.set_tooltip_text(describe(event))

    def _on_day(self, _box, event, index):
        """A click picks the day; one of the months either side turns to
        that month."""
        if (event.type != Gdk.EventType.BUTTON_PRESS
                or event.button != Gdk.BUTTON_PRIMARY):
            return False
        first = self.month - dt.timedelta(days=self.month.weekday())
        self.pick(first + dt.timedelta(days=index))
        return True

    def _on_key(self, _grid, event):
        """See STEPS: past the first or the last of the month, the month
        turns with the pick."""
        key = event.keyval
        if key in COPY:
            self._copy()
        elif key in TODAY:
            self.pick(self.today)
        elif key in PAGES:
            self.pick(add_months(self.picked, PAGES[key]))
        elif key in STEPS:
            self.pick(self.picked + dt.timedelta(days=STEPS[key]))
        else:
            return False
        return True

    def _show_cursor(self):
        """Ring the picked day; today only while the grid shows the
        keyboard focus, as its tile names it already. Should the grid get
        the focus while the pick is in another month, the pick moves to
        today, or to the 1st away from today's month."""
        shown = self.grid.has_visible_focus()
        if shown and self.picked.replace(day=1) != self.month:
            this = self.today.replace(day=1) == self.month
            self.picked = self.today if this else self.month
        first = self.month - dt.timedelta(days=self.month.weekday())
        for i, cell in enumerate(self.cells):
            day = first + dt.timedelta(days=i)
            mark(cell, "cursor",
                 day == self.picked and (shown or day != self.today))
        # Only a new pick refills the row: the focus coming or going must
        # not cut a "Copied" short.
        if self.picked != self._named:
            self._name_pick()
        self._list_events()

    def _name_pick(self):
        """Fill the picked day's row. A "Copied" still showing ends: the
        new text stays (copy_flash), and so it loses its green here."""
        mark(self.pick_sub, "copied", False)
        day = self._named = self.picked
        self.pick_name.set_text(f"{WEEKDAYS[day.weekday()]}, {day.day} "
                                f"{MONTHS[day.month - 1]} {day.year}")
        sub = " · ".join(filter(None, (holidays(day.year).get(day),
                                       ahead((day - self.today).days))))
        self.pick_sub.set_text(sub[0].upper() + sub[1:])
        self.pick_row.set_tooltip_text(f"Copy {day.isoformat()}")

    def _copy(self):
        """The picked day to the clipboard as 2026-10-16: ISO 8601 sorts,
        and file names and forms take it. The row says so for a moment."""
        iso = self.picked.isoformat()
        copy_flash(self.pick_sub, iso, f"Copied {iso}")

    def _catch_scroll(self, _card, _old):
        """The card's boxes have no window to receive a scroll, so it goes
        to the first one above them: bar-popup's EventBox round the card.
        Ask that for scroll events, the smooth ones too: a touchpad sends
        no other kind."""
        parent = self.get_parent()
        if parent is not None:
            parent.add_events(Gdk.EventMask.SCROLL_MASK
                              | Gdk.EventMask.SMOOTH_SCROLL_MASK)
            parent.connect("scroll-event", self._on_scroll)

    def _on_scroll(self, _widget, event):
        """Down (with natural scrolling: two fingers up) is the next month,
        up the one before."""
        if self.dead:
            return False
        smooth, _dx, dy = event.get_scroll_deltas()
        if not smooth:
            _, direction = event.get_scroll_direction()
            if direction not in (Gdk.ScrollDirection.UP,
                                 Gdk.ScrollDirection.DOWN):
                return False
            self.turn(1 if direction == Gdk.ScrollDirection.DOWN else -1)
            return True
        device = event.get_source_device()
        wheel = (device is not None
                 and device.get_source() == Gdk.InputSource.MOUSE)
        now = event.get_time()
        if (event.is_scroll_stop_event()
                or abs(now - self.scroll_time) > SWIPE_GAP):
            self.scrolled, self.swiped = 0.0, False
        self.scroll_time = now
        if self.swiped:
            return True
        self.scrolled += dy
        if abs(self.scrolled) >= (NOTCH if wheel else SWIPE):
            self.turn(1 if self.scrolled > 0 else -1)
            # The rest of a notch is dropped; a swipe waits for its end.
            self.scrolled, self.swiped = 0.0, not wheel
        return True

    def _tick(self):
        today = dt.date.today()
        # Also a sync's new events, read if the signature says so.
        changed = reload_calendars()
        if today != self.today:
            # Today's month follows today; another month stays on screen,
            # and a pick of another day stays where it is.
            if self.month == self.today.replace(day=1):
                self.month = today.replace(day=1)
            if self.picked == self.today:
                self.picked = today
            self.today = today
            self._named = None  # "in 3 days" counts from the new today
            changed = True
        if changed:
            self._listed = None  # the Events rows too
            self._relabel()
        return GLib.SOURCE_CONTINUE
