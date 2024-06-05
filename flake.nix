{
  description = "Nix related tooling for a command line interface for audible. With the CLI you can download your Audible books, cover, chapter files & conver them.";
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    systems.url = "github:nix-systems/x86_64-linux";
    flake-compat.url = "https://flakehub.com/f/edolstra/flake-compat/1.tar.gz";
    nix-filter.url = "github:numtide/nix-filter";
    nix-appimage = {
      url = "github:ralismark/nix-appimage";
    };
  };
  outputs = {
    self,
    nixpkgs,
    systems,
    nix-appimage,
    flake-compat,
    nix-filter,
  } @ inputs: let
    eachSystem = nixpkgs.lib.genAttrs (import systems);
    pkgsFor = eachSystem (system: (nixpkgs.legacyPackages.${system}.extend self.overlays.default));
  in {
    formatter = eachSystem (system: pkgsFor.${system}.alejandra);
    checks = eachSystem (system: self.packages.${system});
    overlays = import ./nix/overlays.nix {
      inherit inputs;
      lib = nixpkgs.lib; # TODO: Understand this construct
    };

    packages = eachSystem (system: let
      pkgs = pkgsFor.${system};
    in rec {
      audible-cli = pkgs.audible-cli;
      audible-cli-full = pkgs.audible-cli-full;
      isbntools = pkgs.python3Packages.isbntools;
      audible-cli-AppImage = inputs.nix-appimage.mkappimage.${system} {
        drv = audible-cli-full;
        name = audible-cli-full.name;
        entrypoint = pkgs.lib.getExe audible-cli-full;
      };
      audible-cli-docker = pkgs.dockerTools.buildLayeredImage {
        name = audible-cli-full.pname; # `.name` has illegal docker tag format
        tag = "latest";
        contents = [audible-cli-full];
        config = {
          Entrypoint = [
            "${pkgs.lib.getExe audible-cli-full}"
          ];
        };
      };
      default = audible-cli-full;
    });
  };
}
