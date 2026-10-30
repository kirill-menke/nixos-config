{
  lib,
  buildGoModule,
  fetchFromGitHub,
}:

buildGoModule (finalAttrs: {
  pname = "mcp-auth-proxy";
  version = "2.10.2";

  src = fetchFromGitHub {
    owner = "sigbit";
    repo = "mcp-auth-proxy";
    tag = "v${finalAttrs.version}";
    hash = "sha256-unjFj943dCI2Ylo2Ekv7gsX8ltPE1nfnaOxbqBsAeu4=";
  };

  vendorHash = "sha256-3yMn2ybSLa+6V/VKY0AzChFCqGv5dPSzz70Xdps5n2E=";

  # Adds ALLOWED_REDIRECT_URIS: dynamic client registration rejects any other
  # redirect URI, so a crafted authorize link can't hand a token to a third party.
  patches = [ ./restrict-redirect-uris.patch ];

  subPackages = [ "." ];
  ldflags = [
    "-s"
    "-w"
  ];

  meta = {
    description = "OAuth 2.1 authentication proxy for MCP servers";
    homepage = "https://github.com/sigbit/mcp-auth-proxy";
    license = lib.licenses.mit;
    mainProgram = "mcp-auth-proxy";
  };
})
