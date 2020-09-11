import asyncio
import pathlib
import ssl
import string
import sys
import unicodedata

import aiofiles
import click
import httpx
import tqdm
from audible.client import AsyncClient
from click import echo, secho
from tabulate import tabulate

from .utils import pass_config, LongestSubString


CLIENT_TIMEOUT = 15
CODEC_HIGH_QUALITY = "LC_128_44100_stereo"
CODEC_NORMAL_QUALITY = "LC_64_44100_stereo"


SSL_PROTOCOLS = (asyncio.sslproto.SSLProtocol,)

def ignore_httpx_ssl_eror(loop):
    """Ignore aiohttp #3535 / cpython #13548 issue with SSL data after close

    There is an issue in Python 3.7 up to 3.7.3 that over-reports a
    ssl.SSLError fatal error (ssl.SSLError: [SSL: KRB5_S_INIT] application data
    after close notify (_ssl.c:2609)) after we are already done with the
    connection. See GitHub issues aio-libs/aiohttp#3535 and
    python/cpython#13548.

    Given a loop, this sets up an exception handler that ignores this specific
    exception, but passes everything else on to the previous exception handler
    this one replaces.

    Checks for fixed Python versions, disabling itself when running on 3.7.4+
    or 3.8.

    """
    if sys.version_info >= (3, 7, 4):
        return

    orig_handler = loop.get_exception_handler()

    def ignore_ssl_error(loop, context):
        if context.get("message") in {
            "SSL error in data received",
            "Fatal error on transport",
        }:
            # validate we have the right exception, transport and protocol
            exception = context.get("exception")
            protocol = context.get("protocol")
            if (
                isinstance(exception, ssl.SSLError)
                and exception.reason == "KRB5_S_INIT"
                and isinstance(protocol, SSL_PROTOCOLS)
            ):
                if loop.get_debug():
                    asyncio.log.logger.debug("Ignoring httpx SSL KRB5_S_INIT error")
                return
        if orig_handler is not None:
            orig_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(ignore_ssl_error)


async def download_content(client, url, output_dir, filename,
                           overwrite_existing=False):
    output_dir = pathlib.Path(output_dir)

    if not output_dir.is_dir():
        raise Exception("Output dir doesn't exists")

    file = output_dir / filename
    tmp_file = file.with_suffix(".tmp")        

    if file.exists() and not overwrite_existing:
        secho(f"File {file} already exists. Skip download.", fg="red")
        return True

    try:
        async with client.stream("GET", url) as r:
            length = int(r.headers["Content-Length"])

            progressbar = tqdm.tqdm(
                desc=filename, total=length, unit='B', unit_scale=True,
                unit_divisor=1024
            )

            try:
                with progressbar:
                    async with aiofiles.open(tmp_file, mode="wb") as f:
                    #with progressbar, tmp_file.open("wb") as f:
                        async for chunk in r.aiter_bytes():
                            await f.write(chunk)
                            progressbar.update(len(chunk))

                if file.exists() and overwrite_existing:
                    i = 0
                    while file.with_suffix(f"{file.suffix}.old.{i}").exists():
                        i += 1
                    file.rename(file.with_suffix(f"{file.suffix}.old.{i}"))
                tmp_file.rename(file)
                tqdm.tqdm.write(f"File {file} downloaded to {output_dir} in {r.elapsed}")
                return True
            finally:
                # remove tmp_file if download breaks
                tmp_file.unlink() if tmp_file.exists() else ""
    except KeyError as e:
        secho(f"An error occured during downloading {file}", fg="red")
        return False


