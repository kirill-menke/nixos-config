#
# kirill.es on the NAS: two cloud-hypervisor microVMs (prod + dev), each
# running the site plus its own cloudflared. Publicly reachable through
# Cloudflare Tunnel only; nothing on this host or the router is opened.
#
# Guest config: kirill-es-guest.nix. Flake inputs: microvm, kirill-es,
# kirill-es-dev (flake.nix).
#
# What this file owns, host side:
#
#   * microvm.vms.<name>   fully declarative guests (config in this repo, the
#                          app package + service module from the kirill-es input)
#   * tap addressing       one routed /30 per VM, no bridge, so guests share no
#                          Ethernet segment with the LAN or each other
#   * microvm-netguard     nftables table: guests may reach the public
#                          internet on a few ports, and nothing else (no LAN, no
#                          NAS services, no tailnet, no other VM). NAT to the uplink.
#   * microvm-secrets-*    renders each VM's sops secrets into a read-only disk
#                          image on tmpfs, attached as a second volume
#   * tank/microvms        ZFS dataset holding the VM volumes, snapshotted by sanoid
#
# Why a separate nft table and not networking.nftables / networking.nat:
# this host's firewall is the iptables (nf_tables backend) one, and
# vpn-confinement drives iptables directly. networking.nftables.enable would
# switch the firewall backend, and its default `flush ruleset` would wipe
# those iptables-nft tables on every reload. A table of our own coexists:
# packets traverse every table's base chains, and a drop in any of them is
# final. So the drops below hold whatever the iptables side accepts.
#
{
  config,
  inputs,
  lib,
  pkgs,
  ...
}:
let
  # The NIC with the default route (static 192.168.0.175 in adguard.nix).
  wan = "enp2s0";

  # One entry per VM. Addresses: 10.88.<n>.0/30, host .1, guest .2. Nothing
  # else on this box uses 10.88/16 (vpn-confinement: 192.168.15.0/24, its
  # WireGuard: 10.2.0.0/16 inside the netns, tailnet: 100.64/10).
  # Taps must be <= 15 chars and start with "vm-" (the nft rules match vm-*).
  vms = {
    kirill-es = {
      enable = true;
      app = inputs.kirill-es;
      origin = "https://kirill.es";
      net = {
        tap = "vm-kirill-es";
        mac = "02:4b:45:00:01:02";
        hostAddr = "10.88.1.1";
        guestAddr = "10.88.1.2";
        prefixLength = 30;
      };
      vsockCid = 881;
      vcpu = 2;
      # The NAS has 7.5 GiB and runs Jellyfin + the *arr stack: keep guests small.
      mem = 512;
    };

    # Separate VM rather than a second service in the prod guest: a guest
    # update means a guest reboot, so sharing one VM would bounce prod on
    # every dev deploy. Separate VMs also keep dev's unreviewed code away from
    # prod's DB and keys, and dev can be switched off with `enable = false`.
    kirill-es-dev = {
      enable = true;
      app = inputs.kirill-es-dev;
      origin = "https://dev.kirill.es";
      net = {
        tap = "vm-kirill-dev";
        mac = "02:4b:45:00:02:02";
        hostAddr = "10.88.2.1";
        guestAddr = "10.88.2.2";
        prefixLength = 30;
      };
      vsockCid = 882;
      vcpu = 1;
      mem = 384;
    };
  };

  enabled = lib.filterAttrs (_: vm: vm.enable) vms;
  names = lib.attrNames enabled;

  # ext4 label of every secrets image (one per VM, each VM only sees its own).
  secretsLabel = "host-secrets";

  ip = lib.getExe' pkgs.iproute2 "ip";
  sysctl = lib.getExe' pkgs.procps "sysctl";
  nft = lib.getExe pkgs.nftables;

  #############################################################################
  # nftables: isolation + NAT for every vm-* tap
  #############################################################################

  netguardRules = pkgs.writeText "microvm-netguard.nft" ''
    # Idempotent replace: create (no-op if present), delete, re-create, all
    # in one atomic transaction.
    table inet microvm_guard
    delete table inet microvm_guard

    table inet microvm_guard {
      # Destinations a guest may never reach, even on an allowed port:
      # this-network, RFC 1918 (LAN, vpn netns, other VMs), CGNAT/tailnet,
      # loopback, link-local, IETF/DS-Lite (192.0.0.0/24), benchmarking,
      # multicast, reserved.
      set private4 {
        type ipv4_addr
        flags interval
        elements = {
          0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8,
          169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.168.0.0/16,
          198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4
        }
      }

      # Resolvers the guests are configured with (kirill-es-guest.nix).
      set resolvers4 {
        type ipv4_addr
        elements = { 1.1.1.1, 1.0.0.1, 9.9.9.9, 149.112.112.112 }
      }

      # Guests never talk to the host itself: no SSH, no AdGuard DNS, no
      # Jellyfin/reel-api/NFS, nothing the NixOS firewall opens "on all
      # interfaces". Priority before the iptables-nft filter chains. A drop
      # is final whichever table it comes from.
      chain input {
        type filter hook input priority filter - 10; policy accept;
        iifname "vm-*" counter drop
      }

      chain forward {
        type filter hook forward priority filter - 10; policy accept;

        # Into a guest: only replies to connections the guest opened. No
        # inbound service exists. cloudflared dials out.
        oifname "vm-*" ct state established,related accept
        oifname "vm-*" counter drop

        iifname "vm-*" jump from_guest
      }

      chain from_guest {
        ct state established,related accept
        ct state invalid drop

        # Anti-spoofing: each tap may only source its own guest address.
        ${lib.concatMapStringsSep "\n    " (
          vm: ''iifname "${vm.net.tap}" ip saddr != ${vm.net.guestAddr} drop''
        ) (lib.attrValues enabled)}

        # Guests are IPv4-only.
        meta nfproto ipv6 drop

        # Host port-forwards (vpn-confinement DNATs :8080 on every host
        # address into the qBittorrent netns) are not for guests. The forward
        # hook sees post-DNAT addresses, so @private4 would catch it too.
        # This states the intent.
        ct status dnat drop

        # Out of the uplink only: not tailscale0, not the proton veth/bridge.
        oifname != "${wan}" drop
        ip daddr @private4 drop

        # What the guests actually need:
        #   tcp 443        Cloudflare API (cloudflared)
        #   tcp/udp 7844   Cloudflare Tunnel edge (QUIC, falls back to HTTP/2)
        #   udp 123        NTP (systemd-timesyncd)
        #   53             DNS, to the configured public resolvers only
        #   icmp echo      ping, for debugging
        tcp dport { 443, 7844 } accept
        udp dport { 123, 7844 } accept
        ip daddr @resolvers4 meta l4proto { tcp, udp } th dport 53 accept
        icmp type echo-request accept

        counter drop
      }

      chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        iifname "vm-*" oifname "${wan}" masquerade
      }
    }
  '';
