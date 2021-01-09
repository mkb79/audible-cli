# Plugin Commands

## Location

Audible-cli expected plugin commands in the `plugins` subdir of the config dir. You can provide a custom dir with the `audible --plugins PATH_TO_PLUGIN_DIR`.

## Commands in this folder

To use commands in these folder simply copy them to the plugin folder.

## Custom Commands

You can provide own subcommands and execute them with `audible plugin-cmds SUBCOMMAND`.
All plugin commands must be placed in the plugin folder. Every subcommand must have his own file.
Every file have to be named ``cmd_{SUBCOMMAND}.py``. Each subcommand file must have a function called `cli` as entrypoint. This function have to be decorated with ``@click.group()`` or  ``@click.command()``.

Relative imports in the command files doesn't work. So you have to work with absolute imports. Please take care about this.