# `play <file>` -- one command for watching anything.  It probes the source
# and matches the output to it, so there is nothing to remember:
#
#   not playing to the TV   -> plain mpv, nothing else touched
#   playing to the TV       -> 4K at the refresh closest to the file's frame
#                              rate (23.976 content gets a 23.98 Hz output,
#                              so no judder), restored when you quit
#   HDR source (PQ or HLG)  -> 10-bit framebuffer + cm,hdr, so HDR actually
#                              reaches the panel instead of being tone-mapped
#   TrueHD source           -> bitstreamed untouched to the soundbar
#
# Only mpv can do the HDR part.  Celluloid embeds mpv through the render API,
# which forces `vo/libmpv` (it overrides --mpv-vo=gpu-next), so its GTK
# surface never requests an HDR colorspace.
#
# Called from ./tv.nix, which passes hypr-monitor, tv and the bar's
# nightlight (waybar-popups).
{
  lib,
  writeShellScriptBin,
  coreutils,
  util-linux,
  findutils,
  gnused,
  gawk,
  mpv,
  hyprland,
  jq,
  ffmpeg,
  socat,
  wireplumber,
  hypr-monitor,
  tv,
  waybar-popups,
}:

writeShellScriptBin "play" ''
  # Do not rely on the caller's PATH: without these, setsid/timeout/find/sed
  # can be missing, and play exits instantly while the launch still looks
  # like it succeeded.
  export PATH=${
    lib.makeBinPath [
      coreutils
      util-linux
      findutils
      gnused
      gawk
    ]
  }:$PATH

  MON=HDMI-A-2
  MPV=${mpv}/bin/mpv
  HYPR=${hyprland}/bin/hyprctl
  JQ=${jq}/bin/jq
  FFPROBE=${ffmpeg}/bin/ffprobe
  SOCAT=${socat}/bin/socat
  HYPRMON=${hypr-monitor}/bin/hypr-monitor

  # Fixed path so a later `play` can find the running player to hand it a new
  # playlist or stop it.  Every mpv started here gets it, including the plain
  # non-TV paths.
  RUNTIME="''${XDG_RUNTIME_DIR:-/tmp}"
  SOCK="$RUNTIME/play.sock"
  IPC=(--input-ipc-server="$SOCK")

  # Who owns the TV right now.  Without this, a second `play` simply started a
  # second mpv: both bound the same IPC socket, so only the newer one could be
  # reached through it, both fought over the audio device, and -- worst --
  # the second wrapper captured the *movie* mode as the mode to restore, so
  # quitting left the desktop at 4K23.98.
  PIDFILE="$RUNTIME/play.pid"          # the wrapper that owns the display
  MPVPIDFILE="$RUNTIME/play.mpvpid"    # its mpv, for when IPC will not answer
  STATEFILE="$RUNTIME/play.state"      # output profile, see $profile below
  ORIGFILE="$RUNTIME/play.orig"        # desktop mode to put back
  HANDOFF="$RUNTIME/play.handoff"      # set by a successor: skip the restore
  LOCKFILE="$RUNTIME/play.lock"

  # Detach from the terminal so it can be closed while the film keeps
  # playing.  This wrapper has to outlive mpv (it restores the display mode
  # on exit), so a plain background job would die with the shell's SIGHUP --
  # setsid -f puts it in its own session instead.
  #
  # Re-exec rather than fork at the end: everything below, including the
  # window placement that HDR depends on, then runs detached too.
  LOG="$RUNTIME/play.log"
  if [ -z "''${PLAY_DETACHED:-}" ]; then
    # Fail loudly *before* detaching, otherwise a typo just vanishes.
    for a in "$@"; do
      case "$a" in
        -*) ;;
        *://*) ;;
        *) [ -e "$a" ] || { echo "play: no such file or directory: $a" >&2; exit 1; } ;;
      esac
    done
    PLAY_DETACHED=1 setsid -f "$0" "$@" </dev/null >"$LOG" 2>&1
    echo "play: started in the background (log: $LOG)"
    exit 0
  fi

  # Every argument that exists on disk is a media file; anything else is
  # passed through to mpv untouched.  More than one is a playlist, such as
  # the rest of a season.  The first one is what the output gets matched to
  # -- episodes of a season share a frame rate, HDR type and audio codec, so
  # probing them all would be work for nothing.
  #
  # mpv happily takes a directory and plays it as a playlist, but ffprobe
  # cannot read one -- the probe then silently returns nothing and every
  # decision below quietly degrades (no passthrough, no HDR).  Resolve a
  # directory to the largest video file inside it, which is the feature
  # rather than a sample or an extra.  Hand mpv the resolved file too: it
  # treats a directory as a playlist, so after the feature it walks into
  # things like "Additional Languages" and carries on playing audio-only.
  args=("$@")
  files=()
  for i in "''${!args[@]}"; do
    a="''${args[$i]}"
    case "$a" in -*) continue ;; esac
    [ -e "$a" ] || continue
    if [ -d "$a" ]; then
      biggest=$(find "$a" -maxdepth 2 -type f \
                  \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.m2ts' \
                     -o -iname '*.avi' -o -iname '*.mov' -o -iname '*.webm' \) \
                  -printf '%s\t%p\n' 2>/dev/null | sort -rn | head -n 1 | cut -f2-)
      if [ -n "$biggest" ]; then
        a="$biggest"
        args[$i]="$biggest"
      fi
    fi
    files+=("$a")
  done
  file="''${files[0]:-}"

  # --- talking to a player that is already up -------------------------------

  mpv_ipc() {
    printf '%s\n' "$1" | timeout 2 $SOCAT - "UNIX-CONNECT:$SOCK" 2>/dev/null
  }

  mpv_alive() {
    [ -S "$SOCK" ] || return 1
    case "$(mpv_ipc '{"command":["get_property","mpv-version"]}')" in
      *'"error":"success"'*) return 0 ;;
      *) return 1 ;;
    esac
  }

  pid_alive() {
    local p
    p=$(cat "$PIDFILE" 2>/dev/null) || return 1
    [ -n "$p" ] || return 1
    kill -0 "$p" 2>/dev/null
  }

  # Replace the running player's playlist with ours.  jq builds the JSON so
  # that release names full of brackets, quotes and apostrophes survive.
  swap_playlist() {
    local f how=replace
    for f in "''${files[@]}"; do
      mpv_ipc "$($JQ -nc --arg f "$f" --arg m "$how" '{command:["loadfile",$f,$m]}')" >/dev/null
      how=append
    done
    # A player that was left paused would otherwise sit on the new film's
    # first frame.
    mpv_ipc '{"command":["set_property","pause",false]}' >/dev/null
  }

  # Take the TV from whoever has it.  Returns 0 when the running player was
  # reused (nothing more to do), 1 when the caller should start its own.
  inherited=""
  takeover() {
    local want="$1" have
    have=$(cat "$STATEFILE" 2>/dev/null || true)

    if pid_alive; then
      # The file guard matters for `play <url>`: nothing on disk means
      # nothing to swap in, so that has to start a player of its own.
      if mpv_alive && [ "$have" = "$want" ] && [ ''${#files[@]} -gt 0 ]; then
        # Same output setup, so the running mpv is already configured
        # correctly: swapping the file in keeps the display mode, the HDR
        # colorspace and the open passthrough device exactly as they are.
        swap_playlist
        return 0
      fi
      # It has to go.  Tell its wrapper not to put the desktop mode back on
      # the way out: we are about to set our own, and restoring in between
      # costs two extra TV resyncs.  Its saved desktop mode is inherited
      # instead -- ours would otherwise be the movie mode still on screen.
      touch "$HANDOFF"
    fi
    # An mpv whose wrapper is gone (killed, or crashed before its trap ran)
    # still owns the audio device and the screen, so it is quit either way.
    # Its $ORIGFILE is the best record of the desktop mode there is: the
    # monitor is sitting in movie mode right now, so reading hyprctl would
    # just save that as the thing to restore.
    if mpv_alive || pid_alive; then
      inherited=$(cat "$ORIGFILE" 2>/dev/null || true)
      mpv_ipc '{"command":["quit"]}' >/dev/null
      for _ in $(seq 1 30); do
        mpv_alive || pid_alive || break
        sleep 0.5
      done
      # Still there: it never opened its IPC socket (it was still starting
      # up), so ask the processes directly.
      if mpv_alive || pid_alive; then
        kill -TERM "$(cat "$MPVPIDFILE" 2>/dev/null)" 2>/dev/null || true
        sleep 2
        kill -TERM "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null || true
        sleep 1
      fi
      rm -f "$HANDOFF"
    fi
    return 1
  }

  sink=$(${wireplumber}/bin/wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null \
         | sed -n 's/.*node\.nick = "\(.*\)"/\1/p')

  # Headphones, the DP monitor, a URL, anything unprobeable: just be mpv.
  # Still claim ownership, so that starting one of these while a film is on
  # the TV takes the TV down cleanly instead of stacking a second player.
  case "$sink" in
    *"LG TV"*) ;;
    *)
      exec 9>"$LOCKFILE"
      flock -w 60 9 2>/dev/null || true
      takeover plain && exit 0
      # Nothing here manages the display mode, so if we just took the TV away
      # from a film, put the desktop mode back ourselves -- its wrapper was
      # told to skip its own restore.
      if [ -n "$inherited" ]; then
        $HYPRMON set "$inherited"
      fi
      echo "$$" > "$PIDFILE"
      echo "$$" > "$MPVPIDFILE"
      echo plain > "$STATEFILE"
      rm -f "$ORIGFILE"
      flock -u 9
      # Close the lock fd before exec: mpv would otherwise inherit it and
      # hold the lock for as long as the film lasts.
      exec 9>&-
      exec $MPV "''${IPC[@]}" "$@"
      ;;
  esac
  [ -n "$file" ] || exec $MPV "''${IPC[@]}" "$@"

  # Keep the keys and match on them: ffprobe emits these fields in stream
  # order, not in the order they were requested, so positional parsing
  # silently swaps them.
  v=$($FFPROBE -v error -select_streams v:0 \
      -show_entries stream=avg_frame_rate,color_transfer \
      -of default=nw=1 "$file" 2>/dev/null)
  fpsr=$(printf '%s\n' "$v" | sed -n 's/^avg_frame_rate=//p' | head -n 1)
  trc=$(printf '%s\n' "$v" | sed -n 's/^color_transfer=//p' | head -n 1)
  acodecs=$($FFPROBE -v error -select_streams a -show_entries stream=codec_name \
            -of csv=p=0 "$file" 2>/dev/null | tr '\n' ' ')

  # avg_frame_rate is a rational like 24000/1001.
  fps=$(printf '%s' "$fpsr" | ${gawk}/bin/awk -F/ \
        '{ if (NF==2 && $2>0) printf "%.3f", $1/$2; else print "0" }')
  [ -z "$fps" ] && fps=0

  hdr=no
  case "$trc" in smpte2084|arib-std-b67) hdr=yes ;; esac

  # Closest 4K mode to the source frame rate.
  mode=$($HYPR monitors -j 2>/dev/null | $JQ -r --arg m "$MON" --argjson f "$fps" '
    [ .[] | select(.name==$m) | .availableModes[]
      | select(test("^3840x2160@"))
      | { s: ., r: (capture("@(?<r>[0-9.]+)Hz").r | tonumber) } ]
    | if length == 0 then empty else (min_by((.r - $f) | fabs) | .s) end' 2>/dev/null)
  mode=''${mode%Hz}

  # eac3 is deliberately not bitstreamed -- it produced silence over this
  # HDMI path -- so only TrueHD switches passthrough on.
  spdif=no
  case "$acodecs" in *truehd*) spdif=yes ;; esac

  # Everything about this file that the *output* has to be set up for.  Two
  # files with the same profile can share one mpv; anything else needs a
  # restart, because the display mode, the HDR colorspace hint (--vo=gpu-next
  # is fixed at startup) and the passthrough device are all decided before
  # mpv opens.  Episodes of a season match, which is the case worth having.
  profile="$mode|$hdr|$spdif"

  # Wake the TV and put it on the PC input before touching the display mode.
  # Never fatal: an unplugged or offline TV must not stop a movie starting,
  # hence the `|| true` and the timeout.  Outside the lock: it can block for
  # 40s waking a sleeping TV, and nothing after it may wait that long.
  timeout 40 ${tv}/bin/tv pc >/dev/null 2>&1 || true

  # Serialise from here to the moment mpv is up and the state files describe
  # it.  Two launches in quick succession would otherwise both look at an
  # empty $PIDFILE and both start a player.
  exec 9>"$LOCKFILE"
  flock -w 60 9 2>/dev/null || true

  takeover "$profile" && exit 0

  orig="$inherited"
  if [ -z "$orig" ]; then
    orig=$($HYPRMON get "$MON")
  fi

  echo "$$" > "$PIDFILE"
  echo "$profile" > "$STATEFILE"
  printf '%s\n' "$orig" > "$ORIGFILE"

  restore() {
    # A successor is already setting its own mode and has taken our saved
    # desktop mode out of $ORIGFILE; restoring here would only add two more
    # TV resyncs between the two films.
    if [ -e "$HANDOFF" ]; then
      rm -f "$HANDOFF"
      return
    fi
    ${waybar-popups}/bin/nightlight resume $$ || true
    if [ "$(cat "$PIDFILE" 2>/dev/null)" = "$$" ]; then
      rm -f "$PIDFILE" "$MPVPIDFILE" "$STATEFILE" "$ORIGFILE"
    fi
    if [ -n "$orig" ]; then
      $HYPRMON set "$orig"
    fi
  }
  trap restore EXIT INT TERM

  # hyprsunset tints every output alike, so evening mode would tint the
  # film too.  Neutral colours until playback ends (restore resumes it; a
  # successor that takes over pauses again under its own PID).
  ${waybar-popups}/bin/nightlight pause $$ || true

  if [ -n "$orig" ] && [ -n "$mode" ]; then
    pos=$(printf '%s' "$orig" | cut -d, -f3)
    scale=$(printf '%s' "$orig" | cut -d, -f4)
    # cm,hdr is what makes the compositor advertise PQ/BT.2020; without it it
    # reports gamma2.2 / 80 nits / Rec.709 and mpv tone-maps down to SDR.
    # Only for HDR sources -- forcing it for SDR content maps the picture into
    # HDR and looks washed out.
    extra=bitdepth,10
    [ "$hdr" = yes ] && extra=bitdepth,10,cm,hdr
    $HYPRMON set "$MON,$mode,$pos,$scale,$extra"
    # Let the new mode and scale settle before the window appears.
    sleep 5
  fi

  # mpv defaults to hwdec=no, which means 4K HEVC 10-bit is decoded on the
  # CPU.  vaapi first, because auto-safe would try hevc-vulkan first and this
  # iGPU lacks VK_KHR_video_decode_queue -- it still works, but only after
  # printing "Error parsing NAL unit" noise.  auto-safe stays as the fallback.
  opts=(--screen-name=$MON --border=no --hwdec=vaapi,auto-safe)
  [ "$hdr" = yes ] && opts+=(--vo=gpu-next --target-colorspace-hint=yes)

  # Resolve the ALSA device *after* the mode change: this codec binds PCM
  # devices to HDMI pins dynamically, so the number can move.
  if [ "$spdif" = yes ]; then
    dev=$($MPV --audio-device=help 2>/dev/null \
          | sed -n "s/^[[:space:]]*'\(alsa\/hdmi[^']*\)'.*LG TV.*/\1/p" | head -n 1)
    [ -n "$dev" ] && opts+=(--audio-spdif=truehd,dts-hd --audio-device="$dev")
  fi

  # 9>&- so mpv does not inherit the lock and hold it for the whole film.
  $MPV "''${IPC[@]}" "''${opts[@]}" "''${args[@]}" 9>&- &
  mpvpid=$!
  echo "$mpvpid" > "$MPVPIDFILE"
  flock -u 9

  # Filmmaker Mode: no motion interpolation, no sharpening, source gamma and
  # colour left alone -- the whole point of matching the refresh rate and
  # bitstreaming the audio.  Deliberately late and in the background: webOS
  # keeps a separate picture mode per input *and* per dynamic range, so
  # setting it before the HDR signal is up would file it under SDR instead.
  # Never fatal, and skipped entirely with PLAY_NO_FILMMAKER=1.
  if [ -z "''${PLAY_NO_FILMMAKER:-}" ]; then
    ( sleep 12
      timeout 20 ${tv}/bin/tv picture filmMaker >/dev/null 2>&1 || true ) &
  fi

  # Deliberately NOT fullscreen.  Entering fullscreen after HDR has engaged
  # drops the surface back to gamma2.2 permanently -- Hyprland never re-sends
  # PQ feedback for it, and no fullscreenstate variant recovers it.  Floating
  # the window at exactly the output's size looks identical and keeps HDR.
  # Hyprland also opens windows on the active monitor, which beats mpv's own
  # screen-name, so move it explicitly.
  (
    for _ in $(seq 1 60); do
      sleep 1
      $HYPR clients -j 2>/dev/null \
        | $JQ -e '.[]|select(.class=="mpv")' >/dev/null 2>&1 || continue
      # Under the Lua config `hyprctl dispatch` takes hl.dsp.* expressions.
      $HYPR dispatch 'hl.dsp.focus({ window = "class:mpv" })' >/dev/null 2>&1
      $HYPR dispatch "hl.dsp.window.move({ window = \"class:mpv\", monitor = \"$MON\" })" >/dev/null 2>&1
      $HYPR dispatch 'hl.dsp.window.float({ window = "class:mpv", action = "on" })' >/dev/null 2>&1
      geo=$($HYPR monitors -j 2>/dev/null | $JQ -r --arg m "$MON" \
            '.[]|select(.name==$m)|"\((.width/.scale)|floor) \((.height/.scale)|floor) \(.x) \(.y)"')
      set -- $geo
      if [ -n "$1" ]; then
        $HYPR dispatch "hl.dsp.window.resize({ window = \"class:mpv\", x = $1, y = $2 })" >/dev/null 2>&1
        $HYPR dispatch "hl.dsp.window.move({ window = \"class:mpv\", x = $3, y = $4 })" >/dev/null 2>&1
      fi
      break
    done
  ) &

  wait $mpvpid
''
