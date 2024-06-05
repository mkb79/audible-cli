{
  lib,
  nix-filter,
  python3Packages,
  ffmpeg_7-headless,
  enable-plugin-decrypt ? true,
  enable-plugin-goodreads-transform ? true,
  enable-plugin-annotations ? true,
  enable-plugin-image-urls ? true,
  enable-plugin-listening-stats ? true,
  version ? "git",
}:
# The core of the code was taken from nixpkgs and with special thanks to the
# upstream maintainer `jvanbruegge`
# https://github.com/NixOS/nixpkgs/blob/63c3a29ca82437c87573e4c6919b09a24ea61b0f/pkgs/by-name/au/audible-cli/package.nix
python3Packages.buildPythonApplication {
  pname = "audible-cli";
  inherit version;
  pyproject = true;

  src = nix-filter {
    root = ./..;
    include =
      [
        # Include the "src" path relative to the root
        "src"
        "LICENSE"
        "setup.cfg"
        "README.md" # Required by `setup.cfg`
        "setup.py"
        "nix"
      ]
      ++ lib.optionals enable-plugin-annotations [
        "plugin_cmds/cmd_get-annotations.py"
      ]
      ++ lib.optionals enable-plugin-goodreads-transform [
        "plugin_cmds/cmd_goodreads-transform.py"
      ]
      ++ lib.optionals enable-plugin-image-urls [
        "plugin_cmds/cmd_image-urls.py"
      ]
      ++ lib.optionals enable-plugin-listening-stats [
        "plugin_cmds/cmd_listening-stats.py"
      ]
      ++ lib.optionals enable-plugin-decrypt [
        "plugin_cmds/cmd_decrypt.py"
      ];
  };

  # there is no real benefit of trying to make ffmpeg smaller, as headless
  # only takes about 25MB, whereas Python takes >120MB.
  dependencies = lib.optionals enable-plugin-decrypt [ffmpeg_7-headless];
  makeWrapperArgs =
    lib.optionals
    (enable-plugin-annotations || enable-plugin-goodreads-transform || enable-plugin-image-urls || enable-plugin-listening-stats || enable-plugin-decrypt)
    ["--set AUDIBLE_PLUGIN_DIR $src/plugin_cmds"];

  nativeBuildInputs = with python3Packages; [
    pythonRelaxDepsHook
    setuptools
  ];
  # FUTURE: Renable once shell completion is fixed!
  # ++ [
  #   installShellFiles
  # ];

  propagatedBuildInputs =
    (with python3Packages; [
      aiofiles
      audible
      click
      httpx
      packaging
      pillow
      questionary
      setuptools
      tabulate
      toml
      tqdm
    ])
    ++ lib.optionals enable-plugin-goodreads-transform [
      python3Packages.isbntools
    ];

  pythonRelaxDeps = [
    "httpx"
  ];

  # FUTURE: Fix fish code_completions & re-enable them
  # postInstall = ''
  #   export PATH=$out/bin:$PATH
  #   installShellCompletion --cmd audible \
  #     --bash <(source utils/code_completion/audible-complete-bash.sh) \
  #     --fish <(source utils/code_completion/audible-complete-zsh-fish.sh) \
  #     --zsh <(source utils/code_completion/audible-complete-zsh-fish.sh)
  # '';

  # upstream has no tests
  doCheck = false;
  # FUTURE: Add import tests for the different plugins!

  pythonImportsCheck = [
    "audible_cli"
  ];

  # passthru.updateScript = pkgs.nix-update-script {};

  meta = {
    description = "A command line interface for audible package. With the cli you can download your Audible books, cover, chapter files";
    license = lib.licenses.agpl3Only;
    homepage = "https://github.com/mkb79/audible-cli";
    maintainers = with lib.maintainers; [kai-tub];
    mainProgram = "audible";
  };
}
