#
# Disk layout for the NAS, consumed by disko (and by nixos-anywhere at install
# time, which runs disko before installing).
#
# Disks are addressed by /dev/disk/by-id/ deliberately. Kernel names like
# /dev/sda are assigned in probe order: the 8TB was "sda" only because the
# installer USB stick happened to take "sdb", and this box has drives hanging
# off two different SATA controllers (Intel SoC 00:17.0 and ASMedia 04:00.0),
# so the ordering is not stable across boots or as bays get populated.
#
{ ... }:
{
  disko.devices = {
    disk = {
      # --- OS disk: WD_BLACK SN7100 1TB, M.2 slot ---------------------------
      # Note this slot is electrically PCIe 3.0 x1, so the drive negotiates
      # 8GT/s x1 (~985 MB/s) rather than its rated 16GT/s x4. Irrelevant for a
      # root filesystem; noted so it isn't mistaken for a fault later.
      os = {
        type = "disk";
        device = "/dev/disk/by-id/nvme-WD_BLACK_SN7100_1TB_26073T802326";
        content = {
          type = "gpt";
          partitions = {
            ESP = {
              size = "1G";
              type = "EF00";
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/boot";
                # Kernels and initrds live here; keep them unreadable to non-root.
                mountOptions = [ "umask=0077" ];
              };
            };
            root = {
              size = "100%";
              content = {
                type = "filesystem";
                format = "ext4";
                mountpoint = "/";
              };
            };
          };
        };
      };

      # --- Data disk: Seagate IronWolf 8TB, bay 1 ---------------------------
      data0 = {
        type = "disk";
        device = "/dev/disk/by-id/ata-ST8000VN002-2ZM188_ZPV0WCWX";
        content = {
          type = "gpt";
          partitions = {
            zfs = {
              # Deliberately NOT "100%". Stopping 2 GiB short of the end means a
              # future mirror partner that is a few thousand sectors smaller (a
              # different production run, or a different vendor's "8 TB") can
              # still be attached. `zpool attach` refuses a device even
              # marginally smaller than the existing one, and a vdev cannot be
              # shrunk after the fact -- so this slack is unrecoverable if
              # omitted now. 2 GiB out of 8 TB costs 0.025%.
              end = "-2G";
              content = {
                type = "zfs";
                pool = "tank";
              };
            };
          };
        };
      };

      # --- Data disk: Seagate IronWolf 8TB (used), mirror partner -----------
      # Mirror partner of data0, attached by hand on 2026-11-17 with
      # `zpool attach` after wiping the labels of its previous pool. Its
      # partition was created with the exact sector geometry of data0's
      # (start 2048, 15623856128 sectors), which is what this entry yields too.
      data1 = {
        type = "disk";
        device = "/dev/disk/by-id/ata-ST8000VN0022-2EL112_ZA1EL343";
        content = {
          type = "gpt";
          partitions = {
            zfs = {
              # Same 2 GiB slack as data0: see there.
              end = "-2G";
              content = {
                type = "zfs";
                pool = "tank";
              };
            };
          };
        };
      };
    };

    zpool = {
      tank = {
        type = "zpool";

        # Two-way mirror of data0 + data1. The pool started as a single-disk
        # vdev (mode = "") and became a mirror in place on 2026-11-17:
        #
        #   zpool attach -o ashift=12 tank wwn-0x5000c500fd4f360b-part1 \
        #     /dev/disk/by-id/wwn-0x5000c500b4b02211-part1
        #
        # To replace or add a mirror member, use `zpool replace` / `zpool
        # attach`. Do NOT use `zpool add` -- that appends a second top-level
        # vdev, producing a stripe with no redundancy across vdevs, which is
        # the single most common way people destroy their own redundancy plan.
        mode = "mirror";

        options = {
          # IMMUTABLE once the vdev exists. 4 KiB sectors. Autodetection can
          # pick ashift=9 from a 512e drive, which then permanently misaligns
          # any 4Kn drive attached later.
          ashift = "12";
          # Rely on the pool being listed in NixOS config rather than on a
          # cachefile baked into the initrd; avoids stale-cache import failures.
          cachefile = "none";
        };

        rootFsOptions = {
          compression = "zstd";
          atime = "off";
          xattr = "sa";
          acltype = "posixacl";
          # The pool root is a container only; real data lives in datasets
          # below, so nothing should mount at /tank itself.
          mountpoint = "none";
          "com.sun:auto-snapshot" = "false";
        };

        datasets = {
          data = {
            type = "zfs_fs";
            mountpoint = "/tank/data";
            options = {
              # "legacy" hands mounting to systemd via the generated
              # fileSystems entry, instead of ZFS mounting it itself. Keeps
              # mount ordering under NixOS's control.
              mountpoint = "legacy";
              "com.sun:auto-snapshot" = "true";
            };
          };
          # Own datasets so they can be snapshotted without the media library
          # (snapshots.nix). Created by hand on 2026-11-08 on the existing pool
          # (`zfs create -o mountpoint=legacy tank/data/<name>`), data moved in.
          "data/photos" = {
            type = "zfs_fs";
            mountpoint = "/tank/data/photos";
            options.mountpoint = "legacy";
          };
          "data/pc-documents" = {
            type = "zfs_fs";
            mountpoint = "/tank/data/pc-documents";
            options.mountpoint = "legacy";
          };
          # Everything that is a copy of something else, in one place. Made
          # 2026-11-08: `zfs create` for backups, `zfs rename` of the former
          # data/paperless-export, iphone-15 and vaultwarden copied in.
          "data/backups" = {
            type = "zfs_fs";
            mountpoint = "/tank/data/backups";
            options.mountpoint = "legacy";
          };
          "data/backups/paperless" = {
            type = "zfs_fs";
            mountpoint = "/tank/data/backups/paperless";
            options.mountpoint = "legacy";
          };
        };
      };
    };
  };
}
