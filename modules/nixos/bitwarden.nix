# Bitwarden in Chrome for the NAS's Vaultwarden (hosts/nas/vaultwarden.nix),
# in place of Chrome's own password manager. Hosts that use Vaultwarden import
# this next to ./workstation.nix (programs.chromium writes Google Chrome's
# policy too); its Firefox side is modules/home/bitwarden.nix.
{ lib, ... }:

{
  programs.chromium = {
    extensions = lib.mkAfter [
      "nngceckbapebfimnlniiiahkandclblb" # Bitwarden
    ];
    extraOpts.PasswordManagerEnabled = false;
  };
}
