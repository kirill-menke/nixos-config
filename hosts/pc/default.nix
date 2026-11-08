#
# Desktop PC -- Hyprland workstation, HDMI path to the LG C4 for lossless
# TrueHD playback (./tv.nix), and the NFS client of the NAS. What it shares
# with the laptop, Hyprland included, is modules/nixos/workstation.nix;
# Kirill's Home Manager config here is ./home.nix. The PC's own hardware and
# features each have a module, imported below.
#
{ ... }:

{
  imports = [
    ./hardware-configuration.nix # the nixos-generate-config scan
    ./options.nix # pc.* values the modules below share
    ./gpu.nix # the GTX 1080 Ti and the iGPU
    ./diagnostics.nix # what a hard freeze leaves on disk
    ./wake-on-lan.nix # what may wake it from suspend
    ./iphone.nix # Bluetooth keyboard/mouse for the iPhone, AirPlay mirroring
    ./tv.nix # films on the LG C4: play, celluloid-auto, tv, the NAS library
    ./paperless.nix # the NAS's Paperless documents as files: /mnt/paperless
    ./photos.nix # the NAS's photo archive, read-write: /mnt/photos
    ./nas.nix # the NAS's data pool, read-write: /mnt/nas
    ./immich-ml.nix # face/search AI for the NAS's Immich (too little RAM there)
    ./outlook-mcp.nix # one SSH tunnel to the NAS's shared Outlook server for omp
    ./syncthing.nix # the heidi folder, synced with the notebook
    ./tailscale.nix # the tailnet: the phone reaches the PC, the PC the NAS
    ./t3code.nix # T3 Code server for agent sessions started from the phone
    ./vault.nix # Vaultwarden for agents: rbw and the vault fill tool
    ./calendar.nix # the NAS's calendars, synced for the bar's calendar card
    ../../modules/nixos/workstation.nix # what the PC shares with the laptop
    ../../modules/nixos/git-fetch.nix # fetches the clones the NAS shares via git
  ];

  gitFetch.dir = "%h/Documents/projects";

  home-manager.users.kirill = import ./home.nix;

  # Passwordless sudo for just the commands Claude Code needs to rebuild and
  # debug services unattended; everything else still asks for the password.
  security.sudo.extraRules = [
    {
      users = [ "kirill" ];
      commands =
        map
          (c: {
            command = "/run/current-system/sw/bin/${c}";
            options = [ "NOPASSWD" ];
          })
          [
            "nixos-rebuild"
            "systemctl"
            "journalctl"
          ];
    }
  ];

  # Generations are cheap individually -- they share almost every store path,
  # so the marginal cost of one is only what changed -- but they never expire
  # on their own. 290 of them had accumulated here, pinning ~57 GiB that was
  # otherwise unreachable, on top of ~185 GiB of plain garbage.
  #
  # Age, not count: nix.gc has no keep-newest-N option. 60d leaves roughly a
  # month of rollback depth, which is far more than the "the last switch broke
  # something" case this is actually for.
  nix.gc.options = "--delete-older-than 60d";

  boot.loader.systemd-boot = {
    memtest86.enable = true;
    # Caps boot menu entries and what lands on the 1 GiB ESP. Unrelated to
    # the store: this frees no /nix space and deletes no generations, it
    # only stops the menu and /boot growing without bound.
    configurationLimit = 100;
  };

  networking.hostName = "pc";

  # mDNS/zeroconf, so *.local names resolve (e.g. the NAS as nas.local)
  # instead of having to look up its address in the router's DHCP leases.
  # Publishing, for UxPlay, is in ./iphone.nix.
  #   nssmdns4     - wires mdns into /etc/nsswitch.conf, which is what actually
  #                  makes ssh/ping/curl able to resolve .local at all
  #   openFirewall - opens UDP/5353; without it the firewall drops the mDNS
  #                  replies, since multicast responses aren't ESTABLISHED
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    openFirewall = true;
  };

  users.users.kirill = {
    description = "Kirill Menke";
    extraGroups = [ "docker" ];
  };

  # Docker — needed by Strix to run its pentest agents in a sandbox container.
  virtualisation.docker.enable = true;

  environment.sessionVariables.CLAUDE_CODE_MAX_OUTPUT_TOKENS = "128000";

  # SDDM and its Catppuccin theme: modules/nixos/desktop.nix.
  services.displayManager.sddm.wayland.enable = true;

  # On-screen keystroke display for screen recordings (Wayland-native,
  # needs the setuid wrapper this module provides to read input devices)
  programs.wshowkeys.enable = true;

  # AusweisApp for ELSTER login; the iPhone app pairs as card reader
  # ("Smartphone als Kartenleser"), which needs UDP 24727 open.
  programs.ausweisapp = {
    enable = true;
    openFirewall = true;
  };

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = "25.05";

}
