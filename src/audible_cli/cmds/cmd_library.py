import asyncio
import json
import pathlib

import click
from click import echo

from ..decorators import (
    bunch_size_option,
    timeout_option,
    pass_client,
    pass_session,
    wrap_async
)
from ..models import Library
from ..utils import export_to_csv


@click.group("library")
def cli():
    """interact with library"""


async def _get_library(session, client):
    bunch_size = session.params.get("bunch_size")

    return await Library.from_api_full_sync(
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

async def _get_sorted_library(session, client):
    library = await _get_library(session, client)
    return await _sort_library(library)


async def _sort_library(library):
    prepared_library = await asyncio.gather(
        *[_prepare_item(i) for i in library]
    )
    prepared_library.sort(key=lambda x: x["asin"])
    return prepared_library


@wrap_async
def _prepare_item(item):
    data_row = {}
    data_row["asin"] = item["asin"]
    data_row["title"] = item["title"]
    data_row["subtitle"] = item["subtitle"]
    data_row["runtime_length_min"] = item["runtime_length_min"]
    data_row["is_finished"] = item["is_finished"]
    data_row["percent_complete"] = item["percent_complete"]
    data_row["release_date"] = item["release_date"]
    data_row["authors"] = ", ".join(i["name"] for i in item["authors"])
    data_row["narrators"] = ", ".join(i["name"] for i in item["narrators"])
    if item["series"] is not None:
        data_row["series_title"] = item["series"][0]["title"]
        data_row["series_sequence"] = item["series"][0]["sequence"]
    ratings = item["rating"] or {}
    data_row["rating"] = ratings.get("display_average_rating", "-")
    data_row["num_ratings"] = ratings.get("num_ratings", "-")
    data_row["date_added"] = item["library_status"]["date_added"]
    data_row["cover_url"] = item["product_images"].get("500", "-")
    genres = []
    for genre in item["category_ladders"]:
            for ladder in genre["ladder"]:
                genres.append(ladder["name"])
    data_row["genres"] = ", ".join(genres)
    data_row["description"] = item["extended_product_description"]
    return data_row


@cli.command("export")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=pathlib.Path),
    default=pathlib.Path().cwd() / r"library.{format}",
    show_default=True,
    help="output file"
)
@timeout_option
@click.option(
    "--format", "-f",
    type=click.Choice(["tsv", "csv", "json"]),
    default="tsv",
    show_default=True,
    help="Output format"
)
@bunch_size_option
@click.option(
    "--resolve-podcasts",
    is_flag=True,
    help="Resolve podcasts to show all episodes"
)
@pass_session
@pass_client
async def export_library(session, client, **params):
    """export library"""

    output_format = params.get("format")
    output_filename: pathlib.Path = params.get("output")
    if output_filename.suffix == r".{format}":
        suffix = "." + output_format
        output_filename = output_filename.with_suffix(suffix)

    library = await _get_sorted_library(session, client)

    if output_format in ("tsv", "csv"):
        dialect = "csv" if output_format == "csv" else "excel-tab"
        headers = (
            "asin", "title", "subtitle", "authors", "narrators", "series_title",
            "series_sequence", "genres", "runtime_length_min", "is_finished",
            "percent_complete", "rating", "num_ratings", "date_added",
            "release_date", "cover_url", "description"
        )
        export_to_csv(output_filename, library, headers, dialect)

    elif output_format == "json":
        data = json.dumps(library, indent=4)
        output_filename.write_text(data)


@cli.command("list")
@timeout_option
@bunch_size_option
@click.option(
    "--resolve-podcasts",
    is_flag=True,
    help="Resolve podcasts to show all episodes"
)
@pass_session
@pass_client
async def list_library(session, client, resolve_podcasts=False):
    """list titles in library"""

    @wrap_async
    def _prepare_item(item):
        fields = [item.asin]

        authors = ", ".join(
            sorted(a["name"] for a in item.authors) if item.authors else ""
        )
        if authors:
            fields.append(authors)

        series = ", ".join(
            sorted(s["title"] for s in item.series) if item.series else ""
        )
        if series:
            fields.append(series)

        fields.append(item.title)
        return ": ".join(fields)

    library = await _get_library(session, client)

    if resolve_podcasts:
        await library.resolve_podcats()

    books = await asyncio.gather(
        *[_prepare_item(i) for i in library]
    )

    for i in sorted(books):
        echo(i)
