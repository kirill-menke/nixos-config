"""Quick notes modal (SUPER+N).

A two-pane Textual app in a floating kitty window, with a Work and a Personal
tab (Ctrl+PgDn/PgUp or a click switches): on the left every note of the tab,
under its headings; on the right a text box for a new note. Enter saves it to
the shown tab, one bullet per line under today's date (Shift+Enter starts
another line). Each tab is a plain Markdown file in ~/Documents/notes
(work.md, notes.md), so notes can be read and edited anywhere:

    ## 2026-10-11

    - look up how ZFS special vdevs work
      - and whether they need a mirror

Any other heading (`# Open items`, `## Health`) is shown as a section title;
deleting a section's last note drops its heading, as for a day.

Text typed but not saved yet is kept as a draft and comes back the next time
the app starts.

With --resident (as notes.sh starts it) the app keeps running between uses,
as a fresh kitty + Textual start takes ~0.7 s: Hyprland's window rule parks
the window on the hidden workspace "special:notes", Esc moves it back there,
and SIGUSR1 (SUPER+N) brings it to the current workspace. It shows itself
once it has drawn its first frame, so no blank terminal is ever seen, unless
--hidden (the login prewarm) keeps it parked.
"""

import asyncio
import os
import re
import signal
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.theme import Theme
from textual.widgets import Footer, OptionList, Tab, Tabs, TextArea
from textual.widgets.option_list import Option

# SIGUSR1 would kill the app until on_mount installs the real handler, so a
# SUPER+N pressed while it is still starting is only noted.
early_show = []
signal.signal(signal.SIGUSR1, lambda *_: early_show.append(True))

NOTES_DIR = Path(
    os.environ.get("NOTES_DIR", Path.home() / "Documents" / "notes")
)
# (tab id, label, file in NOTES_DIR, placeholder of the entry box)
TABS = (
    ("work", "Work", "work.md", "Something for work…"),
    ("personal", "Personal", "notes.md", "Something to remember…"),
)
DRAFT = Path(
    os.environ.get("NOTES_DRAFT")
    or Path(os.environ.get("XDG_STATE_HOME")
            or Path.home() / ".local" / "state") / "notes-modal" / "draft.txt"
)

WINDOW = 'window = "class:^(notes-modal)$"'
SHOW = (
    "hl.dispatch(hl.dsp.window.move({ workspace ="
    f" hl.get_active_workspace().id, {WINDOW}, follow = true }})) "
    f"hl.dispatch(hl.dsp.window.center({{ {WINDOW} }})) "
    f"hl.dispatch(hl.dsp.focus({{ {WINDOW} }}))"
)
HIDE = ("hl.dispatch(hl.dsp.window.move({ workspace = \"special:notes\","
        f" {WINDOW}, follow = false }}))")

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
BULLET = re.compile(r"^(\s*)[-*+•]\s+(.*)$")

MAUVE = "#cba6f7"

# Screen's own hidden Tab binding outranks one on the App, so the hint for
# the footer goes on each pane.
SWITCH = Binding("tab", "app.focus_next", "Switch", key_display="⇥")

# Catppuccin Mocha with the mauve accent of the SUPER+T dictionary modal,
# on kitty's own background so the window padding blends in.
MOCHA = Theme(
    name="notes-mocha",
    primary=MAUVE,
    secondary="#b4befe",
    accent=MAUVE,
    foreground="#cdd6f4",
    background="#1e1e2e",
    surface="#1e1e2e",
    panel="#313244",
    warning="#f9e2af",
    error="#f38ba8",
    success="#a6e3a1",
    dark=True,
    variables={
        "border": MAUVE,
        "border-blurred": "#45475a",
        "block-cursor-background": "#313244",
        "block-cursor-foreground": "#cdd6f4",
        "block-cursor-text-style": "none",
        "block-cursor-blurred-background": "#313244 50%",
        "input-cursor-background": "#f5e0dc",
        "input-cursor-foreground": "#1e1e2e",
        "footer-background": "#1e1e2e",
        "footer-key-foreground": MAUVE,
    },
)


# --- the Markdown files ----------------------------------------------------

class Notebook:
    """One tab's file, its lines as last read or written, the mtime they
    belong to, and the undo history."""

    def __init__(self, path):
        self.path = path
        self.reload()

    def read(self):
        try:
            return self.path.read_text().splitlines()
        except FileNotFoundError:
            return []

    def stat(self):
        try:
            return self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def reload(self):
        self.lines, self.mtime, self.history = self.read(), self.stat(), []

    def stale(self):
        return self.stat() != self.mtime

    def save(self, lines):
        """Resolved first, so a symlinked notes file stays a symlink."""
        self.lines = lines
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        write_atomic(self.path.resolve(),
                     "".join(f"{line}\n" for line in lines))
        self.mtime = self.stat()


