{
  lib,
  python3Packages,
  fetchFromGitHub,
  nix-update-script,
}:
python3Packages.buildPythonPackage rec {
  pname = "isbntools";
  version = "4.3.29";
  pyproject = true;

  src = fetchFromGitHub {
    owner = "xlcnd";
    repo = "isbntools";
    rev = "refs/tags/v${version}";
    hash = "sha256-s47y14YHL/ihAUCnneDcTlyVQj3rUgUnBLD2dPBGD/Y=";
  };

  nativeBuildInputs = with python3Packages; [
    setuptools
  ];
  propagatedBuildInputs = with python3Packages; [
    isbnlib
  ];

  # FUTURE: Configure and enable the upstream tests!
  doCheck = false;

  pythonImportsCheck = [
    "isbntools"
  ];

  passthru.updateScript = nix-update-script {};

  meta = {
    description = "A Python framework for 'all things ISBN' including metadata, descriptions, covers...";
    license = lib.licenses.lgpl3Plus;
    homepage = "https://github.com/xlcnd/isbntools";
    changelog = "https://github.com/xlcnd/isbntools/tree/v${src.rev}/CHANGES.txt";
    maintainers = [lib.maintainers.kai-tub];
  };
}
