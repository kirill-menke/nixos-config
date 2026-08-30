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
# server and enable the NAT-PMP (port forwarding) toggle (unused today, but
# baked into the generated peer -- saves regenerating if inbound is ever
# wanted). Then, on the NAS:
#
#   install -m600 <downloaded>.conf /var/lib/nixos-secrets/protonvpn-wg.conf
#
# Without it the proton-* namespace units fail to start -- deliberate and
# visible, same as leetx-api's environmentFile.
#
# Inbound peers / NAT-PMP are deliberately skipped: Proton's forwarded port is
# dynamic (a natpmpc loop against 10.2.0.1 plus a qBittorrent API update), and
# this box leeches well-seeded public torrents with upload capped at the floor,
# so outbound-only costs little. arr.nix no longer opens the torrenting port
# and the router forward for 51413 is dead weight -- remove it.
#
{ inputs, ... }:
{
  imports = [ inputs.vpn-confinement.nixosModules.default ];

  # Namespace names are capped at 7 characters (they become interface names).
  vpnNamespaces.proton = {
    enable = true;
    wireguardConfigFile = "/var/lib/nixos-secrets/protonvpn-wg.conf";

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

    # Nothing listens for inbound over the VPN -- without Proton's NAT-PMP
    # handing us a (dynamic) forwarded port, opening 51413 here would do
    # nothing. See the header before adding it.
    openVPNPorts = [ ];
  };

  systemd.services.qbittorrent.vpnConfinement = {
    enable = true;
    vpnNamespace = "proton";
  };
}
