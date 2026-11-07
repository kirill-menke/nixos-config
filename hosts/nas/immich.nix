#
# Immich: photo and video library with a timeline, map, albums, faces and
# search, for the iPhone app (which also backs the phone up) and the browser.
#
# Reached over the tailnet only: Immich listens on loopback and `tailscale
# serve` publishes it as https://nas.<tailnet>.ts.net:2443, the server URL for
# the app. The phone needs Tailscale connected to back up.
#
# The library is a read-only "external library" at /tank/data/photos: a copy of
# the PC's ~/Pictures/photos, which stays the master. Immich never
# writes there; its nightly library scan picks up what is synced in later.
# Syncthing keeps it in step with the PC since 2026-11-07 (receive-only folder
# "photos" in syncthing.nix); new files land 0644 with group immich (setgid dir).
#
# Phone uploads land in /tank/data/immich/upload. tank is still a single disk,
# so their only second copy is the off-site backup (backup.nix).
#
# Memory: this box has 7.5 GiB and many residents. Immich, its Postgres and its
# Redis share system-immich.slice, capped at 2.5 GiB of RAM (throttled from
# 2 GiB) and 1.5 GiB of swap, so an overrun gets Immich OOM-killed and
# restarted (Restart=on-failure) instead of squeezing everything else
# (2026-10-31: a memory burst stalled SSH and AdGuard DNS for ~15 minutes).
# Measured on Immich 3.3.5 without machine learning: ~1.1 GiB idle, ~1.6 GiB
# while processing.
#
# Machine learning (faces, smart search, OCR) does not run here at all: the PC
# serves it (hosts/pc/immich-ml.nix). Its URL comes from sops (immich/ml-url),
# since the tailnet address stays out of this public repo. While the PC is off
# those jobs fail; immich-ml-catchup queues the missing ones hourly once the
# PC answers again, but only for a queue that is idle (a busy one already holds
# those assets, and queueing them again would duplicate the work). It needs an
# admin API key with the job.read and job.create permissions in
# /var/lib/nixos-secrets/immich-api-key, as the header line `x-api-key: <key>`
# (root, 0600); without that file it stays inactive.
#
# Videos transcode on the iGPU with Quick Sync, the driver stack media.nix sets
# up for Jellyfin. renderD128 is world-accessible (0666), so PrivateUsers needs
# no group mapping.
#
# Where things live:
#   /var/lib/postgresql   the database: albums, faces, metadata (NVMe)
#   /tank/data/immich     uploads, thumbnails, transcoded videos, and nightly
#                         database dumps in backups/ (HDD)
#   /tank/data/photos     the external library
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  port = 2283; # loopback
  # Tailnet, via tailscale serve. Taken there: 443 = finance, 3000 = AdGuard,
  # 6443 = Vaultwarden, 7443 = T3 Code, 8443 = ntfy, 9443 = vibereel-phone,
  # 10443 = Paperless.
  httpsPort = 2443;
  mediaDir = "/tank/data/immich";
  photosDir = "/tank/data/photos";
  apiKeyFile = "/var/lib/nixos-secrets/immich-api-key";
  serve = "${pkgs.tailscale}/bin/tailscale serve";

  mlCatchup = pkgs.writeShellApplication {
    name = "immich-ml-catchup";
    runtimeInputs = [
      pkgs.curl
      pkgs.jq
    ];
    text = ''
      url=$(<"$CREDENTIALS_DIRECTORY/ml-url")
      if ! curl -fsS --max-time 5 "$url/ping" >/dev/null; then
        echo "Machine learning at the PC is not reachable; nothing queued."
        exit 0
      fi
      # The key travels in a header file, never on the command line.
      api=http://127.0.0.1:${toString port}/api
      key=$CREDENTIALS_DIRECTORY/api-key
      queues=$(curl -fsS --max-time 15 -H @"$key" "$api/jobs")
      for queue in smartSearch faceDetection ocr; do
        pending=$(jq -r --arg q "$queue" '.[$q].jobCounts | .active + .waiting + .delayed' <<<"$queues")
        if [ "$pending" -gt 0 ]; then
          echo "$queue: $pending jobs still pending, left alone."
          continue
        fi
        # force=false: only assets the job never finished for.
        curl -fsS --max-time 15 -X PUT -H @"$key" \
          -H 'Content-Type: application/json' \
          -d '{"command":"start","force":false}' \
          "$api/jobs/$queue" >/dev/null
        echo "$queue: queued the missing assets."
      done
    '';
  };
