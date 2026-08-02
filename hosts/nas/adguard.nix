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
#   * No dashboard login, and the dashboard is NOT reachable from the LAN.
#     AdGuard Home can only do auth via a bcrypt hash inside the yaml, and
#     this module renders the yaml into the world-readable Nix store -- and
#     this repo is public on top of that. Committing a password hash is off
#     the table for the same reason root.hash lives outside the repo. Instead
#     the UI port simply stays closed on the LAN firewall and is reachable
#     only over tailscale0, which is a trusted interface: every packet there
#     is WireGuard-authenticated as one of the tailnet devices.
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
{ ... }:
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
        address = "192.168.0.175";
        prefixLength = 24;
      }
    ];
  };
  networking.defaultGateway = "192.168.0.1";

  #############################################################################
  # AdGuard Home
  #############################################################################

  services.adguardhome = {
    enable = true;

    # Grants CAP_NET_RAW to the service, which the built-in DHCP server needs
    # for raw sockets and the pre-offer ping check.
    allowDHCP = true;

    # Deliberately NOT the module's openFirewall: that would open the web UI
    # port to the LAN, and the UI has no login (see header). Port 53 is opened
    # manually below; the UI stays tailnet-only.
    openFirewall = false;

    # Web UI bind address. 0.0.0.0 so it answers on tailscale0; the LAN never
    # reaches it because the firewall keeps 3000 closed.
    host = "0.0.0.0";
    port = 3000;

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
        local_ptr_upstreams = [ "192.168.0.1" ];
      };

      filtering = {
        protection_enabled = true;
        filtering_enabled = true;
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
          gateway_ip = "192.168.0.1";
          subnet_mask = "255.255.255.0";
          # .175 (this box) and .1 (router) stay outside the pool.
          range_start = "192.168.0.10";
          range_end = "192.168.0.150";
          lease_duration = 86400;
        };
      };
    };
  };

  # DNS and DHCP to the LAN. The web UI port is deliberately absent here
  # (tailnet-only, see header).
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
  networking.nameservers = [
    "9.9.9.9"
    "1.1.1.1"
  ];
  networking.dhcpcd.extraConfig = "nooption domain_name_servers";
}
