#
# Paperless's documents as plain files, one per document, for the PC to mount
# at /mnt/paperless (hosts/pc/paperless.nix), with an inbox for new ones.
#
# paperless-view.service rebuilds the tree in /var/lib/paperless/view every
# minute: <year>/<correspondent>/<title>.pdf, each the file the web UI's
# Download button serves, as a hardlink into Paperless's own storage
# (paperless-view.py says what it picks and why). It only reads the database.
# The tree stays outside MEDIA_ROOT, where Paperless's sanity check would
# report every link as an orphaned file.
#
# paperless-view-sftp serves the tree to the PC, which logs in as kirill. The
# links keep Paperless's owner and mode (paperless, 0600) and are Paperless's
# own stored files, so sftp-server runs as the paperless user in a sandbox
# where the whole file system is read-only except inbox/: there the
# consumption directory (the scanner's share, scans.nix) is bound over the
# empty directory the service keeps, so a file copied in is consumed like a
# scan. That grants nothing new, as kirill has passwordless sudo here.
#
{ config, pkgs, ... }:
let
  cfg = config.services.paperless;
  viewDir = "${cfg.dataDir}/view";
  inbox = "inbox";

  generator = pkgs.writers.writePython3Bin "paperless-view" { } (
    builtins.readFile ./paperless-view.py
  );

  # sshfs takes it by path (-o sftp_server=/run/current-system/sw/bin/...):
  # a name without a slash would be looked up as an sshd subsystem. Each SFTP
  # session is a transient unit that ends when the PC disconnects.
  sftp = pkgs.writeShellApplication {
    name = "paperless-view-sftp";
    text = ''
      exec /run/wrappers/bin/sudo -n ${config.systemd.package}/bin/systemd-run \
        --quiet --pipe --wait --collect --service-type=exec \
        --description="Paperless view over SFTP" \
        --property=User=${cfg.user} \
        --property=ProtectSystem=strict \
        --property=BindPaths=${cfg.consumptionDir}:${viewDir}/${inbox} \
        --property=ProtectHome=yes \
        --property=PrivateTmp=yes \
        --property=PrivateNetwork=yes \
        --property=NoNewPrivileges=yes \
        ${config.services.openssh.package}/libexec/sftp-server
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
      ExecStart = "${generator}/bin/paperless-view ${cfg.dataDir}/db.sqlite3 ${cfg.mediaDir}/documents ${viewDir} ${inbox}";
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
