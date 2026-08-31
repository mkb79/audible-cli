import logging
import os
import pathlib

import click
from click import echo
from tabulate import tabulate

from .._dialog import DialogOption
from ..constants import AVAILABLE_MARKETPLACES
from ..decorators import pass_session
from ..utils import build_auth_file, read_auth_file


logger = logging.getLogger("audible_cli.cmds.cmd_manage")


@click.group("manage")
def cli():
    """Manage audible-cli."""


@cli.group("config")
def manage_config():
    """Manage config."""


@cli.group("profile")
def manage_profiles():
    """Manage profiles."""


@cli.group("auth-file")
def manage_auth_files():
    """Manage auth files."""


@manage_config.command("edit")
@pass_session
def config_editor(session):
    """Open the config file with default editor."""
    click.edit(filename=os.fspath(session.config.filename))


@manage_profiles.command("list")
@pass_session
def list_profiles(session):
    """List all profiles in the config file."""
    head = ["P", "Profile", "auth file", "cc"]
    config = session.config
    profiles = config.data.get("profile")

    data = []
    for profile in profiles:
        auth_file = config.get_profile_option(profile, "auth_file")
        country_code = config.get_profile_option(profile, "country_code")
        is_primary = profile == session.config.primary_profile
        data.append(
            ["*" if is_primary else "", profile, auth_file, country_code])

    table = tabulate(
        data, head, tablefmt="pretty",
        colalign=("center", "left", "left", "center"))

    echo(table)


@manage_profiles.command("add")
@click.option(
    "--profile", "-P",
    prompt="Please enter the profile name",
    help="The profile name to add to config.",
    cls=DialogOption,
)
@click.option(
    "--country-code", "-cc",
    prompt="Please enter the country code",
    type=click.Choice(AVAILABLE_MARKETPLACES),
    help="The country code for the profile.",
    cls=DialogOption,
)
@click.option(
    "--auth-file", "-f",
    type=click.Path(exists=False, file_okay=True),
    prompt="Please enter name for the auth file",
    help="The auth file name (without dir) to be added. "
         "The auth file must exist.",
    cls=DialogOption,
)
@click.option(
    "--is-primary",
    is_flag=True,
)
@pass_session
@click.pass_context
def add_profile(ctx, session, profile, country_code, auth_file, is_primary):
    """Adds a profile to config file."""
    if not (session.config.dirname / auth_file).exists():
        logger.error("Auth file doesn't exists")
        raise click.Abort()

    session.config.add_profile(
        name=profile,
        auth_file=auth_file,
        country_code=country_code,
        is_primary=is_primary)


@manage_profiles.command("remove")
@click.option(
    "--profile", "-P",
    required=True,
    multiple=True,
    help="The profile name to remove from config."
)
@pass_session
def remove_profile(session, profile):
    """Remove one or multiple profile(s) from config file.

    Through the config class, like `add` does, and written once after the
    loop rather than once per profile.
    """
    for p in profile:
        if not session.config.has_profile(p):
            logger.error("Profile %s doesn't exist. Can't remove it.", p)
            continue

        session.config.delete_profile(p, write_config=False)

    session.config.write_config()


@pass_session
def check_if_auth_file_not_exists(session, ctx, param, value):
    value = session.config.dirname / value
    if pathlib.Path(value).exists():
        logger.error("The file already exists.")
        raise click.Abort()
    return value


@manage_auth_files.command("add")
@click.option(
    "--auth-file", "-f",
    type=click.Path(exists=False, file_okay=True),
    prompt="Please enter name for the auth file",
    callback=check_if_auth_file_not_exists,
    help="The auth file name (without dir) to be added.",
    cls=DialogOption,
)
@click.option(
    "--password", "-p",
    prompt="Please enter a password for the auth file (or enter for none)",
    default="",
    show_default=False,
    hide_input=True,
    confirmation_prompt=True,
    help="The optional password for the auth file.",
    cls=DialogOption,
)
@click.option(
    "--audible-username", "-au",
    prompt="Please enter the audible username",
    help="The audible username to authenticate.",
    cls=DialogOption,
)
@click.option(
    "--audible-password", "-ap",
    hide_input=True,
    confirmation_prompt=True,
    prompt="Please enter the password for the audible user",
    help="The password for the audible user.",
    cls=DialogOption,
)
@click.option(
    "--country-code", "-cc",
    type=click.Choice(AVAILABLE_MARKETPLACES),
    prompt="Please enter the country code",
    help="The country code for the marketplace you want to authenticate.",
    cls=DialogOption,
)
@click.option(
    "--external-login",
    is_flag=True,
    help="Authenticate using a web browser."
)
@click.option(
    "--with-username",
    is_flag=True,
    help="Using a pre-amazon Audible account to login."
)
@pass_session
def add_auth_file(
        session, auth_file, password, audible_username,
        audible_password, country_code, external_login, with_username
):
    """Register a new device and add an auth file to config dir."""
    build_auth_file(
        filename=session.config.dirname / auth_file,
        username=audible_username,
        password=audible_password,
        country_code=country_code,
        file_password=password or None,
        external_login=external_login,
        with_username=with_username
    )


@pass_session
def check_if_auth_file_exists(session, ctx, param, value):
    value = session.config.dirname / value
    if not pathlib.Path(value).exists():
        logger.error("The file doesn't exists.")
        raise click.Abort()
    return value


@manage_auth_files.command("remove")
@click.option(
    "--auth-file", "-f",
    type=click.Path(exists=False, file_okay=True),
    callback=check_if_auth_file_exists,
    prompt="Please enter name for the auth file",
    help="The auth file name (without dir) to be added.",
    cls=DialogOption,
)
@click.option(
    "--password", "-p",
    help="The optional password for the auth file."
)
def remove_auth_file(auth_file, password):
    """Deregister a device and remove auth file from config dir."""
    auth = read_auth_file(auth_file, password)
    device_name = auth.device_info["device_name"]
    auth.refresh_access_token()
    auth.deregister_device()
    logger.info("%s deregistered", device_name)
    auth_file.unlink()
    logger.info("%s removed from config dir", auth_file)
