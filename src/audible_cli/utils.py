import io
import pathlib
from difflib import SequenceMatcher
from typing import Optional, Union

import click
import httpx
from audible import Authenticator
from click import echo, secho, prompt
from PIL import Image

from .constants import DEFAULT_AUTH_FILE_ENCRYPTION


def prompt_captcha_callback(captcha_url: str) -> str:
    """Helper function for handling captcha."""

    echo("Captcha found")
    if click.confirm("Open Captcha with default image viewer", default="Y"):
        captcha = httpx.get(captcha_url).content
        f = io.BytesIO(captcha)
        img = Image.open(f)
        img.show()
    else:
        echo(
            "Please open the following url with a webbrowser "
            "to get the captcha:"
        )
        echo(captcha_url)

    guess = prompt("Answer for CAPTCHA")
    return str(guess).strip().lower()


def prompt_otp_callback() -> str:
    """Helper function for handling 2-factor authentication."""

    echo("2FA is activated for this account.")
    guess = prompt("Please enter OTP Code")
    return str(guess).strip().lower()


def build_auth_file(filename: Union[str, pathlib.Path],
                    username: str,
                    password: str,
                    country_code: str,
                    file_password: Optional[str] = None) -> None:
    echo()
    secho("Login with amazon to your audible account now.", bold=True)

    file_options = {"filename": pathlib.Path(filename)}
    if file_password:
        file_options.update(
            password=file_password, encryption=DEFAULT_AUTH_FILE_ENCRYPTION)

    auth = Authenticator.from_login(
        username=username,
        password=password,
        locale=country_code,
        captcha_callback=prompt_captcha_callback,
        otp_callback=prompt_otp_callback)

    echo()
    secho("Login was successful. Now registering a new device.", bold=True)

    auth.register_device()
    device_name = auth.device_info["device_name"]
    echo()
    secho(f"Successfully registered {device_name}.", bold=True)

    if not filename.parent.exists():
        filename.parent.mkdir(parents=True)

    auth.to_file(**file_options)


class LongestSubString:
    def __init__(self, search_for, search_in, case_sensitiv=False):
        search_for = search_for if case_sensitiv else search_for.lower()
        search_in = search_in if case_sensitiv else search_in.lower()

        self._search_for = search_for
        self._search_in = search_in
        self._s = SequenceMatcher(None, self._search_for, self._search_in)
        self._match = self.match()

    def match(self):
        return self._s.find_longest_match(
            0, len(self._search_for), 0, len(self._search_in)
        )

    @property
    def longest_match(self):
        return self._search_for[self._match.a:self._match.a + self._match.size]

    @property
    def percentage(self):
        return (self._match.size / len(self._search_for) * 100)


def asin_in_library(asin, library):
    items = library.get("items") or library

    try:
        return next(i for i in items if asin in i["asin"])
    except StopIteration:
        return False