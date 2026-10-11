#
# AdGuard Home -- network-wide DNS ad/tracker blocking.
#
# Why AdGuard Home rather than Pi-hole: Pi-hole is not packaged for NixOS (it
# would have to run as an OCI container and be configured imperatively through
# its web UI); AdGuard Home is a first-class NixOS module and everything below
# is declarative.
#
# Scope check: this blocks ads by refusing to resolve ad DOMAINS. That kills
# banner ads, most in-app ads and trackers network-wide (including webOS
# telemetry on the C4), but NOT YouTube ads -- those are served from the same
# googlevideo.com CDN as the videos themselves and cannot be separated at the
# DNS layer.
#
# Design:
#
#   * Fully declarative (mutableSettings = false): the yaml config is
#     regenerated from this file on every service start, same philosophy as
#     Jellyfin's forceEncodingConfig. Changes clicked into the web UI are
#     thrown away on the next restart -- edit this file instead. The query log
#     and statistics live in the state directory, not the yaml, and survive
#     restarts fine.
#
#   * The dashboard needs a login and listens on loopback only. Whoever can
#     use it can add DNS rewrites, i.e. silently redirect any domain for
#     every device on the LAN, so being "on the tailnet" or "a process on
#     this box" is not enough -- and a page in any browser on a tailnet
#     device could reach a bare port by DNS rebinding.
#
#     Reached as https://nas.<tailnet>.ts.net:3000 through `tailscale serve`
#     (unit below), not on the LAN. The TLS name check also defeats DNS
#     rebinding. Break-glass without Tailscale:
#       ssh -L 3000:127.0.0.1:3000 root@nas.local   ->  http://localhost:3000
#
#     AdGuard Home only does auth via bcrypt hashes inside its yaml, and the
#     module renders that yaml into the world-readable Nix store -- and this
#     repo is public. A bcrypt hash is offline-crackable, so it stays out of
#     both, like root.hash. preStart merges it into the runtime copy of the
#     yaml (mode 600 in the service's private state dir) on every start.
#     Provision it once, on the NAS (htpasswd prompts for the password):
#
#       nix shell nixpkgs#apacheHttpd -c htpasswd -nBC 12 kirill \
#         | install -Dm600 /dev/stdin /var/lib/nixos-secrets/adguardhome/htpasswd
#       systemctl restart adguardhome
#
#     One `name:bcrypt-hash` line per account. If the file is missing or
#     empty, the UI is locked (no login can succeed) while DNS and DHCP keep
#     running -- it fails closed without taking the LAN down with it.
#
#   * DNS (53) is open to the LAN, and this box is ALSO the LAN's DHCP
#     server. That is not by preference but forced by the router: the
#     Vodafone Station (CGA4233DE) offers no way to change the DNS server it
#     hands out -- not for its DHCP clients, not upstream. The only way to
#     point the LAN at AdGuard is to switch the Station's DHCP off
#     (Experten-Modus -> Einstellungen -> LAN -> "Lokaler DHCPv4-Server")
#     and serve leases from here, with this host as DNS. AdGuard ping-checks
#     a candidate address before offering it, which smooths over any stale
#     leases the Station handed out before the cutover.
#
#     Consequence: the DHCP server cannot lease its own address, so enp2s0
#     (the patched port) is static below. This also makes this box a harder
#     dependency for the LAN -- if adguardhome is down, clients lose DNS
#     (and eventually leases). Rollback is one toggle: re-enable the
#     Station's DHCP server.
#
#     Known gap: the Station keeps announcing ITSELF as the IPv6 resolver
#     via router advertisements, and that cannot be disabled either.
#     Dual-stack clients (the iPhone above all) may therefore leak some
#     queries past AdGuard over IPv6. If that bothers you on a device, set
#     its Wi-Fi DNS manually to 192.168.0.175 -- iOS then overrides both
#     address families.
#
# On-the-go blocking for the iPhone: in the Tailscale admin console (DNS tab)
# add this host's tailnet IP as a global nameserver and enable "Override local
# DNS". The phone then filters through the NAS even on cellular. This host
# itself runs `--accept-dns=false` (tailscale.nix), so it never tries to
# resolve through itself over the tailnet -- no loop.
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  # This box on the LAN, and the Vodafone Station.
  nasAddr = "192.168.0.175";
  router = "192.168.0.1";

  # Web UI: loopback port, and the same port number on the tailnet (HTTPS).
  uiPort = 3000;

  # Hand-provisioned web UI logins (htpasswd format, see header). A directory
  # rather than the file itself, because LoadCredential on a missing file
  # fails the whole unit (and with it LAN DNS); an empty directory does not.
  uiLoginDir = "${config.nas.secretsDir}/adguardhome";

  yq = lib.getExe pkgs.yq-go;
  tailscaleServe = "${pkgs.tailscale}/bin/tailscale serve";
