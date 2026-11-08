#
# Memos: the store behind the SUPER+N notes app (dotfiles/hypr/scripts/notes.py)
# and, through its own web page, the phone's way to the same notes. Each note
# is one memo; the app's Work tab is the memos tagged #work, Personal the rest.
#
# Reached over the tailnet only: Memos listens on loopback and `tailscale
# serve` publishes it as https://nas.<tailnet>.ts.net:11443.
#
# One account, kirill (admin, made 2026-11-08 through the API; sign-ups are
# switched off since). Its password is sops memos/password (secrets/nas.yaml),
# for the web page. The PC's notes app uses a personal access token instead,
# kept in ~/.config/notes-modal/memos.json on the PC.
#
# Data: SQLite in /var/lib/memos. backup.nix copies a consistent snapshot of it
# off-site every night.
#
{ config, pkgs, ... }:
let
  port = 5230; # loopback, Memos' default
  # Tailnet, via tailscale serve. Port list: gatus.nix.
  httpsPort = 11443;
  serve = "${pkgs.tailscale}/bin/tailscale serve";
in
{
  services.memos = {
    enable = true;
    # The whole environment, so the tailnet name stays out of this public
    # repo (same sops value as reel-api.nix). Memos trusts browser requests
    # from MEMOS_INSTANCE_URL besides its own host.
    environmentFile = config.sops.templates."memos.env".path;
  };

  sops.templates."memos.env" = {
    content = ''
      MEMOS_MODE=prod
      MEMOS_ADDR=127.0.0.1
      MEMOS_PORT=${toString port}
      MEMOS_DATA=${config.services.memos.dataDir}
      MEMOS_DRIVER=sqlite
      MEMOS_INSTANCE_URL=https://${
        config.sops.placeholder."reel-api/tailnet-host"
      }:${toString httpsPort}
    '';
    restartUnits = [ "memos.service" ];
  };

  systemd.services = {
    # The module sets no restart policy.
    memos.serviceConfig.Restart = "on-failure";

    # Same pattern as radicale.nix; `off` on stop clears only this port.
    memos-tailscale-serve = {
      description = "Publish Memos on port ${toString httpsPort} via tailscale serve";
      after = [
        "tailscaled.service"
        "memos.service"
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
