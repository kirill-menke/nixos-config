#
# TerraMaster F4-425 Plus -- headless NAS.
#
# Intel N150, 16 GB DDR5, 2x Realtek RTL8126 5GbE, 4x SATA bays, 3x M.2 NVMe.
# No BMC/IPMI: once installed there is no console unless a monitor is physically
# attached, so anything that could block boot on the network is avoided here.
#
# Installed with nixos-anywhere; disk layout lives in ./disko.nix.
#
{
  config,
  lib,
  pkgs,
  ...
}:
{
  imports = [ ./disko.nix ];

  #############################################################################
  # Boot / hardware
  #############################################################################

  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # Enough to find the NVMe root and the SATA disks in early boot. Both SATA
  # controllers (Intel Alder Lake-N AHCI, ASMedia ASM1061/1062) use ahci.
  boot.initrd.availableKernelModules = [
    "xhci_pci"
    "ahci"
    "nvme"
    "usbhid"
    "usb_storage"
    "sd_mod"
  ];
  boot.kernelModules = [ "kvm-intel" ];

  hardware.enableRedistributableFirmware = true;

  # Explicit rather than inherited: this flake's nixpkgs gives linuxPackages
  # 6.18.39, and ZFS 2.4.3 builds against it (verified: the resolved module is
  # zfs-kernel-2.4.3-6.18.39 with meta.broken = false). linuxPackages_latest is
  # NOT supported by ZFS -- pinning the default here stops a future nixpkgs bump
  # from silently moving this host onto a kernel whose ZFS module won't build.
  # Re-check this after any `nix flake update`.
  boot.kernelPackages = pkgs.linuxPackages;

  #############################################################################
  # ZFS
  #############################################################################

  boot.supportedFilesystems.zfs = true;

  # Required by the ZFS module (it asserts on null). Must be unique per host and
  # stable forever -- it is what stops two machines importing the same pool.
  # Generated once; do not change it after the pool exists.
  networking.hostId = "a3f21c07";

  # Root is ext4 on the NVMe, so no pool needs importing to reach the rootfs.
  # Leaving this false means a dirty pool is never force-imported behind your
  # back, which is the safe default.
  boot.zfs.forceImportRoot = false;

  # Monthly scrub. On a single-disk vdev this can only *detect* corruption, not
  # repair it -- there is no second copy to heal from until the mirror exists.
  # Still worth it: you want to know.
  services.zfs.autoScrub = {
    enable = true;
    interval = "monthly";
  };

  # Mail-less ZED: log events to the journal. Without a configured MTA the mail
  # path would just fail silently, so it is explicitly off.
  services.zfs.zed.enableMail = false;

  #############################################################################
  # Networking
  #############################################################################

  networking.hostName = "nas";

  # DHCP on whichever port is patched. The lease is keyed to the NIC MAC, so
  # this host keeps 192.168.0.175 across reboots and across the install itself.
  networking.useDHCP = lib.mkDefault true;

  # Announce as nas.local, so the box is reachable without consulting DHCP
  # leases. (The desktop resolves this via its own avahi + nssmdns4.)
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    openFirewall = true;
    publish = {
      enable = true;
      addresses = true;
      workstation = true;
    };
  };

  # Headless box with no BMC: a boot that blocks waiting for the network is
  # unrecoverable without physically attaching a monitor.
  systemd.network.wait-online.enable = false;

  networking.firewall = {
    enable = true;
    allowedTCPPorts = [ 22 ];
  };

  #############################################################################
  # Access
  #############################################################################

  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "prohibit-password";
      PasswordAuthentication = false;
    };
  };

  users.users.root.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAOzNg1J0OHBLfMX2OnvNNhXFTqqwVR+lpVh+uSYZSXY kirill@pc"
  ];

  users.users.kirill = {
    isNormalUser = true;
    description = "Kirill Menke";
    extraGroups = [ "wheel" ];
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAOzNg1J0OHBLfMX2OnvNNhXFTqqwVR+lpVh+uSYZSXY kirill@pc"
    ];
  };

  # No password is set for kirill (key-only login), so sudo could never be used
  # without this. Single-admin headless box; the SSH key is the real boundary.
  security.sudo.wheelNeedsPassword = false;

  # Console fallback if the network ever fails to come up and you attach a
  # monitor.
  #
  # The hash lives in a file on the machine rather than inline here, because
  # this repo is public. A sha-512 crypt hash is not a secret you can publish
  # safely: at the default 5000 rounds it is cheap to attack offline on a GPU,
  # and anything pushed to GitHub is scraped and cached permanently, so
  # deleting it later does not undo the exposure.
  #
  # Provision it out of band, once, on the NAS:
  #
  #   install -Dm600 /dev/stdin /var/lib/nixos-secrets/root.hash \
  #     <<< "$(mkpasswd -m sha-512)"
  #
  # If the file is missing, root simply ends up with no valid password (login
  # via console is refused); SSH key auth is unaffected either way.
  users.users.root.hashedPasswordFile = "/var/lib/nixos-secrets/root.hash";

  #############################################################################
  # Base tooling
  #############################################################################

  environment.systemPackages = with pkgs; [
    git
    vim
    tmux
    htop
    smartmontools
    nvme-cli
    ethtool
    pciutils
    rsync
    curl
  ];

  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];

  # Matches the nixpkgs this host is first installed from. Do not bump casually.
  system.stateVersion = "25.11";
}
