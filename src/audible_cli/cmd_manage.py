import click
import pathlib
from click import echo, secho
from tabulate import tabulate

from audible.auth import FileAuthenticator

from .config import pass_config
from .utils import build_auth_file


@click.group()
def cli():
    """manage audible"""


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
@pass_config
def config_editor(config):
    """Open the config file with default editor"""
    click.edit(filename=config.filename)


@manage_profiles.command("list")
@pass_config
def list_profiles(config):
    """List all profiles in the config file"""
    head = ["P", "Profile", "auth file", "cc"]
    data = []
    profiles = config.data.get("profile")

    for profile in profiles:
        p = profiles.get(profile)
        auth_file = p.get("auth_file")
        country_code = p.get("country_code")
        is_primary = profile == config.primary_profile
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
    help="The auth file name (without dir) to be added. " \
         "The auth file must exist."
)
@click.option(
    "--is-primary",
    is_flag=True,
)
@pass_config
@click.pass_context
def add_profile(ctx, config, profile, country_code, auth_file, is_primary):
    """Adds a profile to config file"""
    if not (config.dir_path / auth_file).exists():
        ctx.fail("Auth file doesn't exists.")

    config.add_profile(
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
@pass_config
@click.pass_context
def remove_profile(ctx, config, profile):
    """Remove one or multiple profile(s) from config file"""
    profiles = config.data.get("profile")
    for p in profile:
        if p not in profiles:
            secho(
                f"Profile '{p}' doesn't exist. Can't remove it.", fg="red")
        else:
            del profiles[p]
            echo(f"Profile '{p}' removed from config")

    config.write_config()
    echo("Changes successful saved to config file.")


@pass_config
def check_if_auth_file_not_exists(config, ctx, value):
    value = config.dir_path / value
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
@pass_config
def add_auth_file(config, auth_file, password, audible_username, audible_password, country_code):
    "Register a new device and add an auth file to config dir"
    build_auth_file(
        filename=auth_file,
        username=audible_username,
        password=audible_password,
        country_code=country_code,
        file_password=password
    )


@pass_config
def check_if_auth_file_exists(config, ctx, value):
    value = config.dir_path / value
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
    "Deregister a device and remove auth file from config dir"
    auth = FileAuthenticator(auth_file, password)
    device_name = auth.device_info["device_name"]
    auth.refresh_access_token()

    auth.deregister_device()
    echo(f"{device_name} deregistered")
    
    auth_file.unlink()
    echo(f"{auth_file} removed from config dir")
    
