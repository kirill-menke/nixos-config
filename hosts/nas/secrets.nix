#
# sops-nix plumbing shared by every module that has a secret.
#
# This repo is public, so secrets live in secrets/*.yaml encrypted to the age
# recipients in .sops.yaml (kirill's personal key and this box's SSH host key).
# At activation sops-nix decrypts them with the host key into /run/secrets
# (tmpfs, root-only by default); nothing plaintext reaches the Nix store or a
# disk. Each module declares the secrets it uses next to the code that reads
# them -- this file only says how to decrypt.
#
# Edit from the desktop: `sops secrets/nas.yaml` (needs ~/.config/sops/age/keys.txt).
# After reinstalling the NAS with a new host key, re-derive its recipient with
# `ssh nas cat /etc/ssh/ssh_host_ed25519_key.pub | ssh-to-age`, replace `nas`
# in .sops.yaml and run `sops updatekeys secrets/*.yaml`.
#
{ inputs, ... }:
{
  imports = [ inputs.sops-nix.nixosModules.sops ];

  sops.age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
}
