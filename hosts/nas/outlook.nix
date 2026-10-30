#
# Outlook (kirill.menke@outlook.de) for claude.ai, read-only.
#
# The module, its two packages and the setup/operations guide live in
# ./outlook-mcp (see README.md there). This file only says how this NAS runs it:
#
#   claude.ai --HTTPS :443--> Tailscale Funnel on the tailnet node "outlook-mcp"
#     --> mcp-auth-proxy 127.0.0.1:8471 (GitHub login, kirill-menke only)
#           | stdio
#         ms-365-mcp-server --> Graph (Mail.Read)
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
