#
# Paperless-ngx: OCR, search and filing for every scanned document.
#
# Flow: the Brother ADS-1800W writes PDFs into the Samba share `scans`
# (scans.nix), which is the consumption directory here. The consumer takes a
# file once it has been unchanged for 5 s, OCRs it (German + English), files it
# and deletes it from the share. A stack holding several documents is split at
# PATCH-T separator sheets (a page carrying a "PATCHT" barcode); the separator
# pages themselves are dropped.
#
# Where things live:
#   /var/lib/paperless           SQLite database, search index, originals and
#                                OCR'd archive PDFs (NVMe)
#   /tank/data/paperless-export  nightly document_exporter dump (HDD): a second
#                                copy on another disk, and what
#                                `paperless-manage document_importer` restores
#
# Access:
#   http://nas.local:28981                LAN (firewall opens it on enp2s0 only)
#   https://nas.<tailnet>.ts.net:10443    phone, via tailscale serve (below)
# Login: kirill / sops paperless/admin-password (secrets/nas.yaml).
#
{ config, pkgs, ... }:
let
  inherit (config.services.paperless) port;
  # 443 = finance, 8443 = ntfy, 9443 = vibereel-phone, 3000 = AdGuard.
  httpsPort = 10443;
  lanIf = "enp2s0";
  serve = "${pkgs.tailscale}/bin/tailscale serve";
in
{
  services.paperless = {
    enable = true;
    # All interfaces, so a boot never races the static LAN address; the
    # firewall below admits only the LAN, and tailscale0 is trusted anyway.
    address = "0.0.0.0";
    consumptionDir = config.nas.scanRoot;
    passwordFile = config.sops.secrets."paperless/admin-password".path;
    environmentFile = config.sops.templates."paperless.env".path;

    settings = {
      PAPERLESS_ADMIN_USER = "kirill";
      PAPERLESS_TIME_ZONE = "Europe/Berlin";
      PAPERLESS_OCR_LANGUAGE = "deu+eng";
      PAPERLESS_CONSUMER_ENABLE_BARCODES = true;
      PAPERLESS_FILENAME_FORMAT = "{{ created_year }}/{{ correspondent }}/{{ title }}";

      # The N150 has 4 cores and no SMT, shared with Jellyfin (media.nix gives
      # ffmpeg 3 threads). One document at a time, two pages in parallel, so a
      # big scanning backlog cannot starve playback.
      PAPERLESS_TASK_WORKERS = 1;
      PAPERLESS_THREADS_PER_WORKER = 2;
    };

    # Runs nightly at 01:30; stops the Paperless services for its duration.
    exporter = {
      enable = true;
      directory = "/tank/data/paperless-export";
    };
  };

  # Admin login. Changing the secret re-runs the module's manage_superuser on
  # the next scheduler start.
  sops.secrets."paperless/admin-password" = {
    sopsFile = ../../secrets/nas.yaml;
    restartUnits = [ "paperless-scheduler.service" ];
  };

  # Django refuses POSTs (the login form) whose Origin is not this host. Behind
  # tailscale serve the browser sends https://nas.<tailnet>.ts.net:10443 while
  # Paperless sees plain http, so that origin must be trusted explicitly. The
  # tailnet name stays out of this public repo: same sops value as reel-api.nix.
  sops.templates."paperless.env" = {
    content = ''
      PAPERLESS_CSRF_TRUSTED_ORIGINS=https://${
        config.sops.placeholder."reel-api/tailnet-host"
      }:${toString httpsPort}
    '';
    # paperless-manage (and the exporter) source it as the paperless user.
    owner = config.services.paperless.user;
    restartUnits = [ "paperless-web.service" ];
  };

  networking.firewall.interfaces.${lanIf}.allowedTCPPorts = [ port ];

  # Same pattern as adguard.nix; `off` on stop clears only this port.
  systemd.services.paperless-tailscale-serve = {
    description = "Publish Paperless on port ${toString httpsPort} via tailscale serve";
    after = [
      "tailscaled.service"
      "paperless-web.service"
    ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${serve} --bg --https=${toString httpsPort} http://127.0.0.1:${toString port}";
      ExecStop = "${serve} --https=${toString httpsPort} off";
    };
  };
}
