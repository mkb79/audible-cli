import logging
import pathlib
import sys

import click
from tabulate import tabulate

from .. import __version__
from .._dialog import ask, confirm, say
from ..config import ConfigFile
from ..constants import AVAILABLE_MARKETPLACES, CONFIG_FILE, DEFAULT_AUTH_FILE_EXTENSION
from ..decorators import pass_session
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


def ask_user(config: ConfigFile):
    d = {}
    welcome_message = (
        f"\nWelcome to the audible-cli {__version__} quickstart utility.")
    say(welcome_message, bold=True)
    say(len(welcome_message) * "=", bold=True)

    intro = """Quickstart will guide you through the process of build a basic
config, create a first profile and assign an auth file to the profile now.

The profile created by quickstart will set as primary. It will be used, if no
other profile is chosen.

An auth file can be shared between multiple profiles. Simply enter the name of
an existing auth file when asked about it. Auth files have to be stored in the
config dir. If the auth file doesn't exists, it will be created. In this case,
an authentication to the audible server is necessary to register a new device.
"""
    say()
    say(intro)

    path = config.dirname.absolute()
    say("Selected dir to proceed with:", bold=True)
    say(path)

    say()
    say("Please enter values for the following settings (just press Enter "
         "to accept a default value, if one is given in brackets).")

    say()
    d["profile_name"] = ask(
        "Please enter a name for your primary profile",
        default="audible")

    say()
    d["country_code"] = ask(
        "Enter a country code for the profile",
        show_choices=False,
        type=click.Choice(AVAILABLE_MARKETPLACES)
    )

    say()
    d["auth_file"] = ask(
        "Please enter a name for the auth file",
        default=d["profile_name"] + "." + DEFAULT_AUTH_FILE_EXTENSION)

    while (path / d["auth_file"]).exists():
        say()
        say("The auth file already exists in config dir.", bold=True)
        say()

        d["use_existing_auth_file"] = confirm(
            "Should this file be used for the new profile",
            default=False)

        if d["use_existing_auth_file"]:
            logger.info("Use existing auth file for new profile.")

            return d

        say()
        d["auth_file"] = ask(
            "Please enter a new name for the auth file "
            "(or just Enter to exit)",
            default=""
        )
        if not d["auth_file"]:
            sys.exit(1)

    say()
    encrypt_file = confirm(
        "Do you want to encrypt the auth file?",
        default=False)

    if encrypt_file:
        say()
        d["auth_file_password"] = ask(
            "Please enter a password for the auth file",
            confirmation_prompt=True, hide_input=True)

    say()
    d["external_login"] = confirm(
        "Do you want to login with external browser?",
        default=False)
    d["audible_username"] = None
    d["audible_password"] = None

    say()
    d["with_username"] = confirm(
        "Do you want to login with a pre-amazon Audible account?",
        default=False)

    if not d["external_login"]:
        d["audible_username"] = ask("Please enter your amazon username")
        d["audible_password"] = ask(
            "Please enter your amazon password",
            hide_input=True, confirmation_prompt=True
        )

    return d


@click.command("quickstart")
@pass_session
def cli(session):
    """Quick setup audible."""
    config_file: pathlib.Path = session.app_dir / CONFIG_FILE
    config = ConfigFile(config_file, file_exists=False)
    if config_file.is_file():
        m = f"Config file {config_file} already exists. Quickstart will " \
            f"not overwrite existing files."
        logger.error(m)
        raise click.Abort()

    d = ask_user(config)

    say()
    say(tabulate_summary(d))
    confirm("Do you want to continue?", abort=True)

    if "use_existing_auth_file" not in d:
        build_auth_file(
            filename=session.app_dir / d.get("auth_file"),
            username=d.get("audible_username"),
            password=d.get("audible_password"),
            country_code=d.get("country_code"),
            file_password=d.get("auth_file_password"),
            external_login=d.get("external_login"),
            with_username=d.get("with_username")
        )

    config.add_profile(
        name=d.get("profile_name"),
        auth_file=d.get("auth_file"),
        country_code=d.get("country_code"),
        is_primary=True,
    )
