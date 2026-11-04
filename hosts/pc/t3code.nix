#
# T3 Code server on this PC, so the phone can start and follow agent sessions
# here from anywhere. Same setup as hosts/nas/t3code.nix: `t3 serve` as kirill
# on loopback, published as https://pc.<tailnet>.ts.net:7443 by `tailscale
# serve` (./tailscale.nix). Clients need a pairing: `t3-pair <label>` here,
# `ssh nas t3-pair <label>` for the NAS.
#
# A system unit, so it is up whenever the PC is, also right after a
# Wake-on-LAN boot (`wake-pc` on the NAS) before anyone logs in. It suspends
# with the PC; clients show the machine offline meanwhile.
#
# It owns ~/.t3, where the desktop app (t3code-desktop) would otherwise start
# a server of its own, and the app quits when that directory is taken. So the
# desktop app runs with its local environment switched off
# (`"localEnvironmentEnabled": false` in ~/.t3/userdata/desktop-settings.json,
# Settings -> Environments) and is a client of this server like the phone:
# pair it with `t3 pair`, which prints this server's loopback link, and with
# a `t3-pair` link from the NAS.
#
# Agents see kirill's own tools first, as in his login shell: `claude` on the
# host's agentBackend (modules/home/kirill.nix), git, gh, node.
#
{ inputs, pkgs, ... }:
let
  agents = inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system};

  # The server alone: the wrapped `t3code` (the `t3` on kirill's PATH) puts
  # llm-agents' provider CLIs ahead of PATH, which would bypass his `claude`.
  t3 = agents.t3code.unwrapped;

  port = 3773; # loopback
  httpsPort = 7443; # tailnet, via tailscale serve

  serve = "${pkgs.tailscale}/bin/tailscale serve";

  # `t3-pair [label]`: a pairing link for one more device on this PC's tailnet
  # name, plus a QR code for the phone's camera. Valid for 15 minutes and one
  # device; a paired device keeps its access.
  t3Pair = pkgs.writeShellApplication {
    name = "t3-pair";
    runtimeInputs = [
      t3
      pkgs.jq
      pkgs.qrencode
      pkgs.tailscale
    ];
    text = ''
      base="https://$(tailscale status --json | jq -r '.Self.DNSName | rtrimstr(".")'):${toString httpsPort}"
      pairing=$(t3 auth pairing create --json --ttl 15m --label "''${1:-device}" --base-url "$base")
      url=$(jq -r .pairUrl <<<"$pairing")
      qrencode -t ansiutf8 "$url"
      echo "$url"
      echo "or, in Add environment: $base with code $(jq -r .credential <<<"$pairing")"
    '';
  };
in
{
  environment.systemPackages = [ t3Pair ];

  # The desktop app keeps the credentials of the servers it is paired with in
  # Electron's safeStorage, which T3 Code forces onto libsecret on Hyprland (it
  # refuses Chromium's plain-text fallback). Without a Secret Service on the
  # session bus, Add environment fails with "Could not save the local
  # connection catalog: Desktop secure storage is unavailable". PAM creates and
  # unlocks gnome-keyring's "login" keyring at the SDDM login (sddm stacks
  # `login`, which this module hooks).
  services.gnome.gnome-keyring.enable = true;

  systemd.services.t3code = {
    description = "T3 Code server";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];

    # kirill's profile ahead of the system's, as in his login shell.
    path = [
      "/run/wrappers"
      "/etc/profiles/per-user/kirill"
      "/run/current-system/sw"
    ];

    serviceConfig = {
      User = "kirill";
      WorkingDirectory = "/home/kirill";
      ExecStart = "${t3}/bin/t3 serve --host 127.0.0.1 --port ${toString port}";
      Restart = "always";
      RestartSec = 10;
    };
  };

  # As on the NAS; `off` on stop clears only this port.
  systemd.services.t3code-tailscale-serve = {
    description = "Publish T3 Code on port ${toString httpsPort} via tailscale serve";
    after = [
      "tailscaled.service"
      "t3code.service"
    ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${serve} --bg --https=${toString httpsPort} http://127.0.0.1:${toString port}";
      ExecStop = "${serve} --https=${toString httpsPort} off";
      # Fails while the node is logged out (before enrolment) or still
      # connecting after boot.
      Restart = "on-failure";
      RestartSec = 30;
    };
  };
}
