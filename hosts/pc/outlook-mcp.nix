#
# Outlook for omp on this PC: one SSH tunnel to the NAS's shared Outlook MCP
# server (hosts/nas/outlook.nix, `shared`), instead of one ssh connection and
# one ~100 MB server on the NAS per session.
#
# Clients run `outlook-mcp-client`, which connects to the tunnel's socket
# /run/outlook-mcp/mcp.sock and speaks MCP over stdio: the omp extension
# ~/.omp/agent/extensions/outlook-bridge.ts (T3 Code sessions),
# ~/.omp/agent/mcp.json (terminal sessions) and the vault fill tool's mail.py.
# The socket is kirill's alone (directory 0700, socket 0600), so other users
# on this PC can't read the mailbox through it. On the NAS end, kirill is in
# the group allowed onto the shared server's socket.
#
# The tunnel reconnects every 10 s while the NAS is unreachable; meanwhile
# clients fail at once with "No such file or directory" or "Connection
# refused" and retry on their next call.
#
{ pkgs, ... }:
let
  socket = "/run/outlook-mcp/mcp.sock";
  remoteSocket = "/run/outlook-mcp-mux/mcp.sock";

  client = pkgs.writeShellApplication {
    name = "outlook-mcp-client";
    text = "exec ${pkgs.socat}/bin/socat STDIO UNIX-CONNECT:${socket}";
  };
in
{
  environment.systemPackages = [ client ];

  systemd.services.outlook-mcp-tunnel = {
    description = "SSH tunnel to the NAS's shared Outlook MCP server";
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" ];
    after = [ "network-online.target" ];
    # Keep retrying however long the NAS is away.
    startLimitIntervalSec = 0;
    serviceConfig = {
      User = "kirill";
      RuntimeDirectory = "outlook-mcp";
      RuntimeDirectoryMode = "0700";
      # The same key and pinned host key (programs.ssh.knownHosts.nas in
      # paperless.nix) as the Paperless mount; no ~/.ssh/config involved.
      ExecStart = builtins.concatStringsSep " " [
        "${pkgs.openssh}/bin/ssh -N -T"
        "-i /home/kirill/.ssh/id_ed25519"
        "-o BatchMode=yes"
        "-o ExitOnForwardFailure=yes"
        "-o ServerAliveInterval=30"
        "-o ServerAliveCountMax=3"
        "-o StreamLocalBindUnlink=yes"
        "-o ControlMaster=no"
        "-o ControlPath=none"
        "-L ${socket}:${remoteSocket}"
        "kirill@192.168.0.175"
      ];
      Restart = "always";
      RestartSec = 10;

      NoNewPrivileges = true;
      PrivateTmp = true;
      ProtectSystem = "strict";
      ProtectHome = "read-only";
    };
  };
}
