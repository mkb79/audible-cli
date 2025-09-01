import asyncio
import logging
from functools import partial, wraps
from typing import Any, Callable
from types import SimpleNamespace

import click
import httpx
from click.core import Parameter, ParameterSource
from packaging.version import parse

from .config import Session
from .utils import datetime_type
from ._logging import _normalize_logger
from . import __version__


logger = logging.getLogger("audible_cli.options")

pass_session = click.make_pass_decorator(Session, ensure=True)


def run_async(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, ** kwargs))
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
            raise click.Abort()

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
    kwargs.setdefault("default", 30)
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


def page_size_option(
    func: Callable[..., Any] | None = None, **kwargs: Any
) -> Callable[..., Any]:
    """Create a Click option for page size with a legacy alias.

    Adds a primary ``--page-size`` option and accepts the legacy
    ``--bunch-size`` during a transition period. The value is validated to
    be within ``[10, 1000]``. If both flags are supplied, ``--page-size``
    takes precedence. The value is stored under both keys (``page_size``,
    ``bunch_size``) to keep older commands compatible.
    """
    # Primary option defaults
    kwargs.setdefault("type", click.IntRange(10, 1000))
    kwargs.setdefault("default", 1000)
    kwargs.setdefault("show_default", True)
    kwargs.setdefault("metavar", "[10-1000]")
    kwargs.setdefault(
        "help",
        (
            "Number of items to request per API call (10–1000). Larger values "
            "reduce the number of requests but may cause timeouts or higher "
            "memory usage on slow connections. Tip: Use smaller values if you "
            "experience 408 timeouts or 429 rate limits."
        ),
    )

    def _page_size_callback(
        ctx: click.Context, param: Parameter, value: int | None
    ) -> None:
        """Callback for ``--page-size``.

        If the value came from the command line, always write it.
        If it's the default, only write it when the session does not already
        contain values (e.g., set by legacy ``--bunch-size``).
        """
        if value is None:
            return

        source = ctx.get_parameter_source("page_size")
        session = ctx.ensure_object(Session)  # your app's Session

        if source == ParameterSource.COMMANDLINE:
            # Explicit --page-size: always set/override.
            add_param_to_session(ctx, SimpleNamespace(name="page_size"), value)
            add_param_to_session(ctx, SimpleNamespace(name="bunch_size"), value)
            return

        # Default value path: do not overwrite if legacy already populated.
        if "page_size" in session.params or "bunch_size" in session.params:
            # Skip to avoid clobbering a legacy value (or any pre-set value).
            return

        # Neither key set yet → apply default to both keys.
        add_param_to_session(ctx, SimpleNamespace(name="page_size"), value)
        add_param_to_session(ctx, SimpleNamespace(name="bunch_size"), value)

    kwargs["callback"] = _page_size_callback
    kwargs["expose_value"] = False

    page_size = click.option("--page-size", **kwargs)

    def _legacy_bunch_size_callback(
        ctx: click.Context, param: Parameter, value: int | None
    ) -> None:
        """Handle ``--bunch-size`` as a deprecated alias.

        If ``--page-size`` was explicitly provided, ignore legacy and note it.
        Otherwise, store the legacy value under both keys and warn.
        """
        if value is None:
            return

        if ctx.get_parameter_source("page_size") == ParameterSource.COMMANDLINE:
            click.echo(
                "Note: --bunch-size is deprecated and ignored because --page-size was provided.",
                err=True,
            )
            return

        click.echo(
            "Warning: --bunch-size is deprecated. Please use --page-size.",
            err=True,
        )
        add_param_to_session(ctx, SimpleNamespace(name="page_size"), value)
        add_param_to_session(ctx, SimpleNamespace(name="bunch_size"), value)

    legacy = click.option(
        "--bunch-size",
        type=click.IntRange(10, 1000),
        default=None,            # only active when explicitly provided
        hidden=True,             # keep it out of --help
        callback=_legacy_bunch_size_callback,
        expose_value=False,
    )

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        # Order doesn't matter now; guards in callbacks prevent overwrites.
        return page_size(legacy(f))

    if callable(func):
        return decorator(func)

    return decorator


# Backward-compat alias for old imports
bunch_size_option = page_size_option


def start_date_option(func=None, **kwargs):
    kwargs.setdefault("type", datetime_type)
    kwargs.setdefault(
        "help",
        "Only considers books added to library on or after this UTC date."
    )
    kwargs.setdefault("callback", add_param_to_session)
    kwargs.setdefault("expose_value", False)

    option = click.option("--start-date", **kwargs)

    if callable(func):
        return option(func)

    return option


def end_date_option(func=None, **kwargs):
    kwargs.setdefault("type", datetime_type)
    kwargs.setdefault(
        "help",
        "Only considers books added to library on or before this UTC date."
    )
    kwargs.setdefault("callback", add_param_to_session)
    kwargs.setdefault("expose_value", False)

    option = click.option("--end-date", **kwargs)

    if callable(func):
        return option(func)

    return option
