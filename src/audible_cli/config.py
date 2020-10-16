import pathlib
from typing import Union

import click
import toml
from audible.auth import FileAuthenticator
from audible.exceptions import FileEncryptionError
from click import echo, prompt


DEFAULT_CONFIG_DATA = {"title": "Audible Config File", "APP": {}, "profile": {}}


class Config:
    """This class holds the config and environment."""

    def __init__(self):
        self.filename: Optional[pathlib.Path] = None
        self._config_data: Dict[str, Union[str, Dict]] = DEFAULT_CONFIG_DATA
        self._params: Dict[str, Any] = {}
        self._auth: Optional[FileAuthenticator] = None

    @property
    def data(self):
        return self._config_data

    @property
    def auth(self):
        if self._auth:
            return self._auth

        profile_name = self.params.get("profile", None) or self.primary_profile

        if profile_name is None:
            message = (
                "No profile provided and primary profile not set "
                "properly in config."
            )
            try:
                ctx = click.get_current_context()
                ctx.fail(message)
            except RuntimeError:
                raise KeyError(message)

        if profile_name not in self.data["profile"]:
            message = "Provided profile not found in config."
            try:
                ctx = click.get_current_context()
                ctx.fail(message)
            except RuntimeError:
                raise UserWarning(message)

        profile = self.data["profile"][profile_name]
        auth_file = self.dir_path / profile["auth_file"]
        country_code = profile["country_code"]

        while True:
            try:
                self._auth = FileAuthenticator(
                    auth_file,
                    self.params.get("password", None),
                    country_code)
                break
            except (FileEncryptionError, ValueError):
                echo("Auth file is encrypted but no/wrong password is provided")
                pw = prompt("Please enter the password (or enter to exit)",
                            hide_input=True, default="")
                if not pw:
                    ctx = click.get_current_context()
                    ctx.abort()

                self.params["password"] = pw

        return self._auth

    @property
    def params(self):
        return self._params

    def file_exists(self):
        return self.filename.exists()

    @property
    def dir_path(self):
        return self.filename.parent

    def dir_path_exists(self):
        return self.filename.parent.exists()

    @property
    def primary_profile(self):
        return self.data["APP"]["primary_profile"]

    def read_config(self, filename):
        config_file = pathlib.Path(filename).resolve()

        try:
            self.data.update(toml.load(config_file))
        except FileNotFoundError:
            message = f"Config file {filename} could not be found"
            try:
                ctx = click.get_current_context()
                ctx.fail(message)
            except RuntimeError:
                raise FileNotFoundError(message)

        self.filename = config_file

    def write_config(self, filename=None):
        config_file = pathlib.Path(filename or self.filename).resolve()
        config_dir = config_file.parent

        if not config_dir.is_dir():
            config_dir.mkdir(parents=True)

        toml.dump(self.data, config_file.open("w"))

    def add_profile(
        self,
        name: str,
        auth_file: Union[str, pathlib.Path],
        country_code: str,
        is_primary: bool=False,
        abort_on_existing_profile: bool=True,
        write_config: bool=True,
        **additional_options
    ):

        if name in self.data["profile"] and abort_on_existing_profile:
            message = "Profile already exists."
            try:
                ctx = click.get_current_context()
                ctx.fail(message)
            except RuntimeError:
                raise RuntimeError("Profile already exists.")

        self.data["profile"][name] = {
            "auth_file": str(auth_file),
            "country_code": country_code,
            **additional_options
        }

        if is_primary:
            self.data["APP"]["primary_profile"] = name

        if write_config:
            self.write_config()


pass_config = click.make_pass_decorator(Config, ensure=True)
