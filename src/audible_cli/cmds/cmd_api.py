"""Send one request to the Audible API and print what came back.

This command is for the API and its JSON. An endpoint is a path, never a
URL: the host follows from the profile or from `--country-code`, so there
is one place where the marketplace is decided. For the other hosts
audible-cli talks to, and for answers that are not JSON, there is
`audible request`.
"""

import json
import logging
import pathlib
from typing import Any, NoReturn

import click
import httpx
from audible.client import raise_for_status
from click.core import ParameterSource

from ..config import Session
from ..constants import AVAILABLE_MARKETPLACES
from ..decorators import pass_session, run_async, timeout_option
from ..exceptions import AudibleCliException


logger = logging.getLogger("audible_cli.cmds.cmd_api")


class ApiPath(click.ParamType[tuple[str, list[tuple[str, str]]]]):
    """An API path, with an optional query string.

    The query is read into pairs and sent as pairs, which means it is
    decoded as UTF-8 and encoded again. A percent escape that is not
    UTF-8, such as `%FF`, does not survive that; the path does, because
    it is passed on as it was written.
    """

    name = "path"

    def convert(
        self,
        value: Any,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> tuple[str, list[tuple[str, str]]]:
        """Take the path apart, and refuse anything carrying a host.

        Args:
            value: What was typed.
            param: The parameter being converted.
            ctx: The context it belongs to.

        Returns:
            The path without its query, and the query as a list of pairs.
        """
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL as error:
            self.fail(f"{value!r} is not a path: {error}", param, ctx)

        if url.scheme or url.netloc:
            self.fail(
                f"{value!r} is a URL. This command takes a path, such as "
                f"'library' -- the host comes from the profile or "
                f"--country-code. Use 'audible request' for a URL.",
                param,
                ctx,
            )

        if url.fragment:
            self.fail(
                f"{value!r} carries a fragment, which is never sent to a "
                f"server. Write it as %23 if it belongs to the path.",
                param,
                ctx,
            )

        # `url.path` decodes percent escapes, which would turn `%2F` in an
        # asin into a path separator. The raw form carries the query too,
        # so it is cut off here rather than read from `url.path`.
        path = url.raw_path.split(b"?", 1)[0].decode()

        # httpx reads an empty path as `/`, so `api ""` and `api "?a=1"`
        # would ask for the root of the API rather than for an endpoint.
        if path == "/":
            self.fail("no endpoint. Give a path such as 'library'", param, ctx)

        query = list(url.params.multi_items())

        if any(not key for key, _ in query):
            self.fail(f"{value!r} has a query parameter without a name", param, ctx)

        return path, query


class QueryPair(click.ParamType[tuple[str, str]]):
    """One `key=value` query parameter."""

    name = "key[=value]"

    def convert(
        self,
        value: Any,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> tuple[str, str]:
        """Split at the first `=` only, so a value may contain more.

        A key on its own is a query parameter without a value. httpx sends
        it as `key=`; a bare `key` cannot be expressed through it.

        Args:
            value: What was typed.
            param: The parameter being converted.
            ctx: The context it belongs to.

        Returns:
            The key and the value, which may be empty.
        """
        key, _, val = value.partition("=")

        if not key:
            self.fail(f"{value!r} has no name", param, ctx)

        return key, val


#: Header names a caller may not set. They carry the authentication, or
#: they describe a body this command always writes itself: the body is
#: JSON, and the client says so.
REFUSED_HEADERS = frozenset(
    {
        "authorization",
        "content-length",
        "content-type",
        "host",
        "proxy-authorization",
        "transfer-encoding",
        "x-adp-alg",
        "x-adp-signature",
        "x-adp-token",
        "x-amz-access-token",
    }
)


class HeaderPair(click.ParamType[tuple[str, str]]):
    """One `Name: value` request header."""

    name = "name: value"

    def convert(
        self,
        value: Any,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> tuple[str, str]:
        """Split at the first colon, and refuse what is not ours to set.

        Args:
            value: What was typed.
            param: The parameter being converted.
            ctx: The context it belongs to.

        Returns:
            The header name and its value.
        """
        name, sep, val = value.partition(":")
        name = name.strip()

        if not sep or not name:
            self.fail(f"{value!r} is not 'Name: value'", param, ctx)

        if name.lower() in REFUSED_HEADERS:
            self.fail(
                f"{name} is set by audible-cli itself and cannot be given here",
                param,
                ctx,
            )

        return name, val.strip()


def load_json(text: str) -> Any:
    """Read JSON, and only JSON.

    Python also reads `NaN` and `Infinity`, which no JSON parser on the
    other side has to understand and which cannot be serialised back.
    They are rejected here rather than half way through the request.

    Args:
        text: The document to read.

    Returns:
        Whatever the JSON describes.

    Raises:
        ValueError: If `text` is not JSON.
    """

    def refuse(constant: str) -> NoReturn:
        raise ValueError(f"{constant} is not part of json")

    return json.loads(text, parse_constant=refuse)


class JsonBody(click.ParamType[Any]):
    """A request body, given as JSON."""

    name = "json"

    def convert(
        self,
        value: Any,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> Any:
        """Parse the body before anything else happens.

        Args:
            value: What was typed.
            param: The parameter being converted.
            ctx: The context it belongs to.

        Returns:
            Whatever the JSON describes.
        """
        try:
            return load_json(value)
        except ValueError as error:
            self.fail(f"not valid JSON: {error}", param, ctx)


def resolve_body(options: dict[str, Any]) -> tuple[Any, bool]:
    """Work out the body to send, and whether one was asked for.

    `--body null` parses to None, and so does an option nobody gave, so
    the two are told apart by where the value came from rather than by
    what it is. Neither ends up on the wire: the client hands the body to
    httpx as `json=`, and `json=None` writes an empty body, not `null`.

    A file is read here, before the credentials are. With `-` the body is
    standard input, and a password prompt would find it exhausted.

    Args:
        options: The parsed command line.

    Returns:
        The body, and whether the caller gave one.

    Raises:
        click.UsageError: If both ways of giving a body were used, if the
            file is not JSON, or if the body is `null`.
    """
    context = click.get_current_context()
    given = context.get_parameter_source("body") is not ParameterSource.DEFAULT
    body = options["body"]

    if options["body_file"] is not None:
        if given:
            raise click.UsageError("--body and --body-file cannot both be given")
        try:
            body = load_json(options["body_file"].read())
        except ValueError as error:
            raise click.UsageError(
                f"{options['body_file'].name} is not valid JSON: {error}"
            ) from error
        given = True

    if given and body is None:
        raise click.UsageError(
            "`null` cannot be sent as a body. Leave the option out to send "
            "none, or use 'audible request' for a body of your own."
        )

    return body, given


@click.command("api")
@click.argument("endpoint", type=ApiPath())
@click.option(
    "--method",
    "-m",
    type=click.Choice(["GET", "POST", "DELETE", "PUT"], case_sensitive=False),
    default="GET",
    help="The http request method",
    show_default=True,
)
@click.option(
    "--query",
    "-q",
    type=QueryPair(),
    multiple=True,
    help="A query parameter (e.g. num_results=5). Only one parameter "
    "per option. Multiple options of this type are allowed, including "
    "the same key more than once.",
)
@click.option(
    "--param",
    type=QueryPair(),
    multiple=True,
    deprecated="use --query instead",
    help="An earlier spelling of --query.",
)
@click.option(
    "--header",
    "-H",
    type=HeaderPair(),
    multiple=True,
    help="A request header (e.g. 'Accept-Language: en-US'). Repeatable, "
    "including the same name twice. The headers that carry the "
    "authentication or describe the body belong to the client and "
    "are refused here.",
)
@click.option(
    "--body",
    "-b",
    type=JsonBody(),
    help="The json formatted body to send",
)
@click.option(
    "--body-file",
    type=click.File("r", encoding="utf-8"),
    help="Read the json formatted body from a file. `-` reads it from "
    "standard input, which then cannot answer a password prompt.",
)
@click.option(
    "--indent",
    "-i",
    type=click.IntRange(min=0),
    help="pretty-printed output with indent level",
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["json"]),
    default="json",
    deprecated="the answer is json, and json is what this writes",
    help="An earlier choice between json and a Python dict.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=pathlib.Path),
    help="Write the response to a file, as UTF-8. Worth having over a shell "
    "redirect on Windows, where `>` writes UTF-16 or the console "
    "codepage.",
)
@click.option(
    "--dump-header",
    "-D",
    type=click.Path(path_type=pathlib.Path),
    help="Write the status line and the response headers to a file. The API "
    "returns `continuation-token` and `total-count` there, which is how "
    "a script pages through a long answer.",
)
@click.option(
    "--country-code",
    "-c",
    type=click.Choice(AVAILABLE_MARKETPLACES),
    help="Requested Audible marketplace. If not set, the country code for "
    "the current profile is used.",
)
@timeout_option
@pass_session
@run_async
async def cli(session: Session, **options: Any) -> None:
    """Send requests to an Audible API endpoint.

    ENDPOINT is a path such as `library` or `1.0/library`, and may carry a
    query string of its own; `--query` adds to it rather than replacing
    it. The response has to be JSON; anything else is reported as a
    failure rather than quoted into one.

    Take a look at
    https://audible.readthedocs.io/en/latest/misc/external_api.html for known
    endpoints and parameters.
    """
    path, embedded = options["endpoint"]
    body, has_body = resolve_body(options)

    # The client offers one method per verb rather than a single `request`,
    # and the two that carry a body take it positionally.
    method = options["method"].upper()
    carried = [body] if method in ("POST", "PUT") else []

    if has_body and not carried:
        raise click.UsageError(f"a {method} carries no body")

    # A query given in the endpoint and one given as options are both the
    # caller's, so they are sent together rather than one replacing the
    # other. Repeats survive: the API reads `asins=A&asins=B` as two.
    params = embedded + list(options["query"]) + list(options["param"])
    headers = list(options["header"]) or None

    client = session.get_client(country_code=options["country_code"])
    send = getattr(client, method.lower())

    async with client.session:
        try:
            response = await send(
                path,
                *carried,
                params=params,
                headers=headers,
                response_callback=lambda resp: resp,
            )
        except Exception as error:
            raise AudibleCliException(str(error)) from error

    # Written before the status is judged: a failed call is exactly when
    # somebody wants to see what came back.
    if options["dump_header"] is not None:
        head = (
            f"{response.http_version} {response.status_code} {response.reason_phrase}\n"
        )
        head += "".join(
            f"{name}: {value}\n" for name, value in response.headers.items()
        )
        options["dump_header"].write_text(head, encoding="utf-8")
        logger.info("Headers saved to %s", options["dump_header"].resolve())

    # The library turns a status into one of its own exceptions. Left
    # alone it would reach `main()` as an unexpected error and exit 3.
    try:
        raise_for_status(response)
    except Exception as error:
        raise AudibleCliException(str(error)) from error

    try:
        answer = response.json()
    except ValueError as error:
        raise AudibleCliException(
            f"The answer is not JSON, which is what this command is for. "
            f"Use 'audible request' to see it as it came: {error}"
        ) from error

    answer = json.dumps(answer, indent=options["indent"])

    output = options["output"]
    if output is None:
        click.echo(answer)
    else:
        output.write_text(answer, encoding="utf-8")
        logger.info("Output saved to %s", output.resolve())
