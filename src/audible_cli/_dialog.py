"""The conversation with the person at the terminal.

Questions and the sentences that explain them go to stderr, and they go
there together. Apart, either half can be lost on its own: send the
explanation through the log and a raised verbosity leaves a bare password
prompt on the screen; leave the question on stdout and a redirect swallows
it while the command waits for an answer nobody can see it asking for.

Both on stderr keeps stdout free for what a command produces, even while
it is asking. And it keeps the whole exchange out of the log file, where a
transcript of questions would be noise.
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
    Copying that method here would work today and fall behind quietly: it
    grows features between releases. Binding the two names for the length
    of one call does not.
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
        """Ask for this option's value.

        Args:
            ctx: The context the option is being processed in.

        Returns:
            The value the person gave.
        """
        with _prompting_on_stderr():
            return super().prompt_for_value(ctx)


def say(message: object = "", **styles) -> None:
    """Write one line of the conversation.

    Args:
        message: The line, or anything that can be printed. Empty for
            a blank one.
        **styles: Passed to :func:`click.secho`, e.g. ``bold=True``.
    """
    click.secho(str(message), err=True, **styles)


def ask(text: str, **kwargs) -> Any:
    """Ask for a value.

    Args:
        text: The question.
        **kwargs: Passed to :func:`click.prompt`.

    Returns:
        Whatever :func:`click.prompt` returns for the answer.
    """
    return click.prompt(text, err=True, **kwargs)


def confirm(text: str, **kwargs) -> bool:
    """Ask a yes-or-no question.

    Args:
        text: The question.
        **kwargs: Passed to :func:`click.confirm`.

    Returns:
        The answer.
    """
    return click.confirm(text, err=True, **kwargs)


def selection_output() -> Any:
    """Point a questionary prompt at the same stream as the rest.

    questionary renders through prompt_toolkit, which draws on stdout
    unless it is given somewhere else. Pass this as ``output=`` so a
    selection list does not end up on the one stream that is supposed to
    stay clean.

    Returns:
        A prompt_toolkit output writing to stderr.
    """
    return create_output(stdout=sys.stderr)
