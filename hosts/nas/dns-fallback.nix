#
# DNS fallback for the LAN: if AdGuard Home stops answering, the NAS keeps
# answering DNS on the same address -- straight from Cloudflare, unfiltered --
# until AdGuard is healthy again.
#
# Why not hand clients a second DNS server (e.g. 1.1.1.1) via DHCP: clients
# don't treat the second server as a fallback. Android, iOS, Windows and glibc
# race or rotate between them, so a share of every device's lookups would skip
# the filter all the time. Instead there is only ever one DNS server on the LAN
# (this box, port 53) and the failover happens behind it:
#
#   dns-watchdog (every 10 s) asks 127.0.0.1 for a domain AdGuard blocks. That
#   is answered from the filter list without any upstream, so an internet outage
#   never looks like "AdGuard down". Two misses in a row (~20 s) -> restart
#   adguardhome once; still silent -> start dns-fallback, a plain dnsmasq
#   forwarder that Conflicts= with adguardhome and takes over port 53. While on
#   the fallback, every 10 min: start adguardhome again (which stops the
#   fallback), give it 15 s, keep it if it answers, else back to the fallback.
#
# Scope: this covers adguardhome crashing, hanging, or failing to start (a bad
# config after a rebuild). If the whole NAS is down there is no DNS -- and no
# DHCP -- on the LAN either; that was already the case (see adguard.nix).
# DHCP is AdGuard's too, so it pauses while the fallback runs; leases last 24 h.
#
{ pkgs, ... }:
let
  # Blocked by a user rule in adguard.nix (answered locally, no upstream) and
  # left out of AdGuard's query log and statistics there.
  probe = "dns-watchdog.invalid";
  watchdog = pkgs.writeShellScript "dns-watchdog" ''
    set -u
    PATH=${pkgs.lib.makeBinPath [ pkgs.bind.host pkgs.systemd pkgs.coreutils pkgs.gnugrep ]}
    state=/run/dns-watchdog; mkdir -p $state
    answers() { host -W 2 -t A ${probe} 127.0.0.1 >/dev/null 2>&1; }
    count() { cat $state/$1 2>/dev/null || echo 0; }
    set_count() { echo "$2" > $state/$1; }

    if systemctl -q is-active dns-fallback.service; then
      n=$(( $(count retry) + 1 ))
      if [ "$n" -lt 60 ]; then set_count retry $n; exit 0; fi  # 60 x 10 s = 10 min
      set_count retry 0
      echo "fallback active: trying adguardhome again"
      systemctl start adguardhome.service   # Conflicts= stops dns-fallback
      sleep 15
      if answers; then echo "adguardhome answers again, fallback off"; set_count fails 0; exit 0; fi
      echo "adguardhome still silent, back to fallback"
      systemctl start dns-fallback.service
      exit 0
    fi

    if answers; then set_count fails 0; exit 0; fi
    n=$(( $(count fails) + 1 )); set_count fails $n
    echo "adguardhome did not answer ($n/2)"
    [ "$n" -lt 2 ] && exit 0
    if [ "$n" -eq 2 ]; then
      echo "restarting adguardhome"
      systemctl restart adguardhome.service
      sleep 10
      if answers; then echo "adguardhome recovered after restart"; set_count fails 0; exit 0; fi
    fi
    echo "adguardhome down: switching LAN DNS to the Cloudflare fallback"
    set_count retry 0
    systemctl start dns-fallback.service
  '';
in
{
  systemd.services.dns-fallback = {
    description = "Fallback LAN DNS (dnsmasq -> Cloudflare) while AdGuard Home is down";
    conflicts = [ "adguardhome.service" ];
    after = [ "network-online.target" ];
    serviceConfig = {
      ExecStart = builtins.concatStringsSep " " [
        "${pkgs.dnsmasq}/bin/dnsmasq --keep-in-foreground"
        "--no-resolv --no-hosts --no-poll --strict-order"
        "--server=1.1.1.1 --server=1.0.0.1 --server=9.9.9.9"
        "--port=53 --cache-size=2000 --user=nobody --group=nogroup"
        "--pid-file=/run/dns-fallback.pid"
      ];
      Restart = "on-failure";
    };
  };

  systemd.services.dns-watchdog = {
    description = "Fail LAN DNS over to Cloudflare when AdGuard Home stops answering";
    after = [ "adguardhome.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = watchdog;
    };
  };

  systemd.timers.dns-watchdog = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitActiveSec = "10s";
      AccuracySec = "1s";
    };
  };
}
