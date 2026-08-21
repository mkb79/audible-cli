"""Where each kind of output goes, across the package.

One rule: stdout carries what a command produces or asks, stderr carries
everything said about the work. These are the cases where the line is easy
to cross back over, so they are held here rather than left to review.
"""

import logging
from unittest import mock

import click
import pytest
from audible.exceptions import FileEncryptionError
from click.testing import CliRunner

from audible_cli import __version__, _logging
from audible_cli.cli import cli
from audible_cli.config import Session
from audible_cli.plugins import BrokenCommand


@pytest.fixture(autouse=True)
def narrating_to_stderr(monkeypatch):
    """The package logger as the command line configures it."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    logger = logging.getLogger("audible_cli")
    handlers, level = logger.handlers[:], logger.level
    _logging.click_basic_config(logger)
    logger.setLevel(logging.INFO)
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)


def test_the_version_is_the_only_thing_version_produces(monkeypatch):
    # `audible --version` is read by scripts. An update notice glued to the
    # end of the line is something else, and belongs somewhere else.
    response = mock.Mock()
    response.json.return_value = {
        "tag_name": "99.0.0",
        "html_url": "https://example.invalid/release",
    }
    monkeypatch.setattr("audible_cli.decorators.httpx.get", lambda *a, **kw: response)

    result = CliRunner().invoke(cli, ["--version"])

    assert result.stdout == f"audible-cli, version {__version__}\n"
    assert "An update is available" in result.stderr


def test_a_broken_plugin_reports_itself_on_stderr():
    # The summary carries its level, the traceback keeps its own shape.
    try:
        raise ImportError("no module named 'nowhere'")
    except ImportError:
        broken = BrokenCommand("nowhere")

    group = click.Group()
    group.add_command(broken)

    result = CliRunner().invoke(group, ["nowhere"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: The nowhere plugin could not be loaded")
    assert "\nTraceback (most recent call last):\n" in result.stderr
    assert "error: Traceback" not in result.stderr


def test_the_reason_for_a_password_prompt_travels_with_the_prompt(tmp_path):
    # The mirror-image mistake: sent through the logger, a verbosity that
    # quiets the explanation still leaves the question standing, and the
    # user faces a bare password prompt with no idea why.
    (tmp_path / "config.toml").write_text(
        '[APP]\nprimary_profile = "one"\n\n'
        '[profile.one]\nauth_file = "one.json"\ncountry_code = "de"\n'
    )
    (tmp_path / "one.json").write_text("{}")

    session = Session()
    session._app_dir = tmp_path

    @click.command()
    def cmd():
        session.get_auth_for_profile("one")

    with mock.patch(
        "audible_cli.config.Authenticator.from_file",
        side_effect=FileEncryptionError("encrypted"),
    ):
        # An empty answer is how the prompt is told to give up.
        result = CliRunner().invoke(cmd, [], input="\n")

    assert "Auth file is encrypted" in result.stdout
    assert "Auth file is encrypted" not in result.stderr
