#
# reel-api -- VibeReel's backend (lookup/add, activity, streams, trailers, push)
# for the TV and phone clients. Called leetx-api until 2026-10.
#
# A thin façade over services this box already runs: search goes to Prowlarr
# (which owns the indexers and their FlareSolverr Cloudflare handling),
# manual downloads go to qBittorrent under a dedicated category so they never
# mix with what Sonarr and Radarr manage. The contract is deliberately small --
# search returns opaque ids, one call resolves an id to a magnet, one call
# hands a magnet to qBittorrent -- so a thin client (the TV) needs no torrent
# stack of its own.
#
# Bound to loopback (nginx's LAN /ml proxy and tailscale serve, both in
# vibereel-phone.nix) and to the LAN address (the TV, which is a LAN device,
# not on the tailnet) -- not 0.0.0.0, so not on the VPN namespace bridge.
# Every request needs a Jellyfin access token of a signed-in user (the
# clients send theirs; reel-api checks it against Jellyfin's /Users/Me), and
# Host / Origin must name this box, which stops DNS-rebinding and cross-site
# requests from browsers on the LAN. Nothing new to provision for that: no
# secret of its own.
#
{ inputs, config, lib, ... }:
let
  # The static LAN address (adguard.nix pins enp2s0).
  lanAddr = (lib.head config.networking.interfaces.enp2s0.ipv4.addresses).address;

  # Names clients may reach it by (IP literals and localhost always work):
  # MagicDNS / LAN short name, avahi (default.nix), AdGuard's .lan zone
  # (adguard.nix), and VibeReel web (vibereel-web.nix), via the LAN listener
  # or the tunnel.
  publicHosts = [
    "nas"
    "nas.local"
    "nas.lan"
    "vr.kirill.es"
  ];
  # Plus the tailnet origin the phone app is served from (vibereel-phone.nix):
  # nas.<tailnet>.ts.net names the tailnet, which this public repo must never
  # hold in plain text, so it comes from sops (secrets/reel-api.yaml).
  tailnetHost = config.sops.placeholder."reel-api/tailnet-host";
in
{
  imports = [ inputs.reel-api.nixosModules.default ];

  services.reel-api = {
    enable = true;

    # Reachable from the TV over the LAN (http://<lanAddr>:8790). Turns on
    # the firewall rule for the port; the module also enables
    # services.flaresolverr for Prowlarr's Cloudflare-gated indexers.
    openFirewall = true;
    listenAddresses = [
      "127.0.0.1"
      lanAddr
    ];

    # Without the tailnet name; the full list arrives at runtime (below).
    allowedHosts = publicHosts;

    # qBittorrent sits inside the ProtonVPN namespace (vpn.nix); from this
    # host its WebUI answers at the namespace address, not loopback.
    qbittorrentUrl = "http://192.168.15.1:8080";

    # PROWLARR_API_KEY lives here, off this (public) repo. Provision once:
    #
    #   install -d -m700 /var/lib/nixos-secrets
    #   echo "PROWLARR_API_KEY=<key from /var/lib/private/prowlarr/config.xml>" \
    #     > /var/lib/nixos-secrets/reel-api.env
    #   chmod 600 /var/lib/nixos-secrets/reel-api.env
    #
    # Without it the unit fails to start -- deliberate and visible.
    environmentFile = "${config.nas.secretsDir}/reel-api.env";
  };

  # The full host list, tailnet name included, rendered by sops at activation.
  # An EnvironmentFile beats the module's Environment=, so this replaces the
  # public list above. No "-": a missing file stops the start rather than
  # running without the phone app's origin.
  sops.secrets."reel-api/tailnet-host" = {
    sopsFile = ../../secrets/reel-api.yaml;
    key = "tailnet-host";
  };
  sops.templates."reel-api-hosts.env" = {
    content = "REEL_API_ALLOWED_HOSTS=${lib.concatStringsSep "," (publicHosts ++ [ tailnetHost ])}\n";
    restartUnits = [ "reel-api.service" ];
  };
  systemd.services.reel-api.serviceConfig.EnvironmentFile = [
    config.sops.templates."reel-api-hosts.env".path
  ];

  # The rename's secrets half: the module's reel-api-migrate (which runs once
  # before reel-api starts and moves the old state/cache dirs) also renames
  # the old environment file, under the same rule -- only if the old one
  # exists and the new one doesn't. mv keeps its 0600 mode and owner.
  systemd.services.reel-api-migrate.script = lib.mkAfter ''
    old=${config.nas.secretsDir}/leetx-api.env new=${config.nas.secretsDir}/reel-api.env
    if [ -f "$old" ] && [ ! -e "$new" ]; then
      mv -T "$old" "$new"
      echo "moved $old -> $new"
    fi
  '';

  #############################################################################
  # One-time, after the first rebuild
  #############################################################################
  #
  # Point Prowlarr's FlareSolverr indexer proxy at the now-local instance and
  # confirm the Cloudflare-gated indexers resolve through it. During bring-up the proxy
  # pointed at a dev box (192.168.0.11:8191); repoint it to 127.0.0.1:8191:
  #
  #   K=$(grep -oP '(?<=<ApiKey>)[^<]+' /var/lib/private/prowlarr/config.xml)
  #   PID=$(curl -s -H "X-Api-Key: $K" http://127.0.0.1:9696/api/v1/indexerProxy \
  #         | grep -oP '"id":\s*\K\d+' | head -1)
  #   # edit that proxy's host field to http://127.0.0.1:8191/ (UI is simplest:
  #   #   Prowlarr -> Settings -> Indexer Proxies -> flaresolverr), then
  #   #   Test those indexers.
}
