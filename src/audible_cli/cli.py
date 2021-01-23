import sys

import click

from .cmds import (
    cmd_activation_bytes,
    cmd_download,
    cmd_library,
    cmd_manage,
    cmd_plugins,
    cmd_quickstart
)
from .options import (
    auth_file_password_option,
    cli_config_option,
    plugin_cmds_option,
    profile_option,
    quickstart_config_option
)
from . import __version__

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS)
@cli_config_option
@profile_option
@auth_file_password_option
@plugin_cmds_option
@click.version_option(__version__)
def cli():
    """Entrypoint for all other subcommands and groups."""


cli_cmds = [
    cmd_activation_bytes.cli,
    cmd_download.cli,
    cmd_library.cli,
    cmd_manage.cli,
    cmd_plugins.cli
]

[cli.add_command(cmd) for cmd in cli_cmds]


@click.command(context_settings=CONTEXT_SETTINGS)
@quickstart_config_option
@click.pass_context
def quickstart(ctx):
    """Entrypoint for the quickstart command"""
    try:
        sys.exit(ctx.forward(cmd_quickstart.cli))
    except KeyboardInterrupt:
        sys.exit("\nERROR: Interrupted by user")


def main(*args, **kwargs):
    try:
        sys.exit(cli(*args, **kwargs))
    except KeyboardInterrupt:
        sys.exit("\nERROR: Interrupted by user")
