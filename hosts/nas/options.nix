#
# Shared knobs for the NAS modules, so each path is spelled once and the
# modules that share it cannot drift apart.
#
{ lib, ... }:
{
  options.nas = {
    mediaRoot = lib.mkOption {
      type = lib.types.str;
      default = "/tank/data/media";
      description = "Jellyfin library; NFS-exported to the desktop (media.nix).";
    };

    downloadRoot = lib.mkOption {
      type = lib.types.str;
      default = "/tank/data/downloads";
      description = ''
        qBittorrent's download tree. Must live on the same ZFS dataset as
        mediaRoot so the Sonarr/Radarr imports are hardlinks (see arr.nix).
      '';
    };

    scanRoot = lib.mkOption {
      type = lib.types.str;
      default = "/tank/data/scans";
      description = ''
        Scanner inbox: written over SMB by the Brother ADS-1800W (scans.nix),
        consumed and emptied by Paperless (paperless.nix).
      '';
    };

    secretsDir = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/nixos-secrets";
      description = ''
        Unmanaged, root-owned directory for secrets provisioned by hand on the
        box -- this repo is public. Prefer sops (secrets.nix), which survives a
        reinstall; this is for anything not migrated yet.
      '';
    };
  };
}
