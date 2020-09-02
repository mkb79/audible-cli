import importlib
import os
import pathlib
import sys

import click

from . import cmd_quickstart
from .utils import Config

APP_NAME: str = "Audible"
CONFIG_FILE: str = "config.toml"
CONFIG_ENV_DIR: str = "AUDIBLE_CONFIG_DIR"
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


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


def add_option_to_config(ctx, param, value):
    """Add a provided option to :attr:`Config._params`"""
    config = ctx.ensure_object(Config)
    config.params[param.name] = value
    return value


class CliCommands(click.Group):
    def list_commands(self, ctx):
        return sorted(["manage", "download"])

    def get_command(self, ctx, name):
        try:
            mod = importlib.import_module(f"audible_cli.cmd_{name}")
        except ImportError:
            click.secho(
                f"Something went wrong during setup command: {name}\n",
                fg="red",
                bold=True
            )
            return
        return mod.cli


@click.group(cls=CliCommands, context_settings=CONTEXT_SETTINGS)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, file_okay=True),
    default=get_config_file_path(),
    show_default=True,
    callback=read_config,
    expose_value=False,
    help="The config file to be used."
)
@click.option(
    "--profile",
    "-P",
    callback=add_option_to_config,
    expose_value=False,
    help="The profile to use instead primary profile (case sensitive!)."
)
@click.option(
    "--password",
    "-p",
    callback=add_option_to_config,
    expose_value=False,
    help="The password for the profile auth file."
)
def cli():
    pass


def main(*args, **kwargs):
    try:
        cli(*args, **kwargs)
    except KeyboardInterrupt:
        sys.exit('\nERROR: Interrupted by user')


@click.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=False, file_okay=True),
    default=get_config_file_path(),
    show_default=True,
    callback=set_config,
    expose_value=False,
    help="The config file to be used."
)
@click.pass_context
def quickstart(ctx):
    ctx.forward(cmd_quickstart.cli)
