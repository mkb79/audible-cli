import click

from audible_cli.decorators import pass_client, timeout_option


@click.command("image-urls")
@click.argument("asin")
@timeout_option()
@pass_client()
async def cli(client, asin):
    """Print out the image urls for different resolutions for a book"""
    r = await client.get(
        f"catalog/products/{asin}",
        response_groups="media",
        image_sizes=(
            "1215, 408, 360, 882, 315, 570, 252, 558, 900, 500")
    )
    images = r["product"]["product_images"]
    for res, url in images.items():
        click.echo(f"Resolution {res}: {url}")
