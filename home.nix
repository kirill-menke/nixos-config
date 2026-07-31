{ inputs, config, pkgs, lib, ... }:

let
  # Python interpreter with GTK + cairo bindings for the pomodoro popup.
  pomodoroPython = pkgs.python3.withPackages (ps: [ ps.pygobject3 ps.pycairo ]);

  # The catppuccin pomodoro popup, wrapped so it finds the GTK/layer-shell
  # GObject-Introspection typelibs at runtime.
  pomodoro-popup = pkgs.stdenv.mkDerivation {
    name = "pomodoro-popup";
    src = ./dotfiles/waybar/scripts;
    nativeBuildInputs = [ pkgs.wrapGAppsHook3 pkgs.gobject-introspection ];
    buildInputs = [ pkgs.gtk3 pkgs.gtk-layer-shell pomodoroPython ];
    dontConfigure = true;
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      mkdir -p $out/bin
      sed "1s|.*|#!${pomodoroPython}/bin/python3|" pomodoro-popup.py > $out/bin/pomodoro-popup
      chmod +x $out/bin/pomodoro-popup
      runHook postInstall
    '';
  };

  # Catppuccin GTK theme (Mocha, Blue accent) to match the rest of the system.
  # pkgs.catppuccin-gtk carries the build-args patch from the overlay in
  # configuration.nix; we just pick the flavour/accent here.
  ctpFlavor = "mocha";
  ctpAccent = "blue";
  ctpGtkName = "catppuccin-${ctpFlavor}-${ctpAccent}-standard";
  ctpGtk = pkgs.catppuccin-gtk.override {
    variant = ctpFlavor;
    accents = [ ctpAccent ];
    size = "standard";
  };
  ctpPapirus = pkgs.catppuccin-papirus-folders.override {
    flavor = ctpFlavor;
    accent = ctpAccent;
  };

  # Catppuccin Mocha as libadwaita named colors. This is what actually themes
  # GTK4/libadwaita apps (Nautilus) — they ignore full GTK themes and only read
  # these @define-color overrides from ~/.config/gtk-4.0/gtk.css.
  ctpAdwCss = ''
    /* Catppuccin Mocha — libadwaita named colors */
    @define-color accent_color        #89b4fa;
    @define-color accent_bg_color     #89b4fa;
    @define-color accent_fg_color     #11111b;

    @define-color destructive_color   #f38ba8;
    @define-color destructive_bg_color #f38ba8;
    @define-color destructive_fg_color #11111b;

    @define-color success_color       #a6e3a1;
    @define-color success_bg_color    #a6e3a1;
    @define-color success_fg_color    #11111b;

    @define-color warning_color       #f9e2af;
    @define-color warning_bg_color    #f9e2af;
    @define-color warning_fg_color    #11111b;

    @define-color error_color         #f38ba8;
    @define-color error_bg_color      #f38ba8;
    @define-color error_fg_color      #11111b;

    @define-color window_bg_color     #1e1e2e;
    @define-color window_fg_color     #cdd6f4;
    @define-color view_bg_color       #1e1e2e;
    @define-color view_fg_color       #cdd6f4;

    @define-color headerbar_bg_color  #181825;
    @define-color headerbar_fg_color  #cdd6f4;
    @define-color headerbar_border_color #cdd6f4;
    @define-color headerbar_backdrop_color @window_bg_color;
    @define-color headerbar_shade_color rgba(0, 0, 0, 0.36);

    @define-color card_bg_color       #313244;
    @define-color card_fg_color       #cdd6f4;
    @define-color card_shade_color    rgba(0, 0, 0, 0.36);

    @define-color dialog_bg_color     #1e1e2e;
    @define-color dialog_fg_color     #cdd6f4;

    @define-color popover_bg_color    #181825;
    @define-color popover_fg_color    #cdd6f4;

    @define-color shade_color         rgba(0, 0, 0, 0.36);
    @define-color scrollbar_outline_color rgba(0, 0, 0, 0.5);

    @define-color sidebar_bg_color    #181825;
    @define-color sidebar_fg_color    #cdd6f4;
    @define-color sidebar_backdrop_color #1e1e2e;
    @define-color sidebar_shade_color rgba(0, 0, 0, 0.36);
    @define-color secondary_sidebar_bg_color #181825;
    @define-color secondary_sidebar_fg_color #cdd6f4;
  '';

  # Celluloid launcher that picks the audio path from whichever sink is
  # currently default: bitstream TrueHD/DTS-HD when the TV is selected,
  # ordinary PipeWire decoding otherwise (headphones, monitor, ...).
  #
  # Celluloid is a single-instance app and mpv options only take effect when
  # the instance is created, so the choice is made at launch.  Select the sink
  # first, then start Celluloid; switching sinks later needs a restart.
  celluloid-auto = pkgs.writeShellScriptBin "celluloid-auto" ''
    MON=HDMI-A-2

    nick=$(${pkgs.wireplumber}/bin/wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null \
           | sed -n 's/.*node\.nick = "\(.*\)"/\1/p')

    case "$nick" in
      *"LG TV"*) ;;
      *) exec ${pkgs.celluloid}/bin/celluloid --mpv-hwdec=auto-safe --mpv-slang=en "$@" ;;
    esac

    # --- TV path: native 4K at the film's exact 23.976 cadence ------------
    # Capture the current mode so it can be put back afterwards, rather than
    # hardcoding it or relying on `hyprctl reload` (the hyprland.conf line for
    # this output asks for a mode the TV cannot do and silently falls back).
    orig=$(${pkgs.hyprland}/bin/hyprctl monitors -j 2>/dev/null \
           | ${pkgs.jq}/bin/jq -r --arg m "$MON" \
             '.[]|select(.name==$m)|"\($m),\(.width)x\(.height)@\(.refreshRate),\(.x)x\(.y),\(.scale)"')

    restore() {
      if [ -n "$orig" ]; then
        ${pkgs.hyprland}/bin/hyprctl keyword monitor "$orig" >/dev/null 2>&1 || true
      fi
    }
    trap restore EXIT INT TERM

    if [ -n "$orig" ]; then
      pos=$(printf '%s' "$orig" | cut -d, -f3)
      scale=$(printf '%s' "$orig" | cut -d, -f4)
      # bitdepth 10 gives an XRGB2101010 framebuffer, which is what an HDR
      # signal needs; the TV's EDID advertises PQ/HDR10 and HLG.
      ${pkgs.hyprland}/bin/hyprctl keyword monitor \
        "$MON,3840x2160@23.98,$pos,$scale,bitdepth,10" >/dev/null 2>&1 || true
      # Let the HDMI link re-negotiate before touching audio.
      sleep 3
    fi

    # Resolve the ALSA device from the ELD monitor name *after* the mode
    # change: this codec binds PCM devices to HDMI pins dynamically, so the
    # number can move when the link is renegotiated.
    dev=$(${pkgs.mpv}/bin/mpv --audio-device=help 2>/dev/null \
          | sed -n "s/^[[:space:]]*'\(alsa\/hdmi[^']*\)'.*LG TV.*/\1/p" \
          | head -n 1)

    # Not exec'd: the wrapper has to outlive Celluloid to restore the mode.
    if [ -n "$dev" ]; then
      ${pkgs.celluloid}/bin/celluloid --mpv-hwdec=auto-safe --mpv-slang=en \
        --mpv-audio-spdif=truehd,dts-hd --mpv-audio-device="$dev" "$@"
    else
      ${pkgs.celluloid}/bin/celluloid --mpv-hwdec=auto-safe --mpv-slang=en "$@"
    fi
  '';

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
  play = pkgs.writeShellScriptBin "play" ''
    # Do not rely on the caller's PATH.  mpv-remote launches this from a
    # systemd user service whose PATH is set for ffmpeg only, and without this
    # setsid/timeout/find/sed are all missing, so play exits instantly while
    # the launch still looks like it succeeded.
    export PATH=${lib.makeBinPath [
      pkgs.coreutils pkgs.util-linux pkgs.findutils pkgs.gnused pkgs.gawk
    ]}:$PATH

    MON=HDMI-A-2
    MPV=${pkgs.mpv}/bin/mpv
    HYPR=${pkgs.hyprland}/bin/hyprctl
    JQ=${pkgs.jq}/bin/jq
    FFPROBE=${pkgs.ffmpeg}/bin/ffprobe
    SOCAT=${pkgs.socat}/bin/socat

    # Fixed path so mpv-remote (the phone remote) can find the running player.
    # Every mpv started here gets it, including the plain non-TV paths.
    RUNTIME="''${XDG_RUNTIME_DIR:-/tmp}"
    SOCK="$RUNTIME/mpv-remote.sock"
    IPC=(--input-ipc-server="$SOCK")

    # Who owns the TV right now.  Without this, a second `play` simply started a
    # second mpv: both bound the same IPC socket, so the remote could only see
    # and stop the newer one, both fought over the audio device, and -- worst --
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
    # passed through to mpv untouched.  More than one is a playlist, which is
    # how the remote queues up the rest of a season for autoplay.  The first
    # one is what the output gets matched to -- episodes of a season share a
    # frame rate, HDR type and audio codec, so probing them all would be work
    # for nothing.
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

    sink=$(${pkgs.wireplumber}/bin/wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null \
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
          $HYPR keyword monitor "$inherited" >/dev/null 2>&1 || true
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
    fps=$(printf '%s' "$fpsr" | ${pkgs.gawk}/bin/awk -F/ \
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
    # it.  Two taps in quick succession on the phone would otherwise both look
    # at an empty $PIDFILE and both start a player.
    exec 9>"$LOCKFILE"
    flock -w 60 9 2>/dev/null || true

    takeover "$profile" && exit 0

    orig="$inherited"
    if [ -z "$orig" ]; then
      orig=$($HYPR monitors -j 2>/dev/null | $JQ -r --arg m "$MON" \
             '.[]|select(.name==$m)|"\($m),\(.width)x\(.height)@\(.refreshRate),\(.x)x\(.y),\(.scale)"')
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
      if [ "$(cat "$PIDFILE" 2>/dev/null)" = "$$" ]; then
        rm -f "$PIDFILE" "$MPVPIDFILE" "$STATEFILE" "$ORIGFILE"
      fi
      if [ -n "$orig" ]; then
        $HYPR keyword monitor "$orig" >/dev/null 2>&1 || true
      fi
    }
    trap restore EXIT INT TERM

    if [ -n "$orig" ] && [ -n "$mode" ]; then
      pos=$(printf '%s' "$orig" | cut -d, -f3)
      scale=$(printf '%s' "$orig" | cut -d, -f4)
      # cm,hdr is what makes the compositor advertise PQ/BT.2020; without it it
      # reports gamma2.2 / 80 nits / Rec.709 and mpv tone-maps down to SDR.
      # Only for HDR sources -- forcing it for SDR content maps the picture into
      # HDR and looks washed out.
      extra=bitdepth,10
      [ "$hdr" = yes ] && extra=bitdepth,10,cm,hdr
      $HYPR keyword monitor "$MON,$mode,$pos,$scale,$extra" >/dev/null 2>&1 || true
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
        $HYPR dispatch focuswindow class:mpv >/dev/null 2>&1
        $HYPR dispatch movewindow mon:$MON >/dev/null 2>&1
        $HYPR dispatch setfloating >/dev/null 2>&1
        geo=$($HYPR monitors -j 2>/dev/null | $JQ -r --arg m "$MON" \
              '.[]|select(.name==$m)|"\((.width/.scale)|floor) \((.height/.scale)|floor) \(.x) \(.y)"')
        set -- $geo
        if [ -n "$1" ]; then
          $HYPR dispatch resizewindowpixel exact $1 $2,class:mpv >/dev/null 2>&1
          $HYPR dispatch movewindowpixel exact $3 $4,class:mpv >/dev/null 2>&1
        fi
        break
      done
    ) &

    wait $mpvpid
  '';

  # Phone remote: serves a touch UI on the LAN and proxies it onto mpv's JSON
  # IPC socket (the one `play` creates).  Add it to the iOS home screen and it
  # opens fullscreen with its own icon -- the page carries the
  # apple-mobile-web-app-* meta tags and serves its own PNG icon.
  #
  # It also browses remote file servers and pulls files down with curl.  Those
  # are configured in ~/.config/mpv-remote/sources.json (mode 0600) rather than
  # here on purpose: this file is rendered into /nix/store, which is world
  # readable, and the sources carry passwords.  Format is a list of
  # { id, name, url, user, password, dest } where dest is "shows" or "movies".
  #
  # This exists because the PC has no HDMI-CEC hardware: /sys/class/cec does
  # not exist, and Intel desktop graphics do not implement a CEC adapter, so
  # the TV's own remote physically cannot reach it without a USB-CEC dongle.
  # W503 is the "line break before binary operator" rule, which contradicts
  # W504 and current PEP 8 guidance; E501 is long lines in the embedded HTML.
  mpv-remote = pkgs.writers.writePython3Bin "mpv-remote"
    { flakeIgnore = [ "E501" "E226" "W503" ]; }
    (builtins.readFile ./dotfiles/mpv-remote/server.py);

  # `tv` -- drive the LG C4 over the network (webOS SSAP).  The TV cannot send
  # its remote's key presses to the PC (webOS exposes no such endpoint, and the
  # Magic Remote is 2.4 GHz RF straight to the TV), but this direction works,
  # so `play` can wake it and select the PC input by itself.
  tv = pkgs.writers.writePython3Bin "tv"
    {
      libraries = [ pkgs.python3Packages.aiowebostv ];
      flakeIgnore = [ "E501" ];
    }
    (builtins.readFile ./dotfiles/mpv-remote/tv.py);

  # Shadow the packaged Celluloid launcher so the normal app icon uses the
  # wrapper.  Derived from the upstream file with sed rather than rewritten by
  # hand, so translations, MimeType associations and actions track upstream.
  # DBusActivatable must be turned off: otherwise launchers activate Celluloid
  # over D-Bus and skip Exec entirely, bypassing the wrapper.
  celluloid-auto-desktop = pkgs.runCommand "celluloid-auto-desktop" { } ''
    sed -e 's|^Exec=celluloid|Exec=${celluloid-auto}/bin/celluloid-auto|' \
        -e 's|^DBusActivatable=true|DBusActivatable=false|' \
        ${pkgs.celluloid}/share/applications/io.github.celluloid_player.Celluloid.desktop \
        > $out
  '';
