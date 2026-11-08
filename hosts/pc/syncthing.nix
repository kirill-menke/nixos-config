#
# Syncthing, every folder versioned (the last 10 versions of each file kept):
#   - the agents' shared context with the NAS: ~/Documents/agents/memory,
#     ~/Documents/agents/profile and ~/.omp/agent's AGENTS.md + ADHD bits; why and
#     what is left out is in hosts/nas/syncthing.nix
#   - a one-way copy to the NAS for its off-site backup (hosts/nas/backup.nix):
#     ~/Documents (without the folders above, Heidenhain's code and build
#     output); unversioned, restic keeps the history. Photos are not here:
#     since 2026-11-08 the NAS's /tank/data/photos is their only master
#
{ config, ... }:

{
  # Syncthing's sync (22000/tcp+udp) and local discovery (21027/udp) ports, on
  # the wired LAN only, as UxPlay's. Syncthing runs under Home Manager, so
  # NixOS's services.syncthing.openDefaultPorts, which acts only together with
  # the system service, never opened them: the notebook could not connect in,
  # and the PC never heard its local discovery broadcasts.
  networking.firewall.interfaces.${config.pc.lanInterface} = {
    allowedTCPPorts = [ 22000 ];
    allowedUDPPorts = [
      22000
      21027
    ];
  };

  home-manager.users.kirill =
    { config, ... }:
    {
      # Syncthing's folder markers (services.syncthing below)
      programs.git.ignores = [
        ".stfolder/"
        ".stversions/"
      ];

      services.syncthing = {
        enable = true;
        overrideDevices = true;
        overrideFolders = true;
        settings = {
          devices = {
            # Dialled over the tailnet only: the NAS has global discovery and
            # relays off, and the profile should not cross anything else.
            nas = {
              id = "FYLDFKF-CV7T3VE-OWXKCG6-SRDX6FZ-MRJPX3Z-25UULJV-ZGNAFXX-RQKHZAC";
              addresses = [ "tcp://nas.taila639ad.ts.net:22000" ];
            };
          };
          folders = {
            memory = {
              path = "${config.home.homeDirectory}/Documents/agents/memory";
              devices = [ "nas" ];
              versioning = {
                type = "simple";
                params.keep = "10";
              };
            };
            profile = {
              path = "${config.home.homeDirectory}/Documents/agents/profile";
              devices = [ "nas" ];
              versioning = {
                type = "simple";
                params.keep = "10";
              };
            };
            omp-agent = {
              path = "${config.home.homeDirectory}/.omp/agent";
              devices = [ "nas" ];
              # Keep in step with hosts/nas/syncthing.nix.
              ignorePatterns = [
                "!/AGENTS.md"
                "!/i-have-adhd.json"
                "!/extensions/i-have-adhd-main-only.ts"
                "!/extensions/outlook-bridge.ts"
                "!/extensions/memory-keeper.ts"
                "!/skills"
                "!/skills/**"
                "*"
              ];
              versioning = {
                type = "simple";
                params.keep = "10";
              };
            };
            # Send-only: nothing the NAS does can change the PC's files.
            pc-documents = {
              path = "${config.home.homeDirectory}/Documents";
              type = "sendonly";
              devices = [ "nas" ];
              ignorePatterns = [
                # their own two-way folders above
                "/agents/memory"
                "/agents/profile"
                # Heidenhain's code (CodeCommit clones + their Orca
                # worktrees) stays off a private cloud
                "/heidenhain"
                "/projects/heidi"
                "/projects/oriana"
                "/projects/dev-platform"
                "/projects/orca/projects/dev-platform"
                "/projects/orca/workspaces/heidi"
                "/projects/orca/workspaces/oriana"
                "/projects/orca/workspaces/dev-platform"
                # archived on the NAS (+ restic), may be freed from the PC
                "/linda-stadter"
                "/school"
                "/university"
                "/finances"
                "/job-applications"
                # rebuildable
                "(?d)node_modules"
                "(?d)target"
                "(?d).venv"
                "(?d)__pycache__"
                "(?d).direnv"
                "(?d)result"
              ];
            };
          };
        };
      };
    };
}
