# Laptop -- Dell notebook, Hyprland on the shared desktop
# (modules/nixos/desktop.nix, modules/home/desktop.nix), three users.
{
  config,
  pkgs,
  inputs,
  ...
}:

{
  imports = [
    # Include the results of the hardware scan.
    ./hardware-configuration.nix
    ../../modules/nixos/desktop.nix
    inputs.home-manager.nixosModules.home-manager
  ];

  nixpkgs.overlays = [
    inputs.claude-code.overlays.default
  ];

  home-manager = {
    useGlobalPkgs = true;
    useUserPackages = true;
    backupFileExtension = "backup";
    extraSpecialArgs = { inherit inputs; };
    users.kirill = import ./home-kirill.nix;
    users.lena = import ./home-lena.nix;
    users.christoph = import ./home-christoph.nix;
  };

  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];

  # Binary cache for numtide/llm-agents.nix (provides the omp / oh-my-pi package),
  # so those agents download prebuilt instead of building from source.
  nix.settings.extra-substituters = [ "https://cache.numtide.com" ];
  nix.settings.extra-trusted-public-keys = [
    "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g="
  ];

  # Automatic garbage collection: weekly, keep last 30 days of generations.
  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 30d";
  };

  # Deduplicate the store via hardlinks.
  nix.optimise.automatic = true;

  # Bootloader.
  boot.loader.systemd-boot.enable = true;
  boot.loader.systemd-boot.configurationLimit = 20;
  boot.loader.efi.canTouchEfiVariables = true;

  boot.supportedFilesystems = [
    "ntfs"
    "exfat"
  ];

  # The PC is "pc": distinct names keep mDNS (*.local) and a bare
  # `nixos-rebuild --flake ~/.config/nixos` (which picks the hostname) apart.
  networking.hostName = "laptop";
  networking.hosts = {
    "localhost:5173" = [ "oriana.local" ];
  };

  hardware.bluetooth.enable = true;
  # Off at every boot: off is the usual state here, and with AutoEnable it
  # came back on after each boot (switched off by hand 5 times since 16 Oct).
  # The bar's Bluetooth card turns it on when it's needed. Applies to every
  # user of this laptop.
  hardware.bluetooth.powerOnBoot = false;

  # Power modes (Power Saver / Balanced / Performance), switched in the
  # bar's Battery card (bar-popup battery). On battery, Balanced moves
  # the CPU's energy preference from balance_performance to balance_power;
  # power-profiles-daemon learns "on battery" from UPower, so it needs it.
  services.power-profiles-daemon.enable = true;

  # At 2 % (percentageAction) UPower hibernates the laptop, so the session
  # survives an empty battery (twice on 17 Oct it powered off and lost it).
  # Hibernation writes to the disk swap file by zramSwap below. Should
  # logind be unable to hibernate at that moment (CanHibernate not "yes"),
  # UPower falls back to a clean PowerOff on its own.
  services.upower = {
    enable = true;
    percentageAction = 2;
    criticalPowerAction = "Hibernate";
  };

  # Both daemons are D-Bus activated and NixOS ignores the [Install] section
  # of packaged units, so nothing would start them at boot (PPD doesn't
  # auto-start UPower either). Start them where their upstream units ask to.
  systemd.services.power-profiles-daemon.wantedBy = [ "graphical.target" ];
  systemd.services.upower.wantedBy = [ "graphical.target" ];
  # UPower reads UPower.conf only when it starts, and nothing restarted it
  # when the file changed: a switch now restarts it, so a new critical
  # action (Hibernate) holds at once instead of after the next reboot.
  systemd.services.upower.restartTriggers = [ config.environment.etc."UPower/UPower.conf".source ];

  # Battery charging stays in Dell's "Fast" (ExpressCharge) mode by choice.
  # The mode is a BIOS setting (BAT0/charge_types), so nothing here sets it;
  # the Battery card's "Charge limit" switch flips it between Fast and
  # Custom, in which the firmware keeps to the BIOS's start/stop thresholds
  # (50/90 %). For that switch, this lets the users group (every account
  # here) write the mode; the thresholds stay root's. dell-laptop adds
  # charge_types to BAT0 in its own init, after BAT0's "add" event, and the
  # module's "add" comes once that init is done. Either event starts the
  # service (udev rules below), whichever driver comes last (dell_laptop in
  # each of the last five boots, 0.5-2.2 s after BAT0), and wantedBy covers
  # deploy. Not RemainAfterExit, so a reload of either driver runs it again.
  # A no-op without the file (the desktop PC).
  systemd.services.charge-mode-access = {
    description = "Let the users group switch BAT0's charge mode (Battery card)";
    wantedBy = [ "multi-user.target" ];
    unitConfig.ConditionPathExists = "/sys/class/power_supply/BAT0/charge_types";
    serviceConfig.Type = "oneshot";
    script = ''
      chgrp users /sys/class/power_supply/BAT0/charge_types
      chmod g+w /sys/class/power_supply/BAT0/charge_types
    '';
  };

  # The power key opens the bar's power card (hyprland.lua binds
  # XF86PowerOff) instead of powering off at once without asking. Held for
  # 5 s, logind still powers off cleanly: the way out of a hung session.
  services.logind.settings.Login = {
    HandlePowerKey = "ignore";
    HandlePowerKeyLongPress = "poweroff";
  };

  time.timeZone = "Europe/Berlin";

  # Where the laptop is, for the bar's weather (weather.py): at each fetch,
  # every 10 minutes, it asks GeoClue, which sends the Wi-Fi networks in
  # sight to beaconDB (the module's default provider), and falls back to
  # Seeon-Seebruck without a fix. The demo agent, on by default, is the
  # user agent GeoClue 2.8 waits for before it starts any client; a program
  # outside a sandbox then gets the location without a prompt. The entry is
  # the DesktopId weather.py announces. Laptop only: the desktop PC has no
  # Wi-Fi to locate by and stays at home.
  services.geoclue2 = {
    enable = true;
    appConfig."waybar-weather" = {
      isAllowed = true;
      isSystem = false;
    };
  };

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

  # BAT0's charge mode for the Battery card: charge-mode-access, above.
  services.udev.extraRules = ''
    ACTION=="add", SUBSYSTEM=="module", KERNEL=="dell_laptop", TAG+="systemd", ENV{SYSTEMD_WANTS}+="charge-mode-access.service"
    ACTION=="add", SUBSYSTEM=="power_supply", KERNEL=="BAT0", TAG+="systemd", ENV{SYSTEMD_WANTS}+="charge-mode-access.service"
  '';

  security.sudo.extraRules = [
    {
      users = [ "kirill" ];
      commands = [
        {
          command = "ALL";
          options = [ "NOPASSWD" ];
        }
      ];
    }
  ];

  users.users.kirill = {
    isNormalUser = true;
    description = "Kirill";
    extraGroups = [
      "networkmanager"
      "wheel"
      "input"
    ];
    shell = pkgs.zsh;
  };

  users.users.lena = {
    isNormalUser = true;
    description = "Lena";
    extraGroups = [
      "networkmanager"
      "wheel"
      "input"
    ];
    shell = pkgs.zsh;
  };

  users.users.christoph = {
    isNormalUser = true;
    description = "Christoph";
    extraGroups = [
      "networkmanager"
      "wheel"
      "input"
    ];
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

  services.xserver = {
    enable = true;
  };

  # Theme and package: modules/nixos/desktop.nix.
  services.displayManager.sddm = {
    # X11 greeter (not Wayland) so the Xsetup script below can place the login
    # screen on the external monitor when the laptop lid is closed. The themed
    # QML greeter renders identically under X11; the desktop session stays Wayland.
    wayland.enable = false;

    # Runs as root before the greeter appears. If the lid is closed, turn the
    # internal panel off and drive the login screen on the first external output.
    setupScript = ''
      if grep -q closed /proc/acpi/button/lid/LID0/state 2>/dev/null; then
        internal=$(${pkgs.xrandr}/bin/xrandr --query | grep -E '^eDP[^ ]* connected' | cut -d' ' -f1)
        external=$(${pkgs.xrandr}/bin/xrandr --query | grep -E '^(HDMI|DP|DVI)[^ ]* connected' | cut -d' ' -f1 | head -n1)
        if [ -n "$external" ]; then
          ${pkgs.xrandr}/bin/xrandr --output "$external" --auto --primary
          [ -n "$internal" ] && ${pkgs.xrandr}/bin/xrandr --output "$internal" --off
        fi
      fi
    '';
  };
  programs.nix-ld.enable = true;
  programs.nix-ld.libraries = with pkgs; [
    stdenv.cc.cc.lib
  ];

  programs.steam = {
    enable = true;
    remotePlay.openFirewall = true;
  };

  programs.ydotool.enable = true;

  programs.chromium = {
    enable = true;
    extensions = [
      "bkkmolkhemgaeaeggcmfbghljjjoofoh" # Catppuccin Mocha theme
    ];
  };

  programs.zsh.enable = true;

  # blueman-manager, for what the bar's Bluetooth card doesn't do (e.g. a
  # legacy PIN pairing), and its D-Bus mechanism, for every user. Installed
  # here only: listed again in home-shared.nix, its applet's D-Bus name was
  # registered twice.
  services.blueman.enable = true;

  services.gvfs.enable = true;
  services.udisks2.enable = true;
  services.usbmuxd.enable = true;

  # Enable graphics
  hardware.graphics = {
    enable = true;
    enable32Bit = true;
    # VA-API driver for the Intel GPU (iHD), so video decodes on its media
    # engine instead of the CPU: there was no Intel *_drv_video.so at all.
    extraPackages = [ pkgs.intel-media-driver ];
  };

  # Enable the OpenSSH daemon.
  # services.openssh.enable = true;

  networking.firewall.enable = true;

  # Compressed swap in RAM, so a memory spike slows things down instead of
  # the kernel killing processes. Priority 5: used before the swap file.
  zramSwap.enable = true;

  # Disk swap file for hibernation (UPower hibernates at 2 %, see above).
  # 16 GiB, more than the 15.1 GiB of RAM, so any image fits. Priority 0,
  # below zram's 5, so everyday swapping still goes to zram first; systemd
  # never writes the image to zram (it skips /dev/zram*). The first switch
  # creates the file (dd and mkswap, which takes a moment).
  # No resume= or resume_offset=: the initrd runs systemd, and systemd-sleep
  # hands the file's disk and offset to the next boot in the
  # HibernateLocation EFI variable, which the initrd's resume generator
  # reads. So a re-created file needs no new offset either.
  swapDevices = [
    {
      device = "/var/lib/swapfile";
      size = 16 * 1024; # MiB
      priority = 0;
    }
  ];

  # MGLRU thrashing valve: when the last second's working set no longer fits
  # in memory, the OOM killer ends one app (usually the biggest) instead of
  # the whole system freezing while it swaps (min_ttl_ms was 0: off). The
  # kernel docs say 1000 "usually eliminates intolerable janks due to
  # thrashing". The file only exists with MGLRU; tmpfiles skips it if not.
  systemd.tmpfiles.settings."10-mglru"."/sys/kernel/mm/lru_gen/min_ttl_ms".w.argument = "1000";

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = "24.11";

}
