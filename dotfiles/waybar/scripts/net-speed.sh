#!/usr/bin/env bash
# custom/net-speed: download and upload rate of the interface that holds the
# default route, e.g. {"text":"↓ 512K ↑ 1.2M","tooltip":"↓ 512 KiB/s · ↑ 1.2
# MiB/s\nwlp0s20f3 · 192.168.1.110"}.
#
# One process for as long as the bar runs (no "interval" in config.jsonc):
# every INTERVAL it reads /proc/net/route (on an IPv6-only network
# /proc/net/ipv6_route) and that interface's two byte counters in sysfs with
# bash builtins and sleeps in `read -t`, so a tick starts no process. `ip`
# runs only when the default route changes, for the tooltip's address, and
# `busctl` only at start and on the connectivity signal (Metered, below).
# Waybar ends it with SIGTERM (it also dies with the bar's thread); a write
# to a pipe nobody reads any more ends it too.
#
# Each rate is four characters, the unit included ("  0K", "512K", "1.2M",
# " 12M", "1.1G": KiB/s, MiB/s, GiB/s, spelled out in the tooltip). Every
# glyph of the bar's font is 9px wide, so the module keeps one width from
# idle to gigabit, and with no default route ("   –") too: nothing next to
# it moves. Under IDLE a rate shows as 0K, so background chatter doesn't
# change the text; both ways under it the module is dimmed (class idle).
# Classes: idle, metered, disconnected (no default route).
#
# Every line printed costs the bar a redraw, about twice the script's own
# CPU, so the text follows the rates only as far as it tells something: at
# once when a rate moves by more than BAND from what the bar shows or the
# link turns idle, busy, metered or disconnected; any other change after
# STALE samples. A steady download redraws the bar every 10 s, not every 2 s.
#
# Metered: while NetworkManager calls the connection metered (a profile
# marked so, or one it guesses is, such as an Android phone's hotspot; an
# iPhone's needs marking), traffic shows in peach (class metered; idle still
# dims it) and the tooltip says so. NetworkManager is asked at start and on
# RTMIN+9, the signal its dispatcher sends the bar's connectivity module
# whenever a link or a VPN comes or goes (modules/nixos/desktop.nix): waybar passes
# no signal on to a script that runs for good, so the dispatcher sends this
# script the signal itself. A profile marked metered while it is connected
# shows once a link next comes or goes.
#
# History, for the System card's Network line (card_system.py), which
# shows the last 3 minutes the moment it opens: every tick rewrites
# $XDG_RUNTIME_DIR/net-speed.<pid>.hist. One file per bar, as waybar runs
# this script once per monitor and a bar that comes with docking must not
# cut the panel bar's 3 minutes short; removed when the script ends.
#   Line 1 "IFACE SINCE RX_TOTAL TX_TOTAL METERED", the link: its interface,
#     the epoch second its totals count from, the bytes down and up since
#     then (each sample's bytes added up, so a counter that starts over
#     can't make them negative), "metered" or "-". "- 0 0 0 -" with no route.
#   Then a line "T RX TX" per sample, oldest first, at most HISTORY: the µs
#     since the epoch at its end, B/s down and up over it. None while there
#     is no default route, so a gap means no route or a suspend.
# The totals start over when another interface or gateway takes the route,
# or when the route comes back after more than RESET s away (a resume, a
# long outage): on a phone's hotspot they read "used since connecting".
# The file is written aside and linked into place, so the card reads a
# whole one or, for a few µs a tick, none.

INTERVAL=2  # s between samples
IDLE=10240  # B/s, 10 KiB/s: less shows as 0K; both ways less is idle
BAND=50     # % of the rate shown (of IDLE at least): a bigger move shows at once
STALE=5     # samples, 10 s: a smaller change shows after this many
HISTORY=90  # samples the history file keeps: 3 min
RESET=60    # s since the last tick with a route (a suspend, an outage): totals start over
# The tooltip's last line names what a right click does, which nothing on
# the bar shows (config.jsonc's mouse scheme): small, in subtext0
HINT="\\n<span size='small' foreground='#a6adc8'>Right-click: btop</span>"

# A pipe nobody writes to: `read -t` on it is a sleep without a fork.
exec {tick}<> <(:)

