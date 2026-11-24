# Kirill on the laptop: what every laptop user has (./home-shared.nix), his
# setup shared with the PC (modules/home/kirill.nix), and what only the
# laptop has.
{ pkgs, ... }:

{
  imports = [
    ./home-shared.nix
    ../../modules/home/kirill.nix
  ];

  # Plain `claude` and `omp` run on Bedrock EU; claude-direct and omp-direct
  # talk to the Anthropic API directly.
  kirill.agentBackend = "bedrock";

  kirill.wallpaperPicture = "%h/Pictures/backgrounds/nice-blue-background.png";

  # Kirill-specific packages
  home.packages = with pkgs; [
    blender
    qbittorrent
    trash-cli # rm alias below
  ];

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

  # Every 5 min: fast-forward the Orca CodePipeline plugin clone to origin/main,
  # like vibe's orca-plugin-pull. Orca's devPluginPaths entry
  # ~/orca/plugins/codepipeline is a symlink to its codepipeline/ folder, and
  # Orca hot-reloads the worker. The clone's own credential helper calls `aws`
  # (--profile research), hence the PATH; while the SSO login has expired, or
  # uncommitted edits touch an updated file, a run fails and the next retries.
  systemd.user.services.orca-plugin-pull = {
    Unit = {
      Description = "Pull the CodePipeline Orca plugin (orca-plugin main)";
      ConditionPathExists = "%h/Documents/orca-plugin/.git";
    };
    Service = {
      Type = "oneshot";
      Environment = "PATH=/etc/profiles/per-user/kirill/bin:/run/current-system/sw/bin";
      ExecStart = "${pkgs.git}/bin/git -C %h/Documents/orca-plugin pull --ff-only --quiet";
    };
  };
  systemd.user.timers.orca-plugin-pull = {
    Unit.Description = "Pull the CodePipeline Orca plugin every 5 minutes";
    Timer = {
      OnStartupSec = "2min";
      OnUnitActiveSec = "5min";
    };
    Install.WantedBy = [ "timers.target" ];
  };

  # Alt+Up/Down scroll kitty's scrollback one line (Ctrl+Shift+Up/Down still
  # work). Kitty consumes these keys, so apps inside it no longer see Alt+Up/Down.
  programs.kitty.keybindings = {
    "alt+up" = "scroll_line_up";
    "alt+down" = "scroll_line_down";
  };
}
