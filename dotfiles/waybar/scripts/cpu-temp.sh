#!/usr/bin/env bash
# custom/temperature: the CPU package temperature, e.g. {"text":"47°C",
# "tooltip":"CPU 47 °C · limit 105 °C\nNot throttled by heat since boot"},
# with "class" "warning" or "critical" when it runs hot.
#
# One process for as long as the bar runs (no "interval" in config.jsonc),
# like net-speed.sh: every INTERVAL it reads two sysfs files with bash
# builtins and sleeps in `read -t`, so a tick starts no process, and it
# prints only when the line changes.
#
# The sensor is the one the System card reads (card_system.py's
# CPU_SENSORS), found by hwmon driver and input label, whatever its hwmonN:
# Intel's coretemp "Package id 0"; AMD's k10temp Tdie where the chip has one
# (early Ryzens, whose Tctl reads 10-27 °C high), else Tctl; zenpower. With
# none it hides the module and waits, where waybar's own module would fall
# back to thermal_zone0 (here the virtual INT3400 zone, stuck at 20 °C).
#
# Warm and hot are 20 and 10 °C under the CPU's limit where the sensor
# reports one (coretemp's _crit, TjMax: 105 °C on the notebook, so 85 and
# 95 °C there), else 85 and 95 °C; the System card colours by the same
# limits. Within 10 °C of the limit the kernel counts a throttle as heat
# (therm_throt.c); its total since boot, which only Intel CPUs keep, is in
# the tooltip. A class holds until the reading is HYST under its
# threshold, so a reading that hovers around one doesn't flip the colour
# every tick.

INTERVAL=5  # s between samples
HYST=3      # °C
THROTTLED=/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_total_time_ms
# The tooltip's last line names what a right click does, which nothing on
# the bar shows (config.jsonc's mouse scheme): small, in subtext0
HINT="\\n<span size='small' foreground='#a6adc8'>Right-click: btop</span>"

# A pipe nobody writes to: `read -t` on it is a sleep without a fork, and a
# plain `read` on it waits forever.
exec {tick}<> <(:)

input= limit=
find_sensor() {  # sets input and limit (m°C, or empty)
    local driver labels want d f name lab
    for driver in coretemp k10temp zenpower; do
        case $driver in
            coretemp) labels=("Package id 0") ;;
            *) labels=(Tdie Tctl) ;;
        esac
        for d in /sys/class/hwmon/hwmon[0-9]*; do
            read -r name < "$d/name" || continue
            [[ $name == "$driver" ]] || continue
            input=$d/temp1_input  # unlabelled: the first input
            for want in "${labels[@]}"; do
                for f in "$d"/temp[0-9]*_label; do
                    read -r lab < "$f" && [[ $lab == "$want" ]] || continue
                    input=${f%_label}_input
                    break 2
                done
            done
            read -r limit < "${input%_input}_crit" || limit=
            return 0
        done
    done 2>/dev/null
    return 1
}

if ! find_sensor; then
    printf '%s\n' '{"text":""}'
    read -r -u "$tick"  # until the bar ends this script
    exit 0
fi

if [[ $limit =~ ^[0-9]+$ ]] && (( limit >= 80000 && limit <= 130000 )); then
    limit=$(( limit / 1000 ))
    warm=$(( limit - 20 )) hot=$(( limit - 10 ))
    of=" · limit $limit °C"
else
    warm=85 hot=95 of=
fi

class= shown=
while :; do
    read -r milli < "$input" || milli=
    if [[ $milli =~ ^-?[0-9]+$ ]]; then
        t=$(( (milli + 500) / 1000 ))
        # Up at the threshold, down HYST under it
        if (( t >= hot )) || { [[ $class == critical ]] && (( t > hot - HYST )); }; then
            class=critical
        elif (( t >= warm )) || { [[ $class ]] && (( t > warm - HYST )); }; then
            class=warning
        else
            class=
        fi
        # Intel only: an AMD CPU has no such counter, and then no line
        heat=
        if read -r ms < "$THROTTLED"; then
            if (( ms >= 60000 )); then
                heat="\\nThrottled by heat for $(( ms / 60000 )) min since boot"
            elif (( ms > 0 )); then
                heat="\\nThrottled by heat for $(( ms / 1000 )).$(( ms / 100 % 10 )) s since boot"
            else
                heat="\\nNot throttled by heat since boot"
            fi
        fi 2>/dev/null
        printf -v line '{"text":"%d°C","tooltip":"CPU %d °C%s%s%s"%s}' \
            "$t" "$t" "$of" "$heat" "$HINT" "${class:+,\"class\":\"$class\"}"
    else
        line='{"text":""}'  # the sensor went away
    fi
    if [[ $line != "$shown" ]]; then
        printf '%s\n' "$line" 2>/dev/null || exit 0  # the bar is gone
        shown=$line
    fi
    read -rt "$INTERVAL" -u "$tick"
done
