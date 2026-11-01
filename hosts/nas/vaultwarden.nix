#
# Vaultwarden: a Bitwarden-compatible password server for the official
# Bitwarden apps (iPhone, desktop, browser extension, the `bw` and `rbw`
# CLIs). Every client keeps an encrypted copy of the vault and unlocks it
# offline, so this box being down stops sync, not access.
#
# Reached over the tailnet only: Vaultwarden listens on loopback and `tailscale
# serve` publishes it as https://nas.<tailnet>.ts.net:6443, the server URL to
# give every client ("self-hosted"). The phone needs Tailscale connected to
# sync.
#
# Accounts: registration is closed. `sudo vaultwarden-signups [minutes]` opens
# it for that long (default 30) and closes it again by itself; `sudo
# vaultwarden-signups close` ends it early. That is only needed for the first
# account: after that, an organization owner invites people from the web vault,
# and an invited address may register while registration is closed. There is
# no SMTP, so no invitation mail goes out; tell them yourself. No admin page
# (no ADMIN_TOKEN): there is no token to leak, and no admin-saved config.json
# that would silently override this file.
#
# Where things live:
#   /var/lib/vaultwarden           SQLite database, attachments, sends and the
#                                  key that signs login tokens (NVMe)
#   /tank/data/vaultwarden-backup  nightly copy at 23:00 UTC (HDD), one
#                                  generation: a second copy on another disk
# Restore: stop vaultwarden, delete db.sqlite3-wal and db.sqlite3-shm in
# /var/lib/vaultwarden (a stale WAL would be replayed onto the restored
# database), copy the backup's contents in, chown -R vaultwarden:vaultwarden,
# start it.
#
# Updates: the Bitwarden apps don't promise to work with Vaultwarden, and a new
# app release occasionally needs a newer server (bitwarden/ios#3023: the iPhone
# app 2026.8.1 couldn't read vaults from Vaultwarden 1.35.3, 1.37.2 fixed it),
# so this host's nixpkgs should not fall far behind.
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  port = 8222; # loopback
  # Tailnet, via tailscale serve. Taken there: 443 = finance, 3000 = AdGuard,
  # 7443 = T3 Code, 8443 = ntfy, 9443 = vibereel-phone, 10443 = Paperless.
  httpsPort = 6443;
  backupDir = "/tank/data/vaultwarden-backup";
  serve = "${pkgs.tailscale}/bin/tailscale serve";

  # Read after the module's environment files, so its SIGNUPS_ALLOWED wins.
  # It lives in /run: a reboot closes registration too.
  signupsEnv = "/run/vaultwarden-signups.env";
  closeSignups = pkgs.writeShellScript "vaultwarden-signups-close" ''
    if [ -e ${signupsEnv} ]; then
      ${pkgs.coreutils}/bin/rm -f ${signupsEnv}
      ${pkgs.systemd}/bin/systemctl restart vaultwarden.service
    fi
  '';
  signups = pkgs.writeShellApplication {
    name = "vaultwarden-signups";
    runtimeInputs = [
      pkgs.jq
      pkgs.systemd
      pkgs.tailscale
    ];
    text = ''
      arg=''${1:-30}
      case $arg in
        close) ;;
        "" | *[!0-9]*)
          echo "usage: vaultwarden-signups [minutes | close]" >&2
          exit 2
          ;;
      esac
      if [ "$(id -u)" -ne 0 ]; then
        echo "needs root: sudo vaultwarden-signups $arg" >&2
        exit 1
      fi

      # Drops the pending close of an earlier call.
      systemctl stop vaultwarden-signups-close.timer 2>/dev/null || true
      if [ "$arg" = close ]; then
        ${closeSignups}
        echo "Registration closed."
        exit 0
      fi

      echo SIGNUPS_ALLOWED=true >${signupsEnv}
      systemctl restart vaultwarden.service
      systemd-run --quiet --collect --unit=vaultwarden-signups-close \
        --on-active="''${arg}m" ${closeSignups}
      host=$(tailscale status --json | jq -r '.Self.DNSName | rtrimstr(".")')
      echo "Registration open for $arg minutes: https://$host:${toString httpsPort}"
    '';
  };
in
{
  environment.systemPackages = [ signups ];

  services.vaultwarden = {
    enable = true;
    inherit backupDir;
    environmentFile = config.sops.templates."vaultwarden.env".path;
    config = {
      ROCKET_ADDRESS = "127.0.0.1";
      ROCKET_PORT = port;
      SIGNUPS_ALLOWED = false;
      # tailscale serve sets it to the tailnet device's address, replacing any
      # the client sent, so logs and login rate limits see the real device.
      IP_HEADER = "X-Forwarded-For";
    };
  };

  # Links and the WebAuthn relying party are built from DOMAIN. The tailnet name
  # stays out of this public repo: same sops value as paperless.nix.
  sops.templates."vaultwarden.env" = {
    content = ''
      DOMAIN=https://${config.sops.placeholder."reel-api/tailnet-host"}:${toString httpsPort}
    '';
    restartUnits = [ "vaultwarden.service" ];
  };

  systemd.services = {
    # "-": optional, only present while vaultwarden-signups holds it open.
    vaultwarden.serviceConfig.EnvironmentFile = lib.mkAfter [ "-${signupsEnv}" ];

    # Never write the backup into the root fs's empty mountpoint when tank is
    # missing.
    backup-vaultwarden.unitConfig.RequiresMountsFor = [ backupDir ];

    # Same pattern as paperless.nix; `off` on stop clears only this port.
    vaultwarden-tailscale-serve = {
      description = "Publish Vaultwarden on port ${toString httpsPort} via tailscale serve";
      after = [
        "tailscaled.service"
        "vaultwarden.service"
      ];
      wants = [ "tailscaled.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${serve} --bg --https=${toString httpsPort} http://127.0.0.1:${toString port}";
        ExecStop = "${serve} --https=${toString httpsPort} off";
      };
    };
  };
}
