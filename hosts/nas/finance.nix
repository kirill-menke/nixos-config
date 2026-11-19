#
# Kirifin (finance app) -- DEMO deploy with synthetic data.
#
# Secrets come from sops (secrets/finance.yaml). So does the app's hostname: a MagicDNS
# name contains the tailnet name (<node>.<tailnet>.ts.net), which this public repo must
# never hold in plain text. That stays true after the move to the Tailscale Service
# svc:ledger (kirifin docs/runbooks/first-deploy.md): ledger.<tailnet>.ts.net names the
# tailnet too, so only the sops value changes then. The Nix store only ever sees a
# placeholder; both services get the real name from an EnvironmentFile rendered by sops.
#
{ config, inputs, pkgs, ... }:
let
  # Overridden at runtime by FINANCE_HOSTNAME / NTFY_BASE_URL (env beats the config
  # file in both apps). `.invalid` never resolves, so a missing override fails closed.
  placeholderHost = "hostname-from-sops.invalid";
  hostname = config.sops.placeholder."finance/hostname";
in
{
  imports = [
    inputs.kirifin.nixosModules.default
  ];

  # Secrets (ADR 0006, 0019, 0024): encrypted in secrets/finance.yaml, decrypted to
  # /run/secrets (tmpfs, root-only) and handed to the services via LoadCredential.
  sops = {
    defaultSopsFile = ../../secrets/finance.yaml;
    secrets = {
      "finance/users".restartUnits = [ "finance.service" ];
      "finance/wrapping-key".restartUnits = [ "finance.service" ];
      "finance/ntfy-token".restartUnits = [ "finance.service" ];
      "finance/ntfy-host-token" = { };
      "finance/fints-product-id".restartUnits = [ "finance.service" ];
      # This node's MagicDNS name, bare (nas.<tailnet>.ts.net). Not a secret, but personal.
      "finance/hostname" = { };
    };
    templates = {
      "finance-hostname.env" = {
        content = "FINANCE_HOSTNAME=${hostname}\n";
        restartUnits = [ "finance.service" ];
      };
      "ntfy-base-url.env" = {
        content = "NTFY_BASE_URL=https://${hostname}:8443\n";
        restartUnits = [ "ntfy-sh.service" ];
      };
    };
  };

  services.finance = {
    enable = true;
    hostname = placeholderHost; # real value: FINANCE_HOSTNAME, see below
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
  # Read by PID 1 before the sandbox applies; no "-" prefix, so a missing file stops the
  # start instead of running with the placeholder.
  systemd.services.finance.serviceConfig.EnvironmentFile =
    config.sops.templates."finance-hostname.env".path;
  services.zfs.autoScrub = {
    enable = true;
    interval = "monthly";
  };

  # ntfy (ADR 0004, ADR 0017 §6): local only, on a unix socket; the phone reaches it via
  # tailscale serve on port 8443 of this node (demo; later svc:notify). Upstream ntfy.sh only
  # relays "new message" pokes for iOS push (metadata, no message text).
  services.ntfy-sh = {
    enable = true;
    # iOS push hashes base-url into the poll topic, so it must be the exact URL the phone uses.
    environmentFile = config.sops.templates."ntfy-base-url.env".path;
    settings = {
      base-url = "https://${placeholderHost}:8443"; # real value: NTFY_BASE_URL
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
