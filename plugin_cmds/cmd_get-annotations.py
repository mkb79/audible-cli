import logging

import click
from audible.exceptions import NotFoundError

from audible_cli.constants import CDE_ATTEMPTS, CDE_FIRST_DELAY
from audible_cli.decorators import pass_client
from audible_cli.utils import request_with_retry


logger = logging.getLogger("audible_cli.cmds.cmd_get-annotations")


@click.command("get-annotations")
@click.argument("asin")
@pass_client
async def cli(client, asin):
    url = "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"
    params = {
        "type": "AUDI",
        "key": asin
    }
    try:
        r = await request_with_retry(
            lambda: client.get(url, params=params),
            f"The annotations for {asin}",
            attempts=CDE_ATTEMPTS,
            first_delay=CDE_FIRST_DELAY,
        )
    except NotFoundError:
        # Not the product, so not on the stream the product goes out on.
        logger.info("No annotations found for asin %s", asin)
    else:
        click.echo(r)
