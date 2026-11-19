#!/usr/bin/env python3
"""The waybar pomodoro module: one long-running `pomodoro-bar` per bar, and
the commands that change the timer.

`pomodoro-bar start FOCUS BREAK` (the popup's Start), `pomodoro-bar toggle`
(middle click, SUPER+SHIFT+P; the popup's Pause/Resume), `pomodoro-bar
stop` (double right-click; the popup's Stop) and `pomodoro-bar add SECONDS`
(scrolling on the module: five minutes more or less for the phase on)
change the state file under a lock, then wake every bar with SIGUSR1
through the pomodoro-bar-PID.pid files in $XDG_RUNTIME_DIR; toggle starts
an idle timer at its last lengths. A bar prints the module's JSON whenever
it changes: while a timer runs, the whole minutes left (the seconds of the
last one), so about once a minute; idle or paused, it sleeps until poked.

When a phase runs out, the bar that notices moves the timer on (focus ->
break -> idle), plays the sound and sends the notification. It decides under
the lock, on a fresh read of the state, so with a bar on every monitor that
happens once. A phase that ran out unseen, while the computer slept or no
bar ran, ends quietly in the idle timer instead (missed()).

Focus sessions come in sets of four: the break after the fourth is three
times as long, at most 30 minutes (long_break()). A set starts over after
that break, or once the timer has stood idle for longer than such a break
(set_done()). A stopped session doesn't count, nor does one that ran out
unseen; the sessions before it still do.

While a focus phase runs, a swaync inhibitor keeps notifications from
popping up (they still collect in the panel, and critical ones show anyway).
Released by hand (the panel's Release), it stays released for the rest of
the phase; only a swaync that restarted, and so lost it, gets it back. The
user's own Do Not Disturb switch is never touched.

The timer goes with a reboot, as $XDG_RUNTIME_DIR does; the lengths last
started with and the set's count are kept in $XDG_STATE_HOME/pomodoro.json,
beside nightlight.json, and the idle timer starts from them.
"""

import contextlib
import fcntl
import glob
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
STATE = os.path.join(RUNTIME, "pomodoro-state.json")
LOCK = STATE + ".lock"
# swaync calls queue on a lock of their own: the state lock is only ever
# held for a read and a write, so a bar never waits for swaync.
INHIBIT_LOCK = os.path.join(RUNTIME, "pomodoro-inhibitor.lock")
INHIBITOR = "pomodoro"  # the application ID swaync knows our inhibitor by
SWAYNC = "org.erikreider.swaync.cc"  # swaync's name on the session bus
NOTIFICATIONS = "org.freedesktop.Notifications"  # swaync's as well
# Named by PID rather than by monitor, so an exiting copy can only ever
# remove its own.
PIDFILE = os.path.join(RUNTIME, f"pomodoro-bar-{os.getpid()}.pid")
PIDFILES = os.path.join(RUNTIME, "pomodoro-bar-*.pid")  # every bar's
# "Break over" while it is up: its ID and the bus name of the swaync that
# showed it, so that the next session, however started, can take it down
# (close_note()).
NOTE = os.path.join(RUNTIME, "pomodoro-break-over")
# The lengths last started with and the set's count, kept across reboots
# as nightlight.py keeps its settings (keep()).
STATE_HOME = (os.environ.get("XDG_STATE_HOME")
              or os.path.expanduser("~/.local/state"))
KEPT = os.path.join(STATE_HOME, "pomodoro.json")

# Lengths in minutes, times in epoch seconds. A running phase ends at
# "end", a paused one has "left" (s); "length" is the phase's whole length
# (s), as add() has moved it (span()). "done" counts the focus sessions done
# in the set, and "since" is when the timer last went idle (set_done()).
DEFAULTS = {"status": "idle", "phase": "work", "end": 0, "left": 0,
            "length": 0, "work": 60, "break": 10, "done": 0, "since": 0}
# What every state takes over from the one before it (carry()).
CARRIED = ("work", "break", "done", "since")

ICON_IDLE = "\uf252"    # nf-fa-hourglass_half
ICON_BREAK = "\uf0f4"   # nf-fa-coffee
ICON_PAUSED = "\uf04c"  # nf-fa-pause
# Focus counts down on a pie that empties by eighths as the session runs,
# as a kitchen timer's dial does.
PIE = [chr(0xF0A9E + i) for i in range(8)]  # nf-md-circle_slice_1 .. _8

