import logging
import sys
from pkg_resources import iter_entry_points

import click

from .cmds import build_in_cmds, cmd_quickstart
from .config import get_plugin_dir
from .constants import PLUGIN_ENTRY_POINT
from .decorators import (
    password_option,
    profile_option,
    verbosity_option,
    version_option
)
from .exceptions import AudibleCliException
from ._logging import click_basic_config
from . import plugins


logger = logging.getLogger("audible_cli")
click_basic_config(logger)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@plugins.from_folder(get_plugin_dir())
@plugins.from_entry_point(iter_entry_points(PLUGIN_ENTRY_POINT))
@build_in_cmds
@click.group(context_settings=CONTEXT_SETTINGS)
@profile_option
@password_option
@version_option
@verbosity_option(cli_logger=logger)
def cli():
    """Entrypoint for all other subcommands and groups."""


@click.command(context_settings=CONTEXT_SETTINGS)
@click.pass_context
@version_option
@verbosity_option(cli_logger=logger)
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
