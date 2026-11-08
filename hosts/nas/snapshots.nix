# Local ZFS snapshots: undo for a wrong delete or overwrite, not a backup.
# They live on the same single disk as the data; the off-site copy is
# backup.nix.
#
# Only the irreplaceable folders that something writes into get snapshots,
# each in its own dataset (disko.nix) so the media library's churn is never
# pinned:
#
#   tank/data/photos            the photo archive's master; the PC writes into
#                               it over sftp (/mnt/photos, hosts/pc/photos.nix)
#   tank/data/pc-documents      Syncthing copy of the PC's ~/Documents: a delete
#                               on the PC lands here within seconds
#   tank/data/backups           iPhone backup + Vaultwarden's nightly copy (one
#                               generation by itself; snapshots add history)
#   tank/data/backups/paperless nightly Paperless export (paperless.nix); the
#                               live Paperless data is on the ext4 NVMe
#
# A snapshot costs nothing until data it holds is deleted or overwritten; that
# space stays used until the last snapshot holding it is pruned (<= 12 months).
#
# Restore: browse /tank/data/<name>/.zfs/snapshot/<snapshot>/ and copy back.
# List: `zfs list -t snapshot -r tank/data`.
# The sanoid timer is shared with finance.nix (tank/finance) and kirill-es.nix
# (tank/microvms); its failures go to ntfy via finance.nix.
{ ... }:
let
  archive = {
    autosnap = true;
    autoprune = true;
    hourly = 48;
    daily = 30;
    monthly = 12;
    yearly = 0;
  };
in
{
  services.sanoid.datasets = {
    "tank/data/photos" = archive;
    "tank/data/pc-documents" = archive;
    "tank/data/backups" = archive;
    "tank/data/backups/paperless" = archive;
  };
}
