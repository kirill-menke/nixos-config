#
# Outlook (kirill.menke@outlook.de) for claude.ai and omp: read, write drafts and
# organize, but never send or delete.
#
# The module, its two packages and the setup/operations guide live in
# ./outlook-mcp (see README.md there). This file only says how this NAS runs it:
#
#   claude.ai --HTTPS :443--> Tailscale Funnel on the tailnet node "outlook-mcp"
#     --> mcp-auth-proxy 127.0.0.1:8471 (GitHub login, kirill-menke only)
#           | stdio
#         ms-365-mcp-server --> Graph (Mail.ReadWrite)
#
#   omp (PC and NAS) --> outlook-mcp-client --> one shared server for all
#     sessions (outlook-mcp-mux, Unix socket); the PC reaches the socket
#     through one SSH tunnel (hosts/pc/outlook-mcp.nix)
#
# claude.ai's backend only connects to port 443, and this box's own node already
# serves the tailnet-only finance API there. Funnel is switched on per host and
# port, so funneling 443 here would publish finance too. The module therefore
# runs a second tailscaled as a node of its own, https://outlook-mcp.<tailnet>.ts.net,
# and only that node has Funnel (tailscale-policy.hujson).
#
# One-time after the first deploy, on the NAS:
#   sudo outlook-mcp-login tailscale   log the node in (opens a URL, log in as yourself)
#   sudo outlook-mcp-login             Microsoft device-code sign-in as the account
#                                      below; the unit stays inactive until then
#
{ config, ... }:
{
  imports = [ ./outlook-mcp ];

  services.outlook-mcp = {
    enable = true;
    microsoftAccount = "kirill.menke@outlook.de";
    # Read, write drafts, organize. Nothing that sends mail (no Mail.Send), deletes
    # or touches rules and mailbox settings (an auto-forward rule would leak all
    # future mail). The token carries Mail.ReadWrite, which Graph would also accept
    # for deleting, so this list is the boundary: the server exposes nothing else.
    # --read-only would drop every write tool, hence off. After changing the list,
    # sign in again (`sudo outlook-mcp-login`) so the token carries its scopes.
    readOnly = false;
    tools = [
      # read
      "list-mail-messages"
      "list-mail-folders"
      "list-mail-child-folders"
      "list-mail-folder-messages"
      "get-mail-message"
      "read-mail-text"
      "list-mail-attachments"
      "get-mail-attachment-content"
      # drafts: reviewed and sent by hand in Outlook
      "create-draft-email"
      "create-reply-draft"
      "create-reply-all-draft"
      "create-forward-draft"
      "add-mail-attachment"
      # organize: read/flag/categories, move or copy (Deleted Items included), folders
      "update-mail-message"
      "move-mail-message"
      "copy-mail-message"
      "create-mail-folder"
      "create-mail-child-folder"
      "update-mail-folder"
    ];
    # Every omp session used to start its own server over SSH (~100 MB each on
    # this box; 17 at once in a busy agent run). Now they share one.
    shared = {
      enable = true;
      users = [ "kirill" ];
    };
    # https://outlook-mcp.<tailnet>.ts.net names the tailnet, which this public
    # repo never holds in plain text, so EXTERNAL_URL arrives through the sops
    # template below instead.
    externalUrl = null;
    github.allowedUsers = [ "kirill-menke" ];
    environmentFile = config.sops.templates."outlook-mcp.env".path;
  };

  sops = {
    secrets = {
      "outlook-mcp/external-url".sopsFile = ../../secrets/nas.yaml;
      # GitHub App "Outlook MCP (kirill-menke)", created from a manifest
      # (github.com/settings/apps/outlook-mcp-kirill-menke): no permissions,
      # installed nowhere, only identifies the login. Callback:
      # https://outlook-mcp.<tailnet>.ts.net/.auth/github/callback.
      "outlook-mcp/github-client-id".sopsFile = ../../secrets/nas.yaml;
      "outlook-mcp/github-client-secret".sopsFile = ../../secrets/nas.yaml;
    };

    # Root-only, as sops-nix renders templates by default: systemd reads the file
    # before dropping to the service user.
    templates."outlook-mcp.env" = {
      content = ''
        EXTERNAL_URL=${config.sops.placeholder."outlook-mcp/external-url"}
        GITHUB_CLIENT_ID=${config.sops.placeholder."outlook-mcp/github-client-id"}
        GITHUB_CLIENT_SECRET=${config.sops.placeholder."outlook-mcp/github-client-secret"}
      '';
      restartUnits = [ "outlook-mcp.service" ];
    };
  };
}
