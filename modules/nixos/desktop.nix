# The system side of the shared Hyprland desktop (modules/home/desktop.nix):
# what its bar, cards and lock screen need from NixOS on every host.
{ pkgs, ... }:

{
  programs.hyprland = {
    enable = true;
    xwayland.enable = true;
    # Hyprland 0.56 ships a "Hyprland (uwsm)" session next to the plain one,
    # and SDDM falls back to it whenever its remembered session path is gone
    # (every nixpkgs bump). Without UWSM's systemd user units installed that
    # session hangs right after login, so install them.
    withUWSM = true;
  };
  # Prefer the plain (non-UWSM) session, as before Hyprland 0.56.
  services.displayManager.defaultSession = "hyprland";

  # PAM entry so hyprlock (SUPER+L, the power card, hypridle) can check the
  # password; the package itself is in modules/home/desktop.nix.
  security.pam.services.hyprlock = { };

  networking.networkmanager.enable = true;
  # Captive portals (hotel, train, café Wi-Fi): every 5 minutes NetworkManager
  # fetches this page, which answers "NetworkManager is online" (its default
  # expected response). A sign-in page in its place makes it report "portal":
  # the bar says "Sign in" beside the network module, and the waybar Network
  # card offers to open the sign-in page.
  networking.networkmanager.settings.connectivity = {
    uri = "http://nmcheck.gnome.org/check_network_status.txt";
    interval = 300;
  };
  # The bar's connectivity module (connectivity.py: "Sign in", no internet,
  # a VPN's lock) asks NetworkManager only when told: on each change of its
  # verdict, and as a link or a VPN comes or goes, this sends every bar the
  # module's signal, RTMIN+9, so nothing polls. The bar runs as
  # .waybar-wrapped (the Nix wrapper), hence the pattern; -H skips a bar
  # still starting up, which an unhandled real-time signal would kill.
  # net-speed.sh (one per bar) re-reads NetworkManager's Metered on the same
  # signal, to turn the rates peach on a metered link: waybar passes no
  # signal on to a script that runs for good, so the second pkill sends it
  # to the scripts themselves (they run as bash, hence -f on the command
  # line; -H again skips one without the trap yet).
  networking.networkmanager.dispatcherScripts = [
    {
      source = pkgs.writeShellScript "waybar-connectivity" ''
        case "$2" in
          connectivity-change|up|down|vpn-up|vpn-down) ;;
          *) exit 0 ;;
        esac
        ${pkgs.procps}/bin/pkill -H -RTMIN+9 -x '\.?waybar(-wrapped)?' || true
        ${pkgs.procps}/bin/pkill -H -RTMIN+9 -f '/waybar/scripts/net-speed\.sh$' || true
      '';
    }
  ];

  # DDC/CI: external monitors' own brightness over the display cable, for the
  # waybar display card (bar-popup display). Loads i2c-dev and gives the seat
  # user access to /dev/i2c-*.
  hardware.i2c.enable = true;

  # fwupd, for the bar's attention module (attention.py): a peach "BIOS
  # update available", or any firmware update LVFS has for this machine.
  # The daemon starts on demand over D-Bus and quits five minutes after
  # its last call (IdleTimeout). NixOS's fwupd module turns on
  # fwupd-refresh.timer, which starts it hourly and fetches LVFS's metadata
  # once that is a day old. attention.py asks fwupd for its updates
  # whenever it shows up or announces a change (never at each check: every
  # call starts its five minutes over), keeps the answer in
  # ~/.local/state/attention-firmware.json, and starts fwupd itself only
  # if a day went by without one.
  services.fwupd.enable = true;

  # A keyboard that comes later (a dock, a receiver, the headphones' media
  # keys) starts on Hyprland's first layout. This starts kb-layout-sync
  # (modules/home/desktop.nix) in each user's session for every new key
  # device, and it gives the keyboard the layout in use.
  services.udev.extraRules = ''
    ACTION=="add", SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_KEY}=="1", TAG+="systemd", ENV{SYSTEMD_USER_WANTS}+="kb-layout-sync.service"
  '';
}
