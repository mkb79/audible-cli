import asyncio
import asyncio.log
import asyncio.sslproto
import json
import pathlib
import ssl
import sys
import unicodedata

import aiofiles
import audible
import click
import httpx
import tqdm
from audible.exceptions import NotFoundError
from click import echo, secho
from tabulate import tabulate

from ..config import pass_session
from ..models import Library
from ..utils import Downloader

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


async def download_cover(client, output_dir, base_filename, item, res,
                         overwrite_existing):
    filename = f"{base_filename}_({str(res)}).jpg"
    filepath = output_dir / filename

    url = item.get_cover_url(res)
    if url is None:
        secho(
            f"No COVER found for {item.full_title} with given resolution.",
            fg="yellow", err=True)
        return

    dl = Downloader(url, filepath, client, overwrite_existing, "image/jpeg")
    await dl.arun(stream=False, pb=False)


async def download_pdf(client, output_dir, base_filename, item,
                       overwrite_existing):
    url = item.get_pdf_url()
    if url is None:
        secho(f"No PDF found for {item.full_title}.", fg="yellow", err=True)
        return

    filename = base_filename + ".pdf"
    filepath = output_dir / filename
    dl = Downloader(
        url, filepath, client, overwrite_existing,
        ["application/octet-stream", "application/pdf"]
    )
    await dl.arun(stream=False, pb=False)


async def download_chapters(api_client, output_dir, base_filename, item,
                            quality, overwrite_existing):
    if not output_dir.is_dir():
        raise Exception("Output dir doesn't exists")

    filename = base_filename + "-chapters.json"
    file = output_dir / filename
    if file.exists() and not overwrite_existing:
        secho(
            f"File {file} already exists. Skip saving chapters.",
            fg="blue", err=True
        )
        return True

    try:
        metadata = await item.aget_content_metadata(quality, api_client)
    except NotFoundError:
        secho(f"Can't get chapters for {item.full_title}. Skip item.",
              fg="red", err=True)
        return
    metadata = json.dumps(metadata, indent=4)
    async with aiofiles.open(file, "w") as f:
        await f.write(metadata)
    tqdm.tqdm.write(f"Chapter file saved to {file}.")


async def download_aax(client, output_dir, base_filename, item, quality,
                       overwrite_existing):
    url, codec = await item.aget_aax_url(quality, client)
    filename = base_filename + f"-{codec}.aax"
    filepath = output_dir / filename
    dl = Downloader(
        url, filepath, client, overwrite_existing,
        ["audio/aax", "audio/vnd.audible.aax"]
    )
    await dl.arun(pb=True)


async def download_aaxc(api_client, client, output_dir, base_filename, item,
                        quality, overwrite_existing):
    url, codec, dlr = await item.aget_aaxc_url(quality, api_client)

    filepath = pathlib.Path(
        output_dir) / f"{base_filename}-{codec}.aaxc"
    dlr_file = filepath.with_suffix(".voucher")

    if dlr_file.is_file() and not overwrite_existing:
        secho(f"File {dlr_file} already exists. Skip download.",
              fg="blue", err=True)
    else:
        dlr = json.dumps(dlr, indent=4)
        async with aiofiles.open(dlr_file, "w") as f:
            await f.write(dlr)
        secho(f"Voucher file saved to {dlr_file}.")

    dl = Downloader(
        url, filepath, client, overwrite_existing,
        ["audio/aax", "audio/vnd.audible.aax"]
    )
    await dl.arun(pb=True)


async def consume(queue):
    while True:
        item = await queue.get()
        try:
            await item
        except Exception as e:
            secho(f"Error in job: {e}", fg="red", err=True)
        queue.task_done()


