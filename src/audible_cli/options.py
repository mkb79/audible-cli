import os
import pathlib

import click

from .config import Config


APP_NAME: str = "Audible"
CONFIG_FILE: str = "config.toml"
CONFIG_ENV_DIR: str = "AUDIBLE_CONFIG_DIR"


def add_param_to_config(ctx, param, value):
    """Add a parameter to :class:`Config` `param` attribute""" 
    config = ctx.ensure_object(Config)
    config.params[param.name] = value
    return value


def get_config_dir_path(ignore_env: bool = False) -> pathlib.Path:
    env_dir = os.getenv(CONFIG_ENV_DIR)
    if env_dir and not ignore_env:
        return pathlib.Path(env_dir).resolve()

    return pathlib.Path(
        click.get_app_dir(APP_NAME, roaming=False, force_posix=True)
    )


def get_config_file_path(ignore_env: bool = False) -> pathlib.Path:
    return (get_config_dir_path(ignore_env) / CONFIG_FILE).absolute()


def read_config(ctx, param, value):
    """Callback that is used whenever --config is passed.  We use this to
    always load the correct config.  This means that the config is loaded
    even if the group itself never executes so our config stay always
    available.
    """
    config = ctx.ensure_object(Config)
    config.read_config(value)
    return value


def set_config(ctx, param, value):
    """
    Callback like `read_config` but without reading the config file. The use 
    case is when config file doesn't exists but a `Config` object is needed.
    """
    config = ctx.ensure_object(Config)
    config.filename = pathlib.Path(value)
    return value


cli_config_option = click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, file_okay=True),
    default=get_config_file_path(),
    show_default=True,
    callback=read_config,
    expose_value=False,
    help="The config file to be used."
)

quickstart_config_option = click.option(
    "--config",
    "-c",
    type=click.Path(exists=False, file_okay=True),
    default=get_config_file_path(),
    show_default=True,
    callback=set_config,
    expose_value=False,
    help="The config file to be used."
)

profile_option = click.option(
    "--profile",
    "-P",
    callback=add_param_to_config,
    expose_value=False,
    help="The profile to use instead primary profile (case sensitive!)."
)

auth_file_password_option = click.option(
    "--password",
    "-p",
    callback=add_param_to_config,
    expose_value=False,
    help="The password for the profile auth file.")
