#
# Outlook (kirill.menke@outlook.de) for claude.ai, read-only.
#
# The module, its two packages and the setup/operations guide live in
# ./outlook-mcp (see README.md there). This file only says how this NAS runs it:
#
#   claude.ai --HTTPS--> Tailscale Funnel :10000 --> mcp-auth-proxy 127.0.0.1:8471
#                        (public internet)           GitHub login, kirill-menke only
#                                                      | stdio
#                                                    ms-365-mcp-server --> Graph (Mail.Read)
#
# Port 10000 because Funnel only offers 443, 8443 and 10000, and the other two
# already carry tailnet-only serve handlers (443 = finance, 8443 = ntfy).
# Funnel is switched on per port, so sharing one would publish that service as
# well. Funnel also needs the `funnel` node attribute, which the tailnet policy
# grants to this box only (tailscale-policy.hujson).
#
# One-time after the first deploy: `sudo outlook-mcp-login` on the NAS
# (device-code sign-in as the account below; the unit stays inactive until then).
#
{ config, ... }:
let
  cfg = config.services.outlook-mcp;
in
{
  imports = [ ./outlook-mcp ];

  services.outlook-mcp = {
    enable = true;
    microsoftAccount = "kirill.menke@outlook.de";
    # https://nas.<tailnet>.ts.net:10000 names the tailnet, which this public
    # repo never holds in plain text, so EXTERNAL_URL arrives through the sops
    # template below instead.
    externalUrl = null;
    funnel.port = 10000;
    github.allowedUsers = [ "kirill-menke" ];
    environmentFile = config.sops.templates."outlook-mcp.env".path;
  };

  sops = {
    # GitHub App "Outlook MCP (kirill-menke NAS)", created from a manifest
    # (github.com/settings/apps/outlook-mcp-kirill-menke-nas): no permissions,
    # installed nowhere, only identifies the login. Callback:
    # https://nas.<tailnet>.ts.net:10000/.auth/github/callback.
    secrets."outlook-mcp/github-client-id".sopsFile = ../../secrets/nas.yaml;
    secrets."outlook-mcp/github-client-secret".sopsFile = ../../secrets/nas.yaml;

    # Root-only, as sops-nix renders templates by default: systemd reads the file
    # before dropping to the service user. The tailnet host is the same sops value
    # reel-api.nix and paperless.nix use.
    templates."outlook-mcp.env" = {
      content = ''
        EXTERNAL_URL=https://${config.sops.placeholder."reel-api/tailnet-host"}:${toString cfg.funnel.port}
        GITHUB_CLIENT_ID=${config.sops.placeholder."outlook-mcp/github-client-id"}
        GITHUB_CLIENT_SECRET=${config.sops.placeholder."outlook-mcp/github-client-secret"}
      '';
      restartUnits = [ "outlook-mcp.service" ];
    };
  };
}
