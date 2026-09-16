{
  description = "NixOS configuration with Home Manager";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    affinity-nix.url = "github:mrshmllow/affinity-nix";
    spicetify-nix.url = "github:Gerg-L/spicetify-nix";
    nix-claude-code.url = "github:ryoppippi/nix-claude-code";

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

    # Personal search/magnet/download API (Prowlarr + qBittorrent façade).
    # Its NixOS module builds the package against this flake's nixpkgs.
    leetx-api = {
      url = "path:/home/kirill/Documents/projects/leetx-api";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Network-namespace VPN confinement for the NAS: puts qBittorrent behind
    # ProtonVPN (see hosts/nas/vpn.nix). No nixpkgs input to follow.
    vpn-confinement.url = "github:Maroka-chan/VPN-Confinement";
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
