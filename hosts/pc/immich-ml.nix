#
# Immich's machine learning (faces, smart search, OCR) for the NAS's Immich
# (hosts/nas/immich.nix), which has too little RAM to run it itself. The NAS
# sends image previews to http://<this PC's tailnet IP>:3003 (sops value
# immich/ml-url in secrets/nas.yaml) and stores the results; nothing persists
# here except the downloaded models.
#
# The server has no authentication at all, so the port is open on tailscale0
# only, never on the LAN; the tailnet policy decides which devices get there.
#
# Version: must match the NAS's Immich. Both come from nixpkgs, so after a lock
# bump on either host compare `immich.version` of the two systems.
#
# CPU inference (the i5-12400 indexes a photo in well under a second); the
# 1080 Ti would need onnxruntime built with CUDA, i.e. compiling. Models
# (~1 GB) download from Hugging Face on first use into /var/cache/immich-ml and
# are unloaded after 5 idle minutes, so the PC only pays RAM while the NAS
# sends work. Nice'd, so a big first import doesn't make the desktop stutter.
#
{ lib, pkgs, ... }:
let
  port = 3003;
in
{
  networking.firewall.interfaces.tailscale0.allowedTCPPorts = [ port ];

  systemd.services.immich-machine-learning = {
    description = "Immich machine learning for the NAS";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    environment = {
      IMMICH_HOST = "0.0.0.0";
      IMMICH_PORT = toString port;
      MACHINE_LEARNING_WORKERS = "1";
      MACHINE_LEARNING_WORKER_TIMEOUT = "120";
      MACHINE_LEARNING_MODEL_TTL = "300";
      MACHINE_LEARNING_CACHE_FOLDER = "/var/cache/immich-ml";
      XDG_CACHE_HOME = "/var/cache/immich-ml";
    };
    serviceConfig = {
      ExecStart = lib.getExe pkgs.immich.machine-learning;
      DynamicUser = true;
      CacheDirectory = "immich-ml";
      Restart = "on-failure";
      RestartSec = 3;
      Nice = 10;
      CPUWeight = 50;
      MemoryMax = "4G";

      # The nixpkgs module's hardening for the same service.
      CapabilityBoundingSet = "";
      NoNewPrivileges = true;
      PrivateUsers = true;
      PrivateTmp = true;
      PrivateDevices = true;
      PrivateMounts = true;
      ProtectClock = true;
      ProtectControlGroups = true;
      ProtectHome = true;
      ProtectHostname = true;
      ProtectKernelLogs = true;
      ProtectKernelModules = true;
      ProtectKernelTunables = true;
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
        "AF_UNIX"
      ];
      RestrictNamespaces = true;
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      UMask = "0077";
    };
  };
}
