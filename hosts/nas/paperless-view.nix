#
# Paperless's documents as plain files, one per document, for the PC to mount
# read-only at /mnt/paperless (hosts/pc/paperless.nix).
#
# paperless-view.service rebuilds the tree in /var/lib/paperless/view every
# minute: <year>/<correspondent>/<title>.pdf, each the file the web UI's
# Download button serves, as a hardlink into Paperless's own storage
# (paperless-view.py says what it picks and why). It only reads the database.
# The tree stays outside MEDIA_ROOT, where Paperless's sanity check would
# report every link as an orphaned file.
#
# The links keep Paperless's owner and mode (paperless, 0600), so the PC logs
# in as kirill and runs the SFTP server as the paperless user, read-only:
# paperless-view-sftp. That grants nothing new, as kirill has passwordless
# sudo here.
#
{ config, pkgs, ... }:
let
  cfg = config.services.paperless;
  viewDir = "${cfg.dataDir}/view";

  generator = pkgs.writers.writePython3Bin "paperless-view" { } (
    builtins.readFile ./paperless-view.py
  );

  # sshfs takes it by path (-o sftp_server=/run/current-system/sw/bin/...):
  # a name without a slash would be looked up as an sshd subsystem.
  sftp = pkgs.writeShellApplication {
    name = "paperless-view-sftp";
    text = ''
      exec /run/wrappers/bin/sudo -n -u ${cfg.user} ${config.services.openssh.package}/libexec/sftp-server -R
    '';
  };
in
{
  environment.systemPackages = [ sftp ];

  systemd.tmpfiles.settings."10-paperless-view".${viewDir}.d = {
    inherit (cfg) user;
    inherit (config.users.users.${cfg.user}) group;
    mode = "0700";
  };

  systemd.services.paperless-view = {
    description = "Paperless documents as one file each in ${viewDir}";
    serviceConfig = {
      Type = "oneshot";
      User = cfg.user;
      ExecStart = "${generator}/bin/paperless-view ${cfg.dataDir}/db.sqlite3 ${cfg.mediaDir}/documents ${viewDir}";
      # It runs every minute, so systemd's info messages (started, finished)
      # stay out of the journal; its own lines (what changed) and failures
      # are notices and above.
      LogLevelMax = "notice";
      SyslogLevel = "notice";
      UMask = "0077";
      NoNewPrivileges = true;
      PrivateTmp = true;
      PrivateNetwork = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      # One writable mount for the database's -shm file, the storage and the
      # view: a hardlink cannot cross mounts, not even two bind mounts of the
      # same file system.
      ReadWritePaths = [ cfg.dataDir ];
    };
  };

  systemd.timers.paperless-view = {
    wantedBy = [ "timers.target" ];
    timerConfig.OnCalendar = "minutely";
  };
}
