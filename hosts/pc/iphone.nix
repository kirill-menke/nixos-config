#
# The work iPhone: the PC's keyboard and mouse type on it over Bluetooth
# (Super+I, iphone-hid), and it mirrors its screen to the PC over AirPlay
# (UxPlay, the `iphone` command). Not over USB: the company MDM forbids host
# pairing and app installs.
#
{ config, pkgs, ... }:

let
  # Super+I bridge: the PC's keyboard and mouse become a Bluetooth HID
  # device for the work iPhone (see the docstring in the script).
  iphone-hid = pkgs.writers.writePython3Bin "iphone-hid" {
    libraries = with pkgs.python3Packages; [
      evdev
      dbus-fast
    ];
    # F821/F722: dbus-fast declares D-Bus signatures as string annotations
    # ("o", "a{sv}"), which pyflakes mistakes for forward references.
    flakeIgnore = [
      "E501"
      "F821"
      "F722"
    ];
  } (builtins.readFile ./iphone-hid.py);
in
{
  hardware.bluetooth = {
    powerOnBoot = true;
    # For iphone-hid, which makes this adapter a keyboard+mouse:
    #   input    - listens on the HID L2CAP PSMs 17/19 that iphone-hid needs.
    #              Costs the PC the use of Bluetooth keyboards, mice and
    #              gamepads; none are used here (both input devices are
    #              2.4 GHz dongles, the only pairing is the Bose headphones).
    #   hostname - would reset the device class to "desktop computer", and
    #              iOS does not offer computers in its Bluetooth list.
    disabledPlugins = [
      "input"
      "hostname"
    ];
    settings.General = {
      Name = "PC";
      # Major class peripheral, minor class keyboard+pointing combo.
      Class = "0x0005C0";
    };
  };

  systemd.services.iphone-hid = {
    description = "PC keyboard and mouse as a Bluetooth HID device for the iPhone";
    wantedBy = [ "multi-user.target" ];
    after = [ "bluetooth.service" ];
    requires = [ "bluetooth.service" ];
    # Restart with bluetoothd: its agent and SDP record die with it.
    partOf = [ "bluetooth.service" ];
    path = [
      pkgs.util-linux # runuser, for desktop notifications
      pkgs.libnotify
    ];
    environment.IPHONE_HID_USER = "kirill";
    serviceConfig = {
      ExecStart = "${iphone-hid}/bin/iphone-hid";
      StateDirectory = "iphone-hid";
      Restart = "on-failure";
      RestartSec = 3;
    };
  };

  # UxPlay registers its AirPlay service through avahi's DNS-SD socket (avahi
  # itself is in ./default.nix); without publishing, the iPhone never sees
  # the PC.
  services.avahi.settings.publish = {
    disable-publishing = false;
    disable-user-service-publishing = false;
    publish-addresses = true;
  };

  # 7000/7001/7100 TCP + 6000/6001/7011 UDP: UxPlay AirPlay mirroring, which
  # uses these fixed ports only when started with `-p` (default is random).
  # Scoped to the wired LAN interface rather than opened globally.
  # (Interface-scoped rather than `extraInputRules`, which is nftables-only and
  # this host still uses the iptables backend.)
  networking.firewall.interfaces.${config.pc.lanInterface} = {
    allowedTCPPorts = [
      7000
      7001
      7100
    ];
    allowedUDPPorts = [
      6000
      6001
      7011
    ];
  };

  home-manager.users.kirill = {
    home.packages = with pkgs; [
      # AirPlay receiver: mirrors the work iPhone (Control Center ->
      # Bildschirmsynchronisierung). Run as `uxplay -p` (the `iphone` alias
      # below) so it uses the fixed ports opened on the LAN interface above.
      uxplay
    ];

    programs.zsh.shellAliases = {
      # AirPlay receiver for the work iPhone (Control Center ->
      # Bildschirmsynchronisierung -> PC).  -p pins the ports the firewall
      # opens; -nh keeps the name a plain "PC".  Detached with setsid so the
      # terminal can be closed; any running instance is replaced first, since
      # a second one could not bind the same ports.
      iphone = "pkill -f '^uxplay '; setsid -f uxplay -p -n PC -nh >/dev/null 2>&1";
    };
  };
}
