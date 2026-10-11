#
# Guest NixOS configuration for one kirill.es microVM (prod or dev).
#
# Not a module of the NAS itself: kirill-es.nix calls this function once per VM and hands the result to
# microvm.vms.<name>.config. Everything here describes the inside of the VM,
# plus the few microvm.* knobs that say how the host should run it.
#
# Inside the guest:
#
#   cloudflared  --(http://127.0.0.1:3000)-->  kirill-es (node, prerendered pages + access log)
#        |
#        +-- outbound QUIC/HTTP2 :7844 to CF edge
#
# Nothing listens on the guest's network interface. All traffic is
# guest-initiated and leaves through the host's NAT (see kirill-es.nix).
#
# Storage, as the guest sees it:
#
#   /nix/store          erofs image of exactly this guest's closure (read-only, built by the host)
#   /                   tmpfs, gone on every restart
#   /var                ext4 volume var.img, persistent: DB, access log, journal
#   /run/host-secrets   tiny read-only ext4 image the host renders from sops at each VM start
#
{
  name, # VM name, e.g. "kirill-es"; also the guest hostname
  app, # the kirill.es flake input (provides nixosModules.default)
  origin, # "https://kirill.es"
  net, # { tap, mac, hostAddr, guestAddr, prefixLength }
  vsockCid,
  vcpu,
  mem, # MiB
  secretsLabel, # ext4 label of the secrets image; must match kirill-es.nix
}:
{ lib, pkgs, ... }:
{
  imports = [ app.nixosModules.default ];

  #############################################################################
  # Hypervisor and virtual hardware
  #############################################################################

  microvm = {
    # cloud-hypervisor: Rust VMM, small virtio-only device model, runs under its
    # own seccomp filter (the runner passes `--seccomp true`), and shuts guests
    # down cleanly over its API socket (ACPI power button). See README for the
    # comparison with qemu and firecracker.
    hypervisor = "cloud-hypervisor";
    inherit vcpu mem;

    # Gives the host unit Type=notify (it only counts as started once the
    # guest's systemd reports ready) and enables `microvm -s` debug SSH if
    # that is ever switched on. Must be unique per VM on this host.
    vsock.cid = vsockCid;

    interfaces = [
      {
        type = "tap";
        id = net.tap; # host-side interface name, <= 15 chars
        inherit (net) mac;
      }
    ];

    # No microvm.shares at all, so storeOnDisk defaults to true: the guest
    # boots from an erofs image holding only its own closure, instead of
    # seeing the host's whole /nix/store over virtiofs. Also: no virtiofsd
    # (a root-run host daemon serving the guest) and the guest RAM stays
    # KSM-mergeable (cloud-hypervisor must use shared memory with virtiofs).
    shares = [ ];

    volumes = [
      {
        # Relative path, so it lives in /var/lib/microvms/<name>/var.img.
        # Created sparse + mkfs'd by microvm@<name> on first start.
        image = "var.img";
        mountPoint = "/var";
        size = 4096; # MiB, sparse; DB is ~26 MB today
        label = "var";
      }
      {
        # Rendered by microvm-secrets-<name>.service on the host (tmpfs, 0400
        # microvm:kvm) right before the VM starts. autoCreate = false: the
        # runner must never mkfs this path. No `label` here, because microvm.nix
        # only applies labels to volumes it creates and warns otherwise. The
        # mount below finds it by the label the host gave it.
        image = "/run/microvm-secrets/${name}/secrets.img";
        mountPoint = "/run/host-secrets";
        size = 4;
        autoCreate = false;
        readOnly = true;
      }
    ];
  };

  fileSystems."/run/host-secrets" = {
    device = lib.mkForce "/dev/disk/by-label/${secretsLabel}";
    noCheck = true; # read-only device, nothing for fsck to do
    options = [
      "ro"
      "nosuid"
      "nodev"
      "noexec"
    ];
  };

  #############################################################################
  # Network: static, IPv4 only, public resolvers only
  #############################################################################

  # Point-to-point /30 on the tap. The host is the gateway and NATs out of
  # the LAN NIC. Everything private (LAN, NAS, tailnet, other VMs) is
  # dropped on the host (kirill-es.nix). The guest's own firewall is a second
  # fence, not the boundary.
  networking.useNetworkd = true;
  networking.useDHCP = false;
  systemd.network.networks."10-uplink" = {
    matchConfig.MACAddress = net.mac;
    address = [ "${net.guestAddr}/${toString net.prefixLength}" ];
    routes = [ { Gateway = net.hostAddr; } ];
    networkConfig = {
      # No IPv6 at all: the host drops guest IPv6, and link-local would be
      # the one thing able to reach the host's IPv6 listeners.
      LinkLocalAddressing = "no";
      IPv6AcceptRA = false;
    };
  };
  # Public resolvers, not AdGuard on the NAS: the guest must not talk to
  # any NAS service, DNS included. The host only lets DNS through to these.
  networking.nameservers = [
    "1.1.1.1"
    "9.9.9.9"
  ];
  services.resolved.enable = false;

  networking.firewall = {
    enable = true;
    allowedTCPPorts = [ ];
    allowedUDPPorts = [ ];
  };

  #############################################################################
  # The app
  #############################################################################

  services.kirill-es = {
    enable = true;
    host = "127.0.0.1"; # only cloudflared, in this same guest, connects
    port = 3000;
    inherit origin;
    # Every request arrives through the tunnel and the app port is loopback-
    # only, so this header is always the one Cloudflare set.
    addressHeader = "CF-Connecting-IP";
  };
  systemd.services.kirill-es.unitConfig.RequiresMountsFor = [ "/var/lib" ];

  #############################################################################
  # Cloudflare Tunnel (remotely managed: ingress lives in the CF dashboard)
  #############################################################################

  # Not services.cloudflared: in this nixpkgs it only does locally-managed
  # tunnels (credentials JSON + ingress in Nix), and the token is what was
  # decided. cloudflared 2026.9.1 reads the token from a file (--token-file),
  # so it never shows up in argv or the unit's environment.
  systemd.services.cloudflared = {
    description = "Cloudflare Tunnel for ${origin}";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "kirill-es.service"
    ];
    wants = [ "network-online.target" ];
    unitConfig = {
      RequiresMountsFor = [ "/run/host-secrets" ];
      # Boots unattended after power loss, possibly long before the WAN is
      # back (same reasoning as claude-code.nix): never give up.
      StartLimitIntervalSec = 0;
    };
    serviceConfig = {
      ExecStart = lib.concatStringsSep " " [
        "${lib.getExe pkgs.cloudflared} tunnel"
        "--no-autoupdate"
        # Default would try localhost:20241-5 and, "when running in a
        # virtual environment", all interfaces. Pin it to loopback.
        "--metrics 127.0.0.1:20241"
        # The guest has no IPv6 route.
        "--edge-ip-version 4"
        "run --token-file %d/tunnel-token"
      ];
      LoadCredential = [ "tunnel-token:/run/host-secrets/tunnel-token" ];
      Restart = "always";
      RestartSec = 5;

      DynamicUser = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      PrivateDevices = true;
      PrivateUsers = true;
      ProtectHostname = true;
      ProtectClock = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectKernelLogs = true;
      ProtectControlGroups = true;
      ProtectProc = "invisible";
      ProcSubset = "pid";
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
        "AF_UNIX"
        "AF_NETLINK"
      ];
      RestrictNamespaces = true;
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      SystemCallArchitectures = "native";
      SystemCallFilter = [
        "@system-service"
        "~@privileged"
        "~@resources"
      ];
      # A remotely-managed tunnel takes its ingress rules from the Cloudflare
      # dashboard, i.e. from whoever holds that account. This keeps a
      # dashboard-side mistake (or takeover) from turning the tunnel into a
      # proxy into private address space. Loopback (the app) stays reachable.
      IPAddressDeny = [
        "10.0.0.0/8"
        "172.16.0.0/12"
        "192.168.0.0/16"
        "100.64.0.0/10"
        "169.254.0.0/16"
        "fc00::/7"
        "fe80::/10"
      ];
      MemoryMax = "256M";
    };
  };

  #############################################################################
  # Ops: logs, access
  #############################################################################

  # Guest logs also go to the serial console, which cloud-hypervisor writes
  # to the host unit's stdout, so `journalctl -u microvm@<name>` on the NAS
  # shows them without logging into the guest. The guest's own journal is
  # persistent on the /var volume and capped.
  services.journald.settings.Journal = {
    SystemMaxUse = "200M";
    ForwardToConsole = true;
    MaxLevelConsole = "info";
  };

  # The app appends to <cwd>/logs/access.log forever. It reopens the file on
  # every request (writeFileSync flag 'a'), so plain rename rotation works.
  services.logrotate.settings.kirill-es-access = {
    files = [ "/var/lib/private/kirill-es/logs/access.log" ];
    frequency = "weekly";
    rotate = 8;
    compress = true;
    missingok = true;
    notifempty = true;
  };


  # No sshd, no passwords, no login. Debug from the host via
  # `journalctl -u microvm@${name}`. For a shell, temporarily set
  # microvm.vsock.ssh.enable = true plus an authorized key, then use
  # `microvm -s ${name}` (vsock, never the network).
  services.openssh.enable = false;
  users.mutableUsers = false;
  users.allowNoPasswordLogin = true;

  # Nothing in the guest builds or installs anything: no nix-daemon, no Nix
  # DB registration of the closure, a smaller image.
  nix.enable = false;
  documentation.enable = false;

  # First NixOS release this guest is installed with; never bump casually.
  system.stateVersion = "26.11";
}
