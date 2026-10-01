#
# Kirifin (finance app) -- DEMO deploy with synthetic data.
#
# NOT FOR COMMIT as is: `hostname` contains the tailnet name. Before committing,
# publish via the Tailscale Service svc:ledger and replace the hostname (see kirifin
# docs/runbooks/first-deploy.md). Secrets come from sops (secrets/finance.yaml).
#
{ config, inputs, pkgs, ... }:
{
  imports = [
    inputs.kirifin.nixosModules.default
    inputs.sops-nix.nixosModules.sops
  ];

  # Secrets (ADR 0006, 0019, 0024): encrypted in secrets/finance.yaml, decrypted to
  # /run/secrets (tmpfs, root-only) and handed to the services via LoadCredential.
  sops = {
    defaultSopsFile = ../../secrets/finance.yaml;
    age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
    secrets = {
      "finance/users".restartUnits = [ "finance.service" ];
      "finance/wrapping-key".restartUnits = [ "finance.service" ];
      "finance/ntfy-token".restartUnits = [ "finance.service" ];
      "finance/ntfy-host-token" = { };
      "finance/fints-product-id".restartUnits = [ "finance.service" ];
    };
  };

  services.finance = {
    enable = true;
    hostname = "nas.taila639ad.ts.net";
    dataDir = "/var/lib/finance"; # mountpoint of tank/finance (encrypted, copies=2)
    zfsDataset = "tank/finance";
    usersFile = config.sops.secrets."finance/users".path;
    wrappingKeyFile = config.sops.secrets."finance/wrapping-key".path;
    sanoid.enable = true;
    ntfy.url = "unix:/run/ntfy-sh/ntfy.sock";
    ntfy.tokenFile = config.sops.secrets."finance/ntfy-token".path;
    hostAlerts.enable = true;
    hostAlerts.tokenFile = config.sops.secrets."finance/ntfy-host-token".path;
    fints.productIdFile = config.sops.secrets."finance/fints-product-id".path;
  };
  services.zfs.autoScrub = {
    enable = true;
    interval = "monthly";
  };

  # ntfy (ADR 0004, ADR 0017 §6): local only, on a unix socket; the phone reaches it via
  # tailscale serve on port 8443 of this node (demo; later svc:notify). Upstream ntfy.sh only
  # relays "new message" pokes for iOS push (metadata, no message text).
  services.ntfy-sh = {
    enable = true;
    settings = {
      base-url = "https://${config.services.finance.hostname}:8443";
      listen-http = "";
      listen-unix = "/run/ntfy-sh/ntfy.sock";
      listen-unix-mode = 432; # 0660: group ntfy-sh (finance) may publish
      behind-proxy = true;
      auth-file = "/var/lib/ntfy-sh/user.db";
      auth-default-access = "deny-all";
      upstream-base-url = "https://ntfy.sh";
    };
  };
  # The ntfy-sh unit uses DynamicUser, whose group no static user can join. A static
  # ntfy-sh user/group makes systemd use it instead, so finance can be in that group.
  users.users.ntfy-sh = {
    isSystemUser = true;
    group = "ntfy-sh";
  };
  users.groups.ntfy-sh = { };
  users.users.finance.extraGroups = [ "ntfy-sh" ];

  systemd.services.ntfy-tailscale-serve = {
    description = "Publish ntfy on the NAS tailnet name, port 8443, via tailscale serve";
    after = [ "tailscaled.service" "ntfy-sh.service" ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.tailscale}/bin/tailscale serve --bg --https=8443 unix:/run/ntfy-sh/ntfy.sock";
      ExecStop = "${pkgs.tailscale}/bin/tailscale serve --https=8443 off";
    };
  };

  # Demo access without Tailscale Services: serve the app on this node's own
  # MagicDNS name (ADR 0017 risk R9 fallback). Adds the identity headers.
  systemd.services.finance-tailscale-serve = {
    description = "Publish Kirifin on the NAS tailnet name via tailscale serve";
    after = [ "tailscaled.service" "finance.service" ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.tailscale}/bin/tailscale serve --bg --https=443 unix:/run/finance/api.sock";
      ExecStop = "${pkgs.tailscale}/bin/tailscale serve --https=443 off";
    };
  };
}
