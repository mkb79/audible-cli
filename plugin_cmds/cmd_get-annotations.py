import click
from audible.exceptions import NotFoundError

from audible_cli.decorators import pass_client


@click.command("get-annotations")
@click.argument("asin")
@pass_client
async def cli(client, asin):
    url = "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"
    params = {"type": "AUDI", "key": asin}
    try:
        r = await client.get(url, params=params)
    except NotFoundError:
        click.echo(f"No annotations found for asin {asin}")
    else:
        click.echo(r)
