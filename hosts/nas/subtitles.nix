#
# Automatic English subtitle fetching for new library additions.
#
# Why this exists: files whose only subtitles are image-based (PGS) force
# Jellyfin to burn them into the picture, which is a full video re-encode --
# it throws away Dolby Vision and pins the CPU. A sibling .srt makes the same
# subtitles free, rendered client-side. This keeps that true for anything new
# without you having to think about it.
#
# Triggered as soon as a file lands, so a new film has subtitles by the time
# you sit down: a recursive inotify watch sees copies and moves within seconds,
# and a five-minute poll sees the Radarr and Sonarr imports the watch misses.
# The daily timer is only a backstop for anything that slipped past both
# (e.g. files added while the box was off).
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (config.nas) mediaRoot;
  credsFile = config.sops.secrets."nas/opensubtitles-env".path;

  fetchSubtitles = pkgs.writers.writePython3Bin "fetch-subtitles" { flakeIgnore = [ "E501" ]; } (
    builtins.readFile ./fetch-subtitles.py
  );

  stripSubtitles = pkgs.writers.writePython3Bin "strip-subtitles" { flakeIgnore = [ "E501" ]; } (
    builtins.readFile ./strip-subtitles.py
  );

  prewarmSubtitles = pkgs.writers.writePython3Bin "prewarm-subtitles" { flakeIgnore = [ "E501" ]; } (
    builtins.readFile ./prewarm-subtitles.py
  );

  # The jobs are oneshots that run as jellyfin, inspect the library with
  # ffprobe/ffmpeg, write nothing but files next to the media, and yield to
  # whatever is playing. Only what differs between them is passed in.
  libraryJob =
    {
      description,
      exec,
      environment,
      timeout,
      nice,
      after ? [ ],
      onSuccess ? [ ],
      extraServiceConfig ? { },
    }:
    {
      inherit description after onSuccess;

      path = [
        pkgs.ffmpeg-headless
        pkgs.coreutils
      ];

      serviceConfig = {
        Type = "oneshot";
        User = "jellyfin";
        Group = "users";
        Environment = [ "MEDIA_ROOT=${mediaRoot}" ] ++ environment;
        ExecStart = lib.getExe exec;

        # Nothing here should ever wedge the box. Idle I/O scheduling keeps a
        # sweep from starving a stream that is playing off the same spindle --
        # the pool is a single disk until the mirror lands.
        TimeoutStartSec = timeout;
        Nice = nice;
        IOSchedulingClass = "idle";

        # They only ever write next to the media.
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        ReadWritePaths = [ mediaRoot ];
      }
      // extraServiceConfig;
    };

  # The inotify watcher below is the real trigger; these only catch what
  # slipped past it (e.g. files added while it was restarting).
  dailyBackstop = description: {
    inherit description;
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      Persistent = true; # run on boot if the box was off at the scheduled time
      RandomizedDelaySec = "30m";
    };
  };
