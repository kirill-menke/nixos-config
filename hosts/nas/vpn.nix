#
# ProtonVPN confinement for qBittorrent.
#
# Why a network namespace rather than a host-wide tunnel or per-uid policy
# routing: qBittorrent is the ONLY thing that should exit via the VPN --
# Jellyfin, the *arrs, AdGuard, Tailscale and leetx must keep using the real
# uplink. Confining the one service to a netns whose only route out is the
# WireGuard interface gets exactly that, and is a kill switch by construction:
# if the tunnel is down there is no other route, so announces and peer traffic
# stop instead of leaking. Trackers, DHT and peers only ever see Proton's exit
# IP.
#
# What this deliberately does NOT hide: Prowlarr/FlareSolverr scraping the
# indexer *website* still happens from the real IP. That is ordinary HTTPS,
# not torrenting; confining Prowlarr too is possible later but means
# repointing Sonarr/Radarr/leetx at a mapped Prowlarr address as well.
#
# The WireGuard config is a secret (it contains the private key) and follows
# the same out-of-band pattern as the other secrets on this box. Generate it at
# account.protonvpn.com -> Downloads -> WireGuard configuration: pick a P2P
# server and enable the NAT-PMP (port forwarding) toggle -- proton-natpmp
# below depends on it. Then, on the NAS:
#
#   install -m600 <downloaded>.conf /var/lib/nixos-secrets/protonvpn-wg.conf
#
# Without it the proton-* namespace units fail to start -- deliberate and
# visible, same as leetx-api's environmentFile.
#
# Inbound peers arrive via Proton's NAT-PMP (proton-natpmp below). Being
# connectable matters here because the swarms this box actually leeches from
# are tiny -- measured 2026-08, the active grabs had ~6-seed swarms of which
# only 2 accepted our outbound connections; every NATed seed we cannot dial
# is one that can now dial us. Inbound only ever arrives through the tunnel,
# on whatever port Proton assigns: the host firewall keeps the torrenting
# port closed and the router forward for 51413 stays dead weight -- remove it.
#
{
  inputs,
  config,
  pkgs,
  ...
}:
{
  imports = [ inputs.vpn-confinement.nixosModules.default ];

  # Namespace names are capped at 7 characters (they become interface names).
  vpnNamespaces.proton = {
    enable = true;
    wireguardConfigFile = "${config.nas.secretsDir}/protonvpn-wg.conf";

    # Non-connected subnets whose traffic into the namespace needs a return
    # route via the bridge: only the tailnet. Host-local processes (Sonarr,
    # Radarr, leetx-api) talk to 192.168.15.1 over the veth's own connected
    # /24 and must NOT be listed -- the module turns every entry into
    # `ip route add <entry> via 192.168.15.5` inside the namespace, and the
    # veth /24 collides with the connected route ("RTNETLINK answers: File
    # exists", proton.service fails). The namespace-side ACCEPT for the
    # mapped port below covers them regardless of source.
    accessibleFrom = [
      "100.64.0.0/10"
    ];

    # Traffic hitting the HOST's addresses on 8080 is routed to the namespace,
    # which is what keeps `http://nas:8080` working over the tailnet exactly
    # as before. Host-local clients skip this and use 192.168.15.1:8080
    # directly (prerouting DNAT does not apply to host-originated traffic).
    portMappings = [
      {
        from = 8080;
        to = 8080;
        protocol = "tcp";
      }
    ];

    # Proton's forwarded port is dynamic (the gateway assigns it), so it
    # cannot be opened statically here -- proton-natpmp maintains the
    # matching ACCEPT rule inside the namespace itself.
    openVPNPorts = [ ];
  };

  systemd.services.qbittorrent.vpnConfinement = {
    enable = true;
    vpnNamespace = "proton";
  };

  # Proton NAT-PMP keep-alive: ask the gateway for a port mapping every 45 s
  # (lifetime 60 s -- Proton drops it when not renewed) and make the rest of
  # the stack agree with whatever public port came back:
  #
  #   * the netns firewall policy is INPUT DROP and openVPNPorts only does
  #     static ports, so the loop owns a `natpmp` chain it refills on drift;
  #   * qBittorrent must listen on that exact port (Proton maps public port
  #     P to internal port P). Compare-and-set over the WebUI API -- which
  #     also re-applies it after a qBittorrent restart resets the config to
  #     the static 51413 (see the serverConfig note in arr.nix).
  #
  # The confinement below supplies bindsTo/after on proton.service and runs
  # the unit inside the namespace, so iptables here edits the netns tables
  # and 127.0.0.1:8080 is the WebUI (LocalHostAuth=false covers it). The
  # script runs under `set -e`: a natpmpc failure (tunnel down, server
  # without NAT-PMP) exits the unit and Restart retries every 30 s.
  systemd.services.proton-natpmp = {
    description = "ProtonVPN NAT-PMP port forwarding for qBittorrent";
    wantedBy = [ "multi-user.target" ];
    # Soft ordering only: the loop must keep the mapping alive even while
    # qBittorrent restarts, so it never binds to it.
    after = [ "qbittorrent.service" ];

    vpnConfinement = {
      enable = true;
      vpnNamespace = "proton";
    };

    path = with pkgs; [
      libnatpmp
      iptables
      curl
      gnugrep
    ];

    script = ''
      qbt=http://127.0.0.1:8080/api/v2
      while true; do
        natpmpc -g 10.2.0.1 -a 1 0 udp 60 > /dev/null
        out=$(natpmpc -g 10.2.0.1 -a 1 0 tcp 60)
        port=$(printf '%s\n' "$out" | grep -oE 'Mapped public port [0-9]+' | grep -oE '[0-9]+')
        [ -n "$port" ]

        # Chain + jump survive a service restart but not a netns rebuild;
        # re-create both idempotently before checking the rule itself.
        iptables -w -nL natpmp > /dev/null 2>&1 || iptables -w -N natpmp
        iptables -w -C INPUT -i proton0 -j natpmp 2> /dev/null \
          || iptables -w -I INPUT 1 -i proton0 -j natpmp
        if ! iptables -w -nL natpmp | grep -q "dpt:$port\b"; then
          iptables -w -F natpmp
          iptables -w -A natpmp -p tcp --dport "$port" -j ACCEPT
          iptables -w -A natpmp -p udp --dport "$port" -j ACCEPT
          echo "forwarded port now $port"
        fi

        # qBittorrent may be down mid-restart -- skip this round, not the loop.
        cur=$(curl -sf -m 5 "$qbt/app/preferences" \
          | grep -oE '"listen_port":[0-9]+' | grep -oE '[0-9]+' || true)
        if [ -n "$cur" ] && [ "$cur" != "$port" ]; then
          curl -sf -m 5 "$qbt/app/setPreferences" \
            --data-urlencode "json={\"listen_port\":$port}" \
            && echo "qBittorrent listen_port $cur -> $port"
        fi

        sleep 45
      done
    '';

    serviceConfig = {
      Restart = "always";
      RestartSec = 30;
    };
  };
}
