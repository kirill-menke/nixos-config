# Outlook (personal Microsoft account) as a remote MCP server for claude.ai.
#
#   claude.ai ──HTTPS──▶ Tailscale Funnel ──▶ mcp-auth-proxy (OAuth 2.1, GitHub login)
#                                                  │ stdio
#                                                  ▼
#                                          ms-365-mcp-server ──▶ Microsoft Graph
#
# Microsoft tokens never leave this machine: claude.ai only ever holds a token
# for mcp-auth-proxy. See README.md for the one-time setup.
{
  config,
  lib,
  pkgs,
  utils,
  ...
}:

let
  cfg = config.services.outlook-mcp;
  inherit (lib) mkOption mkEnableOption types;

  stateDir = "/var/lib/outlook-mcp";
  msalDir = "${stateDir}/msal";
  logDir = "/var/log/outlook-mcp";
  tokenCache = "${msalDir}/token-cache.json";

  serverArgs = [
    "--enabled-tools"
    "^(${lib.concatStringsSep "|" cfg.tools})$"
  ]
  ++ lib.optional cfg.readOnly "--read-only"
  ++ cfg.extraServerArgs;

  serverEnv = {
    HOME = stateDir;
    # Personal accounts must use the consumers authority, otherwise refresh
    # tokens are rejected about an hour after sign-in.
    MS365_MCP_TENANT_ID = "consumers";
    # No desktop keyring on a server; the cache key lives next to the cache (0600).
    MS365_MCP_USE_KEYTAR = "0";
    MS365_MCP_TOKEN_CACHE_PATH = tokenCache;
    MS365_MCP_SELECTED_ACCOUNT_PATH = "${msalDir}/selected-account.json";
    # Refuse any account other than this one, including via the login tool.
    MS365_MCP_EXPECTED_USERNAME = cfg.microsoftAccount;
    MS365_MCP_LOG_DIR = logDir;
    MS365_MCP_MAX_TOP = toString cfg.maxResults;
    # Without this, list tools accept fetchAllPages and merge up to 100 pages.
    MS365_MCP_ALLOW_PAGINATION = "false";
    # The login check (--login, --verify-login, `outlook-mcp-login status`) calls
    # Graph /me, whose least-privileged permission is User.Read, not Mail.Read.
    # Upstream's own HTTP OAuth mode always requests it for the same reason (server.js).
    MS365_MCP_EXTRA_SCOPES = "User.Read";
  };

  # Effective port of externalUrl, to check it against funnel.port.
  urlPortMatch = builtins.match "https://[^/:]+(:([0-9]+))?" cfg.externalUrl;
  externalPort =
    if urlPortMatch == null then
      null
    else if builtins.elemAt urlPortMatch 1 == null then
      443
    else
      lib.toInt (builtins.elemAt urlPortMatch 1);

  serverCommand = [ (lib.getExe cfg.package) ] ++ serverArgs;

  proxyCommand = [
    (lib.getExe cfg.proxyPackage)
    "--listen"
    "127.0.0.1:${toString cfg.port}"
    "--no-auto-tls" # TLS is terminated by Tailscale Funnel
  ]
  # Without the flag the proxy reads EXTERNAL_URL from environmentFile; a flag
  # would take precedence over it.
  ++ lib.optionals (cfg.externalUrl != null) [
    "--external-url"
    cfg.externalUrl
  ]
  ++ [
    "--data-path"
    "${stateDir}/auth"
    "--github-allowed-users"
    (lib.concatStringsSep "," cfg.github.allowedUsers)
    "--"
    # The backend inherits the proxy's environment; keep the GitHub secret out of it.
    "${pkgs.coreutils}/bin/env"
    "-u"
    "GITHUB_CLIENT_ID"
    "-u"
    "GITHUB_CLIENT_SECRET"
    "-u"
    "ALLOWED_REDIRECT_URIS"
  ]
  ++ serverCommand;

  loginTool = pkgs.writeShellApplication {
    name = "outlook-mcp-login";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.util-linux
      config.systemd.package
    ];
    text = ''
      if [ "$(id -u)" -ne 0 ]; then
        echo "Run as root: sudo outlook-mcp-login [status]" >&2
        exit 1
      fi

      run_server() {
        runuser -u outlook-mcp -- env ${
          lib.escapeShellArgs (lib.mapAttrsToList (n: v: "${n}=${v}") serverEnv)
        } ${lib.escapeShellArgs serverCommand} "$@"
      }

      # --verify-login always exits 0 (the JSON tells); --login exits 1 on hard
      # failure (timeout, wrong account), so its result is verified separately.
      verify() {
        local result
        result="$(run_server --verify-login)"
        echo "$result"
        grep -q '"success":true' <<<"$result"
      }

      case "''${1:-login}" in
        login)
          echo "Sign in as ${cfg.microsoftAccount} when the device-code prompt appears."
          run_server --login
          if ! verify; then
            echo "Sign-in did not work; the service was left as it is." >&2
            exit 1
          fi
          systemctl restart outlook-mcp.service
          echo "Signed in; outlook-mcp.service restarted."
          ;;
        status)
          verify
          ;;
        *)
          echo "usage: outlook-mcp-login [login|status]" >&2
          exit 2
          ;;
      esac
    '';
  };

  tailscale = lib.getExe config.services.tailscale.package;
