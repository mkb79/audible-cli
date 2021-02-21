"""
Core components for click_plugins
https://github.com/click-contrib/click-plugins
"""
import os
import pathlib
import sys
import traceback
from importlib import import_module

import click


def from_folder(plugin_dir):
    """
    A decorator to register external CLI commands to an instance of
    `click.Group()`.

    Parameters
    ----------
    plugin_dir : type
        Desc.

    Returns
    -------
    click.Group()
    """
    def decorator(group):
        if not isinstance(group, click.Group):
            raise TypeError("Plugins can only be attached to an instance of "
                            "click.Group()")

        pdir = pathlib.Path(plugin_dir)
        cmds = [x for x in pdir.glob("cmd_*.py")]
        sys.path.insert(0, str(pdir.resolve()))

        for cmd in cmds:
            mod_name = cmd.stem
            try:
                mod = import_module(mod_name)
                name = mod_name[4:] if mod.cli.name == "cli" else mod.cli.name
                group.add_command(mod.cli, name=name)
            except Exception:
                # Catch this so a busted plugin doesn't take down the CLI.
                # Handled by registering a dummy command that does nothing
                # other than explain the error.
                group.add_command(BrokenCommand(mod_name[4:]))

        return group

    return decorator


def from_entry_point(entry_point_group: str):
    """
    A decorator to register external CLI commands to an instance of
    `click.Group()`.

    Parameters
    ----------
    entry_point_group : iter
        An iterable producing one `pkg_resources.EntryPoint()` per iteration.

    Returns
    -------
    click.Group()
    """
    def decorator(group):
        if not isinstance(group, click.Group):
            print(type(group))
            raise TypeError("Plugins can only be attached to an instance of "
                            "click.Group()")

        for entry_point in entry_point_group or ():
            try:
                group.add_command(entry_point.load())
            except Exception:
                # Catch this so a busted plugin doesn't take down the CLI.
                # Handled by registering a dummy command that does nothing
                # other than explain the error.
                group.add_command(BrokenCommand(entry_point.name))

        return group

    return decorator


class BrokenCommand(click.Command):
    """
    Rather than completely crash the CLI when a broken plugin is loaded, this
    class provides a modified help message informing the user that the plugin
    is broken and they should contact the owner. If the user executes the
    plugin or specifies `--help` a traceback is reported showing the exception
    the plugin loader encountered.
    """

    def __init__(self, name):
        """
        Define the special help messages after instantiating a `click.Command()`.
        """

        click.Command.__init__(self, name)

        util_name = os.path.basename(sys.argv and sys.argv[0] or __file__)

        if os.environ.get('CLICK_PLUGINS_HONESTLY'):  # pragma no cover
            icon = u'\U0001F4A9'
        else:
            icon = u'\u2020'

        self.help = (
            "\nWarning: entry point could not be loaded. Contact "
            "its author for help.\n\n\b\n"
            + traceback.format_exc())
        self.short_help = (
            icon + " Warning: could not load plugin. See `%s %s --help`."
            % (util_name, self.name))

    def invoke(self, ctx):

        """
        Print the traceback instead of doing nothing.
        """

        click.echo(self.help, color=ctx.color)
        ctx.exit(1)

    def parse_args(self, ctx, args):
        return args
