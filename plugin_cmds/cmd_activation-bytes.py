import httpx
import click
from audible.activation_bytes import extract_activation_bytes
from audible_cli.config import pass_session


@click.command()
@click.option(
    "--save", "-s",
    is_flag=True,
    help="Save activation bytes to auth file.")
@pass_session
def cli(session, **options):
    "Get activation bytes"

    if session.auth.activation_bytes is None:
        click.echo("Activation bytes not found in auth file. Fetching online.")
        url = "https://www.audible.com/license/token"
        params = {
            "player_manuf": "Audible,iPhone",
            "action": "register",
            "player_model": "iPhone"
        }
        with httpx.Client(auth=session.auth) as client:    
            r = client.get(url, params=params)
        session.auth.activation_bytes = extract_activation_bytes(r.content)
        if options.get("save"):
            click.echo("Save activation bytes to file.")
            session.auth.to_file()

    click.echo(session.auth.activation_bytes)
