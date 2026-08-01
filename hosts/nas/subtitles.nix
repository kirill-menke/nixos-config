#
# Automatic English subtitle fetching for new library additions.
#
# Why this exists: files whose only subtitles are image-based (PGS) force
# Jellyfin to burn them into the picture, which is a full video re-encode --
# it throws away Dolby Vision and pins the CPU. A sibling .srt makes the same
# subtitles free, rendered client-side. This keeps that true for anything new
# without you having to think about it.
#
# Event-driven rather than polled: a recursive inotify watch triggers the fetch
# within seconds of a file landing, so a new film has subtitles by the time you
# sit down. The daily timer is only a backstop for anything that slipped past
# the watcher (e.g. files added while it was restarting).
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  mediaRoot = "/tank/data/media";
  credsFile = "/var/lib/nixos-secrets/opensubtitles.env";

  fetchSubtitles = pkgs.writers.writePython3Bin "fetch-subtitles"
    { flakeIgnore = [ "E501" ]; }
    (builtins.readFile ./fetch-subtitles.py);
in
{
  #############################################################################
  # The fetch job
  #############################################################################

  systemd.services.fetch-subtitles = {
    description = "Fetch missing English subtitles from OpenSubtitles";

    # ffprobe decides whether a file already has a *text* subtitle track; if it
    # does, no download is needed and none of the daily quota is spent.
    path = [
      pkgs.ffmpeg-headless
      pkgs.coreutils
    ];

    serviceConfig = {
      Type = "oneshot";
      User = "jellyfin";
      Group = "users";

      # Credentials live on the machine, never in this repo -- it is public.
      # Provision once (see the README block at the bottom of this file).
      EnvironmentFile = credsFile;

      Environment = [
        "MEDIA_ROOT=${mediaRoot}"
        # Each language is fetched independently, so a file that already has an
        # English text track still gets German pulled. Costs one download per
        # language per file, so the backlog trickles across days.
        "OS_LANGS=en,de"
        # Free tier is 20 downloads/day. Ten per run leaves headroom for a
        # second batch the same day and means a big import trickles in rather
        # than erroring out halfway.
        "OS_MAX_PER_RUN=10"
        "JELLYFIN_URL=http://127.0.0.1:8096"
      ];

      ExecStart = lib.getExe fetchSubtitles;

      # Nothing here should ever wedge the box.
      TimeoutStartSec = "30m";
      Nice = 10;
      IOSchedulingClass = "idle";

      # It only ever writes .srt files next to the media.
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      NoNewPrivileges = true;
      ReadWritePaths = [ mediaRoot ];
    };
  };

  #############################################################################
  # Trigger: filesystem events, with a daily backstop
  #############################################################################

  # Recursive inotify watch. systemd's own .path units cannot do this -- they
  # watch a single directory and do not recurse, which would miss
  # shows/<Series>/<Season N>/<episode>.mkv entirely.
  systemd.services.subtitle-watch = {
    description = "Watch the library and trigger subtitle fetching";
    wantedBy = [ "multi-user.target" ];
    after = [ "zfs-mount.service" ];
    path = [
      pkgs.inotify-tools
      pkgs.systemd
    ];
    serviceConfig = {
      Restart = "always";
      RestartSec = 10;
      Nice = 10;
    };
    script = ''
      # close_write catches a direct write finishing; moved_to catches rsync
      # and mv, which write to a temp name and rename on completion. Both mean
      # the file is whole -- watching `create` would fire on empty files.
      inotifywait -m -r -q \
        -e close_write -e moved_to \
        --format '%w%f' ${mediaRoot} \
      | while read -r f; do
          case "$f" in
            *.mkv|*.mp4) ;;
            *) continue ;;
          esac
          # A oneshot already running will not be started twice, so a bulk copy
          # of a whole season coalesces into a single sweep -- the script scans
          # the entire tree anyway.
          systemctl start --no-block fetch-subtitles.service || true
        done
    '';
  };

  systemd.timers.fetch-subtitles = {
    description = "Daily backstop for subtitle fetching";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      Persistent = true; # run on boot if the box was off at the scheduled time
      RandomizedDelaySec = "30m";
    };
  };

  #############################################################################
  # Credentials
  #############################################################################
  #
  # Unmanaged state, like /var/lib/nixos-secrets/root.hash. Provision once:
  #
  #   install -d -m700 /var/lib/nixos-secrets
  #   cat > /var/lib/nixos-secrets/opensubtitles.env <<'EOF'
  #   OS_API_KEY=...
  #   OS_USER=...
  #   OS_PASS=...
  #   JELLYFIN_TOKEN=...
  #   EOF
  #   chmod 600 /var/lib/nixos-secrets/opensubtitles.env
  #   chown jellyfin:users /var/lib/nixos-secrets/opensubtitles.env
  #
  # Without it the unit fails to start; that is deliberate and visible rather
  # than silently doing nothing. JELLYFIN_TOKEN is optional -- omit it and the
  # fetch still works, it just will not trigger a library rescan afterwards.
}
