#
# Fetch every git repo under one directory every 10 minutes, so `git status`
# in any clone says when the other machine pushed.
#
# Agents run on the PC or the NAS (Orca, T3 Code), and each machine keeps its
# own clones; work moves between them only through git (push/pull). Syncing
# the clones themselves (Syncthing) would break them: git rewrites .git/index,
# refs and lock files in place, so two machines committing within a minute
# leave conflict copies inside .git, and Orca worktrees hold absolute paths
# that differ per machine (~/Documents/projects on the PC, ~/projects on the
# NAS).
#
# A fetch only moves remote-tracking refs (origin/*): no working tree, local
# branch or index is touched, so it is safe while an agent works in the repo.
#
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.gitFetch;

  gitFetchRepos = pkgs.writeShellApplication {
    name = "git-fetch-repos";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.findutils
      pkgs.git
      pkgs.openssh
    ];
    text = ''
      # No terminal here: fail instead of waiting for a password or host key.
      export GIT_TERMINAL_PROMPT=0
      export GIT_SSH_COMMAND="ssh -o BatchMode=yes"

      failed=0
      while IFS= read -r -d "" gitdir; do
        repo=''${gitdir%/.git}
        if ! timeout 120 git -C "$repo" fetch --all --prune --quiet; then
          echo "fetch failed: $repo" >&2
          failed=1
        fi
      done < <(find "$1" -mindepth 2 -maxdepth 5 -name node_modules -prune -o -name .git -type d -print0)
      exit "$failed"
    '';
  };
in
{
  options.gitFetch.dir = lib.mkOption {
    type = lib.types.nullOr lib.types.str;
    default = null;
    example = "%h/projects";
    description = ''
      Directory whose git clones (up to 4 levels down, worktrees excluded)
      kirill's user manager fetches every 10 minutes. `%h` is his home.
    '';
  };

  config = lib.mkIf (cfg.dir != null) {
    systemd.user.services.git-fetch = {
      description = "Fetch every git clone under ${cfg.dir}";
      unitConfig.ConditionUser = "kirill";
      # His git config's credential helpers (`aws codecommit credential-helper`
      # for the CodeCommit clones on the PC) must resolve as in his shell; a
      # NixOS unit's PATH holds only coreutils and the like.
      environment.PATH = lib.mkForce "/etc/profiles/per-user/%u/bin:/run/current-system/sw/bin";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${lib.getExe gitFetchRepos} ${cfg.dir}";
        Nice = 19;
        IOSchedulingClass = "idle";
      };
    };

    systemd.user.timers.git-fetch = {
      description = "Fetch every git clone under ${cfg.dir} every 10 minutes";
      wantedBy = [ "timers.target" ];
      unitConfig.ConditionUser = "kirill";
      timerConfig = {
        OnCalendar = "*:0/10";
        # Right after boot or resume too, not only at the next tick.
        Persistent = true;
        RandomizedDelaySec = 60;
      };
    };
  };
}
