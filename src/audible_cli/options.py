import click

from .config import (
    add_param_to_session,
    add_plugin_path_to_session,
    config_file_path,
    plugin_path,
    read_config,
    set_config
)


cli_config_option = click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, file_okay=True),
    default=config_file_path(),
    show_default=True,
    callback=read_config,
    expose_value=False,
    help="The config file to be used."
)

quickstart_config_option = click.option(
    "--config",
    "-c",
    type=click.Path(exists=False, file_okay=True),
    default=config_file_path(),
    show_default=True,
    callback=set_config,
    expose_value=False,
    help="The config file to be used."
)

plugin_cmds_option = click.option(
    "--plugins",
    type=click.Path(exists=False, dir_okay=True),
    default=plugin_path(),
    show_default=True,
    callback=add_plugin_path_to_session,
    expose_value=False,
    help="The path with additional plugins."
)

profile_option = click.option(
    "--profile",
    "-P",
    callback=add_param_to_session,
    expose_value=False,
    help="The profile to use instead primary profile (case sensitive!)."
)

auth_file_password_option = click.option(
    "--password",
    "-p",
    callback=add_param_to_session,
    expose_value=False,
    help="The password for the profile auth file.")