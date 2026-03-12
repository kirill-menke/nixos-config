#!/usr/bin/env bash

IFACE=$(nmcli -t -f TYPE,DEVICE,STATE connection show --active 2>/dev/null | grep ":activated$" | head -1 | cut -d: -f2)

if [ -z "$IFACE" ]; then
    echo '{"text": "󰛳 N/A", "tooltip": "No active interface"}'
    exit 0
fi

get_bytes() {
    awk "/$IFACE:/{gsub(/$IFACE:/, \"\"); print \$1, \$9}" /proc/net/dev
}

B1=($(get_bytes)); sleep 1; B2=($(get_bytes))
RX=$(( B2[0] - B1[0] ))
TX=$(( B2[1] - B1[1] ))

fmt() {
    awk "BEGIN{printf \"%.1f\", $1/1048576}"
}

RX_STR=$(fmt $RX)
TX_STR=$(fmt $TX)

echo "{\"text\":\"󰛳  $TX_STR | $RX_STR MB/s\",\"tooltip\":\"Interface: $IFACE\\nUpload: ${TX_STR} MB/s\\nDownload: ${RX_STR} MB/s\"}"
