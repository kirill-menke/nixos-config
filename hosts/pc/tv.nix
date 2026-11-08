#
# Films on the LG C4 TV, and the HW-Q995GF soundbar behind it, played from
# this PC over HDMI-A-2: `play <file>` matches the output's mode, HDR and
# audio passthrough to the film; `celluloid-auto`, also behind Celluloid's
# launcher, picks the audio path from the default sink; `tv` drives the TV
# over the network. The films come from the NAS.
#
{ ... }:

{
  # The NAS's library (hosts/nas/media.nix), read-only. Jellyfin serves it
  # from the NAS too, but its webOS app on the C4 cannot bitstream Dolby
  # TrueHD: playing from this machine over HDMI is the only path that gets
  # lossless TrueHD Atmos to the Q995GF, so `play <file>` plays these files.
  fileSystems."/mnt/media" = {
    device = "nas.local:/tank/data/media";
    fsType = "nfs";
    options = [
      # Don't block boot on the NAS being up, and don't wedge the desktop if it
      # goes away mid-session -- `soft` returns an error instead of hanging.
      "x-systemd.automount"
      "noauto"
      "x-systemd.idle-timeout=600"
      "soft"
      "timeo=150"
      "retrans=3"
      "ro"
      "nfsvers=4.2"
      # File manager sidebar entry.
      "x-gvfs-show"
      "x-gvfs-name=Media"
    ];
  };

  home-manager.users.kirill =
    { config, pkgs, ... }:
    let
      # The bar's scripts and cards (modules/home/desktop.nix); `play` and
      # `celluloid-auto` pause and resume evening mode through its nightlight.
      waybar-popups = config.desktop.waybarPopups;

      # `hypr-monitor get OUTPUT` prints the output's live mode as a rule string,
      # `hypr-monitor set RULE` applies one.  RULE keeps the old `hyprctl keyword
      # monitor` shape, "NAME,WxH@R,XxY,SCALE[,bitdepth,N][,cm,NAME]", which is
      # also what `play` stores on disk for the restore.  The keyword went away
      # with the Lua config; the replacement is hl.monitor() through `hyprctl
      # eval`.  Unlike the keyword it *merges* into the output's existing rule, so
      # bitdepth and cm are always spelled out -- restoring the desktop mode would
      # otherwise keep the film's 10-bit HDR link.
      hypr-monitor = pkgs.writeShellScriptBin "hypr-monitor" ''
        HYPR=${pkgs.hyprland}/bin/hyprctl
        case "''${1:-}" in
          get)
            $HYPR monitors -j 2>/dev/null | ${pkgs.jq}/bin/jq -r --arg m "$2" \
              '.[]|select(.name==$m)|"\($m),\(.width)x\(.height)@\(.refreshRate),\(.x)x\(.y),\(.scale)"'
            ;;
          set)
            IFS=,
            set -- $2
            [ $# -ge 4 ] || exit 0
            lua="output=\"$1\",mode=\"$2\",position=\"$3\",scale=$4"
            bitdepth=8 cm=srgb
            shift 4
            while [ $# -ge 2 ]; do
              case "$1" in
                bitdepth) bitdepth=$2 ;;
                cm)       cm=$2 ;;
              esac
              shift 2
            done
            $HYPR eval "hl.monitor({$lua,bitdepth=$bitdepth,cm=\"$cm\"})" >/dev/null 2>&1 || true
            ;;
          *)
            echo "usage: hypr-monitor get OUTPUT | set NAME,MODE,POS,SCALE[,bitdepth,N][,cm,NAME]" >&2
            exit 64
            ;;
        esac
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
        HYPRMON=${hypr-monitor}/bin/hypr-monitor

        nick=$(${pkgs.wireplumber}/bin/wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null \
               | sed -n 's/.*node\.nick = "\(.*\)"/\1/p')

        case "$nick" in
          *"LG TV"*) ;;
          *) exec ${pkgs.celluloid}/bin/celluloid --mpv-hwdec=auto-safe --mpv-slang=en "$@" ;;
        esac

        # --- TV path: native 4K at the film's exact 23.976 cadence ------------
        # Capture the current mode so it can be put back afterwards, rather than
        # hardcoding it or relying on `hyprctl reload` (the hyprland.lua rule for
        # this output asks for a mode the TV cannot do and silently falls back).
        orig=$($HYPRMON get "$MON")

        # hyprsunset tints every output alike, so evening mode would tint the
        # film too: neutral colours until Celluloid quits.
        ${waybar-popups}/bin/nightlight pause $$ || true

        restore() {
          ${waybar-popups}/bin/nightlight resume $$ || true
          if [ -n "$orig" ]; then
            $HYPRMON set "$orig"
          fi
        }
        trap restore EXIT INT TERM

        if [ -n "$orig" ]; then
          pos=$(printf '%s' "$orig" | cut -d, -f3)
          scale=$(printf '%s' "$orig" | cut -d, -f4)
          # bitdepth 10 gives an XRGB2101010 framebuffer, which is what an HDR
          # signal needs; the TV's EDID advertises PQ/HDR10 and HLG.
          $HYPRMON set "$MON,3840x2160@23.98,$pos,$scale,bitdepth,10"
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

      # `play <file>`, one command for watching anything: ./play.nix.
      play = pkgs.callPackage ./play.nix { inherit hypr-monitor tv waybar-popups; };

      # `tv` -- drive the LG C4 over the network (webOS SSAP).  The TV cannot send
      # its remote's key presses to the PC (webOS exposes no such endpoint, and the
      # Magic Remote is 2.4 GHz RF straight to the TV), but this direction works,
      # so `play` can wake it and select the PC input by itself.
      tv = pkgs.writers.writePython3Bin "tv" {
        libraries = [ pkgs.python3Packages.aiowebostv ];
        flakeIgnore = [ "E501" ];
      } (builtins.readFile ./tv.py);

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
      home.packages = [
        celluloid-auto # sink-aware Celluloid launcher, see the let block above
        play # smart movie launcher: matches mode, HDR and audio to the source
        tv # LG TV network control (wake + input switching)
      ];

      # The TV's mpv settings; VA-API decoding and MPRIS are shared
      # (modules/home/workstation.nix).
      programs.mpv = {
        # English subtitles on by default.  mpv prefers the plain track over SDH /
        # hearing-impaired variants of the same language, and subs-with-matching-audio
        # defaults to yes, so they still show with an English audio track.
        config.slang = "en";
        # Bitstream Dolby TrueHD/Atmos untouched to the LG TV -> HW-Q995GF soundbar,
        # so the bar renders the Atmos objects onto its own speaker layout.
        # Opt-in: plain `mpv` still decodes normally via PipeWire.  Use `mpv --profile=atmos`.
        # Passthrough bypasses PipeWire and opens the ALSA device directly, so it
        # fails if something else is already playing to the TV.
        profiles.atmos = {
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
      };

      # Make the normal "Celluloid" launcher entry use the sink-aware wrapper, so
      # opening a file from inside the app (Open > Open File) gets the right
      # audio path without any special command line.
      xdg.dataFile."applications/io.github.celluloid_player.Celluloid.desktop".source =
        celluloid-auto-desktop;

      # Pin HDMI audio to the LG TV (HDMI-A-2).  Left alone, WirePlumber selects
      # `output:hdmi-stereo`, which is the GN07 DisplayPort monitor, and the TV
      # goes silent.  Applies whenever the stored profile is unavailable, e.g. if
      # the TV is off at boot.
      xdg.configFile."wireplumber/wireplumber.conf.d/51-hdmi-tv.conf".text = ''
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
    };
}