in
{
  options.services.outlook-mcp = {
    enable = mkEnableOption "the Outlook MCP server for claude.ai";

    package = mkOption {
      type = types.package;
      default = pkgs.callPackage ./pkgs/ms-365-mcp-server/package.nix { };
      defaultText = lib.literalExpression "ms-365-mcp-server (bundled)";
      description = "The ms-365-mcp-server package.";
    };

    proxyPackage = mkOption {
      type = types.package;
      default = pkgs.callPackage ./pkgs/mcp-auth-proxy/package.nix { };
      defaultText = lib.literalExpression "mcp-auth-proxy (bundled)";
      description = "The mcp-auth-proxy package.";
    };

    microsoftAccount = mkOption {
      type = types.str;
      example = "someone@outlook.com";
      description = "The personal Microsoft account to serve. Sign-ins as any other account are rejected.";
    };

    externalUrl = mkOption {
      type = types.nullOr (types.strMatching "https://[^/]+");
      example = "https://nas.tail1234.ts.net:8443";
      description = ''
        Public base URL claude.ai reaches the proxy at, without a trailing slash,
        including the Funnel port unless it is 443. The connector URL is this plus /mcp.
        Set to null to pass it as EXTERNAL_URL=... in `environmentFile` instead,
        e.g. to keep the tailnet name out of a public repository; the Funnel port
        check below is then skipped.
      '';
    };

    environmentFile = mkOption {
      type = types.path;
      example = "/run/secrets/outlook-mcp.env";
      description = ''
        EnvironmentFile for the proxy with GITHUB_CLIENT_ID=... and
        GITHUB_CLIENT_SECRET=... of the GitHub OAuth app, plus EXTERNAL_URL=...
        when `externalUrl` is null. Keep it out of the Nix store (agenix,
        sops-nix, or a root-only file).
      '';
    };

    allowedRedirectUris = mkOption {
      type = types.nonEmptyListOf types.str;
      default = [ "https://claude.ai/api/mcp/auth_callback" ];
      description = ''
        The only OAuth redirect URIs clients may register at the proxy (enforced by
        a local patch to mcp-auth-proxy). The default is the callback used by
        claude.ai web, desktop and mobile. Without this, anyone could register a
        client with their own redirect URI and phish a token with a single link.
      '';
    };

    port = mkOption {
      type = types.port;
      default = 8471;
      description = "Local port mcp-auth-proxy listens on (127.0.0.1 only).";
    };

    tools = mkOption {
      type = types.listOf types.str;
      default = [
        "list-mail-messages"
        "list-mail-folders"
        "list-mail-child-folders"
        "list-mail-folder-messages"
        "get-mail-message"
        "read-mail-text"
        "list-mail-attachments"
        "get-mail-attachment-content"
      ];
      description = ''
        Exact ms-365-mcp-server tool names to expose. The requested Microsoft
        Graph permissions follow from these (the default needs only Mail.Read).
        After changing this, sign in again so the token carries the new scopes.
      '';
    };

    readOnly = mkOption {
      type = types.bool;
      default = true;
      description = "Pass --read-only, which drops every write tool regardless of `tools`.";
    };

    maxResults = mkOption {
      type = types.ints.positive;
      default = 25;
      description = ''
        Cap on items per list call (Graph `$top`), to keep responses small.
        Multi-page fetching is disabled, so this is also the most one call returns.
      '';
    };

    extraServerArgs = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "Extra arguments for ms-365-mcp-server.";
    };

    github = {
      allowedUsers = mkOption {
        type = types.nonEmptyListOf types.str;
        example = [ "octocat" ];
        description = "GitHub usernames allowed to sign in at the proxy.";
      };
    };

    funnel = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Publish the proxy through Tailscale Funnel.";
      };

      port = mkOption {
        type = types.enum [
          443
          8443
          10000
        ];
        default = 8443;
        description = ''
          Public Funnel port. Funnel is switched on per host:port, so every other
          `tailscale serve` handler on the same port would become public too, and
          stopping this unit removes all handlers on that port. Pick a port nothing
          else serves on (check `tailscale serve status`).
        '';
      };
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.funnel.enable -> config.services.tailscale.enable;
        message = "services.outlook-mcp.funnel needs services.tailscale.enable = true.";
      }
      {
        assertion = cfg.externalUrl != null -> !(lib.hasSuffix "/" cfg.externalUrl);
        message = "services.outlook-mcp.externalUrl must not end with a slash.";
      }
      {
        assertion = cfg.funnel.enable && cfg.externalUrl != null -> externalPort == cfg.funnel.port;
        message = "services.outlook-mcp.externalUrl must use the Funnel port (${toString cfg.funnel.port}), e.g. https://nas.tail1234.ts.net${
          lib.optionalString (cfg.funnel.port != 443) ":${toString cfg.funnel.port}"
        }.";
      }
    ];

    users.users.outlook-mcp = {
      isSystemUser = true;
      group = "outlook-mcp";
      home = stateDir;
    };
    users.groups.outlook-mcp = { };

    environment.systemPackages = [ loginTool ];

    systemd.tmpfiles.settings."10-outlook-mcp" = {
      ${stateDir}.d = {
        user = "outlook-mcp";
        group = "outlook-mcp";
        mode = "0700";
      };
      ${msalDir}.d = {
        user = "outlook-mcp";
        group = "outlook-mcp";
        mode = "0700";
      };
      ${logDir}.d = {
        user = "outlook-mcp";
        group = "outlook-mcp";
        mode = "0750";
      };
    };

    systemd.services.outlook-mcp = {
      description = "Outlook MCP server behind mcp-auth-proxy";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      # Stays inactive until `sudo outlook-mcp-login` has created the token cache.
      unitConfig.ConditionPathExists = tokenCache;

      environment = serverEnv // {
        ALLOWED_REDIRECT_URIS = lib.concatStringsSep "," cfg.allowedRedirectUris;
      };

      serviceConfig = {
        ExecStart = utils.escapeSystemdExecArgs proxyCommand;
        EnvironmentFile = cfg.environmentFile;
        User = "outlook-mcp";
        Group = "outlook-mcp";
        WorkingDirectory = stateDir;
        StateDirectory = "outlook-mcp";
        StateDirectoryMode = "0700";
        LogsDirectory = "outlook-mcp";
        LogsDirectoryMode = "0750";
        Restart = "on-failure";
        RestartSec = 10;

        UMask = "0077";
        NoNewPrivileges = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        ProtectControlGroups = true;
        ProtectClock = true;
        ProtectHostname = true;
        ProtectProc = "invisible";
        ProcSubset = "pid";
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        CapabilityBoundingSet = "";
        SystemCallArchitectures = "native";
        SystemCallFilter = [
          "@system-service"
          "@pkey" # V8 uses memory protection keys on CPUs that have them
          "~@privileged"
        ];
        # Node's JIT needs writable+executable memory, so no MemoryDenyWriteExecute.
      };
    };

    systemd.services.outlook-mcp-funnel = lib.mkIf cfg.funnel.enable {
      description = "Tailscale Funnel for the Outlook MCP server";
      wantedBy = [ "multi-user.target" ];
      wants = [ "tailscaled.service" ];
      after = [
        "tailscaled.service"
        "tailscaled-autoconnect.service"
      ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${tailscale} funnel --bg --yes --https=${toString cfg.funnel.port} http://127.0.0.1:${toString cfg.port}";
        ExecStop = "${tailscale} funnel --https=${toString cfg.funnel.port} off";
        # tailscaled may still be logging in right after boot.
        Restart = "on-failure";
        RestartSec = 15;
        TimeoutStartSec = 90;
      };
    };

    # ms-365-mcp-server writes its own log files and never rotates them.
    services.logrotate.settings.outlook-mcp = {
      files = "${logDir}/*.log";
      frequency = "weekly";
      rotate = 4;
      compress = true;
      copytruncate = true;
      missingok = true;
      notifempty = true;
      su = "outlook-mcp outlook-mcp";
    };
  };
}
