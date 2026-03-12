#!/usr/bin/env bash

CACHE="/tmp/waybar-network-public-ip"
CACHE_TTL=300  # refresh public IPs every 5 minutes

get_public_ips() {
    if [ -f "$CACHE" ] && [ $(( $(date +%s) - $(stat -c %Y "$CACHE") )) -lt $CACHE_TTL ]; then
        cat "$CACHE"
    else
        PUB4=$(curl -s --max-time 3 -4 https://ifconfig.me 2>/dev/null)
        PUB6=$(curl -s --max-time 3 -6 https://ifconfig.me 2>/dev/null)
        echo "${PUB4}|${PUB6}" | tee "$CACHE"
    fi
}

ACTIVE=$(nmcli -t -f TYPE,DEVICE,STATE connection show --active 2>/dev/null | grep ":activated$" | head -1)
TYPE=$(echo "$ACTIVE" | cut -d: -f1)
DEVICE=$(echo "$ACTIVE" | cut -d: -f2)

if [ -z "$DEVICE" ]; then
    echo '{"text": "󰤭 No network", "tooltip": "Disconnected"}'
    exit 0
fi

IPS=$(get_public_ips)
PUB4=$(echo "$IPS" | cut -d'|' -f1)
PUB6=$(echo "$IPS" | cut -d'|' -f2)

TOOLTIP="IPv4: ${PUB4:-N/A}\nIPv6: ${PUB6:-N/A}"

case "$TYPE" in
    ethernet|802-3-ethernet)
        TEXT="󰈀 Ethernet"
        ;;
    wifi|802-11-wireless)
        SIGNAL=$(nmcli -t -f IN-USE,SIGNAL device wifi 2>/dev/null | grep '^\*' | cut -d: -f2)
        ESSID=$(nmcli -t -f ACTIVE,SSID device wifi 2>/dev/null | grep '^yes' | cut -d: -f2)
        if   [ "${SIGNAL:-0}" -ge 75 ]; then ICON="󰤨"
        elif [ "${SIGNAL:-0}" -ge 50 ]; then ICON="󰤥"
        elif [ "${SIGNAL:-0}" -ge 25 ]; then ICON="󰤢"
        else ICON="󰤟"
        fi
        TEXT="$ICON ${SIGNAL:-0}%"
        TOOLTIP="$ESSID\n$TOOLTIP"
        ;;
    *)
        TEXT="󰈀 $TYPE"
        ;;
esac

echo "{\"text\": \"$TEXT\", \"tooltip\": \"$TOOLTIP\"}"
