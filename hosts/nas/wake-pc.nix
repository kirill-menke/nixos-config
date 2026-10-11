#
# `wake-pc` -- wake the suspended desktop from anywhere, via this box.
#
# The desktop arms Wake-on-LAN for magic packets only (`wol g`, see the
# wake-on-lan unit in hosts/pc/default.nix). A sleeping NIC has no IP stack:
# it cannot answer ARP and only pattern-matches raw frames for its own MAC
# repeated 16 times. So the packet has to originate on the LAN segment as a
# broadcast -- a phone on cellular cannot send it through Tailscale, because
# the PC is not on the tailnet and no router forwards broadcasts. This box is
# always on and on the LAN, so the phone SSHes here (or asks the NAS's Claude
# session) and runs `wake-pc`.
#
# Sent as UDP to port 9 at the subnet-directed broadcast 192.168.0.255, which
# the kernel routes out enp2s0 (the connected route) and puts on the wire with
# destination MAC ff:ff:ff:ff:ff:ff. Plain UDP needs no root, unlike
# ether-wake's raw 0x0842 frames. Three copies a second apart, since nothing
# acknowledges a magic packet.
#
# Afterwards it waits for the PC to answer ping, so the phone gets a yes/no.
# The PC's address is dynamic (AdGuard DHCP) and it publishes no mDNS name,
# so the address is looked up in this box's neighbour table by MAC; if the
# entry has been flushed the wait is skipped and only the send is reported.
#
{ pkgs, ... }:
let
  pcMac = "9c:6b:00:a3:38:bc";
  lanIf = "enp2s0";
  lanBroadcast = "192.168.0.255";

  wakePc = pkgs.writeShellApplication {
    name = "wake-pc";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.gawk
      pkgs.iproute2
      pkgs.wakeonlan
    ];
    # ping comes from /run/wrappers (setcap), which stays on PATH.
    text = ''
      for _ in 1 2 3; do
        wakeonlan -q -i ${lanBroadcast} -p 9 ${pcMac}
        sleep 1
      done
      echo "magic packet sent to ${pcMac} via ${lanBroadcast}"

      ip=$(ip -4 neigh show dev ${lanIf} | awk -v mac=${pcMac} '$3 == mac { print $1; exit }')
      if [ -z "$ip" ]; then
        echo "PC address unknown (no neighbour entry); not waiting"
        exit 0
      fi

      while [ "$SECONDS" -lt 90 ]; do
        if ping -c1 -W1 "$ip" >/dev/null 2>&1; then
          echo "PC is up at $ip after ''${SECONDS}s"
          exit 0
        fi
        sleep 1
      done
      echo "PC at $ip did not answer within ~90s" >&2
      exit 1
    '';
  };
in
{
  environment.systemPackages = [ wakePc ];
}
