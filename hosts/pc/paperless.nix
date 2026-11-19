#
# The NAS's Paperless documents as plain files at /mnt/paperless:
# <year>/<correspondent>/<title>.pdf, one file per document, the one the web
# UI's Download button serves, read-only. The NAS refreshes that tree every
# minute (hosts/nas/paperless-view.nix), so new, renamed and deleted documents
# show here within a minute or two.
#
# /mnt/paperless/inbox is the one writable folder: it is Paperless's
# consumption directory, so a file copied in is imported like a scan and
# disappears once it is. Files only: Paperless does not look into folders,
# and a file it cannot import stays there.
#
# sshfs, mounted on first access and unmounted after 10 idle minutes, so
# neither boot nor the desktop waits for the NAS. It logs in as kirill; on the
# NAS the SFTP server runs as the paperless user in a sandbox that refuses
# writes outside inbox/ ("Operation not permitted").
#
{ config, ... }:

{
  fileSystems."/mnt/paperless" = {
    device = "kirill@192.168.0.175:/var/lib/paperless/view";
    fsType = "sshfs";
    noCheck = true; # no block device to fsck
    options = [
      "x-systemd.automount"
      "x-systemd.idle-timeout=600"
      "_netdev"
      # systemd mounts it as root and kirill uses it: every file shows as
      # kirill's with its mode on the NAS (0600), so no other user gets in.
      "allow_other"
      "default_permissions"
      "uid=1000" # kirill
      "gid=${toString config.ids.gids.users}"
      # ssh runs as root, so kirill's key and the NAS's host key (below) are
      # spelled out, and it must never wait for a prompt.
      "IdentityFile=/home/kirill/.ssh/id_ed25519"
      "BatchMode=yes"
      "ConnectTimeout=10"
      "ServerAliveInterval=15"
      "ServerAliveCountMax=3"
      "reconnect"
      "sftp_server=/run/current-system/sw/bin/paperless-view-sftp"
      # File manager sidebar: gvfs hides mounts outside /media and /run/media
      # unless asked to show them.
      "x-gvfs-show"
      "x-gvfs-name=Paperless"
    ];
  };

  programs.ssh.knownHosts.nas = {
    hostNames = [ "192.168.0.175" ];
    publicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFXzQxolK0FvMWjxX2UEB2mCltABp02YErdTywnar3cT";
  };
}