in
{
  #############################################################################
  # Static address -- required for the DHCP-server role
  #############################################################################

  # Same address the DHCP lease always produced, now nailed down. Only the
  # patched port (enp2s0) is static; enp3s0 keeps the useDHCP default from
  # default.nix as a recovery path -- though with the Station's DHCP off it
  # would only get an address if the Station's DHCP is switched back on.
  networking.interfaces.enp2s0 = {
    useDHCP = false;
    ipv4.addresses = [
      {
        address = nasAddr;
        prefixLength = 24;
      }
    ];
  };
  networking.defaultGateway = router;

  #############################################################################
  # AdGuard Home
  #############################################################################

  services.adguardhome = {
    enable = true;

    # Grants CAP_NET_RAW to the service, which the built-in DHCP server needs
    # for raw sockets and the pre-offer ping check.
    allowDHCP = true;

    # Deliberately NOT the module's openFirewall: that would open the web UI
    # port to the LAN. Port 53 is opened manually below.
    openFirewall = false;

    # Web UI on loopback only; tailscale serve (below) is the way in.
    host = "127.0.0.1";
    port = uiPort;

    mutableSettings = false;

    settings = {
      dns = {
        # All interfaces: LAN (clients sent here by router DHCP) and
        # tailscale0 (the phone when remote, once the tailnet nameserver is
        # configured).
        bind_hosts = [ "0.0.0.0" ];
        port = 53;

        # Encrypted (DoH) upstreams, two independent providers so a single
        # outage does not take home DNS down. AdGuard load-balances toward
        # whichever answers faster.
        upstream_dns = [
          "https://dns.quad9.net/dns-query"
          "https://cloudflare-dns.com/dns-query"
        ];
        # Plain-IP resolvers used ONLY to bootstrap the DoH hostnames above.
        bootstrap_dns = [
          "9.9.9.9"
          "1.1.1.1"
        ];

        # Reverse lookups for LAN addresses go to the router, so the query
        # log shows device names instead of bare 192.168.0.x.
        local_ptr_upstreams = [ router ];
      };

      filtering = {
        protection_enabled = true;
        filtering_enabled = true;
        # VibeReel web (vibereel-web.nix): at home, straight to nginx on the
        # LAN address instead of out through Cloudflare and the tunnel. AAAA
        # for the name answers empty, so no client picks Cloudflare's v6.
        rewrites = [
          {
            domain = "vr.kirill.es";
            answer = nasAddr;
            enabled = true;
          }
        ];
      };

      filters = [
        {
          name = "AdGuard DNS filter";
          url = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt";
          id = 1;
          enabled = true;
        }
        {
          # Balanced "just works" list: aggressive enough to matter, curated
          # not to break sites. https://oisd.nl
          name = "OISD Big";
          url = "https://big.oisd.nl";
          id = 2;
          enabled = true;
        }
      ];

      # A week of query log is plenty for debugging "why is this blocked".
      # The default 90 days would just be a browsing-history archive.
      querylog.interval = "168h";

      # dns-fallback.nix's watchdog asks for this name every 10 s to see that
      # AdGuard is alive. A rule of our own answers it without any upstream;
      # the log and statistics leave it out so they only show real clients.
      user_rules = [ "||dns-watchdog.invalid^" ];
      querylog.ignored = [ "dns-watchdog.invalid" ];
      querylog.ignored_enabled = true;
      statistics.ignored = [ "dns-watchdog.invalid" ];
      statistics.ignored_enabled = true;

      # LAN DHCP, because the Vodafone Station cannot advertise a custom DNS
      # server (see header). Dynamic and UI-added static leases live in
      # leases.json in the state directory, so they survive restarts despite
      # mutableSettings = false.
      dhcp = {
        enabled = true;
        interface_name = "enp2s0";
        # Clients become resolvable as <hostname>.lan through AdGuard.
        local_domain_name = "lan";
        dhcpv4 = {
          gateway_ip = router;
          subnet_mask = "255.255.255.0";
          # .175 (this box) and .1 (router) stay outside the pool.
          range_start = "192.168.0.10";
          range_end = "192.168.0.150";
          lease_duration = 86400;
        };
      };
    };
  };

  # Web UI login, merged in at runtime (see header). Runs as the module's
  # DynamicUser after its own preStart has copied the store yaml into place,
  # so it rewrites that fresh copy every start. Credentials come in through
  # LoadCredential, since the dynamic user cannot read the secrets dir.
  systemd.tmpfiles.rules = [ "d ${uiLoginDir} 0700 root root -" ];
  systemd.services.adguardhome = {
    # On a switch that first adds the tmpfiles rule, create the dir before
    # the restarted unit tries to load it.
    after = [ "systemd-tmpfiles-resetup.service" ];
    serviceConfig.LoadCredential = [ "ui:${uiLoginDir}" ];
    # No `yq -i`: it chowns its temp file back to the original owner, and
    # chown is in @privileged, which the module's SystemCallFilter denies
    # (SIGSYS, "Bad system call"). Write a temp file and rename it instead;
    # UMask=0077 keeps it 0600.
    preStart = lib.mkAfter ''
      conf="$STATE_DIRECTORY/AdGuardHome.yaml"
      yqi() { ${yq} "$1" "$conf" > "$conf.tmp"; mv -f "$conf.tmp" "$conf"; }
      yqi '.users = []'
      for f in "$CREDENTIALS_DIRECTORY"/ui_*; do
        [ -f "$f" ] || continue
        while IFS=: read -r name hash || [ -n "$name" ]; do
          [ -n "$name" ] && [ -n "$hash" ] || continue
          NAME="$name" HASH="$hash" yqi '.users += [{"name": strenv(NAME), "password": strenv(HASH)}]'
        done < "$f"
      done
      if [ "$(${yq} '.users | length' "$conf")" -eq 0 ]; then
        echo "no web UI login in ${uiLoginDir}: UI locked, see adguard.nix" >&2
        # Not a bcrypt hash, so every login attempt fails.
        yqi '.users = [{"name": "locked", "password": "!"}]'
      fi
    '';
  };

  # Publish the loopback UI on this node's tailnet name, HTTPS port 3000.
  # Same pattern as finance.nix; `off` on stop clears only this port.
  systemd.services.adguardhome-tailscale-serve = {
    description = "Publish the AdGuard Home UI on port ${toString uiPort} via tailscale serve";
    after = [ "tailscaled.service" ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${tailscaleServe} --bg --https=${toString uiPort} http://127.0.0.1:${toString uiPort}";
      ExecStop = "${tailscaleServe} --https=${toString uiPort} off";
    };
  };

  # DNS and DHCP to the LAN. The web UI port is deliberately absent here
  # (loopback + tailscale serve, see header).
  networking.firewall = {
    allowedTCPPorts = [ 53 ];
    allowedUDPPorts = [
      53
      67 # DHCP server
    ];
  };

  # Once the router hands out this box as the LAN DNS server, the DHCP lease
  # would point resolv.conf at... this box itself, making the host's own name
  # resolution depend on adguardhome.service being up. Same rule as
  # wait-online and tailscale DNS: nothing on a headless box may hinge on one
  # daemon. Pin the host's own resolvers and ignore what DHCP offers.
  #
  # Order and timeout matter (measured 2026-09-28): plain-UDP Quad9 answered in
  # 120-850 ms here and dropped ~1 in 15 queries, while 1.1.1.1 answered in
  # ~20 ms with none lost. glibc waits 5 s per lost reply before trying the next
  # server, and every lookup is an A + AAAA pair, so with Quad9 first the
  # median getent took 5 s (17 of 20 over 1 s, worst 10 s) -- Sonarr/Radarr
  # re-resolving their metadata APIs stalled VibeReel's first detail open for
  # 5-10 s. Cloudflare first, Quad9 kept as the independent fallback, and a
  # 1 s per-try timeout so a lost packet costs 1 s instead of 5.
  networking.nameservers = [
    "1.1.1.1"
    "9.9.9.9"
  ];
  networking.resolvconf.extraOptions = [
    "timeout:1"
    "attempts:3"
  ];
  networking.dhcpcd.extraConfig = "nooption domain_name_servers";
}
