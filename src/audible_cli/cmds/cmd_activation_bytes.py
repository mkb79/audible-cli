import logging

import click
from audible.activation_bytes import (
    extract_activation_bytes,
    fetch_activation_sign_auth
)

from ..config import pass_session


logger = logging.getLogger("audible_cli.cmds.cmd_activation_bytes")


@click.command("activation-bytes")
@click.option(
    "--reload", "-r",
    is_flag=True,
    help="Reload activation bytes and save to auth file.")
@pass_session
def cli(session, **options):
    """Get activation bytes."""
    auth = session.auth
    if auth.activation_bytes is None or options.get("reload"):
        logger.info("Fetching activation bytes from Audible server")
        ab = fetch_activation_sign_auth(auth)
        ab = extract_activation_bytes(ab)
        auth.activation_bytes = ab
        logger.info("Save activation bytes to file")
        auth.to_file()

    click.echo(auth.activation_bytes)
