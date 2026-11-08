#
# Off-site backup: restic → Hetzner Storage Box BX11 (1 TB, Falkenstein, ordered
# 2026-11-07 in the Cloud Console project "kirill.es", box #694655).
#
# Only what can't be replaced goes off-site (~160 GB); the media library
# (/tank/data/media, ~2.2 TB) can be downloaded again and stays local.
# restic encrypts on this box before upload, so Hetzner only ever sees ciphertext.
#
# Secrets in secrets/nas.yaml (sops), never in this public repo:
#   backup/restic-password      the repository key. Losing it = losing the backup;
#                               Kirill's age key (recovery sheet) decrypts nas.yaml
#   backup/storagebox-ssh-key   private half of the key "nas-restic" on the box
#   backup/storagebox-user      the box's login (u…), which is also its host name
#
# Restore, e.g.: sudo restic-storagebox snapshots
#                sudo restic-storagebox restore latest --target /tmp/r --include /var/lib/radicale
#
{ config, pkgs, ... }:

let
  staging = "/var/lib/restic-staging";
  user = config.sops.placeholder."backup/storagebox-user";
  # HostKeyAlias keeps the box's name out of known_hosts, so this file can live
  # in the store. Key read with ssh-keyscan -p 23 on 2026-11-07.
  knownHosts = pkgs.writeText "storagebox-known-hosts" ''
    storagebox ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIICf9svRenC/PLKIL9nk6K/pxQgoiFC41wTNvoIncOxs
  '';
in
{
  sops.secrets = {
    "backup/restic-password".sopsFile = ../../secrets/nas.yaml;
    "backup/storagebox-ssh-key".sopsFile = ../../secrets/nas.yaml;
    "backup/storagebox-user".sopsFile = ../../secrets/nas.yaml;
  };
  sops.templates."storagebox-ssh-config".content = ''
    Host storagebox
      HostName ${user}.your-storagebox.de
      User ${user}
      Port 23
      HostKeyAlias storagebox
      IdentityFile ${config.sops.secrets."backup/storagebox-ssh-key".path}
      IdentitiesOnly yes
      UserKnownHostsFile ${knownHosts}
      StrictHostKeyChecking yes
      ServerAliveInterval 60
      ServerAliveCountMax 10
  '';

  services.restic.backups.storagebox = {
    # The box's SFTP root is read-only; the account's own space is /home.
    repository = "sftp:storagebox:/home/restic";
    passwordFile = config.sops.secrets."backup/restic-password".path;
    extraOptions = [
      "sftp.command='${pkgs.openssh}/bin/ssh -F ${
        config.sops.templates."storagebox-ssh-config".path
      } storagebox -s sftp'"
    ];
    initialize = true;
    paths = [
      "/tank/data/pc-documents" # PC ~/Documents, Syncthing receive-only (syncthing.nix)
      "/tank/data/photos" # PC ~/Pictures/photos, Syncthing receive-only
      # iphone-15/, vaultwarden/ (nightly 23:00 UTC, vaultwarden.nix) and the
      # paperless/ dataset (nightly 01:30, paperless.nix); restic crosses into
      # the child dataset's mount.
      "/tank/data/backups"
      "/tank/data/immich/backups" # Immich's own nightly DB dumps
      "/tank/data/immich/upload" # phone uploads: no other copy
      "/tank/data/immich/library"
      "/var/lib/radicale"
      "/home/kirill/Documents/memory"
      "/home/kirill/Documents/profile"
      staging
    ];
    exclude = [
      ".stversions"
      ".stfolder"
    ];
    # wacli writes its SQLite database all the time; back up a consistent copy.
    backupPrepareCommand = ''
      install -d -m 700 ${staging}
      if [ -e /var/lib/wacli/wacli.db ]; then
        ${pkgs.sqlite}/bin/sqlite3 /var/lib/wacli/wacli.db ".backup '${staging}/wacli.db'"
      fi
    '';
    pruneOpts = [
      "--keep-daily 7"
      "--keep-weekly 5"
      "--keep-monthly 12"
      "--keep-yearly 3"
    ];
    checkOpts = [ "--read-data-subset=1%" ];
    # After paperless-export (01:30) and Immich's dump (02:00).
    timerConfig = {
      OnCalendar = "03:30";
      RandomizedDelaySec = "15m";
      Persistent = true;
    };
  };

  systemd.services.restic-backups-storagebox = {
    unitConfig = {
      RequiresMountsFor = [ "/tank/data" ];
      # ntfy topic finance-system, like sanoid and the scrub (finance.nix).
      OnFailure = [ "finance-notify-failure@%n.service" ];
    };
    serviceConfig = {
      Nice = 19;
      IOSchedulingClass = "idle";
    };
  };
}
