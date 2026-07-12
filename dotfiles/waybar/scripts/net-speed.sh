#!/usr/bin/env bash

IFACE=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}')

if [ -z "$IFACE" ]; then
    echo '{"text":"󰛳 N/A","tooltip":"No active interface"}'
    exit 0
fi

read -r RX TX < <(awk -v i="${IFACE}:" '$1==i {print $2, $10}' /proc/net/dev)
NOW=$(date +%s.%N)

# Rate = counter delta since the previous invocation (state kept in /tmp)
STATE="/tmp/waybar-net-speed-${IFACE}"
RX_RATE=0
TX_RATE=0
if [ -f "$STATE" ]; then
    read -r P_NOW P_RX P_TX < "$STATE"
    RX_RATE=$(awk -v a="$RX" -v b="$P_RX" -v t1="$NOW" -v t0="$P_NOW" \
        'BEGIN{dt=t1-t0; r=(dt>0)?(a-b)/dt:0; if(r<0)r=0; print r}')
    TX_RATE=$(awk -v a="$TX" -v b="$P_TX" -v t1="$NOW" -v t0="$P_NOW" \
        'BEGIN{dt=t1-t0; r=(dt>0)?(a-b)/dt:0; if(r<0)r=0; print r}')
fi
echo "$NOW $RX $TX" > "$STATE"

fmt() {
    awk -v b="$1" 'BEGIN{ printf "%.1fM", b/1048576 }'
}

IP=$(ip -4 -o addr show dev "$IFACE" | awk '{print $4; exit}')
echo "{\"text\":\"↓ $(fmt "$RX_RATE") ↑ $(fmt "$TX_RATE")\",\"tooltip\":\"$IFACE · ${IP%%/*}\"}"
