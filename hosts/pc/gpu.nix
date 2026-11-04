#
# The PC's two GPUs: the GTX 1080 Ti on NVIDIA's proprietary legacy_580
# driver, and the Alder Lake iGPU (UHD 730). With the card in, as now, the
# iGPU is switched off in the firmware (only the card shows up in lspci);
# with the card out, the iGPU drives the screens and decodes video (iHD, from
# modules/nixos/workstation.nix). The screens' modes are in ./hyprland.lua.
#
{ config, pkgs, ... }:

let
  # True only when an NVIDIA card (PCI vendor 0x10de) is actually installed.
  # Used as ExecCondition on the NVIDIA-dependent units so they skip cleanly
  # while the 1080 Ti is out and start by themselves when it returns.
  # Checking /dev/nvidia* would not work: /dev/nvidiactl exists regardless.
  nvidiaPresent = "${pkgs.bash}/bin/bash -c 'grep -qx 0x10de /sys/bus/pci/devices/*/vendor'";
in
{
  boot.blacklistedKernelModules = [ "nouveau" ];

  services.xserver.videoDrivers = [ "nvidia" ];

  # For Wayland/Hyprland compatibility
  hardware.nvidia = {
    modesetting.enable = true;
    powerManagement.enable = true;
    nvidiaPersistenced = true; # keep the driver initialised between clients
    open = false; # Use proprietary drivers (GTX 1080Ti isn't supported by open drivers)
    nvidiaSettings = true;
    # GTX 1080 Ti (Pascal) — 595+ dropped support. The 2026-09 "1440p@60
    # only" cap was not this driver but nvidia-pstated forcing P0, which
    # pinned the display clock low; the daemon is gone (the Pascal "P-state
    # hangs" it was for were the RAM/board freezes), so don't pin P-states.
    package = config.boot.kernelPackages.nvidiaPackages.legacy_580;
  };

  # `vainfo`, to check what the GPU in use can decode: NVDEC through
  # nvidia-vaapi-driver with the card in, iHD on the iGPU with it out. libva
  # picks the driver by itself (see ./hyprland.lua).
  environment.systemPackages = [ pkgs.libva-utils ];

  # Every NVIDIA-dependent unit is gated on the card actually being in the
  # machine, so pulling it does not leave services crash-looping (the old
  # gpu-log logger hit 36 restarts in one boot while the 1080 Ti was out).  ExecCondition marks
  # the unit *skipped* rather than failed, so `nixos-rebuild` also stays quiet.
  #
  # The check is the PCI vendor ID: /dev/nvidia* is not usable because
  # /dev/nvidiactl exists even with no card installed.
  systemd.services.nvidia-persistenced.serviceConfig.ExecCondition = nvidiaPresent;
}
