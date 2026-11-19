#!/usr/bin/env bash
# Quick notes modal (SUPER+N): notes-modal (notes.py, packaged in
# modules/home/desktop.nix) in a floating kitty window. Notes are kept in
# Memos on the NAS (hosts/nas/memos.nix): Work tab = #work, Personal = rest.
# The app stays running, parked on the hidden workspace special:notes, so it
# opens at once: SIGUSR1 brings it to the current workspace (or focuses it
# there), Esc parks it again. Only a first start, or one after SUPER+Q, pays
# for kitty + Textual (~0.7 s); the app shows itself once it has drawn.
# `notes.sh --hidden` (Hyprland's start hook) starts it parked.

app='/bin/notes-modal( |$)'
if [ "$1" = --hidden ]; then
    pgrep -f "$app" >/dev/null && exit
elif pkill -USR1 -f "$app"; then
    exit
fi

exec kitty --class=notes-modal --title=Notes -o window_padding_width=12 notes-modal --resident "$@"
