# The Hyprland desktop both hosts share: Waybar with its cards (bar-popup),
# swaync, wofi, hyprlock/hypridle, evening mode, the SUPER+N/SUPER+T modals.
# Each host gives its own part of the Hyprland config (screens, cursor,
# host-only binds) in desktop.hyprlandHostConfig and keeps its own theme
# (GTK, cursor), wallpaper and audio drop-ins. The system side is
# modules/nixos/desktop.nix.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  cfg = config.desktop;
  dotfiles = ../../dotfiles;

  # Python interpreter with GTK + cairo bindings for the waybar popups.
  popupPython = pkgs.python3.withPackages (ps: [
    ps.pygobject3
    ps.pycairo
    # segno: the QR code the Network card's connected Wi-Fi row shows, so a
    # phone joins by scanning
    ps.segno
  ]);

  # The bar's scripts, as the waybar-popups source and the bar's reload
  # trigger, without the __pycache__ that testing them in place leaves
  # behind (it would rebuild the package and reload the bar for nothing).
  barScripts = lib.cleanSourceWith {
    src = dotfiles + "/waybar/scripts";
    filter = path: type: baseNameOf path != "__pycache__";
  };

  # The catppuccin waybar popups -- bar-popup for the cards (network,
  # bluetooth, sound, display, weather, calendar, pomodoro, power, battery,
  # system, attention; one card_<name>.py each) -- wrapped so they find the
  # GTK/layer-shell GObject-Introspection typelibs at runtime.
  # nightlight is the evening-mode scheduler behind the display card (and
  # its own CLI); weather feeds the weather module and its card; osd handles
  # the volume/mic/brightness keys with a swaync level popup;
  # pomodoro-bar is the pomodoro module itself, one
  # process per bar; publicip looks up the public IP for the network card
  # and copies it on the network module's right click; attention feeds the
  # bar's needs-attention module (failed or crash-looping units, a full disk
  # or failing drive, an app hogging the CPU on battery, a generation or
  # kernel that waits for a reboot) and the Attention card, which imports
  # it, as the Power card does for its Restart row.
  # camera feeds the bar's camera module: the apps that hold the webcam
  # open, woken by inotify on /dev/video* (no polling). connectivity feeds
  # the bar's "Sign in" / no-internet / VPN-lock module beside the network
  # module, which NetworkManager's dispatcher wakes (modules/nixos/desktop.nix).
  # agents-busy tells hypridle whether a coding agent (omp, Orca) is at
  # work, so the battery suspend waits for it.
  # notif-bar feeds the bar's notification bell: swaync's state, plus how
  # many notifications arrived since the panel was last open.
  # bar-popup is a shell script that hands the request to the resident card
  # host over D-Bus with busctl (a few ms); the host is bar-popup.py,
  # installed as bar-popup-host and started by the user service below, or
  # by the first click.
  waybar-popups = pkgs.stdenv.mkDerivation {
    name = "waybar-popups";
    src = barScripts;
    nativeBuildInputs = [
      pkgs.wrapGAppsHook3
      pkgs.gobject-introspection
    ];
    buildInputs = [
      pkgs.gtk3
      pkgs.gtk-layer-shell
      popupPython
    ];
    dontConfigure = true;
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      mkdir -p $out/bin $out/lib
      for name in pomodoro-bar nightlight weather osd publicip attention camera connectivity agents-busy notif-bar; do
        sed "1s|.*|#!${popupPython}/bin/python3|" $name.py > $out/bin/$name
        chmod +x $out/bin/$name
      done
      sed "1s|.*|#!${popupPython}/bin/python3|" bar-popup.py > $out/bin/bar-popup-host
      chmod +x $out/bin/bar-popup-host
      # bar-popup imports cardkit and its cards, one card_<name>.py each,
      # which import the modules they need (the power and attention cards
      # import attention); nightlight imports osd for the panel's backlight.
      cp nightlight.py weather.py osd.py publicip.py attention.py cardkit.py card_*.py $out/lib/
      # The bar's palette, the cards' only copy of the colours: cardkit
      # reads it from next to itself and heads the cards' stylesheet with it.
      cp ${dotfiles + "/waybar/mocha.css"} $out/lib/mocha.css
      # Bytecode, compiled once here. The store is read-only, so Python
      # compiled these modules again on every launch: 100-180 ms per card
      # click. Hash-based .pyc files don't depend on the store's normalised
      # mtimes, and store files never change, so they're never rechecked.
      ${popupPython}/bin/python3 -m compileall -q --invalidation-mode unchecked-hash $out/lib
      runHook postInstall
    '';
    preFixup = ''
      gappsWrapperArgs+=(--prefix PYTHONPATH : $out/lib)
    '';
    # The client goes in after wrapGAppsHook3 has wrapped bin/ (a fixup
    # hook, run before postFixup), so it starts no GTK wrapper; busctl and
    # the host by full path, so it needs nothing from PATH.
    postFixup = ''
      substitute $src/bar-popup.sh $out/bin/bar-popup \
        --replace-fail "busctl --user" "${pkgs.systemd}/bin/busctl --user" \
        --replace-fail "exec bar-popup-host" "exec $out/bin/bar-popup-host"
      chmod +x $out/bin/bar-popup
    '';
  };

  # SUPER+N quick-notes modal (Textual TUI, launched in kitty by
  # hypr/scripts/notes.sh); notes are kept in ~/Documents/notes.md.
  notes-modal = pkgs.writers.writePython3Bin "notes-modal" {
    libraries = [ pkgs.python3Packages.textual ];
  } (builtins.readFile (dotfiles + "/hypr/scripts/notes.py"));

  # kb-layout-sync: a newly connected keyboard to the layout in use; udev
  # starts it (modules/nixos/desktop.nix) through the unit below.
  kb-layout-sync = pkgs.writeShellApplication {
    name = "kb-layout-sync";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.jq
      pkgs.hyprland
    ];
    text = builtins.readFile (dotfiles + "/hypr/scripts/kb-layout-sync.sh");
  };

  # Crash recovery for the bar and the notification daemon. home-manager's
  # units restart after 100 ms and give up after 5 starts in 10 s, so half
  # a second of a crash loop (a resume, a monitor hotplug) left no bar until
  # a manual restart. This retries after 1 s, backing off to 10 s, and gives
  # up only after 10 starts in a minute. The 1 s also lets the session's
  # stop job cancel the pointless restart at logout, and GDK_BACKEND=wayland
  # makes one that still happens fail at once instead of trying X11's :1.
  sessionRestart = {
    Unit = {
      StartLimitIntervalSec = 60;
      StartLimitBurst = 10;
    };
    Service = {
      RestartSec = 1;
      RestartSteps = 3;
      RestartMaxDelaySec = 10;
      Environment = [ "GDK_BACKEND=wayland" ];
    };
  };

  # A file under dotfiles/ installed as an executable script.
  script = path: {
    source = dotfiles + "/${path}";
    executable = true;
  };
