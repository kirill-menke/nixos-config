# outlook-mcp

Read-only access to a personal Outlook mailbox for claude.ai, self-hosted on NixOS.

```
claude.ai ──HTTPS :8443──▶ Tailscale Funnel ──▶ mcp-auth-proxy :8471 (127.0.0.1)
                                                  │  OAuth 2.1 gate, GitHub login
                                                  │  stdio
                                                  ▼
                                           ms-365-mcp-server ──▶ Microsoft Graph (Mail.Read)
```

- **[ms-365-mcp-server](https://github.com/Softeria/ms-365-mcp-server)** talks to Outlook through Microsoft Graph. It signs in once with a device code using Softeria's public app registration, so no Azure tenant is needed. The Microsoft tokens stay in `/var/lib/outlook-mcp/msal`.
- **[mcp-auth-proxy](https://github.com/sigbit/mcp-auth-proxy)** turns that stdio server into `https://<host>:8443/mcp` behind OAuth 2.1 with dynamic client registration. claude.ai signs in to this gate with your GitHub account and only ever holds a token for the gate, never a Microsoft token.
- **Tailscale Funnel** publishes the gate on the internet, which works behind DS-Lite/CGNAT.

## What Claude can do

These are the tools exposed by default, and all of them need only `Mail.Read`. The token also carries `User.Read`, only because the sign-in check calls Graph `/me`:

| Tool | Purpose |
| --- | --- |
| `list-mail-messages`, `list-mail-folder-messages` | Search and list messages, globally or per folder |
| `list-mail-folders`, `list-mail-child-folders` | Browse the folder tree |
| `get-mail-message`, `read-mail-text` | Read one message |
| `list-mail-attachments`, `get-mail-attachment-content` | Read attachments |

Nothing can send, move, delete or draft. `--read-only` drops write tools even if you add one to `tools` by mistake. List calls return at most `maxResults` items (25) and can't follow further pages.

The stdio mode also always exposes the server's own account tools (`login`, `logout`, `verify-login`, `list-accounts`, `select-account`, `remove-account`), and they can't be filtered out. The account pin (`microsoftAccount`) stops them from switching to a different account. The worst they can do is sign the NAS out, and you fix that by running `sudo outlook-mcp-login` again.

## Layout

```
default.nix                                  NixOS module (services.outlook-mcp)
pkgs/ms-365-mcp-server/package.nix           built from the published npm tarball (ships prebuilt dist/)
pkgs/ms-365-mcp-server/package.json          published manifest without dev/optional deps
pkgs/ms-365-mcp-server/package-lock.json
pkgs/ms-365-mcp-server/update.sh             bumps the three files above
pkgs/mcp-auth-proxy/package.nix              built from source with buildGoModule
pkgs/mcp-auth-proxy/restrict-redirect-uris.patch
                                             only lets clients register the claude.ai callback
```

## Setup

The examples below use `nas.tail1234.ts.net`. Replace it with your NAS's name.

### 1. Tailscale

In the Tailscale admin console:

1. **DNS:** turn on MagicDNS and **HTTPS Certificates**.
2. **Access controls:** the NAS needs the `funnel` node attribute. New tailnets have it for every member already:
   ```json
   "nodeAttrs": [{ "target": ["autogroup:member"], "attr": ["funnel"] }]
   ```
   If it's missing, the first `tailscale funnel` run prints a link that adds it.

On the NAS:

```sh
tailscale status --json | jq -r .Self.DNSName   # e.g. nas.tail1234.ts.net.  → drop the trailing dot
tailscale serve status                          # make sure nothing else uses :8443
```

Funnel is switched on per host and port, not per path. Any other `tailscale serve` handler on port 8443 would become public too, and stopping the funnel unit removes every handler on that port. That's why this setup uses its own port. If 8443 is taken, set `funnel.port = 10000;` and use `:10000` everywhere below.

### 2. GitHub app for the login

The gate identifies you through GitHub. Either kind of GitHub app works, because both use the same login endpoints:

- **OAuth App:** <https://github.com/settings/developers> → **OAuth Apps → New OAuth App**, then generate a client secret.
- **GitHub App** with no permissions, installed nowhere. It can also be created from a [manifest](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest), which hands back the client ID and secret without copying them out of the settings page. This NAS's app was made that way.

Either way:

- **Homepage URL:** `https://nas.tail1234.ts.net:8443`
- **Authorization callback URL:** `https://nas.tail1234.ts.net:8443/.auth/github/callback`

Store the client ID and secret outside the Nix store:

```sh
sudo install -d -m 0700 /etc/secrets
sudo install -m 0400 /dev/stdin /etc/secrets/outlook-mcp.env <<'EOF'
GITHUB_CLIENT_ID=Ov23li...
GITHUB_CLIENT_SECRET=...
EOF
```

agenix or sops-nix work just as well. The file only needs to be readable by root, because systemd reads it before dropping privileges.

### 3. NixOS configuration

Copy this folder next to your NAS configuration and import it:

```nix
{
  imports = [ ./outlook-mcp ];

  services.tailscale.enable = true;

  services.outlook-mcp = {
    enable = true;
    microsoftAccount = "kirill.menke@outlook.de";
    externalUrl = "https://nas.tail1234.ts.net:8443";   # no trailing slash
    github.allowedUsers = [ "YourGitHubLogin" ];        # exact capitalization, the match is case-sensitive
    environmentFile = "/etc/secrets/outlook-mcp.env";
  };
}
```

If the port in `externalUrl` doesn't match `funnel.port`, an assertion stops the build.

To keep the host name out of a public repository, set `externalUrl = null` and add `EXTERNAL_URL=https://nas.tail1234.ts.net:8443` to the environment file instead; the port check is skipped then. `../outlook.nix` does this with a sops-nix template.

The proxy needs Go ≥ 1.26.2, which nixos-unstable has. If your channel's default `go` is older, override the package:

```nix
services.outlook-mcp.proxyPackage = pkgs.callPackage ./outlook-mcp/pkgs/mcp-auth-proxy/package.nix {
  buildGoModule = pkgs.buildGo126Module;
};
```

### 4. Sign in to Microsoft (once)

```sh
sudo outlook-mcp-login
```

1. The command prints a code and a Microsoft URL. Open the URL on any device.
2. Enter the code, sign in as `kirill.menke@outlook.de`, and approve reading your mail and your basic profile.

The script checks the result with a Graph call and then starts the service. The service stays inactive until this step succeeds. To check the sign-in later, run `sudo outlook-mcp-login status`, which exits non-zero if it fails.

### 5. Check the public endpoint

```sh
curl -s https://nas.tail1234.ts.net:8443/.well-known/oauth-authorization-server | jq .registration_endpoint
curl -si https://nas.tail1234.ts.net:8443/mcp | head -1      # expect a 401
```

### 6. Connect claude.ai

1. Go to **Customize → Connectors**, choose **+ Add**, then **Add custom connector**.
2. **Name:** `Outlook`. **URL:** `https://nas.tail1234.ts.net:8443/mcp`.
3. **Authentication:** sign in.
4. **OAuth client:** **Register automatically**. The proxy supports dynamic client registration but not Claude's published-identity option.
5. Sign in with GitHub in the window that opens.

In a chat, turn it on under **+ → Connectors**.

## Operations

| | |
| --- | --- |
| Status | `systemctl status outlook-mcp outlook-mcp-funnel` |
| Logs | `journalctl -u outlook-mcp` and `/var/log/outlook-mcp/*.log` (rotated weekly) |
| Microsoft sign-in lapsed | `sudo outlook-mcp-login` |
| Change tools | Edit `services.outlook-mcp.tools`, switch, then `sudo outlook-mcp-login` so the token gets the new scopes |
| Update ms-365-mcp-server | `pkgs/ms-365-mcp-server/update.sh [version]` |
| Update mcp-auth-proxy | Bump `version`, set the `src` hash and `vendorHash` to `lib.fakeHash`, and copy the `got` values from each failed build. Check that `restrict-redirect-uris.patch` still applies. |

### Revoking access

- **claude.ai:** remove the connector under Customize → Connectors.
- **Gate:** `sudo systemctl stop outlook-mcp && sudo rm -rf /var/lib/outlook-mcp/auth`. This invalidates every token and client the gate has issued.
- **Microsoft:** remove the app at <https://account.live.com/consent/Manage>, then delete `/var/lib/outlook-mcp/msal`.
- **GitHub:** delete the OAuth app.

## Security notes

- **Only the gate is public.** `/mcp` requires a gate token, which requires a GitHub login as one of `allowedUsers`.
- **Redirect URIs are pinned.** Upstream mcp-auth-proxy accepts any redirect URI at registration, and its consent page doesn't say which client is asking. That means a crafted link could otherwise hand someone a mail-reading token after a single click from you. The bundled patch only accepts `allowedRedirectUris` (claude.ai's callback), so codes can only ever go to claude.ai. GitHub 2FA doesn't help against that attack once you've authorized the OAuth app, which is why the patch matters.
- **The token cache isn't safe in backups.** It's AES-encrypted, but its key file (`.cache-key`) sits in the same directory, owned by the `outlook-mcp` user. Anything that copies `/var/lib/outlook-mcp/msal`, including ZFS snapshots or replication of that dataset, gets a working Microsoft session. Exclude that directory, or treat those backups as secrets.
- **Mail text reaches Anthropic** whenever Claude reads a message for you, the same as with any connector.
- **Emails can contain prompt injection.** Read-only means an injected instruction can't send, delete or forward anything.
- **The consent screen names Softeria's app** (client ID `084a3e9f-a9f4-43f7-89f9-d229cf97853e`). It's a public client, so Softeria never receives your tokens. Using your own app registration would need an Azure/Entra tenant, because personal accounts can no longer register apps without one.
