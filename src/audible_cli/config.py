import os
import pathlib
from typing import Any, Dict, Optional, Union

import click
import toml
from audible import Authenticator
from audible.exceptions import FileEncryptionError
from click import echo, prompt

from .constants import (
    CONFIG_DIR_ENV,
    CONFIG_FILE,
    DEFAULT_CONFIG_DATA,
    PLUGIN_DIR_ENV,
    PLUGIN_PATH
)


class Config:
    """Holds the config file data and environment."""

    def __init__(self) -> None:
        self._config_file: Optional[pathlib.Path] = None
        self._config_data: Dict[str, Union[str, Dict]] = DEFAULT_CONFIG_DATA
        self._current_profile: Optional[str] = None
        self._is_read: bool = False

    @property
    def filename(self) -> Optional[pathlib.Path]:
        return self._config_file

    def file_exists(self) -> bool:
        return self.filename.exists()

    @property
    def dirname(self) -> pathlib.Path:
        return self.filename.parent

    def dir_exists(self) -> bool:
        return self.filename.parent.exists()

    @property
    def is_read(self) -> bool:
        return self._is_read

    @property
    def data(self) -> Dict[str, Union[str, Dict]]:
        return self._config_data

    @property
    def app_config(self) -> Dict[str, str]:
        return self.data.get("APP", {})

    @property
    def profile_config(self) -> Dict[str, str]:
        return self.data["profile"][self._current_profile]

    @property
    def primary_profile(self) -> Optional[str]:
        return self.app_config.get("primary_profile")

    def has_profile(self, name: str) -> bool:
        return name in self.data.get("profile", {})

    def add_profile(self,
                    name: str,
                    auth_file: Union[str, pathlib.Path],
                    country_code: str,
                    is_primary: bool = False,
                    abort_on_existing_profile: bool = True,
                    write_config: bool = True,
                    **additional_options) -> None:

        if self.has_profile(name) and abort_on_existing_profile:
            message = f"Profile {name} already exists."
            try:
                ctx = click.get_current_context()
                ctx.fail(message)
            except RuntimeError as exc:
                raise RuntimeError(message) from exc

        profile_data = {"auth_file": str(auth_file),
                        "country_code": country_code,
                        **additional_options}
        self.data["profile"][name] = profile_data

        if is_primary:
            self.data["APP"]["primary_profile"] = name

        if write_config:
            self.write_config()

    def delete_profile(self, name: str) -> None:
        del self.data["profile"][name]

    def read_config(self, filename: Optional[
            Union[str, pathlib.Path]] = None) -> None:
        f = pathlib.Path(filename or self.filename).resolve()

        try:
            self.data.update(toml.load(f))
        except FileNotFoundError as exc:
            message = f"Config file {f} could not be found."
            try:
                ctx = click.get_current_context()
                ctx.fail(message)
            except RuntimeError:
                raise FileNotFoundError(message) from exc

        self._config_file = f
        self._is_read = True

    def write_config(self, filename: Optional[
            Union[str, pathlib.Path]] = None) -> None:
        f = pathlib.Path(filename or self.filename).resolve()

        if not f.parent.is_dir():
            f.parent.mkdir(parents=True)

        toml.dump(self.data, f.open("w"))


class Session:
    """Holds the settings for the current session."""
    def __init__(self) -> None:
        self._auth: Optional[Authenticator] = None
        self._config: Optional[Config] = None
        self._params: Dict[str, Any] = {}
        self._app_dir = get_app_dir()
        self._plugin_dir = get_plugin_dir()

    @property
    def params(self):
        return self._params

    @property
    def app_dir(self):
        return self._app_dir

    @property
    def plugin_dir(self):
        return self._plugin_dir

    @property
    def config(self):
        if self._config is None:
            conf_file = self.app_dir / CONFIG_FILE
            self._config = Config()
            self._config.read_config(conf_file)

            name = self.params.get("profile") or self.config.primary_profile
            if name is None:
                message = ("No profile provided and primary profile not set "
                           "properly in config.")
                try:
                    ctx = click.get_current_context()
                    ctx.fail(message)
                except RuntimeError:
                    raise KeyError(message)

            if not self.config.has_profile(name):
                message = "Provided profile not found in config."
                try:
                    ctx = click.get_current_context()
                    ctx.fail(message)
                except RuntimeError:
                    raise UserWarning(message)

            self.config._current_profile = name

        return self._config

    def _set_auth(self):
        profile = self.config.profile_config
        auth_file = self.config.dirname / profile["auth_file"]
        country_code = profile["country_code"]
        password = self.params.get("password")

        while True:
            try:
                self._auth = Authenticator.from_file(
                    filename=auth_file,
                    password=password,
                    locale=country_code)
                break
            except (FileEncryptionError, ValueError):
                echo("Auth file is encrypted but no/wrong password "
                     "is provided")
                password = prompt(
                    "Please enter the password (or enter to exit)",
                    hide_input=True, default="")
                if password == "":
                    ctx = click.get_current_context()
                    ctx.abort()

    @property
    def auth(self):
        if self._auth is None:
            self._set_auth()
        return self._auth


pass_session = click.make_pass_decorator(Session, ensure=True)


def get_app_dir() -> pathlib.Path:
    app_dir = os.getenv(CONFIG_DIR_ENV) or click.get_app_dir(
        "Audible", roaming=False, force_posix=True)
    return pathlib.Path(app_dir).resolve()


def get_plugin_dir() -> pathlib.Path:
    plugin_dir = os.getenv(PLUGIN_DIR_ENV) or (get_app_dir() / PLUGIN_PATH)
    return pathlib.Path(plugin_dir).resolve()


def add_param_to_session(ctx: click.Context, param, value):
    """Add a parameter to :class:`Session` `param` attribute"""
    session = ctx.ensure_object(Session)
    session.params[param.name] = value
    return value
