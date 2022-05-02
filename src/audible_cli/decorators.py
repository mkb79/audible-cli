import asyncio
import logging
from functools import partial, wraps

import click
import httpx
from packaging.version import parse

from .config import Session
from ._logging import _normalize_logger
from . import __version__


logger = logging.getLogger("audible_cli.options")

pass_session = click.make_pass_decorator(Session, ensure=True)


def run_async(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if hasattr(asyncio, "run"):
            logger.debug("Using asyncio.run ...")
            return asyncio.run(f(*args, ** kwargs))
        else:
            logger.debug("Using asyncio.run_until_complete ...")
            loop = asyncio.get_event_loop()

            if loop.is_closed():
                loop = asyncio.new_event_loop()

            try:
                return loop.run_until_complete(f(*args, ** kwargs))
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
    return wrapper


def wrap_async(f):
    """Wrap a synchronous function and runs them in an executor"""

    @wraps(f)
    async def wrapper(*args, loop=None, executor=None, **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()

        partial_func = partial(f, *args, **kwargs)
        return await loop.run_in_executor(executor, partial_func)

    return wrapper


def pass_client(func=None, **client_kwargs):
    def coro(f):
        @wraps(f)
        @pass_session
        @run_async
        async def wrapper(session, *args, **kwargs):
            client = session.get_client(**client_kwargs)
            async with client.session:
                return await f(*args, client, **kwargs)
        return wrapper

    if callable(func):
        return coro(func)

    return coro


def add_param_to_session(ctx: click.Context, param, value):
    """Add a parameter to :class:`Session` `param` attribute
    
    This is usually used as a callback for a click option
    """
    session = ctx.ensure_object(Session)
    session.params[param.name] = value
    return value


def version_option(func=None, **kwargs):
    def callback(ctx, param, value):
        if not value or ctx.resilient_parsing:
            return

        message = f"audible-cli, version {__version__}"
        click.echo(message, color=ctx.color, nl=False)

        url = "https://api.github.com/repos/mkb79/audible-cli/releases/latest"
        headers = {"Accept": "application/vnd.github.v3+json"}
        logger.debug(f"Requesting Github API for latest release information")
        try:
            response = httpx.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()
        except Exception as e:
            logger.error(e)
            click.Abort()

        content = response.json()

        current_version = parse(__version__)
        latest_version = parse(content["tag_name"])

        html_url = content["html_url"]
        if latest_version > current_version:
            click.echo(
                f" (update available)\nVisit {html_url} "
                f"for information about the new release.",
                color=ctx.color
            )
        else:
            click.echo(" (up-to-date)", color=ctx.color)

        ctx.exit()

    kwargs.setdefault("is_flag", True)
    kwargs.setdefault("expose_value", False)
    kwargs.setdefault("is_eager", True)
    kwargs.setdefault("help", "Show the version and exit.")
    kwargs["callback"] = callback

    option = click.option("--version", **kwargs)

    if callable(func):
        return option(func)

    return option


def profile_option(func=None, **kwargs):
    kwargs.setdefault("callback", add_param_to_session)
    kwargs.setdefault("expose_value", False)
    kwargs.setdefault(
        "help",
        "The profile to use instead primary profile (case sensitive!)."
    )

    option = click.option("--profile", "-P", **kwargs)

    if callable(func):
        return option(func)

    return option


def password_option(func=None, **kwargs):
    kwargs.setdefault("callback", add_param_to_session)
    kwargs.setdefault("expose_value", False)
    kwargs.setdefault("help", "The password for the profile auth file.")

    option = click.option("--password", "-p", **kwargs)

    if callable(func):
        return option(func)

    return option


def verbosity_option(func=None, *, cli_logger=None, **kwargs):
    """A decorator that adds a `--verbosity, -v` option to the decorated
    command.
    Keyword arguments are passed to
    the underlying ``click.option`` decorator.
    """
    def callback(ctx, param, value):
        x = getattr(logging, value.upper(), None)
        if x is None:
            raise click.BadParameter(
                f"Must be CRITICAL, ERROR, WARNING, INFO or DEBUG, "
                f"not {value}"
            )
        cli_logger.setLevel(x)

    kwargs.setdefault("default", "INFO")
    kwargs.setdefault("metavar", "LVL")
    kwargs.setdefault("expose_value", False)
    kwargs.setdefault(
        "help", "Either CRITICAL, ERROR, WARNING, "
        "INFO or DEBUG. [default: INFO]"
    )
    kwargs.setdefault("is_eager", True)
    kwargs.setdefault("callback", callback)

    cli_logger = _normalize_logger(cli_logger)

    option = click.option("--verbosity", "-v", **kwargs)

    if callable(func):
        return option(func)

    return option


def timeout_option(func=None, **kwargs):
    def callback(ctx: click.Context, param, value):
        if value == 0:
            value = None
        session = ctx.ensure_object(Session)
        session.params[param.name] = value
        return value

    kwargs.setdefault("type", click.INT)
    kwargs.setdefault("default", 10)
    kwargs.setdefault("show_default", True)
    kwargs.setdefault(
        "help", ("Increase the timeout time if you got any TimeoutErrors. "
                 "Set to 0 to disable timeout.")
    )
    kwargs.setdefault("callback", callback)
    kwargs.setdefault("expose_value", False)

    option = click.option("--timeout", **kwargs)

    if callable(func):
        return option(func)

    return option


def bunch_size_option(func=None, **kwargs):
    kwargs.setdefault("type", click.IntRange(10, 1000))
    kwargs.setdefault("default", 1000)
    kwargs.setdefault("show_default", True)
    kwargs.setdefault(
        "help", ("How many library items should be requested per request. A "
                 "lower size results in more requests to get the full library. "
                 "A higher size can result in a TimeOutError on low internet "
                 "connections.")
    )
    kwargs.setdefault("callback", add_param_to_session)
    kwargs.setdefault("expose_value", False)

    option = click.option("--bunch-size", **kwargs)

    if callable(func):
        return option(func)

    return option
