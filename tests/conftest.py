import pytest
from helpers import FakeClient

from audible_cli.constants import CONFIG_DIR_ENV, PLUGIN_DIR_ENV


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture(autouse=True)
def never_the_real_config(tmp_path_factory, monkeypatch):
    """Keep every test away from the directory the user actually uses.

    A test that builds a `Session` without saying where reaches the real
    config through `get_app_dir()`, and a command that writes its config
    then writes that one. Pointing the environment variable at a fresh
    directory makes that impossible rather than unlikely.
    """
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path_factory.mktemp("config")))
    monkeypatch.setenv(PLUGIN_DIR_ENV, str(tmp_path_factory.mktemp("plugins")))
