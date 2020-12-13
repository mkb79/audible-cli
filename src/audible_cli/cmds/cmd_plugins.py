from importlib import util

import click

from ..config import Session


def plugin_path_from_ctx(ctx):
    session = ctx.ensure_object(Session)
    return session.plugin_path


class PluginCommands(click.Group):
    """Loads commands from plugin folder.
    
    All command files in the plugin folder must be named ``cmd_{cmd_name}.py``.
    They must have a :func:`cli` function as entrypoint.
    The :func:`cli` have to be decorated with ``@click.group()`` or 
    ``@click.command()``. The command (or group) name displayed by the command 
    line interface is the ``cmd_name`` from the filename above. You can specify
    a custom plugin folder with the ``--plugins`` option.
    
    Relative imports in the command files doesn't work. So you have to work 
    with absolute imports. Please take care about this.

    .. code-block: shell

       audible --plugins ./plugins plugin-cmds {cmd_name}

    """
    def list_commands(self, ctx):
        plugin_path = plugin_path_from_ctx(ctx)
        cmds = [x.stem[4:] for x in plugin_path.glob("cmd_*.py")]
        return sorted(cmds)

    def get_command(self, ctx, name):
        try:
            plugin_path = plugin_path_from_ctx(ctx)
            name = (plugin_path / ("cmd_" + name)).with_suffix(".py")
            spec = util.spec_from_file_location(name.stem, name)
            mod = util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except ImportError as exc:
            click.secho(
                f"Something went wrong during setup command: {name}\n",
                fg="red",
                bold=True
            )
            click.echo(exc)
            return
        return mod.cli


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(
    "plugin-cmds",
    cls=PluginCommands,
    context_settings=CONTEXT_SETTINGS
)
def cli():
    """Run custom one-file commands from plugin folder."""
