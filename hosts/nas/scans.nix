#
# Scan inbox: the Brother ADS-1800W drops every scan into one SMB share,
# \\192.168.0.175\scans -> nas.scanRoot (/tank/data/scans), which is
# Paperless's consumption directory (paperless.nix). Paperless OCRs and files
# each PDF, then deletes it here: the share is an inbox, not an archive.
#
# Why SMB: the scanner's network-folder targets are CIFS and (S)FTP only; the
# NFS export in media.nix is not an option for it. Only smbd runs: the scanner
# addresses the share by IP, so NetBIOS name service (nmbd) and domain
# membership (winbindd) stay off.
#
# Access: one Samba account, `scanner`, valid on this share only, LAN only.
# smbd writes every file as the paperless user (force user below), so the
# consumer, sandboxed with PrivateUsers=, owns what it has to read and delete.
#
# Its password is the sops secret nas/scanner-smb-password. Samba keeps its own
# password database (/var/lib/samba/private/passdb.tdb), so
# samba-scanner-password below re-syncs it from the secret on every boot and
# whenever the secret changes. To rotate: `sops secrets/nas.yaml`, deploy, then
# update the password in the scanner's Scan to Network profile.
#
# Data safety: files only sit here until Paperless has consumed them.
#
{ config, ... }:
let
  inherit (config.nas) scanRoot;
  inherit (config.services.paperless) user group;
  passwordFile = config.sops.secrets."nas/scanner-smb-password".path;
in
{
  # No shell, no home: exists only because Samba authenticates against a Unix
  # account. Files never belong to it (force user below). The directory itself
  # is created by the paperless module (consumptionDir).
  users.users.scanner = {
    isSystemUser = true;
    group = "users";
    description = "Brother ADS-1800W scan-to-SMB";
  };

  services.samba = {
    enable = true;
    nmbd.enable = false;
    winbindd.enable = false;

    settings = {
      global = {
        "server string" = "nas";
        # LAN clients only. tailscale0 is a trusted firewall interface
        # (tailscale.nix), so this, not the firewall, keeps the tailnet out.
        "hosts allow" = "192.168.0.0/24 127.0.0.1";
        "hosts deny" = "0.0.0.0/0";
        "map to guest" = "never";

        # No printing: stops smbd from probing for CUPS and logging about it.
        "load printers" = "no";
        printing = "bsd";
        "printcap name" = "/dev/null";
        "disable spoolss" = "yes";
      };

      scans = {
        path = scanRoot;
        comment = "Scanner inbox (Paperless consumption dir)";
        "valid users" = "scanner";
        "read only" = "no";
        "force user" = user;
        "force group" = group;
        "create mask" = "0640";
        "directory mask" = "0750";
      };
    };
  };

  # Brother documents Scan to Network as using 139 and 445; no UDP is needed
  # with nmbd off.
  networking.firewall.allowedTCPPorts = [
    139
    445
  ];

  sops.secrets."nas/scanner-smb-password" = {
    sopsFile = ../../secrets/nas.yaml;
    restartUnits = [ "samba-scanner-password.service" ];
  };

  systemd.services.samba-scanner-password = {
    description = "Sync the scanner's Samba password from sops";
    wantedBy = [ "multi-user.target" ];
    before = [ "samba-smbd.service" ];
    path = [ config.services.samba.package ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    # smbpasswd -s reads the new password twice from stdin, so it never
    # reaches an argv (sudo/journald) or the store. `smbpasswd -a` on an
    # existing account just sets the password, so this is idempotent.
    script = ''
      mkdir -p /var/lib/samba/private
      pw=$(cat ${passwordFile})
      printf '%s\n%s\n' "$pw" "$pw" | smbpasswd -s -a scanner
    '';
  };
}
