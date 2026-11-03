#
# Kirill's calendars from the NAS's CalDAV server (hosts/nas/radicale.nix),
# the ones his iPhone shows, kept on this PC by vdirsyncer: every 5 minutes
# it fetches what changed there and sends what changed here, both ways. The
# copy lives in ~/.local/share/calendars/nas/<calendar>/, one .ics file per
# event, and the bar's calendar card (dotfiles/waybar/scripts/card_calendar.py)
# shows it. `systemctl --user start vdirsyncer` syncs now.
#
# Both sides changed the same event between two syncs: the NAS's version wins.
# Calendars come from the NAS: one made on the phone shows up here by itself.
# One deleted there stays here until its folder is deleted too.
#
# No khal (a calendar CLI that edits this copy): 0.14.1 fails to build in
# this nixpkgs (no man output), so it is in no binary cache.
#
{ pkgs, ... }:

let
  # The server, from tailscaled: its name stays out of this public repo.
  url = pkgs.writeShellScript "nas-calendar-url" ''
    suffix=$(${pkgs.tailscale}/bin/tailscale status --json | ${pkgs.jq}/bin/jq -r .MagicDNSSuffix)
    echo "https://nas.$suffix:5443/"
  '';
  # The calendar account's password, decrypted with Kirill's age key
  # (~/.config/sops/age/keys.txt) from the same encrypted file the NAS reads.
  password = pkgs.writeShellScript "nas-calendar-password" ''
    exec ${pkgs.sops}/bin/sops -d --extract '["calendar"]["password"]' ${../../secrets/nas.yaml}
  '';
in
{
  home-manager.users.kirill =
    { config, ... }:
    let
      calendars = "${config.xdg.dataHome}/calendars";
      vdirsyncer = "${pkgs.vdirsyncer}/bin/vdirsyncer";
    in
    {
      home.packages = [ pkgs.vdirsyncer ];

      # Written here, not with programs.vdirsyncer: its pairs can't say
      # `implicit = "create"`, which lets discover add a new calendar
      # without asking.
      xdg.configFile."vdirsyncer/config".text = ''
        [general]
        status_path = "${config.xdg.dataHome}/vdirsyncer/status/"

        [pair nas]
        a = "nas_remote"
        b = "nas_local"
        collections = ["from a"]
        implicit = "create"
        metadata = ["color", "displayname"]
        conflict_resolution = "a wins"

        [storage nas_remote]
        type = "caldav"
        url.fetch = ["command", "${url}"]
        username = "kirill"
        password.fetch = ["command", "${password}"]

        [storage nas_local]
        type = "filesystem"
        path = "${calendars}/nas/"
        fileext = ".ics"
      '';

      systemd.user.services.vdirsyncer = {
        Unit = {
          Description = "Sync the NAS's calendars with ~/.local/share/calendars";
          # A rebuild doesn't sync; the timer will.
          X-SwitchMethod = "keep-old";
        };
        Service = {
          Type = "oneshot";
          ExecStart = [
            "${vdirsyncer} discover"
            "${vdirsyncer} metasync"
            "${vdirsyncer} sync"
          ];
        };
      };
      systemd.user.timers.vdirsyncer = {
        Unit.Description = "Sync the NAS's calendars every 5 minutes";
        Timer = {
          OnCalendar = "*:0/5";
          # Right after boot or resume too, not only at the next 5 minutes.
          Persistent = true;
        };
        Install.WantedBy = [ "timers.target" ];
      };
    };
}
