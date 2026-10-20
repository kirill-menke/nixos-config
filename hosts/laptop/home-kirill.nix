{
  inputs,
  config,
  pkgs,
  lib,
  ...
}:

let
  # Direct-Anthropic overlay for `omp-direct`. Plain `omp` runs Bedrock EU from
  # its config.yml (eu.-prefixed amazon-bedrock/ roles); `omp-direct` runs
  # `omp --config <this>` so the default, task-subagent and web roles use the
  # un-prefixed anthropic/ models for that run. Full subtrees so a shallow
  # overlay merge still wins. disabledProviders keeps every other role and
  # subagent (scout's @smol, advisor, a Bedrock role saved in config.yml) off
  # Bedrock too.
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

  claudeCode = inputs.llm-agents.packages.x86_64-linux.claude-code;

  # Claude Code on Bedrock EU by default for Kirill (the other users keep the
  # direct API). Claude Code turns a Bedrock default id's us. prefix into the
  # AWS_REGION's (eu-central-1 -> eu.), so the haiku subagents stay in the EU
  # as well. `claude-direct` below runs the unwrapped binary on the direct API.
  # The version stays in the name: the home-manager module reads it.
  claudeBedrock = pkgs.symlinkJoin {
    name = "claude-code-bedrock-${claudeCode.version}";
    paths = [ claudeCode ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = ''
      wrapProgram $out/bin/claude \
        --set CLAUDE_CODE_USE_BEDROCK 1 \
        --set-default AWS_PROFILE research \
        --set-default AWS_REGION eu-central-1 \
        --set ANTHROPIC_DEFAULT_OPUS_MODEL 'eu.anthropic.claude-opus-5-5[1m]' \
        --set ANTHROPIC_DEFAULT_SONNET_MODEL 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0[1m]'
    '';
    inherit (claudeCode) meta;
  };
in
{
  imports = [ ./home-shared.nix ];

  home.username = "kirill";
  home.homeDirectory = "/home/kirill";

  # Kirill-specific packages
  home.packages =
    (with pkgs; [
      bottles
      krita
      blender
      x2goclient
      appimage-run
      qbittorrent
      aria2
      trash-cli # rm alias below
    ])
    ++ [
      # oh-my-pi coding agent, pointed at Bedrock via the Heidenhain `research` SSO profile
      # (EU region only: the org SCP denies Bedrock outside the EU). --set-default so a
      # caller can still override AWS_PROFILE/AWS_REGION per run.
      (pkgs.symlinkJoin {
        name = "omp-bedrock";
        paths = [ inputs.llm-agents.packages.x86_64-linux.omp ];
        nativeBuildInputs = [ pkgs.makeWrapper ];
        postBuild = ''
          wrapProgram $out/bin/omp \
            --set-default AWS_PROFILE research \
            --set-default AWS_REGION eu-central-1
        '';
      })
      # `omp-direct`: run omp against the direct Anthropic API for this
      # invocation by overlaying the anthropic/ model roles (default/task/web).
      # Plain `omp` uses Bedrock EU.
      (pkgs.writeShellScriptBin "omp-direct" ''
        exec omp --config ${ompDirectOverlay} "$@"
      '')
      # T3 Code (pingdotgg/t3code): GUI control surface for Claude Code/Codex/etc.
      # `t3code-desktop` is the Electron app; `t3` the server + local web app.
      inputs.llm-agents.packages.x86_64-linux.t3code-desktop
      inputs.llm-agents.packages.x86_64-linux.t3code
      # `claude-direct`: Claude Code on the direct Anthropic API with un-prefixed
      # model ids. Unsets the Bedrock env in case it runs inside a `claude` shell.
      (pkgs.writeShellScriptBin "claude-direct" ''
        unset CLAUDE_CODE_USE_BEDROCK ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL
        exec ${claudeCode}/bin/claude "$@"
      '')
      # stablyai orca ADE (same source as vibe), with three asar patches:
      # - stablyai/orca#25256 backported so plugin panels can call their own worker
      #   (`commands.invoke`; the CodePipeline panel needs it); drop it once a
      #   release ships the PR.
      # - Alt+Up/Down scroll a terminal pane one line, like kitty (local change).
      # - The pet gets a speech bubble showing the CodePipeline plugin's team
      #   feed (`pet-feed` command, ~/orca/plugins/codepipeline/feed.mjs).
      # Each patcher fails the build if its anchors stop matching after an Orca bump.
      # Also: `orca-ide <command>` runs Orca's CLI, as upstream's deb makes it
      # (/usr/bin/orca-ide links to the CLI). This package's orca-ide was the
      # GUI launcher, whose Wayland flags the CLI rejected ("Unknown flag
      # --ozone-platform-hint"), so any command outside Orca failed. Now only a
      # bare `orca-ide` or an orca:// link (the desktop entry) starts the GUI;
      # anything else goes to bin/orca, the CLI. The check fails the build if
      # the package's launchers change.
      (inputs.llm-agents.packages.x86_64-linux.orca.overrideAttrs (old: {
        nativeBuildInputs = old.nativeBuildInputs ++ [ pkgs.python3 ];
        postInstall = (old.postInstall or "") + ''
          python3 ${./orca-patches/orca-panel-commands-invoke.py} $out/lib/Orca/resources/app.asar
          python3 ${./orca-patches/orca-terminal-alt-arrow-scroll.py} $out/lib/Orca/resources/app.asar
          python3 ${./orca-patches/orca-pet-speech-bubble.py} $out/lib/Orca/resources/app.asar

          grep -q -- --ozone-platform-hint $out/bin/orca-ide
          grep -q -- /resources/bin/orca-ide $out/bin/orca
          mkdir -p $out/libexec
          mv $out/bin/orca-ide $out/libexec/orca-ide-gui
          cat > $out/bin/orca-ide <<'EOF'
          #!@shell@
          case "''${1-}" in
            "" | orca:*) exec @out@/libexec/orca-ide-gui "$@" ;;
            *) exec @out@/bin/orca "$@" ;;
          esac
          EOF
          substituteInPlace $out/bin/orca-ide --subst-var out --subst-var-by shell ${pkgs.runtimeShell}
          chmod +x $out/bin/orca-ide
        '';
      }))
    ];

  # Plain `claude` is the Bedrock EU wrapper (claudeBedrock above).
  programs.claude-code.package = claudeBedrock;

  # Kirill's git identity
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
        helper = "!aws codecommit credential-helper $@";
        UseHttpPath = true;
      };
    };
    lfs.enable = true;
  };

  # rm in an interactive zsh moves files to ~/.local/share/Trash instead of
  # deleting them (trash-put ignores rm's -r/-f/-i/-d). Scripts are unaffected;
  # `\rm` still deletes for good, `trash-restore` brings files back.
  programs.zsh.shellAliases.rm = "trash-put";

  # Weekly: empty what has been in the home trash for more than 30 days.
  systemd.user.services.trash-empty = {
    Unit.Description = "Empty trash older than 30 days";
    Service = {
      Type = "oneshot";
      ExecStart = "${pkgs.trash-cli}/bin/trash-empty --trash-dir %h/.local/share/Trash 30";
    };
  };
  systemd.user.timers.trash-empty = {
    Unit.Description = "Weekly trash cleanup";
    Timer = {
      OnCalendar = "weekly";
      Persistent = true;
    };
    Install.WantedBy = [ "timers.target" ];
  };

  # Alt+Up/Down scroll kitty's scrollback one line (Ctrl+Shift+Up/Down still
  # work). Kitty consumes these keys, so apps inside it no longer see Alt+Up/Down.
  programs.kitty.keybindings = {
    "alt+up" = "scroll_line_up";
    "alt+down" = "scroll_line_down";
  };

  # Kirill's wallpaper setup
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

  home.file.".config/hypr/splashes.txt".source = ../../dotfiles/hypr/splashes.txt;
  home.file.".config/hypr/scripts/daily-wallpaper.sh" = {
    source = ../../dotfiles/hypr/scripts/daily-wallpaper.sh;
    executable = true;
  };

  systemd.user.services.daily-wallpaper = {
    Unit.Description = "Generate daily wallpaper with text overlay";
    Service = {
      Type = "oneshot";
      ExecStart = "%h/.config/hypr/scripts/daily-wallpaper.sh %h/Pictures/backgrounds/nice-blue-background.png";
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

  home.activation.ensureDailyWallpaper = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    mkdir -p "$HOME/.cache/hypr"
    if [[ ! -f "$HOME/.cache/hypr/daily-wallpaper.jpg" ]]; then
      cp "$HOME/Pictures/backgrounds/background.jpg" \
         "$HOME/.cache/hypr/daily-wallpaper.jpg" 2>/dev/null || true
    fi
  '';
}
