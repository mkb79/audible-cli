import asyncio
import asyncio.log
import asyncio.sslproto
import json
import pathlib
import ssl
import logging
import sys
import unicodedata

import aiofiles
import audible
import click
import httpx
from click import echo
from tabulate import tabulate

from ..config import pass_session
from ..exceptions import DirectoryDoesNotExists, NotFoundError
from ..models import Library
from ..utils import Downloader


logger = logging.getLogger("audible_cli.cmds.cmd_download")

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

    def ignore_ssl_error(context):
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
                    asyncio.log.logger.debug(
                        "Ignoring httpx SSL KRB5_S_INIT error")
                return
        if orig_handler is not None:
            orig_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(ignore_ssl_error)


class DownloadCounter:
    def __init__(self):
        self._aax: int = 0
        self._aaxc: int = 0
        self._chapter: int = 0
        self._cover: int = 0
        self._pdf: int = 0
        self._voucher: int = 0
        self._voucher_saved: int = 0

    @property
    def aax(self):
        return self._aax

    def count_aax(self):
        self._aax += 1
        logger.debug(f"Currently downloaded aax files: {self.aax}")

    @property
    def aaxc(self):
        return self._aaxc

    def count_aaxc(self):
        self._aaxc += 1
        logger.debug(f"Currently downloaded aaxc files: {self.aaxc}")

    @property
    def chapter(self):
        return self._chapter

    def count_chapter(self):
        self._chapter += 1
        logger.debug(f"Currently downloaded chapters: {self.chapter}")

    @property
    def cover(self):
        return self._cover

    def count_cover(self):
        self._cover += 1
        logger.debug(f"Currently downloaded covers: {self.cover}")

    @property
    def pdf(self):
        return self._pdf

    def count_pdf(self):
        self._pdf += 1
        logger.debug(f"Currently downloaded PDFs: {self.pdf}")

    @property
    def voucher(self):
        return self._voucher

    def count_voucher(self):
        self._voucher += 1
        logger.debug(f"Currently downloaded voucher files: {self.voucher}")

    @property
    def voucher_saved(self):
        return self._voucher_saved

    def count_voucher_saved(self):
        self._voucher_saved += 1
        logger.debug(f"Currently saved voucher files: {self.voucher_saved}")

    def as_dict(self) -> dict:
        return {
            "aax": self.aax,
            "aaxc": self.aaxc,
            "chapter": self.chapter,
            "cover": self.cover,
            "pdf": self.pdf,
            "voucher": self.voucher,
            "voucher_saved": self.voucher_saved
        }

    def has_downloads(self):
        for _, v in self.as_dict().items():
            if v > 0:
                return True

        return False


counter = DownloadCounter()


def create_base_filename(item, mode):
    if "ascii" in mode:
        base_filename = item.full_title_slugify

    elif "unicode" in mode:
        base_filename = unicodedata.normalize("NFKD", item.full_title)

    else:
        base_filename = item.asin

    if "asin" in mode:
        base_filename = item.asin + "_" + base_filename

    return base_filename


async def download_cover(
        client, output_dir, base_filename, item, res, overwrite_existing
):
    filename = f"{base_filename}_({str(res)}).jpg"
    filepath = output_dir / filename

    url = item.get_cover_url(res)
    if url is None:
        logger.error(
            f"No COVER found for {item.full_title} with given resolution"
        )
        return

    dl = Downloader(url, filepath, client, overwrite_existing, "image/jpeg")
    downloaded = await dl.run(stream=False, pb=False)

    if downloaded:
        counter.count_cover()


async def download_pdf(
        client, output_dir, base_filename, item, overwrite_existing
):
    url = item.get_pdf_url()
    if url is None:
        logger.info(f"No PDF found for {item.full_title}")
        return

    filename = base_filename + ".pdf"
    filepath = output_dir / filename
    dl = Downloader(
        url, filepath, client, overwrite_existing,
        ["application/octet-stream", "application/pdf"]
    )
    downloaded = await dl.run(stream=False, pb=False)

    if downloaded:
        counter.count_pdf()


