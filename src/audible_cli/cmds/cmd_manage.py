import pathlib

import click
from audible import Authenticator
from click import echo, secho
from tabulate import tabulate

from ..config import pass_session
from ..utils import build_auth_file


@click.group("manage")
def cli():
    """manage audible-cli"""


@cli.group("config")
def manage_config():
    """manage config"""


@cli.group("profile")
def manage_profiles():
    """manage profiles"""


@cli.group("auth-file")
def manage_auth_files():
    """manage auth files"""


@manage_config.command("edit")
@pass_session
def config_editor(session):
    """Open the config file with default editor"""
    click.edit(filename=session.config.filename)


@manage_profiles.command("list")
@pass_session
def list_profiles(session):
    """List all profiles in the config file"""
    head = ["P", "Profile", "auth file", "cc"]
    profiles = session.config.data.get("profile")

    data = []
    for profile in profiles:
        p = profiles.get(profile)
        auth_file = p.get("auth_file")
        country_code = p.get("country_code")
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
    help="The profile name to add to config."
)
@click.option(
    "--country-code", "-cc",
    prompt="Please enter the country code",
    type=click.Choice([
        "us", "ca", "uk", "au", "fr", "de", "jp", "it", "in"]),
    help="The country code for the profile."
)
@click.option(
    "--auth-file", "-f",
    type=click.Path(exists=False, file_okay=True),
    prompt="Please enter name for the auth file",
    help="The auth file name (without dir) to be added. "
         "The auth file must exist."
)
@click.option(
    "--is-primary",
    is_flag=True,
)
@pass_session
@click.pass_context
def add_profile(ctx, session, profile, country_code, auth_file, is_primary):
    """Adds a profile to config file"""
    if not (session.config.dirname / auth_file).exists():
        ctx.fail("Auth file doesn't exists.")

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
    """Remove one or multiple profile(s) from config file"""
    profiles = session.config.data.get("profile")
    for p in profile:
        if p not in profiles:
            secho(
                f"Profile '{p}' doesn't exist. Can't remove it.", fg="red")
        else:
            del profiles[p]
            echo(f"Profile '{p}' removed from config")

    session.config.write_config()
    echo("Changes successful saved to config file.")


@pass_session
def check_if_auth_file_not_exists(session, ctx, param, value):
    value = session.config.dirname / value
    if pathlib.Path(value).exists():
        ctx.fail("The file already exists.")
    return value


@manage_auth_files.command("add")
@click.option(
    "--auth-file", "-f",
    type=click.Path(exists=False, file_okay=True),
    prompt="Please enter name for the auth file",
    callback=check_if_auth_file_not_exists,
    help="The auth file name (without dir) to be added."
)
@click.option(
    "--password", "-p",
    help="The optional password for the auth file."
)
@click.option(
    "--audible-username", "-au",
    prompt="Please enter the audible username",
    help="The audible username to authenticate."
)
@click.option(
    "--audible-password", "-ap",
    hide_input=True,
    confirmation_prompt=True,
    prompt="Please enter the password for the audible user",
    help="The password for the audible user."
)
@click.option(
    "--country-code", "-cc",
    type=click.Choice(["us", "ca", "uk", "au", "fr", "de", "jp", "it", "in"]),
    prompt="Please enter the country code",
    help="The country code for the marketplace you want to authenticate."
)
@click.option(
    "--external-login",
    is_flag=True,
    help="Authenticate using a webbrowser."
)
@click.option(
    "--with-username",
    is_flag=True,
    help="Using a pre-amazon Audible account to login."
)
@pass_session
def add_auth_file(session, auth_file, password, audible_username,
                  audible_password, country_code, external_login, with_username):
    """Register a new device and add an auth file to config dir"""
    build_auth_file(
        filename=session.config.dirname / auth_file,
        username=audible_username,
        password=audible_password,
        country_code=country_code,
        file_password=password,
        external_login=external_login,
        with_username=with_username
    )


@pass_session
def check_if_auth_file_exists(session, ctx, param, value):
    value = session.config.dirname / value
    if not pathlib.Path(value).exists():
        ctx.fail("The file doesn't exists.")
    return value


@manage_auth_files.command("remove")
@click.option(
    "--auth-file", "-f",
    type=click.Path(exists=False, file_okay=True),
    callback=check_if_auth_file_exists,
    prompt="Please enter name for the auth file",
    help="The auth file name (without dir) to be added."
)
@click.option(
    "--password", "-p",
    help="The optional password for the auth file."
)
def remove_auth_file(auth_file, password):
    """Deregister a device and remove auth file from config dir"""
    auth = Authenticator.from_file(auth_file, password)
    device_name = auth.device_info["device_name"]
    auth.refresh_access_token()
    auth.deregister_device()
    echo(f"{device_name} deregistered")
    auth_file.unlink()
    echo(f"{auth_file} removed from config dir")
