#!/usr/bin/env bash

CACHE="/tmp/waybar-weather"
CACHE_TTL=600  # refresh every 10 minutes

get_weather() {
    if [ -f "$CACHE" ] && [ $(( $(date +%s) - $(stat -c %Y "$CACHE") )) -lt $CACHE_TTL ]; then
        cat "$CACHE"
    else
        curl -s --max-time 5 \
            "https://api.open-meteo.com/v1/forecast?latitude=47.977&longitude=12.472&current=temperature_2m,weather_code&temperature_unit=celsius&wind_speed_unit=kmh" \
            | tee "$CACHE"
    fi
}

DATA=$(get_weather)
TEMP=$(echo "$DATA" | grep -oP '"temperature_2m":\K[0-9.-]+')
CODE=$(echo "$DATA" | grep -oP '"weather_code":\K[0-9]+')

case "$CODE" in
    0)           ICON="󰖙" DESC="Clear sky" ;;
    1)           ICON="󰖕" DESC="Mostly clear" ;;
    2)           ICON="󰖕" DESC="Partly cloudy" ;;
    3)           ICON="󰖔" DESC="Overcast" ;;
    45|48)       ICON="󰖑" DESC="Foggy" ;;
    51|53|55)    ICON="󰖗" DESC="Drizzle" ;;
    61|63|65)    ICON="󰖗" DESC="Rain" ;;
    71|73|75)    ICON="󰖘" DESC="Snow" ;;
    77)          ICON="󰖘" DESC="Snow grains" ;;
    80|81|82)    ICON="󰖗" DESC="Rain showers" ;;
    85|86)       ICON="󰖘" DESC="Snow showers" ;;
    95)          ICON="󰖓" DESC="Thunderstorm" ;;
    96|99)       ICON="󰖓" DESC="Thunderstorm with hail" ;;
    *)           ICON="󰖐" DESC="Unknown" ;;
esac

echo "{\"text\": \"$ICON ${TEMP}°C\", \"tooltip\": \"$DESC\"}"
