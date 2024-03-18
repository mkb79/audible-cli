"""
Core components for click_plugins
https://github.com/click-contrib/click-plugins
"""
import os
import pathlib
import sys
import traceback
from importlib import import_module
from typing import Union

import click


def from_folder(plugin_dir: Union[str, pathlib.Path]):
    """
    A decorator to register external CLI commands to an instance of
    `click.Group()`.

    Parameters
    ----------
    plugin_dir : str
        Desc.

    Returns
    -------
    click.Group()
    """
    def decorator(group):
        if not isinstance(group, click.Group):
            raise TypeError(
                "Plugins can only be attached to an instance of click.Group()"
            )

        plugin_path = pathlib.Path(plugin_dir).resolve()
        sys.path.insert(0, str(plugin_path))

        for cmd_path in plugin_path.glob("cmd_*.py"):
            cmd_path_stem = cmd_path.stem
            try:
                mod = import_module(cmd_path_stem)
                cmd = mod.cli
                if cmd.name == "cli":
                    # if no name given to the command, use the filename
                    # excl. starting cmd_ as name
                    cmd.name = cmd_path_stem[4:]
                group.add_command(cmd)

                orig_help = cmd.help or ""
                new_help = (
                    f"(P) {orig_help}\n\nPlugin loaded from file: {str(cmd_path)}"
                )
                cmd.help = new_help
            except Exception:  # noqa
                # Catch this so a busted plugin doesn't take down the CLI.
                # Handled by registering a dummy command that does nothing
                # other than explain the error.
                group.add_command(BrokenCommand(cmd_path_stem[4:]))

        return group

    return decorator


def from_entry_point(entry_point_group):
    """
    A decorator to register external CLI commands to an instance of
    `click.Group()`.

    Parameters
    ----------
    entry_point_group : list
        A list producing one `pkg_resources.EntryPoint()` per iteration.

    Returns
    -------
    click.Group()
    """
    def decorator(group):
        if not isinstance(group, click.Group):
            raise TypeError(
                "Plugins can only be attached to an instance of click.Group()"
            )

        for entry_point in entry_point_group or ():
            try:
                cmd = entry_point.load()
                dist_name = entry_point.dist.name
                if cmd.name == "cli":
                    # if no name given to the command, use the filename
                    # excl. starting cmd_ as name
                    cmd.name = dist_name
                group.add_command(cmd)

                orig_help = cmd.help or ""
                new_help = f"(P) {orig_help}\n\nPlugin loaded from package: {dist_name}"
                cmd.help = new_help
            except Exception:  # noqa
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
    is broken, and they should contact the owner. If the user executes the
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