def write_atomic(path, text):
    """Write via a temporary file, so closing the window mid-save never
    truncates `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def load_draft():
    try:
        return DRAFT.read_text()
    except FileNotFoundError:
        return ""


def save_draft(text):
    """Called on every edit, not at exit: SUPER+Q ends kitty with a SIGHUP
    that leaves the app no time to save."""
    if text.strip():
        write_atomic(DRAFT, text)
    else:
        DRAFT.unlink(missing_ok=True)


def hyprland(lua):
    subprocess.run(["hyprctl", "eval", lua], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def append(lines, entry):
    """Return `lines` plus each line of `entry` as a bullet under today's
    heading. Indentation nests bullets; a typed "- " marker is dropped."""
    bullets = []
    for raw in textwrap.dedent(entry.expandtabs(2)).splitlines():
        m = re.match(r"^(\s*)(?:[-*+•]\s+)?(.*?)\s*$", raw)
        if m.group(2):
            bullets.append(f"{m.group(1)}- {m.group(2)}")
    if not bullets:
        return lines
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    # Today's heading is reused only if it is the last one, so a note never
    # lands in a section (`## Health`) that follows it.
    heads = [line for line in out if HEADING.match(line)]
    if not heads or heads[-1] != f"## {date.today().isoformat()}":
        out += ([""] if out else []) + [f"## {date.today().isoformat()}", ""]
    return out + bullets


def remove(lines, i):
    """Return `lines` without line `i`, and without its heading if that was
    the section's last note. A section runs to the next heading of the same
    or a higher level, so a heading with subsections is kept."""
    out = lines[:i] + lines[i + 1:]
    heads = [j for j in range(i) if HEADING.match(out[j])]
    if not heads:
        return out
    start = heads[-1]
    level = len(HEADING.match(out[start]).group(1))
    end = next(
        (j for j in range(start + 1, len(out))
         if (m := HEADING.match(out[j])) and len(m.group(1)) <= level),
        len(out),
    )
    if not any(line.strip() for line in out[start + 1:end]):
        del out[start:end]
    return out


# --- the UI ----------------------------------------------------------------

def day_label(iso):
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    today = date.today()
    if d == today:
        return "Today"
    if (today - d).days == 1:
        return "Yesterday"
    return d.strftime("%a %-d %b" if d.year == today.year else "%a %-d %b %Y")


def options(lines):
    """One option per note; headings and the gaps before them are disabled
    options, so the cursor skips them. Option ids are line numbers in the
    file."""
    opts = []
    after_heading = False
    for i, line in enumerate(lines):
        if m := HEADING.match(line):
            if opts and not after_heading:
                opts.append(Option(" ", disabled=True))
            label = Text(day_label(m.group(2)), style=f"bold {MAUVE}")
            opts.append(Option(label, disabled=True))
            after_heading = True
            continue
        elif m := BULLET.match(line):
            level = len(m.group(1)) // 2
            mark = "  " * level + ("•" if level == 0 else "◦") + " "
            opts.append(Option(hanging(Text(mark, style=MAUVE),
                                       Text(m.group(2))), id=str(i)))
        elif line.strip():
            opts.append(Option(Text(line.strip(), style="dim"), id=str(i)))
        else:
            continue
        after_heading = False
    return opts


def hanging(mark, text):
    """`mark` followed by `text`, wrapped lines aligned under the text."""
    grid = Table.grid()
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_row(mark, text)
    return grid


def note_text(line):
    m = BULLET.match(line)
    return m.group(2) if m else line.strip()


class Entry(TextArea):
    """The new-note box. Enter submits instead of breaking the line; kitty's
    keyboard protocol lets Textual tell Shift+Enter apart for that."""

    BINDINGS = [
        Binding("enter", "submit", "Save", key_display="⏎", priority=True),
        Binding("shift+enter", "newline", "New line", key_display="⇧⏎",
                priority=True),
        Binding("ctrl+j", "newline", show=False, priority=True),
        SWITCH,
    ]

    class Submitted(Message):
        def __init__(self, text):
            super().__init__()
            self.text = text

    def action_submit(self):
        self.post_message(self.Submitted(self.text))

    def action_newline(self):
        self.replace("\n", *self.selection, maintain_selection_offset=False)


class NoteList(OptionList):
    """No letter shortcuts: text typed here by mistake goes to the entry
    box instead, so it can never delete notes."""

    BINDINGS = [
        Binding("enter", "select", "Copy", key_display="⏎"),
        Binding("delete", "app.delete_note", "Delete", key_display="del"),
        Binding("ctrl+z", "app.undo", "Undo", key_display="^z"),
        SWITCH,
    ]

    def on_key(self, event):
        if event.is_printable:
            event.stop()
            entry = self.app.query_one(Entry)
            entry.focus()
            entry.insert(event.character)


class NoteTabs(Tabs, can_focus=False):
    """Work / Personal. Not focusable, so Tab keeps moving between the two
    panes; switched by click or the App's Ctrl+PgDn/PgUp."""


