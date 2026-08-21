import asyncio
import csv
import io
import logging
import pathlib
import random
from datetime import UTC, datetime
from difflib import SequenceMatcher

import aiofiles
import click
import httpx
from audible import Authenticator
from audible.client import raise_for_status
from audible.exceptions import (
    NetworkError,
    NotResponding,
    RequestError,
    StatusError,
)
from audible.login import playwright_external_login_url_callback
from PIL import Image

from ._dialog import ask, confirm, say
from .constants import DEFAULT_AUTH_FILE_ENCRYPTION
from .progress import progress_disabled, take_progressbar


logger = logging.getLogger("audible_cli.utils")


def is_transient(exc: BaseException) -> bool:
    """Whether a request never got an answer and is worth repeating.

    An answer is not transient: every `StatusError` carries an HTTP status.
    The audible client remaps httpx errors and raises `from None`, so the
    original sits in `args`, not in the cause.
    """
    if isinstance(exc, httpx.TransportError):
        return not isinstance(exc, httpx.LocalProtocolError)
    if isinstance(exc, StatusError):
        return False
    if isinstance(exc, NotResponding | NetworkError):
        return True
    if isinstance(exc, RequestError):
        return any(
            isinstance(arg, httpx.TransportError)
            and not isinstance(arg, httpx.LocalProtocolError)
            for arg in exc.args
        )
    return False


async def request_with_retry(
    make_request, describe: str, *, attempts: int, first_delay: float
):
    """Make a request again while it gets no answer at all.

    Only for requests that can be repeated without consequence. How many
    times and how long to wait are the caller's to decide: `attempts` counts
    the total, and the delay starts at `first_delay` seconds, doubles, and
    carries jitter so callers that fail together do not retry together.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, not {attempts}")

    for attempt in range(1, attempts + 1):
        try:
            return await make_request()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            delay = first_delay * 2 ** (attempt - 1) * random.uniform(0.8, 1.2)  # noqa: S311
            logger.warning(
                "%s got no answer (%s: %s). Attempt %s of %s, retrying in %.1fs.",
                describe,
                type(exc).__name__,
                exc,
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
    return None  # unreachable: the loop returns or raises


def to_utc_datetime(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC.

    Naive values are interpreted as UTC. That matches the documented
    semantics of the ``--start-date`` and ``--end-date`` options, which ask
    for a UTC date, and it keeps values coming from :data:`datetime_type`
    comparable to the timestamps parsed from API responses.
    """
    if value.utcoffset() is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def parse_api_datetime(value: str) -> datetime:
    """Parse a timestamp as returned by the Audible API as UTC.

    The API uses both variants, with and without fractional seconds
    (``2019-11-29T11:40:49.000Z`` vs. ``2019-11-29T11:40:49Z``), sometimes
    for the same field. Top-level library items usually carry the fraction,
    child episodes of a podcast usually do not, so both have to be accepted.

    Accepts anything :meth:`datetime.datetime.fromisoformat` understands as
    long as it carries timezone information, and normalizes it to UTC. That
    is deliberately wider than the two shapes above: an offset other than
    ``Z`` denotes the same instant and is worth accepting, while a timestamp
    without any offset is ambiguous and is not.

    Raises:
        ValueError: If the value is not a timestamp
            :meth:`~datetime.datetime.fromisoformat` accepts, or if it does
            not carry timezone information.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid API datetime {value!r}") from exc

    if parsed.utcoffset() is None:
        raise ValueError(f"API datetime {value!r} has no timezone information")

    return parsed.astimezone(UTC)


class UTCDateTime(click.DateTime):
    """A :class:`click.DateTime` that always yields UTC values.

    The accepted formats either end in a literal ``Z`` or carry no timezone
    at all, so :class:`click.DateTime` always returns a naive value. Both
    cases mean UTC here, so the result is marked as such.
    """

    def convert(self, value, param, ctx):
        return to_utc_datetime(super().convert(value, param, ctx))


datetime_type = UTCDateTime([
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ"
])


def prompt_captcha_callback(captcha_url: str) -> str:
    """Helper function for handling captcha."""
    say("Captcha found")
    if confirm("Open Captcha with default image viewer", default=True):
        captcha = httpx.get(captcha_url).content
        f = io.BytesIO(captcha)
        img = Image.open(f)
        img.show()
    else:
        say(
            "Please open the following url with a web browser "
            "to get the captcha:"
        )
        say(captcha_url)

    guess = ask("Answer for CAPTCHA")
    return str(guess).strip().lower()


def prompt_otp_callback() -> str:
    """Helper function for handling 2-factor authentication."""
    say("2FA is activated for this account.")
    guess = ask("Please enter OTP Code")
    return str(guess).strip().lower()


def prompt_cvf_callback() -> str:
    """Helper function for handling the code Amazon sends by mail or SMS."""
    say("Amazon has sent a verification code by mail or SMS.")
    guess = ask("Please enter CVF Code")
    return str(guess).strip().lower()


def prompt_approval_callback() -> None:
    """Helper function for handling an approval alert."""
    say("Approval alert detected! Amazon sends you a mail.")
    ask(
        "Please press ENTER when you approve the notification",
        default="",
        show_default=False,
    )


EXTERNAL_LOGIN_INSTRUCTIONS = """\
Please copy the following url and insert it into a web browser of your choice:

