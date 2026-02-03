{ inputs, config, pkgs, ... }:

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
    awscli2
  
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
    };
    extraConfig = {
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
  programs.mpv.enable = true;

  # Utilities
  programs.wofi.enable = true;
  programs.fastfetch.enable = true;
  programs.htop.enable = true;
  programs.btop.enable = true;
  programs.hyprshot.enable = true;
  services.swaync.enable = true;

  services.udiskie.enable = true;
  services.hyprpaper = {
    enable = true;
    settings = {
      preload = [ "/home/kirill/Pictures/backgrounds/background.jpg" ];
      wallpaper = [ ",/home/kirill/Pictures/backgrounds/background.jpg" ];
    };
  };

  services.hypridle.enable = true;
  services.playerctld.enable = true;
  programs.waybar.enable = true;

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
  
  home.file.".config/hypr".source = ./dotfiles/hypr;

  home.stateVersion = "24.11";

  # Let Home Manager manage itself
  programs.home-manager.enable = true;
}
