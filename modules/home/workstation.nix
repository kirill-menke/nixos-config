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
    gnome-text-editor # opens .txt/.md on double-click (xdg.mime in ../nixos/workstation.nix)
    duf

    # Utilities
    wget
    pavucontrol
    imagemagick
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
    # ~/.config/claude instead of ~/.claude and ~/.claude.json: exported as
    # CLAUDE_CONFIG_DIR, which Orca and omp follow too. omp then also counts
    # Claude's user files as opted in; its skills, tools and subagents were
    # the same with and without (2026-11-08).
    configDir = "${config.xdg.configHome}/claude";
  };
  # The same for user services and apps that systemd or D-Bus start; the T3
  # Code server sets its own (hosts/pc/t3code.nix).
  systemd.user.sessionVariables.CLAUDE_CONFIG_DIR = config.programs.claude-code.configDir;
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
  programs.btop.enable = true;

  # Everything in ~ sorted into the XDG base directories: settings in
  # ~/.config, data in ~/.local/share, history in ~/.local/state, rebuildable
  # files in ~/.cache. xdg.enable exports XDG_{CONFIG,DATA,STATE,CACHE}_HOME;
  # the variables below move the apps that only follow their own variable.
  # What stays in ~ can't be moved: the path is fixed in the app (.ssh, .steam,
  # .pki, .vscode, .orca, .pi, .agents, .webos, .mozilla's native messaging
  # hosts), or other state holds it as an absolute path (.omp, .t3: T3 Code's
  # database records thousands of them).
  xdg.enable = true;
  home.sessionVariables = {
    HISTFILE = "${config.xdg.stateHome}/bash/history"; # bash; zsh sets its own below
    NODE_REPL_HISTORY = "${config.xdg.stateHome}/node_repl_history";
    PYTHON_HISTORY = "${config.xdg.stateHome}/python_history";
    WGETRC = "${config.xdg.configHome}/wget/wgetrc";
    BUN_INSTALL_CACHE_DIR = "${config.xdg.cacheHome}/bun";
  };
  xdg.configFile."wget/wgetrc".text = ''
    hsts-file = ${config.xdg.cacheHome}/wget-hsts
  '';
  # The files the variables above replace, moved on the next switch, before
  # Home Manager links anything into the new places. Nothing is overwritten:
  # what finds its new place taken stays, with a warning. A shell still open
  # from before keeps writing its history to the old place until it exits, so
  # histories are appended to the new file instead.
  home.activation.moveToXdgDirs = lib.hm.dag.entryBetween [ "linkGeneration" ] [ "writeBoundary" ] ''
    move() {
      [ -e "$1" ] || return 0
      run mkdir -p "$(dirname "$2")"
      if [ -e "$2" ]; then
        warnEcho "$1 stays: $2 exists already, merge them by hand"
      else
        run mv "$1" "$2"
      fi
    }
    append() {
      [ -e "$1" ] || return 0
      run mkdir -p "$(dirname "$2")"
      run sh -c 'cat "$1" >> "$2"' sh "$1" "$2" && run rm "$1"
    }
    append "$HOME/.histfile" "${config.xdg.stateHome}/zsh/history"
    append "$HOME/.bash_history" "${config.xdg.stateHome}/bash/history"
    append "$HOME/.node_repl_history" "${config.xdg.stateHome}/node_repl_history"
    append "$HOME/.python_history" "${config.xdg.stateHome}/python_history"
    append "$HOME/.z" "${config.xdg.stateHome}/z"
    move "$HOME/.wget-hsts" "${config.xdg.cacheHome}/wget-hsts"
    move "$HOME/.claude" "${config.programs.claude-code.configDir}"
    move "$HOME/.claude.json" "${config.programs.claude-code.configDir}/.claude.json"
    # Nix's own links, with use-xdg-base-directories (../nixos/workstation.nix):
    # Nix recreates them in the new place by itself, pointing where the old
    # ones did, so an old one whose new place exists is dropped.
    for link in nix-profile:profile nix-defexpr:defexpr; do
      old="$HOME/.''${link%%:*}" new="${config.xdg.stateHome}/nix/''${link#*:}"
      if [ ! -e "$new" ] && [ ! -L "$new" ]; then
        move "$old" "$new"
      elif [ -e "$old" ] || [ -L "$old" ]; then
        run rm -rf "$old"
      fi
    done
    # Bun's download cache, still refilled by processes started before the
    # variable existed; a cache, so dropped rather than merged
    if [ -d "$HOME/.bun" ]; then
      run rm -rf "$HOME/.bun/install/cache"
      run rmdir --ignore-fail-on-non-empty "$HOME/.bun/install" "$HOME/.bun"
    fi
    run mkdir -p "${config.xdg.stateHome}/bash"
    # Rebuilt on demand: zsh's completion dumps, z's lock, PulseAudio's old
    # cookie path (~/.config/pulse/cookie is the one in use)
    run rm -f "$HOME"/.zcompdump* "$HOME/.z.lock" "$HOME/.pulse-cookie"
  '';

  programs.zsh = {
    enable = true;
    # ~/.config/zsh instead of ~/.zshrc, ~/.zprofile and ~/.zshenv. zsh finds
    # it through ZDOTDIR, set in /etc/zshenv.local (modules/nixos/workstation.nix),
    # so no ~/.zshenv is needed to point there.
    dotDir = "${config.xdg.configHome}/zsh";
    enableCompletion = true;
    autosuggestion.enable = true;
    syntaxHighlighting.enable = true;

    history = {
      path = "${config.xdg.stateHome}/zsh/history";
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
      compinit -d "${config.xdg.cacheHome}/zsh/zcompdump-$ZSH_VERSION"
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
      # Read before oh-my-zsh.sh: its own compinit dump (same file as above,
      # not ~/.zcompdump-*) and z's directory history (not ~/.z)
      extraConfig = ''
        ZSH_COMPDUMP="${config.xdg.cacheHome}/zsh/zcompdump-$ZSH_VERSION"
        _Z_DATA="${config.xdg.stateHome}/z"
      '';
    };
  };
  # Only the stub that sources ~/.config/zsh/.zshenv; ZDOTDIR makes it unneeded.
  home.file.".zshenv".enable = false;

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
    # The theme lives in ~/.local/share/icons; ~/.icons is the pre-XDG copy.
    dotIcons.enable = false;
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
    gtk2.configLocation = "${config.xdg.configHome}/gtk-2.0/gtkrc";
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