{url}

Now you have to login with your Amazon credentials. After submit your username
and password you have to do this a second time and solving a captcha before
sending the login form.

After login, your browser will show you an error page (Page not found). Do not
worry about this. It has to be like this. Please copy the url from the address
bar in your browser now."""


def prompt_external_callback(url: str) -> str:
    """Ask for the login url a browser was redirected to.

    The audible library has a callback of its own, but it holds the
    conversation with `print` and `input`. The browser route it tries
    first has nothing to print, so that one is still worth asking for.
    """
    # import readline to prevent issues when input URL in
    # CLI prompt when using macOS
    try:
        import readline  # noqa: F401, PLC0415
    except ImportError:
        pass

    try:
        return playwright_external_login_url_callback(url)
    except ImportError:
        pass

    say()
    say(EXTERNAL_LOGIN_INSTRUCTIONS.format(url=url))
    say()

    return ask("Please insert the copied url (after login)")


def full_response_callback(resp: httpx.Response) -> httpx.Response:
    raise_for_status(resp)
    return resp


def build_auth_file(
        filename: str | pathlib.Path,
        username: str | None,
        password: str | None,
        country_code: str,
        file_password: str | None = None,
        external_login: bool = False,
        with_username: bool = False
) -> None:
    say()
    say("Login with amazon to your audible account now.", bold=True)

    # Normalize once: the signature allows a str, but the parent directory is
    # created further down, after the login has already registered the device
    filename = pathlib.Path(filename)
    file_options = {"filename": filename}
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
            otp_callback=prompt_otp_callback,
            cvf_callback=prompt_cvf_callback,
            approval_callback=prompt_approval_callback)

    device_name = auth.device_info["device_name"]
    logger.info("Successfully registered %s.", device_name)

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

    def close(self):
        # Callers release a bar by closing it, and a bar that shows nothing
        # still has to accept that
        pass


class Downloader:
    def __init__(
            self,
            url: httpx.URL | str,
            file: pathlib.Path | str,
            client,
            overwrite_existing: bool,
            content_type: list[str] | str | None = None
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
        if progress_disabled():
            return DummyProgressBar()

        docked = take_progressbar(self._file, total=total)
        if docked is not None:
            return docked

        # No row from the dock, so nothing. A bar placed the old way drifts
        # with the log output and draws over its neighbours, and the dock is
        # meant to be the only way progress is shown.
        return DummyProgressBar()

    def _file_okay(self):
        if not self._file.parent.is_dir():
            logger.error("Folder %s doesn't exists! Skip download", self._file.parent)
            return False

        if self._file.exists() and not self._file.is_file():
            logger.error("Object %s exists but is no file. Skip download", self._file)
            return False

        if self._file.is_file() and not self._overwrite_existing:
            logger.info("File %s already exists. Skip download", self._file)
            return False

        return True

    def _postpare(self, elapsed, status_code, length, content_type):
        if not 200 <= status_code < 400:
            try:
                msg = self._tmp_file.read_text()
            except Exception:
                msg = "Unknown"
            logger.error("Error downloading %s. Message: %s", self._file, msg)
            return False

        if length is not None:
            downloaded_size = self._tmp_file.stat().st_size
            length = int(length)
            if downloaded_size != length:
                logger.error(
                    "Error downloading %s. File size missmatch. Expected size: %s; "
                    "Downloaded: %s",
                    self._file,
                    length,
                    downloaded_size
                )
                return False

        if self._expected_content_type is not None:
            if content_type not in self._expected_content_type:
                try:
                    msg = self._tmp_file.read_text()
                except Exception:
                    msg = "Unknown"
                logger.error(
                    "Error downloading %s. Wrong content type. Expected type(s): %s; "
                    "Got: %s; Message: %s",
                    self._file,
                    self._expected_content_type,
                    content_type,
                    msg
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
        logger.info("File %s downloaded in %s.", self._file, elapsed)
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
    headers: list | tuple,
    dialect: str
) -> None:
    with file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, dialect=dialect)
        writer.writeheader()

        for i in data:
            writer.writerow(i)
