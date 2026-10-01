#
# Reap Sonarr/Radarr downloads that will never finish.
#
# Why this exists: public swarms die. A release with zero seeders sits in
# qBittorrent at "stalled" or "downloading metadata" forever, and the *arrs
# notice ("The download is stalled with no connections") but never act on it
# -- no failure, no blocklist, no search for another release. Measured
# 2026-09: ten such torrents had sat for up to four weeks, and because they
# held the front of qBittorrent's queue, five healthy grabs with 6-144
# seeders were parked behind them in queuedDL the whole time
# (IgnoreSlowTorrentsForQueueing in arr.nix did not free those slots).
#
# What it does (details in reap-stalled.sh): anything an *arr is waiting on
# that has had no metadata for 6 h, or no seeder, no full copy among its peers
# and no verified progress for 24 h, has its finished files imported first
# and is then removed through the *arr queue with blocklist + re-search.
# Blocklisting is per release, so the next search is free to pick any other
# release of the same episode or film.
#
# Hourly is plenty: the grace periods are hours long, and each run is a
# handful of local API calls.
#
{ lib, pkgs, ... }:
let
  reapStalled = pkgs.writeShellApplication {
    name = "reap-stalled";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.curl
      pkgs.gnused
      pkgs.jq
    ];
    text = builtins.readFile ./reap-stalled.sh;
  };
in
{
  systemd.services.reap-stalled = {
    description = "Remove dead Sonarr/Radarr downloads, blocklist and re-search";
    after = [
      "sonarr.service"
      "radarr.service"
      "qbittorrent.service"
    ];

    environment = {
      # qBittorrent lives in the VPN namespace (vpn.nix); host-side clients
      # reach it over the veth, where the WebUI auth whitelist lets them in.
      QBIT_URL = "http://192.168.15.1:8080";
      SONARR_URL = "http://127.0.0.1:8989";
      RADARR_URL = "http://127.0.0.1:7878";
      META_GRACE_H = "6";
      STALL_GRACE_H = "24";
    };

    serviceConfig = {
      Type = "oneshot";
      ExecStart = lib.getExe reapStalled;

      # The API keys are read from each app's own config.xml, handed over as
      # credentials so the unit itself can run as a throwaway user with no
      # access to /var/lib/{sonarr,radarr}.
      LoadCredential = [
        "sonarr.xml:/var/lib/sonarr/.config/NzbDrone/config.xml"
        "radarr.xml:/var/lib/radarr/.config/Radarr/config.xml"
      ];
      # progress.json: per-torrent verified byte count and when it last moved.
      StateDirectory = "reap-stalled";

      DynamicUser = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      NoNewPrivileges = true;
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
      ];
      # Salvage imports can take a while on 4K season packs (see the poll
      # loop in reap-stalled.sh).
      TimeoutStartSec = "2h";
    };
  };

  systemd.timers.reap-stalled = {
    description = "Hourly sweep for dead Sonarr/Radarr downloads";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "hourly";
      Persistent = true;
      RandomizedDelaySec = "5m";
    };
  };
}
