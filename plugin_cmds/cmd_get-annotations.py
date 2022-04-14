import click

from audible_cli.decorators import pass_session, run_async


@click.command("get-annotations")
@click.argument("asin")
@pass_session
@run_async()
async def cli(session, asin):
    async with session.get_client() as client:
        url = f"https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"
        params = {
            "type": "AUDI",
            "key": asin
        }
        r = await client.get(url, params=params)
        click.echo(r)
