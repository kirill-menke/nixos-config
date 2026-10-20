#!/usr/bin/env python3
"""The waybar bell (custom/notifications): swaync's state, plus what is new.

`swaync-client -s` prints swaync's state as a JSON line on every change
(count, dnd, visible, inhibited) and again whenever swaync (re)appears;
this turns each line into the module's JSON, so nothing polls. What it
adds to `swaync-client -swb` is "new". swaync's count is its history, where
a popup stays until it is dismissed, so the count alone can't tell what
arrived from what was already read. notif-bar remembers how many
notifications the panel held when it was last open: those are seen, the
rest arrived since.

  alt      swaync's own state, for format-icons:
           [dnd-][inhibited-]notification|none, "notification" meaning
           "something in the panel"
  class    alt, plus "new" while something arrived since the panel was last
           open and "cc-open" while it is open
  text     the badge after the bell: the number of new notifications ("9+"
           past nine) as markup, or "" when nothing is new
  tooltip  the state in words, "Do Not Disturb · 2 new, 5 in the panel",
           then the right click's hint

The seen count lives in $XDG_RUNTIME_DIR, so a restarted bar, or the bar of
a monitor plugged in later, still knows it; every bar reads the same lines
and so writes the same count. A dismissal while the panel is closed lowers
it to what the panel still holds: what gets dismissed then is mostly a
popup, i.e. a new one.
"""

import json
import os
import subprocess
import sys

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
SEEN = os.environ.get("NOTIF_BAR_SEEN",
                      os.path.join(RUNTIME, "notifications-seen"))
# The feed; overridden only to replay recorded lines (lab fixtures).
SOURCE = os.environ.get("NOTIF_BAR_SOURCE", "swaync-client -s")
# The tooltip's last line, the gestures a click doesn't show, as every bar
# module has it: small, in the cards' subtext0 (mocha). Markup, so the
# module runs with "escape": false, safe as every string here is our own.
HINT = ("\n<span size='small' foreground='#a6adc8'>"
        "Right-click: Do Not Disturb</span>")
# The badge ("format": "{icon}{text}"): bold, at the label's x-small and
# raised 5 pt to the bell's shoulder, tucked 1 pt into the bell's advance
# (Pango puts half of a letter_spacing before the digits, none after the
# line's last one). A digit adds 5 px, which style.css's min-width holds
# with the widest bell. In mocha's @warn (peach) in every state, so under
# Do Not Disturb or a hold, with the bell itself dim or mauve, what is new
# still shows; markup can't name CSS colours.
BADGE = ("<span size='x-small' rise='5pt' font_weight='bold' "
         "letter_spacing='-2048' foreground='#fab387'>{}</span>")


def badge(new):
    """The badge for `new` new notifications: past nine "9+", which the
    tooltip spells out."""
    return BADGE.format(new if new < 10 else "9+") if new else ""


def load_seen():
    try:
        with open(SEEN) as f:
            return int(f.read())
    except (OSError, ValueError):
        return 0  # a new session: whatever is in the panel is new


def save_seen(n):
    tmp = f"{SEEN}.{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            f.write(str(n))
        os.replace(tmp, SEEN)  # whole, for a bar that starts meanwhile
    except OSError:
        pass


def module(count, dnd, held, visible, seen):
    new = max(count - seen, 0)
    alt = (("dnd-" if dnd else "") + ("inhibited-" if held else "")
           + ("notification" if count else "none"))
    classes = [alt] + (["new"] if new else []) + (["cc-open"] if visible else [])
    words = []
    if dnd:
        words.append("Do Not Disturb")
    elif held:  # under Do Not Disturb they are held back anyway
        words.append("Popups held back")
    if count:
        panel = f"{count} in the panel"
        words.append(f"{new} new, {panel}" if new else panel)
    else:
        words.append("No notifications")
    return {"text": badge(new), "alt": alt, "class": classes,
            "tooltip": " · ".join(words) + HINT}


def main():
    seen = load_seen()
    feed = subprocess.Popen(SOURCE, shell=True, stdout=subprocess.PIPE,
                            text=True)
    shown = None
    try:
        for line in feed.stdout:
            try:
                state = json.loads(line)
                count = int(state["count"])
                dnd, held = bool(state["dnd"]), bool(state["inhibited"])
                visible = bool(state["visible"])
            except (ValueError, KeyError, TypeError):
                continue
            # Open, the panel shows everything; dismissed ones leave the seen.
            new_seen = count if visible else min(seen, count)
            if new_seen != seen:
                seen = new_seen
                save_seen(seen)
            out = json.dumps(module(count, dnd, held, visible, seen))
            if out != shown:  # swaync repeats itself, e.g. on reappearing
                print(out, flush=True)
                shown = out
        return feed.wait()  # swaync-client gone: waybar's restart-interval
    except BrokenPipeError:  # the bar is gone
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    finally:
        # A bar that stops the module kills its whole process group; one
        # that crashed leaves it running. swaync-client ignores SIGPIPE, so
        # left behind it would wait for swaync's lines forever.
        feed.terminate()


if __name__ == "__main__":
    sys.exit(main())
