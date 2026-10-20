"""The Calendar card, `bar-popup calendar`: the month in weeks from Monday
with their ISO week numbers, today in mauve, Sundays and Bavaria's public
holidays in red, the picked day, then the next two holidays. ‹ › and
scrolling turn the month; away from today's month a button turns back to
it. A click on a day picks it: the row under the grid names it in full,
with its holiday and how far off it is, and a click on that row copies
the day as 2026-10-16. From the keyboard the days are one stop for Tab,
where the arrow keys move the pick and the month follows it, Page Up and
Page Down (Home and End too) turn the month, T goes back to today and
Enter copies.
"""

import calendar
import datetime as dt

import gi

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
.card.calendar .day { color: @text; padding: 5px 0; border-radius: 10px; }
.card.calendar .day.sat { color: @subtext0; }
.card.calendar .day.off { color: @red; }
.card.calendar .day.other { color: @dim; }
.card.calendar .day.today {
    background-color: @accent;
    color: @crust;
    font-weight: bold;
}
.card.calendar .holidays { padding: 2px 10px 0; }
.card.calendar .holidays .date { color: @red; font-weight: bold; }
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


def mark(widget, cls, on):
    """Add or remove one style class."""
    ctx = widget.get_style_context()
    if on:
        ctx.add_class(cls)
    else:
        ctx.remove_class(cls)


class CalendarPanel(Panel):
    """The month in the title row and as a grid, the picked day, then the
    next public holidays. The grid is built once with six weeks; turning
    the month only relabels it, so nothing moves or flickers."""

    css = "calendar"

    def __init__(self, app):
        super().__init__(app)
        self.today = dt.date.today()
        self.month = self.today.replace(day=1)
        self.picked = self.today  # the day the row under the grid names
        self._named = None        # the day that row names; see _show_cursor
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
        self.fill()
        # Past midnight, today moves on. A check every 30 s rather than one
        # timer for midnight: a timer's clock stands still in suspend.
        self._tick_id = GLib.timeout_add_seconds(30, self._tick)
        self.connect("destroy", lambda *_: GLib.source_remove(self._tick_id))

    def fill(self):
        grid = Gtk.Grid(column_homogeneous=True, row_spacing=2)
        for col, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            grid.attach(label(name, "weekday", xalign=0.5), col + 1, 0, 1, 1)
        self.weeks, self.cells = [], []
        for row in range(1, WEEKS + 1):
            week = label("", "week", xalign=0.5)
            grid.attach(week, 0, row, 1, 1)
            self.weeks.append(week)
            for col in range(1, 8):
                cell = label("", "day", xalign=0.5)
                # A label has no window to take a click; an input-only box
                # round it does.
                box = hand(Gtk.EventBox(visible_window=False))
                box.add(cell)
                box.connect("button-press-event", self._on_day,
                            len(self.cells))
                grid.attach(box, col, row, 1, 1)
                self.cells.append(cell)
        # One stop for Tab, then the arrow keys move the pick.
        grid.set_can_focus(True)
        grid.connect("key-press-event", self._on_key)
        grid.connect("notify::has-focus", lambda *_: self._show_cursor())
        self.grid = grid
        self.body.pack_start(grid, False, False, 0)
        self.body.pack_start(self._pick_row(), False, False, 0)

        self.body.pack_start(label("Public holidays", "seclabel"),
                             False, False, 0)
        upcoming = Gtk.Grid(column_spacing=12, row_spacing=4)
        upcoming.get_style_context().add_class("holidays")
        self.upcoming = []
        for row in range(2):
            date = label("", "date")
            name = label("", ellipsize=Pango.EllipsizeMode.END)
            name.set_hexpand(True)
            when = label("", "sub", xalign=1.0)
            for col, widget in enumerate((date, name, when)):
                upcoming.attach(widget, col, row, 1, 1)
            self.upcoming.append((date, name, when))
        self.body.pack_start(upcoming, False, False, 0)
        self._relabel()

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
        for row, week in enumerate(self.weeks):
            monday = first + dt.timedelta(weeks=row)
            week.set_text(str(monday.isocalendar()[1]))
            mark(week, "now", 0 <= (self.today - monday).days < 7)
            for col in range(7):
                day = monday + dt.timedelta(days=col)
                cell = self.cells[7 * row + col]
                cell.set_text(str(day.day))
                ours = day.month == self.month.month
                mark(cell, "other", not ours)
                mark(cell, "today", day == self.today)
                mark(cell, "sat", ours and col == 5)
                mark(cell, "off", ours and (col == 6 or day in off))
                cell.set_tooltip_text(off.get(day) if ours else None)
        self._show_cursor()

        # From today on, or from the month shown if that is still to come.
        start = max(self.today, self.month)
        nxt = sorted((day, title) for year in (start.year, start.year + 1)
                     for day, title in holidays(year).items() if day >= start)
        for (date, name, when), (day, title) in zip(self.upcoming, nxt):
            date.set_text(f"{DAYS[day.weekday()]} {day.day} "
                          f"{MONTHS[day.month - 1][:3]}")
            name.set_text(title)
            when.set_text(ahead((day - self.today).days))

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
        if today != self.today:
            # Today's month follows today; another month stays on screen,
            # and a pick of another day stays where it is.
            if self.month == self.today.replace(day=1):
                self.month = today.replace(day=1)
            if self.picked == self.today:
                self.picked = today
            self.today = today
            self._named = None  # "in 3 days" counts from the new today
            self._relabel()
        return GLib.SOURCE_CONTINUE
