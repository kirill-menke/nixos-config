# What the two desktops (hosts/pc, hosts/laptop) share at the system level on
# top of the Hyprland desktop (./desktop.nix, imported here): Home Manager,
# Nix, boot, locale, fonts, Kirill's account and the everyday services and
# programs. Hardware, power, users and each host's own services stay with the
# host (hosts/<name>/).
{ inputs, pkgs, ... }:

{
  imports = [
    ./desktop.nix # Hyprland, the bar's system side, the login screen
    inputs.home-manager.nixosModules.home-manager
  ];

  # Home Manager as a NixOS module: it activates with the system, reuses the
  # system's nixpkgs (overlays included) and installs per-user packages into
  # /etc/profiles/per-user rather than a separate profile. Each host lists
  # its users (home-manager.users).
  home-manager = {
    useGlobalPkgs = true;
    useUserPackages = true;
    backupFileExtension = "backup";
    # Pass the flake inputs to the users' modules.
    extraSpecialArgs = { inherit inputs; };
  };

  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];
  # Per-user profile and channel links in ~/.local/state/nix instead of
  # ~/.nix-profile and ~/.nix-defexpr (moved by ../home/workstation.nix).
  nix.settings.use-xdg-base-directories = true;

  # Flakes only: no nix-channel, and no channel on NIX_PATH. The tmpfiles
  # rules below delete the root channel left from the pre-flake install
  # (nixos-25.05, which `<nixpkgs>` lookups still fell back to).
  nix.channel.enable = false;

  # Binary cache for numtide/llm-agents.nix, so Claude Code, omp, Orca and
  # t3code download prebuilt instead of building from source (Electron never
  # builds locally).
  nix.settings.extra-substituters = [ "https://cache.numtide.com" ];
  nix.settings.extra-trusted-public-keys = [
    "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g="
  ];

  # Weekly garbage collection; how old a generation may get is each host's
  # (nix.gc.options).
  nix.gc = {
    automatic = true;
    dates = "weekly";
  };

  # Hardlink identical store paths together. Costs a periodic scan, and pays
  # for itself on a store this size.
  nix.optimise.automatic = true;

  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  boot.supportedFilesystems = [
    "ntfs"
    "exfat"
  ];

  networking.firewall.enable = true;

  hardware.bluetooth.enable = true;

  # blueman-manager, for what the bar's Bluetooth card doesn't do (e.g. a
  # legacy PIN pairing), and its D-Bus mechanism, for every user. Installed
  # here only: listed again in Home Manager, its applet's D-Bus name was
  # registered twice.
  services.blueman.enable = true;

  time.timeZone = "Europe/Berlin";

  # Select internationalisation properties.
  i18n.defaultLocale = "en_US.UTF-8";

  i18n.extraLocaleSettings = {
    LC_ADDRESS = "de_DE.UTF-8";
    LC_IDENTIFICATION = "de_DE.UTF-8";
    LC_MEASUREMENT = "de_DE.UTF-8";
    LC_MONETARY = "de_DE.UTF-8";
    LC_NAME = "de_DE.UTF-8";
    LC_NUMERIC = "de_DE.UTF-8";
    LC_PAPER = "de_DE.UTF-8";
    LC_TELEPHONE = "de_DE.UTF-8";
    LC_TIME = "de_DE.UTF-8";
  };

  # Configure keymap in X11
  services.xserver.xkb = {
    layout = "us";
    variant = "";
  };

  # Each host adds the description and any further groups.
  users.users.kirill = {
    isNormalUser = true;
    extraGroups = [
      "networkmanager"
      "wheel"
      "input"
    ];
    shell = pkgs.zsh;
  };

  programs.zsh.enable = true;
  # Users whose Home Manager zsh lives in ~/.config/zsh (programs.zsh.dotDir in
  # ../home/workstation.nix) get ZDOTDIR here, before any file in ~ is read;
  # the others keep zsh's default, their home.
  environment.etc."zshenv.local".text = ''
    [[ -z $ZDOTDIR && -e $HOME/.config/zsh/.zshrc ]] && export ZDOTDIR=$HOME/.config/zsh
  '';

  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;

  systemd.tmpfiles.rules = [
    # Mount directories for the iPhone
    "d /media/kirill 0755 kirill users -"
    "d /media/kirill/iphone 0755 kirill users -"
    # The old root channel (nix.channel above); keeps it gone
    "r /root/.nix-channels"
    "R /root/.nix-defexpr/channels"
    "r /nix/var/nix/profiles/per-user/root/channels"
    "r /nix/var/nix/profiles/per-user/root/channels-*-link"
  ];

  environment.sessionVariables.NIXOS_OZONE_WL = "1";

  # Double-clicked text files open in GNOME Text Editor (installed in
  # ../home/workstation.nix; replaced Apostrophe, whose PDF export pulled in
  # 2.3 GiB of TeX Live). System-wide /etc/xdg/mimeapps.list, so the user's
  # own ~/.config/mimeapps.list stays writable by apps and still overrides it.
  # Types without a default of their own (.log, .csv, …) fall back to
  # text/plain.
  xdg.mime.defaultApplications = {
    "text/plain" = "org.gnome.TextEditor.desktop";
    "text/markdown" = "org.gnome.TextEditor.desktop";
    "text/x-markdown" = "org.gnome.TextEditor.desktop";
  };

  environment.systemPackages = with pkgs; [
    # Thumbnail generation (used by system services)
    libheif
    ffmpeg-headless
    ffmpegthumbnailer

    # iPhone/USB mounting support
    ntfs3g
    usbutils
    usbmuxd
    ifuse
  ];

  fonts.packages = with pkgs; [
    font-awesome
    nerd-fonts.fantasque-sans-mono
    nerd-fonts.caskaydia-cove
    nerd-fonts.jetbrains-mono
  ];

  services.xserver.enable = true;
  services.tumbler.enable = true;
  services.gvfs.enable = true;
  services.udisks2.enable = true;
  services.usbmuxd.enable = true;

  hardware.graphics = {
    enable = true;
    enable32Bit = true;
    # VA-API driver for the Intel GPU (iHD), so video decodes on its media
    # engine instead of the CPU: without it there was no Intel *_drv_video.so
    # at all.
    extraPackages = [ pkgs.intel-media-driver ];
  };

  programs.nix-ld.enable = true;
  programs.nix-ld.libraries = with pkgs; [
    stdenv.cc.cc.lib
  ];

  programs.steam = {
    enable = true;
    remotePlay.openFirewall = true;
  };

  # Chrome policy (programs.chromium writes Google Chrome's too): the
  # Catppuccin Mocha theme, like the rest of the desktop, and Bitwarden for the
  # NAS's Vaultwarden (hosts/nas/vaultwarden.nix) in place of Chrome's own
  # password manager.
  programs.chromium = {
    enable = true;
    extensions = [
      "bkkmolkhemgaeaeggcmfbghljjjoofoh" # Catppuccin Mocha theme
      "nngceckbapebfimnlniiiahkandclblb" # Bitwarden
    ];
    extraOpts.PasswordManagerEnabled = false;
  };
}
