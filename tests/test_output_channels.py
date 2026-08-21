"""Where each kind of output goes, across the package.

One rule: stdout carries the result a command was asked for, stderr
carries everything else -- narration, diagnostics, and the conversation
with the person at the terminal, questions included. These are the cases
where the line is easy to cross back over.
"""

import inspect
import io
import logging
import pathlib
import re
import sys
from unittest import mock

import click
import pytest
from audible import Authenticator
from audible.exceptions import FileEncryptionError
from click.testing import CliRunner

from audible_cli import __version__, _dialog, _logging, utils
from audible_cli.cli import cli
from audible_cli.cmds.cmd_manage import cli as manage
from audible_cli.cmds.cmd_quickstart import cli as quickstart
from audible_cli.config import Session
from audible_cli.plugins import BrokenCommand
from audible_cli.utils import build_auth_file, prompt_external_callback


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


@pytest.mark.parametrize("level", [logging.INFO, logging.ERROR, logging.CRITICAL])
def test_a_broken_plugin_reports_itself_on_stderr(level):
    # The summary carries its level, the traceback keeps its own shape.
    # And no verbosity may silence it: the command the user just typed
    # cannot run, and an exit code on its own explains nothing.
    logging.getLogger("audible_cli").setLevel(level)

    try:
        raise ImportError("no module named 'nowhere'")
    except ImportError:
        broken = BrokenCommand("nowhere")

    group = click.Group()
    group.add_command(broken)

    result = CliRunner().invoke(group, ["nowhere"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("critical: The nowhere plugin could not be loaded")
    assert "\nTraceback (most recent call last):\n" in result.stderr
    assert "critical: Traceback" not in result.stderr


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
        result = CliRunner().invoke(cmd, [], input="\n")

    assert "Auth file is encrypted" in result.stderr
    assert "Please enter the auth-file password" in result.stderr
    assert result.stdout == "the payload\n"


def test_the_selection_output_is_bound_to_the_diagnostic_stream():
    # The neighbouring test checks that every call site hands questionary
    # an output; this one, that the output points where it should and
    # follows sys.stderr rather than capturing it at import.
    captured = io.StringIO()
    with mock.patch.object(sys, "stderr", captured):
        assert _dialog.selection_output().stdout is captured


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


def test_the_wizard_leaves_the_payload_stream_empty(tmp_path, monkeypatch):
    # Nothing a wizard says is a product, and a redirect must swallow no
    # question -- including the last, hence the walk to the end.
    monkeypatch.setenv("AUDIBLE_CONFIG_DIR", str(tmp_path))
    answers = "\nde\n\nn\nn\nn\nsomeone\nsecret\nsecret\ny\n"

    with mock.patch("audible_cli.cmds.cmd_quickstart.build_auth_file") as build:
        result = CliRunner().invoke(quickstart, [], input=answers)

    assert result.exit_code == 0, result.stderr
    assert build.called, "the wizard stopped before the end"
    assert result.stdout == ""
    assert "quickstart utility" in result.stderr
    assert "Do you want to continue?" in result.stderr
    assert (tmp_path / "config.toml").is_file()


def test_an_option_that_asks_for_itself_asks_on_stderr():
    # Click's own `prompt=` reaches for click.core.prompt, which has no
    # err of its own. Without DialogOption the question lands on stdout
    # and a redirect leaves the command waiting on an invisible ask.
    result = CliRunner().invoke(manage, ["profile", "add"], input="a name\n")

    assert result.stdout == ""
    assert "Please enter the profile name" in result.stderr


@pytest.mark.parametrize(
    ("callback", "answer", "expected"),
    [
        ("prompt_otp_callback", "123456\n", "2FA is activated"),
        ("prompt_cvf_callback", "654321\n", "verification code"),
        ("prompt_approval_callback", "\n", "Approval alert"),
    ],
)
def test_each_login_question_is_asked_on_stderr(callback, answer, expected):
    # The neighbouring test checks who owns these; this one, what they do.
    @click.command()
    def cmd():
        click.echo("the payload")
        getattr(utils, callback)()

    result = CliRunner().invoke(cmd, [], input=answer)

    assert result.stdout == "the payload\n"
    assert expected in result.stderr


def test_the_captcha_question_is_asked_on_stderr():
    @click.command()
    def cmd():
        click.echo("the payload")
        utils.prompt_captcha_callback("https://example.invalid/captcha.jpg")

    # Decline the image viewer, so the url is printed instead of fetched.
    result = CliRunner().invoke(cmd, [], input="n\nguess\n")

    assert result.stdout == "the payload\n"
    assert "Captcha found" in result.stderr
    assert "https://example.invalid/captcha.jpg" in result.stderr


def test_the_external_login_asks_on_the_conversation_channel(monkeypatch):
    # Without playwright installed this is the path taken anyway, but a
    # machine that has it would otherwise never reach the questions.
    monkeypatch.setattr(
        "audible_cli.utils.playwright_external_login_url_callback",
        mock.Mock(side_effect=ImportError),
    )

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


@pytest.mark.parametrize(
    ("maker", "external"),
    [("from_login", False), ("from_login_external", True)],
)
def test_every_login_callback_is_one_of_ours(tmp_path, maker, external):
    # The library's own callbacks hold their half of the conversation with
    # print and input, which puts it back on stdout. Whichever ones it
    # offers, audible-cli has to bring its own -- so the expectation is
    # read off the library rather than written down here, and a callback
    # added by a future version fails this instead of slipping through.
    wanted = {
        name
        for name in inspect.signature(getattr(Authenticator, maker)).parameters
        if name.endswith("_callback")
    }

    with mock.patch("audible_cli.utils.Authenticator") as authenticator:
        build_auth_file(
            filename=tmp_path / "auth.json",
            username="someone",
            password="secret",  # noqa: S106
            country_code="de",
            external_login=external,
        )

    passed = getattr(authenticator, maker).call_args.kwargs
    given = {name: cb for name, cb in passed.items() if name.endswith("_callback")}

    assert set(given) == wanted
    for name, callback in given.items():
        assert callback.__module__ == "audible_cli.utils", name


def test_the_log_file_option_is_on_the_real_command_line(tmp_path, monkeypatch):
    # Through the group, so the option is exercised where it is declared,
    # and --verbosity is in play.
    monkeypatch.setenv("AUDIBLE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[APP]\nprimary_profile = "one"\n\n'
        '[profile.one]\nauth_file = "one.json"\ncountry_code = "de"\n\n'
        '[profile.two]\nauth_file = "two.json"\ncountry_code = "de"\n'
    )
    path = tmp_path / "audible.log"

    result = CliRunner().invoke(
        cli, ["--log-file", str(path), "manage", "profile", "remove", "-P", "two"]
    )

    assert result.exit_code == 0, result.stderr
    assert result.stdout == ""
    written = path.read_text(encoding="utf-8")
    assert "Profile two removed from config" in written
    assert "INFO [audible_cli.config] config.py:" in written


def test_a_quiet_console_means_a_quiet_log_file(tmp_path, monkeypatch):
    # The file follows --verbosity rather than collecting everything, which
    # is what makes it a log of the run and not a second contract.
    monkeypatch.setenv("AUDIBLE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[APP]\nprimary_profile = "one"\n\n'
        '[profile.one]\nauth_file = "one.json"\ncountry_code = "de"\n\n'
        '[profile.two]\nauth_file = "two.json"\ncountry_code = "de"\n'
    )
    path = tmp_path / "audible.log"

    CliRunner().invoke(
        cli,
        [
            "-v",
            "error",
            "--log-file",
            str(path),
            "manage",
            "profile",
            "remove",
            "-P",
            "two",
        ],
    )

    assert path.read_text(encoding="utf-8") == ""
