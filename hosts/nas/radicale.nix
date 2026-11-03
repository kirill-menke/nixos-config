#
# Radicale: a CalDAV server for Kirill's calendars. The iPhone's Calendar app
# syncs with it as a CalDAV account, the PC through vdirsyncer
# (hosts/pc/calendar.nix), whose copy the bar's calendar card shows. Every
# client keeps a full copy, so this box being down stops sync, not access.
#
# Reached over the tailnet only: Radicale listens on loopback and `tailscale
# serve` publishes it as https://nas.<tailnet>.ts.net:5443, the server to give
# every client. The phone needs Tailscale connected to sync.
#
# One account, kirill. Its password is sops calendar/password
# (secrets/nas.yaml): the PC's vdirsyncer decrypts it with Kirill's age key,
# and the phone got it once, inside the profile that set up its account.
# Calendars live under /kirill/: kalender and privat came over from iCloud on
# 2026-11-04, out of the iPhone backup of 2026-11-03, alerts included. One
# made on the phone reaches the PC at its next sync. The web page at /.web/
# lists them and takes .ics uploads.
#
# History: every change is a git commit in /var/lib/radicale/collections (the
# storage hook below). An event deleted by mistake, or a calendar a client
# wiped (which every other copy then syncs), comes back from there:
#   sudo -u radicale git -C /var/lib/radicale/collections log --stat
#   sudo -u radicale git -C /var/lib/radicale/collections checkout <commit>~ -- <file>
# and the clients fetch the file again at their next sync.
#
{ config, pkgs, ... }:
let
  port = 5232; # loopback, Radicale's default
  # Tailnet, via tailscale serve. Taken there: 443 = finance, 2443 = Immich,
  # 3000 = AdGuard, 6443 = Vaultwarden, 7443 = T3 Code, 8443 = ntfy,
  # 9443 = vibereel-phone, 10443 = Paperless.
  httpsPort = 5443;
  collections = "/var/lib/radicale/collections";
  serve = "${pkgs.tailscale}/bin/tailscale serve";
in
{
  services.radicale = {
    enable = true;
    # No server.hosts: Radicale stays on localhost:${toString port}, and the
    # module then also confines the process to loopback addresses.
    settings = {
      auth = {
        type = "htpasswd";
        htpasswd_filename = config.sops.templates."radicale-users".path;
        # The password sits in /run/secrets in clear anyway, readable by root
        # alone, as this file is by radicale alone; a hash would add nothing.
        htpasswd_encryption = "plain";
      };
      # Run in the collections folder after every write. shlex-quoted user
      # name, hence outside the message's quotes.
      storage.hook = ''git add -A && (git diff --cached --quiet || git commit --quiet -m "Changes by "%(user)s)'';
    };
  };

  # Rendered into /run, owned by radicale (the service has PrivateUsers=, so
  # a group would not let it read the file).
  sops.secrets."calendar/password" = {
    sopsFile = ../../secrets/nas.yaml;
    restartUnits = [ "radicale.service" ];
  };
  sops.templates."radicale-users" = {
    content = "kirill:${config.sops.placeholder."calendar/password"}\n";
    owner = "radicale";
    restartUnits = [ "radicale.service" ];
  };

  systemd.services = {
    radicale = {
      path = [ pkgs.git ];
      environment = {
        GIT_AUTHOR_NAME = "Radicale";
        GIT_AUTHOR_EMAIL = "radicale@nas";
        GIT_COMMITTER_NAME = "Radicale";
        GIT_COMMITTER_EMAIL = "radicale@nas";
      };
      # The history's repository, once. Radicale's lock, caches and half-written
      # files stay out of it.
      preStart = ''
        cd ${collections}
        if [ ! -d .git ]; then
          git init --quiet --initial-branch=main
          printf '%s\n' .Radicale.cache .Radicale.lock '.Radicale.tmp-*' >.gitignore
          git add -A
          git commit --quiet -m "Start of the history"
        fi
      '';
    };

    # Same pattern as vaultwarden.nix; `off` on stop clears only this port.
    radicale-tailscale-serve = {
      description = "Publish Radicale on port ${toString httpsPort} via tailscale serve";
      after = [
        "tailscaled.service"
        "radicale.service"
      ];
      wants = [ "tailscaled.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${serve} --bg --https=${toString httpsPort} http://127.0.0.1:${toString port}";
        ExecStop = "${serve} --https=${toString httpsPort} off";
      };
    };
  };
}