# A running phase shows whole minutes, so its text changes once a minute
# (each second in the last one), and a bar sleeps until then. That sleep
# runs on CLOCK_MONOTONIC, which stands still while the computer sleeps:
# capped at this many seconds, a bar sees a resume, and a phase that ran
# out meanwhile, at most that late.
WAKE_CAP = 10

# A tooltip's last line: the gestures a click doesn't show (config.jsonc),
# small, in the cards' subtext0, as on the bar's other modules.
HINT = "\n<span size='small' foreground='#a6adc8'>{}</span>"

# What plays when a focus phase / a break ends: names in the freedesktop
# sound theme.
SOUNDS = {"work": "complete", "break": "bell"}

# A break that ran out longer ago than this, in s, ended unseen: missed().
LATE = 300

# A set: SET focus sessions, the last one followed by a break LONG times
# the usual one, at most LONG_MAX minutes (long_break()).
SET = 4
LONG = 3
LONG_MAX = 30

# add() keeps a phase from a minute left up to the most that the bar's
# three characters show, 99m (minutes_left()); in s.
LEAST_LEFT = 60
MOST_LEFT = 99 * 60 + 59


def load():
    """The timer; defaults fill in missing keys, and no state file (or one
    that can't be read) is the idle timer, at the lengths last started
    with and the set's count: a reboot empties $XDG_RUNTIME_DIR, not those
    (kept())."""
    state = dict(DEFAULTS)
    try:
        with open(STATE) as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        saved = None
    if isinstance(saved, dict):
        state.update(saved)
    else:
        state.update(kept())
    return state


def kept():
    """What keep() kept, as far as it can be read: the focus and break
    lengths, and the set's "done" and "since", which a file kept before
    there were sets lacks. {} while there are no lengths to read."""
    try:
        with open(KEPT) as fh:
            saved = json.load(fh)
        work, brk = saved["work"], saved["break"]
    except (OSError, ValueError, TypeError, KeyError):
        return {}
    if not all(type(n) is int and n >= 0 for n in (work, brk)):
        return {}
    found = {"work": work, "break": brk}
    for key, most in (("done", SET), ("since", None)):
        n = saved.get(key)
        if type(n) is int and 0 <= n and (most is None or n <= most):
            found[key] = n
    return found


def write(path, text):
    # Through a temp file of our own, so readers (the bars, the popup)
    # never see half a file and no two writers share a temp name.
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def save(state):
    write(STATE, json.dumps(state))


def keep(state):
    """Keep the timer's lengths and the set's count for after a reboot,
    unless they are kept already: a start, a phase's end and a stop change
    them, a pause or add() never."""
    now_kept = {key: state[key] for key in CARRIED}
    if now_kept != kept():
        # Without them the timer still runs, at the defaults after a reboot.
        with contextlib.suppress(OSError):
            os.makedirs(STATE_HOME, exist_ok=True)
            write(KEPT, json.dumps(now_kept))


def is_focus(state):
    return state["status"] == "running" and state["phase"] == "work"


def carry(old):
    """The lengths and the set, which every state takes over from the one
    before it."""
    return {key: old[key] for key in CARRIED}


def span(state):
    """The phase's whole length in s: its focus or break minutes, or the
    long break's, as add() has moved it since. A state file written before
    there was a "length" gives its lengths' minutes."""
    return state["length"] or 60 * state[
        "work" if state["phase"] == "work" else "break"]


def long_break(state):
    """The break after a set's last session, in minutes: LONG times the
    usual one, at most LONG_MAX, though never shorter than the usual one
    (`start 50 40`)."""
    return max(state["break"], min(LONG * state["break"], LONG_MAX))


def next_break(state):
    """The break, in minutes, that follows the focus phase in `state`."""
    return long_break(state) if state["done"] + 1 >= SET else state["break"]


def is_long(state):
    """Whether the break in `state` is the long one: it keeps the set's
    count at SET until the set starts over."""
    return state["phase"] == "break" and state["done"] >= SET


