import importlib
import sys

import click

from . import cmd_quickstart
from .options import (
    auth_file_password_option,
    cli_config_option,
    profile_option,
    quickstart_config_option
)


class CliCommands(click.Group):
    def list_commands(self, ctx):
        return sorted(["manage", "download", "library"])

    def get_command(self, ctx, name):
        try:
            mod = importlib.import_module(f"audible_cli.cmd_{name}")
        except ImportError as exc:
            click.secho(
                f"Something went wrong during setup command: {name}\n",
                fg="red",
                bold=True
            )
            click.echo(exc)
            return
        return mod.cli


CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(cls=CliCommands, context_settings=CONTEXT_SETTINGS)
@cli_config_option
@profile_option
@auth_file_password_option
def cli():
    pass


@click.command(context_settings=CONTEXT_SETTINGS)
@quickstart_config_option
@click.pass_context
def quickstart(ctx):
    ctx.forward(cmd_quickstart.cli)


def main(*args, **kwargs):
    try:
        cli(*args, **kwargs)
    except KeyboardInterrupt:
        sys.exit('\nERROR: Interrupted by user')
