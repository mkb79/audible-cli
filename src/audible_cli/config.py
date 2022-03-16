import logging
import os
import pathlib
from typing import Any, Dict, Optional, Union

import audible
import click
import toml
from audible import Authenticator
from audible.exceptions import FileEncryptionError

from . import __version__
from .constants import (
    CONFIG_DIR_ENV,
    CONFIG_FILE,
    DEFAULT_CONFIG_DATA,
    PLUGIN_DIR_ENV,
    PLUGIN_PATH
)
from .exceptions import AudibleCliException, ProfileAlreadyExists


logger = logging.getLogger("audible_cli.config")


class ConfigFile:
    """Presents an audible-cli configuration file

    Instantiate a :class:`~audible_cli.config.ConfigFile` will load the file 
    content by default. To create a new config file, the ``file_exists`` 
    argument must be set to ``False``.
    
    Audible-cli configuration files are written in the toml markup language. 
    It has a main section named `APP` and sections for each profile named 
    `profile.<profile_name>`. 
    
    Args:
        filename: The file path to the config file
        file_exists: If ``True``, the file must exists and the file content 
            is loaded.
    """

    def __init__(
            self,
            filename: Union[str, pathlib.Path],
            file_exists: bool = True
    ) -> None:
        filename = pathlib.Path(filename).resolve()
        config_data = DEFAULT_CONFIG_DATA.copy()
        file_data = {}

        if file_exists:
            if not filename.is_file():
                raise AudibleCliException(
                    f"Config file {click.format_filename(filename)} "
                    f"does not exists"
                )
            file_data = toml.load(filename)

        config_data.update(file_data)

        self._config_file = filename
        self._config_data = config_data

    @property
    def filename(self) -> pathlib.Path:
        """Returns the path to the config file"""
        return self._config_file

    @property
    def dirname(self) -> pathlib.Path:
        """Returns the path to the config file directory"""
        return self.filename.parent

    @property
    def data(self) -> Dict[str, Union[str, Dict]]:
        """Returns the configuration data"""
        return self._config_data

    @property
    def app_config(self) -> Dict[str, str]:
        """Returns the configuration data for the APP section"""
        return self.data["APP"]

    def has_profile(self, name: str) -> bool:
        """Check if a profile with these name are in the configuration data
        
        Args:
            name: The name of the profile
        """
        return name in self.data["profile"]

    def get_profile(self, name: str) -> Dict[str, str]:
        """Returns the configuration data for these profile name
        
        Args:
            name: The name of the profile
        """
        if not self.has_profile(name):
            raise AudibleCliException(f"Profile {name} does not exists")
        return self.data["profile"][name]

    @property
    def primary_profile(self) -> str:
        if "primary_profile" not in self.app_config:
            raise AudibleCliException("No primary profile in config set")
        return self.app_config["primary_profile"]

    def get_profile_option(
            self,
            profile: str,
            option: str,
            default: Optional[str] = None
    ) -> str:
        """Returns the value for an option for the given profile.
        
        Looks first, if an option is in the ``profile`` section. If not, it 
        searchs for the option in the ``APP`` section. If not found, it 
        returns the ``default``.
        
        Args:
            profile: The name of the profile
            option: The name of the option to search for
            default: The default value to return, if the option is not found
        """
        profile = self.get_profile(profile)
        if option in profile:
            return profile[option]
        if option in self.app_config:
            return self.app_config[option]
        return default

    def add_profile(
            self,
            name: str,
            auth_file: Union[str, pathlib.Path],
            country_code: str,
            is_primary: bool = False,
            write_config: bool = True,
            **additional_options
    ) -> None:
        """Adds a new profile to the config
        
        Args:
            name: The name of the profile
            auth_file: The name of the auth_file
            country_code: The country code of the marketplace to use with 
                this profile
            is_primary: If ``True``, this profile is set as primary in the 
                ``APP`` section
            write_config: If ``True``, save the config to file
        """

        if self.has_profile(name):
            raise ProfileAlreadyExists(name)

        profile_data = {
            "auth_file": str(auth_file),
            "country_code": country_code,
            **additional_options
        }
        self.data["profile"][name] = profile_data

        if is_primary:
            self.data["APP"]["primary_profile"] = name

        if write_config:
            self.write_config()

    def delete_profile(self, name: str, write_config: bool = True) -> None:
        """Deletes a profile from config
        
        Args:
            name: The name of the profile
            write_config: If ``True``, save the config to file

        Note:    
            Does not delete the auth file.
        """
        if not self.has_profile(name):
            raise AudibleCliException(f"Profile {name} does not exists")

        del self.data["profile"][name]
        if write_config:
            self.write_config()

    def write_config(
            self,
            filename: Optional[Union[str, pathlib.Path]] = None
    ) -> None:
        """Write the config data to file
        
        Args:
            filename: If not ``None`` the config is written to these file path 
                instead of ``self.filename``
        """
        f = pathlib.Path(filename or self.filename).resolve()

        if not f.parent.is_dir():
            f.parent.mkdir(parents=True)

        toml.dump(self.data, f.open("w"))