hist=${XDG_RUNTIME_DIR:-/tmp}/net-speed.$$.hist
tmp=${hist%.hist}.tmp  # not *.hist, which the card looks for
trap 'exit 0' TERM INT HUP  # exit, so that the EXIT trap runs
trap 'rm -f -- "$hist" "$tmp"' EXIT
# Bash has no rename, and `mv` would be a process a tick. Its loadable
# builtins ln and unlink (on BASH_LOADABLES_PATH, which nixpkgs' bash sets
# to its own lib/bash) put the new file in the old one's place without
# one; mv only where they are missing.
if enable -f ln ln 2>/dev/null && enable -f unlink unlink 2>/dev/null; then
    publish() { ln -f "$tmp" "$hist" && unlink "$tmp"; }
else
    publish() { mv -f "$tmp" "$hist"; }
fi

ask=1  # NetworkManager is asked at the next tick
trap 'ask=1' RTMIN+9

rate() {  # bytes/s -> four characters in REPLY, integers only
    local b=$1 t
    if (( b < IDLE )); then
        REPLY='  0K'
    elif (( b < 1023488 )); then         # under 999.5 KiB/s: whole KiB
        printf -v REPLY '%3dK' $(( (b + 512) / 1024 ))
    elif (( b < 10433332 )); then        # under 9.95 MiB/s: one decimal
        t=$(( (b * 10 + 524288) / 1048576 ))
        printf -v REPLY '%d.%dM' $(( t / 10 )) $(( t % 10 ))
    elif (( b < 1048051712 )); then      # under 999.5 MiB/s: whole MiB
        printf -v REPLY '%3dM' $(( (b + 524288) / 1048576 ))
    else
        t=$(( (b * 10 + 536870912) / 1073741824 ))
        printf -v REPLY '%d.%dG' $(( t / 10 )) $(( t % 10 ))
    fi
}

