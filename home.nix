{ inputs, config, pkgs, lib, ... }:

{
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
    bottles
    google-chrome
    signal-desktop
    krita
    spotify
    x2goclient
  
    # Media applications
    celluloid
  
    # File management
    nautilus
    duf
 
    # Utilities
    wget
    pavucontrol
    nwg-look
    imagemagick
    pulsemixer
    file
    wl-clipboard
    wtype
    appimage-run
    ydotool
    blueman
    yt-dlp
    rclone
  
    # Themes/Appearance
    catppuccin-gtk
    catppuccin-cursors
    rose-pine-hyprcursor
    catppuccin-papirus-folders
    noto-fonts-color-emoji
]) ++ [
    # Custom flakes
    inputs.affinity-nix.packages.x86_64-linux.v3
];

  # Development Tools
  programs.claude-code.enable = true;

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
  programs.firefox.enable = true;

  # Media Applications
  programs.zathura.enable = true;
  programs.imv.enable = true;
  home.file.".config/imv/config".source = ./dotfiles/imv/config;
  programs.mpv.enable = true;

  # Utilities
  programs.wofi.enable = true;
  home.file.".config/wofi/style.css".source = ./dotfiles/wofi/style.css;
  programs.fastfetch.enable = true;
  programs.htop.enable = true;
  programs.btop.enable = true;
  programs.hyprshot.enable = true;
  services.swaync.enable = true;

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

  services.hypridle.enable = true;
  services.playerctld.enable = true;
  programs.waybar.enable = true;

  home.file.".config/waybar/config.jsonc".source = ./dotfiles/waybar/config.jsonc;
  home.file.".config/waybar/mocha.css".source = ./dotfiles/waybar/mocha.css;
  home.file.".config/waybar/style.css".source = ./dotfiles/waybar/style.css;
  home.file.".config/waybar/scripts/gpu.sh" = {
    source = ./dotfiles/waybar/scripts/gpu.sh;
    executable = true;
  };
  home.file.".config/waybar/scripts/net-throughput.sh" = {
    source = ./dotfiles/waybar/scripts/net-throughput.sh;
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
      grep = "grep --color=auto";
      icat = "kitty +kitten icat";
      open = "xdg-open";
      rebuild = "sudo nixos-rebuild switch --flake ~/.config/nixos#pc";
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
    extraConfig = builtins.readFile ./dotfiles/hypr/hyprland.conf;
  };

  home.file.".config/hypr/hyprlock.conf".source = ./dotfiles/hypr/hyprlock.conf;
  home.file.".config/hypr/mocha.conf".source = ./dotfiles/hypr/mocha.conf;

  home.stateVersion = "24.11";

  # Let Home Manager manage itself
  programs.home-manager.enable = true;
}
