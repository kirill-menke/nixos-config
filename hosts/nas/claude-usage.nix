#
# Claude usage on the start page: how much of the Claude plan's weekly limit
# is used, and which machine and project spent it.
#
# The plan (Max, kirill's login) has one weekly limit for everything that
# runs on it: the agents on the PC, and this box's unattended jobs
# (auto-apply's listings, profile-nightly, Paperless's titles). When the PC's
# sessions use it up, those jobs fail until the reset, so the start page
# shows where it stands.
#
# claude-usage.service (every 30 minutes, as kirill) asks `omp usage` for the
# limit and adds up the costs the clients logged since the week began
# (claude-usage.py says where they come from), into
# /var/lib/claude-usage/usage.json. nginx serves that one file on loopback:
#   glance.nix   the "Claude usage" card
#   gatus.nix    the checks "Weekly limit under 80 percent", "Code login
#                valid" and "Code login lasts over 3 days", whose alerts go
#                to the phone like every other one
#
# The same run notes how long Claude Code's own login still holds (`claude
# auth status`): it ends after a fixed time, and auto-apply's and Paperless's
# `claude -p` calls then fail until someone logs in again.
#
# The PC's share arrives as pc.json in the same directory: this box cannot
# reach the PC, so a user timer there (~/.local/share/systemd/user/
# claude-usage-push.timer, hand-made, with a copy of claude-usage.py as
# ~/.local/bin/claude-usage) pipes `claude-usage scan` over ssh every 30
# minutes while the PC is on. A PC that is off spends nothing, so its last
# file stays right.
#
#   systemctl start claude-usage; journalctl -u claude-usage    two lines a run
#
{
  config,
  inputs,
  pkgs,
  ...
}:
let
  agents = inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system};
  stateDir = "/var/lib/claude-usage";

  collector = pkgs.writers.writePython3Bin "claude-usage" { } (builtins.readFile ./claude-usage.py);
in
{
  # kirill's, so the PC's push (ssh as kirill) can write pc.json.
  systemd.tmpfiles.rules = [ "d ${stateDir} 0755 kirill users -" ];

  systemd.services.claude-usage = {
    description = "Claude usage for the start page: weekly limit and who spent it";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    environment = {
      OMP_BIN = "${agents.omp}/bin/omp";
      CLAUDE_BIN = "${agents.claude-code}/bin/claude";
      # The card's clock times.
      TZ = "Europe/Berlin";
    };
    serviceConfig = {
      Type = "oneshot";
      # His omp login and the session logs under his home.
      User = "kirill";
      ExecStart = "${collector}/bin/claude-usage collect ${stateDir}";
      WorkingDirectory = stateDir;
      Nice = 10;
      MemoryMax = "800M";
      TimeoutStartSec = "5min";
    };
  };

  systemd.timers.claude-usage = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*:0/30";
      Persistent = true;
    };
  };

  services.nginx.virtualHosts.claude-usage = {
    listen = [
      {
        addr = "127.0.0.1";
        port = config.nas.claudeUsagePort;
      }
    ];
    root = stateDir;
    locations."= /usage.json".extraConfig = ''
      add_header Cache-Control "no-store";
    '';
    # pc.json and the collector's cache stay private.
    locations."/".return = "404";
  };
}
