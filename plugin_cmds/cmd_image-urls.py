import audible
import click
from audible_cli.config import pass_session


@click.command("get-cover-urls")
@click.option(
    "--asin", "-a",
    multiple=False,
    help="asin of the audiobook"
)
@pass_session
def cli(session, asin):
    "Print out the image urls for different resolutions for a book"
    with audible.Client(auth=session.auth) as client:
        r = client.get(f"catalog/products/{asin}",
                       response_groups="media",
                       image_sizes=("1215, 408, 360, 882, 315, 570, 252, "
                                    "558, 900, 500"))
    images = r["product"]["product_images"]
    for res, url in images.items():
        click.echo(f"Resolution {res}: {url}")
