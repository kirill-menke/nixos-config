#!/usr/bin/env bash
# Quick notes modal (SUPER+N): notes-modal (notes.py, packaged in home.nix)
# in a floating kitty window. Notes are kept in ~/Documents/notes.md.
# Pressing SUPER+N while it is already open focuses it instead.

if hyprctl clients -j | jq -e 'any(.[]; .class == "notes-modal")' >/dev/null; then
    exec hyprctl dispatch 'hl.dsp.focus({ window = "class:^(notes-modal)$" })' >/dev/null
fi

exec kitty --class=notes-modal --title=Notes -o window_padding_width=12 notes-modal
