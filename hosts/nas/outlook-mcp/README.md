# outlook-mcp

Read-only access to a personal Outlook mailbox for claude.ai, self-hosted on NixOS.

```
claude.ai ──HTTPS :443──▶ Tailscale Funnel ──▶ tailnet node "outlook-mcp" (second tailscaled, userspace)
                                                  │
                                                  ▼
                                           mcp-auth-proxy :8471 (127.0.0.1)
                                                  │  OAuth 2.1 gate, GitHub login
                                                  │  stdio
                                                  ▼
                                           ms-365-mcp-server ──▶ Microsoft Graph (Mail.Read)
```

- **[ms-365-mcp-server](https://github.com/Softeria/ms-365-mcp-server)** talks to Outlook through Microsoft Graph. It signs in once with a device code using Softeria's public app registration, so no Azure tenant is needed. The Microsoft tokens stay in `/var/lib/outlook-mcp/msal`.
- **[mcp-auth-proxy](https://github.com/sigbit/mcp-auth-proxy)** turns that stdio server into `https://outlook-mcp.<tailnet>.ts.net/mcp` behind OAuth 2.1 with dynamic client registration. claude.ai signs in to this gate with your GitHub account and only ever holds a token for the gate, never a Microsoft token.
- **Tailscale Funnel** publishes the gate on the internet, which works behind DS-Lite/CGNAT. It runs on a tailnet node of its own, because claude.ai only connects to port 443 (see [Tailscale](#1-tailscale)).

## What Claude can do

These are the tools exposed by default, and all of them need only `Mail.Read`. The token also carries `User.Read`, only because the sign-in check calls Graph `/me`:

| Tool | Purpose |
| --- | --- |
| `list-mail-messages`, `list-mail-folder-messages` | Search and list messages, globally or per folder |
| `list-mail-folders`, `list-mail-child-folders` | Browse the folder tree |
| `get-mail-message`, `read-mail-text` | Read one message |
| `list-mail-attachments`, `get-mail-attachment-content` | Read attachments |

By default nothing can send, move, delete or draft. `--read-only` drops write tools even if you add one to `tools` by mistake. List calls return at most `maxResults` items (25) and can't follow further pages.

To allow writing, set `readOnly = false` and add the tool names to `tools`, then run `sudo outlook-mcp-login` so the token gets the new scope. Drafts (`create-draft-email`, `create-reply-draft`, `create-reply-all-draft`, `create-forward-draft`, `add-mail-attachment`) and organizing (`update-mail-message`, `move-mail-message`, `copy-mail-message`, `create-mail-folder`, `create-mail-child-folder`, `update-mail-folder`) need `Mail.ReadWrite`, and nothing leaves the mailbox. Graph would also accept that scope for deleting, so the tool list is the boundary. Sending (`send-mail`, `send-draft-message`, `reply-*`, `forward-mail-message`) needs `Mail.Send` and lets an email with hidden instructions mail your data to anyone. Rules (`create-mail-rule`) can set up a silent auto-forward. `../outlook.nix` enables drafts and organizing.

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

The examples below use `outlook-mcp.tail1234.ts.net`, the gate's own node (`funnel.hostname`). Replace `tail1234` with your tailnet's name.

### 1. Tailscale

claude.ai's backend only connects to MCP servers on port 443 ([anthropics/claude-ai-mcp#125](https://github.com/anthropics/claude-ai-mcp/issues/125)). On 8443 or 10000 the connector fails with "Couldn't reach", and no request ever arrives. Funnel is switched on per host and port, so funneling 443 on the host's own tailnet node would also publish whatever else that node serves there. The module therefore runs a second tailscaled in userspace-networking mode as a tailnet node of its own (`funnel.hostname`, default `outlook-mcp`), and Funnel runs only on that node's 443.

In the Tailscale admin console:

1. **DNS:** turn on MagicDNS and **HTTPS Certificates**.
2. **Access controls:** the node needs the `funnel` node attribute. New tailnets grant it to every member:
   ```json
   "nodeAttrs": [{ "target": ["autogroup:member"], "attr": ["funnel"] }]
   ```
   If it's missing, `tailscale funnel` prints a link that adds it. Once the node exists, you can narrow the target to its address.

After the first switch, log the node in as yourself:

```sh
sudo outlook-mcp-login tailscale    # prints a login URL, then turns Funnel on
sudo outlook-mcp-tailscale status   # `tailscale` bound to the gate's node
```

Check that the name resolves publicly with an IPv4 address, since claude.ai connects over IPv4: `dig @1.1.1.1 outlook-mcp.tail1234.ts.net A`. The first HTTPS requests can fail with a TLS error for a minute while the node fetches its certificate.

### 2. GitHub app for the login

The gate identifies you through GitHub. Either kind of GitHub app works, because both use the same login endpoints:

- **OAuth App:** <https://github.com/settings/developers> → **OAuth Apps → New OAuth App**, then generate a client secret.
- **GitHub App** with no permissions, installed nowhere. It can also be created from a [manifest](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest), which hands back the client ID and secret without copying them out of the settings page. This NAS's app was made that way.

Either way:

- **Homepage URL:** `https://outlook-mcp.tail1234.ts.net`
- **Authorization callback URL:** `https://outlook-mcp.tail1234.ts.net/.auth/github/callback`

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

  services.outlook-mcp = {
    enable = true;
    microsoftAccount = "kirill.menke@outlook.de";
    externalUrl = "https://outlook-mcp.tail1234.ts.net";   # no trailing slash, no port
    github.allowedUsers = [ "YourGitHubLogin" ];        # exact capitalization, the match is case-sensitive
    environmentFile = "/etc/secrets/outlook-mcp.env";
  };
}
```

`externalUrl` must be on port 443; an assertion stops the build otherwise.

To keep the host name out of a public repository, set `externalUrl = null` and add `EXTERNAL_URL=https://outlook-mcp.tail1234.ts.net` to the environment file instead; the port check is skipped then. `../outlook.nix` does this with a sops-nix template.

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
curl -s https://outlook-mcp.tail1234.ts.net/.well-known/oauth-authorization-server | jq .registration_endpoint
curl -si https://outlook-mcp.tail1234.ts.net/mcp | head -1      # expect a 401
```

### 6. Connect claude.ai

1. Go to **Customize → Connectors**, choose **+ Add**, then **Add custom connector**.
2. **Name:** `Outlook`. **URL:** `https://outlook-mcp.tail1234.ts.net/mcp`.
3. **Authentication:** sign in.
4. **OAuth client:** **Register automatically**. The proxy supports dynamic client registration but not Claude's published-identity option.
5. Sign in with GitHub in the window that opens, then click **Authorize** on the gate's page. If no window opens, allow pop-ups for claude.ai and click **Connect** on the connector.

In a chat, turn it on under **+ → Connectors**.

### 7. Local MCP clients over SSH (optional)

MCP clients on a machine that can SSH into the NAS (omp, Claude Code, Claude Desktop) can start the server directly as a stdio server. That skips the public gate and OAuth:

```json
{
  "mcpServers": {
    "outlook": {
      "type": "stdio",
      "command": "ssh",
      "args": ["-T", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30", "nas", "sudo", "outlook-mcp-stdio"]
    }
  }
}
```

`outlook-mcp-stdio` runs the server as `outlook-mcp` with the service's tools, `--read-only` and account pin, and shares its token cache. The server re-reads the cache from disk before every access, so concurrent processes stay consistent. It needs root on the NAS, so anyone who can use it could read the mailbox anyway. Without shared mode, each connected client starts its own Node process on the NAS (about 100–160 MiB) for as long as its session lasts.

### 8. One server for all local clients (`shared.enable`, optional)

With `services.outlook-mcp.shared.enable = true`, `outlook-mcp-mux.service` runs a single server and serves it on the Unix socket `/run/outlook-mcp-mux/mcp.sock` (mode 0660, group `outlook-mcp-clients`, members from `shared.users`). `mux.py` initialises the server once, answers each client's `initialize` and `ping` itself and forwards everything else with request ids rewritten, so any number of sessions share it. `outlook-mcp-client` (socat) connects a stdio client to the socket, and `outlook-mcp-stdio` becomes such a connection too, so existing `ssh nas sudo outlook-mcp-stdio` configs keep working. Another machine forwards the socket over one SSH connection, e.g. `ssh -N -L /run/outlook-mcp/mcp.sock:/run/outlook-mcp-mux/mcp.sock nas`, and its clients run socat on that end.

Why a socket and not upstream's `--http` mode: in HTTP mode ms-365-mcp-server expects every request to carry the caller's own Microsoft token (`401 Missing or malformed Authorization header` otherwise) and ignores the local token cache. A Unix socket also keeps the server off the tailnet; the gate's userspace node relays tailnet connections to every loopback TCP port. The gate itself keeps its own server.

## Operations

| | |
| --- | --- |
| Status | `systemctl status outlook-mcp outlook-mcp-tailscaled outlook-mcp-funnel`; `sudo outlook-mcp-tailscale funnel status` |
| Gate node logged out | `sudo outlook-mcp-login tailscale` |
| Logs | `journalctl -u outlook-mcp` and `/var/log/outlook-mcp/*.log` (rotated weekly) |
| Microsoft sign-in lapsed | `sudo outlook-mcp-login` |
| Change tools | Edit `services.outlook-mcp.tools`, switch, then `sudo outlook-mcp-login` so the token gets the new scopes |
| Update ms-365-mcp-server | `pkgs/ms-365-mcp-server/update.sh [version]` |
| Update mcp-auth-proxy | Bump `version`, set the `src` hash and `vendorHash` to `lib.fakeHash`, and copy the `got` values from each failed build. Check that `restrict-redirect-uris.patch` still applies. |

### Revoking access

- **claude.ai:** remove the connector under Customize → Connectors.
- **Public endpoint:** `sudo systemctl stop outlook-mcp-funnel` turns Funnel off. Removing the node under Machines in the admin console drops it from the tailnet.
- **Gate:** `sudo systemctl stop outlook-mcp && sudo rm -rf /var/lib/outlook-mcp/auth`. This invalidates every token and client the gate has issued.
- **Microsoft:** remove the app at <https://account.live.com/consent/Manage>, then delete `/var/lib/outlook-mcp/msal`.
- **GitHub:** delete the GitHub app.

## Security notes

- **Only the gate is public.** `/mcp` requires a gate token, which requires a GitHub login as one of `allowedUsers`.
- **Scanners find it within minutes.** The node's certificate lands in public Certificate Transparency logs, and scanners start probing right away. Everything except the OAuth metadata and the login endpoints answers 401.
- **The gate's node relays tailnet traffic to this host's loopback.** Like any userspace-networking node, it forwards tailnet connections for any port to 127.0.0.1 here, so every device the tailnet policy lets reach that node can reach loopback-only services through it. Funnel itself only delivers to :443. Keep the node reachable only by devices you already trust with the host.
- **Redirect URIs are pinned.** Upstream mcp-auth-proxy accepts any redirect URI at registration, and its consent page doesn't say which client is asking. That means a crafted link could otherwise hand someone a mail-reading token after a single click from you. The bundled patch only accepts `allowedRedirectUris` (claude.ai's callback), so codes can only ever go to claude.ai. GitHub 2FA doesn't help against that attack once you've authorized the OAuth app, which is why the patch matters.
- **The token cache isn't safe in backups.** It's AES-encrypted, but its key file (`.cache-key`) sits in the same directory, owned by the `outlook-mcp` user. Anything that copies `/var/lib/outlook-mcp/msal`, including ZFS snapshots or replication of that dataset, gets a working Microsoft session. Exclude that directory, or treat those backups as secrets.
- **Mail text reaches Anthropic** whenever Claude reads a message for you, the same as with any connector.
- **Emails can contain prompt injection.** Read-only means an injected instruction can't send, delete or forward anything.
- **The consent screen names Softeria's app** (client ID `084a3e9f-a9f4-43f7-89f9-d229cf97853e`). It's a public client, so Softeria never receives your tokens. Using your own app registration would need an Azure/Entra tenant, because personal accounts can no longer register apps without one.
