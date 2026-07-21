#!/usr/bin/env bash
# Waybar custom module for the Pomodoro timer.
#
# Reads the state file written by the popup, prints the current countdown as
# JSON, and — when a phase expires — fires a notification and transitions
# work -> break -> idle. Runs once per waybar poll (interval: 1), so no
# long-lived daemon is needed and it survives waybar restarts.

STATE="${XDG_RUNTIME_DIR:-/tmp}/pomodoro-state.json"

ICON_IDLE=$'\uf252'   # hourglass (nf-fa-hourglass_half)
ICON_WORK=$'\uf017'   # clock     (nf-fa-clock_o)
ICON_BREAK=$'\uf0f4'  # coffee    (nf-fa-coffee)

idle() {
    printf '{"text":"%s","tooltip":"Pomodoro — click to start","class":"idle"}\n' "$ICON_IDLE"
    exit 0
}

[ -f "$STATE" ] || idle

status=$(jq -r '.status // "idle"' "$STATE" 2>/dev/null)
[ "$status" = "running" ] || idle

phase=$(jq -r '.phase // "work"'   "$STATE" 2>/dev/null)
end=$(jq -r   '.end // 0'          "$STATE" 2>/dev/null)
work=$(jq -r  '.work // 60'        "$STATE" 2>/dev/null)
brk=$(jq -r   '.break // 10'       "$STATE" 2>/dev/null)

now=$(date +%s)
remaining=$(( end - now ))

if [ "$remaining" -gt 0 ]; then
    mm=$(( remaining / 60 ))
    ss=$(( remaining % 60 ))
    if [ "$phase" = "work" ]; then
        printf '{"text":"%s %02d:%02d","tooltip":"Focus — %02d:%02d left","class":"work"}\n' \
            "$ICON_WORK" "$mm" "$ss" "$mm" "$ss"
    else
        printf '{"text":"%s %02d:%02d","tooltip":"Break — %02d:%02d left","class":"break"}\n' \
            "$ICON_BREAK" "$mm" "$ss" "$mm" "$ss"
    fi
    exit 0
fi

# Phase expired: transition state (atomically) and notify exactly once.
if [ "$phase" = "work" ]; then
    newend=$(( now + brk * 60 ))
    jq -n --argjson end "$newend" --argjson work "$work" --argjson brk "$brk" \
        '{status:"running",phase:"break",end:$end,work:$work,break:$brk}' \
        > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
    notify-send -a Pomodoro -u normal -i alarm-symbolic \
        "Focus session complete " "Take a ${brk} minute break."
    printf '{"text":"%s %02d:00","tooltip":"Break — %02d:00 left","class":"break"}\n' \
        "$ICON_BREAK" "$brk" "$brk"
else
    jq -n --argjson work "$work" --argjson brk "$brk" \
        '{status:"idle",phase:"work",end:0,work:$work,break:$brk}' \
        > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
    notify-send -a Pomodoro -u critical -i alarm-symbolic \
        "Break over " "Ready for another session?"
    idle
fi
