#
# Claude Code Remote Control -- phone-driven agent sessions on this box.
#
# Each project below gets one `claude remote-control` server: an outbound-only
# HTTPS connection to Anthropic that surfaces the session on claude.ai/code
# and in the Claude mobile app, bound to the claude.ai account that logged in
# on this machine. Nothing listens on the network -- no firewall port, no
# tailnet dependency -- which is the point: phone control with zero exposed
# surface, independent of SSH and Tailscale.
#
# Deliberately full trust: bypassPermissions lets the agent edit and run
# anything as `kirill` with no per-action prompts. That makes the claude.ai
# account an admin credential for this box -- treat it like the SSH key
# (strong password + MFA). Claude refuses this mode as root, hence `kirill`.
#
# One server process is bound to one directory, so it's one unit per project;
# from the app each server can still spawn several concurrent sessions in
# that directory on demand (inside a git repo, `--spawn worktree` would give
# each its own worktree). Conversations are plain files under
# ~kirill/.claude/projects and resumable locally forever; only *remote*
# re-attachment of a stopped server expires, after ~4h
# (`claude remote-control --continue` within that window).
#
# One-time provisioning, as kirill (not root), before first start:
#
#   ssh kirill@nas
#   claude          # then /login, pick the claude.ai account option
#
# Credentials land under ~kirill/.claude; the units below reuse them. Until
# then the units crash-loop harmlessly (30s backoff).
#
{ lib, pkgs, ... }:
let
  # One entry per project; each becomes /home/kirill/projects/<name> and a
  # claude-remote-<name>.service. Add a name here, rebuild, and a new session
  # appears on claude.ai/code.
  projects = [
    "auto-apply"
    "kirill.es"
    "leetx-api"
    "polymarket"
    "vibe-reel"
  ];

  projectDir = p: "/home/kirill/projects/${p}";
in
{
  # This host otherwise allows no unfree packages; scope the exception to
  # exactly this one instead of flipping allowUnfree globally.
  nixpkgs.config.allowUnfreePredicate =
    pkg: builtins.elem (lib.getName pkg) [ "claude-code" ];

  environment.systemPackages = [ pkgs.claude-code ];

  systemd.tmpfiles.rules =
    [ "d /home/kirill/projects 0755 kirill users -" ]
    ++ map (p: "d ${projectDir p} 0755 kirill users -") projects;

  systemd.services = lib.listToAttrs (
    map (p: {
      name = "claude-remote-${p}";
      value = {
        description = "Claude Code Remote Control (${p})";
        wantedBy = [ "multi-user.target" ];
        after = [ "network-online.target" ];
        wants = [ "network-online.target" ];

        path = [ pkgs.git ];

        serviceConfig = {
          User = "kirill";
          WorkingDirectory = projectDir p;
          Environment = "HOME=/home/kirill";
          ExecStart =
            "${pkgs.claude-code}/bin/claude remote-control"
            + " --permission-mode bypassPermissions"
            + " --name nas:${p}";

          # The server exits after ~10 minutes without network. Always come
          # back and never give up: this box boots unattended after power
          # loss, possibly long before the WAN does.
          Restart = "always";
          RestartSec = 30;
        };
        unitConfig.StartLimitIntervalSec = 0;
      };
    }) projects
  );
}
