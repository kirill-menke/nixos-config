{
  description = "NixOS configuration with Home Manager";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    affinity-nix.url = "github:mrshmllow/affinity-nix";
    
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, affinity-nix, ... }@inputs: {
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
