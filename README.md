# audible-cli

**audible-cli** is a command line interface for the [Audible](https://github.com/mkb79/Audible) package. Both are written in python.

## Requirements

audible-cli needs at least *Python 3.6* and *Audible v0.4.1*.

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

| OS | Path|
|:—|:—|
| Windows | ``C:\Users\<user>\AppData\Local\audible`` |
| Unix | ``~/.audible`` |
| Mac OS X | ``~/.audible`` |

To override this behavior, you can call `audible` or `audible-quickstart` with the `-c PATH_TO_CONF_DIR` option. You have to do this on each call. So if you want to make use of a custom folder best practice is to use the environment variable method.

### The config file

The config data will be stored in the [toml](https://github.com/toml-lang/toml) format as ``config.toml``.

It has a main section named ``APP`` and sections for each profile you created named ``profile.<profile_name>``

### profiles

audible-cli make use of profiles. Each profile contains the name of the corresponding auth file and the country code for the audible marketplace. If you have audiobooks on multiple marketplaces, you have to create a profile for each one with the same auth file.

In the main section of the config file, a primary profile is defined. This profile is used, if no other is specified. You can call `audible` or `audible-quickstart` with the `-P PROFILE_NAME` option, to select another profile.

### auth files

Like the config file, auth files are stored in the config dir too. If you protected your auth file with a password call `audible` or `audible-quickstart` with the `-p PASSWORD` option, to provide the password.

If the auth file is encrypted and you don’t provide the password, you will be asked for it with a „hidden“ input field. 

## Getting started

Use the `audible-quickstart` command in your shell to create your first config, profile and auth file. `audible-quickstart` runs on interactive mode, so you have to answer multiple questions to finish.

## Commands

Call `audible -h` to let you show all main subcommands. At this time, there are the `manage` and `download` subcommand. The `manage` command has multiple subcommands. So take a look with the `audible manage -h` and `audible manage <subcommand> -h`. 

**More informations will be coming soon.** 