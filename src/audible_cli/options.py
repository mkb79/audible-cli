import click

from .utils import Config


def add_param_to_config(ctx, param, value):
    """Add a parameter to :class:`Config` `param` attribute""" 
    config = ctx.ensure_object(Config)
    config.params[param.name] = value
    return value


profile_option = click.option(
    "--profile",
    "-P",
    callback=add_param_to_config,
    expose_value=False,
    help="The profile to use instead of the primary.")

auth_file_password_option = click.option(
    "--password",
    "-p",
    callback=add_param_to_config,
    expose_value=False,
    help="The password for the profile auth file.")
