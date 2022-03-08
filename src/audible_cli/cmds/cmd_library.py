import asyncio
import csv
import json
import pathlib
from typing import Union

import audible
import click
from click import echo

from ..config import pass_session
from ..models import Library


@click.group("library")
def cli():
    """interact with library"""


async def _get_library(auth, **params):
    timeout = params.get("timeout")
    if timeout == 0:
        timeout = None

    bunch_size = params.get("bunch_size")

    async with audible.AsyncClient(auth, timeout=timeout) as client:
        library = await Library.from_api_full_sync(
            client,
            response_groups=(
                "contributors, media, price, product_attrs, product_desc, "
                "product_extended_attrs, product_plan_details, product_plans, "
                "rating, sample, sku, series, reviews, ws4v, origin, "
                "relationships, review_attrs, categories, badge_types, "
                "category_ladders, claim_code_url, is_downloaded, "
                "is_finished, is_returnable, origin_asin, pdf_url, "
                "percent_complete, provided_review"
            ),
            bunch_size=bunch_size
        )
    return library


async def _list_library(auth, **params):
    library = await _get_library(auth, **params)

    books = []

    for item in library:
        asin = item.asin
        authors = ", ".join(
            sorted(a["name"] for a in item.authors) if item.authors else ""
        )
        series = ", ".join(
            sorted(s["title"] for s in item.series) if item.series else ""
        )
        title = item.title
        books.append((asin, authors, series, title))

    for asin, authors, series, title in sorted(books):
        fields = [asin]
        if authors:
            fields.append(authors)
        if series:
            fields.append(series)
        fields.append(title)
        echo(": ".join(fields))


def _prepare_library_for_export(library: Library):
    keys_with_raw_values = (
        "asin", "title", "subtitle", "runtime_length_min", "is_finished",
        "percent_complete", "release_date"
    )

    prepared_library = []

    for item in library:
        data_row = {}
        for key in item:
            v = getattr(item, key)
            if v is None:
                pass
            elif key in keys_with_raw_values:
                data_row[key] = v
            elif key in ("authors", "narrators"):
                data_row[key] = ", ".join([i["name"] for i in v])
            elif key == "series":
                data_row["series_title"] = v[0]["title"]
                data_row["series_sequence"] = v[0]["sequence"]
            elif key == "rating":
                overall_distributing = v.get("overall_distribution") or {}
                data_row["rating"] = overall_distributing.get(
                    "display_average_rating", "-")
                data_row["num_ratings"] = overall_distributing.get(
                    "num_ratings", "-")
            elif key == "library_status":
                data_row["date_added"] = v["date_added"]
            elif key == "product_images":
                data_row["cover_url"] = v.get("500", "-")
            elif key == "category_ladders":
                genres = []
                for genre in v:
                    for ladder in genre["ladder"]:
                        genres.append(ladder["name"])
                data_row["genres"] = ", ".join(genres)

        prepared_library.append(data_row)

    prepared_library.sort(key=lambda x: x["asin"])

    return prepared_library


def _export_to_csv(
        file: pathlib.Path,
        data: list,
        headers: Union[list, tuple],
        dialect: str
):
    with file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, dialect=dialect)
        writer.writeheader()

        for i in data:
            writer.writerow(i)


async def _export_library(auth, **params):
    output_format = params.get("format")
    output_filename: pathlib.Path = params.get("output")
    if output_filename.suffix == r".{format}":
        suffix = "." + output_format
        output_filename = output_filename.with_suffix(suffix)

    library = await _get_library(auth, **params)

    prepared_library = _prepare_library_for_export(library)

    headers = (
        "asin", "title", "subtitle", "authors", "narrators", "series_title",
        "series_sequence", "genres", "runtime_length_min", "is_finished",
        "percent_complete", "rating", "num_ratings", "date_added",
        "release_date", "cover_url"
    )

    if output_format in ("tsv", "csv"):
        if output_format == csv:
            dialect = "excel"
        else:
            dialect = "excel-tab"
        _export_to_csv(output_filename, prepared_library, headers, dialect)

    if output_format == "json":
        data = json.dumps(prepared_library, indent=4)
        output_filename.write_text(data)


@cli.command("export")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=pathlib.Path),
    default=pathlib.Path().cwd() / r"library.{format}",
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
    "--format", "-f",
    type=click.Choice(["tsv", "csv", "json"]),
    default="tsv",
    show_default=True,
    help="Output format"
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
def export_library(session, **params):
    """export library"""
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(_export_library(session.auth, **params))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


@cli.command("list")
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
def list_library(session, **params):
    """list titles in library"""
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(_list_library(session.auth, **params))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
