#
# Syncthing, every folder versioned (the last 10 versions of each file kept):
#   - ~/Documents/heidenhain/heidi, with the notebook
#   - the agents' shared context with the NAS: ~/Documents/memory,
#     ~/Documents/profile and ~/.omp/agent's AGENTS.md + ADHD bits; why and
#     what is left out is in hosts/nas/syncthing.nix
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
            notebook = {
              id = "VEN3IQO-TTXDSXA-6NLXJAW-7FUBOVQ-LPEXAJH-NRU6RNJ-MBN3P6R-MD77IAI";
            };
            # Dialled over the tailnet only: the NAS has global discovery and
            # relays off, and the profile should not cross anything else.
            nas = {
              id = "FYLDFKF-CV7T3VE-OWXKCG6-SRDX6FZ-MRJPX3Z-25UULJV-ZGNAFXX-RQKHZAC";
              addresses = [ "tcp://nas.taila639ad.ts.net:22000" ];
            };
          };
          folders = {
            memory = {
              path = "${config.home.homeDirectory}/Documents/memory";
              devices = [ "nas" ];
              versioning = {
                type = "simple";
                params.keep = "10";
              };
            };
            profile = {
              path = "${config.home.homeDirectory}/Documents/profile";
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
                "!/skills"
                "!/skills/**"
                "*"
              ];
              versioning = {
                type = "simple";
                params.keep = "10";
              };
            };
            "heidi" = {
              path = "${config.home.homeDirectory}/Documents/heidenhain/heidi";
              devices = [ "notebook" ];
              versioning = {
                type = "simple";
                params.keep = "10";
              };
            };
          };
        };
      };
    };
}