async def download_chapters(
        output_dir, base_filename, item, quality, overwrite_existing
):
    if not output_dir.is_dir():
        raise DirectoryDoesNotExists(output_dir)

    filename = base_filename + "-chapters.json"
    file = output_dir / filename
    if file.exists() and not overwrite_existing:
        logger.info(
            f"File {file} already exists. Skip saving chapters"
        )
        return True

    try:
        metadata = await item.get_content_metadata(quality)
    except NotFoundError:
        logger.error(
            f"Can't get chapters for {item.full_title}. Skip item."
        )
        return
    metadata = json.dumps(metadata, indent=4)
    async with aiofiles.open(file, "w") as f:
        await f.write(metadata)
    logger.info(f"Chapter file saved to {file}.")
    counter.count_chapter()


async def download_aax(
        client, output_dir, base_filename, item, quality, overwrite_existing
):
    # url, codec = await item.get_aax_url(quality)
    url, codec = await item.get_aax_url_old(quality)
    filename = base_filename + f"-{codec}.aax"
    filepath = output_dir / filename
    dl = Downloader(
        url, filepath, client, overwrite_existing,
        ["audio/aax", "audio/vnd.audible.aax", "audio/audible"]
    )
    downloaded = await dl.run(pb=True)

    if downloaded:
        counter.count_aax()


async def download_aaxc(
        client, output_dir, base_filename, item,
        quality, overwrite_existing
):
    lr, url, codec = None, None, None

    # https://github.com/mkb79/audible-cli/issues/60
    if not overwrite_existing:
        codec, _ = item._get_codec(quality)
        if codec is not None:
            filepath = pathlib.Path(
                output_dir) / f"{base_filename}-{codec}.aaxc"
            lr_file = filepath.with_suffix(".voucher")
        
            if lr_file.is_file():
                if filepath.is_file():
                    logger.info(
                        f"File {lr_file} already exists. Skip download."
                    )
                    logger.info(
                        f"File {filepath} already exists. Skip download."
                    )
                    return
                else:
                    logger.info(
                        f"Loading data from voucher file {lr_file}."
                    )
                    async with aiofiles.open(lr_file, "r") as f:
                        lr = await f.read()
                    lr = json.loads(lr)
                    content_metadata = lr["content_license"][
                        "content_metadata"]
                    url = httpx.URL(
                        content_metadata["content_url"]["offline_url"])
                    codec = content_metadata["content_reference"][
                        "content_format"]

    if url is None or codec is None or lr is None:
        url, codec, lr = await item.get_aaxc_url(quality)
        counter.count_voucher()

    if codec.lower() == "mpeg":
        ext = "mp3"
    else:
        ext = "aaxc"

    filepath = pathlib.Path(
        output_dir) / f"{base_filename}-{codec}.{ext}"
    lr_file = filepath.with_suffix(".voucher")

    if lr_file.is_file() and not overwrite_existing:
        logger.info(
            f"File {lr_file} already exists. Skip download."
        )
    else:
        lr = json.dumps(lr, indent=4)
        async with aiofiles.open(lr_file, "w") as f:
            await f.write(lr)
        logger.info(f"Voucher file saved to {lr_file}.")
        counter.count_voucher_saved()

    dl = Downloader(
        url,
        filepath,
        client,
        overwrite_existing,
        [
            "audio/aax", "audio/vnd.audible.aax", "audio/mpeg", "audio/x-m4a",
            "audio/audible"
        ]
    )
    downloaded = await dl.run(pb=True)

    if downloaded:
        counter.count_aaxc()


async def consume(queue):
    while True:
        item = await queue.get()
        try:
            await item
        except Exception as e:
            logger.error(e)
        queue.task_done()


