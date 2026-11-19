{
  inputs,
  config,
  pkgs,
  lib,
  ...
}:

{
  imports = [ ./home-shared.nix ];

  home.username = "christoph";
  home.homeDirectory = "/home/christoph";

  # Christoph's git identity
  programs.git = {
    enable = true;
    settings = {
      user.name = "Christoph";
      # user.email = "christoph@example.com";
      init.defaultBranch = "main";
      pull.rebase = true;
      push.autoSetupRemote = true;
      pager.branch = false;
    };
    lfs.enable = true;
  };

  # Christoph's wallpaper setup
  services.hyprpaper = {
    enable = true;
    settings = {
      splash = false;
      preload = [ "/home/christoph/.cache/hypr/daily-wallpaper.jpg" ];
      wallpaper = [
        {
          monitor = "";
          path = "/home/christoph/.cache/hypr/daily-wallpaper.jpg";
        }
      ];
    };
  };

  home.activation.ensureDailyWallpaper = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    mkdir -p "$HOME/.cache/hypr"
    if [[ ! -f "$HOME/.cache/hypr/daily-wallpaper.jpg" ]]; then
      cp "$HOME/Pictures/backgrounds/background.jpg" \
         "$HOME/.cache/hypr/daily-wallpaper.jpg" 2>/dev/null || true
    fi
  '';
}
