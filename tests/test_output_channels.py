"""Where each kind of output goes, across the package.

One rule: stdout carries what a command produces or asks, stderr carries
everything said about the work. These are the cases where the line is easy
to cross back over, so they are held here rather than left to review.
"""

import logging
import pathlib
import re
from unittest import mock

import click
import pytest
from audible.exceptions import FileEncryptionError
from click.testing import CliRunner

from audible_cli import __version__, _logging
from audible_cli.cli import cli
from audible_cli.cmds.cmd_quickstart import cli as quickstart
from audible_cli.config import Session
from audible_cli.plugins import BrokenCommand
from audible_cli.utils import prompt_external_callback


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
    # Explanation and question on one stream, and neither of them through
    # the logger: at CRITICAL the reason would be gone while the question
    # waited on, and the user would face a bare password prompt.
    (tmp_path / "config.toml").write_text(
        '[APP]\nprimary_profile = "one"\n\n'
        '[profile.one]\nauth_file = "one.json"\ncountry_code = "de"\n'
    )
    (tmp_path / "one.json").write_text("{}")

    session = Session()
    session._app_dir = tmp_path
    logging.getLogger("audible_cli").setLevel(logging.CRITICAL)

    @click.command()
    def cmd():
        click.echo("the payload")
        session.get_auth_for_profile("one")

    with mock.patch(
        "audible_cli.config.Authenticator.from_file",
        side_effect=FileEncryptionError("encrypted"),
    ):
        # An empty answer is how the prompt is told to give up.
        result = CliRunner().invoke(cmd, [], input="\n")

    assert "Auth file is encrypted" in result.stderr
    assert "Please enter the auth-file password" in result.stderr
    assert result.stdout == "the payload\n"


def test_a_selection_list_does_not_draw_on_the_payload_stream():
    # questionary renders through prompt_toolkit, which draws on stdout
    # unless it is handed somewhere else. Every call site has to hand it
    # somewhere else, so the rule is checked at the source.
    package = pathlib.Path("src/audible_cli")
    asking = []

    for path in package.rglob("*.py"):
        text = path.read_text()
        for call in re.finditer(
            r"questionary\.(?:select|text|checkbox)\((?:[^()]|\([^()]*\))*\)", text
        ):
            if "output=selection_output()" not in call.group():
                asking.append(f"{path.name}: {call.group()[:60]}")

    assert asking == [], f"drawing on stdout: {asking}"


def test_the_external_login_asks_on_the_conversation_channel():
    # The audible library holds this conversation with print and input,
    # which puts half of it on stdout. audible-cli brings its own.
    @click.command()
    def cmd():
        click.echo("the payload")
        click.echo(
            prompt_external_callback("https://amazon.example/ap/signin"), err=True
        )

    result = CliRunner().invoke(
        cmd, [], input="https://amazon.example/ap/maplanding?token=abc\n"
    )

    assert result.stdout == "the payload\n"
    assert "https://amazon.example/ap/signin" in result.stderr
    assert "Please insert the copied url" in result.stderr
    assert result.stderr.rstrip().endswith("maplanding?token=abc")


def test_the_wizard_leaves_the_payload_stream_empty(tmp_path, monkeypatch):
    # Nothing a wizard says is a product. Redirecting stdout must not
    # swallow a single question.
    monkeypatch.setenv("AUDIBLE_CONFIG_DIR", str(tmp_path))

    result = CliRunner().invoke(quickstart, [], input="\n" * 4 + "n\nn\nn\nn\n")

    assert result.stdout == ""
    assert "quickstart utility" in result.stderr
