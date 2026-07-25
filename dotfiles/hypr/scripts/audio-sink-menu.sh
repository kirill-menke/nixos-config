#!/usr/bin/env bash
# Sink picker for the waybar audio module.
#
# Lists every connected audio sink and switches the default to the chosen one.
# Existing streams follow the default in PipeWire, so a running player moves
# too.  This matters beyond convenience: `play` decides whether to bitstream
# Atmos and switch the TV into 4K/HDR by looking at which sink is default.

set -u

cur=$(wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null |
      sed -n '1s/.*id \([0-9]\+\).*/\1/p')

# node.nick is the friendly name ("LG TV SSCR2", "KM-Bose"); fall back through
# description and name for devices that do not set it.
mapfile -t sinks < <(
  pw-dump 2>/dev/null | jq -r '
    .[]
    | select(.info.props."media.class" == "Audio/Sink")
    | select(.info.props."node.name" | test("^auto_null$") | not)
    | [ (.id|tostring),
        (.info.props."node.nick"
         // .info.props."node.description"
         // .info.props."node.name"),
        (.info.props."device.api" // "") ]
    | @tsv'
)

[ "${#sinks[@]}" -eq 0 ] && exit 0

menu=""
for row in "${sinks[@]}"; do
  IFS=$'\t' read -r id name api <<<"$row"
  case "$api" in
    bluez5) icon="󰂰" ;;
    *)      case "$name" in
              *TV*|*HDMI*) icon="󰔂" ;;
              *)           icon="󰕾" ;;
            esac ;;
  esac
  # A dot marks the sink that is currently default.
  [ "$id" = "$cur" ] && mark="  ●" || mark=""
  menu+="$icon  $name$mark"$'\n'
done

choice=$(printf '%s' "$menu" |
         wofi --dmenu -p sink --width 340 --lines "$(( ${#sinks[@]} + 1 ))" \
              --hide-scroll -D hide_search=true)

[ -z "$choice" ] && exit 0

# Strip icon prefix and the current-default marker back off, then match the
# name to its node id.
picked=${choice#*  }
picked=${picked%  ●}

for row in "${sinks[@]}"; do
  IFS=$'\t' read -r id name api <<<"$row"
  if [ "$name" = "$picked" ]; then
    wpctl set-default "$id"
    exit 0
  fi
done
