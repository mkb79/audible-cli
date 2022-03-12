import logging
import sys
from pkg_resources import iter_entry_points

import click
import httpx
from packaging.version import parse

from .cmds import build_in_cmds, cmd_quickstart
from .config import (
    get_plugin_dir,
    add_param_to_session
)
from .constants import PLUGIN_ENTRY_POINT
from .exceptions import AudibleCliException
from ._logging import click_basic_config, click_verbosity_option
from . import __version__, plugins


logger = logging.getLogger("audible_cli")
click_basic_config(logger)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


def version_option(**kwargs):
    def callback(ctx, param, value):
        if not value or ctx.resilient_parsing:
            return

        message = f"audible-cli, version {__version__}"
        click.echo(message, color=ctx.color, nl=False)

        url = "https://api.github.com/repos/mkb79/audible-cli/releases/latest"
        headers = {"Accept": "application/vnd.github.v3+json"}
        logger.debug(f"Requesting Github API for latest release information")
        try:
            response = httpx.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()
        except Exception as e:
            logger.error(e)
            click.Abort()

        content = response.json()

        current_version = parse(__version__)
        latest_version = parse(content["tag_name"])

        html_url = content["html_url"]
        if latest_version > current_version:
            click.echo(
                f" (update available)\nVisit {html_url} "
                f"for information about the new release.",
                color=ctx.color
            )
        else:
            click.echo(" (up-to-date)", color=ctx.color)

        ctx.exit()

    kwargs.setdefault("is_flag", True)
    kwargs.setdefault("expose_value", False)
    kwargs.setdefault("is_eager", True)
    kwargs.setdefault("help", "Show the version and exit.")
    kwargs["callback"] = callback
    return click.option("--version", **kwargs)


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
@version_option()
@click_verbosity_option(logger)
def cli():
    """Entrypoint for all other subcommands and groups."""


@click.command(context_settings=CONTEXT_SETTINGS)
@click.pass_context
@version_option()
@click_verbosity_option(logger)
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