def set_done(state, now):
    """The sessions done in the set that a session started at `now` joins:
    none once the set is complete, or once the timer has stood idle longer
    than a long break lasts, which was rest enough."""
    if state["done"] >= SET or (
            state["status"] == "idle"
            and now - state["since"] > long_break(state) * 60):
        return 0
    return state["done"]


def idle(old, since):
    """The idle timer after `old`, idle since `since`; after the long
    break, a set that starts over."""
    return {"status": "idle", "phase": "work", "end": 0, **carry(old),
            "done": 0 if is_long(old) else old["done"], "since": since}


def bar_pid(pidfile):
    """The PID in a bar's pidfile, or None if that bar is gone. A bar can't
    delete its pidfile when it is killed outright (systemd sends SIGKILL to
    what is left of waybar's unit when waybar stops or crashes), and its
    PID may since belong to another process, which SIGUSR1 would end. A bar
    is pomodoro-bar run without arguments, so its last argument is the
    script itself; that also tells it from `pomodoro-bar toggle` and the
    other commands."""
    try:
        with open(pidfile) as fh:
            pid = int(fh.read())
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            last = fh.read().rstrip(b"\0").rsplit(b"\0", 1)[-1]
        return pid if b"pomodoro-bar" in last else None
    except (OSError, ValueError):
        return None


def poke_bars():
    """Wake every bar (idle or paused, they sleep until poked) and delete
    the pidfiles of bars that are gone."""
    for pidfile in glob.glob(PIDFILES):
        pid = bar_pid(pidfile)
        try:
            if pid is None:
                os.remove(pidfile)
            else:
                os.kill(pid, signal.SIGUSR1)
        except OSError:
            pass  # that bar has just exited


def background(target, *args):
    """Run target(*args) in a thread of its own, so the bar keeps ticking
    while swaync-client, notify-send or pw-play take their time. Started
    after bar() has blocked SIGUSR1, the thread inherits the block: a poke
    is only ever taken by the loop's wait."""
    threading.Thread(target=target, args=args, daemon=True).start()


