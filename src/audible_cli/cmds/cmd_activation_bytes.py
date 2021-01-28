import click
from audible.activation_bytes import (
    extract_activation_bytes,
    fetch_activation_sign_auth
)

from ..config import pass_session


@click.command("activation-bytes")
@click.option(
    "--save", "-s",
    is_flag=True,
    help="Save activation bytes to auth file.")
@pass_session
def cli(session, **options):
    """Get activation bytes."""
    auth = session.auth
    if auth.activation_bytes is None:
        click.echo("Activation bytes not found in auth file. Fetching online.")
        ab = fetch_activation_sign_auth(auth)
        ab = extract_activation_bytes(ab)
        auth.activation_bytes = ab
        if options.get("save"):
            click.echo("Save activation bytes to file.")
            auth.to_file()

    click.echo(auth.activation_bytes)
