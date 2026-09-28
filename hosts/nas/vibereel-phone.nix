#
# VibeReel Phone -- the iPhone PWA build of the TV's Jellyfin client.
#
# One HTTPS origin on the tailnet, so the app, Jellyfin and leetx-api are
# same-origin (no CORS, one service-worker scope, one certificate):
#
#   https://nas.<tailnet>.ts.net:9443/     -> the built PWA (nginx, below)
#   https://nas.<tailnet>.ts.net:9443/jf/  -> Jellyfin   127.0.0.1:8096
#   https://nas.<tailnet>.ts.net:9443/ml/  -> leetx-api  127.0.0.1:8790
#
# tailscale serve strips the mount path before proxying, so Jellyfin sees
# plain /Items/..., and its HLS playlists (relative segment URIs) resolve
# under /jf on the phone. Port 443 of this node already carries the finance
# app (finance.nix) and 8443 ntfy, hence 9443.
#
# Why nginx for the static part instead of `tailscale serve <dir>`: the
# built-in file server sends no Cache-Control, has no SPA fallback and
# guesses MIME types from Go's table (no .webmanifest; there is no
# /etc/mime.types on NixOS). A PWA must be able to say "revalidate index.html
# and sw.js every time" or an update never reaches the home-screen app.
#
# The directory is owned by kirill so vibe-reel's `phone/deploy.sh` can rsync
# a release in without root: releases/<stamp>/ + an atomically swapped
# `current` symlink, which nginx serves.
#
# LAN fallback: the same app plus /jf and /ml proxies on http://nas:8797.
# Everything works there except the service worker (Safari only registers one
# on a secure origin), so no offline shell and no home-screen update logic.
# The tailnet origin is the real one; this exists for development and for a
# phone whose Tailscale is off at home.
#
{ pkgs, ... }:
let
  root = "/var/lib/vibereel-phone";
  staticPort = 8792; # loopback only, behind tailscale serve
  lanPort = 8797;
  httpsPort = 9443;

  staticLocations = {
    "/" = {
      # Unknown paths fall back to the shell (client-side routing).
      tryFiles = "$uri $uri/ /index.html";
      extraConfig = ''
        add_header Cache-Control "no-cache" always;
      '';
    };
    # Vite's content-hashed bundles never change under one name.
    "/assets/" = {
      tryFiles = "$uri =404";
      extraConfig = ''
        add_header Cache-Control "public, max-age=31536000, immutable"; # 200s only, not a 404
      '';
    };
    "= /sw.js".extraConfig = ''
      add_header Cache-Control "no-cache, no-store, must-revalidate" always;
      add_header Service-Worker-Allowed "/" always;
    '';
    "= /manifest.webmanifest".extraConfig = ''
      default_type application/manifest+json;
      add_header Cache-Control "no-cache" always;
    '';
  };

  proxy = port: {
    proxyPass = "http://127.0.0.1:${toString port}/"; # trailing / strips the prefix
    extraConfig = ''
      proxy_buffering off;
      proxy_request_buffering off;
      proxy_read_timeout 1h;
      client_max_body_size 0;
    '';
  };

  serve = "${pkgs.tailscale}/bin/tailscale serve";
in
{
  systemd.tmpfiles.rules = [
    "d ${root}          0755 kirill users -"
    "d ${root}/releases 0755 kirill users -"
  ];

  services.nginx = {
    enable = true;
    recommendedGzipSettings = true;
    recommendedOptimisation = true;
    # nginx's own mime.types predates .webmanifest.
    appendHttpConfig = ''
      types { application/manifest+json webmanifest; }
    '';

    virtualHosts.vibereel-phone = {
      listen = [
        {
          addr = "127.0.0.1";
          port = staticPort;
        }
      ];
      root = "${root}/current";
      locations = staticLocations;
    };

    virtualHosts.vibereel-phone-lan = {
      listen = [
        {
          addr = "0.0.0.0";
          port = lanPort;
        }
      ];
      root = "${root}/current";
      locations = staticLocations // {
        "/jf/" = proxy 8096;
        "/ml/" = proxy 8790;
      };
    };
  };

  networking.firewall.allowedTCPPorts = [ lanPort ];

  # Applied idempotently on every start; `off` on stop clears only this port,
  # leaving finance (443) and ntfy (8443) alone.
  systemd.services.vibereel-phone-tailscale-serve = {
    description = "Publish VibeReel Phone (+ /jf Jellyfin, /ml leetx-api) on port ${toString httpsPort} via tailscale serve";
    after = [
      "tailscaled.service"
      "nginx.service"
    ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = [
        "${serve} --bg --https=${toString httpsPort} --set-path=/ http://127.0.0.1:${toString staticPort}"
        "${serve} --bg --https=${toString httpsPort} --set-path=/jf http://127.0.0.1:8096"
        "${serve} --bg --https=${toString httpsPort} --set-path=/ml http://127.0.0.1:8790"
      ];
      ExecStop = "${serve} --https=${toString httpsPort} off";
    };
  };
}
