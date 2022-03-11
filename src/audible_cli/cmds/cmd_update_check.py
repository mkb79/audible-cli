import logging

import click
import httpx
from packaging.version import parse

from .. import __version__


logger = logging.getLogger("audible_cli.cmds.update-check")


@click.command("update-check")
def cli():
    """Check, if an update for audible-cli is available"""
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
            f"Update available. Please visit {html_url} "
            f"for download and release information."
        )
    else:
        click.echo("Up to date.")
