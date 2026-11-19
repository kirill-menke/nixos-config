#
# What may wake the PC from suspend: the power button and a magic packet on
# the wired LAN (`wake-pc` on the NAS sends one), but not the 2.4 GHz
# receivers of the mouse and keyboard.
#
{ config, pkgs, ... }:

let
  lanIf = config.pc.lanInterface;
in
{
  # The 2.4 GHz receivers resume the box the instant S3 is entered — the mouse
  # sensor twitches, the dongle signals remote wakeup, and suspend lasts <1s.
  # Keep them from arming USB wakeup at all; the power button still wakes.
  services.udev.extraRules = ''
    ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="046d", ATTR{idProduct}=="c547", ATTR{power/wakeup}="disabled"
    ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="36b0", ATTR{idProduct}=="3002", ATTR{power/wakeup}="disabled"
  '';

  # Wake-on-LAN on the wired NIC, so a suspended box can be brought back from
  # the phone (`wake-pc` on the NAS).  The PCI device had wakeup disabled, so
  # nothing could reach it in S3 at all.
  #
  # `wol g` is magic packet only.  It used to be `wol ug`, adding unicast so the
  # phone's connection attempt was itself the wake trigger and nothing
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
  # NOT `networking.interfaces.<if>.wakeOnLan`: that writes a systemd .link
  # file, which NixOS only installs into /etc/systemd/network when
  # systemd-networkd is enabled.  This host runs NetworkManager, so the file was
  # never placed, udev went on applying 99-default.link, and the setting looked
  # applied while the NIC stayed wakeup-disabled.  Setting it with ethtool works
  # regardless of which network daemon is in charge.
  systemd.services.wake-on-lan = {
    description = "Arm Wake-on-LAN on ${lanIf}";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = pkgs.writeShellScript "arm-wol" ''
        ${pkgs.ethtool}/bin/ethtool -s ${lanIf} wol g
      '';
    };
  };

  # Re-arm immediately before suspending: the setting only matters at that
  # moment, and NetworkManager reactivating the link can clear it in between.
  powerManagement.powerDownCommands = ''
    ${pkgs.ethtool}/bin/ethtool -s ${lanIf} wol g || true
  '';
}
