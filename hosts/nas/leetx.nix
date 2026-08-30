#
# leetx-api -- personal search/magnet/download API for the TV client.
#
# A thin façade over services this box already runs: search goes to Prowlarr
# (which owns the 1337x indexer and its FlareSolverr Cloudflare handling),
# downloads go to qBittorrent under a dedicated `leetx` category so they never
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
{ inputs, ... }:
{
  imports = [ inputs.leetx-api.nixosModules.default ];

  services.leetx-api = {
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
    #     > /var/lib/nixos-secrets/leetx-api.env
    #   chmod 600 /var/lib/nixos-secrets/leetx-api.env
    #
    # Without it the unit fails to start -- deliberate and visible.
    environmentFile = "/var/lib/nixos-secrets/leetx-api.env";
  };

  #############################################################################
  # One-time, after the first rebuild
  #############################################################################
  #
  # Point Prowlarr's FlareSolverr indexer proxy at the now-local instance and
  # confirm the 1337x indexer resolves through it. During bring-up the proxy
  # pointed at a dev box (192.168.0.11:8191); repoint it to 127.0.0.1:8191:
  #
  #   K=$(grep -oP '(?<=<ApiKey>)[^<]+' /var/lib/private/prowlarr/config.xml)
  #   PID=$(curl -s -H "X-Api-Key: $K" http://127.0.0.1:9696/api/v1/indexerProxy \
  #         | grep -oP '"id":\s*\K\d+' | head -1)
  #   # edit that proxy's host field to http://127.0.0.1:8191/ (UI is simplest:
  #   #   Prowlarr -> Settings -> Indexer Proxies -> flaresolverr), then
  #   #   Test the 1337x indexer.
}
