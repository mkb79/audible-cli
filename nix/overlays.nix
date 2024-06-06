{
  lib,
  inputs,
}: let
  mkDate = longDate: (lib.concatStringsSep "-" [
    (builtins.substring 0 4 longDate)
    (builtins.substring 4 2 longDate)
    (builtins.substring 6 2 longDate)
  ]);
in {
  default = final: prev: let
    date = mkDate (inputs.self.lastModifiedDate or "19700101");
    # Take advantage of the 'accidentally' TOML-compatible file structure
    _versionPy = builtins.fromTOML (builtins.readFile ../src/audible_cli/_version.py);
    version = _versionPy.__version__;
  in {
    audible-cli = final.callPackage ./default.nix {
      version = "${version}+date=${date}_${inputs.self.shortRev or "dirty"}";
      nix-filter = inputs.nix-filter.lib;
      enable-plugin-decrypt = false;
      enable-plugin-goodreads-transform = false;
      enable-plugin-annotations = false;
      enable-plugin-image-urls = false;
      enable-plugin-listening-stats = false;
    };
    audible-cli-full = final.callPackage ./default.nix {
      version = "${version}+date=${date}_${inputs.self.shortRev or "dirty"}";
      nix-filter = inputs.nix-filter.lib;
    };
    python3Packages =
      prev.python3Packages
      // {
        isbntools = prev.callPackage ./isbntools.nix {};
      };
  };
}
