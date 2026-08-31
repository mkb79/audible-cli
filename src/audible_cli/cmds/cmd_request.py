"""Send one request to a host audible-cli talks to, and show the answer.

This is the raw command. The endpoint is a whole URL, the body leaves as
it was written, and the answer arrives as it was sent: no JSON is parsed,
no status is second-guessed beyond the exit code. For the Audible API and
its JSON there is `audible api`, which knows more and asks for less.

The URL has to name a host this tool has business with. The request
carries the credentials of the profile, so the list of hosts is the list
of who may be handed them.
"""

import contextlib
import logging
import pathlib
from typing import Any, BinaryIO
from urllib.parse import urlencode

import click
import httpx

from .._params import AUTHENTICATION_HEADERS, HeaderPair, QueryPair
from ..config import Session
from ..constants import ALLOWED_REQUEST_HOSTS, CDE_HOST
from ..decorators import pass_session, run_async, timeout_option
from ..exceptions import AudibleCliException


logger = logging.getLogger("audible_cli.cmds.cmd_request")


class RequestUrl(click.ParamType[httpx.URL]):
    """A whole URL, on a host that may see the credentials."""

    name = "url"

    def convert(
        self,
        value: Any,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> httpx.URL:
        """Refuse what may not be asked, and keep the rest as it is.

        Args:
            value: What was typed.
            param: The parameter being converted.
            ctx: The context it belongs to.

        Returns:
            The URL, unchanged.
        """
        try:
            url = httpx.URL(value)
            # Reading the host is what decodes a punycode name, and an
            # unusable one raises from `idna`, not from httpx.
            host = url.host
        except (httpx.InvalidURL, UnicodeError) as error:
            self.fail(f"{value!r} is not a url: {error}", param, ctx)

        if not url.is_absolute_url:
            self.fail(
                f"{value!r} is not a whole url. This command needs the host "
                f"spelled out; use 'audible api' to send a path to the "
                f"Audible API.",
                param,
                ctx,
            )

        if url.scheme != "https":
            self.fail(
                f"{url.scheme} would carry the credentials in the clear. "
                f"Only https is sent.",
                param,
                ctx,
            )

        if url.userinfo:
            self.fail(
                f"{value!r} carries a name and password of its own, which "
                f"the profile's credentials would not replace",
                param,
                ctx,
            )

        if url.port is not None:
            self.fail(f"{host} is not asked on port {url.port}", param, ctx)

        if host not in ALLOWED_REQUEST_HOSTS:
            self.fail(
                f"{host} is not a host audible-cli talks to, and the "
                f"request would hand it the credentials of the profile. "
                f"Allowed are api, www, cds and api.amazon of a "
                f"marketplace, and {CDE_HOST}.",
                param,
                ctx,
            )

        return url


class HttpMethod(click.ParamType[str]):
    """The request method, in the upper case the wire uses."""

    name = "method"

    def convert(
        self,
        value: Any,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str:
        """Take any method a host might understand.

        The list is not fixed here: this command is the raw one, and a
        host that answers PATCH or OPTIONS is not the client's business.

        Args:
            value: What was typed.
            param: The parameter being converted.
            ctx: The context it belongs to.

        Returns:
            The method in upper case.
        """
        method = value.upper()

        if not method.isalpha():
            self.fail(f"{value!r} is not an http method", param, ctx)

        return method


def extend_query(url: httpx.URL, pairs: tuple[tuple[str, str], ...]) -> httpx.URL:
    """Add query parameters without disturbing the ones already there.

    Reading a query into pairs and writing it back decodes and encodes
    it again: `%FF` turns into the replacement character, `%20` into `+`,
    and a key without a value grows one. The delivery urls this command
    is for are signed over exactly those bytes, so the new pairs are
    appended to the raw query instead of merged into a parsed one.

    Args:
        url: What was asked for.
        pairs: What `--query` added.

    Returns:
        The URL with the pairs appended.
    """
    raw = url.raw_path
    separator = b"&" if b"?" in raw else b"?"
    return url.copy_with(raw_path=raw + separator + urlencode(list(pairs)).encode())


def resolve_body(options: dict[str, Any]) -> bytes | None:
    """Work out the bytes to send as the body.

    A file is read here, before the credentials are. With `-` the body is
    standard input, and a password prompt would find it exhausted.

    Args:
        options: The parsed command line.

    Returns:
        The body, or None when there is none.

    Raises:
        click.UsageError: If both ways of giving a body were used.
    """
    body: str | None = options["body"]
    body_file: BinaryIO | None = options["body_file"]

    if body is not None and body_file is not None:
        raise click.UsageError("--body and --body-file cannot both be given")

    if body is not None:
        return body.encode()

    if body_file is not None:
        return body_file.read()

    return None


def describe(response: httpx.Response) -> str:
    """Write the status line and the headers the way a dump file has them.

    Args:
        response: What came back.

    Returns:
        One line for the status and one for each header.
    """
    status = (
        f"{response.http_version} {response.status_code} {response.reason_phrase}\n"
    )
    # `items()` would join a header that came twice into one line, and
    # two `set-cookie` are not one `set-cookie` with a comma in it.
    return status + "".join(
        f"{name}: {value}\n" for name, value in response.headers.multi_items()
    )


@click.command("request")
@click.argument("url", type=RequestUrl())
@click.option(
    "--method",
    "-m",
    type=HttpMethod(),
    default="GET",
    help="The http request method. Whatever the host understands: GET, "
    "POST, PUT, DELETE, HEAD, PATCH, OPTIONS.",
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
    "--header",
    "-H",
    type=HeaderPair(AUTHENTICATION_HEADERS),
    multiple=True,
    help="A request header (e.g. 'Accept: text/html'). Repeatable, "
    "including the same name twice. The headers that carry the "
    "authentication belong to the client and are refused here.",
)
@click.option(
    "--body",
    "-b",
    help="The body to send, as it should arrive. The client says "
    "`Content-Type: application/json` unless a header says otherwise.",
)
@click.option(
    "--body-file",
    type=click.File("rb"),
    help="Read the body from a file, byte for byte. `-` reads it from "
    "standard input, which then cannot answer a password prompt.",
)
@click.option(
    "--include",
    "-i",
    is_flag=True,
    help="Write the status line and the response headers in front of the "
    "body, wherever the body goes.",
)
@click.option(
    "--dump-header",
    "-D",
    type=click.Path(path_type=pathlib.Path),
    help="Write the status line and the response headers to a file, also "
    "when the answer was an error.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=pathlib.Path),
    help="Write the body to a file, byte for byte.",
)
@timeout_option
@pass_session
@run_async
async def cli(session: Session, **options: Any) -> None:
    """Send one request to a host audible-cli talks to.

    URL is a whole https url, and its host has to be one of those this
    tool has business with: `api`, `www`, `cds` and `api.amazon` of a
    marketplace, and the delivery host `cde-ta-g7g.amazon.com`. The
    request carries the credentials of the profile, which is why the
    other hosts are refused rather than tried.

    The answer is written as it arrived. A redirect is shown, not
    followed, and an error status is written out before it ends the
    command.
    """
    url = options["url"]
    body = resolve_body(options)
    method = options["method"]
    headers = list(options["header"]) or None
    output = options["output"]

    if options["query"]:
        url = extend_query(url, options["query"])

    client = session.get_client()

    async with client.session:
        try:
            # Streamed, so that a file from the delivery host is written
            # as it arrives rather than held whole in memory first.
            async with client.raw_request(
                method, str(url), stream=True, headers=headers, content=body
            ) as response:
                head = describe(response)

                if options["dump_header"] is not None:
                    options["dump_header"].write_text(head, encoding="utf-8")
                    logger.info("Headers saved to %s", options["dump_header"].resolve())

                if output is None:
                    sink = contextlib.nullcontext(click.get_binary_stream("stdout"))
                else:
                    sink = output.open("wb")

                with sink as answer:
                    if options["include"]:
                        answer.write(head.encode("utf-8") + b"\n")

                    async for chunk in response.aiter_bytes():
                        answer.write(chunk)
        except httpx.RequestError as error:
            raise AudibleCliException(f"{method} {url} failed: {error}") from error

    if output is not None:
        logger.info("Output saved to %s", output.resolve())

    # Written first, then reported: the body of a refusal is what says
    # why it was refused.
    if response.is_error:
        raise AudibleCliException(f"{response.status_code} {response.reason_phrase}")