def swaync(*args):
    # -sw: fail at once when swaync isn't there, rather than wait for it.
    try:
        subprocess.run(["swaync-client", "-sw", *args],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def sync_inhibitor():
    """Hold swaync's inhibitor exactly while a focus phase runs. Whoever
    changes the timer calls this afterwards; one call at a time, each on a
    fresh read of the timer, so when two changes race the last call still
    leaves the right answer standing."""
    with open(INHIBIT_LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # held until the file closes
        swaync("-Ia" if is_focus(load()) else "-Ir", INHIBITOR)


def guard_inhibitor():
    """swaync forgets its inhibitors when it restarts (an update, a crash).
    `swaync-client -s` prints swaync's state on every change and each time
    swaync (re)appears; a bar runs one through a focus phase and puts the
    inhibitor back when a new swaync has none (reassert()). Returns that
    process, to stop when the phase ends, or None without swaync-client."""
    try:
        sub = subprocess.Popen(["swaync-client", "-s"],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    background(reassert, sub.stdout)
    return sub


def reassert(lines):
    """Put the inhibitor back when it went with a swaync that restarted.
    When the swaync that held it lets it go, it was released on purpose
    (the panel's Release clears every inhibitor): that holds for the rest
    of the phase."""
    holder = None  # bus_owner() of the swaync last seen holding it
    released = False
    for line in lines:  # until the subscriber is stopped
        try:
            inhibited = json.loads(line)["inhibited"]
        except (ValueError, KeyError, TypeError):
            continue
        if released:
            continue
        if inhibited:
            if holder is None:
                holder = bus_owner(SWAYNC)
            continue
        # A pause or a phase end removes it on purpose: re-read the timer.
        if not is_focus(load()):
            continue
        owner = bus_owner(SWAYNC)
        if owner is None:
            continue  # swaync is gone; the next one prints a line too
        if owner == holder:
            released = True
            continue
        sync_inhibitor()
        holder = None  # until swaync says that it holds it


def bus_owner(name):
    """The unique name that owns `name` on the session bus, or None while
    none does. A restarted swaync comes back under a new one."""
    try:
        out = subprocess.run(
            ["busctl", "--user", "call", "org.freedesktop.DBus",
             "/org/freedesktop/DBus", "org.freedesktop.DBus",
             "GetNameOwner", "s", name],
            capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    # As busctl prints a string: s ":1.57"
    return out[1].strip('"') if len(out) == 2 and out[0] == "s" else None


def play(name):
    """Play `name` from the freedesktop sound theme, if it is installed
    (NixOS installs it unless xdg.sounds.enable is off)."""
    home = (os.environ.get("XDG_DATA_HOME")
            or os.path.expanduser("~/.local/share"))
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for base in [home, *dirs.split(":")]:
        path = os.path.join(base, "sounds", "freedesktop", "stereo",
                            f"{name}.oga")
        if os.path.isfile(path):
            try:
                subprocess.run(["pw-play", path], stdout=subprocess.DEVNULL,
                               timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
            return


def missed(state, now):
    """Whether the phase in `state` ran out unseen, long before `now`, while
    the computer slept or no bar ran: a break over for more than LATE, or a
    focus phase whose break would be over by now too. Moved on as usual, it
    would start a break nobody takes and sound the alarm for a session long
    over."""
    late = now - state["end"]
    return late > (next_break(state) * 60 if state["phase"] == "work"
                   else LATE)


def expire(now):
    """End the phase that ran out by `now`: focus turns into a break that
    starts now, the long one after a set's last session, and a break or a
    missed() phase into the idle timer, idle since the phase ran out (a
    missed focus session doesn't count). Returns the timer as it was and
    as it is now, or None if another bar has already done it."""
    with open(LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # held until the file closes
        old = load()
        if old["status"] != "running" or old["end"] > now:
            return None
        if old["phase"] == "work" and not missed(old, now):
            brk = next_break(old) * 60
            new = {"status": "running", "phase": "break", "end": now + brk,
                   "length": brk, **carry(old), "done": old["done"] + 1}
        else:
            new = idle(old, old["end"])
        save(new)
        keep(new)  # under the lock, as the move on: once, by one bar
        return old, new


def phase_over(ended, new, now):
    """What follows the end of a phase, on the bar that ended it: the
    sound, the inhibitor off after focus (it would hold back this very
    notification), and the notification. A missed() phase plays nothing:
    focus says quietly when it ended, a break says nothing at all. `new`
    is the timer that followed (expire())."""
    quiet = missed(ended, now)
    if not quiet:
        background(play, SOUNDS[ended["phase"]])
    if ended["phase"] == "work":
        sync_inhibitor()
        if quiet:
            end = time.strftime("%H:%M", time.localtime(ended["end"]))
            notify("low", "Focus session ended",
                   f"At {end}, while you were away.")
        else:
            minutes = new["length"] // 60
            # Through Do Not Disturb, as osd's and publicip's popups are:
            # the user set this timer, so its end is news they asked for.
            notify("normal", "Focus session complete",
                   f"Fourth session done: take a {minutes} minute break."
                   if is_long(new) else f"Take a {minutes} minute break.",
                   "boolean:SWAYNC_BYPASS_DND:true")
    elif not quiet:
        # Critical: it shows through Do Not Disturb and stays up until
        # answered. The next session, however started, takes it down
        # (change()).
        notify("critical", "Break over", "Ready for another session?",
               keep=True)


def notify(urgency, summary, body, *hints, keep=False):
    """Send a notification, with notify-send's hints (TYPE:NAME:VALUE).
    keep: note it in NOTE, for close_note() to take it down."""
    cmd = ["notify-send", "-a", "Pomodoro", "-u", urgency,
           "-i", "alarm-symbolic"]
    for hint in hints:
        cmd += ["-h", hint]
    if keep:
        cmd.append("--print-id")
    try:
        # stdout is waybar's pipe: anything printed there would be read as
        # the module's next JSON line. The ID comes through a pipe of its
        # own.
        note = subprocess.run([*cmd, summary, body], stdout=subprocess.PIPE,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return
    if keep and note.isdecimal():
        # With the server's name: a restarted swaync counts IDs from 1 again.
        write(NOTE, f"{note} {bus_owner(NOTIFICATIONS)}")


def close_note():
    """Take "Break over" down if it is still up (closed by hand already, it
    takes nothing else with it: an ID is never reused by the same swaync)."""
    try:
        with open(NOTE) as fh:
            note = fh.read().split()
        os.remove(NOTE)
    except OSError:
        return
    if (len(note) == 2 and note[0].isdecimal()
            and note[1] == bus_owner(NOTIFICATIONS)):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["busctl", "--user", "call", NOTIFICATIONS,
                 "/org/freedesktop/Notifications", NOTIFICATIONS,
                 "CloseNotification", "u", note[0]],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5)


def mmss(seconds):
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def minutes_left(left):
    """A phase's time left on the bar: whole minutes, then the seconds of
    the last one. Three characters from 60m down to 1s (a space pads a
    single digit, as wide as one in this monospaced face), so the module
    keeps its width as it counts, two characters narrower than mm:ss."""
    return f"{left // 60:>2}m" if left >= 60 else f"{left:>2}s"


def next_change(state, now):
    """When a running phase's text next changes: at its next whole minute
    left, or at the next second in the last minute."""
    left = state["end"] - now
    return now + 1 if left <= 60 else state["end"] - (left // 60 * 60 - 1)


def module(state, now):
    """The module's JSON at `now`, in whole seconds. It changes only with
    the text (next_change()): the tooltip says when a phase ends rather
    than how long it has left."""
    name = ("Focus" if state["phase"] == "work"
            else "Long break" if is_long(state) else "Break")
    if state["status"] == "paused":
        left = state["left"]
        return {"text": f"{ICON_PAUSED} {minutes_left(left)}",
                "tooltip": f"{name} paused — {mmss(left)} left" + HINT.format(
                    "Scroll: ±5 min · Middle-click: resume"
                    " · Double right-click: stop"),
                "class": "paused"}
    if state["status"] != "running":
        # No scroll here: add() leaves an idle timer as it is.
        return {"text": ICON_IDLE,
                "tooltip": "Pomodoro — click to start" + HINT.format(
                    f"Middle-click: start {state['work']} min focus"),
                "class": "idle"}
    left = state["end"] - now
    end = time.strftime("%H:%M", time.localtime(state["end"]))
    # Its whole length as add() left it, to the nearest minute.
    tooltip = f"{name} until {end} ({(span(state) + 30) // 60} min)" + (
        HINT.format("Scroll: ±5 min · Middle-click: pause"
                    " · Double right-click: stop"))
    if state["phase"] == "work":
        return {"text": f"{pie(state, left)} {minutes_left(left)}",
                "tooltip": tooltip, "class": "work"}
    return {"text": f"{ICON_BREAK} {minutes_left(left)}",
            "tooltip": tooltip, "class": "break"}


def pie(state, left):
    """The focus phase's glyph with `left` s to go: the eighths of its
    whole length left, rounded up, off the minutes shown, so the pie only
    ever changes along with them."""
    eighths = -(-(left // 60) * 480 // max(60, span(state)))
    return PIE[min(max(eighths, 1), 8) - 1]


def bar():
    # Blocked before the pidfile exists: SIGUSR1 kills a process that
    # neither blocks nor handles it. Blocked, a poke stays pending until
    # the loop waits for one.
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
    # waybar stops the module with SIGTERM; leave through `finally`.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit())
    focus = False
    guard = None  # guard_inhibitor's subscriber, through a focus phase
    try:
        for pidfile in glob.glob(PIDFILES):
            if bar_pid(pidfile) is None:  # left behind by a bar that is gone
                with contextlib.suppress(OSError):
                    os.remove(pidfile)
        # In one piece: read half-written, it would pass for a gone bar's.
        write(PIDFILE, str(os.getpid()))
        if not is_focus(load()):
            # An inhibitor left behind by whatever died before it could
            # take it away. (Through focus, the guard checks it.)
            background(sync_inhibitor)
        shown = None
        while True:
            now = int(time.time())
            state = load()
            ended = None
            if state["status"] == "running" and state["end"] <= now:
                ended = expire(now)
                state = load()
            line = json.dumps(module(state, now), ensure_ascii=False,
                              separators=(",", ":"))
            if line != shown:
                print(line, flush=True)
                shown = line
            if ended:  # after the print: the bar needn't wait for it
                background(phase_over, *ended, now)
            if is_focus(state) != focus:
                focus = not focus
                if focus:
                    guard = guard_inhibitor()
                elif guard:
                    guard.terminate()
                    guard.wait()
                    guard = None
            if state["status"] == "running":
                # Until the text changes, or WAKE_CAP (see there).
                wake = min(next_change(state, now), now + WAKE_CAP)
                signal.sigtimedwait({signal.SIGUSR1},
                                    max(0, wake - time.time()))
            else:
                signal.sigwaitinfo({signal.SIGUSR1})
    finally:
        if guard:
            guard.terminate()
        with contextlib.suppress(OSError):
            os.remove(PIDFILE)


def start(old, now, work, brk):
    """A focus session at these lengths, in the set that set_done() says
    it joins."""
    return {"status": "running", "phase": "work", "end": now + work * 60,
            "length": work * 60, **carry(old), "work": work, "break": brk,
            "done": set_done(old, now)}


def toggle(old, now):
    """Start an idle timer at its last lengths, pause a running phase,
    keeping what it has left, or resume a paused one: the middle click and
    SUPER+SHIFT+P do all three. A phase that has just run out (a bar moves
    it on) stays as it is."""
    if old["status"] == "idle":
        return start(old, now, old["work"], old["break"])
    if old["status"] == "running" and old["end"] > now:
        return {"status": "paused", "phase": old["phase"],
                "left": old["end"] - now, "length": span(old), **carry(old)}
    if old["status"] == "paused":
        return {"status": "running", "phase": old["phase"],
                "end": now + old["left"], "length": span(old), **carry(old)}
    return old


def stop(old, now):
    """The idle timer, the phase on thrown away: a focus session stopped
    doesn't count, though the set goes on. An idle timer stays idle since
    when it went idle."""
    return idle(old, old["since"] if old["status"] == "idle" else now)


def add(old, now, delta):
    """Move the end of a running or paused phase by `delta` s (scrolling on
    the module: ±5 min), and its length with it. A step that would leave
    less than LEAST_LEFT, or more than MOST_LEFT, goes as far as it can in
    whole minutes, so the seconds left stay as they were: a phase in its
    last two minutes can't be shortened at all. An idle timer, or a phase
    that has just run out (a bar moves it on), stays as it is."""
    if old["status"] == "paused":
        left = old["left"]
    elif old["status"] == "running" and old["end"] > now:
        left = old["end"] - now
    else:
        return old
    # Past either limit already (a `start 120 10`), only the step back
    # towards it: there the modulo would round the other way.
    if delta < 0 and left > LEAST_LEFT:
        moved = max(delta, LEAST_LEFT + (left - LEAST_LEFT) % 60 - left)
    elif delta > 0 and left < MOST_LEFT:
        moved = min(delta, MOST_LEFT - (MOST_LEFT - left) % 60 - left)
    else:
        moved = 0
    if not moved:
        return old
    new = {"status": old["status"], "phase": old["phase"],
           "length": span(old) + moved, **carry(old)}
    if old["status"] == "paused":
        new["left"] = left + moved
    else:
        new["end"] = old["end"] + moved
    return new


def change(edit, *args):
    """Change the timer to edit(old, now, *args) under the lock, keeping
    its lengths and the set's count for after a reboot, then take "Break
    over" down (whatever the change, it answers that), bring swaync's
    inhibitor along and wake every bar. Stop always sets the inhibitor, so
    a stop also clears one left behind by whatever died half-way."""
    now = int(time.time())
    with open(LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # held until the file closes
        old = load()
        new = edit(old, now, *args)
        if new is old:
            return
        save(new)
        keep(new)
    close_note()
    if edit is stop or is_focus(old) != is_focus(new):
        sync_inhibitor()
    poke_bars()


def main(argv):
    cmd = argv[1:]
    if not cmd:
        bar()
    elif (cmd[0] == "start" and len(cmd) == 3
          and all(n.isdecimal() for n in cmd[1:])):
        change(start, int(cmd[1]), int(cmd[2]))
    elif cmd == ["toggle"]:
        change(toggle)
    elif cmd == ["stop"]:
        change(stop)
    elif (cmd[0] == "add" and len(cmd) == 2
          and re.fullmatch(r"[+-]?[0-9]+", cmd[1])):
        change(add, int(cmd[1]))
    else:
        print("usage: pomodoro-bar [start FOCUS BREAK | toggle | stop"
              " | add SECONDS]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
