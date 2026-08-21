"""Questions and the sentences that explain them.

Both go to stderr, together. Apart, either half can be lost on its own: a
raised verbosity strips an explanation off the prompt it belongs to, and a
redirect swallows a question the command is still waiting on.
"""

import contextlib
import functools
import sys
from typing import Any

import click
from prompt_toolkit.output.defaults import create_output


@contextlib.contextmanager
def _prompting_on_stderr():
    """Let click's own prompt helpers use the conversation channel.

    :meth:`click.Option.prompt_for_value` reaches for ``click.core.prompt``
    and ``click.core.confirm`` directly and takes no ``err`` of its own.
    Binding the two names beats copying that method, which grows features
    between releases. The patch is process-wide and assumes, as a command
    line may, that two prompts are not running at once.
    """
    original = click.core.prompt, click.core.confirm
    click.core.prompt = functools.partial(click.prompt, err=True)
    click.core.confirm = functools.partial(click.confirm, err=True)
    try:
        yield
    finally:
        click.core.prompt, click.core.confirm = original


class DialogOption(click.Option):
    """An option whose ``prompt=`` joins the conversation on stderr.

    Pass as ``cls=DialogOption`` to any option that asks for its value
    when it was not given on the command line.
    """

    def prompt_for_value(self, ctx: click.Context) -> Any:
        """Ask for this option's value, on stderr."""
        with _prompting_on_stderr():
            return super().prompt_for_value(ctx)


def say(message: object = "", **styles) -> None:
    """Write one line of the conversation. Styles go to :func:`click.secho`."""
    click.secho(str(message), err=True, **styles)


def ask(text: str, **kwargs) -> Any:
    """Ask for a value. Keyword arguments go to :func:`click.prompt`."""
    return click.prompt(text, err=True, **kwargs)


def confirm(text: str, **kwargs) -> bool:
    """Ask a yes-or-no question. Keyword arguments go to :func:`click.confirm`."""
    return click.confirm(text, err=True, **kwargs)


def selection_output() -> Any:
    """Point a questionary prompt at the same stream as the rest.

    questionary renders through prompt_toolkit, which draws on stdout
    unless it is given somewhere else. Pass as ``output=``.
    """
    return create_output(stdout=sys.stderr)
