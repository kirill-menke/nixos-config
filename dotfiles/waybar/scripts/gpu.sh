#!/usr/bin/env bash
# The GPU module, one script for as long as the bar runs (config.jsonc gives
# it no "interval"). It works out once which GPU it can read and samples it
# every INTERVAL s, printing a line only when it changes. No sample starts a
# process:
# - NVIDIA: one `nvidia-smi --loop` for as long as the script runs, whose
#   lines it reads as they come, as the System card does.
# - AMD: amdgpu's own load figure and its sensors, in sysfs.
# - Intel (i915 or xe; the notebook): the share of the time the render GT
#   spent awake, out of its idle state RC6, from the world-readable sysfs
#   files the System card measures it by. intel_gpu_top's busy figures would
#   need CAP_PERFMON (perf_event_paranoid is 2). Meteor Lake and later
#   decode and encode video on a GT of their own, the media GT, which only
#   the tooltip shows: video work leaves the render GT's figure as it is.
#   While the render GT works (SLOW_FROM % or more) at a lower clock than it
#   asks for, the tooltip also says what holds it back, from the reasons
#   the driver reads off the power controller: heat, a power limit (PL1,
#   the sustained one, is shared with the CPU), the board's PROCHOT, the
#   voltage regulator. Only then: a reason can be set while the GT idles
#   (the media GT's were, at 1 % video), where a slower clock costs nothing.
# Where it can read none it hides the module and only waits.
#
# The figure is two characters wide even under 10 %: every glyph of the
# bar's font is as wide as a digit, so the pill doesn't change width when
# an integrated GPU's few percent at rest turn into tens.

INTERVAL=5
# % busy from which the Intel tooltip says why the render GT runs slower
# than it asks; under it the GT has too little to do for the clock to matter
SLOW_FROM=10
# °C from which a dedicated GPU shows the hot glyph in peach (warning), then
# in red (critical): the System card's GPU_HEAT
WARM=80 HOT=90

# A pipe nobody writes to: `read -t` on it is a sleep without a fork, and a
# plain `read` on it waits forever.
exec {tick}<> <(:)

HIDDEN='{"text": ""}'
HOT_ICON=󰢮
ICON=󰾲
# The tooltip's last line names what a right click does, which nothing on
# the bar shows (config.jsonc's mouse scheme): small, in subtext0
HINT="\\n<span size='small' foreground='#a6adc8'>Right-click: nvtop</span>"

shown=
emit() {  # print the module line $1 if it differs from the one shown
    [[ $1 == "$shown" ]] && return
    printf '%s\n' "$1" 2>/dev/null || exit 0  # the bar is gone
    shown=$1
}

gib() {  # MiB -> "2.1" in $REPLY. Integer maths: printf's %f would follow
    # LC_NUMERIC, which is German (a comma) in the bar's environment.
    local tenths=$(( ($1 * 10 + 512) / 1024 ))
    REPLY="$(( tenths / 10 )).$(( tenths % 10 ))"
}

num() {  # the leading whole number of $1 in $REPLY: "45.12" -> 45, "[N/A]" -> ""
    [[ $1 =~ ^\ *([0-9]+) ]] && REPLY=${BASH_REMATCH[1]} || REPLY=
}

join() {  # the arguments, " · " between them, in $REPLY
    REPLY=
    local bit
    for bit; do REPLY+=${REPLY:+ · }$bit; done
}

line() {  # icon, usage %, °C or "", tooltip[, class] -> the module line, emitted
    local text
    printf -v text '%s %2d%%' "$1" "$2"
    [[ $3 ]] && text+=" $3°C"
    emit "{\"text\":\"$text\",\"tooltip\":\"$4$HINT\"${5:+,\"class\":\"$5\"}}"
}

dedicated() {  # usage %, °C or "", tooltip -> a dedicated GPU's module line
    local icon=$ICON class=
    if [[ $2 ]]; then
        if (( $2 >= HOT )); then class=critical
        elif (( $2 >= WARM )); then class=warning; fi
        [[ $class ]] && icon=$HOT_ICON
    fi
    line "$icon" "$1" "$2" "$3" "$class"
}

# --- NVIDIA ------------------------------------------------------------------

NVIDIA_QUERY=utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw,fan.speed,clocks.gr,name