in
{
  services.immich = {
    enable = true;
    host = "127.0.0.1";
    inherit port;
    mediaLocation = mediaDir;
    machine-learning.enable = false;
    accelerationDevices = [ "/dev/dri/renderD128" ];

    # Upstream's start.sh preloads mimalloc; nixpkgs runs node directly, on
    # glibc malloc, whose per-thread arenas fragment under sharp/libvips. The
    # first scan without it grew the worker to 1.25 GB RSS and the slice to its
    # 1.75 GiB MemoryHigh, where throttling stalled the API (2026-11-03).
    environment.LD_PRELOAD = "${pkgs.mimalloc}/lib/libmimalloc.so";

    # A config file makes these read-only in the web UI; change them here.
    settings = {
      ffmpeg = {
        accel = "qsv";
        accelDecode = true;
      };
      # Defaults are 3 thumbnails / 5 metadata / 5 library jobs at once. One
      # thumbnail at a time (libvips still uses every core per image) keeps
      # big HEIC/DNG decode buffers inside the 2 GiB cap during the import.
      job = {
        thumbnailGeneration.concurrency = 1;
        metadataExtraction.concurrency = 2;
        library.concurrency = 1;
        videoConversion.concurrency = 1;
      };
      machineLearning.urls = [ { _secret = config.sops.secrets."immich/ml-url".path; } ];
    };
  };

  sops.secrets."immich/ml-url" = {
    sopsFile = ../../secrets/nas.yaml;
    restartUnits = [ "immich-server.service" ];
  };

  users.users.kirill.extraGroups = [ "immich" ];

  systemd.tmpfiles.settings.immich-dirs = {
    # The module only fixes the mode of an existing media dir; it doesn't
    # create one outside /var/lib.
    ${mediaDir}.d = {
      user = "immich";
      group = "immich";
      mode = "0700";
    };
    # setgid: files synced in inherit the immich group, which can read them.
    ${photosDir}.d = {
      user = "kirill";
      group = "immich";
      mode = "2750";
    };
  };

  # Swap room matters as much as RAM: Quick Sync keeps its video frames in GPU
  # buffers that count as shmem, which only swapping can evict. With 512 MiB of
  # swap the slice hit that cap during the import (shmem 530 MB), sat at
  # MemoryHigh with nothing reclaimable, and stalled for an hour: two iPhone
  # clips took 37 minutes, the API timed out, memory pressure ~33 %
  # (2026-11-04, about 00:40-01:20 CET). These limits ended it within a minute.
  systemd.slices.system-immich.sliceConfig = {
    MemoryHigh = "2G";
    MemoryMax = "2560M";
    MemorySwapMax = "1536M";
  };

  systemd.services = {
    # Never write uploads into the root fs's empty mountpoint when tank is
    # missing.
    immich-server.unitConfig.RequiresMountsFor = [
      mediaDir
      photosDir
    ];

    # Only Immich uses Postgres and this Redis on this box, so they count
    # against Immich's cap.
    postgresql.serviceConfig.Slice = "system-immich.slice";
    redis-immich.serviceConfig.Slice = "system-immich.slice";

    # Same pattern as vaultwarden.nix; `off` on stop clears only this port.
    immich-tailscale-serve = {
      description = "Publish Immich on port ${toString httpsPort} via tailscale serve";
      after = [
        "tailscaled.service"
        "immich-server.service"
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

    immich-ml-catchup = {
      description = "Queue Immich's missing machine-learning jobs once the PC answers";
      after = [ "immich-server.service" ];
      unitConfig.ConditionPathExists = apiKeyFile;
      serviceConfig = {
        Type = "oneshot";
        DynamicUser = true;
        LoadCredential = [
          "ml-url:${config.sops.secrets."immich/ml-url".path}"
          "api-key:${apiKeyFile}"
        ];
        ExecStart = lib.getExe mlCatchup;
      };
    };
  };

  systemd.timers.immich-ml-catchup = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "hourly";
      RandomizedDelaySec = "5m";
    };
  };
}
