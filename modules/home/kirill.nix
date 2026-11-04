# Kirill's own setup on both desktops, on top of ./workstation.nix: git, the
# AI agents (Claude Code, omp, t3code, Orca), the daily wallpaper and his
# apps. Each host picks the agents' default backend (kirill.agentBackend).
{
  inputs,
  config,
  pkgs,
  lib,
  ...
}:

let
  cfg = config.kirill;
  agents = inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system};
  claudeCode = agents.claude-code;

  # Bedrock goes through the Heidenhain `research` SSO profile, EU region only
  # (the org SCP denies Bedrock outside the EU). --set-default, so a caller
  # can still override AWS_PROFILE/AWS_REGION per run.
  awsArgs = [
    "--set-default"
    "AWS_PROFILE"
    "research"
    "--set-default"
    "AWS_REGION"
    "eu-central-1"
  ];

  # Claude Code on Bedrock EU. Claude Code turns a Bedrock default id's us.
  # prefix into the AWS_REGION's (eu-central-1 -> eu.), so the haiku subagents
  # stay in the EU as well.
  claudeBedrockArgs = [
    "--set"
    "CLAUDE_CODE_USE_BEDROCK"
    "1"
  ]
  ++ awsArgs
  ++ [
    "--set"
    "ANTHROPIC_DEFAULT_OPUS_MODEL"
    "eu.anthropic.claude-opus-5-5[1m]"
    "--set"
    "ANTHROPIC_DEFAULT_SONNET_MODEL"
    "eu.anthropic.claude-sonnet-4-5-20250929-v1:0[1m]"
  ];

  # omp overlays (`omp --config <file>`) that move the default, task-subagent
  # and web roles to the other backend for one run. Full subtrees, so the
  # shallow merge over config.yml still wins. disabledProviders keeps every
  # other role and subagent (scout's @smol, advisor, a role saved in
  # config.yml) off the backend left behind.
  ompDirectOverlay = pkgs.writeText "omp-direct-overlay.yml" ''
    modelRoles:
      default: anthropic/claude-opus-5-5
      web: anthropic/claude-haiku-5-5
    task:
      agentModelOverrides:
        task: anthropic/claude-opus-5-5:max
      maxRecursionDepth: 4
    disabledProviders:
      - amazon-bedrock
      - bedrock-mantle
  '';
  ompBedrockOverlay = pkgs.writeText "omp-bedrock-overlay.yml" ''
    modelRoles:
      default: amazon-bedrock/eu.anthropic.claude-opus-5-5
      web: amazon-bedrock/eu.anthropic.claude-haiku-5-5
    task:
      agentModelOverrides:
        task: amazon-bedrock/eu.anthropic.claude-opus-5-5:max
      maxRecursionDepth: 4
    disabledProviders:
      - anthropic
  '';

  # Plain `claude` where Bedrock is the default. The version stays in the
  # name: the home-manager module reads it.
  claudeBedrock = pkgs.symlinkJoin {
    name = "claude-code-bedrock-${claudeCode.version}";
    paths = [ claudeCode ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = "wrapProgram $out/bin/claude ${lib.escapeShellArgs claudeBedrockArgs}";
    inherit (claudeCode) meta;
  };

  # A command NAME that runs BIN with makeWrapper's ARGS.
  wrapAs =
    name: bin: args:
    pkgs.runCommand name { nativeBuildInputs = [ pkgs.makeWrapper ]; } ''
      makeWrapper ${bin} $out/bin/${name} ${lib.escapeShellArgs args}
    '';

  # Plain `claude` and `omp` run on agentBackend; the other backend gets
  # suffixed commands.
  agentCommands =
    if cfg.agentBackend == "bedrock" then
      [
        # `omp` on Bedrock EU: config.yml holds the eu.-prefixed
        # amazon-bedrock/ roles, this adds the profile and region.
        (pkgs.symlinkJoin {
          name = "omp-bedrock";
          paths = [ agents.omp ];
          nativeBuildInputs = [ pkgs.makeWrapper ];
          postBuild = "wrapProgram $out/bin/omp ${lib.escapeShellArgs awsArgs}";
        })
        (pkgs.writeShellScriptBin "omp-direct" ''
          exec omp --config ${ompDirectOverlay} "$@"
        '')
        # Unsets the Bedrock env in case it runs inside a `claude` shell.
        (pkgs.writeShellScriptBin "claude-direct" ''
          unset CLAUDE_CODE_USE_BEDROCK ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL
          exec ${claudeCode}/bin/claude "$@"
        '')
      ]
    else
      [
        agents.omp
        (wrapAs "omp-bedrock" "${agents.omp}/bin/omp" (
          awsArgs
          ++ [
            "--add-flags"
            "--config ${ompBedrockOverlay}"
          ]
        ))
        (wrapAs "claude-bedrock" "${claudeCode}/bin/claude" claudeBedrockArgs)
      ];

  # stablyai orca ADE (same source as vibe), with two asar patches:
  # - Alt+Up/Down scroll a terminal pane one line, like kitty (local change).
  # - The pet gets a speech bubble showing the CodePipeline plugin's team
  #   feed (`pet-feed` command, ~/orca/plugins/codepipeline/feed.mjs).
  # Each patcher fails the build if its anchors stop matching after an Orca bump.
  # A panel reaches its own worker natively since 1.4.248 (orca.plugin.request
  # in the panel, orca.panels.handle in the worker), so the commands.invoke
  # backport of stablyai/orca#25256 is gone.
  # Also: `orca-ide <command>` runs Orca's CLI, as upstream's deb makes it
  # (/usr/bin/orca-ide links to the CLI). This package's orca-ide is the GUI
  # launcher (it execs the Electron binary, lib/Orca/orca-ide), so a command
  # given to it opened the app instead; earlier builds failed outright, as
  # the CLI rejected their --ozone-platform-hint flag. Now a bare `orca-ide`,
  # an orca:// link or an absolute path / file:// URI (the desktop entry, which
  # since 1.4.248 also opens Markdown, CSV and TSV files) starts the GUI;
  # anything else goes to bin/orca, the CLI -- which shadows the GNOME screen
  # reader of the same name, not installed here. The package makes both
  # launchers in its postFixup (binary wrappers since 1.4.248), so the swap
  # runs after that; its checks fail the build if the launchers change.
  # Updates come via `nix flake update llm-agents`, not the in-app updater.
  orca = agents.orca.overrideAttrs (old: {
    nativeBuildInputs = old.nativeBuildInputs ++ [ pkgs.python3 ];
    postInstall = (old.postInstall or "") + ''
      python3 ${../../patches/orca-terminal-alt-arrow-scroll.py} $out/lib/Orca/resources/app.asar
      python3 ${../../patches/orca-pet-speech-bubble.py} $out/lib/Orca/resources/app.asar
    '';
    postFixup = (old.postFixup or "") + ''
      grep -aqF "makeCWrapper '$out/lib/Orca/orca-ide'" $out/bin/orca-ide
      [ "$(readlink -f $out/bin/orca)" = "$out/lib/Orca/resources/bin/orca-ide" ]
      mkdir -p $out/libexec
      mv $out/bin/orca-ide $out/libexec/orca-ide-gui
      cat > $out/bin/orca-ide <<'EOF'
      #!@shell@
      case "''${1-}" in
        "" | orca:* | /* | file://*) exec @out@/libexec/orca-ide-gui "$@" ;;
        *) exec @out@/bin/orca "$@" ;;
      esac
      EOF
      substituteInPlace $out/bin/orca-ide --subst-var out --subst-var-by shell ${pkgs.runtimeShell}
      chmod +x $out/bin/orca-ide
    '';
  });

  # Android SDK for Orca's Android emulator pane, which shells out to adb,
  # emulator and avdmanager from ~/Android/Sdk (linked below). From nixpkgs'
  # androidenv, patched to run on NixOS, rather than Android Studio's SDK
  # manager: Google's prebuilt emulator only runs inside Studio's FHS env, not
  # when Orca starts it. Read-only, so SDK packages and system images are added
  # here, not in Studio. AVDs live in ~/.android/avd (Studio's Device Manager).
  androidSdk =
    ((pkgs.androidenv.override { licenseAccepted = true; }).composeAndroidPackages {
      # Not "latest": nixpkgs' newest platform (37.1) has no system images yet.
      platformVersions = [
        "36"
        "37.0"
      ];
      includeEmulator = true;
      includeSystemImages = true;
      systemImageTypes = [ "google_apis_playstore" ];
      abiVersions = [ "x86_64" ];
    }).androidsdk;

  # Written by daily-wallpaper.sh (timer below), read by hyprpaper.
  wallpaper = "${config.home.homeDirectory}/.cache/hypr/daily-wallpaper.jpg";
in
{
  options.kirill = {
    agentBackend = lib.mkOption {
      type = lib.types.enum [
        "bedrock"
        "direct"
      ];
      description = ''
        Where plain `claude` and `omp` send their requests: "bedrock" (Bedrock
        EU, research profile) adds claude-direct and omp-direct for the direct
        Anthropic API, "direct" adds claude-bedrock and omp-bedrock.
      '';
    };
    wallpaperPicture = lib.mkOption {
      type = lib.types.str;
      default = "%h/Pictures/backgrounds/background.jpg";
      description = ''
        The picture under the daily wallpaper's text. An argument of the
        daily-wallpaper unit's ExecStart, so %h is the home directory.
      '';
    };
  };

  config = {
    home.username = "kirill";
    home.homeDirectory = "/home/kirill";

    home.packages =
      (with pkgs; [
        krita
        x2goclient
        appimage-run
      ])
      ++ [
        orca
        (pkgs.android-studio.withSdk androidSdk)
        # T3 Code (pingdotgg/t3code): GUI control surface for Claude Code/Codex/etc.
        # `t3code-desktop` is the Electron app; `t3` the server + local web app.
        agents.t3code-desktop
        agents.t3code
      ]
      ++ agentCommands;

    home.file."Android/Sdk".source = "${androidSdk}/libexec/android-sdk";

    programs.claude-code.package = lib.mkIf (cfg.agentBackend == "bedrock") claudeBedrock;

    programs.git = {
      enable = true;
      settings = {
        user.name = "Kirill Menke";
        user.email = "kirill.menke@outlook.de";
        init.defaultBranch = "main";
        pull.rebase = true;
        push.autoSetupRemote = true;
        pager.branch = false;
        # CodeCommit over HTTPS, with the research profile unless AWS_PROFILE
        # names another.
        credential = {
          helper = "!AWS_PROFILE=\${AWS_PROFILE:-research} aws codecommit credential-helper $@";
          UseHttpPath = true;
        };
      };
      lfs.enable = true;
    };

    programs.aria2.enable = true;

    services.hyprpaper = {
      enable = true;
      settings = {
        splash = false;
        preload = [ wallpaper ];
        wallpaper = [
          {
            monitor = "";
            path = wallpaper;
          }
        ];
      };
    };

    # Daily wallpaper text overlay
    xdg.configFile."hypr/splashes.txt".source = ../../dotfiles/hypr/splashes.txt;
    xdg.configFile."hypr/scripts/daily-wallpaper.sh" = {
      source = ../../dotfiles/hypr/scripts/daily-wallpaper.sh;
      executable = true;
    };

    systemd.user.services.daily-wallpaper = {
      Unit.Description = "Generate daily wallpaper with text overlay";
      Service = {
        Type = "oneshot";
        ExecStart = "%h/.config/hypr/scripts/daily-wallpaper.sh ${cfg.wallpaperPicture}";
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
  };
}
