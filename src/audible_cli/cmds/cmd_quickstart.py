import logging
import sys

import audible
import click
from click import echo, secho, prompt
from tabulate import tabulate

from ..config import Config, pass_session
from ..constants import CONFIG_FILE, DEFAULT_AUTH_FILE_EXTENSION
from ..utils import build_auth_file


logger = logging.getLogger("audible_cli.cmds.cmd_quickstart")


def tabulate_summary(d: dict) -> str:
    head = ["Option", "Value"]
    data = [
        ["profile_name", d.get("profile_name")],
        ["auth_file", d.get("auth_file")],
        ["country_code", d.get("country_code")]
    ]
    if "use_existing_auth_file" not in d:
        data.append(
            ["auth_file_password",
             "***" if "auth_file_password" in d else "-"])
        data.append(["audible_username", d.get("audible_username")])
        data.append(["audible_password", "***"])

    return tabulate(data, head, tablefmt="pretty", colalign=("left", "left"))


def ask_user(config: Config):
    d = {}
    welcome_message = (
        f"Welcome to the audible {audible.__version__} quickstart utility.")
    secho(welcome_message, bold=True)
    secho(len(welcome_message) * "=", bold=True)

    intro = """Quickstart will guide you through the process of build a basic 
config, create a first profile and assign an auth file to the profile now.

The profile created by quickstart will set as primary. It will be used, if no 
other profile is chosen.

An auth file can be shared between multiple profiles. Simply enter the name of 
an existing auth file when asked about it. Auth files have to be stored in the 
config dir. If the auth file doesn't exists, it will be created. In this case, 
an authentication to the audible server is necessary to register a new device.
"""
    echo()
    secho(intro, bold=True)

    path = config.dirname.absolute()
    secho("Selected dir to proceed with:", bold=True)
    echo(path.absolute())

    echo()
    echo("Please enter values for the following settings (just press Enter "
         "to accept a default value, if one is given in brackets).")

    echo()
    d["profile_name"] = prompt(
        "Please enter a name for your primary profile",
        default="audible")

    available_country_codes = [
        "us", "ca", "uk", "au", "fr", "de", "es", "jp", "it", "in"]
    echo()
    d["country_code"] = prompt(
        "Enter a country code for the profile",
        show_choices=False,
        type=click.Choice(available_country_codes)
    )

    echo()
    d["auth_file"] = prompt(
        "Please enter a name for the auth file",
        default=d["profile_name"] + "." + DEFAULT_AUTH_FILE_EXTENSION)

    while (path / d["auth_file"]).exists():
        echo()
        secho("The auth file already exists in config dir.", bold=True)
        echo()

        d["use_existing_auth_file"] = click.confirm(
            "Should this file be used for the new profile",
            default=False)

        if d["use_existing_auth_file"]:
            echo()
            echo("Use existing auth file for new profile.")

            return d

        echo()
        d["auth_file"] = prompt(
            "Please enter a new name for the auth file "
            "(or just Enter to exit)",
            default=""
        )
        if not d["auth_file"]:
            sys.exit(1)

    echo()
    encrypt_file = click.confirm(
        "Do you want to encrypt the auth file?",
        default=False)

    if encrypt_file:
        echo()
        d["auth_file_password"] = prompt(
            "Please enter a password for the auth file",
            confirmation_prompt=True, hide_input=True)

    echo()
    d["external_login"] = click.confirm(
        "Do you want to login with external browser?",
        default=False)
    d["audible_username"] = None
    d["audible_password"] = None

    echo()
    d["with_username"] = click.confirm(
        "Do you want to login with a pre-amazon Audible account?",
        default=False)

    if not d["external_login"]:
        d["audible_username"] = prompt("Please enter your amazon username")
        d["audible_password"] = prompt(
            "Please enter your amazon password",
            hide_input=True, confirmation_prompt=True
        )

    return d


@click.command("quickstart")
@click.pass_context
@pass_session
def cli(session, ctx):
    """Quicksetup audible"""
    session._config = Config()
    config = session.config
    config._config_file = session.app_dir / CONFIG_FILE
    if config.file_exists():
        m = f"Config file {config.filename} already exists. Quickstart will " \
            f"not overwrite existing files."

        logger.error(m)
        raise click.Abort()

    d = ask_user(config)

    echo()
    echo(tabulate_summary(d))
    click.confirm("Do you want to continue?", abort=True)

    config.add_profile(
        name=d.get("profile_name"),
        auth_file=d.get("auth_file"),
        country_code=d.get("country_code"),
        is_primary=True,
        write_config=False)

    if "use_existing_auth_file" not in d:
        build_auth_file(
            filename=config.dirname / d.get("auth_file"),
            username=d.get("audible_username"),
            password=d.get("audible_password"),
            country_code=d.get("country_code"),
            file_password=d.get("auth_file_password"),
            external_login=d.get("external_login"),
            with_username=d.get("with_username")
        )

    config.write_config()
