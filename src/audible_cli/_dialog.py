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

import sys

import click
from prompt_toolkit.output.defaults import create_output


def say(message: str = "", **styles) -> None:
    """Write one line of the conversation.

    Args:
        message: The line. Empty for a blank one.
        **styles: Passed to :func:`click.secho`, e.g. ``bold=True``.
    """
    click.secho(message, err=True, **styles)


def ask(text: str, **kwargs):
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


def selection_output():
    """Point a questionary prompt at the same stream as the rest.

    questionary renders through prompt_toolkit, which draws on stdout
    unless it is given somewhere else. Pass this as ``output=`` so a
    selection list does not end up on the one stream that is supposed to
    stay clean.

    Returns:
        A prompt_toolkit output writing to stderr.
    """
    return create_output(stdout=sys.stderr)
