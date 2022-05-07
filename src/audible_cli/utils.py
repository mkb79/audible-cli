import csv
import io
import logging
import pathlib
from difflib import SequenceMatcher
from typing import List, Optional, Union

import aiofiles
import click
import httpx
import tqdm
from PIL import Image
from audible import Authenticator
from audible.client import raise_for_status
from audible.login import default_login_url_callback
from click import echo, secho, prompt

from .constants import DEFAULT_AUTH_FILE_ENCRYPTION


logger = logging.getLogger("audible_cli.utils")


def prompt_captcha_callback(captcha_url: str) -> str:
    """Helper function for handling captcha."""

    echo("Captcha found")
    if click.confirm("Open Captcha with default image viewer", default=True):
        captcha = httpx.get(captcha_url).content
        f = io.BytesIO(captcha)
        img = Image.open(f)
        img.show()
    else:
        echo(
            "Please open the following url with a web browser "
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


def prompt_external_callback(url: str) -> str:
    # import readline to prevent issues when input URL in
    # CLI prompt when using macOS
    try:
        import readline  # noqa
    except ImportError:
        pass

    return default_login_url_callback(url)


def full_response_callback(resp: httpx.Response) -> httpx.Response:
    raise_for_status(resp)
    return resp


def build_auth_file(
        filename: Union[str, pathlib.Path],
        username: Optional[str],
        password: Optional[str],
        country_code: str,
        file_password: Optional[str] = None,
        external_login: bool = False,
        with_username: bool = False
) -> None:
    echo()
    secho("Login with amazon to your audible account now.", bold=True)

    file_options = {"filename": pathlib.Path(filename)}
    if file_password:
        file_options.update(
            password=file_password,
            encryption=DEFAULT_AUTH_FILE_ENCRYPTION
        )

    if external_login:
        auth = Authenticator.from_login_external(
            locale=country_code,
            with_username=with_username,
            login_url_callback=prompt_external_callback)
    else:
        auth = Authenticator.from_login(
            username=username,
            password=password,
            locale=country_code,
            captcha_callback=prompt_captcha_callback,
            otp_callback=prompt_otp_callback)

    echo()

    device_name = auth.device_info["device_name"]
    secho(f"Successfully registered {device_name}.", bold=True)

    if not filename.parent.exists():
        filename.parent.mkdir(parents=True)

    auth.to_file(**file_options)


class LongestSubString:
    def __init__(
            self,
            search_for: str,
            search_in: str,
            case_sensitive: bool = False
    ) -> None:
        if case_sensitive is False:
            search_for = search_for.lower()
            search_in = search_in.lower()

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
        return self._match.size / len(self._search_for) * 100


def asin_in_library(asin, library):
    items = library.get("items") or library

    try:
        return next(i for i in items if asin in i["asin"])
    except StopIteration:
        return False


class DummyProgressBar:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def update(self, *args, **kwargs):
        pass


class Downloader:
    def __init__(
            self,
            url: Union[httpx.URL, str],
            file: Union[pathlib.Path, str],
            client,
            overwrite_existing: bool,
            content_type: Optional[Union[List[str], str]] = None
    ) -> None:
        self._url = url
        self._file = pathlib.Path(file).resolve()
        self._tmp_file = self._file.with_suffix(".tmp")
        self._client = client
        self._overwrite_existing = overwrite_existing

        if isinstance(content_type, str):
            content_type = [content_type, ]
        self._expected_content_type = content_type

    def _progressbar(self, total: int):
        return tqdm.tqdm(
            desc=click.format_filename(self._file, shorten=True),
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024
        )

    def _file_okay(self):
        if not self._file.parent.is_dir():
            logger.error(
                f"Folder {self._file.parent} doesn't exists! Skip download"
            )
            return False

        if self._file.exists() and not self._file.is_file():
            logger.error(
                f"Object {self._file} exists but is no file. Skip download"
            )
            return False

        if self._file.is_file() and not self._overwrite_existing:
            logger.info(
                f"File {self._file} already exists. Skip download"
            )
            return False

        return True

    def _postpare(self, elapsed, status_code, length, content_type):
        if not 200 <= status_code < 400:
            try:
                msg = self._tmp_file.read_text()
            except:  # noqa
                msg = "Unknown"
            logger.error(
                f"Error downloading {self._file}. Message: {msg}"
            )
            return False

        if length is not None:
            downloaded_size = self._tmp_file.stat().st_size
            length = int(length)
            if downloaded_size != length:
                logger.error(
                    f"Error downloading {self._file}. File size missmatch. "
                    f"Expected size: {length}; Downloaded: {downloaded_size}"
                )
                return False

        if self._expected_content_type is not None:
            if content_type not in self._expected_content_type:
                try:
                    msg = self._tmp_file.read_text()
                except:  # noqa
                    msg = "Unknown"
                logger.error(
                    f"Error downloading {self._file}. Wrong content type. "
                    f"Expected type(s): {self._expected_content_type}; "
                    f"Got: {content_type}; Message: {msg}"
                )
                return False

        file = self._file
        tmp_file = self._tmp_file
        if file.exists() and self._overwrite_existing:
            i = 0
            while file.with_suffix(f"{file.suffix}.old.{i}").exists():
                i += 1
            file.rename(file.with_suffix(f"{file.suffix}.old.{i}"))
        tmp_file.rename(file)
        logger.info(
            f"File {self._file} downloaded in {elapsed}."
        )
        return True

    def _remove_tmp_file(self):
        self._tmp_file.unlink() if self._tmp_file.exists() else None

    async def _stream_load(self, pb: bool = True):
        async with self._client.stream(
                "GET", self._url, follow_redirects=True
        ) as r:
            length = r.headers.get("Content-Length")
            content_type = r.headers.get("Content-Type")
            progressbar = self._progressbar(int(length)) if length and pb \
                else DummyProgressBar()

            with progressbar:
                async with aiofiles.open(self._tmp_file, mode="wb") as f:
                    async for chunk in r.aiter_bytes():
                        await f.write(chunk)
                        progressbar.update(len(chunk))

            return self._postpare(
                r.elapsed, r.status_code, length, content_type
            )

    async def _load(self):
        r = await self._client.get(self._url, follow_redirects=True)
        length = r.headers.get("Content-Length")
        content_type = r.headers.get("Content-Type")
        async with aiofiles.open(self._tmp_file, mode="wb") as f:
            await f.write(r.content)
        return self._postpare(r.elapsed, r.status_code, length, content_type)

    async def run(self, stream: bool = True, pb: bool = True):
        if not self._file_okay():
            return False

        try:
            return await self._stream_load(pb) if stream else \
                await self._load()
        finally:
            self._remove_tmp_file()


def export_to_csv(
    file: pathlib.Path,
    data: list,
    headers: Union[list, tuple],
    dialect: str
) -> None:
    with file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, dialect=dialect)
        writer.writeheader()

        for i in data:
            writer.writerow(i)
