"""The parameter types `audible api` and `audible request` share.

Both commands take a query and request headers in the same shape, and
both have to keep a caller out of the headers that carry the
authentication. What differs is one name: `api` writes the body itself
and always as JSON, so it owns `content-type` too, while `request` sends
a body as it was given and leaves the type to whoever wrote it.
"""

from typing import Any

import click


#: Header names neither command lets a caller set. They carry the
#: authentication, or they frame the message, and both belong to the
#: client that sends the request.
AUTHENTICATION_HEADERS = frozenset(
    {
        "authorization",
        "content-length",
        "host",
        "proxy-authorization",
        "transfer-encoding",
        "x-adp-alg",
        "x-adp-signature",
        "x-adp-token",
        "x-amz-access-token",
    }
)


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

        A key on its own is a query parameter without a value. httpx
        sends it as `key=`; a bare `key` cannot be expressed through it.

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


class HeaderPair(click.ParamType[tuple[str, str]]):
    """One `Name: value` request header."""

    name = "name: value"

    def __init__(self, refused: frozenset[str]) -> None:
        """Take the names this command does not hand over.

        Args:
            refused: Lower-case header names to reject.
        """
        self.refused = refused

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

        if name.lower() in self.refused:
            self.fail(
                f"{name} is set by audible-cli itself and cannot be given here",
                param,
                ctx,
            )

        return name, val.strip()
