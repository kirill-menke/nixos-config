# Bitwarden in Firefox for the NAS's Vaultwarden (hosts/nas/vaultwarden.nix).
# Users who use Vaultwarden import this next to ./workstation.nix; its Chrome
# side is modules/nixos/bitwarden.nix.
{
  inputs,
  lib,
  pkgs,
  ...
}:

{
  programs.firefox.profiles.default = {
    extensions.packages = lib.mkAfter [
      inputs.firefox-addons.packages.${pkgs.stdenv.hostPlatform.system}.bitwarden
    ];
    # Passwords live in Bitwarden only: no saving, autofill or generator.
    settings = {
      "signon.rememberSignons" = false;
      "signon.autofillForms" = false;
      "signon.generation.enabled" = false;
    };
  };
}
