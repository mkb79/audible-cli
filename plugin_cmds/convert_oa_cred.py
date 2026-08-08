"""Converts the credentials.json file from OpenAudible >= v2.4 beta to an
audible-cli auth file. The credentials.json file from OpenAudible leaves
unchanged, so you can use one device registration for OpenAudible and
audible-cli.
"""


import json
import pathlib
import re

import audible
import click
from click import echo

from audible_cli.decorators import pass_session
from audible_cli.exceptions import AudibleCliException


def extract_data_from_file(credentials):
    origins = {}
    for k, v in credentials.items():
        if k == "active_device":
            continue
        origin = v["details"]["response"]["success"]
        origin["additionnel"] = {
            "expires": v["expires"],
            "region": v["region"].lower()
        }
        origins.update({k: origin})
    return origins


# A POSIX timestamp this large is year 5138 in seconds but a plausible date
# in milliseconds, which is what OpenAudible writes. No Audible credential
# expires that far out, so treat anything above it as milliseconds.
MILLISECOND_THRESHOLD = 1e11

# Anything outside this is refused rather than sanitized, which keeps the
# check from having to know every way a name can mean something other than a
# file — Windows device aliases like `CONOUT$`, alternate data streams, path
# separators of either platform and unicode lookalikes all fail it already.
USABLE_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)

# Still reserved on Windows even though they pass the pattern above, with or
# without an extension
WINDOWS_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def normalize_expires(expires):
    """Return `expires` in seconds.

    audible stores this as a POSIX timestamp in seconds and hands it to
    `datetime.fromtimestamp`, which rejects a millisecond value outright.
    """
    if expires is not None and abs(expires) > MILLISECOND_THRESHOLD:
        return expires / 1000
    return expires


def is_usable_profile_name(name):
    """Whether `name` can become a file directly below the app dir.

    The name comes out of the file being converted, so it is accepted only
    when it matches a deliberately narrow pattern. Refusing everything else
    is what makes this safe: a list of things to reject would have to keep up
    with every way a name can mean something other than a plain file.
    """
    if not USABLE_PROFILE_NAME.match(name):
        return False

    return name.split(".")[0].upper() not in WINDOWS_DEVICE_NAMES


def make_auth_file(origin):
    tokens = origin["tokens"]
    adp_token = tokens["mac_dms"]["adp_token"]
    device_private_key = tokens["mac_dms"]["device_private_key"]
    # Both cookie fields are optional in audible's own registration handling
    store_authentication_cookie = tokens.get("store_authentication_cookie")
    access_token = tokens["bearer"]["access_token"]
    refresh_token = tokens["bearer"]["refresh_token"]
    expires = normalize_expires(origin["additionnel"]["expires"])

    extensions = origin["extensions"]
    device_info = extensions["device_info"]
    customer_info = extensions["customer_info"]

    website_cookies = {}
    for cookie in tokens.get("website_cookies") or []:
        website_cookies[cookie["Name"]] = cookie["Value"].replace(r'"', r"")

    data = {
        "adp_token": adp_token,
        "device_private_key": device_private_key,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires": expires,
        "website_cookies": website_cookies,
        "store_authentication_cookie": store_authentication_cookie,
        "device_info": device_info,
        "customer_info": customer_info,
        "locale": origin["additionnel"]["region"]
    }
    auth = audible.Authenticator()
    auth._update_attrs(**data)
    return auth


@click.command("convert-oa-file")
@click.option(
    "--input", "-i", "input_files",
    type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
    multiple=True,
    required=True,
    help="OpenAudible credentials.json file. Can be given more than once.")
@pass_session
def cli(session, input_files):
    """Converts a OpenAudible credential file to a audible-cli auth file.

    Stores the auth files in app dir
    """
    app_dir = session.app_dir

    for input_file in input_files:
        credentials = json.loads(input_file.read_text("utf-8"))

        for name, origin in extract_data_from_file(credentials).items():
            if not is_usable_profile_name(name):
                raise AudibleCliException(
                    f"{input_file}: {name!r} is not a usable profile name"
                )

            target = app_dir / pathlib.Path(name).with_suffix(".json")
            auth = make_auth_file(origin)
            auth.to_file(target)
            echo(f"Wrote {click.format_filename(target)}")

