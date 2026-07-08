<p align="center">
  <img src="assets/banner-1536x384.png" alt="audible-cli" style="max-width: 768; width: 100%; height: auto;">
</p>

<h1 align="center" style="font-size: 2em; font-weight: 600; margin-bottom: 0.5em;">
  <strong>AUDIBLE-CLI</strong>
</h1>

<p align="center">
  <b>A powerful command-line tool for managing and downloading your Audible audiobooks.</b><br>
  Built with ❤️ in Python.
</p>

<p align="center">
  <a href="https://pypi.org/project/audible-cli/"><img src="https://img.shields.io/pypi/v/audible-cli?color=blue&logo=pypi"></a>
  <a href="https://pypi.org/project/audible-cli/"><img src="https://img.shields.io/pypi/pyversions/audible-cli?logo=python&logoColor=yellow"></a>
  <a href="https://pypi.org/project/audible-cli/"><img src="https://img.shields.io/pypi/dm/audible-cli"></a>
  <a href="https://github.com/mkb79/audible-cli/releases"><img src="https://img.shields.io/github/v/release/mkb79/audible-cli?logo=github"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/mkb79/audible-cli"></a>
</p>

> [!NOTE]
> ### 🦀 audible-rs — a Rust rewrite in the making, experienced testers wanted
>
> A ground-up **Rust** reimplementation of `audible-cli` (and the
> [`Audible`](https://github.com/mkb79/Audible) library) is in active
> development: one fast, statically-linked binary — **no Python required** —
> with a reworked command set, an encrypted auth format, a local library
> database (SQLite/FTS5), and a capability-based plugin system.
>
> It is **pre-alpha** and I'm looking for **experienced users** to run it
> against real accounts and report the rough edges. If you're comfortable on
> the command line and don't mind occasional breakage, please help kick the
> tyres → **[github.com/mkb79/audible-rs](https://github.com/mkb79/audible-rs)**
> (the README there has a one-line installer for prebuilt Linux/macOS binaries).
>
> `audible-cli` stays fully supported — audible-rs is the future direction,
> not an immediate replacement, and the two use separate config directories,
> so you can run them side by side.

---

## ✨ Features

- 🔑 Manage multiple Audible accounts (profiles)
- 📚 Browse and export your **library** and **wishlist**
- 🎧 Download audiobooks in **AAXC** or **AAX** with metadata & chapters
- ⚡ Fast HTTP requests powered by [httpx](https://www.python-httpx.org/)
- 🛠️ Plugin system for custom commands & extensions
- 💻 Cross-platform: Linux, macOS, Windows
- 🚀 Prebuilt executables (no Python required)

---

## 📦 Installation

**With [uv tool](https://docs.astral.sh/uv/guides/tools/#installing-tools) (recommended)**

```shell
uv tool install audible-cli
```

**With [uvx](https://docs.astral.sh/uv/guides/tools/#running-tools)**

```shell
uvx --from audible-cli audible
```

**From PyPI**

```shell
pip install audible-cli
```

**From GitHub**

```shell
git clone https://github.com/mkb79/audible-cli.git
cd audible-cli
pip install .
```

---

## 🖥️ Standalone executables

Don’t want to install Python?  
Prebuilt binaries are available on the [releases page](https://github.com/mkb79/audible-cli/releases).

- **Windows:** [onedir](https://github.com/mkb79/audible-cli/releases/latest/download/audible_win_dir.zip) (recommended), [onefile](https://github.com/mkb79/audible-cli/releases/latest/download/audible_win.zip)  
- **Linux:** [Ubuntu 22.04](https://github.com/mkb79/audible-cli/releases/latest/download/audible_linux_ubuntu_22_04.zip), [latest](https://github.com/mkb79/audible-cli/releases/latest/download/audible_linux_ubuntu_latest.zip)  
- **macOS:** [onefile](https://github.com/mkb79/audible-cli/releases/latest/download/audible_mac.zip), [onedir](https://github.com/mkb79/audible-cli/releases/latest/download/audible_mac_dir.zip)  

⚠️ *On Windows, prefer the **onedir** build for faster startup.*

---

## 🚀 Quickstart

1. Run the interactive setup:

   ```shell
   audible quickstart
   ```

   → creates config, profile, and auth file.

2. List your library:

   ```shell
   audible library list
   ```

3. Download your entire library:

   ```shell
   audible download --all --aax
   ```

---

## 📚 Common use cases

| Goal | Command |
|------|---------|
| List all audiobooks | `audible library list` |
| Export library to JSON | `audible library export --output library.json` |
| Add to wishlist | `audible wishlist add --asin B004V00AEG` |
| Download since date | `audible download --start-date "2023-01-01" --aaxc --all` |
| Switch profile | `audible -P germany library list` |

---

## ⚙️ Configuration & Profiles

### App directory

`audible-cli` stores its configuration files in an **app directory**.  

| OS       | Path                                      |
|----------|-------------------------------------------|
| Windows  | `C:\Users\<user>\AppData\Local\audible`   |
| Linux    | `~/.audible`                              |
| macOS    | `~/.audible`                              |

You can override this by setting the environment variable:

```shell
export AUDIBLE_CONFIG_DIR=/path/to/dir
```

---

### Config file

- Name: `config.toml`  
- Format: [TOML](https://toml.io/en/)  
- Structure:
  - `[APP]` section → global defaults  
  - `[profile.<name>]` section → settings per Audible account  

Example:

```toml
[APP]
primary_profile = "default"
filename_mode   = "ascii"
chapter_type    = "tree"

[profile.default]
auth_file    = "auth.json"
country_code = "us"

[profile.germany]
auth_file    = "auth_de.json"
country_code = "de"
```

---

### Profiles

- Each profile corresponds to an Audible account or marketplace  
- Contains:  
  - `auth_file` → authentication file  
  - `country_code` → Audible marketplace (`us`, `de`, `uk`, …)  
- Switch profiles with:  

```shell
audible -P germany library list
```

The `[APP].primary_profile` is used if no profile is specified.

---

### Auth files

- Stored in the same app directory as the config file  
- Can be password-protected:  

```shell
audible -p "mypassword" download --asin <ASIN>
```

- If no password is passed, you will be prompted with hidden input  

---

### Config options

🔧 **APP section**
- `primary_profile`: default profile if none is specified  
- `filename_mode`: filename handling for downloads  
  - `ascii` (default)  
  - override with `--filename-mode`  
- `chapter_type`: chapter format for downloads  
  - `tree` (default)  
  - override with `--chapter-type`  

👤 **Profile section**
- `auth_file`: authentication file for this profile  
- `country_code`: Audible marketplace  
- `filename_mode`: overrides `[APP].filename_mode`  
- `chapter_type`: overrides `[APP].chapter_type`  

---

## 🧩 Built-in commands

- **activation-bytes** → Manage DRM activation keys  
- **api** → Call raw Audible API endpoints  
- **download** → Download audiobooks  
- **library** → List, export your library  
- **wishlist** → Manage wishlist (list, add, remove, export)  
- **manage** → Profiles, configs, auth-files  
- **quickstart** → Interactive setup  

Show help:

```shell
audible <command> -h
```

---

## 🔧 Plugins & Extensions

### Custom plugin commands

Create a file in the plugin folder, e.g. `cmd_hello.py`:

```python
import click

@click.command(name="hello")
def cli():
    click.echo("Hello from plugin!")
```

> **Important:** A **custom plugin must start with the prefix `cmd_`**. The loader scans for `cmd_*.py` files and attaches each command to the CLI.

Run:

```shell
audible hello
```

### Plugin packages

You can also distribute plugins as Python packages via entry points.  
The entry point group is **`audible.cli_plugins`**.

#### Example: pyproject.toml

```toml
[project]
name = "audible-myplugin"
version = "0.1.0"
dependencies = ["audible-cli", "click"]

[project.entry-points."audible.cli_plugins"]
my_command = "myplugin.cli:my_command"
another    = "myplugin.cli:another"
```

After installation, your plugin commands will automatically be available in `audible`:

```shell
audible my-command
audible another
```

---

## 🔊 Verbosity

Control logging output:

```shell
audible -v debug library list
audible -v error download --all
```

Levels: `debug`, `info`, `warning`, `error`, `critical`  
Default: `info`

---

## 🧩 Add-ons

- [audible-cli-flask](https://github.com/mkb79/audible-cli-flask) → Run `audible-cli` in a Flask web server  
- [audible-series](https://pypi.org/project/audible-series/) → Organize series from your library  

Want your add-on listed? → Open a PR or issue 🚀

---

## 🤝 Contributing

Contributions welcome!  
- File [issues](https://github.com/mkb79/audible-cli/issues)  
- Open pull requests  
- Share plugins and add-ons  

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0-only)**.  
See [LICENSE](./LICENSE) for details.