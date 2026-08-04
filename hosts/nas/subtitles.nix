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

  stripSubtitles = pkgs.writers.writePython3Bin "strip-subtitles"
    { flakeIgnore = [ "E501" ]; }
    (builtins.readFile ./strip-subtitles.py);
in
{
  #############################################################################
  # The fetch job
  #############################################################################

  systemd.services.fetch-subtitles = {
    description = "Fetch missing English subtitles from OpenSubtitles";

    # Strip first, then fetch. Both are triggered by the same watcher event, so
    # without this they race: fetch would inspect a file that still carries the
    # tracks about to be removed, decide it already has text subtitles, and skip
    # a download that the stripped file does need.
    after = [ "strip-subtitles.service" ];

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
  # The strip job
  #############################################################################
  #
  # Releases ship thirty-odd subtitle tracks. ffmpeg must identify every stream
  # before it can transcode, and subtitle streams are sparse -- a track emits a
  # packet only when a line of dialogue appears -- so it reads deep into the
  # file before the first video frame exists. On this pool that is most of ten
  # seconds of startup lag, paid again on every seek. Dropping the tracks
  # nobody here reads is the cheapest fix available; nothing is re-encoded.

  systemd.services.strip-subtitles = {
    description = "Remux new library files down to English and German subtitles";

    path = [
      pkgs.ffmpeg-headless
      pkgs.coreutils
    ];

    serviceConfig = {
      Type = "oneshot";
      User = "jellyfin";
      Group = "users";

      Environment = [
        "MEDIA_ROOT=${mediaRoot}"
        # ffprobe reports ISO 639-2/B; releases disagree about ger vs deu, and
        # a few tag with the two-letter code, so all the spellings are listed.
        "KEEP_LANGS=eng,en,ger,deu,de"
      ];

      ExecStart = lib.getExe stripSubtitles;

      # Remuxing 4K files is pure I/O. Idle scheduling keeps a sweep from
      # starving a stream that is playing off the same spindle -- the pool is
      # a single disk until the mirror lands.
      TimeoutStartSec = "6h";
      Nice = 15;
      IOSchedulingClass = "idle";

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
          # of a whole season coalesces into a single sweep -- both scripts scan
          # the entire tree anyway.
          #
          # Stripping rewrites the file, which lands as a rename and trips this
          # same watch a second time. That pass is a no-op: a file with nothing
          # left to drop is skipped without being rewritten, so it emits no
          # further event and the loop closes itself after one extra scan.
          systemctl start --no-block strip-subtitles.service || true
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

  systemd.timers.strip-subtitles = {
    description = "Daily backstop for subtitle stripping";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      Persistent = true;
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
