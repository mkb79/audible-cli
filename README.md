# audible-cli

**audible-cli** is a command line interface for the [Audible](https://github.com/mkb79/Audible) package. Both are written in python.

## Requirements

audible-cli needs at least *Python 3.6* and *Audible v0.5.0*.

It depends on the following packages:

* aiofiles
* audible
* click
* colorama (on windows machines)
* httpx
* Pillow
* tabulate
* toml
* tqdm

## Installation

This package is not on PyPi at this moment. To install audible-cli you have to clone it from this repo. 

```shell

git clone https://github.com/mkb79/audible-cli.git
cd audible-cli
pip install .

```

## Basic informations

### config dir

audible-cli uses a config dir where it stores and search for all necessary files.

If the ``AUDIBLE_CONFIG_DIR`` environment variable is set, it uses the value as config dir. 

Otherwise it will use a folder depending on the operating system.

| OS       | Path                                      |
| ---      | ---                                       |
| Windows  | ``C:\Users\<user>\AppData\Local\audible`` |
| Unix     | ``~/.audible``                            |
| Mac OS X | ``~/.audible``                            |

To override this behavior, you can call `audible` or `audible-quickstart` with the `-c PATH_TO_CONF_DIR` option. You have to do this on each call. So if you want to make use of a custom folder best practice is to use the environment variable method.

### The config file

The config data will be stored in the [toml](https://github.com/toml-lang/toml) format as ``config.toml``.

It has a main section named ``APP`` and sections for each profile you created named ``profile.<profile_name>``

### profiles

audible-cli make use of profiles. Each profile contains the name of the corresponding auth file and the country code for the audible marketplace. If you have audiobooks on multiple marketplaces, you have to create a profile for each one with the same auth file.

In the main section of the config file, a primary profile is defined. This profile is used, if no other is specified. You can call `audible -P PROFILE_NAME`, to select another profile.

### auth files

Like the config file, auth files are stored in the config dir too. If you protected your auth file with a password call `audible -p PASSWORD`, to provide the password.

If the auth file is encrypted and you don’t provide the password, you will be asked for it with a „hidden“ input field. 

## Getting started

Use the `audible-quickstart` command in your shell to create your first config, profile and auth file. `audible-quickstart` runs on interactive mode, so you have to answer multiple questions to finish.

## Commands

Call `audible -h` to let you show all main subcommands. At this time, there are the `manage`, `download` and `library` subcommand. The `manage` command has multiple subcommands. So take a look with the `audible manage -h` and `audible manage <subcommand> -h`. 

## Plugins

### Location

Audible-cli expected plugins in the `plugins` subdir of the config dir. Read above how Audible-cli searches the config dir. You can provide a custom dir with the `audible --plugins PATH_TO_PLUGIN_DIR`.

### Custom Commands

You can provide own subcommands and execute them with `audible plugin-cmds SUBCOMMAND`.
All plugin commands must be placed in the plugin folder. Every subcommand must have his own file.
Every file have to be named ``cmd_{SUBCOMMAND}.py``. Each subcommand file must have a function called `cli` as entrypoint. This function have to be decorated with ``@click.group()`` or  ``@click.command()``.

Relative imports in the command files doesn't work. So you have to work with absolute imports. Please take care about this.

Example:

```python
import audible
import click
from audible_cli.config import pass_session


@click.command()
@click.option(
    "--asin", "-a",
    multiple=False,
    help="asin of the audiobook"
)
@pass_session
def cli(session, asin):
    "Print out the image urls for different resolutions for a book"
    with audible.Client(auth=session.auth) as client:
        r = client.get(f"library/{asin}",
                       response_groups="media",
                       image_sizes="1215, 408, 360, 882, 315, 570, 252, 558, 900, 500")
    images = r["item"]["product_images"]
    for res, url in images.items():
        click.echo(f"Resolution {res}: {url}")
```

**More informations will be coming soon.** 
