{ inputs, config, pkgs, ... }:

{
  home.username = "kirill";
  home.homeDirectory = "/home/kirill";

  # Packages that should be installed to the user profile
  home.packages = (with pkgs; [
    # Development tools
    git-lfs
    python3
    nodejs_22
    claude-code
  
    # GUI Applications
    bottles
    google-chrome
    signal-desktop
    firefox
    krita
    discord
    spotify
    vscode
    thunderbird
  
    # Media applications
    zathura
    imv
    celluloid
    mpv
  
    # File management
    nautilus
    duf
  
    # Utilities
    kitty
    wofi
    fastfetch
    htop
    btop
    wget
    waybar
    hyprshot
    hypridle
    hyprpaper
    hyprlock
    swaynotificationcenter
    playerctl
    pavucontrol
    nwg-look
    udiskie
    imagemagick
    wtype
    pulsemixer
    file
    wl-clipboard
    blueman
    yt-dlp
  
    # Themes/Appearance
    catppuccin-gtk
    catppuccin-cursors
    rose-pine-hyprcursor
    catppuccin-papirus-folders
]) ++ [
    # Custom flakes
    inputs.affinity-nix.packages.x86_64-linux.v3
];

  # Basic program configurations
  programs.git = {
    enable = true;
    settings = {
      user.name = "Kirill Menke";
      user.email = "kirill.menke@outlook.de";
      init.defaultBranch = "main";
      pull.rebase = true;
      push.autoSetupRemote = true;
    };
  };

  programs.delta.enableGitIntegration = true;

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
      rebuild = "sudo nixos-rebuild switch --flake .#pc";
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
  };

  # Manage dotfiles
  home.file.".config/hypr".source = ./dotfiles/hypr;

  home.stateVersion = "24.11";

  # Let Home Manager manage itself
  programs.home-manager.enable = true;
}