in
{
  options.desktop = {
    hyprlandHostConfig = lib.mkOption {
      type = lib.types.path;
      description = ''
        This host's part of the Hyprland Lua config (screens, cursor
        variables, host-only binds), put ahead of the shared
        dotfiles/hypr/hyprland.lua in the same chunk.
      '';
    };
    waybarPopups = lib.mkOption {
      type = lib.types.package;
      readOnly = true;
      default = waybar-popups;
      description = "The bar's scripts and cards (nightlight, bar-popup, osd, ...), for host scripts that call them.";
    };
  };

  config = {
    home.packages = with pkgs; [
      waybar-popups
      ddcutil # monitor brightness over DDC/CI, for the display card
      nvtopPackages.full # the GPU module's right click: btop shows no GPU on either machine
      notes-modal
      fzf # dict-lookup.sh (SUPER+T)
      libnotify # notify-send, used by the modals and osd
      hyprlock # lock screen: SUPER+L, the power card, hypridle (PAM entry in modules/nixos/desktop.nix)
      hyprshutdown # the power card's Log out, Restart, Shut down: asks every window to close first, so apps can save
      gnome-tecla # Tecla, the layout module's right click: the key map of the layout in use (nixpkgs' `tecla` is an unrelated C library)
    ];

    # hyprshutdown, as the power card's Log out: every window gets a close
    # request first, so apps can save; hl.dsp.exit() alone kills them.
    programs.zsh.shellAliases.logout = "hyprshutdown -t 'Logging out…'";

    wayland.windowManager.hyprland = {
      enable = true;
      systemd.enable = true;
      # Lua config: hyprlang is deprecated since Hyprland 0.55 and is dropped a
      # release or two later.  home-manager writes hyprland.lua (plus a
      # .luarc.json for editor completion against the hl.* stubs) and appends
      # extraConfig verbatim after its own systemd start/stop hooks.  Switching
      # the running session over needs a relogin.
      configType = "lua";
      extraConfig =
        builtins.readFile cfg.hyprlandHostConfig
        + "\n"
        + builtins.readFile (dotfiles + "/hypr/hyprland.lua");
    };

    home.file.".config/hypr/hyprlock.conf".source = dotfiles + "/hypr/hyprlock.conf";
    home.file.".config/hypr/mocha.conf".source = dotfiles + "/hypr/mocha.conf";
    home.file.".config/hypr/scripts/dict-lookup.sh" = script "hypr/scripts/dict-lookup.sh";
    home.file.".config/hypr/scripts/notes.sh" = script "hypr/scripts/notes.sh";

    programs.wofi.enable = true;
    # The chevron's path must be absolute (wofi gives GTK no base URI for the
    # css), so point it at the current user's home.
    home.file.".config/wofi/style.css".text =
      builtins.replaceStrings [ "/home/kirill/" ] [ "${config.home.homeDirectory}/" ]
        (builtins.readFile (dotfiles + "/wofi/style.css"));
    home.file.".config/wofi/chevron.svg".source = dotfiles + "/wofi/chevron.svg";
    programs.hyprshot.enable = true;

    # config.json can't hold comments (builtins.fromJSON), so its whys are
    # here. The panel sits 4 px under the bar and 8 px from the screen's
    # edges, as a card does (cardkit's CARD_TOP and EDGE): those margins are
    # config.json's alone, style.css adds none. Hyprshot's "Screenshot saved"
    # is transient: it still pops up with its thumbnail but stays out of the
    # history and the bell's count. No volume widget: the Sound card has the
    # output slider, with the inputs and per-app levels.
    services.swaync = {
      enable = true;
      settings = builtins.fromJSON (builtins.readFile (dotfiles + "/swaync/config.json"));
      style = builtins.readFile (dotfiles + "/swaync/style.css");
    };
    systemd.user.services.swaync = sessionRestart; # crash recovery, see the let block
    # Reuse waybar's Catppuccin palette for the @import in style.css
    home.file.".config/swaync/mocha.css".source = dotfiles + "/waybar/mocha.css";

    # Evening mode.  hyprsunset holds the colour matrix and starts neutral;
    # nightlight (waybar-popups) owns the schedule and the settings in
    # ~/.local/state/nightlight.json, which the waybar display card edits.
    services.hyprsunset = {
      enable = true;
      extraArgs = [ "--identity" ];
    };

    systemd.user.services.nightlight = {
      Unit = {
        Description = "Evening mode schedule for hyprsunset";
        # Also after the target itself: a target orders itself after every
        # unit it pulls in unless that unit is already ordered after it, and
        # with hyprsunset in between that formed a cycle which systemd broke
        # by dropping nightlight at every boot.
        After = [
          "hyprsunset.service"
          config.wayland.systemd.target
        ];
        PartOf = [ config.wayland.systemd.target ];
      };
      Service = {
        ExecStart = "${waybar-popups}/bin/nightlight daemon";
        Restart = "on-failure";
        RestartSec = 5;
      };
      Install.WantedBy = [ config.wayland.systemd.target ];
    };

    # The resident card host (bar-popup-host): started with the session, so
    # the first click finds Python, GTK and the cards loaded (~70 ms instead
    # of ~0.5 s), and a rebuild restarts it on the new code instead of the
    # old code serving clicks until logout. --gapplication-service plus the
    # host's hold() keep it alive with no window open.
    systemd.user.services.bar-popup = {
      Unit = {
        Description = "Card host for the waybar modules (bar-popup)";
        After = [ config.wayland.systemd.target ];
        PartOf = [ config.wayland.systemd.target ];
      };
      Service = {
        ExecStart = "${waybar-popups}/bin/bar-popup-host --gapplication-service";
        Restart = "on-failure";
        RestartSec = 2;
      };
      Install.WantedBy = [ config.wayland.systemd.target ];
    };

    # Started by udev (modules/nixos/desktop.nix) once per key device that
    # appears, never at login, so no Install section. Devices already there
    # when the user manager starts (before Hyprland runs) start it too: the
    # condition skips those runs, and the script exits 0 without Hyprland.
    systemd.user.services.kb-layout-sync = {
      Unit = {
        Description = "Give a newly connected keyboard the layout in use";
        ConditionEnvironment = "HYPRLAND_INSTANCE_SIGNATURE";
      };
      Service = {
        Type = "oneshot";
        ExecStart = "${kb-layout-sync}/bin/kb-layout-sync";
      };
    };

    services.udiskie.enable = true;
    # Real check items in the tray menu, which style.css styles like the
    # cards; under Wayland udiskie otherwise draws them as glaring white
    # checkbox pictures, plus a stray "▸".
    services.udiskie.settings.program_options.menu_checkbox_workaround = false;

    # Idle and sleep: lock after 5 min, screen off 30 s later, suspend after
    # 15 min on battery only, and lock before every sleep (lid, power card).
    # hypridle honours idle inhibitors, so a playing video holds all of it off.
    # omp holds none while its agents work (its power.sleepPrevention is off),
    # so the lock and the screen-off still come during agent runs; only the
    # suspend waits for them (agents-busy, below). A host can turn it off
    # (services.hypridle.enable = false).
    # Without settings home-manager writes no hypridle.conf and hypridle
    # crash-loops.
    services.hypridle =
      let
        dpms = action: "hyprctl dispatch 'hl.dsp.dpms({ action = \"${action}\" })'";
      in
      {
        enable = lib.mkDefault true;
        settings = {
          general = {
            lock_cmd = "pidof hyprlock || hyprlock";
            before_sleep_cmd = "loginctl lock-session";
            after_sleep_cmd = dpms "on";
          };
          listener = [
            {
              timeout = 300;
              on-timeout = "loginctl lock-session";
            }
            {
              timeout = 330;
              on-timeout = dpms "off";
              on-resume = dpms "on";
            }
          ]
          # Suspend on battery only, and only once no coding agent works.
          # hypridle fires each listener once per idle stretch, so the later
          # ones retry until the agents are done, for up to 2 h.
          ++
            map
              (min: {
                timeout = min * 60;
                on-timeout = "grep -q 0 /sys/class/power_supply/AC/online && ! ${waybar-popups}/bin/agents-busy && systemctl suspend";
              })
              [
                15
                20
                25
                30
                40
                50
                60
                75
                90
                120
              ];
        };
      };

    services.playerctld.enable = true;
    programs.waybar.enable = true;
    # nixpkgs still ships waybar 0.15.0; three fixes from Waybar's master,
    # each patch says where it comes from:
    #  - a click on a workspace does nothing under Hyprland's Lua config
    #    (Waybar #5008; PR #5013 doesn't apply to 0.15.0, hence a backport);
    #  - the battery module prints its status on every update, ~10,000
    #    journal lines a day (#4887, PR #4844);
    #  - the bar segfaults when a module's 1 s cursor timer outlives its bar,
    #    as on lid-open resumes (#5055, PR #5058).
    # A newer release won't take these patches and the build stops: then
    # delete this override and patches/waybar-*.
    programs.waybar.package = pkgs.waybar.overrideAttrs (old: {
      patches = (old.patches or [ ]) ++ [
        ../../patches/waybar-hyprland-lua-dispatch.patch
        ../../patches/waybar-battery-no-status-print.patch
        ../../patches/waybar-cursor-timeout-after-destroy.patch
      ];
    });
    # As a user service the bar comes back after a crash (waybar 0.15.0
    # segfaulted on lid-open resumes, patched above), and a rebuild reloads
    # it (SIGUSR2) when its config, style or any of its scripts changed. The
    # reload also restarts the long-running scripts (net-speed.sh, gpu.sh,
    # pomodoro-bar, ...), so their edits apply at once instead of at the
    # next login.
    programs.waybar.systemd.enable = true;
    systemd.user.services.waybar = lib.mkMerge [
      sessionRestart # crash recovery, see the let block
      {
        Unit.X-Reload-Triggers = [
          "${dotfiles + "/waybar/config.jsonc"}"
          "${dotfiles + "/waybar/style.css"}"
          "${dotfiles + "/waybar/mocha.css"}"
          "${barScripts}"
        ];
      }
    ];

    home.file.".config/waybar/config.jsonc".source = dotfiles + "/waybar/config.jsonc";
    home.file.".config/waybar/mocha.css".source = dotfiles + "/waybar/mocha.css";
    home.file.".config/waybar/style.css".source = dotfiles + "/waybar/style.css";
    home.file.".config/waybar/scripts/gpu.sh" = script "waybar/scripts/gpu.sh";
    home.file.".config/waybar/scripts/net-speed.sh" = script "waybar/scripts/net-speed.sh";
    home.file.".config/waybar/scripts/memory.sh" = script "waybar/scripts/memory.sh";
    home.file.".config/waybar/scripts/cpu-temp.sh" = script "waybar/scripts/cpu-temp.sh";
  };
}
