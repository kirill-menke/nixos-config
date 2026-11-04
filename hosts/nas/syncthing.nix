#
# Syncthing: the agents' shared context, kept identical on the PC and here, so
# an omp session on either box starts with the same rules, memory and profile.
# Agents write to the memory on both machines, which is why this is a two-way
# sync and not a copy.
#
#   ~/Documents/memory   memory index + topic files (AGENTS.md links it)
#   ~/Documents/profile  Kirill's personal profile (PROFILE.md and its files)
#   ~/.omp/agent         only AGENTS.md and the ADHD bits; sessions, databases,
#                        config.yml and the PC's Orca extensions stay per host
#
# The PC's side is hosts/pc/syncthing.nix. The PC dials this box over the
# tailnet (tailscale0 is a trusted interface, so 22000 needs no firewall rule);
# global discovery and relays are off, so the profile never leaves the tailnet.
#
# The device key in ~kirill/.config/syncthing (cert.pem, key.pem) was made once
# by hand with `syncthing generate` (2026-11-02), so the PC could name this
# box's ID before the first deploy. It is unmanaged state: a reinstall makes a
# new ID, which then has to replace the one in hosts/pc/syncthing.nix.
#
{ ... }:

let
  home = "/home/kirill";
  pc = "pc";
in
{
  services.syncthing = {
    enable = true;
    user = "kirill";
    group = "users";
    dataDir = home;
    configDir = "${home}/.config/syncthing";
    overrideDevices = true;
    overrideFolders = true;
    settings = {
      options = {
        globalAnnounceEnabled = false;
        relaysEnabled = false;
        natEnabled = false;
        urAccepted = -1;
      };
      devices.${pc} = {
        id = "RSMPEFV-QPLV7F6-BXKFC3N-YEQBAUL-U2LSFVJ-OYKC4TY-IS6UBF3-K5QAJQS";
        # The PC dials in; it has no fixed LAN address and its 22000 is open
        # on the LAN only.
        addresses = [ "dynamic" ];
      };
      folders = {
        memory = {
          path = "${home}/Documents/memory";
          devices = [ pc ];
          versioning = {
            type = "simple";
            params.keep = "10";
          };
        };
        profile = {
          path = "${home}/Documents/profile";
          devices = [ pc ];
          versioning = {
            type = "simple";
            params.keep = "10";
          };
        };
        omp-agent = {
          path = "${home}/.omp/agent";
          devices = [ pc ];
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
      };
    };
  };
}
