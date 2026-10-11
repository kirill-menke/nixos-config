#
# VibeReel web -- the desktop browser client, one origin with two ways in:
#
#   outside home:  browser -> Cloudflare (proxied, cache bypass, Access)
#                    -> tunnel "vibereel-web" -> cloudflared (this host)
#                    -> nginx 127.0.0.1:8798 (plain http, loopback only)
#
#   at home:       AdGuard rewrites vr.kirill.es -> 192.168.0.175 (adguard.nix)
#                    -> nginx 192.168.0.175:443, Let's Encrypt cert (DNS-01
#                       via Cloudflare). Never leaves the LAN; no Access.
#
# Both listeners serve the same thing:
#
#   /          the built app from /var/lib/vibereel-web/current (SPA fallback)
#   /jf/       Jellyfin  127.0.0.1:8096, prefix stripped
#   /ml/       reel-api  127.0.0.1:8790, prefix stripped, Host passed through
#              (reel-api's Host/Origin guard: vr.kirill.es is in allowedHosts)
#   /net.json  {"via":"lan"} or {"via":"tunnel"}, answered by nginx itself, so
#              the app knows whether to default to a capped bitrate
#
# Auth is Jellyfin's and reel-api's own (a token per request), exactly as on
# the LAN ports that already exist; Access is an extra gate on the tunnel only.
#
# Deploys: vibe-reel's `web/deploy.sh` rsyncs releases/<stamp>/ and swaps the
# `current` symlink, like the phone app (vibereel-phone.nix). Nothing is
# factored out of that file on purpose: it belongs to another deploy.
#
# Secrets, secrets/vibereel-web.yaml (sops, same recipients as the others):
#   cloudflare-dns-token  API token "nas-acme-vr": Zone Read + DNS Write on
#                         kirill.es only (lego's DNS-01). Not the admin token.
#   tunnel-token          the remotely-managed tunnel's run token. Ingress
#                         (vr.kirill.es -> http://127.0.0.1:8798) lives in the
#                         Cloudflare dashboard/API, not here.
#
# Not the kirill.es microVMs (kirill-es.nix): those tunnels are walled off from
# the LAN by design. This one has to reach Jellyfin and reel-api, so it runs on
# the host, confined by IPAddressAllow to loopback, Cloudflare's tunnel edge
# and the host's DNS resolvers.
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  domain = "vr.kirill.es";
  root = "/var/lib/vibereel-web";
  # The static LAN address (adguard.nix pins enp2s0).
  lanIf = "enp2s0";
  lanAddr = (lib.head config.networking.interfaces.${lanIf}.ipv4.addresses).address;
  tunnelPort = 8798; # loopback only; the tunnel's ingress points here

  netJson = via: {
    extraConfig = ''
      default_type application/json;
      add_header Cache-Control "no-store" always;
      return 200 '{"via":"${via}"}';
    '';
  };

  proxyCommon = ''
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_read_timeout 1h;
    proxy_send_timeout 1h;
    client_max_body_size 0;
  '';

  # Jellyfin: Range passes through untouched; no buffering for video and HLS.
  # Proto is always https: the tunnel listener speaks http, but the browser
  # reached Cloudflare over https.
  jfProxy = {
    proxyPass = "http://127.0.0.1:8096/"; # trailing / strips the prefix
    proxyWebsockets = true; # /socket (session messages)
    extraConfig = proxyCommon + ''
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto https;
    '';
  };

  # reel-api refuses Host names that aren't this box, so it must see the name
  # the browser used, not nginx's upstream 127.0.0.1.
  mlProxy = {
    proxyPass = "http://127.0.0.1:8790/";
    extraConfig = proxyCommon + ''
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto https;
    '';
  };

  locations = via: {
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
    "= /net.json" = netJson via;
    "/jf/" = jfProxy;
    "/ml/" = mlProxy;
  };
