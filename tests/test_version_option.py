"""`--version` answers a question a script may be asking.

The version goes to stdout on a line of its own, and nothing else does.
The update check that follows it is a courtesy on stderr: it may fail,
and when it does it must not take the answer or the exit code with it.
"""

from unittest import mock

import httpx
import pytest
from click.testing import CliRunner

from audible_cli import __version__
from audible_cli.cli import cli


def release(tag):
    """A GitHub release response naming `tag` as the latest version."""
    response = mock.Mock()
    response.json.return_value = {
        "tag_name": tag,
        "html_url": "https://github.example/releases/latest",
    }
    return response


def run(get):
    """Invoke `audible --version` with `get` standing in for httpx."""
    with mock.patch("audible_cli.decorators.httpx.get", get):
        return CliRunner().invoke(cli, ["--version"])


def test_a_failed_update_check_still_answers_the_question():
    # `v=$(audible --version)` on a machine with no network used to come
    # back as "audible-cli, version 0.5.1error: no route" and exit 1.
    result = run(mock.Mock(side_effect=httpx.ConnectError("no route")))

    assert result.exit_code == 0
    assert result.stdout == f"audible-cli, version {__version__}\n"
    assert "Could not check for a newer release: no route" in result.stderr


def test_the_answer_survives_whatever_the_check_raises():
    # Anything the request or the response can throw, not just the one
    # error the other test uses.
    for failure in (
        httpx.ReadTimeout("too slow"),
        httpx.HTTPStatusError("rate limited", request=None, response=None),
        ValueError("nonsense from the API"),
    ):
        result = run(mock.Mock(side_effect=failure))

        assert result.exit_code == 0, failure
        assert result.stdout == f"audible-cli, version {__version__}\n", failure


@pytest.mark.parametrize(
    ("tag", "notice"),
    [
        ("0.0.1", "Up-to-date."),
        ("99.0.0", "An update is available."),
    ],
)
def test_the_notice_never_shares_the_line_with_the_version(tag, notice):
    result = run(mock.Mock(return_value=release(tag)))

    assert result.exit_code == 0
    assert result.stdout == f"audible-cli, version {__version__}\n"
    assert notice in result.stderr
