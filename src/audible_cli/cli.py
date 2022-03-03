import logging
import sys
from pkg_resources import iter_entry_points

import click
import click_log

from .cmds import build_in_cmds, cmd_quickstart
from .config import (
    get_plugin_dir,
    add_param_to_session
)
from .constants import PLUGIN_ENTRY_POINT
from .exceptions import AudibleCliException
from . import __version__, plugins


logger = logging.getLogger("audible_cli")
click_log.basic_config(logger)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@plugins.from_folder(get_plugin_dir())
@plugins.from_entry_point(iter_entry_points(PLUGIN_ENTRY_POINT))
@build_in_cmds()
@click.group(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--profile",
    "-P",
    callback=add_param_to_session,
    expose_value=False,
    help="The profile to use instead primary profile (case sensitive!)."
)
@click.option(
    "--password",
    "-p",
    callback=add_param_to_session,
    expose_value=False,
    help="The password for the profile auth file."
)
@click.version_option(__version__)
@click_log.simple_verbosity_option(logger)
def cli():
    """Entrypoint for all other subcommands and groups."""


@click.command(context_settings=CONTEXT_SETTINGS)
@click.pass_context
@click.version_option(__version__)
@click_log.simple_verbosity_option(logger)
def quickstart(ctx):
    """Entrypoint for the quickstart command"""
    try:
        sys.exit(ctx.forward(cmd_quickstart.cli))
    except click.Abort:
        logger.error("Aborted")
        sys.exit(1)
    except AudibleCliException as e:
        logger.error(e)
        sys.exit(2)
    except Exception:
        logger.exception("Uncaught Exception")
        sys.exit(3)


def main(*args, **kwargs):
    try:
        sys.exit(cli(*args, **kwargs))
    except click.Abort:
        logger.error("Aborted")
        sys.exit(1)
    except AudibleCliException as e:
        logger.error(e)
        sys.exit(2)
    except Exception:
        logger.exception("Uncaught Exception")
        sys.exit(3)
