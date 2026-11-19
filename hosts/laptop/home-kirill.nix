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

  # Alt+Up/Down scroll kitty's scrollback one line (Ctrl+Shift+Up/Down still
  # work). Kitty consumes these keys, so apps inside it no longer see Alt+Up/Down.
  programs.kitty.keybindings = {
    "alt+up" = "scroll_line_up";
    "alt+down" = "scroll_line_down";
  };
}
