import asyncio
import pathlib
import ssl
import sys

import click
from click import echo, secho
from tabulate import tabulate

from .models import Library
from .config import pass_config


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


async def consume(queue):
    while True:
        item = await queue.get()
        try:
            await item
        except Exception as e:
            secho(f"Error in job: {e}")
        queue.task_done()


async def main(auth, **params):
    ignore_errors = params.get("ignore_errors")

    library = await Library.get_from_api(
        auth,
        response_groups="product_desc,pdf_url,media,product_attrs,relationships",
        num_results=1000)

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
    get_cover = params.get("cover")
    get_audio = params.get("no_audio") is not True
    get_aaxc = params.get("aaxc")

    queue = asyncio.Queue()
    for job in jobs:
        item = library.get_item_by_asin(job)
        if get_cover:
            queue.put_nowait(item.get_cover(output_dir, overwrite_existing))
        if get_pdf:
            queue.put_nowait(item.get_pdf(output_dir, overwrite_existing))
        if get_audio:
            if get_aaxc:
                queue.put_nowait(item.get_audiobook_aaxc(output_dir, quality, overwrite_existing))
            else:
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
@click.option(
    "--aaxc",
    is_flag=True,
    help="Experimental: Downloading aaxc files and voucher instead of aax"
)
@pass_config
def cli(config, **params):
    """download audiobook(s) from library"""
    loop = asyncio.get_event_loop()
    ignore_httpx_ssl_eror(loop)
    try:
        loop.run_until_complete(main(config.auth, **params))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
