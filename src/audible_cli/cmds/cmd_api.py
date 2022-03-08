import json
import logging
import pathlib
import sys

import click
from audible import Client

from ..config import pass_session


logger = logging.getLogger("audible_cli.cmds.cmd_api")


@click.command("api")
@click.argument("endpoint")
@click.option(
    "--method", "-m",
    type=click.Choice(
        ["GET", "POST", "DELETE", "PUT"],
        case_sensitive=False
    ),
    default="GET",
    help="The http request method",
    show_default=True,
)
@click.option(
    "--param", "-p",
    help="A query parameter (e.g. num_results=5). Only one parameter "
         "per option. Multiple options of this type are allowed.",
    multiple=True
)
@click.option(
    "--body", "-b",
    help="The json formatted body to send"
)
@click.option(
    "--indent", "-i",
    help="pretty-printed output with indent level"
)
@click.option(
    "--format", "-f",
    type=click.Choice(
        ["json", "dict"],
    ),
    default="json",
    help="The output format. If 'dict', the output is a unformatted Python dict.",
    show_default=True,
)
@click.option(
    "--output", "-o",
    type=click.Path(path_type=pathlib.Path),
    help="Output the response to a file"
)
@click.option(
    "--country-code", "-c",
    type=click.Choice(
        ["us", "ca", "uk", "au", "fr", "de", "es", "jp", "it", "in"]
    ),
    help="Requested Audible marketplace. If not set, the country code for "
         "the current profile is used."
)
@pass_session
def cli(session, **options):
    """Send requests to an Audible API endpoint
    
    Take a look at 
    https://audible.readthedocs.io/en/latest/misc/external_api.html for known 
    endpoints and parameters.
    """
    auth = session.auth
    endpoint = options.get("endpoint")
    method = options.get("method")

    params = {}
    for p in options.get("param"):
        k, v = p.split("=")
        params[k] = v

    body = options.get("body")
    if body is not None:
        body = json.loads(body)

    indent = options.get("indent")
    if indent is not None:
        try:
            indent = int(indent)
        except ValueError:
            pass

    output_format = options.get("format")
    output_filename = options.get("output")
    country_code = options.get("country_code")

    try:
        with Client(auth=auth, country_code=country_code) as client:
            r = client._request(method, endpoint, params=params, json=body)
    except Exception as e:
        logger.error(e)            
        sys.exit(1)

    if output_format == "json":
        r = json.dumps(r, indent=indent)

    if output_filename is None:
        click.echo(r)
    else:
        output_filename.write_text(r)
        logger.info(f"Output saved to {output_filename.resolve()}")