class Notes(App):
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Horizontal { height: 1fr; }
    NoteList, Entry {
        height: 1fr;
        border: round $border-blurred;
        border-title-color: $text-muted;
        background: $surface;
        padding: 0 1;
    }
    NoteList:focus, Entry:focus {
        border: round $border;
        border-title-color: $border;
        background-tint: transparent;
    }
    NoteList { width: 3fr; }
    Entry { width: 2fr; }
    Footer { padding: 0 1; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close", key_display="esc"),
        Binding("ctrl+pagedown", "tab('next')", "Work/Personal",
                key_display="^⇟", priority=True),
        Binding("ctrl+pageup", "tab('previous')", show=False,
                priority=True),
    ]

    def __init__(self, resident=False, hidden=False):
        super().__init__()
        self.resident, self.hidden = resident, hidden
        self.books = {tab: Notebook(NOTES_DIR / name)
                      for tab, _, name, _ in TABS}
        self.tab = TABS[0][0]

    @property
    def book(self):
        return self.books[self.tab]

    def compose(self) -> ComposeResult:
        yield NoteTabs(*(Tab(label, id=tab) for tab, label, _, _ in TABS))
        with Horizontal():
            yield NoteList()
            yield Entry(soft_wrap=True, tab_behavior="focus",
                        placeholder=TABS[0][3])
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self):
        self.register_theme(MOCHA)
        self.theme = MOCHA.name
        self.query_one(NoteList).border_title = "Notes"
        entry = self.query_one(Entry)
        entry.border_title = "New note"
        entry.load_text(load_draft())
        entry.move_cursor(entry.document.end)
        self.show(None)
        entry.focus()
        if self.resident:
            signal.signal(signal.SIGUSR1, signal.SIG_DFL)
            asyncio.get_running_loop().add_signal_handler(
                signal.SIGUSR1, self.reveal)
            if early_show or not self.hidden:
                self.call_after_refresh(hyprland, SHOW)

    def reveal(self):
        """SUPER+N: catch up on edits made elsewhere (and on a new day for
        the Today/Yesterday labels) since the window was parked, then bring
        it to the current workspace."""
        if self.book.stale():
            self.book.reload()
            self.show(None)
        elif self.shown_on != date.today():
            self.show(None)
        self.query_one(Entry).focus()
        hyprland(SHOW)

    def action_close(self):
        if self.resident:
            hyprland(HIDE)
        else:
            self.exit()

    def on_text_area_changed(self, event):
        save_draft(event.text_area.text)

    def action_tab(self, direction):
        tabs = self.query_one(NoteTabs)
        if direction == "next":
            tabs.action_next_tab()
        else:
            tabs.action_previous_tab()

    def on_tabs_tab_activated(self, event):
        """Show the other file; it may have been edited since last shown."""
        if event.tab.id == self.tab:
            return
        self.tab = event.tab.id
        self.query_one(Entry).placeholder = next(
            hint for tab, _, _, hint in TABS if tab == self.tab)
        if self.book.stale():
            self.book.reload()
        self.show(None)

    def show(self, highlight):
        """Rebuild the list; highlight option `highlight`, or the newest
        note when it is None."""
        self.shown_on = date.today()
        notes = self.query_one(NoteList)
        notes.clear_options()
        opts = options(self.book.lines)
        if not opts:
            empty = Text("No notes yet", style="dim italic")
            opts = [Option(empty, disabled=True)]
        notes.add_options(opts)
        ids = [i for i, o in enumerate(opts) if o.id is not None]
        if not ids:
            return
        if highlight is None:
            notes.highlighted = ids[-1]
        else:
            notes.highlighted = max(
                [i for i in ids if i <= highlight] or ids[:1]
            )
        self.call_after_refresh(notes.scroll_to_highlight)

    def change(self, lines, highlight=None, undoable=True):
        if undoable:
            self.book.history.append(self.book.lines)
        self.book.save(lines)
        self.show(highlight)

    def reload_if_stale(self):
        """True (after reloading) if the file changed behind our back, so a
        delete or undo would clobber that edit."""
        if not self.book.stale():
            return False
        self.book.reload()
        self.show(None)
        self.notify("Notes changed on disk; reloaded.", severity="warning")
        return True

    def on_entry_submitted(self, event):
        if not event.text.strip():
            return
        # Re-read first: appending is safe even if the file was edited.
        self.change(append(self.book.read(), event.text))
        self.query_one(Entry).clear()

    def action_delete_note(self):
        notes = self.query_one(NoteList)
        if notes.highlighted is None or self.reload_if_stale():
            return
        option = notes.get_option_at_index(notes.highlighted)
        if option.id is not None:
            self.change(remove(self.book.lines, int(option.id)),
                        highlight=notes.highlighted)

    def action_undo(self):
        if not self.book.history:
            self.notify("Nothing to undo.", timeout=2)
        elif not self.reload_if_stale():
            self.change(self.book.history.pop(), undoable=False)

    def on_option_list_option_selected(self, event):
        text = note_text(self.book.lines[int(event.option.id)])
        # Own session, so the clipboard outlives this kitty window.
        subprocess.Popen(["wl-copy", "--", text], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.notify("Copied to clipboard.", timeout=2)


if __name__ == "__main__":
    Notes(resident="--resident" in sys.argv[1:],
          hidden="--hidden" in sys.argv[1:]).run()
