"""Quick notes modal (SUPER+N).

A two-pane Textual app in a floating kitty window: on the left every note
saved so far, grouped by day; on the right a text box for a new note. Enter
saves it, one bullet per line (Shift+Enter starts another line). Notes live
in a plain Markdown file, so they can be read and edited anywhere:

    ## 2026-10-11

    - look up how ZFS special vdevs work
      - and whether they need a mirror
"""

import os
import re
import subprocess
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
from textual.widgets import Footer, OptionList, TextArea
from textual.widgets.option_list import Option

NOTES = Path(
    os.environ.get("NOTES_FILE", Path.home() / "Documents" / "notes.md")
)

DAY = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
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


# --- the Markdown file -----------------------------------------------------

def load():
    try:
        return NOTES.read_text().splitlines()
    except FileNotFoundError:
        return []


def mtime():
    try:
        return NOTES.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def save(lines):
    """Write atomically, so closing the window mid-save never truncates the
    file. Resolved first, so a symlinked notes file stays a symlink."""
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    path = NOTES.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text("".join(f"{line}\n" for line in lines))
    os.replace(tmp, path)


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
    today = date.today().isoformat()
    days = [m.group(1) for line in out if (m := DAY.match(line))]
    if not days or days[-1] != today:
        out += ([""] if out else []) + [f"## {today}", ""]
    return out + bullets


def remove(lines, i):
    """Return `lines` without line `i`, and without its day heading if that
    was the day's last note."""
    out = lines[:i] + lines[i + 1:]
    heads = [j for j in range(i) if DAY.match(out[j])]
    if not heads:
        return out
    start = heads[-1]
    end = next(
        (j for j in range(start + 1, len(out)) if DAY.match(out[j])),
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
    """One option per note; day headings and the gaps between days are
    disabled options, so the cursor skips them. Option ids are line numbers
    in the file."""
    opts = []
    for i, line in enumerate(lines):
        if m := DAY.match(line):
            if opts:
                opts.append(Option(" ", disabled=True))
            label = Text(day_label(m.group(1)), style=f"bold {MAUVE}")
            opts.append(Option(label, disabled=True))
        elif m := BULLET.match(line):
            level = len(m.group(1)) // 2
            mark = "  " * level + ("•" if level == 0 else "◦") + " "
            opts.append(Option(hanging(Text(mark, style=MAUVE),
                                       Text(m.group(2))), id=str(i)))
        elif line.strip():
            opts.append(Option(Text(line.strip(), style="dim"), id=str(i)))
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
        Binding("escape", "quit", "Close", key_display="esc"),
    ]

    def __init__(self):
        super().__init__()
        self.lines = load()
        self.mtime = mtime()
        self.history = []

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield NoteList()
            yield Entry(soft_wrap=True, tab_behavior="focus",
                        placeholder="Something to look up later…")
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self):
        self.register_theme(MOCHA)
        self.theme = MOCHA.name
        self.query_one(NoteList).border_title = "Notes"
        self.query_one(Entry).border_title = "New note"
        self.show(None)
        self.query_one(Entry).focus()

    def show(self, highlight):
        """Rebuild the list; highlight option `highlight`, or the newest
        note when it is None."""
        notes = self.query_one(NoteList)
        notes.clear_options()
        opts = options(self.lines)
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
            self.history.append(self.lines)
        self.lines = lines
        save(lines)
        self.mtime = mtime()
        self.show(highlight)

    def reload_if_stale(self):
        """True (after reloading) if the file changed behind our back, so a
        delete or undo would clobber that edit."""
        if mtime() == self.mtime:
            return False
        self.lines, self.mtime, self.history = load(), mtime(), []
        self.show(None)
        self.notify("Notes changed on disk; reloaded.", severity="warning")
        return True

    def on_entry_submitted(self, event):
        if not event.text.strip():
            return
        # Re-read first: appending is safe even if the file was edited.
        self.change(append(load(), event.text))
        self.query_one(Entry).clear()

    def action_delete_note(self):
        notes = self.query_one(NoteList)
        if notes.highlighted is None or self.reload_if_stale():
            return
        option = notes.get_option_at_index(notes.highlighted)
        if option.id is not None:
            self.change(remove(self.lines, int(option.id)),
                        highlight=notes.highlighted)

    def action_undo(self):
        if not self.history:
            self.notify("Nothing to undo.", timeout=2)
        elif not self.reload_if_stale():
            self.change(self.history.pop(), undoable=False)

    def on_option_list_option_selected(self, event):
        text = note_text(self.lines[int(event.option.id)])
        # Own session, so the clipboard outlives this kitty window.
        subprocess.Popen(["wl-copy", "--", text], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.notify("Copied to clipboard.", timeout=2)


if __name__ == "__main__":
    Notes().run()
