{
  description = "NixOS configuration with Home Manager";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    affinity-nix.url = "github:mrshmllow/affinity-nix";
    spicetify-nix.url = "github:Gerg-L/spicetify-nix";

    # Claude Code and Orca IDE. Both are bot-updated daily and served from
    # cache.numtide.com (substituter configured in hosts/pc/default.nix); Orca
    # is not in nixpkgs and ships no flake of its own.
    llm-agents.url = "github:numtide/llm-agents.nix";

    nvidia-pstated = {
      url = "github:sasha0552/nvidia-pstated";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Declarative disk partitioning for the NAS. nixos-anywhere runs this at
    # install time to partition, format and mount before installing.
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # VibeReel's backend (leetx-api, Prowlarr + qBittorrent façade), in the vibe-reel repo.
    # path: to the subdirectory, not ?dir=, so the store copy skips the app's node_modules.
    # Its NixOS module builds the package against this flake's nixpkgs.
    leetx-api = {
      url = "path:/home/kirill/Documents/projects/vibe-reel/backend";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Network-namespace VPN confinement for the NAS: puts qBittorrent behind
    # ProtonVPN (see hosts/nas/vpn.nix). No nixpkgs input to follow.
    vpn-confinement.url = "github:Maroka-chan/VPN-Confinement";

    # Kirifin, the self-hosted finance app (private repo). Local git checkout, so
    # only committed files of the given branch are built.
    # sops-nix: decrypts secrets/*.yaml on the host at activation (age via the
    # SSH host key). Only encrypted files are in this repo.
    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    kirifin = {
      url = "git+file:///home/kirill/Documents/projects/kirifin?ref=phase-1-2";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { nixpkgs, ... }@inputs:
    let
      system = "x86_64-linux";

      # One host per directory under ./hosts. Each host's default.nix imports
      # the flake modules it needs itself (home-manager, disko, ...), so the
      # desktop and the headless NAS share only the inputs, not configuration.
      mkHost =
        path:
        nixpkgs.lib.nixosSystem {
          inherit system;
          specialArgs = { inherit inputs; };
          modules = [ path ];
        };
    in
    {
      nixosConfigurations = {
        pc = mkHost ./hosts/pc;
        nas = mkHost ./hosts/nas;
      };

      # `nix fmt` formats the whole tree with the official formatter.
      formatter.${system} = nixpkgs.legacyPackages.${system}.nixfmt-tree;
    };
}
