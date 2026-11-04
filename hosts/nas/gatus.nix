#
# Gatus: checks every service on this box once a minute, keeps their history,
# and sends an alert to the phone when one stays down.
#
# Dashboard: https://nas.<tailnet>.ts.net:4443 (tailscale serve, tailnet only),
# broken services first. Glance (glance.nix) links to it and shows its summary.
#
# Alerts: after 3 failed checks in a row (~3 min) Gatus publishes to the ntfy
# topic finance-system, the one the phone's ntfy app already follows for the
# disk and Kirifin alerts, and again once the service has passed 2 checks.
# Tapping the alert opens the dashboard.
#
# ntfy listens on a unix socket only (finance.nix) and Gatus can only post over
# HTTP, so a socket-activated systemd-socket-proxyd bridges 127.0.0.1:2586 to
# it. The token belongs to ntfy user nas-host (write-only on finance-system),
# label "gatus". Like ntfy's user database it is hand-provisioned; re-create it
# with a script piped to `ssh nas 'sudo sh -s'` (never put it in argv):
#   tok=$(ntfy token add --label=gatus nas-host | grep -o 'tk_[A-Za-z0-9]*')
#   printf 'NTFY_TOKEN=%s\n' "$tok" | install -Dm600 /dev/stdin \
#     /var/lib/nixos-secrets/gatus-ntfy.env
# Without the file Gatus still checks; only the alerts fail (see the journal).
#
# Blind spots: this box down, or ntfy down, leaves nobody to send the alert.
# That needs a dead-man switch outside the house.
#
# History: SQLite in /var/lib/gatus, so restarts and deploys keep it.
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  port = 8095; # loopback
  # Tailnet, via tailscale serve. Taken there: 443 = finance, 1443 = Glance,
  # 2443 = Immich, 3000 = AdGuard, 5443 = Radicale, 6443 = Vaultwarden,
  # 7443 = T3 Code, 8443 = ntfy, 9443 = vibereel-phone, 10443 = Paperless.
  httpsPort = 4443;
  ntfyPort = 2586; # loopback bridge to ntfy's unix socket
  ntfySocket = "/run/ntfy-sh/ntfy.sock";
  tokenFile = "${config.nas.secretsDir}/gatus-ntfy.env";
  serve = "${pkgs.tailscale}/bin/tailscale serve";

  lan = (builtins.head config.networking.interfaces.enp2s0.ipv4.addresses).address;
  # This box can't resolve its own MagicDNS name (tailscale --accept-dns=false);
  # the tailnet resolver can.
  tailnetDns.dns-resolver = "udp://100.100.100.100:53";

  local = path: "http://127.0.0.1:${path}";
  ok = [ "[STATUS] == 200" ];
  certOk = "[CERTIFICATE_EXPIRATION] > 168h";
  tcp = port: {
    url = "tcp://127.0.0.1:${toString port}";
    conditions = [ "[CONNECTED] == true" ];
  };

  checks = {
    Network = {
      # AdGuard answers this name from a user rule with 0.0.0.0 (dns-fallback.nix);
      # the unfiltered fallback forwarder gets NXDOMAIN for it instead.
      "AdGuard DNS" = {
        url = lan;
        dns = {
          query-name = "dns-watchdog.invalid";
          query-type = "A";
        };
        conditions = [
          "[DNS_RCODE] == NOERROR"
          "[BODY] == 0.0.0.0"
        ];
      };
      Internet = {
        url = "9.9.9.9";
        dns = {
          query-name = "example.com";
          query-type = "A";
        };
        conditions = [ "[DNS_RCODE] == NOERROR" ];
      };
      # Gatus itself through tailscale serve: if this fails while Gatus runs,
      # the phone can't reach anything on the tailnet.
      Tailscale = {
        url = "https://\${NAS_HOST}:${toString httpsPort}/health";
        client = tailnetDns;
        conditions = [
          "[STATUS] == 200"
          "[BODY].status == UP"
          certOk
        ];
      };
      "Scanner inbox (Samba)" = tcp 445;
      NFS = tcp 2049;
    };

    Media = {
      Jellyfin = {
        url = local "8096/health";
        conditions = ok ++ [ "[BODY] == Healthy" ];
      };
      Immich = {
        url = local "${toString config.services.immich.port}/api/server/ping";
        conditions = ok ++ [ "[BODY].res == pong" ];
      };
      Radarr = {
        url = local "7878/ping";
        conditions = ok;
      };
      Sonarr = {
        url = local "8989/ping";
        conditions = ok;
      };
      Prowlarr = {
        url = local "9696/ping";
        conditions = ok;
      };
      # Inside the VPN namespace (vpn.nix); host-local clients use its address.
      qBittorrent = {
        url = "http://${config.vpnNamespaces.proton.namespaceAddress}:${toString config.services.qbittorrent.webuiPort}/";
        conditions = ok;
      };
      FlareSolverr = {
        url = local "${toString config.services.flaresolverr.port}/";
        conditions = ok;
      };
      "VibeReel API" = {
        url = local "8790/health";
        conditions = ok ++ [ "[BODY].ok == true" ];
      };
      "VibeReel phone" = {
        url = local "8792/";
        conditions = ok;
      };
    };

    Documents = {
      Paperless = {
        url = local "${toString config.services.paperless.port}/accounts/login/";
        conditions = ok;
      };
      # paperless-ai.nix's bridge answers POSTs only.
      "Paperless AI (Claude)" = tcp 8794;
      Vaultwarden = {
        url = local "${toString config.services.vaultwarden.config.ROCKET_PORT}/alive";
        conditions = ok;
      };
      Radicale = {
        url = local "5232/.web/";
        conditions = ok;
      };
    };

    Apps = {
      # Kirifin listens on a unix socket only; tailscale serve is its front door.
      Kirifin = {
        url = "https://\${NAS_HOST}/api/health";
        client = tailnetDns;
        conditions = ok;
      };
      # Through the bridge below, i.e. the path every alert takes. ntfy runs
      # behind-proxy and logs "unable to parse IP (@)" for every unix-socket
      # request without X-Forwarded-For: once a minute, without the header.
      ntfy = {
        url = local "${toString ntfyPort}/v1/health";
        headers.X-Forwarded-For = "127.0.0.1";
        conditions = ok ++ [ "[BODY].healthy == true" ];
      };
      "T3 Code" = {
        url = local "3773/";
        conditions = ok;
      };
      Syncthing = {
        url = "http://${config.services.syncthing.guiAddress}/rest/noauth/health";
        conditions = ok ++ [ "[BODY].status == OK" ];
      };
      Glance = {
        url = local "${toString config.services.glance.settings.server.port}/api/healthz";
        conditions = ok;
      };
    };

    Public = {
      "kirill.es" = {
        url = "https://kirill.es/";
        conditions = ok ++ [ certOk ];
      };
      # From outside, Cloudflare Access answers first (302 to its login), so
      # the tunnel is checked on its own and the site the way the LAN sees it:
      # AdGuard rewrites the name to this box's nginx.
      "vr.kirill.es" = {
        url = "https://vr.kirill.es/";
        client.dns-resolver = "udp://${lan}:53";
        conditions = ok ++ [ certOk ];
      };
      "Cloudflare tunnel (vr.kirill.es)" = {
        url = local "20241/ready";
        conditions = ok ++ [ "[BODY].readyConnections > 0" ];
      };
      # The Funnel node claude.ai connects to (outlook.nix).
      "Outlook MCP" = {
        url = "\${OUTLOOK_MCP_URL}/.well-known/oauth-authorization-server";
        conditions = ok ++ [ certOk ];
      };
    };
  };

  endpoints = lib.concatLists (
    lib.mapAttrsToList (
      group:
      lib.mapAttrsToList (
        name: check:
        {
          inherit group name;
          interval = "1m";
          alerts = [ { type = "ntfy"; } ];
          ui = {
            hide-hostname = true;
            hide-port = true;
          };
        }
        // check
      )
    ) checks
  );
