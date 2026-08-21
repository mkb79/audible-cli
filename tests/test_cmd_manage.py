import logging
import os
import pathlib
from unittest import mock

import pytest
from click.testing import CliRunner

from audible_cli.cmds.cmd_manage import cli
from audible_cli.config import Session


def test_config_edit_passes_str_path_to_click_edit(tmp_path):
    """Click >= 8.2 takes a `str` or an iterable of them, but not a `Path`."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("[APP]\n")

    session = Session()
    session._config = mock.Mock(filename=pathlib.Path(config_file))

    runner = CliRunner()
    with mock.patch("audible_cli.cmds.cmd_manage.click.edit") as edit:
        result = runner.invoke(cli, ["config", "edit"], obj=session)

    assert result.exit_code == 0, result.output
    edit.assert_called_once_with(filename=os.fspath(config_file))
    assert isinstance(edit.call_args.kwargs["filename"], str)


@pytest.fixture
def narrating():
    """Put the package logger where the top-level group would put it.

    These tests invoke the subgroup directly, so the --verbosity callback
    never runs and the logger keeps its inherited WARNING level, which
    filters out everything a command narrates.
    """
    logger = logging.getLogger("audible_cli")
    level = logger.level
    logger.setLevel(logging.INFO)
    yield
    logger.setLevel(level)


def profile_config(tmp_path, *names):
    """A config file holding one auth-file-less profile per name."""
    lines = ["[APP]", 'primary_profile = "' + names[0] + '"', ""]
    for name in names:
        lines += [
            f"[profile.{name}]",
            f'auth_file = "{name}.json"',
            'country_code = "de"',
            "",
        ]
    config_file = tmp_path / "config.toml"
    config_file.write_text("\n".join(lines))
    return config_file


def test_removing_a_profile_leaves_the_others_alone(tmp_path):
    profile_config(tmp_path, "one", "two", "three")
    session = Session()
    session._app_dir = tmp_path

    result = CliRunner().invoke(cli, ["profile", "remove", "-P", "two"], obj=session)

    assert result.exit_code == 0, result.output
    assert set(session.config.data["profile"]) == {"one", "three"}
    assert "two" not in (tmp_path / "config.toml").read_text()


def test_a_profile_that_is_not_there_does_not_stop_the_others(tmp_path):
    # Reported and carried on, rather than raised: asking to remove three
    # profiles should not leave the run half done because one was a typo.
    profile_config(tmp_path, "one", "two")
    session = Session()
    session._app_dir = tmp_path

    result = CliRunner().invoke(
        cli, ["profile", "remove", "-P", "nope", "-P", "two"], obj=session
    )

    assert result.exit_code == 0, result.output
    assert "nope doesn't exist" in result.stderr
    assert set(session.config.data["profile"]) == {"one"}


def test_the_removal_is_told_once_and_on_stderr(tmp_path, narrating):
    # It used to be said twice: the command echoed it to stdout and the
    # config class logged it. And the save was reported twice as well.
    profile_config(tmp_path, "one", "two")
    session = Session()
    session._app_dir = tmp_path

    result = CliRunner().invoke(cli, ["profile", "remove", "-P", "two"], obj=session)

    assert result.stdout == ""
    assert result.stderr.count("removed from config") == 1
    assert result.stderr.count("Config written to") == 1
