#
# Paperless-ngx: OCR, search and filing for every scanned document.
#
# Flow: the Brother ADS-1800W writes PDFs into the Samba share `scans`
# (scans.nix), which is the consumption directory here; files copied into
# /mnt/paperless/inbox on the PC land there too (paperless-view.nix). The
# consumer takes a file once it has been unchanged for 5 s, OCRs it (German +
# English), files it and deletes it from the share. A stack holding several
# documents is split at PATCH-T separator sheets (a page carrying a "PATCHT"
# barcode); the separator pages themselves are dropped. Scanner stacks go in
# face up, in reading order (see reverseScanPages below).
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
#   /mnt/paperless on the PC              the documents as files, read-only,
#                                         and inbox/ (paperless-view.nix)
# Login: kirill / sops paperless/admin-password (secrets/nas.yaml).
#
{ config, pkgs, ... }:
let
  inherit (config.services.paperless) port;
  # 443 = finance, 8443 = ntfy, 9443 = vibereel-phone, 3000 = AdGuard.
  httpsPort = 10443;
  lanIf = "enp2s0";
  serve = "${pkgs.tailscale}/bin/tailscale serve";

  # The ADS-1800W wants stacks face down, top edge first: it feeds the sheet
  # facing its front, so page 1 has to sit at the bottom. The stack is loaded
  # the way it is read instead: face up, page 1 on top, bottom edge first
  # (since 2026-11-02; before that top edge first). The scanner then feeds the
  # last sheet first and its front sensor sees each sheet's back side, so the
  # images come out in exactly reverse order (last back side first) and upside
  # down. This reverses and turns every page; the scanner has no page-order
  # setting.
  #
  # Runs as the pre-consume script, i.e. after the PATCH-T split, once per
  # resulting document; split parts keep the source's file name stem. Only the
  # scanner's own files (named after its node name) are touched, so web/API
  # uploads keep their order. Paperless stores the modified working copy as the
  # original.
  reverseScanPages = pkgs.writeShellApplication {
    name = "paperless-reverse-scan-pages";
    runtimeInputs = [ pkgs.qpdf ];
    text = ''
      case "''${DOCUMENT_SOURCE_PATH##*/}" in
        BRW5CF370E4AA70_*.pdf) ;;
        *) exit 0 ;;
      esac
      qpdf --warning-exit-0 --replace-input "$DOCUMENT_WORKING_PATH" --pages . z-1 -- --rotate=+180
    '';
  };
in
{
  imports = [
    ./paperless-ai.nix
    ./paperless-view.nix
  ];

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
      PAPERLESS_PRE_CONSUME_SCRIPT = "${reverseScanPages}/bin/paperless-reverse-scan-pages";

      # The N150 has 4 cores and no SMT, shared with Jellyfin (media.nix gives
      # ffmpeg 3 threads). Two documents at a time, two pages each, so a
      # scanning backlog uses all four cores; RAM is the real limit, see the
      # memory cap below and zramSwap in default.nix.
      PAPERLESS_TASK_WORKERS = 2;
      PAPERLESS_THREADS_PER_WORKER = 2;

      # No unpaper pass before OCR: it is single-threaded, ran at 100% CPU on
      # every page and needs ~250 MB per worker, while the ADS-1800W delivers
      # clean ADF scans that gain nothing from it.
      PAPERLESS_OCR_CLEAN = "none";
    };

    # Runs nightly at 01:30; stops the Paperless services for its duration.
    exporter = {
      enable = true;
      directory = "/tank/data/paperless-export";
    };
  };

  # Two OCR workers sit around 1.3-1.5 GB. Above MemoryHigh the workers are
  # throttled (their memory goes to zram), at MemoryMax the kernel kills inside
  # this unit (one failed consume task) instead of starving AdGuard, the
  # microVMs and the rest of the box.
  systemd.services.paperless-task-queue.serviceConfig = {
    MemoryHigh = "2000M";
    MemoryMax = "2800M";
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