in
{
  #############################################################################
  # Secrets
  #############################################################################

  sops.secrets = {
    "vibereel-web/cloudflare-dns-token" = {
      sopsFile = ../../secrets/vibereel-web.yaml;
      key = "cloudflare-dns-token";
      # root 0400 is enough: acme hands it over via LoadCredential.
    };
    "vibereel-web/tunnel-token" = {
      sopsFile = ../../secrets/vibereel-web.yaml;
      key = "tunnel-token";
      restartUnits = [ "cloudflared-vibereel-web.service" ];
    };
  };

  #############################################################################
  # Certificate (DNS-01, so nothing has to be reachable from the internet)
  #############################################################################

  security.acme = {
    acceptTerms = true;
    defaults.email = "kirill.menke@outlook.de";
    certs.${domain} = {
      dnsProvider = "cloudflare";
      # Ask Cloudflare directly whether the TXT record is visible, rather than
      # whatever the host's resolvers cache.
      dnsResolver = "1.1.1.1:53";
      # lego reads CF_DNS_API_TOKEN_FILE; the module loads it as a credential.
      credentialFiles.CF_DNS_API_TOKEN_FILE =
        config.sops.secrets."vibereel-web/cloudflare-dns-token".path;
      group = "nginx";
    };
  };

  #############################################################################
  # nginx
  #############################################################################

  systemd.tmpfiles.rules = [
    "d ${root}          0755 kirill users -"
    "d ${root}/releases 0755 kirill users -"
  ];

  services.nginx = {
    enable = true;

    # LAN: real certificate on the LAN address only. Not 0.0.0.0: tailscale
    # serve holds 443 on the tailnet addresses (finance.nix).
    virtualHosts.vibereel-web-lan = {
      serverName = domain;
      listen = [
        {
          addr = lanAddr;
          port = 443;
          ssl = true;
        }
      ];
      onlySSL = true;
      useACMEHost = domain;
      root = "${root}/current";
      locations = locations "lan";
    };

    # LAN plain http: only a redirect (a typed "vr.kirill.es" in some browsers).
    virtualHosts.vibereel-web-lan-http = {
      serverName = domain;
      listen = [
        {
          addr = lanAddr;
          port = 80;
        }
      ];
      locations."/".return = "301 https://${domain}$request_uri";
    };

    # Tunnel: cloudflared connects here over loopback.
    virtualHosts.vibereel-web-tunnel = {
      serverName = domain;
      listen = [
        {
          addr = "127.0.0.1";
          port = tunnelPort;
        }
      ];
      root = "${root}/current";
      # Only cloudflared reaches this port, and Cloudflare's edge overwrites
      # CF-Connecting-IP, so the client address in logs and X-Forwarded-For
      # is the real one rather than 127.0.0.1.
      extraConfig = ''
        set_real_ip_from 127.0.0.1;
        real_ip_header CF-Connecting-IP;
      '';
      locations = locations "tunnel";
    };
  };

  # 80/443 on the LAN interface only (nginx binds the LAN address anyway).
  networking.firewall.interfaces.${lanIf}.allowedTCPPorts = [
    80
    443
  ];

  #############################################################################
  # Cloudflare Tunnel "vibereel-web" (remotely managed)
  #############################################################################

  # Same shape as the microVM guests' unit (kirill-es-guest.nix): token from a
  # file, never in argv or the environment. Not services.cloudflared, which
  # only does locally-managed tunnels.
  systemd.services.cloudflared-vibereel-web = {
    description = "Cloudflare Tunnel for https://${domain}";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "nginx.service"
    ];
    wants = [ "network-online.target" ];
    # Boots unattended after power loss, possibly long before the WAN is
    # back: never give up.
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      ExecStart = lib.concatStringsSep " " [
        "${lib.getExe pkgs.cloudflared} tunnel"
        "--no-autoupdate"
        # 20241 is cloudflared's default; pinned to loopback.
        "--metrics 127.0.0.1:20241"
        # The LAN has no global IPv6 here, and the allowlist below is v4.
        "--edge-ip-version 4"
        "run --token-file %d/tunnel-token"
      ];
      LoadCredential = [
        "tunnel-token:${config.sops.secrets."vibereel-web/tunnel-token".path}"
      ];
      Restart = "always";
      RestartSec = 5;

      DynamicUser = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      PrivateDevices = true;
      PrivateUsers = true;
      ProtectHostname = true;
      ProtectClock = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectKernelLogs = true;
      ProtectControlGroups = true;
      ProtectProc = "invisible";
      ProcSubset = "pid";
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
        "AF_UNIX"
        "AF_NETLINK"
      ];
      RestrictNamespaces = true;
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      SystemCallArchitectures = "native";
      SystemCallFilter = [
        "@system-service"
        "~@privileged"
        "~@resources"
      ];
      # A remotely-managed tunnel takes its ingress from the Cloudflare
      # account, so a dashboard-side mistake (or takeover) could point it at
      # anything this host can reach -- the LAN, the VPN namespace, the
      # tailnet. Allow only: loopback (nginx :8798), Cloudflare's tunnel edge
      # (region1/region2.v2.argotunnel.com, port 7844, per
      # developers.cloudflare.com/cloudflare-one/connections/connect-networks/
      # deploy-tunnels/tunnel-with-firewall/) and the host's resolvers
      # (adguard.nix: networking.nameservers), which cloudflared needs for the
      # edge SRV lookup. If Cloudflare ever moves the edge, the tunnel goes
      # down rather than open (journal: "dial tcp/udp ... operation not
      # permitted").
      IPAddressDeny = "any";
      IPAddressAllow = [
        "localhost"
        "198.41.192.0/24"
        "198.41.200.0/24"
      ]
      ++ config.networking.nameservers;
      MemoryMax = "256M";
    };
  };
}