in
{
  services.gatus = {
    enable = true;
    environmentFile = config.sops.templates."gatus.env".path;
    settings = {
      web = {
        address = "127.0.0.1";
        inherit port;
      };
      storage = {
        type = "sqlite";
        path = "/var/lib/gatus/data.db";
      };
      ui = {
        title = "NAS status";
        header = "NAS status";
        dashboard-heading = "NAS";
        dashboard-subheading = "Broken services first. Alerts go to the ntfy app.";
        default-sort-by = "health";
        buttons = [
          {
            name = "Start page";
            link = "https://\${NAS_HOST}:1443"; # glance.nix
          }
        ];
      };
      alerting.ntfy = {
        url = "http://127.0.0.1:${toString ntfyPort}";
        topic = "finance-system";
        token = "\${NTFY_TOKEN}";
        click = "https://\${NAS_HOST}:${toString httpsPort}";
        default-alert = {
          failure-threshold = 3;
          success-threshold = 2;
          send-on-resolved = true;
        };
      };
      inherit endpoints;
    };
  };

  # Read by PID 1, so root-only is fine. The tailnet names stay out of this
  # public repo: same sops values as vaultwarden.nix and outlook.nix.
  sops.templates."gatus.env" = {
    content = ''
      NAS_HOST=${config.sops.placeholder."reel-api/tailnet-host"}
      OUTLOOK_MCP_URL=${config.sops.placeholder."outlook-mcp/external-url"}
    '';
    restartUnits = [ "gatus.service" ];
  };

  systemd.sockets.gatus-ntfy = {
    description = "ntfy for Gatus on 127.0.0.1:${toString ntfyPort}";
    wantedBy = [ "sockets.target" ];
    listenStreams = [ "127.0.0.1:${toString ntfyPort}" ];
  };

  systemd.services = {
    gatus = {
      after = [ "gatus-ntfy.socket" ];
      serviceConfig = {
        # "-": optional, see the header.
        EnvironmentFile = lib.mkAfter [ "-${tokenFile}" ];
        MemoryHigh = "128M";
        MemoryMax = "256M";
      };
    };

    gatus-ntfy = {
      description = "Forward Gatus's ntfy requests to ntfy's unix socket";
      after = [ "ntfy-sh.service" ];
      serviceConfig = {
        ExecStart = "${config.systemd.package}/lib/systemd/systemd-socket-proxyd ${ntfySocket}";
        DynamicUser = true;
        # The socket is 0660 ntfy-sh:ntfy-sh (finance.nix).
        SupplementaryGroups = [ "ntfy-sh" ];
        # The listening socket comes from PID 1; the unix socket needs no network.
        PrivateNetwork = true;
        MemoryMax = "32M";
      };
    };

    # Same pattern as radicale.nix; `off` on stop clears only this port.
    gatus-tailscale-serve = {
      description = "Publish Gatus on port ${toString httpsPort} via tailscale serve";
      after = [
        "tailscaled.service"
        "gatus.service"
      ];
      wants = [ "tailscaled.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        # Two serve units changing the config at once lose with "etag
        # mismatch" (hit on the first deploy, next to glance's); try again.
        Restart = "on-failure";
        RestartSec = 3;
        ExecStart = "${serve} --bg --https=${toString httpsPort} http://127.0.0.1:${toString port}";
        ExecStop = "${serve} --https=${toString httpsPort} off";
      };
    };
  };
}