def queue_job(
        queue,
        get_cover,
        get_pdf,
        get_chapters,
        get_aax,
        get_aaxc,
        client,
        output_dir,
        filename_mode,
        item,
        cover_size,
        quality,
        overwrite_existing
):
    base_filename = create_base_filename(item=item, mode=filename_mode)

    if get_cover:
        queue.put_nowait(
            download_cover(
                client=client,
                output_dir=output_dir,
                base_filename=base_filename,
                item=item,
                res=cover_size,
                overwrite_existing=overwrite_existing
            )
        )

    if get_pdf:
        queue.put_nowait(
            download_pdf(
                client=client,
                output_dir=output_dir,
                base_filename=base_filename,
                item=item,
                overwrite_existing=overwrite_existing
            )
        )

    if get_chapters:
        queue.put_nowait(
            download_chapters(
                output_dir=output_dir,
                base_filename=base_filename,
                item=item,
                quality=quality,
                overwrite_existing=overwrite_existing
            )
        )

    if get_aax:
        queue.put_nowait(
            download_aax(
                client=client,
                output_dir=output_dir,
                base_filename=base_filename,
                item=item,
                quality=quality,
                overwrite_existing=overwrite_existing
            )
        )

    if get_aaxc:
        queue.put_nowait(
            download_aaxc(
                client=client,
                output_dir=output_dir,
                base_filename=base_filename,
                item=item,
                quality=quality,
                overwrite_existing=overwrite_existing
            )
        )


async def main(config, auth, **params):
    output_dir = pathlib.Path(params.get("output_dir")).resolve()

    # which item(s) to download
    get_all = params.get("all") is True
    asins = params.get("asin")
    titles = params.get("title")
    if get_all and (asins or titles):
        logger.error(f"Do not mix *asin* or *title* option with *all* option.")
        click.Abort()

    # what to download
    get_aax = params.get("aax")
    get_aaxc = params.get("aaxc")
    get_chapters = params.get("chapter")
    get_cover = params.get("cover")
    get_pdf = params.get("pdf")
    if not any([get_aax, get_aaxc, get_chapters, get_cover, get_pdf]):
        logger.error("Please select an option what you want download.")
        click.Abort()

    # additional options
    sim_jobs = params.get("jobs")
    quality = params.get("quality")
    cover_size = params.get("cover_size")
    overwrite_existing = params.get("overwrite")
    ignore_errors = params.get("ignore_errors")
    no_confirm = params.get("no_confirm")
    resolve_podcats = params.get("resolve_podcasts")
    ignore_podcasts = params.get("ignore_podcasts")
    bunch_size = params.get("bunch_size")
    timeout = params.get("timeout")
    if timeout == 0:
        timeout = None

    filename_mode = params.get("filename_mode")
    if filename_mode == "config":
        filename_mode = config.profile_config.get("filename_mode") or \
                        config.app_config.get("filename_mode") or \
                        "ascii"

    headers = {
        "User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"
    }
    client = httpx.AsyncClient(auth=auth, timeout=timeout, headers=headers)
    api_client = audible.AsyncClient(auth, timeout=timeout)

    async with client, api_client:
        # fetch the user library
        library = await Library.from_api_full_sync(
            api_client,
            image_sizes="1215, 408, 360, 882, 315, 570, 252, 558, 900, 500",
            bunch_size=bunch_size
        )

        if resolve_podcats:
            await library.resolve_podcats()

        # collect jobs
        jobs = []

        if get_all:
            asins = []
            titles = []
            for i in library:
                jobs.append(i.asin)

        for asin in asins:
            if library.has_asin(asin):
                jobs.append(asin)
            else:
                if not ignore_errors:
                    logger.error(f"Asin {asin} not found in library.")
                    click.Abort()
                logger.error(
                    f"Skip asin {asin}: Not found in library"
                )

        for title in titles:
            match = library.search_item_by_title(title)
            full_match = [i for i in match if i[1] == 100]
    
            if full_match or match:
                echo(f"\nFound the following matches for '{title}'")
                table_data = [[i[1], i[0].full_title, i[0].asin]
                              for i in full_match or match]
                head = ["% match", "title", "asin"]
                table = tabulate(
                    table_data, head, tablefmt="pretty",
                    colalign=("center", "left", "center"))
                echo(table)
    
                if no_confirm or click.confirm(
                        "Proceed with this audiobook(s)",
                        default=True
                ):
                    jobs.extend([i[0].asin for i in full_match or match])
    
            else:
                logger.error(
                    f"Skip title {title}: Not found in library"
                )

        queue = asyncio.Queue()

        for job in jobs:
            item = library.get_item_by_asin(job)
            items = [item]
            odir = pathlib.Path(output_dir)

            if not ignore_podcasts and item.is_parent_podcast():
                items.remove(item)
                if item._children is None:
                    await item.get_child_items()

                for i in item._children:
                    if i.asin not in jobs:
                        items.append(i)

                podcast_dir = create_base_filename(item, filename_mode)
                odir = output_dir / podcast_dir
                if not odir.is_dir():
                    odir.mkdir(parents=True)

            for item in items:
                queue_job(
                    queue=queue,
                    get_cover=get_cover,
                    get_pdf=get_pdf,
                    get_chapters=get_chapters,
                    get_aax=get_aax,
                    get_aaxc=get_aaxc,
                    client=client,
                    output_dir=odir,
                    filename_mode=filename_mode,
                    item=item,
                    cover_size=cover_size,
                    quality=quality,
                    overwrite_existing=overwrite_existing
                )

        # schedule the consumer
        consumers = [
            asyncio.ensure_future(consume(queue)) for _ in range(sim_jobs)
        ]

        # wait until the consumer has processed all items
        await queue.join()

        # the consumer is still awaiting an item, cancel it
        for consumer in consumers:
            consumer.cancel()


