#
# The NAS's photo archive at /mnt/photos, read-write: /tank/data/photos is the
# master (the PC keeps no copy since 2026-11-08) and Immich's external library
# (hosts/nas/immich.nix). Files copied in show in Immich after its nightly
# library scan; Immich itself never writes there.
#
# A delete here deletes the master. Undo comes from the NAS's hourly/daily/
# monthly ZFS snapshots (hosts/nas/snapshots.nix): /mnt/photos/.zfs/snapshot/.
#
# sshfs like ./paperless.nix: mounted on first access and unmounted after 10
# idle minutes, so neither boot nor an idle desktop hangs on a NAS outage. It
# logs in as kirill, who owns the archive; new files get group immich from the
# setgid folders, so Immich can read them. The NAS's host key is pinned in
# ./paperless.nix.
#
{ config, ... }:

{
  fileSystems."/mnt/photos" = {
    device = "kirill@192.168.0.175:/tank/data/photos";
    fsType = "sshfs";
    noCheck = true; # no block device to fsck
    options = [
      "x-systemd.automount"
      "x-systemd.idle-timeout=600"
      "_netdev"
      # systemd mounts it as root and kirill uses it: every file shows as
      # kirill's with its mode on the NAS, so no other user gets in.
      "allow_other"
      "default_permissions"
      "uid=1000" # kirill
      "gid=${toString config.ids.gids.users}"
      # ssh runs as root, so kirill's key is spelled out, and it must never
      # wait for a prompt.
      "IdentityFile=/home/kirill/.ssh/id_ed25519"
      "BatchMode=yes"
      "ConnectTimeout=10"
      "ServerAliveInterval=15"
      "ServerAliveCountMax=3"
      "reconnect"
      # File manager sidebar entry.
      "x-gvfs-show"
      "x-gvfs-name=Photos"
    ];
  };
}