spelled() {  # a rate's four characters -> "512 KiB/s" in REPLY, for the tooltip
    local n=${1// /}
    if [[ $n == 0K ]]; then
        REPLY="under $(( IDLE / 1024 )) KiB/s"  # what 0K stands for
    else
        REPLY="${n%?} ${n: -1}iB/s"
    fi
}

moved() {  # rate, the rate the text shows: whether it moved by more than BAND
    local d=$(( $1 - $2 ))
    (( (d < 0 ? -d : d) * 100 > BAND * ($2 > IDLE ? $2 : IDLE) ))
}

default_route() {  # iface and gateway of the lowest-metric default route
    local name dest plen gateway metric flags best=
    iface= gw=
    # /proc/net/route is the main table, which is where NetworkManager puts it
    while read -r name dest gateway _ _ _ metric _; do
        if [[ $dest == 00000000 ]] && { [[ -z $best ]] || (( metric < best )); }; then
            iface=$name gw=$gateway best=$metric
        fi
    done < /proc/net/route
    [[ -n $iface ]] && return
    # No IPv4 default route: an IPv6-only network still has one here
    # (destination ::/0). Skip lo's unreachable route (RTF_REJECT, 0x200).
    while read -r dest plen _ _ gateway metric _ _ flags name; do
        [[ $plen == 00 && -z ${dest//0/} && $name != lo ]] || continue
        (( 16#$flags & 0x200 )) && continue
        if [[ -z $best ]] || (( 16#$metric < best )); then
            iface=$name gw=$gateway best=$(( 16#$metric ))
        fi
    done 2>/dev/null < /proc/net/ipv6_route  # absent with IPv6 off
}

metered() {  # NetworkManager's Metered -> meter: "metered" or "-"
    local reply
    reply=$(busctl --system --timeout=2 get-property org.freedesktop.NetworkManager \
        /org/freedesktop/NetworkManager org.freedesktop.NetworkManager Metered 2>/dev/null)
    # 1 yes, 3 guess-yes, which NetworkManager's docs say to take as yes;
    # 0 unknown, 2 no, 4 guess-no. No NetworkManager: no reply.
    [[ $reply == 'u 1' || $reply == 'u 3' ]] && meter=metered || meter=-
}

prev_if= prev_gw= prev_t= prev_rx= prev_tx= addr= shown= meter=-
text= cls=start base_rx=0 base_tx=0 age=0  # what the text shows, and since when
link_if= link_gw= since=0 tot_rx=0 tot_tx=0 seen=0  # whose totals; the last tick with a route
ring=() slot=0  # the history's samples in a circle: slot is the next, the oldest once full
while :; do
    if [[ $ask ]]; then
        ask=  # first, so a signal during the call asks again next tick
        metered
    fi
    default_route
    stats=/sys/class/net/$iface/statistics
    if [[ -z $iface ]] || ! { read -r rx < "$stats/rx_bytes" \
            && read -r tx < "$stats/tx_bytes"; } 2>/dev/null; then
        # No default route, or its interface has just gone. As wide as a
        # reading, so nothing next to it moves.
        text='↓    – ↑    –' cls=disconnected
        prev_if=
        header='- 0 0 0 -'
    else
        now=${EPOCHREALTIME//[!0-9]/}  # µs; LC_NUMERIC=de_DE writes "s,µs"
        rx_rate=0 tx_rate=0 drx=0 dtx=0
        if [[ $iface == "$prev_if" ]] && (( now > prev_t )); then
            drx=$(( rx - prev_rx )) dtx=$(( tx - prev_tx ))
            (( drx < 0 )) && drx=0  # the counters started over (a modem plugged in again)
            (( dtx < 0 )) && dtx=0
            rx_rate=$(( drx * 1000000 / (now - prev_t) ))
            tx_rate=$(( dtx * 1000000 / (now - prev_t) ))
            ring[slot]="$now $rx_rate $tx_rate"
            slot=$(( (slot + 1) % HISTORY ))
        fi
        if [[ $iface != "$prev_if" || $gw != "$prev_gw" ]]; then
            # The first global address, IPv4 before IPv6:
            # "N: dev inet A/len ..." or "N: dev inet6 A/len ..."
            addr=$(ip -o addr show dev "$iface" scope global)
            addr=${addr#*inet* } addr=${addr%%/*}
        fi
        if [[ $iface != "$link_if" || $gw != "$link_gw" ]] \
                || (( now - seen > RESET * 1000000 )); then
            # Another network, or this one after a gap: its totals from here
            link_if=$iface link_gw=$gw since=${now:0:-6} tot_rx=0 tot_tx=0
        else
            tot_rx=$(( tot_rx + drx )) tot_tx=$(( tot_tx + dtx ))
        fi
        seen=$now
        prev_if=$iface prev_gw=$gw prev_t=$now prev_rx=$rx prev_tx=$tx
        header="$iface $since $tot_rx $tot_tx $meter"

        if (( rx_rate < IDLE && tx_rate < IDLE )); then now_cls=idle
        elif [[ $meter == metered ]]; then now_cls=metered
        else now_cls=
        fi
        if [[ $now_cls != "$cls" ]] || (( ++age >= STALE )) \
                || moved "$rx_rate" "$base_rx" || moved "$tx_rate" "$base_tx"; then
            base_rx=$rx_rate base_tx=$tx_rate age=0 cls=$now_cls
            rate "$rx_rate"; down=$REPLY
            rate "$tx_rate"; up=$REPLY
            text="↓ $down ↑ $up"
        fi
    fi
    # The history, oldest sample first, also while there is no route: the
    # card takes a file rewritten within the last few seconds as a live bar's.
    printf '%s\n' "$header" "${ring[@]:slot}" "${ring[@]:0:slot}" > "$tmp" && publish

    # The tooltip is built only for a line that is printed, from what the
    # text shows, so it never disagrees with it.
    if [[ "$text|$cls|$iface|$addr|$meter" != "$shown" ]]; then
        if [[ $cls == disconnected ]]; then
            tip='No default route'
        else
            spelled "$down"; tip="↓ $REPLY · "
            spelled "$up"; tip+="↑ $REPLY\\n$iface${addr:+ · $addr}"
            [[ $meter == metered ]] && tip+=' · metered'
        fi
        printf '{"text":"%s","tooltip":"%s%s"%s}\n' "$text" "$tip" "$HINT" \
            "${cls:+,\"class\":\"$cls\"}" 2>/dev/null || exit 0  # the bar is gone
        shown="$text|$cls|$iface|$addr|$meter"
    fi
    read -rt "$INTERVAL" -u "$tick"
done