in
{
  imports = [
    inputs.microvm.nixosModules.host
    # Already imported by finance.nix. nixosModules.sops is a path, so the
    # module system de-duplicates it, and this file does not depend on finance.nix.
    inputs.sops-nix.nixosModules.sops
  ];

  #############################################################################
  # The VMs
  #############################################################################

  microvm.vms = lib.mapAttrs (name: vm: {
    # Fully declarative: built with the host, restarted by switch-to-
    # configuration when its closure changes (restartIfChanged defaults to
    # true here). pkgs defaults to the host's nixpkgs.
    config = import ./kirill-es-guest.nix {
      inherit name secretsLabel;
      inherit (vm)
        app
        origin
        net
        vsockCid
        vcpu
        mem
        ;
    };
  }) enabled;

  # VM state (volume images, API sockets, runner links) on ZFS rather than the
  # ext4 root, so sanoid snapshots cover the guests' /var (access logs,
  # journal). A snapshot of a running VM's image is crash-consistent.
  #
  # One-time, before the first rebuild:   zfs create -o mountpoint=legacy tank/microvms
  #
  # nofail: this host has no BMC; a missing dataset must not drop boot into
  # emergency mode. The microvm units then fail instead, which is visible
  # and harmless.
  #
  # nofail also drops the mount's ordering before local-fs.target, so order it
  # before tmpfiles explicitly: tmpfiles creates /var/lib/microvms/<vm>, and
  # would otherwise create it on the root fs underneath the later mount.
  fileSystems."/var/lib/microvms" = {
    device = "tank/microvms";
    fsType = "zfs";
    options = [
      "nofail"
      "x-systemd.before=systemd-tmpfiles-setup.service"
    ];
  };
  services.sanoid = {
    enable = true;
    datasets."tank/microvms" = {
      autosnap = true;
      autoprune = true;
      hourly = 24;
      daily = 14;
      monthly = 3;
    };
  };

  #############################################################################
  # Secrets: sops -> read-only disk image per VM
  #############################################################################
  #
  # cloud-hypervisor cannot take microvm.credentialFiles (systemd credentials
  # via SMBIOS/fw_cfg are qemu-only in microvm.nix; the runner throws). A
  # virtiofs share would add a root-run virtiofsd per VM. Instead: right before
  # each VM starts, render its secrets into a small ext4 image on tmpfs and
  # attach it as a read-only virtio disk. Only that VM's secrets go in, and the
  # image is deleted when the VM stops.
  #
  # secrets/kirill-es.yaml (sops, same recipients as finance.yaml):
  #   <name>/tunnel-token   the Cloudflare Tunnel token for that VM's tunnel
  # The site itself needs no secrets.

  sops.secrets = lib.mkMerge (
    map (name: {
      "${name}/tunnel-token" = {
        sopsFile = ../../secrets/kirill-es.yaml;
        restartUnits = [ "microvm@${name}.service" ];
      };
    }) names
  );

  systemd.services = lib.mkMerge (
    map (
      name:
      let
        vm = enabled.${name};
        dir = "/run/microvm-secrets/${name}";
      in
      {
        "microvm-secrets-${name}" = {
          description = "Render secrets image for MicroVM '${name}'";
          # Stopped/restarted together with the VM, so a VM restart re-renders.
          partOf = [ "microvm@${name}.service" ];
          path = [
            pkgs.coreutils
            pkgs.e2fsprogs
          ];
          serviceConfig = {
            Type = "oneshot";
            RemainAfterExit = true;
            UMask = "0077";
            ExecStop = "${lib.getExe' pkgs.coreutils "rm"} -rf ${dir}";
          };
          script = ''
            set -euo pipefail
            install -d -m 0750 -o root -g kvm ${dir}
            stage=$(mktemp -d)
            trap 'rm -rf "$stage"' EXIT
            install -m 0400 ${config.sops.secrets."${name}/tunnel-token".path} "$stage/tunnel-token"
            rm -f ${dir}/secrets.img.tmp
            # -d populates the fs from $stage (files stay root:root 0400
            # inside the guest). 4 MiB, no journal: it is never written.
            mkfs.ext4 -q -F -L ${secretsLabel} -O ^has_journal \
              -E root_owner=0:0 -d "$stage" ${dir}/secrets.img.tmp 4M
            chown microvm:kvm ${dir}/secrets.img.tmp
            chmod 0400 ${dir}/secrets.img.tmp
            mv -f ${dir}/secrets.img.tmp ${dir}/secrets.img
          '';
        };

        # Never install or run a VM on the root fs if tank/microvms is missing.
        "install-microvm-${name}".unitConfig.RequiresMountsFor = "/var/lib/microvms";

        # Merges into the drop-in the microvm host module already writes for
        # this instance (overrideStrategy = "asDropin").
        "microvm@${name}" = {
          unitConfig.RequiresMountsFor = "/var/lib/microvms";
          requires = [
            "microvm-netguard.service"
            "microvm-secrets-${name}.service"
          ];
          after = [
            "microvm-netguard.service"
            "microvm-secrets-${name}.service"
          ];
        };

        # Host end of the routed /30, added once tap-up has created the tap.
        # IPv6 off on the tap, so the host has no link-local address the
        # guest could reach. (The nft input drop covers it anyway.)
        "microvm-tap-interfaces@${name}".serviceConfig.ExecStartPost = [
          "${ip} addr replace ${vm.net.hostAddr}/${toString vm.net.prefixLength} dev ${vm.net.tap}"
          "${sysctl} -q -w net.ipv6.conf.${vm.net.tap}.disable_ipv6=1"
        ];
      }
    ) names
    ++ [
      {
        microvm-netguard = {
          description = "nftables isolation and NAT for MicroVM taps";
          wantedBy = [ "multi-user.target" ];
          before = [ "microvms.target" ];
          # Rule changes are applied atomically in place. A restart would
          # bounce every VM, because they Require this unit.
          reloadIfChanged = true;
          serviceConfig = {
            Type = "oneshot";
            RemainAfterExit = true;
            ExecStart = "${nft} -f ${netguardRules}";
            ExecReload = "${nft} -f ${netguardRules}";
            # Stopping the guard stops the VMs (Requires=), so isolation
            # never silently goes away under a running guest.
            ExecStop = "${nft} delete table inet microvm_guard";
          };
        };
      }
    ]
  );

  # Already 1 on this host (vpn-confinement/tailscale). Stated here because the
  # NAT above depends on it.
  boot.kernel.sysctl."net.ipv4.ip_forward" = lib.mkDefault 1;

  # Fail evaluation early on the mistakes that would be silent at runtime.
  assertions =
    let
      vmList = lib.attrValues enabled;
      unique = xs: lib.length xs == lib.length (lib.unique xs);
    in
    [
      {
        assertion = lib.all (vm: lib.hasPrefix "vm-" vm.net.tap) vmList;
        message = "kirill-es.nix: tap names must start with vm- (nft rules match vm-*).";
      }
      {
        assertion = unique (map (vm: vm.net.tap) vmList) && unique (map (vm: vm.vsockCid) vmList);
        message = "kirill-es.nix: tap names and vsock CIDs must be unique.";
      }
    ]
    # Supply-chain fence. The guest imports the app repo's NixOS module, but
    # microvm.* options configure the HOST side: tap-up/pci-setup/virtiofsd
    # scripts run as root, preStart and extraArgs as the `microvm` user (which
    # can read every VM's images and secrets). So no file from the app input
    # may define any microvm.* option. A bad commit in kirill.es can then only
    # affect what runs inside its own VM.
    ++ lib.mapAttrsToList (
      name: vm:
      let
        appRoot = toString vm.app.outPath;
        offending = lib.filter (opt: lib.any (file: lib.hasPrefix appRoot (toString file)) opt.files) (
          lib.collect lib.isOption config.microvm.vms.${name}.config.options.microvm
        );
      in
      {
        assertion = offending == [ ];
        message = "kirill-es.nix: the kirill.es flake defines host-side MicroVM options for '${name}': ${
          lib.concatMapStringsSep ", " (opt: lib.showOption opt.loc) offending
        }";
      }
    ) enabled;
}
