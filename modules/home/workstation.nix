# What every user of the two desktops (hosts/pc, hosts/laptop) has on top of
# the Hyprland desktop (./desktop.nix, imported here): shell, terminal,
# editor, the everyday apps and the Catppuccin Mocha theme with the mauve
# accent. Kirill's own setup is ./kirill.nix; each host adds its screens
# (desktop.hyprlandHostConfig) and whatever only it has.
{
  inputs,
  config,
  pkgs,
  lib,
  ...
}:

let
  system = pkgs.stdenv.hostPlatform.system;
in
{
  imports = [
    inputs.spicetify-nix.homeManagerModules.spicetify
    ./desktop.nix
  ];

  # Packages without a home-manager module of their own
  home.packages = with pkgs; [
    # Development tools
    python3
    nodejs_22
    uv
    jq

    # Infrastructure tools
    opentofu

    # GUI Applications
    google-chrome
    signal-desktop
    # spotify is installed by the spicetify-nix module (see programs.spicetify)

    # Media applications
    celluloid

    # File management
    nautilus
    apostrophe # opens .txt/.md on double-click (xdg.mime in ../nixos/workstation.nix)
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
    yt-dlp
    rclone

    # Themes/Appearance (the GTK theme and icons: the gtk block below)
    catppuccin-cursors
    noto-fonts-color-emoji
  ];

  # Development tools
  programs.claude-code = {
    enable = true;
    # ./kirill.nix swaps in the Bedrock EU wrapper where Bedrock is Kirill's
    # default.
    package = lib.mkDefault inputs.llm-agents.packages.${system}.claude-code;
  };
  programs.awscli = {
    enable = true;
    # AWS config file is not managed by Home Manager to allow aws login to write credentials
  };
  programs.delta.enableGitIntegration = true;

  # GUI Applications
  programs.discord.enable = true;
  programs.vscode = {
    enable = true;
    profiles.default.extensions = with pkgs.vscode-extensions; [
      catppuccin.catppuccin-vsc
    ];
  };
  programs.firefox = {
    enable = true;
    # Keep the existing profile location (newer home-manager defaults to XDG).
    configPath = ".mozilla/firefox";
    profiles.default = {
      isDefault = true;
      extensions.packages = with inputs.firefox-addons.packages.${system}; [
        ublock-origin
        sponsorblock
        catppuccin-mocha-mauve
        bitwarden # for the NAS's Vaultwarden (hosts/nas/vaultwarden.nix)
      ];
      # Passwords live in Bitwarden only: no saving, autofill or generator.
      settings = {
        "signon.rememberSignons" = false;
        "signon.autofillForms" = false;
        "signon.generation.enabled" = false;
      };
    };
  };
  programs.thunderbird = {
    enable = true;
    profiles.default = {
      isDefault = true;
    };
  };

  # Spotify + Spicetify (Catppuccin Mocha to match the rest of the system)
  programs.spicetify = {
    enable = true;
    theme = inputs.spicetify-nix.legacyPackages.${system}.themes.catppuccin;
    colorScheme = "mocha";
  };

  # Media Applications
  programs.zathura.enable = true;
  programs.imv.enable = true;
  xdg.configFile."imv/config".source = ../../dotfiles/imv/config;
  programs.mpv = {
    enable = true;
    # mpv ships with hwdec=no, so every video decoded on the CPU: 34
    # CPU-seconds per 25 s of 4K HEVC on the PC, against 2.7 with VA-API on its
    # iGPU. vaapi first: auto-safe alone tries hevc-vulkan first, which the
    # PC's iGPU cannot do (no VK_KHR_video_decode_queue) and which logs NAL
    # parse errors before falling back. auto-safe stays the fallback, and
    # decodes in software what the GPU cannot.
    config.hwdec = "vaapi,auto-safe";
    # MPRIS for mpv, so it shows in waybar's now-playing module and the
    # swaync panel and answers playerctl
    scripts = [ pkgs.mpvScripts.mpris ];
  };

  # Utilities
  programs.fastfetch.enable = true;
  programs.htop.enable = true;
  programs.btop.enable = true;

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
    };

    oh-my-zsh = {
      enable = true;
      theme = "agnoster";
      plugins = [
        "sudo"
        "z"
      ];
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
    # No plugins here, so no Ruby or Python provider for them either.
    withRuby = false;
    withPython3 = false;
    extraConfig = ''
      set tabstop=2
      set shiftwidth=2
      set expandtab
      set clipboard=unnamedplus
      inoremap jj <Esc>
      nnoremap dd "_dd
    '';
  };

  # Theme: Catppuccin Mocha with the mauve accent, like the login screen
  # (modules/nixos/desktop.nix), the bar, kitty, Spotify, VS Code, Firefox and
  # Chrome (modules/nixos/workstation.nix).
  # XCursor theme, also for apps that draw their own cursor (GTK3's hand
  # pointer over waybar's popup buttons): dotfiles/hypr/hyprland.lua exports
  # the same XCURSOR_* vars.
  home.pointerCursor = {
    enable = true;
    name = "catppuccin-mocha-dark-cursors";
    package = pkgs.catppuccin-cursors.mochaDark;
    size = 24;
    gtk.enable = true;
  };

  # The icon theme gtk.iconTheme names: Papirus-Dark plus the tray icons of
  # Discord, Steam and qBittorrent redrawn in the bar's @text.
  xdg.dataFile."icons/Papirus-Dark-Tray".source = ../../dotfiles/icons/Papirus-Dark-Tray;

  gtk = {
    enable = true;
    theme = {
      name = "catppuccin-mocha-mauve-standard";
      package = pkgs.catppuccin-gtk.override {
        accents = [ "mauve" ];
        variant = "mocha";
      };
    };
    iconTheme = {
      # Papirus-Dark with Discord's, Steam's and qBittorrent's tray icons in
      # the bar's text colour (dotfiles/icons/Papirus-Dark-Tray). It inherits
      # Papirus-Dark, so every other icon, launchers' and menus' too, stays
      # as it was; waybar's tray looks the apps' icon names up in this theme.
      # The package is the Papirus-Dark it inherits.
      name = "Papirus-Dark-Tray";
      package = pkgs.catppuccin-papirus-folders.override {
        flavor = "mocha";
        accent = "mauve";
      };
    };
    # GTK4/libadwaita apps (Nautilus) ignore gtk-theme-name: home-manager
    # imports the theme's gtk-4.0 stylesheet into ~/.config/gtk-4.0/gtk.css.
    gtk4.theme = config.gtk.theme;
  };

  # Tell libadwaita apps to use the dark variant.
  dconf.settings = {
    "org/gnome/desktop/interface" = {
      color-scheme = "prefer-dark";
    };
  };

  # Let Home Manager manage itself
  programs.home-manager.enable = true;
}
