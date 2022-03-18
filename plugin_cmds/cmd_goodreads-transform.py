import asyncio
import csv
import logging
import pathlib
from datetime import datetime, timezone

import audible
import click
from audible_cli.config import pass_session
from audible_cli.models import Library
from isbntools.app import isbn_from_words


logger = logging.getLogger("audible_cli.cmds.cmd_goodreads-transform")


@click.command("goodreads-transform")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=pathlib.Path),
    default=pathlib.Path().cwd() / "library.csv",
    show_default=True,
    help="output file"
)
@click.option(
    "--timeout", "-t",
    type=click.INT,
    default=10,
    show_default=True,
    help=(
        "Increase the timeout time if you got any TimeoutErrors. "
        "Set to 0 to disable timeout."
    )
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
    """YOUR COMMAND DESCRIPTION"""
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(_goodreads_transform(session.auth, **params))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


async def _goodreads_transform(auth, **params):
    output = params.get("output")

    logger.debug("fetching library")
    library = await _get_library(auth, **params)

    logger.debug("prepare library")
    library = _prepare_library_for_export(library)

    logger.debug("write data rows to file")
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["isbn", "Date Added", "Date Read", "Title"])

        for row in library:
            writer.writerow(row)

    logger.info(f"File saved to {output}")


async def _get_library(auth, **params):
    timeout = params.get("timeout")
    if timeout == 0:
        timeout = None

    bunch_size = params.get("bunch_size")

    async with audible.AsyncClient(auth, timeout=timeout) as client:
        # added product_detail to response_groups to obtain isbn
        library = await Library.from_api_full_sync(
            client,
            response_groups=(
                "product_details, contributors, is_finished, product_desc"
            ),
            bunch_size=bunch_size
        )
    return library


def _prepare_library_for_export(library):
    prepared_library = []

    isbn_counter = 0
    isbn_api_counter = 0
    isbn_no_result_counter = 0
    skipped_items = 0

    for i in library:
        title = i.title
        authors = i.authors
        if authors is not None:
            authors = ", ".join([a["name"] for a in authors])
        is_finished = i.is_finished
        
        isbn = i.isbn
        if isbn is None:
            isbn_counter += 1
            isbn = isbn_from_words(f"{title} {authors}") or None
            if isbn is None:
                isbn_no_result_counter += 1
        else:
            isbn_api_counter += 1

        date_added = i.library_status
        if date_added is not None:
            date_added = date_added["date_added"]
            date_added = datetime.strptime(
                date_added, '%Y-%m-%dT%H:%M:%S.%fZ'
            ).replace(tzinfo=timezone.utc).astimezone()    
            date_added = date_added.astimezone().date().isoformat()

        date_read = None
        if is_finished:
            date_read = date_added

        if isbn and date_read:
            data_row = [isbn, date_added, date_read, title]
            prepared_library.append(data_row)
        else:
            skipped_items += 1

    logger.debug(f"{isbn_api_counter} isbns from API")
    logger.debug(f"{isbn_counter} isbns requested with isbntools")
    logger.debug(f"{isbn_no_result_counter} isbns without a result")
    logger.debug(f"{skipped_items} title skipped due to no isbn for title found or title not read")

    return prepared_library