async def main(config, auth, **params):
    output_dir = pathlib.Path(params.get("output_dir")).resolve()

    # which item(s) to download
    get_all = params.get("all") is True
    asins = params.get("asin")
    titles = params.get("title")
    if get_all and (asins or titles):
        ctx = click.get_current_context()
        ctx.fail(f"Do not mix *asin* or *title* option with *all* option.")

    # what to download
    get_aax = params.get("aax")
    get_aaxc = params.get("aaxc")
    get_chapters = params.get("chapter")
    get_cover = params.get("cover")
    get_pdf = params.get("pdf")
    if not any([get_aax, get_aaxc, get_chapters, get_cover, get_pdf]):
        ctx = click.get_current_context()
        ctx.fail(f"Please select a option what you want download.")

    # additional options
    sim_jobs = params.get("jobs")
    quality = params.get("quality")
    cover_size = params.get("cover_size")
    overwrite_existing = params.get("overwrite")
    ignore_errors = params.get("ignore_errors")
    no_confirm = params.get("no_confirm")
    timeout = params.get("timeout")
    if timeout == 0:
        timeout = None

    filename_mode = params.get("filename_mode")
    if filename_mode == "config":
        filename_mode = config.profile_config.get("filename_mode") or \
                        config.app_config.get("filename_mode") or \
                        "ascii"

    # fetch the user library
    async with audible.AsyncClient(auth, timeout=timeout) as client:
        library = await Library.aget_from_api(
            client,
            response_groups=("product_desc, pdf_url, media, product_attrs, "
                             "relationships"),
            image_sizes="1215, 408, 360, 882, 315, 570, 252, 558, 900, 500")

    jobs = []

    if get_all:
        asins = []
        titles = []
        for i in library:
            jobs.append(i.asin)

    for asin in asins:
        if library.asin_in_library(asin):
            jobs.append(asin)
        else:
            if not ignore_errors:
                ctx = click.get_current_context()
                ctx.fail(f"Asin {asin} not found in library.")
            secho(f"Skip asin {asin}: Not found in library", fg="red", err=True)

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

            if no_confirm or click.confirm("Proceed with this audiobook(s)",
                    default=True):
                jobs.extend([i[0].asin for i in full_match or match])

        else:
            secho(f"Skip title {title}: Not found in library", fg="red", err=True)

    queue = asyncio.Queue()

    headers = {
        "User-Agent": "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"
    }
    client = httpx.AsyncClient(auth=auth, timeout=timeout, headers=headers)
    api_client = audible.AsyncClient(auth, timeout=timeout)
    async with client, api_client:
        for job in jobs:
            item = library.get_item_by_asin(job)
            base_filename = create_base_filename(item=item, mode=filename_mode)
            if get_cover:
                queue.put_nowait(
                    download_cover(client=client,
                                   output_dir=output_dir,
                                   base_filename=base_filename,
                                   item=item,
                                   res=cover_size,
                                   overwrite_existing=overwrite_existing))

            if get_pdf:
                queue.put_nowait(
                    download_pdf(client=client,
                                 output_dir=output_dir,
                                 base_filename=base_filename,
                                 item=item,
                                 overwrite_existing=overwrite_existing))

            if get_chapters:
                queue.put_nowait(
                    download_chapters(api_client=api_client,
                                      output_dir=output_dir,
                                      base_filename=base_filename,
                                      item=item,
                                      quality=quality,
                                      overwrite_existing=overwrite_existing))

            if get_aax:
                queue.put_nowait(
                    download_aax(client=client,
                                 output_dir=output_dir,
                                 base_filename=base_filename,
                                 item=item,
                                 quality=quality,
                                 overwrite_existing=overwrite_existing))

            if get_aaxc:
                queue.put_nowait(
                    download_aaxc(api_client=api_client,
                                  client=client,
                                  output_dir=output_dir,
                                  base_filename=base_filename,
                                  item=item,
                                  quality=quality,
                                  overwrite_existing=overwrite_existing))

        # schedule the consumer
        consumers = [asyncio.ensure_future(consume(queue)) for _ in
                     range(sim_jobs)]

        # wait until the consumer has processed all items
        await queue.join()

        # the consumer is still awaiting for an item, cancel it
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
    help="Increase the timeout time if you got any TimeoutErrors. Set to 0 to disable timeout."
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
