#
# auto-apply: the job-search viewer and its application tracker, as a
# home-screen app on the phone.
#
# The viewer itself is not this file's: `serve.py` runs from kirill's checkout
# (~/projects/auto-apply) as the user unit auto-apply-serve.service, which the
# repo's systemd/install.sh --serve installs, and answers plain HTTP on port
# 8000 — reachable over the tailnet only, the firewall drops it on the LAN.
#
# This publishes it as https://nas.<tailnet>.ts.net:12443 through `tailscale
# serve`: the same page under TLS, which is what the phone's home-screen app
# is installed from (Safari, Share, Add to Home Screen; it installs as
# "Jobs"). Tailnet only, like every port here: the viewer's API has no login,
# and its Apply button spends on the Claude login.
#
# serve.py refuses a Host that is not a name of its own (DNS rebinding); the
# tailnet name is one through `[serve] allow_hosts` in the checkout's
# config.local.toml, so the name stays out of this public repo.
#
{ pkgs, ... }:
let
  port = 8000; # serve.py, every interface
  # Tailnet, via tailscale serve. Port list: gatus.nix.
  httpsPort = 12443;
  serve = "${pkgs.tailscale}/bin/tailscale serve";
in
{
  # Same pattern as radicale.nix; `off` on stop clears only this port. No
  # ordering on the viewer: it is a user unit, and the proxy answers 502
  # until it is up.
  systemd.services.auto-apply-tailscale-serve = {
    description = "Publish the auto-apply viewer on port ${toString httpsPort} via tailscale serve";
    after = [ "tailscaled.service" ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # Concurrent serve changes fail with "etag mismatch"; see gatus.nix.
      Restart = "on-failure";
      RestartSec = 3;
      ExecStart = "${serve} --bg --https=${toString httpsPort} http://127.0.0.1:${toString port}";
      ExecStop = "${serve} --https=${toString httpsPort} off";
    };
  };
}