class LibraryItem:
    def __init__(self, item, api_client, client):
        self._data = item
        self._api_client = api_client
        self._client = client

    def __getitem__(self, key):
        return self._data[key]

    def __getattr__(self, attr):
        try:
            return self.__getitem__(attr)
        except KeyError:
            return None

    def __iter__(self):
        return iter(self._data)

    @property
    def full_title(self):
        return self.title + (f": {self.subtitle}" if self.subtitle else "")

    @property
    def full_title_slugify(self):
        valid_chars = f"-_.() " + string.ascii_letters + string.digits
        cleaned_title = unicodedata.normalize('NFKD', self.full_title)\
                        .encode('ASCII', 'ignore').replace(b" ", b"_")
        return "".join(chr(c) for c in cleaned_title if chr(c) in valid_chars)

    def substring_in_title_accuracy(self, substring):
        match = LongestSubString(substring, self.full_title)
        return round(match.percentage, 2)

    def substring_in_title(self, substring, p=100):
        accuracy = self.substring_in_title_accuracy(substring)
        return accuracy >= p

    async def get_cover(self, output_dir, overwrite_existing=False):
        url = self.product_images.get("500")
        if url is None:
            # TODO: no cover
            return

        filename = self.full_title_slugify + ".jpg"
        await download_content(client=self._client, url=url,
                               output_dir=output_dir, filename=filename,
                               overwrite_existing=overwrite_existing)

    @property
    def has_pdf(self):
        return self.pdf_url is not None

    async def get_pdf(self, output_dir, overwrite_existing=False):
        if not self.has_pdf:
        # TODO: no pdf
            return

        url = self.pdf_url

        filename = self.full_title_slugify + ".pdf"
        await download_content(client=self._client, url=url,
                               output_dir=output_dir, filename=filename,
                               overwrite_existing=overwrite_existing)

    async def get_download_link(self, codec):
        if self._client.auth.adp_token is None:
            ctx = click.get_current_context()
            ctx.fail("No adp token present. Can't get download link.")
    
        try:
            content_url = ("https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/"
                           "FSDownloadContent")
            params = {
                'type': 'AUDI',
                'currentTransportMethod': 'WIFI',
                'key': self.asin,
                'codec': codec
            }
            r = await self._client.head(
                url=content_url,
                params=params,
                allow_redirects=False)
    
            # prepare link
            # see https://github.com/mkb79/Audible/issues/3#issuecomment-518099852
            link = r.headers['Location']
            tld = self._client.auth.locale.domain
            new_link = link.replace("cds.audible.com", f"cds.audible.{tld}")
            return new_link
        except Exception as e:
            secho(f"Error: {e} occured. Can't get download link. Skip asin {self.asin}")
            return None

    def get_quality(self, verify=None):
        """If verify is set, ensures the given quality is present in the
        codecs list. Otherwise, will find the best aax quality available
        """
        best = (None, 0, 0)
        for codec in self.available_codecs:
            if verify is not None and verify == codec["enhanced_codec"]:
                return verify

            if codec["name"].startswith("aax_"):
                name = codec["name"]
                try:
                    sample_rate, bitrate = name[4:].split("_")
                    sample_rate = int(sample_rate)
                    bitrate = int(bitrate)
                    if sample_rate > best[1] or bitrate > best[2]:
                        best = (
                            codec["enhanced_codec"],
                            sample_rate,
                            bitrate
                        )

                except ValueError:
                    secho("Unexpected codec name: {name}")
                    continue

        if verify is not None:
            secho(f"{verify} codec was not found, using {best[0]} instead")

        return best[0]

    @property
    def is_downloadable(self):
        if self.content_delivery_type in ("Periodical",):
            return False

        return True        

    async def get_audiobook(self, output_dir, quality="high",
                            overwrite_existing=False):
        if not self.is_downloadable:
            secho(f"{self.full_title} is not downloadable. Skip item.", fg="red")
            return

        assert quality in ("best", "high", "normal",)
        if quality == "best":
            codec = self.get_quality()
        else:
            codec = self.get_quality(
                CODEC_HIGH_QUALITY if quality == "high" else CODEC_NORMAL_QUALITY
            )

        url = await self.get_download_link(codec)
        if not url:
        # TODO: no link
            return

        filename = self.full_title_slugify + f"-{codec}.aax"
        await download_content(client=self._client, url=url,
                               output_dir=output_dir, filename=filename,
                               overwrite_existing=overwrite_existing)


class Library:
    def __init__(self, library, api_client):
        self._data = library.get("items") or library
        self._api_client = api_client
        self._client = httpx.AsyncClient(timeout=CLIENT_TIMEOUT,
                                         auth=api_client.auth)

        self._data = [LibraryItem(i, self._api_client, self._client) \
                      for i in library.get("items") or library]

    def __iter__(self):
        return iter(self._data)

    @classmethod
    async def get_from_api(cls, auth, **params):
        api_client = AsyncClient(auth, timeout=CLIENT_TIMEOUT)
        async with api_client as client:
            library = await client.get("library", params=params)

        return cls(library, api_client)

    def get_item_by_asin(self, asin):
        try:
            return next(i for i in self._data if asin in i.asin)
        except StopIteration:
            return None

    def asin_in_library(self, asin):
        return True if self.get_item_by_asin(asin) else False

    def search_item_by_title(self, search_title, p=80):
        match = []
        for i in self._data:
            accuracy = i.substring_in_title_accuracy(search_title)
            match.append([i, accuracy]) if accuracy >= p else ""

        return match


