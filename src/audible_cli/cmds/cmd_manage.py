import logging
import os
import pathlib

import click
from click import echo
from tabulate import tabulate

from .._dialog import DialogOption, ask
from ..constants import AVAILABLE_MARKETPLACES
from ..decorators import pass_session
from ..exceptions import AudibleCliException
from ..utils import (
    build_auth_file,
    detect_auth_file,
    read_auth_file,
    read_auth_text,
    rewrite_auth_file,
)


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
    prompt="A password for the auth file, or nothing for none",
    default="",
    show_default=False,
    hide_input=True,
    confirmation_prompt=True,
    help="The optional password for the auth file.",
    cls=DialogOption,
)
@click.option(
    "--audible-username", "-au",
    help="The audible username to authenticate. Asked for unless "
         "--external-login says a browser will do it.",
)
@click.option(
    "--audible-password", "-ap",
    help="The password for the audible user. Asked for unless "
         "--external-login says a browser will do it.",
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
    # `--external-login` hands the login to a browser, which asks for
    # itself. The two values below never reach it, so asking for them
    # here would be asking for something to throw away.
    if not external_login:
        if audible_username is None:
            audible_username = ask("Please enter the audible username")

        if audible_password is None:
            audible_password = ask(
                "Please enter the password for the audible user",
                hide_input=True,
                confirmation_prompt=True
            )

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


def resolve_to_the_real_file(auth_file: pathlib.Path) -> pathlib.Path:
    """Find the file that actually holds the credentials.

    A rewrite replaces a name with a new file. Given a symlink, that
    would put the new file where the link was and leave the credentials
    it pointed at untouched, while the command reported success -- so
    the link is followed first. A second hard link cannot be dealt with
    the same way, because every other name would keep pointing at the
    old content; that one is refused.

    Args:
        auth_file: The name that was given.

    Returns:
        The file it stands for.

    Raises:
        AudibleCliException: If the file has more than one name.
    """
    target = auth_file.resolve()

    if target.stat().st_nlink > 1:
        raise AudibleCliException(
            f"{auth_file.name} has more than one name in the file system, "
            f"and rewriting it would leave the credentials readable under "
            f"the others"
        )

    return target


def check_password_is_not_empty(ctx, param, value):
    """Refuse an empty password, which would not encrypt anything.

    `required=True` only asks for the option to be there. An empty value
    passes it, and would then read as "no password" further down and
    write the file in the open while reporting success.
    """
    if not value:
        raise click.BadParameter("a password cannot be empty")
    return value


@manage_auth_files.command("encrypt")
@click.option(
    "--auth-file", "-f",
    type=click.Path(exists=False, file_okay=True),
    prompt="Please enter name for the auth file",
    callback=check_if_auth_file_exists,
    help="The auth file name (without dir) to encrypt.",
    cls=DialogOption,
)
@click.option(
    "--password", "-p",
    prompt="Please enter a password for the auth file",
    hide_input=True,
    confirmation_prompt=True,
    callback=check_password_is_not_empty,
    help="The password to encrypt the auth file with.",
    cls=DialogOption,
)
def encrypt_auth_file(auth_file: pathlib.Path, password: str) -> None:
    """Encrypt an auth file that has no password yet.

    Whatever is not given is asked for, so a password need not be typed
    where the shell keeps it. Given as an option, it is in the shell
    history and, while the command runs, in the process list -- which is
    the price of running this without anybody watching.
    """
    auth_file = resolve_to_the_real_file(auth_file)
    shape = detect_auth_file(auth_file)

    if shape is None:
        raise AudibleCliException(f"{auth_file.name} is not an auth file")

    if shape != "plain":
        raise AudibleCliException(
            f"{auth_file.name} is encrypted already. Decrypt it first to "
            f"give it another password."
        )

    rewrite_auth_file(auth_file, read_auth_text(auth_file, shape), password)
    logger.info("%s is encrypted now", auth_file.name)


@manage_auth_files.command("decrypt")
@click.option(
    "--auth-file", "-f",
    type=click.Path(exists=False, file_okay=True),
    prompt="Please enter name for the auth file",
    callback=check_if_auth_file_exists,
    help="The auth file name (without dir) to decrypt.",
    cls=DialogOption,
)
@click.option(
    "--password", "-p",
    prompt="Please enter the password of the auth file",
    hide_input=True,
    callback=check_password_is_not_empty,
    help="The password the auth file has.",
    cls=DialogOption,
)
def decrypt_auth_file(auth_file: pathlib.Path, password: str) -> None:
    """Take the password off an auth file.

    Whatever is not given is asked for, the same way `encrypt` asks. The
    password is not repeated back here: a wrong one cannot destroy
    anything, it only fails to open the file.
    """
    auth_file = resolve_to_the_real_file(auth_file)
    shape = detect_auth_file(auth_file)

    if shape is None:
        raise AudibleCliException(f"{auth_file.name} is not an auth file")

    if shape == "plain":
        raise AudibleCliException(f"{auth_file.name} is not encrypted")

    text = read_auth_text(auth_file, shape, password)

    rewrite_auth_file(auth_file, text, None)
    logger.info("%s is not encrypted any more", auth_file.name)
