#!/usr/bin/env bash
# kb-layout-sync: a keyboard Hyprland has just set up (plugged in, docked,
# a receiver, the headphones' media keys) starts on the first layout; give
# it the one in use. The power button holds that: every Alt+Shift switches
# it with the rest, it never comes or goes, and the bar's language pill
# shows it. Without one, most keyboards hold it, a tie going to the later
# layout, since the keyboard just set up is the one on the first.
# udev starts this once per new key device (modules/nixos/desktop.nix, unit
# in modules/home/desktop.nix), and Hyprland sets the keyboard up as udev announces it,
# hence the wait. Without Hyprland (devices present at login, before it
# runs) it does nothing and still exits 0, so the unit never shows up as
# failed in the bar's attention module. Keyboards already on the layout are
# left alone, so they post no layout event.
set -euo pipefail
sleep 1
devices=$(hyprctl -j devices 2>/dev/null) || exit 0
idx=$(jq 'first(.keyboards[] | select(.name == "power-button")).active_layout_index
    // ([.keyboards[].active_layout_index] | group_by(.) | max_by(length)[0])' \
    <<<"$devices" 2>/dev/null) || exit 0
case $idx in '' | null) exit 0 ;; esac
hyprctl switchxkblayout all "$idx" >/dev/null || true
