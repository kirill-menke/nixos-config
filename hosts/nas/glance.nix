#
# Glance: the start page for everything on this box. Links to every app,
# Gatus's verdict (gatus.nix) and the box's CPU, memory and disks on one page.
#
# https://nas.<tailnet>.ts.net:1443 (tailscale serve, tailnet only). On the
# phone: Safari, Share, Add to Home Screen; it installs as "NAS".
#
# The links need the tailnet name, which stays out of this public repo: it
# comes from sops as NAS_HOST, and Glance fills in ${NAS_HOST} at start.
#
{
  config,
  pkgs,
  ...
}:
let
  port = 8099; # loopback
  # Tailnet, via tailscale serve. Port list: gatus.nix.
  httpsPort = 1443;
  gatusPort = config.services.gatus.settings.web.port;
  gatus = "https://\${NAS_HOST}:4443";
  serve = "${pkgs.tailscale}/bin/tailscale serve";

  link = title: url: icon: { inherit title url icon; };
  host = "\${NAS_HOST}";
in
{
  services.glance = {
    enable = true;
    environmentFile = config.sops.templates."glance.env".path;
    settings = {
      server = {
        host = "127.0.0.1";
        inherit port;
        # tailscale serve sets X-Forwarded-For.
        proxied = true;
      };
      branding = {
        app-name = "NAS";
        hide-footer = true;
      };
      pages = [
        {
          name = "NAS";
          columns = [
            {
              size = "small";
              widgets = [
                {
                  type = "server-stats";
                  servers = [
                    {
                      type = "local";
                      name = "NAS";
                      hide-mountpoints-by-default = true;
                      mountpoints = {
                        "/" = {
                          name = "System (NVMe)";
                          hide = false;
                        };
                        "/tank/data" = {
                          name = "Data (8 TB disk)";
                          hide = false;
                        };
                      };
                    }
                  ];
                }
              ];
            }
            {
              size = "full";
              widgets = [
                # Gatus's latest result per check: one line when all pass,
                # else the failing ones, each linking to its history.
                {
                  type = "custom-api";
                  title = "Status";
                  title-url = gatus;
                  url = "http://127.0.0.1:${toString gatusPort}/api/v1/endpoints/statuses?page=1&pageSize=1";
                  cache = "1m";
                  template = ''
                    {{ $all := .JSON.Array "" }}
                    {{ $down := .JSON.Array "#(results.0.success==false)#" }}
                    {{ if eq (len $down) 0 }}
                      <a class="color-positive size-h3" href="${gatus}" target="_blank">All {{ len $all }} checks healthy</a>
                    {{ else }}
                      <a class="color-negative size-h3" href="${gatus}" target="_blank">{{ len $down }} of {{ len $all }} checks failing</a>
                      <ul class="list list-gap-10 margin-top-10">
                        {{ range $down }}
                          <li><a class="color-negative" href="${gatus}/endpoints/{{ .String "key" }}" target="_blank">{{ .String "group" }} · {{ .String "name" }}</a></li>
                        {{ end }}
                      </ul>
                    {{ end }}
                  '';
                }
                {
                  type = "bookmarks";
                  title = "Apps";
                  groups = [
                    {
                      title = "Media";
                      links = [
                        (link "Jellyfin" "http://${host}:8096" "di:jellyfin")
                        (link "Immich" "https://${host}:2443" "di:immich")
                        (link "VibeReel" "https://${host}:9443" "mdi:movie-open-play")
                      ];
                    }
                    {
                      title = "Documents";
                      links = [
                        (link "Paperless" "https://${host}:10443" "di:paperless-ngx")
                        (link "Vaultwarden" "https://${host}:6443" "di:vaultwarden")
                        (link "Calendars (Radicale)" "https://${host}:5443/.web/" "di:radicale")
                      ];
                    }
                    {
                      title = "Apps";
                      links = [
                        (link "Kirifin" "https://${host}" "mdi:finance")
                        (link "T3 Code" "https://${host}:7443" "mdi:robot-outline")
                        (link "ntfy" "https://${host}:8443" "di:ntfy")
                      ];
                    }
                    {
                      title = "Admin";
                      links = [
                        (link "Gatus" gatus "di:gatus")
                        (link "AdGuard Home" "https://${host}:3000" "di:adguard-home")
                        (link "Radarr" "http://${host}:7878" "di:radarr")
                        (link "Sonarr" "http://${host}:8989" "di:sonarr")
                        (link "Prowlarr" "http://${host}:9696" "di:prowlarr")
                        (link "qBittorrent" "http://${host}:8080" "di:qbittorrent")
                      ];
                    }
                  ];
                }
              ];
            }
          ];
        }
      ];
    };
  };

  sops.templates."glance.env" = {
    content = ''
      NAS_HOST=${config.sops.placeholder."reel-api/tailnet-host"}
    '';
    restartUnits = [ "glance.service" ];
  };

  systemd.services = {
    glance.serviceConfig.MemoryMax = "128M";

    # Same pattern as radicale.nix; `off` on stop clears only this port.
    glance-tailscale-serve = {
      description = "Publish Glance on port ${toString httpsPort} via tailscale serve";
      after = [
        "tailscaled.service"
        "glance.service"
      ];
      wants = [ "tailscaled.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        # Concurrent serve changes fail with "etag mismatch"; see gatus.nix.
        Restart = "on-failure";
        RestartSec = 3;
        ExecStart = "${serve} --bg --https=${toString httpsPort} http://127.0.0.1:${toString port}";
        ExecStop = "${serve} --https=${toString httpsPort} off";
      };
    };
  };
}
