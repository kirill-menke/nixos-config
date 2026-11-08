#
# The NAS's data pool at /mnt/nas, read-write, as the sidebar entry "NAS".
# Replaces opening sftp://nas in the file manager (a GVfs session that dies
# with the login and lives only under /run/user/1000/gvfs).
#
# sshfs, not NFS: NFS without Kerberos trusts whatever uid the client claims,
# so a read-write export would let any LAN/tailnet host write as kirill. The
# read-only NFS media mount (./tv.nix) stays NFS for streaming speed.
#
# It logs in as kirill, so the NAS's own permissions apply: immich/ and
# vaultwarden-backup/ stay closed, the rest is reachable. pc-documents/ is the
# receive side of the Syncthing copy of ~/Documents: edits made there are
# overwritten by the PC, so change files in ~/Documents instead. Deletes are
# undone from the ZFS snapshots under <dataset>/.zfs/snapshot/ where the
# dataset has them (photos, pc-documents, paperless-export).
#
# Same mount options as ./photos.nix: mounted on first access and unmounted
# after 10 idle minutes, so neither boot nor an idle desktop hangs on a NAS
# outage. The NAS's host key is pinned in ./paperless.nix.
#
{ config, ... }:

{
  fileSystems."/mnt/nas" = {
    device = "kirill@192.168.0.175:/tank/data";
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
      "x-gvfs-name=NAS"
    ];
  };
}
