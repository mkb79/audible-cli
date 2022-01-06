import click
from audible.activation_bytes import (
    extract_activation_bytes,
    fetch_activation_sign_auth
)

from ..config import pass_session


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
        click.echo("Fetching activation bytes online.", err=True)
        ab = fetch_activation_sign_auth(auth)
        ab = extract_activation_bytes(ab)
        auth.activation_bytes = ab
        click.echo("Save activation bytes to file.", err=True)
        auth.to_file()

    click.echo(auth.activation_bytes)

