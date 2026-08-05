import os
import pathlib
from unittest import mock

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