async def consume(queue):
    while True:
        item = await queue.get()
        try:
            await item
        except Exception as e:
            secho(f"Error in job: {e}")
        queue.task_done()


async def main(loop, auth, **params):
    ignore_errors = params.get("ignore_errors")

    library = await Library.get_from_api(
        auth, response_groups="product_desc,pdf_url,media,product_attrs,relationships")

    jobs = []

    asin_list = params.get("asin")
    title_list = params.get("title")
    if params.get("all") is True:
        asin_list = []
        title_list = []
        for i in library:
            jobs.append(i.asin)

    for asin in asin_list:
        if library.asin_in_library(asin):
            jobs.append(asin)
        else:
            if not ignore_errors:
                ctx = click.get_current_context()
                ctx.fail(f"Asin {asin} not found in library.")
            secho(f"Skip asin {asin}: Not found in library", fg="red")

    for title in title_list:
        match = library.search_item_by_title(title)
        full_match = [i for i in match if i[1] == 100]

        if full_match or match:
            echo(f"\nFound the following matches for '{title}'")
            table_data = [[i[1], i[0].full_title, i[0].asin] \
                          for i in full_match or match]
            head = ["% match", "title", "asin"]
            table = tabulate(
                table_data, head, tablefmt="pretty",
                colalign=("center", "left", "center"))        
            echo(table)

            if click.confirm("Proceed with this audiobook(s)"):
                jobs.extend([i[0].asin for i in full_match or match])

        else:
            secho(f"Skip title {title}: Not found in library", fg="red")

    output_dir = pathlib.Path(params.get("output_dir")).resolve()
    overwrite_existing = params.get("overwrite")
    quality = params.get("quality")
    get_pdf = params.get("pdf")
    get_cover = params.get("pdf")

    queue = asyncio.Queue()
    for job in jobs:
        item = library.get_item_by_asin(job)
        if get_cover:
            queue.put_nowait(item.get_cover(output_dir, overwrite_existing))
        if get_pdf:
            queue.put_nowait(item.get_pdf(output_dir, overwrite_existing))
        queue.put_nowait(item.get_audiobook(output_dir, quality, overwrite_existing))

    # schedule the consumer
    sim_jobs = params.get("jobs")
    consumers = [asyncio.ensure_future(consume(queue)) for _ in range(sim_jobs)]
    
    # wait until the consumer has processed all items
    await queue.join()

    # the consumer is still awaiting for an item, cancel it
    for consumer in consumers:
        consumer.cancel()


@click.command()
@click.option(
    "--output-dir", "-o",
    type=click.Path(exists=True, dir_okay=True),
    default=pathlib.Path().cwd(),
    help="output dir, uses current working dir as default"
)
@click.option(
    "--asin", "-a",
    multiple=True,
    help="asin of the audiobook"
)
@click.option(
    "--title", "-t",
    multiple=True,
    help="tile of the audiobook (partial search)"
)
@click.option(
    "--no-confirm", "-y",
    is_flag=True,
    help="start without confirm"
)
@click.option(
    "--quality", "-q",
    default="best",
    show_default=True,
    type=click.Choice(["best", "high", "normal"]),
    help="download quality"
)
@click.option(
    "--all",
    is_flag=True,
    help="download all library items, overrides --asin and --title options"
)
@click.option(
    "--pdf",
    is_flag=True,
    help="downloads the pdf in addition to the audiobook"
)
@click.option(
    "--cover",
    is_flag=True,
    help="downloads the cover in addition to the audiobook"
)
@click.option(
    "--no-audio",
    is_flag=True,
    help="skip download audiobook (useful if you only want cover/pdf)"
)
@click.option(
    "--link-only", "-lo",
    is_flag=True,
    help="returns the download link(s) only"
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="rename existing files"
)
@click.option(
    "--ignore-errors",
    is_flag=True,
    help="ignore errors and continue with the rest"
)
@click.option(
    "--jobs", "-j",
    type=int,
    default=3,
    show_default=True,
    help="number of simultaneous downloads"
)
@pass_config
def cli(config, **params):
    """download audiobook(s) from library"""
    loop = asyncio.get_event_loop()
    ignore_httpx_ssl_eror(loop)
    try:
        loop.run_until_complete(main(loop, config.auth, **params))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
