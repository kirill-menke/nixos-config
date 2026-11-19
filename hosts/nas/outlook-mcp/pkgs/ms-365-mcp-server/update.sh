#!/usr/bin/env nix-shell
#!nix-shell -i bash -p nodejs_22 gnused
# Bump ms-365-mcp-server: ./update.sh [version]   (default: latest on npm)
# Rewrites package.json, package-lock.json and the version/hash in package.nix.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
version="${1:-$(npm view @softeria/ms-365-mcp-server version)}"
integrity="$(npm view "@softeria/ms-365-mcp-server@${version}" dist.integrity)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
npm pack --silent "@softeria/ms-365-mcp-server@${version}" >/dev/null
tar -xzf "softeria-ms-365-mcp-server-${version}.tgz"

# Published manifest minus dev deps, scripts and the optional keytar/Azure extras.
node -e '
  const fs = require("fs");
  const p = JSON.parse(fs.readFileSync("package/package.json"));
  delete p.devDependencies;
  delete p.optionalDependencies;
  p.scripts = {};
  fs.writeFileSync(process.argv[1], JSON.stringify(p, null, 2) + "\n");
' "$here/package.json"

cp "$here/package.json" "$work/package.json"
npm install --package-lock-only --ignore-scripts --no-audit --no-fund --silent
cp "$work/package-lock.json" "$here/package-lock.json"

sed -i \
  -e "s|^  version = \".*\";|  version = \"${version}\";|" \
  -e "s|hash = \"sha512-.*\";|hash = \"${integrity}\";|" \
  "$here/package.nix"

echo "ms-365-mcp-server -> ${version}"
