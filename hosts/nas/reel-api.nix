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
# Bound on the LAN and firewalled open, the same exposure Jellyfin has in
# media.nix: the TV is a LAN device, not on the tailnet. Note the tradeoff --
# unlike Jellyfin this carries a POST that starts a download, and there is no
# auth, so anything on the LAN can enqueue one.
#
{ inputs, config, lib, ... }:
{
  imports = [ inputs.reel-api.nixosModules.default ];

  services.reel-api = {
    enable = true;

    # Reachable from the TV over the LAN (http://nas:8790). Turns on the
    # firewall rule for the port; the module also enables services.flaresolverr
    # for Prowlarr's Cloudflare-gated indexers.
    openFirewall = true;

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
