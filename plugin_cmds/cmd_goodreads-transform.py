import logging
import pathlib
from datetime import datetime, timezone

import click
from audible_cli.decorators import (
    bunch_size_option,
    run_async,
    timeout_option,
    pass_session
)
from audible_cli.models import Library
from audible_cli.utils import export_to_csv
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
@timeout_option()
@bunch_size_option()
@pass_session
@run_async()
async def cli(session, **params):
    """YOUR COMMAND DESCRIPTION"""
    output = params.get("output")

    logger.debug("fetching library")
    library = await _get_library(session)

    logger.debug("prepare library")
    library = _prepare_library_for_export(library)

    logger.debug("write data rows to file")

    headers = ("isbn", "Date Added", "Date Read", "Title")
    export_to_csv(
        file=output,
        data=library,
        headers=headers,
        dialect="excel"
    )

    logger.info(f"File saved to {output}")


async def _get_library(session):
    bunch_size = session.params.get("bunch_size")

    async with session.get_client() as client:
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