class Session:
    """Holds the settings for the current session."""
    def __init__(self) -> None:
        self._auth: Optional[Authenticator] = None
        self._config: Optional[CONFIG_FILE] = None
        self._params: Dict[str, Any] = {}
        self._app_dir = get_app_dir()
        self._plugin_dir = get_plugin_dir()
        logger.debug(f"Audible-cli version: {__version__}")
        logger.debug(f"App dir: {click.format_filename(self.app_dir)}")
        logger.debug(f"Plugin dir: {click.format_filename(self.plugin_dir)}")

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
            logger.debug(
                f"Load config from file: "
                f"{click.format_filename(conf_file, shorten=True)}"
            )
            self._config = ConfigFile(conf_file)

        return self._config

    @property
    def selected_profile(self):
        profile = self.params.get("profile") or self.config.primary_profile
        if profile is None:
            message = (
                "No profile provided and primary profile not set "
                "properly in config."
            )
            raise AudibleCliException(message)
        return profile

    def get_auth_for_profile(
            self,
            profile: str,
            password: Optional[str] = None
    ) -> audible.Authenticator:
        auth_file = self.config.get_profile_option(profile, "auth_file")
        country_code = self.config.get_profile_option(profile, "country_code")
        password = password or self.params.get("password")

        while True:
            try:
                auth = Authenticator.from_file(
                    filename=self.config.dirname / auth_file,
                    password=password,
                    locale=country_code)
                break
            except (FileEncryptionError, ValueError):
                logger.info(
                    "Auth file is encrypted but no/wrong password is provided"
                )
                password = click.prompt(
                    "Please enter the password (or enter to exit)",
                    hide_input=True,
                    default="")
                if len(password) == 0:
                    raise click.Abort()

        return auth

    @property
    def auth(self):
        if self._auth is None:
            profile = self.selected_profile

            logger.debug(f"Selected profile: {profile}")

            if not self.config.has_profile(profile):
                message = "Provided profile not found in config."
                raise AudibleCliException(message)

            self._auth = self.get_auth_for_profile(profile)
        return self._auth


pass_session = click.make_pass_decorator(Session, ensure=True)


def get_app_dir() -> pathlib.Path:
    app_dir = os.getenv(CONFIG_DIR_ENV) or click.get_app_dir(
        "Audible", roaming=False, force_posix=True
    )
    return pathlib.Path(app_dir).resolve()


def get_plugin_dir() -> pathlib.Path:
    plugin_dir = os.getenv(PLUGIN_DIR_ENV) or (get_app_dir() / PLUGIN_PATH)
    return pathlib.Path(plugin_dir).resolve()


def add_param_to_session(ctx: click.Context, param, value):
    """Add a parameter to :class:`Session` `param` attribute"""
    session = ctx.ensure_object(Session)
    session.params[param.name] = value
    return value