@click.command("download")
@click.option(
    "--output-dir", "-o",
    type=click.Path(exists=True, dir_okay=True),
    default=pathlib.Path().cwd(),
    help="output dir, uses current working dir as default"
)
@click.option(
    "--all",
    is_flag=True,
    help="download all library items, overrides --asin and --title options"
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
    "--aax",
    is_flag=True,
    help="Download book in aax format"
)
@click.option(
    "--aaxc",
    is_flag=True,
    help="Download book in aaxc format incl. voucher file"
)
@click.option(
    "--quality", "-q",
    default="best",
    show_default=True,
    type=click.Choice(["best", "high", "normal"]),
    help="download quality"
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
    "--cover-size",
    type=click.Choice(["252", "315", "360", "408", "500", "558", "570", "882",
                       "900", "1215"]),
    default="500",
    help="the cover pixel size"
)
@click.option(
    "--chapter",
    is_flag=True,
    help="saves chapter metadata as JSON file"
)
@click.option(
    "--no-confirm", "-y",
    is_flag=True,
    help="start without confirm"
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
@click.option(
    "--filename-mode", "-f",
    type=click.Choice(
        ["config", "ascii", "asin_ascii", "unicode", "asin_unicode"]
    ),
    default="config",
    help="Filename mode to use. [default: config]"
)
@click.option(
    "--timeout",
    type=click.INT,
    default=10,
    show_default=True,
    help="Increase the timeout time if you got any TimeoutErrors. "
         "Set to 0 to disable timeout."
)
@click.option(
    "--resolve-podcasts",
    is_flag=True,
    help="Resolve podcasts to download a single episode via asin or title"
)
@click.option(
    "--ignore-podcasts",
    is_flag=True,
    help="Ignore a podcast if it have episodes"
)
@click.option(
    "--bunch-size",
    type=click.IntRange(10, 1000),
    default=1000,
    show_default=True,
    help="How many library items should be requested per request. A lower "
         "size results in more requests to get the full library. A higher "
         "size can result in a TimeOutError on low internet connections."
)
@pass_session
def cli(session, **params):
    """download audiobook(s) from library"""
    loop = asyncio.get_event_loop()
    ignore_httpx_ssl_eror(loop)
    auth = session.auth
    config = session.config
    try:
        loop.run_until_complete(main(config, auth, **params))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

        if counter.has_downloads():
            echo("The download ended with the following result:")
            for k, v in counter.as_dict().items():
                if v == 0:
                    continue
    
                if k == "voucher_saved":
                    k = "voucher"
                elif k == "voucher":
                    diff = v - counter.voucher_saved
                    if diff > 0:
                        echo(f"Unsaved voucher: {diff}")
                    continue
                echo(f"New {k} files: {v}")
        else:
            echo("No new files downloaded.")
