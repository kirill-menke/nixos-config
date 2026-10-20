#!/usr/bin/env bash
# custom/memory: used memory, e.g. {"text":"󰍛 86%","tooltip":"Memory: 13.0 /
# 15.1 GiB used · 2.1 GiB available\nSwap: …\nPressure: …","class":"warning"},
# coloured by whether programs run short of memory, not by how full it is.
#
# Used is what isn't available (MemAvailable), as the System card counts it.
# With zram swap a full RAM still has room in compressed swap, so used % on
# its own warns early; what tells that the machine is slowing down is the
# kernel's pressure stall information: the share of the last 10 s in which
# some (or all) tasks waited for memory. Thresholds after Android's lmkd,
# which acts on the same counters: 100 ms of every 1 s with some tasks
# waiting is its "medium" pressure, 70 ms with all of them its "critical".
# So: peach (warning) from 80 % used or 10 % "some"; red (critical) from
# 7 % "full", or from 92 % used once swap is 80 % full too (or there is
# none). Swap there is the zram where there is any: a disk swap file behind
# it (the hibernate file) is slow, a last resort rather than room. The
# System card colours its memory row by the same rule.
#
# One process for as long as the bar runs (no "interval" in config.jsonc):
# every INTERVAL it reads the head of /proc/meminfo, /proc/pressure/memory,
# /proc/swaps and zram's mm_stat with bash builtins and sleeps in `read -t`,
# so a tick starts no process. A line is printed only when it differs from
# the last one, so a steady state doesn't wake the bar.

INTERVAL=5      # s between samples, the built-in module's interval
WARN_USED=80    # % used: peach
CRIT_USED=92    # % used: red once swap is SWAP_FULL % full too (or there is none)
SWAP_FULL=80    # %
SOME_WARN=1000  # pressure "some" avg10, in 1/100 %: 10 %, peach
FULL_CRIT=700   # pressure "full" avg10, in 1/100 %: 7 %, red

GLYPH='󰍛'  # md-memory, as the bar has always shown it
# The tooltip's last line names what a right click does, which nothing on
# the bar shows (config.jsonc's mouse scheme): small, in subtext0
HINT="\\n<span size='small' foreground='#a6adc8'>Right-click: btop</span>"

# A pipe nobody writes to: `read -t` on it is a sleep without a fork.
exec {tick}<> <(:)

# The tooltip's figures are rounded as the System card's size() and of()
# round them, so the two never disagree.
size() {  # KiB -> "1.7 GiB", "12 GiB", "795 MiB" in REPLY: a decimal only under 10
    local kib=$1 unit=KiB scale=1
    if (( kib >= 1048576 )); then unit=GiB scale=1048576
    elif (( kib >= 1024 )); then unit=MiB scale=1024
    fi
    local tenths=$(( (kib * 10 + scale / 2) / scale ))
    if (( tenths < 100 )); then
        printf -v REPLY '%d.%d %s' $(( tenths / 10 )) $(( tenths % 10 )) "$unit"
    else
        printf -v REPLY '%d %s' $(( (kib + scale / 2) / scale )) "$unit"
    fi
}

of() {  # used, total (KiB) -> "7.9 / 15.1 GiB" in REPLY: no decimal from 100 GiB
    local u t
    if (( $2 < 104857600 )); then  # 100 GiB
        u=$(( ($1 * 10 + 524288) / 1048576 )) t=$(( ($2 * 10 + 524288) / 1048576 ))
        printf -v REPLY '%d.%d / %d.%d GiB' $(( u / 10 )) $(( u % 10 )) $(( t / 10 )) $(( t % 10 ))
    else
        printf -v REPLY '%d / %d GiB' $(( ($1 + 524288) / 1048576 )) $(( ($2 + 524288) / 1048576 ))
    fi
}