in
{
  #############################################################################
  # The fetch job
  #############################################################################

  systemd.services.fetch-subtitles = libraryJob {
    description = "Fetch missing English subtitles from OpenSubtitles";

    # Strip first, then fetch. Both are triggered by the same watcher event, so
    # without this they race: fetch would inspect a file that still carries the
    # tracks about to be removed, decide it already has text subtitles, and skip
    # a download that the stripped file does need.
    after = [ "strip-subtitles.service" ];

    # ffprobe decides whether a file already has a *text* subtitle track; if it
    # does, no download is needed and none of the daily quota is spent.
    exec = fetchSubtitles;

    environment = [
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

    timeout = "30m";
    nice = 10;

    # Credentials come from sops, never plain in this repo -- it is public.
    # See the Credentials block at the bottom of this file.
    extraServiceConfig.EnvironmentFile = credsFile;
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

  systemd.services.strip-subtitles = libraryJob {
    description = "Remux new library files down to English and German subtitles, English audio first";
    exec = stripSubtitles;

    environment = [
      # ffprobe reports ISO 639-2/B; releases disagree about ger vs deu, and
      # a few tag with the two-letter code, so all the spellings are listed.
      "KEEP_LANGS=eng,en,ger,deu,de"
      # Asked which titles are anime (Japanese audio first instead of English).
      "SONARR_URL=http://127.0.0.1:8989"
      "RADARR_URL=http://127.0.0.1:7878"
      # Asked what is playing; the sweep waits until nothing is.
      "JELLYFIN_URL=http://127.0.0.1:8096"
    ];

    # Remuxing 4K files is pure I/O, so it gets the lower priority of the two.
    # Waiting out a film counts against the timeout too.
    timeout = "6h";
    nice = 15;

    # A rewritten file has lost its extracted subtitles in Jellyfin.
    onSuccess = [ "prewarm-subtitles.service" ];

    extraServiceConfig = {
      # The *arr API keys, straight from each app's config.xml -- the unit runs
      # as jellyfin, which cannot read /var/lib/{sonarr,radarr} itself.
      LoadCredential = [
        "sonarr.xml:/var/lib/sonarr/.config/NzbDrone/config.xml"
        "radarr.xml:/var/lib/radarr/.config/Radarr/config.xml"
      ];
      # JELLYFIN_TOKEN. Optional ("-"): without it the sweep runs, unwaiting.
      EnvironmentFile = "-${credsFile}";
    };
  };

  #############################################################################
  # The prewarm job
  #############################################################################
  #
  # Jellyfin extracts a file's embedded subtitles on the first request for any
  # of them: one ffmpeg pass over the whole file while the player waits. On
  # this pool that was nine minutes for a 28 GB remux, and the pass starved
  # the film's own stream off the same disk (Spider-Man, 2026-10-10). This
  # makes the request ahead of time, for recently changed files, and only
  # while nothing is playing.

  systemd.services.prewarm-subtitles = libraryJob {
    description = "Have Jellyfin extract new files' embedded subtitles before anyone plays them";
    exec = prewarmSubtitles;

    # After them, never alongside: strip rewrites files (new work for this
    # job), and all three read the library off the one disk.
    after = [
      "strip-subtitles.service"
      "fetch-subtitles.service"
    ];

    environment = [
      "JELLYFIN_URL=http://127.0.0.1:8096"
      # Older files still extract on first play. A whole-library sweep is
      # ALL=1, by hand and overnight: it reads every file, ~9 h on this pool.
      "WINDOW_DAYS=3"
    ];

    # Waiting out a film counts against this too.
    timeout = "12h";
    nice = 10;

    extraServiceConfig = {
      # JELLYFIN_TOKEN, required here (optional for fetch).
      EnvironmentFile = credsFile;
      # What has been warmed, by size and mtime.
      StateDirectory = "prewarm-subtitles";
      # It only stats the library; Jellyfin's ffmpeg does the reading.
      ReadWritePaths = [ ];
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

  # The watcher above saw neither Spider-Man nor Gone Girl arrive
  # (2026-10-10). Radarr and Sonarr put an import into a folder they create a
  # moment before, often before inotifywait has added its watch there, and a
  # hardlink import raises no event the watch uses anyway. Either way the
  # import sets the file's ctime (measured: to the second of Radarr's
  # "Assigning file"), so imports are found by polling for that. Metadata
  # only, the tree is in ARC: nothing to it.
  #
  # Strip's own rewrites are new files too, so each one brings one more sweep
  # that finds nothing to do, as with the watcher.
  systemd.services.subtitle-poll = {
    description = "Start subtitle stripping and fetching for newly imported library files";
    after = [ "zfs-mount.service" ];
    path = [
      pkgs.coreutils
      pkgs.findutils
      pkgs.systemd
    ];
    serviceConfig = {
      Type = "oneshot";
      StateDirectory = "subtitle-poll";
    };
    script = ''
      stamp="$STATE_DIRECTORY/last-run"
      # Taken before the scan, so a file linked during it is seen next time.
      now=$(date +%s)
      if [ -e "$stamp" ] && [ -n "$(find ${mediaRoot} -name '.*' -prune -o \
          \( -name '*.mkv' -o -name '*.mp4' \) -cnewer "$stamp" -print -quit)" ]; then
        systemctl start --no-block strip-subtitles.service fetch-subtitles.service
      fi
      touch -d "@$now" "$stamp"
    '';
  };

  systemd.timers.subtitle-poll = {
    description = "Look for newly imported library files every five minutes";
    wantedBy = [ "timers.target" ];
    timerConfig.OnCalendar = "*:0/5";
  };

  systemd.timers.fetch-subtitles = dailyBackstop "Daily backstop for subtitle fetching";
  systemd.timers.strip-subtitles = dailyBackstop "Daily backstop for subtitle stripping";

  # Strip's success starts prewarm, but a new file may not be in Jellyfin yet
  # then, and a rewritten one not rescanned -- both are left for a later run,
  # which this provides. A run with nothing new is one /Items query.
  systemd.timers.prewarm-subtitles = {
    description = "Look for new library files to prewarm subtitles for";
    wantedBy = [ "timers.target" ];
    timerConfig.OnCalendar = "*:0/15";
  };

  #############################################################################
  # Credentials
  #############################################################################
  #
  # An env file in sops (nas/opensubtitles-env in secrets/nas.yaml):
  #
  #   OS_API_KEY=...
  #   OS_USER=...
  #   OS_PASS=...
  #   JELLYFIN_TOKEN=...
  #
  # Root-owned is fine although the job runs as jellyfin: systemd reads
  # EnvironmentFile as PID 1, before dropping privileges. Without it the unit
  # fails to start; that is deliberate and visible rather than silently doing
  # nothing. JELLYFIN_TOKEN is optional -- omit it and the fetch still works,
  # it just will not trigger a library rescan afterwards.
  sops.secrets."nas/opensubtitles-env".sopsFile = ../../secrets/nas.yaml;
}
