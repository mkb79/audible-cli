# Plugin Commands

## Command priority order

Commands will be added in the following order:

1. plugin dir commands
2. plugin packages commands
3. build-in commands

If a command is added, all further commands with the same name will be ignored.
This enables you to "replace" build-in commands very easy.

## Location

Audible-cli expected plugin commands in the `plugins` subdir of the app dir. You can provide a custom dir with the `AUDIBLE_PLUGIN_DIR` environment variable.

## Commands in this folder

To use commands in these folder simply copy them to the plugin folder.

## Custom Commands

You can provide own subcommands and execute them with `audible SUBCOMMAND`.
All plugin commands must be placed in the plugin folder. Every subcommand must
have his own file. Every file have to be named `cmd_{SUBCOMMAND}.py`.
Each subcommand file must have a function called `cli` as entrypoint.
This function have to be decorated with `@click.group(name="GROUP_NAME")` or
`@click.command(name="GROUP_NAME")`.

Relative imports in the command files doesn't work. So you have to work with
absolute imports. Please take care about this.

## Own Plugin Packages

If you want to develop a complete plugin package for `audible-cli` you can
do this on an easy way. You only need to register your sub-commands or
sub-groups to an entry-point in your setup.py that is loaded by the core
package.

Example for a setup.py

```python
from setuptools import setup

setup(
    name="yourscript",
    version="0.1",
    py_modules=["yourscript"],
    install_requires=[
        "click",
        "audible_cli"
    ],
    entry_points="""
        [audible.cli_plugins]
        cool_subcommand=yourscript.cli:cool_subcommand
        another_subcommand=yourscript.cli:another_subcommand
    """,
)
```
