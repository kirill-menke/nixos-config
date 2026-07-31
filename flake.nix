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
  };

  outputs = { self, nixpkgs, home-manager, affinity-nix, nvidia-pstated, disko, ... }@inputs: {
    # Headless NAS (TerraMaster F4-425 Plus). Deliberately does NOT pull in
    # home-manager or anything from ./configuration.nix -- it shares only the
    # flake inputs with the desktop, not its configuration.
    nixosConfigurations.nas = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs = { inherit inputs; };
      modules = [
        disko.nixosModules.disko
        ./hosts/nas
      ];
    };

    nixosConfigurations.pc = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs = { inherit inputs; };
      modules = [
        ./configuration.nix
        
        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.users.kirill = import ./home.nix;
	        home-manager.backupFileExtension = "backup";
          
          # Pass inputs to home.nix
          home-manager.extraSpecialArgs = { inherit inputs; };
        }
      ];
    };
  };
}