in
{
  imports = [
    inputs.spicetify-nix.homeManagerModules.spicetify
  ];

  home.username = "kirill";
  home.homeDirectory = "/home/kirill";

  # All packages without an equivalent home-manager module (yet)
  home.packages = (with pkgs; [
    # Development tools
    python3
    nodejs_22
    uv
    opentofu
    jq
    # GUI Applications
    # bottles  # temporarily disabled due to openldap build failure on unstable
    google-chrome
    signal-desktop
    krita
    # spotify is installed by the spicetify-nix module (see programs.spicetify)
    x2goclient
  
    # Media applications
    celluloid
    celluloid-auto  # sink-aware Celluloid launcher, see the let block above
    play            # smart movie launcher: matches mode, HDR and audio to the source
    mpv-remote      # phone remote (systemd user service below)
    tv              # LG TV network control (wake + input switching)
    obs-studio    # screen recording (Wayland via PipeWire portal)
    kdePackages.kdenlive  # video editing: keyframed zoom/pan post-production
  
    # File management
    nautilus
    duf
 
    # Utilities
    wget
    fzf
    pavucontrol
    nwg-look
    imagemagick
    pulsemixer
    file
    wl-clipboard
    wtype
    pamixer
    brightnessctl
    libnotify
    appimage-run
    ydotool
    blueman
    yt-dlp
    rclone
    rtorrent

    # Waybar pomodoro timer popup
    pomodoro-popup

    # Themes/Appearance
    # catppuccin-gtk / catppuccin-papirus-folders are installed via the gtk block below
    catppuccin-cursors
    rose-pine-hyprcursor
    noto-fonts-color-emoji
]) ++ [
    # Custom flakes
    pkgs.affinity-v3
];

  # GTK theming — Catppuccin Mocha (Blue accent).
  gtk = {
    enable = true;
    theme = {
      name = ctpGtkName;
      package = ctpGtk;
    };
    iconTheme = {
      name = "Papirus-Dark";
      package = ctpPapirus;
    };
    # Nautilus and other GTK4/libadwaita apps ignore gtk-theme-name; theme them
    # via libadwaita's named colors written into ~/.config/gtk-4.0/gtk.css.
    gtk4.extraCss = ctpAdwCss;
  };

  # Tell libadwaita apps to use the dark variant.
  dconf.settings."org/gnome/desktop/interface".color-scheme = "prefer-dark";

  # Development Tools
  programs.claude-code = {
    enable = true;
    package = inputs.nix-claude-code.packages.${pkgs.system}.latest;
  };

  programs.git = {
    enable = true;
    settings = {
      user.name = "Kirill Menke";
      user.email = "kirill.menke@outlook.de";
      init.defaultBranch = "main";
      pull.rebase = true;
      push.autoSetupRemote = true;
      pager.branch = false;
    };
    settings = {
      credential = {
        helper = "!AWS_PROFILE=research aws codecommit credential-helper $@";
        UseHttpPath = true;
      };
    };
    lfs.enable = true;
    ignores = [
      ".stfolder/"
      ".stversions/"
    ];
  };

  programs.delta.enableGitIntegration = true;

  # GUI Applications
  programs.discord.enable = true;
  programs.vscode.enable = true;
  programs.thunderbird = {
    enable = true;
    profiles.default = {
      isDefault = true;
    };
  };
  programs.firefox = {
    enable = true;
    configPath = ".mozilla/firefox";
  };

  # Spotify + Spicetify (Catppuccin Mocha to match the rest of the system)
  programs.spicetify =
    let
      spicePkgs = inputs.spicetify-nix.legacyPackages.${pkgs.stdenv.system};
    in
    {
      enable = true;
      theme = spicePkgs.themes.catppuccin;
      colorScheme = "mocha";
      enabledExtensions = with spicePkgs.extensions; [
        adblockify
        hidePodcasts
        shuffle
      ];
    };

  # Media Applications
  programs.zathura.enable = true;
  programs.imv.enable = true;
  home.file.".config/imv/config".source = ./dotfiles/imv/config;
  programs.mpv.enable = true;
  # Global default: mpv ships with hwdec=no, so every video decoded on the CPU.
  # On this file that was 34 CPU-seconds per 25s of playback versus 2.7 with
  # VAAPI on the iGPU.  auto-safe falls back to software if a stream is
  # unsupported, so it is safe as a blanket default.
  # vaapi first: auto-safe alone tries hevc-vulkan first, which this iGPU
  # cannot do (no VK_KHR_video_decode_queue) and which logs NAL parse errors
  # before falling back.  auto-safe remains the fallback for other codecs.
  programs.mpv.config.hwdec = "vaapi,auto-safe";
  # English subtitles on by default.  mpv prefers the plain track over SDH /
  # hearing-impaired variants of the same language, and subs-with-matching-audio
  # defaults to yes, so they still show with an English audio track.
  programs.mpv.config.slang = "en";
  # Bitstream Dolby TrueHD/Atmos untouched to the LG TV -> HW-Q995GF soundbar,
  # so the bar renders the Atmos objects onto its own speaker layout.
  # Opt-in: plain `mpv` still decodes normally via PipeWire.  Use `mpv --profile=atmos`.
  # Passthrough bypasses PipeWire and opens the ALSA device directly, so it
  # fails if something else is already playing to the TV.
  programs.mpv.profiles.atmos = {
    profile-desc = "TrueHD/Atmos passthrough to the TV";
    # eac3 is deliberately absent: it produced silence over this HDMI path,
    # while truehd works.  dts-hd rides the same HBR path as truehd.
    audio-spdif = "truehd,dts-hd";
    # Name-resolved device, not hw:0,7 -- this Intel codec binds PCM devices to
    # HDMI pins dynamically, so the raw number moves between reboots/replugs.
    audio-device = "alsa/hdmi:CARD=PCH,DEV=1";
    # libplacebo renderer: much better HDR tone mapping than the default, and
    # it reads the Dolby Vision profile 8.1 RPU for dynamic per-scene metadata
    # instead of only the static 1000-nit mastering value.
    vo = "gpu-next";
    # Advertise the HDR colorspace to the compositor so Hyprland's
    # render:cm_auto_hdr can switch the output into HDR rather than
    # tone-mapping down to SDR.  Needs render:cm_enabled = true.
    target-colorspace-hint = "yes";
    # Target the TV.  This is load-bearing for HDR: the compositor's colour
    # feedback follows whichever output the surface sits on, so a window that
    # lands on DP-1 drops back to gamma2.2 / 80 nits / Rec.709 even though
    # everything else is correct.  Fullscreen is deliberately NOT set here --
    # entering it kills HDR; `play` floats the window at output size instead.
    #
    # This profile is only the manual escape hatch (`mpv --profile=atmos`).
    # `play` sets everything itself, per file, and does not use it.
    screen-name = "HDMI-A-2";
    fs-screen-name = "HDMI-A-2";
    # No client-side decoration on the floating playback window.
    border = "no";
  };

  # Phone remote, always up so the page is there whenever you pick up the
  # phone.  It is cheap when idle: it only talks to mpv when a request arrives,
  # and reports "not running" when there is no socket.
  systemd.user.services.mpv-remote = {
    Unit.Description = "Phone remote for mpv";
    Service = {
      ExecStart = "${mpv-remote}/bin/mpv-remote";
      Restart = "on-failure";
      RestartSec = 5;
      # ffprobe/ffmpeg for library metadata and thumbnails, curl for browsing
      # and downloading from remote sources, plus the ordinary shell tools --
      # this PATH *replaces* the inherited one, so listing only ffmpeg here left
      # `play` without setsid/timeout/find/sed and it died on launch while still
      # reporting success.
      Environment = [
        "PATH=${lib.makeBinPath [
          pkgs.ffmpeg pkgs.curl pkgs.coreutils pkgs.util-linux pkgs.findutils
          pkgs.gnused
        ]}"
        "PLAY_BIN=${play}/bin/play"
        "TV_BIN=${tv}/bin/tv"

        # The library now lives on the NAS and is mounted here over NFS. Point
        # the remote at that rather than the old local ~/Videos copies, so
        # there is a single source of truth -- otherwise a film added on the
        # NAS never appears on the phone, and deleting the local copies to
        # reclaim the disk silently empties the library.
        #
        # /mnt/media is an x-systemd.automount, so the first scan triggers the
        # mount. If the NAS is down the mount fails `soft` after ~15s and the
        # library simply reads as empty rather than hanging forever.
        "MPV_LIBRARY=/mnt/media/movies"
        "MPV_SHOWS=/mnt/media/shows"
      ];
    };
    Install.WantedBy = [ "default.target" ];
  };

  # Volume bridge.  The remote's slider needs the TV's own volume (TrueHD is
  # bitstreamed, so mpv passes the audio through untouched and cannot attenuate
  # it), and a fresh SSAP connection costs ~370ms -- far too slow to drag
  # against.  This holds one connection open behind a unix socket, which brings
  # a setVolume down to ~10ms.  It only dials the TV when something asks it to,
  # never for a plain status read, and hangs up after five idle minutes.
  systemd.user.services.tv-bridge = {
    Unit.Description = "LG TV volume bridge for the mpv remote";
    Service = {
      ExecStart = "${tv}/bin/tv serve";
      Restart = "always";
      RestartSec = 5;
    };
    Install.WantedBy = [ "default.target" ];
  };

  # Make the normal "Celluloid" launcher entry use the sink-aware wrapper, so
  # opening a file from inside the app (Open > Open File) gets the right
  # audio path without any special command line.
  home.file.".local/share/applications/io.github.celluloid_player.Celluloid.desktop".source =
    celluloid-auto-desktop;

  # Pin HDMI audio to the LG TV (HDMI-A-2).  Left alone, WirePlumber selects
  # `output:hdmi-stereo`, which is the GN07 DisplayPort monitor, and the TV
  # goes silent.  Applies whenever the stored profile is unavailable, e.g. if
  # the TV is off at boot.
  home.file.".config/wireplumber/wireplumber.conf.d/51-hdmi-tv.conf".text = ''
    device.profile.priority.rules = [
      {
        matches = [
          {
            device.name = "alsa_card.pci-0000_00_1f.3"
          }
        ]
        actions = {
          update-props = {
            priorities = [
              "output:hdmi-stereo-extra1+input:analog-stereo"
              "output:hdmi-stereo-extra1"
            ]
          }
        }
      }
    ]

    # Outrank Bluetooth so the Bose headset cannot claim the default sink
    # when it reconnects.
    monitor.alsa.rules = [
      {
        matches = [
          {
            node.name = "alsa_output.pci-0000_00_1f.3.hdmi-stereo-extra1"
          }
        ]
        actions = {
          update-props = {
            priority.session = 2000
            priority.driver = 2000
          }
        }
      }
    ]
  '';

  home.file.".rtorrent.rc".text = ''
    directory.default.set = ~/Downloads
    ratio.enable =
    ratio.min.set = 0
    ratio.max.set = 0
    ratio.upload.set = 0
  '';

  # Utilities
  programs.wofi.enable = true;
  home.file.".config/wofi/style.css".source = ./dotfiles/wofi/style.css;
  home.file.".config/wofi/chevron.svg".source = ./dotfiles/wofi/chevron.svg;
  programs.fastfetch.enable = true;
  programs.htop.enable = true;
  programs.btop.enable = true;
  programs.hyprshot.enable = true;
  services.swaync = {
    enable = true;
    settings = builtins.fromJSON (builtins.readFile ./dotfiles/swaync/config.json);
    style = builtins.readFile ./dotfiles/swaync/style.css;
  };
  programs.aria2.enable = true;
  # Reuse waybar's Catppuccin palette for the @import in style.css
  home.file.".config/swaync/mocha.css".source = ./dotfiles/waybar/mocha.css;

  services.udiskie.enable = true;
  services.hyprpaper = {
    enable = true;
    settings = {
      splash = false;
      preload = [ "/home/kirill/.cache/hypr/daily-wallpaper.jpg" ];
      wallpaper = [
        {
          monitor = "";
          path = "/home/kirill/.cache/hypr/daily-wallpaper.jpg";
        }
      ];
    };
  };

  # Daily wallpaper text overlay
  home.file.".config/hypr/splashes.txt".source = ./dotfiles/hypr/splashes.txt;
  home.file.".config/hypr/scripts/daily-wallpaper.sh" = {
    source = ./dotfiles/hypr/scripts/daily-wallpaper.sh;
    executable = true;
  };

  home.file.".config/hypr/scripts/power-menu.sh" = {
    source = ./dotfiles/hypr/scripts/power-menu.sh;
    executable = true;
  };

  # Audio sink picker for the waybar audio module (left click).
  home.file.".config/hypr/scripts/audio-sink-menu.sh" = {
    source = ./dotfiles/hypr/scripts/audio-sink-menu.sh;
    executable = true;
  };

  home.file.".config/hypr/scripts/dict-lookup.sh" = {
    source = ./dotfiles/hypr/scripts/dict-lookup.sh;
    executable = true;
  };

  systemd.user.services.daily-wallpaper = {
    Unit.Description = "Generate daily wallpaper with text overlay";
    Service = {
      Type = "oneshot";
      ExecStart = "%h/.config/hypr/scripts/daily-wallpaper.sh";
    };
  };

  systemd.user.timers.daily-wallpaper = {
    Unit.Description = "Daily wallpaper text timer";
    Timer = {
      OnCalendar = "daily";
      Persistent = true;
    };
    Install.WantedBy = [ "timers.target" ];
  };

  # Ensure the generated wallpaper exists before hyprpaper starts
  home.activation.ensureDailyWallpaper = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    mkdir -p "$HOME/.cache/hypr"
    if [[ ! -f "$HOME/.cache/hypr/daily-wallpaper.jpg" ]]; then
      cp "$HOME/Pictures/backgrounds/background.jpg" \
         "$HOME/.cache/hypr/daily-wallpaper.jpg" 2>/dev/null || true
    fi
  '';

  services.hypridle.enable = false;
  services.playerctld.enable = true;

  services.syncthing = {
    enable = true;
    overrideDevices = true;
    overrideFolders = true;
    settings = {
      devices = {
        notebook = {
          id = "VEN3IQO-TTXDSXA-6NLXJAW-7FUBOVQ-LPEXAJH-NRU6RNJ-MBN3P6R-MD77IAI";
        };
      };
      folders = {
        "heidi" = {
          path = "/home/kirill/Documents/heidenhain/heidi";
          devices = [ "notebook" ];
          versioning = {
            type = "simple";
            params.keep = "10";
          };
        };
      };
    };
  };

  programs.waybar.enable = true;

  home.file.".config/waybar/config.jsonc".source = ./dotfiles/waybar/config.jsonc;
  home.file.".config/waybar/mocha.css".source = ./dotfiles/waybar/mocha.css;
  home.file.".config/waybar/style.css".source = ./dotfiles/waybar/style.css;
  home.file.".config/waybar/scripts/gpu.sh" = {
    source = ./dotfiles/waybar/scripts/gpu.sh;
    executable = true;
  };
  home.file.".config/waybar/scripts/net-speed.sh" = {
    source = ./dotfiles/waybar/scripts/net-speed.sh;
    executable = true;
  };
  home.file.".config/waybar/scripts/network.sh" = {
    source = ./dotfiles/waybar/scripts/network.sh;
    executable = true;
  };
  home.file.".config/waybar/scripts/weather.sh" = {
    source = ./dotfiles/waybar/scripts/weather.sh;
    executable = true;
  };
  home.file.".config/waybar/scripts/pomodoro-status.sh" = {
    source = ./dotfiles/waybar/scripts/pomodoro-status.sh;
    executable = true;
  };
  home.file.".config/waybar/scripts/pomodoro-reset.sh" = {
    source = ./dotfiles/waybar/scripts/pomodoro-reset.sh;
    executable = true;
  };

  programs.zsh = {
    enable = true;
    enableCompletion = true;
    autosuggestion.enable = true;
    syntaxHighlighting.enable = true;
   
    history = {
      path = "${config.home.homeDirectory}/.histfile";
      size = 1000;
      save = 1000;
    };
    
    initContent = ''
      # Completion styles
      zstyle ':completion:*' completer _complete _ignored
      
      # Vi mode
      bindkey -v
      
      # Disable beep
      unsetopt beep
    '';
    
    completionInit = ''
      autoload -Uz compinit
      compinit -d "$HOME/.cache/zsh/zcompdump-$ZSH_VERSION"
    '';
    
    shellAliases = {
      ll = "ls -lah";
      la = "ls -a";
      claude = "claude --allow-dangerously-skip-permissions";
      grep = "grep --color=auto";
      icat = "kitty +kitten icat";
      open = "xdg-open";
      # No alias for playback: `play` is a real binary on PATH, so it also works
      # from scripts, .desktop files and non-interactive shells.
      rebuild = "sudo nixos-rebuild switch --flake ~/.config/nixos#pc";
      update = "nix flake update --flake ~/.config/nixos && sudo nixos-rebuild switch --flake ~/.config/nixos#pc";
    };

    oh-my-zsh = {
      enable = true;
      theme = "agnoster";
      plugins = [ "sudo" "z" ];
    };
  };

  programs.kitty = {
    enable = true;
    font = {
      name = "CaskaydiaCove Nerd Font Mono";
      size = 14;
    };
    themeFile = "Catppuccin-Mocha";
    settings = {
      confirm_os_window_close = 0;
      bold_font = "auto";
      italic_font = "auto";
      bold_italic_font = "auto";
    };
  };

  programs.neovim = {
    enable = true;
    defaultEditor = true;
    withRuby = false;
    withPython3 = false;
    extraConfig = ''
      set tabstop=2
      set shiftwidth=2
      set expandtab
      set clipboard=unnamedplus
      inoremap jj <Esc>
      nnoremap dd "_dd
    '';
  };
  
  programs.awscli = {
    enable = true;
    # AWS config file is not managed by Home Manager to allow aws login to write credentials
  };

  wayland.windowManager.hyprland = {
    enable = true;
    systemd.enable = true;
    configType = "hyprlang";
    extraConfig = builtins.readFile ./dotfiles/hypr/hyprland.conf;
  };

  home.file.".config/hypr/hyprlock.conf".source = ./dotfiles/hypr/hyprlock.conf;
  home.file.".config/hypr/mocha.conf".source = ./dotfiles/hypr/mocha.conf;

  home.stateVersion = "24.11";

  # Let Home Manager manage itself
  programs.home-manager.enable = true;
}
