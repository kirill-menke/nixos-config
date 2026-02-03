{ config, pkgs, ... }:

{
  imports =
    [ # Include the results of the hardware scan.
      ./hardware-configuration.nix
    ];

  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  # Bootloader.
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;
  
  boot.supportedFilesystems = [ "ntfs" "exfat" ];

  networking.hostName = "pc";

  # Enable networking
  networking.networkmanager.enable = true;
  
  hardware.bluetooth.enable = true;
  hardware.bluetooth.powerOnBoot = true;

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

  users.users.kirill = {
    isNormalUser = true;
    description = "Kirill Menke";
    extraGroups = [ "networkmanager" "wheel" ];
    shell = pkgs.zsh;
  };

  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;

  # Create mount directories for iphone
  systemd.tmpfiles.rules = [
    "d /media/kirill 0755 kirill users -"
    "d /media/kirill/iphone 0755 kirill users -"
  ];

  environment.sessionVariables = {
  	NIXOS_OZONE_WL = "1";
  };

  environment.systemPackages = with pkgs; [
    # Wine dependencies for system-wide Bottles support
    wine-staging
    winetricks
    gnutls
    libgcrypt
    openssl
    
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

  services.tumbler.enable = true;
  
  services.jellyfin = {
    enable = true;
    openFirewall = true;  # Opens ports 8096 (HTTP) and 8920 (HTTPS)
    user = "jellyfin";
    group = "users";
  };

  services.xserver = {
    enable = true;
    videoDrivers = [ "nvidia" ];
  };

  programs.hyprland = {
    enable = true;
    xwayland.enable = true;
  };

  services.displayManager.gdm = {
    enable = true;
    wayland = true;
  };

  programs.nix-ld.enable = true;
  programs.nix-ld.libraries = with pkgs; [
    stdenv.cc.cc.lib
  ];

  programs.steam = {
    enable = true;
    remotePlay.openFirewall = true;
  };

  programs.zsh.enable = true;

  services.blueman.enable = true;

  services.gvfs.enable = true;
  services.udisks2.enable = true;
  services.usbmuxd.enable = true;

  # For Wayland/Hyprland compatibility
  hardware.nvidia = {
    modesetting.enable = true;
    powerManagement.enable = true;
    open = false;  # Use proprietary drivers (GTX 1080Ti isn't supported by open drivers)
    nvidiaSettings = true;
    package = config.boot.kernelPackages.nvidiaPackages.stable;
  };

  # Enable opengl
  hardware.graphics = {
    enable = true;
    enable32Bit = true;
  };

  # Enable the OpenSSH daemon.
  # services.openssh.enable = true;

  # Open ports in the firewall.
  # networking.firewall.allowedTCPPorts = [ ... ];
  # networking.firewall.allowedUDPPorts = [ ... ];
  networking.firewall.enable = true;

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = "25.05";

}
