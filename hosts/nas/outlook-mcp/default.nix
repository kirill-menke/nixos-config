# Outlook (personal Microsoft account) as a remote MCP server for claude.ai.
#
#   claude.ai ──HTTPS :443──▶ Tailscale Funnel on the gate's own tailnet node
#                                 │ (second tailscaled, userspace networking)
#                                 ▼
#                           mcp-auth-proxy (OAuth 2.1, GitHub login)
#                                 │ stdio
#                                 ▼
#                           ms-365-mcp-server ──▶ Microsoft Graph
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

  # Effective port of externalUrl; claude.ai's backend only connects to 443.
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

  # The server as the service user with the service's environment, tools,
  # read-only mode and account pin (outlook-mcp-login and outlook-mcp-stdio).
  runServer = "runuser -u outlook-mcp -- env ${
    lib.escapeShellArgs (lib.mapAttrsToList (n: v: "${n}=${v}") serverEnv)
  } ${lib.escapeShellArgs serverCommand}";

  # The same server on stdin/stdout, for MCP clients that reach this host over
  # SSH instead of through the public gate: `ssh <host> sudo outlook-mcp-stdio`.
  # It shares the token cache with the service, which upstream keeps coherent
  # by re-reading it from disk before every access (buildDiskCoherencyCachePlugin).
  stdioTool = pkgs.writeShellApplication {
    name = "outlook-mcp-stdio";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.util-linux
    ];
    text = ''
      if [ "$(id -u)" -ne 0 ]; then
        echo "Run as root: sudo outlook-mcp-stdio" >&2
        exit 1
      fi
      exec ${runServer}
    '';
  };

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
        echo "Run as root: sudo outlook-mcp-login [login|status${lib.optionalString cfg.funnel.enable "|tailscale"}]" >&2
        exit 1
      fi

      run_server() {
        ${runServer} "$@"
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
        ${lib.optionalString cfg.funnel.enable ''
          tailscale)
            echo "Log the node in as yourself at the URL below; it becomes ${cfg.funnel.hostname}.<tailnet>.ts.net."
            ${tailscale} up --hostname=${lib.escapeShellArg cfg.funnel.hostname}
            systemctl restart outlook-mcp-funnel.service
            ${tailscale} funnel status
            ;;
        ''}
        *)
          echo "usage: outlook-mcp-login [login|status${lib.optionalString cfg.funnel.enable "|tailscale"}]" >&2
          exit 2
          ;;
      esac
    '';
  };

  # The `tailscale` CLI bound to the gate's own node (see funnel.enable).
  tsSocket = "/run/outlook-mcp-tailscale/tailscaled.sock";
  tailscaleCli = pkgs.writeShellApplication {
    name = "outlook-mcp-tailscale";
    text = ''exec ${lib.getExe config.services.tailscale.package} --socket=${tsSocket} "$@"'';
  };
  tailscale = lib.getExe tailscaleCli;
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
      example = "https://outlook-mcp.tail1234.ts.net";
      description = ''
        Public base URL claude.ai reaches the proxy at, without a trailing slash.
        It must be on port 443, because claude.ai's backend connects to no other
        port; with Funnel that is https://<funnel.hostname>.<tailnet>.ts.net. The
        connector URL is this plus /mcp. Set to null to pass it as EXTERNAL_URL=...
        in `environmentFile` instead, e.g. to keep the tailnet name out of a public
        repository; the port check is then skipped.
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
        description = ''
          Publish the proxy on port 443 through Tailscale Funnel from a tailnet node
          of its own: a second tailscaled in userspace-networking mode with separate
          state. claude.ai only connects to port 443, and Funnel is switched on per
          host and port, so on the host's own node it would also publish whatever
          else that node serves on 443. Log the node in once with
          `sudo outlook-mcp-login tailscale`.
        '';
      };

      hostname = mkOption {
        type = types.str;
        default = "outlook-mcp";
        description = "Device name of that node; its public address is https://<hostname>.<tailnet>.ts.net.";
      };

      wireguardPort = mkOption {
        type = types.port;
        default = 41642;
        description = "UDP port of the node's WireGuard endpoint, opened in the firewall (the host's own tailscaled usually has 41641).";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.externalUrl != null -> !(lib.hasSuffix "/" cfg.externalUrl);
        message = "services.outlook-mcp.externalUrl must not end with a slash.";
      }
      {
        assertion = cfg.externalUrl != null -> externalPort == 443;
        message = "services.outlook-mcp.externalUrl must use port 443: claude.ai's backend connects to no other port.";
      }
    ];

    users.users.outlook-mcp = {
      isSystemUser = true;
      group = "outlook-mcp";
      home = stateDir;
    };
    users.groups.outlook-mcp = { };

    environment.systemPackages = [
      loginTool
      stdioTool
    ]
    ++ lib.optional cfg.funnel.enable tailscaleCli;

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

    systemd.services.outlook-mcp-tailscaled = lib.mkIf cfg.funnel.enable {
      description = "Tailscale node of the Outlook MCP server";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      serviceConfig = {
        # Userspace networking: no TUN device and no routes or firewall rules on
        # the host, so it cannot disturb the host's own tailscaled. Like any
        # userspace node it relays tailnet connections for any port to this
        # host's loopback (README, "Security notes"); Funnel only reaches :443.
        ExecStart = utils.escapeSystemdExecArgs [
          "${config.services.tailscale.package}/bin/tailscaled"
          "--tun=userspace-networking"
          "--statedir=/var/lib/outlook-mcp-tailscale"
          "--socket=${tsSocket}"
          "--port=${toString cfg.funnel.wireguardPort}"
        ];
        StateDirectory = "outlook-mcp-tailscale";
        StateDirectoryMode = "0700";
        RuntimeDirectory = "outlook-mcp-tailscale";
        Restart = "on-failure";
        RestartSec = 5;

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
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        # Userspace networking needs no capabilities; netlink is for its
        # interface monitor.
        CapabilityBoundingSet = "";
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
          "AF_NETLINK"
        ];
        SystemCallArchitectures = "native";
      };
    };

    systemd.services.outlook-mcp-funnel = lib.mkIf cfg.funnel.enable {
      description = "Tailscale Funnel on :443 of the Outlook MCP node";
      wantedBy = [ "multi-user.target" ];
      requires = [ "outlook-mcp-tailscaled.service" ];
      after = [ "outlook-mcp-tailscaled.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${tailscale} funnel --bg --yes --https=443 http://127.0.0.1:${toString cfg.port}";
        ExecStop = "${tailscale} funnel --https=443 off";
        # Fails until the node is logged in (`sudo outlook-mcp-login tailscale`)
        # and while tailscaled is still connecting after boot.
        Restart = "on-failure";
        RestartSec = 15;
        TimeoutStartSec = 90;
      };
    };

    networking.firewall.allowedUDPPorts = lib.mkIf cfg.funnel.enable [ cfg.funnel.wireguardPort ];

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
