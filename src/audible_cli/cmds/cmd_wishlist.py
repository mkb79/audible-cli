import asyncio
import csv
import json
import pathlib
from typing import Union

import audible
import click
from click import echo

from ..config import pass_session
from ..models import Wishlist


async def _get_wishlist(auth, **params):
    timeout = params.get("timeout")
    if timeout == 0:
        timeout = None

    async with audible.AsyncClient(auth, timeout=timeout) as client:
        wishlist = await Wishlist.from_api(
            client,
            response_groups=(
                "contributors, media, price, product_attrs, product_desc, "
                "product_extended_attrs, product_plan_details, product_plans, "
                "rating, sample, sku, series, reviews, review_attrs, ws4v, "
                "customer_rights, categories, category_ladders, claim_code_url"
            )
        )
    return wishlist


async def _list_wishlist(auth, **params):
    wishlist = await _get_wishlist(auth, **params)

    books = []

    for item in wishlist:
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


def _prepare_wishlist_for_export(wishlist: dict):
    keys_with_raw_values = (
        "asin", "title", "subtitle", "runtime_length_min", "is_finished",
        "percent_complete", "release_date"
    )

    prepared_wishlist = []

    for item in wishlist:
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
            elif key == "added_timestamp":
                data_row["date_added"] = v
            elif key == "product_images":
                data_row["cover_url"] = v.get("500", "-")
            elif key == "category_ladders":
                genres = []
                for genre in v:
                    for ladder in genre["ladder"]:
                        genres.append(ladder["name"])
                data_row["genres"] = ", ".join(genres)

        prepared_wishlist.append(data_row)

    prepared_wishlist.sort(key=lambda x: x["asin"])

    return prepared_wishlist


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


async def _export_wishlist(auth, **params):
    output_format = params.get("format")
    output_filename: pathlib.Path = params.get("output")
    if output_filename.suffix == r".{format}":
        suffix = "." + output_format
        output_filename = output_filename.with_suffix(suffix)

    wishlist = await _get_wishlist(auth, **params)

    prepared_wishlist = _prepare_wishlist_for_export(wishlist)

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
        _export_to_csv(output_filename, prepared_wishlist, headers, dialect)

    if output_format == "json":
        data = json.dumps(prepared_wishlist, indent=4)
        output_filename.write_text(data)


@click.group("wishlist")
def cli():
    """interact with wishlist"""


@cli.command("export")
@click.option(
    "--output", "-o",
    type=click.Path(),
    default=pathlib.Path().cwd() / r"wishlist.{format}",
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
@pass_session
def export_library(session, **params):
    """export wishlist"""
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(_export_wishlist(session.auth, **params))
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
@pass_session
def list_library(session, **params):
    """list titles in wishlist"""
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(_list_wishlist(session.auth, **params))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
