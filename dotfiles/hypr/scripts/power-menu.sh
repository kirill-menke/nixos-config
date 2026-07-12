#!/usr/bin/env bash
case "$(printf '󰌾  Lock\n󰤄  Suspend\n󰍃  Logout\n󰜉  Reboot\n󰐥  Shutdown' \
        | wofi --dmenu -p power --width 230 --lines 6 --hide-scroll -D hide_search=true)" in
  *Lock)     hyprlock ;;
  *Suspend)  systemctl suspend ;;
  *Logout)   hyprctl dispatch exit ;;
  *Reboot)   systemctl reboot ;;
  *Shutdown) systemctl poweroff ;;
esac
