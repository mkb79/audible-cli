import os
import pathlib
from typing import Any, Dict, Optional, Union

import click
import toml
from audible import Authenticator
from audible.exceptions import FileEncryptionError
from click import echo, prompt

from .constants import (
    APP_NAME,
    CONFIG_ENV_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG_DATA,
    PLUGIN_PATH
)


class Config:
    """Holds the config file data and environment."""

    def __init__(self) -> None:
        self._config_file: Optional[pathlib.Path] = None
        self._config_data: Dict[str, Union[str, Dict]] = DEFAULT_CONFIG_DATA
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
    def data(self) -> Dict[str, Union[str, Dict]]:
        return self._config_data

    @property
    def primary_profile(self) -> Optional[str]:
        return self.data.get("APP", {}).get("primary_profile")

    def has_profile(self, name: str) -> bool:
        return name in self.data.get("profile", {})

    def get_profile(self, name: str) -> Dict[str, str]:
        return self.data["profile"][name]

    def add_profile(self,
                    name: str,
                    auth_file: Union[str, pathlib.Path],
                    country_code: str,
                    is_primary: bool = False,
                    abort_on_existing_profile: bool = True,
                    write_config: bool = True,
                    **additional_options) -> None:

        if self.has_profile(name) and abort_on_existing_profile:
            message = "Profile already exists."
            try:
                ctx = click.get_current_context()
                ctx.fail(message)
            except RuntimeError:
                raise RuntimeError(message)

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
            message = f"Config file {f} could not be found"
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

    def __init__(self):
        self._params: Dict[str, Any] = {}
        self._auth: Optional[Authenticator] = None
        self._config: Config = Config()
        self._plugin_path: Optional[pathlib.Path] = None

    @property
    def auth(self):
        if self._auth is not None:
            return self._auth

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

        profile = self.config.get_profile(name)
        auth_file = self.config.dirname / profile["auth_file"]
        country_code = profile["country_code"]
        password = self.params.get("password")

        while True:
            try:
                self._auth = Authenticator.from_file(
                    filename=auth_file,
                    password=password,
                    locale=country_code)
                return self._auth
            except (FileEncryptionError, ValueError):
                echo(
                    "Auth file is encrypted but no/wrong password is provided")
                password = prompt(
                    "Please enter the password (or enter to exit)",
                    hide_input=True, default="")
                if password == "":
                    ctx = click.get_current_context()
                    ctx.abort()

    @property
    def config(self):
        return self._config

    @property
    def params(self):
        return self._params

    @property
    def plugin_path(self):
        return self._plugin_path


pass_session = click.make_pass_decorator(Session, ensure=True)


def add_param_to_session(ctx: click.Context, param, value):
    """Add a parameter to :class:`Session` `param` attribute"""
    session = ctx.ensure_object(Session)
    session.params[param.name] = value
    return value


def add_plugin_path_to_session(ctx: click.Context, param, value):
    """Add a plugin cmds path to :class:`Session` `param` attribute"""
    session = ctx.ensure_object(Session)
    session._plugin_path = pathlib.Path(value).resolve()
    return value


def config_dir_path(ignore_env: bool = False) -> pathlib.Path:
    env_dir = os.getenv(CONFIG_ENV_DIR)
    if env_dir and not ignore_env:
        return pathlib.Path(env_dir).resolve()

    return pathlib.Path(
        click.get_app_dir(APP_NAME, roaming=False, force_posix=True)
    )


def config_file_path(ignore_env: bool = False) -> pathlib.Path:
    return (config_dir_path(ignore_env) / CONFIG_FILE).resolve()


def plugin_path(ignore_env: bool = False) -> pathlib.Path:
    return (config_dir_path(ignore_env) / PLUGIN_PATH).resolve()


def read_config(ctx, param, value):
    """Callback that is used whenever --config is passed.  We use this to
    always load the correct config.  This means that the config is loaded
    even if the group itself never executes so our config stay always
    available.
    """
    session = ctx.ensure_object(Session)
    session.config.read_config(value)
    return value


def set_config(ctx, param, value):
    """
    Callback like `read_config` but without reading the config file. The use 
    case is when config file doesn't exists but a `Config` object is needed.
    """
    session = ctx.ensure_object(Session)
    session.config._config_file = pathlib.Path(value)
    return value
