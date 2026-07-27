{ config, pkgs, inputs, ... }:

let
  # True only when an NVIDIA card (PCI vendor 0x10de) is actually installed.
  # Used as ExecCondition on the NVIDIA-dependent units so they skip cleanly
  # while the 1080 Ti is out and start by themselves when it returns.
  # Checking /dev/nvidia* would not work: /dev/nvidiactl exists regardless.
  nvidiaPresent =
    "${pkgs.bash}/bin/bash -c 'grep -qx 0x10de /sys/bus/pci/devices/*/vendor'";
in
{
  imports =
    [ # Include the results of the hardware scan.
      ./hardware-configuration.nix
      inputs.nvidia-pstated.nixosModules.default
    ];

  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  nixpkgs.overlays = [
    inputs.affinity-nix.overlays.default
    (final: prev: {
      python3 = prev.python3.override {
        packageOverrides = pyfinal: pyprev: {
          catppuccin = pyprev.catppuccin.overridePythonAttrs (old: {
            postPatch = (old.postPatch or "") + ''
              substituteInPlace catppuccin/__init__.py \
                --replace-fail 'if importlib.util.find_spec("matplotlib") is not None:' \
                               'if False:'
            '';
            disabledTestPaths = (old.disabledTestPaths or []) ++ [ "tests/test_matplotlib.py" ];
          });
        };
      };
      catppuccin-gtk = prev.catppuccin-gtk.overrideAttrs (old: {
        postPatch = (old.postPatch or "") + ''
          substituteInPlace sources/build/args.py \
            --replace-quiet 'type=bool,' ""
        '';
      });
    })
  ];

  # Bootloader.
  boot.loader.systemd-boot.enable = true;
  boot.loader.systemd-boot.memtest86.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;
  
  boot.blacklistedKernelModules = [ "nouveau" ];
  boot.supportedFilesystems = [ "ntfs" "exfat" ];

  networking.hostName = "pc";
  networking.hosts = {
    "localhost:5173" = [ "oriana.local" ];
  };

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
    extraGroups = [ "networkmanager" "wheel" "input" ];
    shell = pkgs.zsh;
  };

  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;

  # Create mount directories for iphone
  systemd.tmpfiles.rules = [
    "d /media/kirill 0755 kirill users -"
    "d /media/kirill/iphone 0755 kirill users -"
    # Let Jellyfin (uid jellyfin) reach the movie library.  /home/kirill is
    # 0700, so without this the Movies library at /home/kirill/Videos is
    # unreadable and scans silently find nothing.
    # Traverse-only on the home directory: Jellyfin can pass through but
    # cannot list it, so nothing else in ~ is exposed.
    # Note a named-user entry *replaces* what that user would otherwise get,
    # it does not add to it -- granting only --x on Videos (already o+rx)
    # downgraded Jellyfin and broke its directory watcher.
    "a+ /home/kirill - - - - u:jellyfin:--x"
    "a+ /home/kirill/Videos - - - - u:jellyfin:r-x"
    "A+ /home/kirill/Videos/movies - - - - u:jellyfin:rX"
  ];

  environment.sessionVariables = {
  	NIXOS_OZONE_WL = "1";
  	CLAUDE_CODE_MAX_OUTPUT_TOKENS = "128000";
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
    withUWSM = true;
  };

  services.displayManager.sddm.enable = true;
  services.displayManager.sddm.wayland.enable = true;

  programs.nix-ld.enable = true;
  programs.nix-ld.libraries = with pkgs; [
    stdenv.cc.cc.lib
  ];

  programs.steam = {
    enable = true;
    remotePlay.openFirewall = true;
  };

  programs.ydotool.enable = true;

  # On-screen keystroke display for screen recordings (Wayland-native,
  # needs the setuid wrapper this module provides to read input devices)
  programs.wshowkeys.enable = true;

  programs.zsh.enable = true;

  services.blueman.enable = true;

  services.gvfs.enable = true;
  services.udisks2.enable = true;
  services.usbmuxd.enable = true;

  # The 2.4 GHz receivers resume the box the instant S3 is entered — the mouse
  # sensor twitches, the dongle signals remote wakeup, and suspend lasts <1s.
  # Keep them from arming USB wakeup at all; the power button still wakes.
  services.udev.extraRules = ''
    ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="046d", ATTR{idProduct}=="c547", ATTR{power/wakeup}="disabled"
    ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="36b0", ATTR{idProduct}=="3002", ATTR{power/wakeup}="disabled"
  '';

  # For Wayland/Hyprland compatibility
  hardware.nvidia = {
    modesetting.enable = true;
    powerManagement.enable = true;
    nvidiaPersistenced = true;  # keep driver state loaded — avoids Pascal P-state hangs
    open = false;  # Use proprietary drivers (GTX 1080Ti isn't supported by open drivers)
    nvidiaSettings = true;
    package = config.boot.kernelPackages.nvidiaPackages.legacy_580;  # GTX 1080 Ti (Pascal) — 595+ dropped support
  };

  # Enable opengl
  hardware.graphics = {
    enable = true;
    enable32Bit = true;
    # VAAPI for the Alder Lake iGPU.  Without this only nvidia_drv_video.so was
    # present, so hardware decoding was impossible and 4K HEVC 10-bit ran on the
    # CPU.  intel-media-driver provides iHD (Gen8+); intel-vaapi-driver is the
    # older i965 fallback.
    extraPackages = with pkgs; [
      intel-media-driver
      libva-utils          # `vainfo`, to check what the GPU can decode
      # OpenCL for the iGPU.  Jellyfin's HDR tonemapping filter needs it
      # (`-init_hw_device opencl=ocl:0.0`), and the only ICD present was
      # nvidia.icd for the removed card, so `clinfo` reported 0 platforms and
      # ffmpeg died immediately -- the "cannot play on the TV" symptom.
      intel-compute-runtime
      ocl-icd
    ];
  };

  # Pin GPU to P0 — Pascal consumer cards don't support nvidia-smi clock locks,
  # and NVIDIA removed PowerMizer modprobe options in driver 530+. nvidia-pstated
  # uses libnvidia-api.so to set P-state directly. Both low/high are P0 to avoid
  # transitions entirely (transitions are what cause the hangs).
  # Left enabled even with the 1080 Ti pulled (2026-07-12, freeze diagnosis):
  # the ExecCondition below skips it while no card is present, so nothing has
  # to be flipped by hand when it goes back in.
  services.nvidia-pstated = {
    enable = true;
    performanceStateLow = 0;
    performanceStateHigh = 0;
  };

  # Upstream module's unit lacks LD_LIBRARY_PATH; the daemon dlopen()s
  # libnvidia-api.so.1 which lives under /run/opengl-driver/lib on NixOS.
  systemd.services.nvidia-pstated.environment.LD_LIBRARY_PATH = "/run/opengl-driver/lib";

  # Every NVIDIA-dependent unit is gated on the card actually being in the
  # machine, so pulling it does not leave services crash-looping (gpu-log hit
  # 36 restarts in one boot while the 1080 Ti was out).  ExecCondition marks
  # the unit *skipped* rather than failed, so `nixos-rebuild` also stays quiet.
  #
  # The check is the PCI vendor ID: /dev/nvidia* is not usable because
  # /dev/nvidiactl exists even with no card installed.
  systemd.services.nvidia-persistenced.serviceConfig.ExecCondition = nvidiaPresent;
  systemd.services.nvidia-pstated.serviceConfig.ExecCondition = nvidiaPresent;
  systemd.services.gpu-log.serviceConfig.ExecCondition = nvidiaPresent;

  systemd.services.gpu-log = {
    description = "Log GPU metrics for crash diagnostics";
    wantedBy = [ "multi-user.target" ];
    # NOTE: "after graphical.target" created an ordering cycle with
    # multi-user.target, so systemd silently deleted this unit at every boot.
    after = [ "nvidia-persistenced.service" ];
    wants = [ "nvidia-persistenced.service" ];
    serviceConfig = {
      Type = "simple";
      ExecStart = "${pkgs.coreutils}/bin/stdbuf -oL ${config.hardware.nvidia.package.bin}/bin/nvidia-smi dmon -s pucvmet -o DT";
      StandardOutput = "append:/var/log/gpu-log.txt";
      StandardError = "journal";
      Restart = "always";
      RestartSec = 5;
    };
  };

  # Freeze diagnostics: log CPU/NVMe temps, RAM, load and GPU state to the
  # journal every 10s so the last samples before a hard freeze are on disk.
  systemd.services.sysmon = {
    description = "Log system health metrics for crash diagnostics";
    wantedBy = [ "multi-user.target" ];
    after = [ "nvidia-persistenced.service" ];
    path = [ pkgs.coreutils pkgs.gawk config.hardware.nvidia.package.bin ];
    script = ''
      while true; do
        cpu=""; nvme=""
        for h in /sys/class/hwmon/hwmon*; do
          case "$(cat "$h/name")" in
            coretemp) cpu=$(( $(cat "$h/temp1_input") / 1000 )) ;;
            nvme)     nvme=$(( $(cat "$h/temp1_input") / 1000 )) ;;
          esac
        done
        mem=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
        swap=$(awk '/SwapFree/ {print int($2/1024)}' /proc/meminfo)
        load=$(cut -d' ' -f1-3 /proc/loadavg)
        gpu=$(nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu,memory.used,clocks.sm,pstate \
              --format=csv,noheader 2>&1 | tr -d '\n')
        echo "cpu=''${cpu}C nvme=''${nvme}C mem_avail=''${mem}MiB swap_free=''${swap}MiB load=''${load} gpu: ''${gpu}"
        sleep 10
      done
    '';
    serviceConfig = {
      Restart = "always";
      RestartSec = 5;
      SyslogIdentifier = "sysmon";
    };
  };

  # Flush journal to disk every 15s (default is 5min) — otherwise the last
  # minutes before a hard freeze never reach disk and the log looks like the
  # system died earlier than it did.
  services.journald.extraConfig = ''
    SyncIntervalSec=15s
  '';

  # Log MCEs / hardware errors (CPU, memory controller, PCIe AER) persistently.
  hardware.rasdaemon.enable = true;

  # If the kernel detects a lockup, panic instead of hanging silently: the
  # panic (with backtrace) lands in EFI pstore and is readable after reboot
  # under /sys/fs/pstore. kernel.panic=30 then reboots automatically.
  boot.kernel.sysctl = {
    "kernel.softlockup_panic" = 1;
    "kernel.hardlockup_panic" = 1;
    "kernel.panic" = 30;
    "kernel.sysrq" = 1;  # allow Alt+SysRq (REISUB) as a softer escape than the power button
  };

  # Open Syncthing's sync (22000/tcp+udp) and local discovery (21027/udp) ports
  services.syncthing.openDefaultPorts = true;

  # Enable the OpenSSH daemon.
  # services.openssh.enable = true;

  # Open ports in the firewall.
  # networking.firewall.allowedTCPPorts = [ ... ];
  # networking.firewall.allowedUDPPorts = [ ... ];
  networking.firewall.enable = true;
  # mpv phone remote (see mpv-remote in home.nix).  Scoped to the wired LAN
  # interface rather than opened globally -- the remote has no authentication,
  # so anyone who can reach the port can control playback.  (Interface-scoped
  # rather than `extraInputRules`, which is nftables-only and this host still
  # uses the iptables backend.)
  networking.firewall.interfaces.enp3s0.allowedTCPPorts = [ 8322 ];

  # Wake-on-LAN on the wired NIC, so a suspended box can be brought back from
  # the phone before starting a film.  The PCI device had wakeup disabled, so
  # nothing could reach it in S3 at all.
  #
  # `wol g` is magic packet only.  It used to be `wol ug`, adding unicast so the
  # phone's connection attempt to 8322 was itself the wake trigger and nothing
  # had to send a separate magic packet.  That cost more than it was worth: the
  # NIC cannot tell that packet from any other addressed to this host, and this
  # LAN carries ~18 packets/s of ordinary traffic to us, so S3 never survived
  # more than a few seconds.  Suspends on 2026-07-26 and 2026-07-29 all resumed
  # within 6-9s with no device claiming the wake -- the firmware denies native
  # PCIe PME (`_OSC: platform does not support [PCIeHotplug PME]`), so the NIC's
  # PME surfaced only as ACPI gpe6D, which is why every power/wakeup_count read
  # 0 and the cause stayed hidden.  Waking from the phone now needs a magic
  # packet to 9c:6b:00:a3:38:bc first.
  #
  # NOT `networking.interfaces.enp3s0.wakeOnLan`: that writes a systemd .link
  # file, which NixOS only installs into /etc/systemd/network when
  # systemd-networkd is enabled.  This host runs NetworkManager, so the file was
  # never placed, udev went on applying 99-default.link, and the setting looked
  # applied while the NIC stayed wakeup-disabled.  Setting it with ethtool works
  # regardless of which network daemon is in charge.
  systemd.services.wake-on-lan-enp3s0 = {
    description = "Arm Wake-on-LAN on enp3s0";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = pkgs.writeShellScript "arm-wol" ''
        ${pkgs.ethtool}/bin/ethtool -s enp3s0 wol g
      '';
    };
  };

  # Re-arm immediately before suspending: the setting only matters at that
  # moment, and NetworkManager reactivating the link can clear it in between.
  powerManagement.powerDownCommands = ''
    ${pkgs.ethtool}/bin/ethtool -s enp3s0 wol g || true
  '';

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = "25.05";

}
