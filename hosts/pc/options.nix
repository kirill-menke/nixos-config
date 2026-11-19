#
# Shared knobs for the PC modules, so a value is spelled once and the modules
# that share it cannot drift apart (as in hosts/nas/options.nix).
#
{ lib, ... }:
{
  options.pc = {
    lanInterface = lib.mkOption {
      type = lib.types.str;
      default = "enp4s0";
      description = ''
        The wired LAN NIC (MAC 9c:6b:00:a3:38:bc), for Wake-on-LAN
        (wake-on-lan.nix) and the firewall ports of UxPlay (iphone.nix) and
        Syncthing (syncthing.nix). Its name follows the PCI bus number, so it
        can change when PCIe cards are added or pulled -- it used to be
        enp3s0. Check `ip -br link` if WoL fails again.
      '';
    };
  };
}
