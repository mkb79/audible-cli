import click
from audible_cli.decorators import pass_session, run_async, timeout_option


@click.command("image-urls")
@click.argument("asin")
@timeout_option()
@pass_session
@run_async()
async def cli(session, asin):
    "Print out the image urls for different resolutions for a book"
    async with session.get_client() as client:
        r = await client.get(
            f"catalog/products/{asin}",
            response_groups="media",
            image_sizes=("1215, 408, 360, 882, 315, 570, 252, "
                         "558, 900, 500")
        )
    images = r["product"]["product_images"]
    for res, url in images.items():
        click.echo(f"Resolution {res}: {url}")
