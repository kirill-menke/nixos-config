#
# Orca server -- the Orca counterpart of ./t3code.nix: a headless `orca serve`
# runtime on this box, so the PC's Orca desktop app and the Orca phone app can
# start and follow agent sessions here from anywhere on the tailnet.
#
# The server owns the projects, worktrees, terminals and agent sessions (in
# ~kirill/.config/orca); every paired client is a live view of that state, as
# with T3. Tailnet only: Orca 1.4.248 has no bind flag and listens on 0.0.0.0,
# but this host's firewall only trusts tailscale0, so the LAN can't reach the
# port. Clients dial ws://nas.<tailnet>.ts.net:6768; Orca encrypts that link
# itself (the pairing code carries the server's public key), so no `tailscale
# serve` in front.
#
# Electron, not orcad: Orca's own Node server refuses the Nix-patched files
# ("orcad slot ... checksum mismatch") and serve falls back to the Electron
# app, which needs an X display for its browser panes, hence xvfb-run. About
# 660 MB idle (measured on the PC, 2026-11-07).
#
# Terminals outlive the server: with linger on, Orca's terminal daemon moves
# into kirill's user manager (`systemd-run --user --scope`), so a restart of
# orca-serve (a pairing, a rebuild) keeps every running agent. The memory caps
# therefore sit on both cgroups: the server's own unit, and user@1000, where
# the terminals and their agents run. Nothing else uses kirill's user manager
# here (SSH logins live in session scopes, Syncthing and T3 are system units).
#
# Pairing: `orca-pair [label]` for the desktop app or a browser, `orca-pair
# --mobile [label]` for the phone (QR code). There is no command that makes a
# pairing offer on a running server, so the script restarts the server briefly
# with pairing on; see orcaPair. A grant stays valid until revoked; revoke one
# by deleting its entry from ~/.config/orca/orca-devices.json while orca-serve
# is stopped (no CLI for that in 1.4.248).
#
# Every start also writes ~/.local/bin/orca and ~/.local/bin/orca-ide, Orca's
# own launchers for its CLI (first on kirill's PATH here). Harmless on this
# box: there is no GNOME Orca screen reader to shadow.
#
# Updates: `update llm-agents` on the PC, then rebuild-nas. Clients and server
# must speak the same protocol version, so keep this in step with the PC's
# Orca (modules/home/kirill.nix uses the same llm-agents package).
#
{ inputs, pkgs, ... }:
let
  agents = inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system};
  orca = agents.orca;

  port = 6768;

  # xvfb-run -a picks a free display, so a pairing run never collides with
  # anything else on the box.
  serveCmd = "${pkgs.xvfb-run}/bin/xvfb-run -a ${orca}/bin/orca serve --port ${toString port}";

  # `orca-pair [--mobile] [label]`: stops orca-serve, runs the same server once
  # with pairing on until it prints its offer, stops it again and starts
  # orca-serve. Each run mints exactly one grant. Terminals keep running (see
  # above); connected clients reconnect by themselves.
  orcaPair = pkgs.writeShellApplication {
    name = "orca-pair";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.jq
      pkgs.qrencode
      pkgs.tailscale
      pkgs.util-linux
    ];
    text = ''
      mobile=()
      if [ "''${1:-}" = --mobile ]; then
        mobile=(--mobile-pairing)
        shift
      fi
      label=''${1:-}
      addr=$(tailscale status --json | jq -r '.Self.DNSName | rtrimstr(".")')

      out=$(mktemp)
      trap 'rm -f "$out"; sudo systemctl start orca-serve.service' EXIT
      sudo systemctl stop orca-serve.service

      setsid ${serveCmd} --pairing-address "$addr" "''${mobile[@]}" --json >"$out" 2>&1 &
      pid=$!
      ready=
      for _ in $(seq 90); do
        ready=$(grep -m1 '^{"type":"orca_server_ready"' "$out" || true)
        [ -n "$ready" ] && break
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -TERM -- "-$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true

      if [ -z "$ready" ]; then
        echo "orca serve printed no pairing offer; its output:" >&2
        cat "$out" >&2
        exit 1
      fi
      url=$(jq -r '.pairing.url // empty' <<<"$ready")
      if [ -z "$url" ]; then
        echo "no pairing offer: $(jq -c .pairing <<<"$ready")" >&2
        exit 1
      fi

      if [ ''${#mobile[@]} -gt 0 ]; then
        qrencode -t ansiutf8 "$url"
        echo "Orca app: Pair, scan the code or paste the link below."
      else
        echo "Orca desktop: Settings > Remote Orca Servers > Add Server, paste the link below."
      fi
      [ -n "$label" ] && echo "(for: $label; the grant is named by Orca, not by this label)"
      echo "$url"
    '';
  };
in
{
  environment.systemPackages = [
    orca
    orcaPair
  ];

  # Keeps the terminal daemon (and so the agents) alive across server restarts.
  users.users.kirill.linger = true;

  systemd.services.orca-serve = {
    description = "Orca runtime server";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];

    # Agents in Orca's terminals start from kirill's login shell; these are what
    # the server itself runs (agent detection, git for worktrees, ssh to push).
    path = [
      agents.claude-code
      agents.omp
      pkgs.git
      pkgs.openssh
    ];

    # Orca's own crash budget, as on vibe: 5 starts in 5 minutes.
    startLimitBurst = 5;
    startLimitIntervalSec = 300;

    serviceConfig = {
      User = "kirill";
      WorkingDirectory = "/home/kirill";
      ExecStart = "${serveCmd} --no-pairing";
      Restart = "on-failure";
      RestartSec = 5;
      # Exit 3: another Orca already serves this profile (an `orca-pair` run
      # that has not stopped yet). Retrying can't help.
      RestartPreventExitStatus = 3;

      MemoryHigh = "1500M";
      MemoryMax = "2G";
    };
  };

  # The terminals' cgroup (see above): Orca agents get the budget T3's have.
  systemd.services."user@1000" = {
    overrideStrategy = "asDropin";
    serviceConfig = {
      MemoryHigh = "2G";
      MemoryMax = "3G";
    };
  };
}
