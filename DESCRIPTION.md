# audible-cli

**audible-cli** is a command-line tool for managing and downloading your Audible audiobooks.  
It supports multiple accounts, library & wishlist management, metadata-rich downloads, and plugins.  

## ✨ Features

- Manage multiple Audible profiles and marketplaces  
- Browse, export, and download your library  
- Wishlist management (list, add, remove)  
- Download in AAXC or AAX with chapters and metadata  
- Extensible via plugin system (`audible.cli_plugins`)  
- Cross-platform (Linux, macOS, Windows)  
- Prebuilt binaries available (no Python required)  

## 🚀 Quickstart

Install from PyPI:

```shell
pip install audible-cli
```

Initialize configuration:

```shell
audible quickstart
```

List your library:

```shell
audible library list
```

Download all audiobooks:

```shell
audible download --all --aax
```

## 🔧 Plugins

You can extend audible-cli with custom commands or full plugin packages.  
Register your plugin commands to the `audible.cli_plugins` entry point in your `pyproject.toml`.  

Example:

```toml
[project.entry-points."audible.cli_plugins"]
my_command = "myplugin.cli:my_command"
```

---

For full documentation, visit the [GitHub repository](https://github.com/mkb79/audible-cli).
