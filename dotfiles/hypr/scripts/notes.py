"""Quick notes modal (SUPER+N).

A two-pane Textual app in a floating kitty window, with a Work and a Personal
tab (Ctrl+PgDn/PgUp or a click switches): on the left every note of the tab,
grouped by section or by day; on the right a text box for a new note. Enter
saves it to the shown tab (Shift+Enter starts another line).

The notes live in Memos on the NAS (hosts/nas/memos.nix), one memo per note,
so the phone reaches the same notes through Memos' web page. A memo tagged
#work shows in the Work tab, every other one in Personal. A saved note gets
its tab's tag (#work, #personal) unless it already carries one of the two.
A sub-tag is a section: "#personal/Gesundheit" files a note under
"Gesundheit" (a dash in the tag stands for a space); notes without one are
grouped by the day they were written.

Memos' address and the app's access token are in
~/.config/notes-modal/memos.json, {"url": ..., "token": ...}
(NOTES_MEMOS_CONFIG overrides the path). The app keeps the notes it last
fetched and every change not sent yet in ~/.local/state/notes-modal/memos.json
(NOTES_CACHE), so it opens and saves without the NAS: a waiting note shows a
hollow mark, and changes go out once Memos answers again (on SUPER+N, after
every change, and every 30 s while some wait).

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
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone
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

# (tab id = its tag, label, placeholder of the entry box)
TABS = (
    ("work", "Work", "Something for work…"),
    ("personal", "Personal", "Something to remember…"),
)
STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
) / "notes-modal"
DRAFT = Path(os.environ.get("NOTES_DRAFT") or STATE_DIR / "draft.txt")
CACHE = Path(os.environ.get("NOTES_CACHE") or STATE_DIR / "memos.json")
CONFIG = Path(
    os.environ.get("NOTES_MEMOS_CONFIG")
    or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "notes-modal" / "memos.json"
)
RETRY_SECONDS = 30

WINDOW = 'window = "class:^(notes-modal)$"'
SHOW = (
    "hl.dispatch(hl.dsp.window.move({ workspace ="
    f" hl.get_active_workspace().id, {WINDOW}, follow = true }})) "
    f"hl.dispatch(hl.dsp.window.center({{ {WINDOW} }})) "
    f"hl.dispatch(hl.dsp.focus({{ {WINDOW} }}))"
)
HIDE = ("hl.dispatch(hl.dsp.window.move({ workspace = \"special:notes\","
        f" {WINDOW}, follow = false }}))")

# A tag as Memos reads one: "#" and what follows up to a space, at the start
# of a word; trailing punctuation is not part of it.
TAG = re.compile(r"(?<!\S)#([^\s#]+)")

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


# --- the notes: tags, sections ---------------------------------------------

def tags_of(content):
    return [m.group(1).rstrip(".,;:!?)") for m in TAG.finditer(content)]


def tab_of(tags):
    roots = {tag.split("/")[0].lower() for tag in tags}
    return "work" if "work" in roots else "personal"


def section_of(tags, tab):
    """The section a sub-tag of the tab names ("#personal/Kleidung-und-
    Sachen" → "Kleidung und Sachen"), or None."""
    for tag in tags:
        root, _, sub = tag.partition("/")
        if root.lower() == tab and sub:
            return sub.replace("-", " ")
    return None


def tab_tag(word, tab):
    return re.fullmatch(rf"#{tab}(?:/\S*)?", word.rstrip(".,;:!?)"),
                        re.IGNORECASE) is not None


def body(content, tab):
    """The note as shown and copied: its text without the tags of its tab,
    which the tab and the section heading already say."""
    lines = []
    for line in content.splitlines():
        indent = line[:len(line) - len(line.lstrip())]
        words = [w for w in line.split() if not tab_tag(w, tab)]
        if words or not line.strip():
            lines.append(indent + " ".join(words))
    return "\n".join(lines).strip("\n") or content.strip()


def tagged(text, tab):
    """`text` with the tab's tag added, unless it already names a tab."""
    roots = {tag.split("/")[0].lower() for tag in tags_of(text)}
    if roots & {t for t, _, _ in TABS}:
        return text
    return f"{text}\n\n#{tab}" if "\n" in text else f"{text} #{tab}"


def new_memo(content):
    """A note made here. Its id (a valid Memos UID) is chosen here too, so
    sending it twice after a lost answer cannot create it twice."""
    return {
        "id": uuid.uuid4().hex,
        "content": content,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tags": tags_of(content),
    }


def local_day(created):
    return datetime.fromisoformat(created.replace("Z", "+00:00")) \
        .astimezone().date()


def sections(memos, tab):
    """[(heading, memos)] of the tab: named sections in the order of their
    first note, then the days, oldest first; notes oldest first."""
    named, days = {}, {}
    for memo in sorted(memos, key=lambda m: m["created"]):
        if tab_of(memo["tags"]) != tab:
            continue
        if name := section_of(memo["tags"], tab):
            named.setdefault(name, []).append(memo)
        else:
            days.setdefault(local_day(memo["created"]), []).append(memo)
    return list(named.items()) + [(day_label(d), ms)
                                  for d, ms in days.items()]


# --- Memos and the local copy ----------------------------------------------

class SyncError(Exception):
    pass


class Memos:
    """The few calls of Memos' v1 API the app needs."""

    def __init__(self, url, token):
        self.url, self.token, self.user = url.rstrip("/"), token, None

    def call(self, method, path, payload=None):
        request = urllib.request.Request(
            self.url + path, method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError:
            raise
        except (OSError, ValueError) as e:
            raise SyncError("offline") from e

    def send(self, op):
        """Apply one change. Done already (a create Memos has, a delete of
        what it no longer has) counts as done."""
        try:
            if op["op"] == "create":
                memo = op["memo"]
                self.call("POST", f"/api/v1/memos?memoId={memo['id']}",
                          {"content": memo["content"],
                           "visibility": "PRIVATE",
                           "createTime": memo["created"]})
            else:
                self.call("DELETE", f"/api/v1/memos/{op['id']}")
        except urllib.error.HTTPError as e:
            done = 409 if op["op"] == "create" else 404
            if e.code != done:
                raise refused(e) from e

    def list(self):
        """Every note of this account, as {id, content, created, tags}."""
        try:
            if self.user is None:
                me = self.call("GET", "/api/v1/auth/me")
                self.user = me["user"]["name"]
            memos, page = [], ""
            while True:
                query = urllib.parse.urlencode({
                    "pageSize": 1000,
                    "pageToken": page,
                    "filter": f'creator == "{self.user}"',
                })
                answer = self.call("GET", f"/api/v1/memos?{query}")
                memos += [{"id": m["name"].split("/", 1)[1],
                           "content": m["content"],
                           "created": m["createTime"],
                           "tags": m.get("tags", [])}
                          for m in answer.get("memos", [])]
                page = answer.get("nextPageToken")
                if not page:
                    return memos
        except urllib.error.HTTPError as e:
            raise refused(e) from e


def refused(error):
    if error.code in (401, 403):
        return SyncError("Memos refused the token")
    if error.code >= 500 or error.code in (408, 429):
        return SyncError("offline")
    try:
        message = json.loads(error.read()).get("message", "")
    except ValueError:
        message = ""
    return SyncError(f"Memos said {error.code} {message}".strip())


def load_api():
    try:
        config = json.loads(CONFIG.read_text())
        return Memos(config["url"], config["token"])
    except (OSError, ValueError, KeyError):
        return None


class Store:
    """The notes as Memos last listed them, plus the changes not sent yet
    (the outbox), both kept in CACHE. The UI thread adds changes, the sync
    thread sends and fetches; `lock` guards the lists and the file."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        self.memos = data.get("memos", [])
        self.outbox = data.get("outbox", [])

    def save(self):
        """Caller holds the lock."""
        write_atomic(self.path, json.dumps(
            {"memos": self.memos, "outbox": self.outbox},
            ensure_ascii=False, indent=1))

    def view(self):
        """(notes with the outbox applied, ids of notes not sent yet)"""
        with self.lock:
            memos = {m["id"]: m for m in self.memos}
            waiting = set()
            for op in self.outbox:
                if op["op"] == "create":
                    memos[op["memo"]["id"]] = op["memo"]
                    waiting.add(op["memo"]["id"])
                else:
                    memos.pop(op["id"], None)
            return list(memos.values()), waiting

    def waiting(self):
        with self.lock:
            return len(self.outbox)

    def queue(self, op):
        with self.lock:
            self.outbox.append(op)
            self.save()

    def sync(self, api):
        """Send the outbox in order, then fetch every note. Only this
        thread takes from the outbox, and one sync runs at a time."""
        while True:
            with self.lock:
                if not self.outbox:
                    break
                op = self.outbox[0]
            api.send(op)
            with self.lock:
                self.outbox.pop(0)
                self.save()
        memos = api.list()
        with self.lock:
            self.memos = memos
            self.save()


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


# --- the UI ----------------------------------------------------------------

def day_label(d):
    today = date.today()
    if d == today:
        return "Today"
    if (today - d).days == 1:
        return "Yesterday"
    return d.strftime("%a %-d %b" if d.year == today.year else "%a %-d %b %Y")


def options(groups, tab, waiting):
    """One option per note, its id the memo's; headings and the gaps before
    them are disabled options, so the cursor skips them. A note not sent
    yet gets a hollow mark."""
    opts = []
    for heading, memos in groups:
        if opts:
            opts.append(Option(" ", disabled=True))
        opts.append(Option(Text(heading, style=f"bold {MAUVE}"),
                           disabled=True))
        for memo in memos:
            mark = Text("◦ " if memo["id"] in waiting else "• ",
                        style=MAUVE)
            opts.append(Option(hanging(mark, Text(body(memo["content"],
                                                       tab))),
                               id=memo["id"]))
    return opts


def hanging(mark, text):
    """`mark` followed by `text`, wrapped lines aligned under the text."""
    grid = Table.grid()
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_row(mark, text)
    return grid


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
        border-subtitle-color: $warning;
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
        self.store = Store(CACHE)
        self.api = load_api()
        self.tab = TABS[0][0]
        # Per tab, the changes that undo the last ones, newest last.
        self.history = {tab: [] for tab, _, _ in TABS}
        self.shown = {}
        self.shown_key = None
        self.syncing = self.sync_again = False
        self.error = None if self.api else f"not set up: {CONFIG}"

    def compose(self) -> ComposeResult:
        yield NoteTabs(*(Tab(label, id=tab) for tab, label, _ in TABS))
        with Horizontal():
            yield NoteList()
            yield Entry(soft_wrap=True, tab_behavior="focus",
                        placeholder=TABS[0][2])
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self):
        self.register_theme(MOCHA)
        self.theme = MOCHA.name
        self.query_one(NoteList).border_title = "Notes"
        entry = self.query_one(Entry)
        entry.border_title = "New note"
        entry.load_text(load_draft())
        entry.move_cursor(entry.document.end)
        self.show()
        entry.focus()
        self.sync()
        self.set_interval(RETRY_SECONDS, self.retry)
        if self.resident:
            signal.signal(signal.SIGUSR1, signal.SIG_DFL)
            asyncio.get_running_loop().add_signal_handler(
                signal.SIGUSR1, self.reveal)
            if early_show or not self.hidden:
                self.call_after_refresh(hyprland, SHOW)

    def reveal(self):
        """SUPER+N: bring the window to the current workspace, then fetch
        what changed elsewhere (the phone) since it was parked."""
        if self.shown_on != date.today():
            self.show(self.highlighted_id())
        self.query_one(Entry).focus()
        hyprland(SHOW)
        self.sync()

    def action_close(self):
        if self.resident:
            hyprland(HIDE)
        else:
            self.exit()

    def on_text_area_changed(self, event):
        save_draft(event.text_area.text)

    # --- talking to Memos, off the UI thread ---

    def sync(self):
        if self.api is None:
            return
        if self.syncing:
            self.sync_again = True
            return
        self.syncing = True
        self.run_worker(self.sync_thread, thread=True, exit_on_error=False)

    def sync_thread(self):
        try:
            self.store.sync(self.api)
            error = None
        except SyncError as e:
            error = str(e)
        except Exception as e:  # never leave `syncing` set
            error = f"sync failed: {e}"
        self.call_from_thread(self.synced, error)

    def synced(self, error):
        self.syncing = False
        if error and error != "offline" and error != self.error:
            self.notify(error, severity="error", timeout=6)
        self.error = error
        if self.sync_again:
            self.sync_again = False
            self.sync()
        self.show(self.highlighted_id(), only_if_changed=True)

    def retry(self):
        if self.store.waiting():
            self.sync()

    def show_status(self):
        waiting = self.store.waiting()
        parts = [self.error] if self.error else []
        if waiting:
            parts.append(f"{waiting} not sent yet")
        self.query_one(NoteList).border_subtitle = " · ".join(parts)

    # --- the list ---

    def action_tab(self, direction):
        tabs = self.query_one(NoteTabs)
        if direction == "next":
            tabs.action_next_tab()
        else:
            tabs.action_previous_tab()

    def on_tabs_tab_activated(self, event):
        if event.tab.id == self.tab:
            return
        self.tab = event.tab.id
        self.query_one(Entry).placeholder = next(
            hint for tab, _, hint in TABS if tab == self.tab)
        self.show()

    def highlighted_id(self):
        notes = self.query_one(NoteList)
        if notes.highlighted is None:
            return None
        return notes.get_option_at_index(notes.highlighted).id

    def show(self, memo_id=None, index=None, only_if_changed=False):
        """Rebuild the list. Highlight note `memo_id` if it is there, else
        the nearest note at or above option `index`, else the newest."""
        self.show_status()
        memos, waiting = self.store.view()
        key = (self.tab, date.today(), frozenset(waiting),
               frozenset((m["id"], m["content"]) for m in memos))
        if only_if_changed and key == self.shown_key:
            return
        self.shown_key, self.shown_on = key, date.today()
        self.shown = {m["id"]: m for m in memos}
        notes = self.query_one(NoteList)
        notes.clear_options()
        opts = options(sections(memos, self.tab), self.tab, waiting)
        if not opts:
            empty = Text("No notes yet", style="dim italic")
            opts = [Option(empty, disabled=True)]
        notes.add_options(opts)
        ids = [i for i, o in enumerate(opts) if o.id is not None]
        if not ids:
            return
        at = [i for i in ids if opts[i].id == memo_id]
        if at:
            notes.highlighted = at[0]
        elif index is not None:
            notes.highlighted = max([i for i in ids if i <= index]
                                    or ids[:1])
        else:
            notes.highlighted = ids[-1]
        self.call_after_refresh(notes.scroll_to_highlight)

    def change(self, op, undo):
        """Queue `op`, remember `undo` for Ctrl+Z (unless None), send."""
        self.store.queue(op)
        if undo is not None:
            self.history[self.tab].append(undo)
        self.sync()

    def on_entry_submitted(self, event):
        text = textwrap.dedent(event.text.expandtabs(2)).strip("\n").rstrip()
        if not text.strip():
            return
        memo = new_memo(tagged(text, self.tab))
        self.change({"op": "create", "memo": memo},
                    {"op": "delete", "id": memo["id"]})
        self.query_one(Entry).clear()
        self.show(memo["id"])

    def action_delete_note(self):
        notes = self.query_one(NoteList)
        memo_id = self.highlighted_id()
        if memo_id is None:
            return
        self.change({"op": "delete", "id": memo_id},
                    {"op": "create", "memo": self.shown[memo_id]})
        self.show(index=notes.highlighted)

    def action_undo(self):
        if not self.history[self.tab]:
            self.notify("Nothing to undo.", timeout=2)
            return
        op = self.history[self.tab].pop()
        self.change(op, None)
        self.show(op["memo"]["id"] if op["op"] == "create" else None,
                  index=self.query_one(NoteList).highlighted)

    def on_option_list_option_selected(self, event):
        text = body(self.shown[event.option.id]["content"], self.tab)
        # Own session, so the clipboard outlives this kitty window.
        subprocess.Popen(["wl-copy", "--", text], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.notify("Copied to clipboard.", timeout=2)


if __name__ == "__main__":
    Notes(resident="--resident" in sys.argv[1:],
          hidden="--hidden" in sys.argv[1:]).run()
