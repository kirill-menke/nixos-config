#
# T3 Code -- phone-driven agent sessions on this box. One `t3 serve` for every
# project; it replaced the per-project `claude remote-control` units.
#
# The server runs the agents (Claude Code, logged in as kirill under
# ~kirill/.claude) and keeps every project and thread in ~kirill/.t3. The
# iPhone app, the PC's desktop app and browsers are only views of that state,
# so a thread started on one shows up live on all of them. Threads without a
# project are the general-purpose sessions for work on the box itself (each
# gets a folder under ~/.t3/scratch; `wake-pc` is on PATH).
#
# Reached over the tailnet only: the server listens on loopback and `tailscale
# serve` publishes it as https://nas.<tailnet>.ts.net:7443. Port 443 is
# Kirifin's (finance.nix), which is also why `t3 serve --tailscale-serve` and
# `t3 pair --tailscale` must not be used here: both default to 443. Any owner
# device on the tailnet reaches the port; using the server takes a pairing.
#
# Deliberately full trust, as with the old units: T3's default permission mode
# "Full access" is Claude's bypassPermissions, so an agent runs anything as
# kirill, who has passwordless sudo. A paired device is therefore an admin
# credential for this box; drop a lost one with `t3 auth session list` and
# `t3 auth session revoke <id>`.
#
# Updates: `update llm-agents` on the PC, then rebuild-nas. Never `t3 update`
# or upstream's install.sh: their `t3` is a glibc binary for
# /lib64/ld-linux-x86-64.so.2, which NixOS only stubs here. The phone app
# refuses a server on another orchestration protocol (2 from 0.1.0 through at
# least 0.1.6), so if the App Store app ever moves on, bump llm-agents.
#
# One-time setup, as kirill (`ssh nas`):
#
#   t3-pair iphone                      # link + QR for one more device
#   t3 project add ~/projects/<repo>    # or "Add project" in any client
#
{ inputs, pkgs, ... }:
let
  agents = inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system};

  # Claude Code from llm-agents, as on the PC and the laptop. It is unfree, but
  # llm-agents evaluates it in its own nixpkgs, so this host's nixpkgs still
  # allows no unfree packages.
  claudeCode = agents.claude-code;

  # omp, the agent used from now on; T3 runs it as the ACP provider `omp-acp`
  # (`omp acp`, set in ~/.t3/userdata/settings.json). Its rules, memory and
  # profile arrive from the PC through ./syncthing.nix; its login (agent.db)
  # is per host.
  omp = agents.omp;

  # The server alone. The wrapped `t3code` also puts Codex, Cursor, Grok and
  # OpenCode on the server's PATH and carries the Electron app as a second
  # output; only Claude is used here.
  t3 = agents.t3code.unwrapped;

  port = 3773; # loopback
  httpsPort = 7443; # tailnet, via tailscale serve

  serve = "${pkgs.tailscale}/bin/tailscale serve";

  # `t3-pair [label]`: a pairing link for one more device (the phone, a
  # browser, the PC's desktop app) on this box's tailnet name, plus a QR code
  # for the phone's camera. `t3 pair` would hand out the loopback address the
  # server listens on. Valid for 15 minutes and one device; a paired device
  # keeps its access.
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
  environment.systemPackages = [
    claudeCode
    omp
    t3
    t3Pair
  ];

  systemd.services.t3code = {
    description = "T3 Code server";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];

    # What the server itself runs: the Claude and omp CLIs, and git/ssh for its branch,
    # commit and push actions. The agents' own shell commands get kirill's
    # login-shell PATH from Claude Code.
    path = [
      claudeCode
      omp
      pkgs.git
      pkgs.openssh
    ];

    serviceConfig = {
      User = "kirill";
      WorkingDirectory = "/home/kirill";
      ExecStart = "${t3}/bin/t3 serve --host 127.0.0.1 --port ${toString port}";
      Restart = "always";
      RestartSec = 10;

      # Agents and everything they start (installs, builds, test runs) live in
      # this cgroup. Above MemoryHigh the kernel reclaims from it first; at
      # MemoryMax the OOM killer picks inside it rather than AdGuard, the LAN's
      # DNS (see zramSwap in default.nix for the 2026-10-31 squeeze).
      MemoryHigh = "2G";
      MemoryMax = "3G";
    };
  };

  # Same pattern as paperless.nix; `off` on stop clears only this port.
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
    };
  };
}