hundredths() {  # "avg10=12.34" -> 1234 in REPLY
    local v=${1#*=}
    v=${v/./}
    REPLY=$(( 10#$v ))
}

shown=
while :; do
    total=0 avail=0
    mapfile -t -n 3 rows < /proc/meminfo  # MemTotal, MemFree, MemAvailable
    for row in "${rows[@]}"; do
        case $row in
            MemTotal:*) total=${row//[!0-9]/} ;;
            MemAvailable:*) avail=${row//[!0-9]/} ;;
        esac
    done

    some=0 full=0  # no PSI (booted with psi=0): no pressure to show
    if [[ -r /proc/pressure/memory ]]; then
        while read -r kind avg10 _; do
            hundredths "$avg10"
            [[ $kind == some ]] && some=$REPLY || full=$REPLY
        done < /proc/pressure/memory
    fi

    # Swap by kind, in KiB: zram, and what is on disk
    zram_size=0 zram_used=0 zram_ram=0 disk_size=0 disk_used=0
    while read -r dev _ kib used _; do
        case $dev in
            /dev/zram*)
                zram_size=$(( zram_size + kib )) zram_used=$(( zram_used + used ))
                # zram keeps it compressed in RAM: what that takes there is
                # mm_stat's third field, in bytes
                read -r _ _ cost _ 2>/dev/null < "/sys/block/${dev#/dev/}/mm_stat" &&
                    zram_ram=$(( zram_ram + cost / 1024 ))
                ;;
            /*) disk_size=$(( disk_size + kib )) disk_used=$(( disk_used + used )) ;;
        esac
    done < /proc/swaps
    if (( zram_size )); then
        swap_pct=$(( 100 * zram_used / zram_size ))
    else
        swap_pct=$(( disk_size ? 100 * disk_used / disk_size : 100 ))  # none: full
    fi

    used=$(( total - avail ))
    pct=$(( total ? 100 * used / total : 0 ))  # truncated, as waybar's {percentage}
    if (( full >= FULL_CRIT )) || (( pct >= CRIT_USED && swap_pct >= SWAP_FULL )); then
        class=critical
    elif (( pct >= WARN_USED || some >= SOME_WARN )); then
        class=warning
    else
        class=
    fi

    of "$used" "$total"; tip="Memory: $REPLY used"
    size "$avail"; tip+=" · $REPLY available"
    if (( zram_size )); then
        if (( zram_used >= 1024 )); then
            size "$zram_used"; tip+="\\nSwap: $REPLY in zram"
            size "$zram_size"; tip+=" of $REPLY"
            (( zram_ram )) && { size "$zram_ram"; tip+=" · takes $REPLY of RAM"; }
        else
            size "$zram_size"; tip+="\\nSwap: $REPLY zram, empty"
        fi
        # The disk file only once it holds something
        (( disk_used >= 1024 )) && { size "$disk_used"; tip+=" · $REPLY on disk"; }
    elif (( disk_size )); then
        if (( disk_used >= 1024 )); then
            size "$disk_used"; tip+="\\nSwap: $REPLY used"
            size "$disk_size"; tip+=" of $REPLY"
        else
            size "$disk_size"; tip+="\\nSwap: $REPLY, unused"
        fi
    else
        tip+="\\nNo swap"
    fi
    if (( some >= 50 )); then  # rounds to 1 % or more
        tip+="\\nPressure: programs waited for memory $(( (some + 50) / 100 ))% of the last 10 s"
        (( full >= 50 )) && tip+=" (all of them $(( (full + 50) / 100 ))%)"
    else
        tip+="\\nNo memory pressure"
    fi
    printf -v line '{"text":"%s %d%%","tooltip":"%s%s"%s}' \
        "$GLYPH" "$pct" "$tip" "$HINT" "${class:+,\"class\":\"$class\"}"

    if [[ $line != "$shown" ]]; then
        printf '%s\n' "$line" 2>/dev/null || exit 0  # the bar is gone
        shown=$line
    fi
    read -rt "$INTERVAL" -u "$tick"
done
