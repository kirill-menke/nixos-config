{
  lib,
  buildNpmPackage,
  fetchurl,
  importNpmLock,
  nodejs_22,
}:

buildNpmPackage (finalAttrs: {
  pname = "ms-365-mcp-server";
  version = "0.176.1";

  # The published npm tarball ships the prebuilt dist/. Building from the Git
  # source would need `npm run generate`, which downloads the Microsoft Graph
  # OpenAPI spec and can't run inside the Nix sandbox.
  src = fetchurl {
    url = "https://registry.npmjs.org/@softeria/ms-365-mcp-server/-/ms-365-mcp-server-${finalAttrs.version}.tgz";
    hash = "sha512-FPIY4SAIeU2t2+NC7Ec+WIrzg/dALf12CIDGlpB9wNTB6rI3mv9fY8zWS6haNOMqlrFPUmUgQcWmgzqw+VtF4A==";
  };

  # npm tarballs carry no lockfile. ./package.json is the published one minus
  # devDependencies, scripts and the optional native/Azure extras (keytar,
  # @azure/identity, @azure/keyvault-secrets), which this setup never loads.
  # ./package-lock.json was generated from it with
  #   npm install --package-lock-only --ignore-scripts
  # Every entry carries an integrity hash, so importNpmLock needs no npmDepsHash.
  postPatch = ''
    cp ${./package.json} package.json
    cp ${./package-lock.json} package-lock.json
  '';

  npmDeps = importNpmLock {
    package = lib.importJSON ./package.json;
    packageLock = lib.importJSON ./package-lock.json;
  };
  npmConfigHook = importNpmLock.npmConfigHook;

  nodejs = nodejs_22;
  dontNpmBuild = true;

  meta = {
    description = "MCP server for Microsoft 365 / Outlook via Microsoft Graph";
    homepage = "https://github.com/Softeria/ms-365-mcp-server";
    license = lib.licenses.mit;
    mainProgram = "ms-365-mcp-server";
    platforms = lib.platforms.linux;
  };
})