run_nvidia() {
    local smi util temp used total power fan clock name tip
    while :; do
        # The first GPU, a line every INTERVAL s. Line-buffered by stdbuf, as
        # a pipe could leave nvidia-smi's lines to stdio's block buffering.
        # Waybar ignores SIGPIPE and so would nvidia-smi: with the default
        # back, it ends at its next line once this script is gone.
        exec {smi}< <(exec env --default-signal=PIPE stdbuf -oL nvidia-smi -i 0 \
            --loop="$INTERVAL" --format=csv,noheader,nounits \
            --query-gpu="$NVIDIA_QUERY" 2>&1)
        # Split at commas only: an unsupported field reads "[Not Supported]".
        while IFS=, read -r -u "$smi" util temp used total power fan clock name; do
            num "$util"
            [[ $REPLY ]] || continue  # no reading: something nvidia-smi says
            util=$REPLY
            num "$clock"
            name=${name# }
            tip="${name#NVIDIA }\\nUsage $util%${REPLY:+ · $REPLY MHz}"
            num "$used"; used=$REPLY
            num "$total"; total=$REPLY
            if [[ $used && $total ]]; then
                gib "$used"; used=$REPLY
                gib "$total"
                tip+="\\nVRAM $used / $REPLY GiB"
            fi
            num "$temp"; temp=$REPLY
            num "$power"; power=${REPLY:+$REPLY W}
            num "$fan"; fan=${REPLY:+fan $REPLY%}
            join ${temp:+"$temp °C"} ${power:+"$power"} ${fan:+"$fan"}
            tip+=${REPLY:+\\n$REPLY}
            dedicated "$util" "$temp" "$tip"
        done
        exec {smi}<&-
        # nvidia-smi has quit: no driver, or one that no longer matches its
        # library after an update, until the next boot. Hidden, and another
        # try in a minute rather than a process every few seconds.
        emit "$HIDDEN"
        read -rt 60 -u "$tick"
    done
}

# --- AMD ---------------------------------------------------------------------

find_amd() {  # the first amdgpu card: its device directory and hwmon
    local card
    for card in /sys/class/drm/card[0-9]*; do
        [[ ${card##*/} == *-* ]] && continue  # card1-DP-1: a connector
        [[ -r $card/device/gpu_busy_percent ]] || continue
        dev=$card/device
        hwmon=
        for hwmon in "$dev"/hwmon/hwmon*; do break; done
        return 0
    done
    return 1
}

sample_amd() {
    local state busy temp= power= used= total= mhz= tip
    read -r state 2>/dev/null < "$dev/power/runtime_status"
    if [[ $state == suspended ]]; then  # idle anyway, and a read might wake it
        dedicated 0 "" "AMD graphics\\nAsleep"
        return
    fi
    if ! read -r busy 2>/dev/null < "$dev/gpu_busy_percent"; then
        emit "$HIDDEN"
        return
    fi
    # Edge temperature, average power (on an APU the whole chip's) and the
    # graphics clock, where the card reports them
    read -r temp 2>/dev/null < "$hwmon/temp1_input" && temp=$(( temp / 1000 ))
    read -r power 2>/dev/null < "$hwmon/power1_average" \
        || read -r power 2>/dev/null < "$hwmon/power1_input"
    read -r mhz 2>/dev/null < "$hwmon/freq1_input" && mhz=$(( mhz / 1000000 ))
    read -r used 2>/dev/null < "$dev/mem_info_vram_used"
    read -r total 2>/dev/null < "$dev/mem_info_vram_total"
    tip="AMD graphics\\nUsage $busy%${mhz:+ · $mhz MHz}"
    if [[ $used && $total ]]; then
        gib $(( used >> 20 )); used=$REPLY
        gib $(( total >> 20 ))
        tip+="\\nVRAM $used / $REPLY GiB"
    fi
    join ${temp:+"$temp °C"} ${power:+"$(( power / 1000000 )) W"}
    tip+=${REPLY:+\\n$REPLY}
    dedicated "$busy" "$temp" "$tip"
}

# --- Intel -------------------------------------------------------------------

# What holds the render GT's clock down, in the order the tooltip names it:
# i915's and xe's throttle reasons, the power controller's limit reasons.
REASONS=(thermal ratl prochot vr_thermalert vr_tdc pl1 pl2 pl4)
declare -A REASON=(
    [thermal]="heat" [ratl]="heat"  # RATL: a running average of the heat
    [prochot]="the board's PROCHOT signal"
    [vr_thermalert]="voltage regulator heat"
    [vr_tdc]="the voltage regulator's current limit"
    [pl1]="the sustained power limit (PL1)"
    [pl2]="the short-term power limit (PL2)"
    [pl4]="the peak power limit (PL4)"
)

find_intel() {  # the render GT's idle counter (ms), clock (MHz) and throttle
    # files, the media GT's idle counter
    local card gt r
    for card in /sys/class/drm/card[0-9]*; do
        [[ ${card##*/} == *-* ]] && continue  # card1-eDP-1: a connector
        if [[ -r $card/gt/gt0/rc6_residency_ms ]]; then  # i915
            gt=$card/gt
            render=$gt/gt0/rc6_residency_ms clock=$gt/gt0/rps_cur_freq_mhz
            media=$gt/gt1/rc6_residency_ms
            actual=$gt/gt0/rps_act_freq_mhz
            status=$gt/gt0/throttle_reason_status reason=$gt/gt0/throttle_reason_
        elif [[ -r $card/device/tile0/gt0/gtidle/idle_residency_ms ]]; then  # xe
            gt=$card/device/tile0
            render=$gt/gt0/gtidle/idle_residency_ms clock=$gt/gt0/freq0/cur_freq
            media=$gt/gt1/gtidle/idle_residency_ms
            actual=$gt/gt0/freq0/act_freq
            status=$gt/gt0/freq0/throttle/status reason=$gt/gt0/freq0/throttle/reason_
        else
            continue
        fi
        [[ -r $media ]] || media=  # before Meteor Lake video runs on the one GT
        runtime=$card/device/power/runtime_status
        # The reasons this kernel reports: an older one has no status file,
        # so no such line; a newer xe lists only those its platform has.
        held=()
        if [[ -r $status && -r $actual ]]; then
            for r in "${REASONS[@]}"; do [[ -r $reason$r ]] && held+=("$r"); done
        else
            status=
        fi
        return 0
    done
    return 1
}

slowed() {  # the render GT's requested MHz -> "Slowed to 300 MHz by heat" in
    # $REPLY; false while it runs at what it asks. Read only while the GT
    # works: a read wakes the GPU if it is asleep.
    local on mhz r last= why=() i
    read -r on < "$status"
    (( on )) || return 1
    read -r mhz < "$actual"
    (( mhz > 0 && mhz < $1 )) || return 1
    for r in "${held[@]}"; do
        read -r on < "$reason$r"
        if (( on )) && [[ ${REASON[$r]} != "$last" ]]; then  # heat once, not twice
            last=${REASON[$r]}
            why+=("$last")
        fi
    done
    REPLY="Slowed to $mhz MHz"
    for (( i = 0; i < ${#why[@]}; i++ )); do
        if (( i == 0 )); then REPLY+=" by "
        elif (( i == ${#why[@]} - 1 )); then REPLY+=" and "
        else REPLY+=", "
        fi
        REPLY+=${why[i]}
    done
}

busy() {  # idle ms now, idle ms then, window ms -> % of the window awake, in $REPLY
    REPLY=$(( 100 - (($1 - $2) * 100 + $3 / 2) / $3 ))
    (( REPLY < 0 )) && REPLY=0
    (( REPLY > 100 )) && REPLY=100
}

at= idle0= media0=
sample_intel() {
    local now state idle idle_media= window mhz= render_busy video= tip
    read -r now _ < /proc/uptime
    now=$(( 10#${now/./} ))  # in hundredths of a second, suspend included
    read -r state < "$runtime"
    if [[ $state == suspended ]]; then  # idle anyway, and a read would wake it
        at=
        line "$ICON" 0 "" "Intel Graphics\\nAsleep"
        return
    fi
    read -r idle < "$render"
    [[ $media ]] && read -r idle_media < "$media"
    window=$(( (now - at) * 10 ))
    # A window much longer than INTERVAL spans a suspend, across which the
    # counters don't add up: start a new one.
    if [[ $at ]] && (( window > 0 && window <= 3000 * INTERVAL )); then
        busy "$idle" "$idle0" "$window"; render_busy=$REPLY
        [[ $media ]] && { busy "$idle_media" "$media0" "$window"; video=$REPLY; }
        read -r mhz < "$clock"
        (( mhz )) || mhz=  # 0 while the GT has nothing to do (display off)
        tip="Intel Graphics\\nRendering $render_busy%${mhz:+ · $mhz MHz}"
        if (( render_busy >= SLOW_FROM )) && [[ $status && $mhz ]] && slowed "$mhz"; then
            tip+="\\n$REPLY"
        fi
        [[ $video ]] && tip+="\\nVideo $video%"
        line "$ICON" "$render_busy" "" "$tip"
    fi
    at=$now idle0=$idle media0=$idle_media
}

# --- Which GPU ---------------------------------------------------------------

# NVIDIA's only where its driver is loaded, so a machine with nvidia-smi but
# without the driver doesn't start it at all.
if [[ -e /proc/driver/nvidia/version ]] && command -v nvidia-smi &>/dev/null; then
    run_nvidia
elif find_amd; then
    sample=sample_amd
elif find_intel; then
    sample=sample_intel
    $sample                  # the first window's start,
    read -rt 1 -u "$tick"    # so a figure comes after a second, not INTERVAL
else
    # Nothing to read on this machine: hide the module and wait until the bar
    # ends this script. Exiting instead would make waybar log the module as
    # "stopped unexpectedly".
    printf '%s\n' "$HIDDEN"
    read -r -u "$tick"
    exit 0
fi

while :; do
    "$sample"
    read -rt "$INTERVAL" -u "$tick"
done
