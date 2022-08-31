import asyncio
import json
import pathlib
from datetime import datetime

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


datetime_type = click.DateTime([
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%fZ"
])


@click.group("library")
def cli():
    """interact with library"""


async def _get_library(session, client, purchased_after: str):
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
        bunch_size=bunch_size,
        purchased_after=purchased_after
    )


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
@click.option(
    "--start-date",
    type=datetime_type,
    default="1970-1-1",
    help="Only considers books added to library on or after this UTC date."
)
@click.option(
    "--end-date",
    type=datetime_type,
    default=datetime.utcnow(),
    help="Only considers books added to library on or before this UTC date."
)
@pass_session
@pass_client
async def export_library(session, client, **params):
    """export library"""

    @wrap_async
    def _prepare_item(item):
        purchase_date = datetime_type.convert(
            item.purchase_date, None, None
        )
        if not start_date <= purchase_date <= end_date:
            return None

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

        return data_row

    output_format = params.get("format")
    output_filename: pathlib.Path = params.get("output")
    if output_filename.suffix == r".{format}":
        suffix = "." + output_format
        output_filename = output_filename.with_suffix(suffix)

    start_date = params.get("start_date")
    purchased_after = start_date.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    end_date = params.get("end_date")

    library = await _get_library(session, client, purchased_after)
    if params.get("resolve_podcasts"):
        await library.resolve_podcats()

    keys_with_raw_values = (
        "asin", "title", "subtitle", "extended_product_description", "runtime_length_min", "is_finished",
        "percent_complete", "release_date", "purchase_date"
    )

    prepared_library = await asyncio.gather(
        *[_prepare_item(i) for i in library]
    )
    prepared_library = [i for i in prepared_library if i is not None]
    prepared_library.sort(key=lambda x: x["asin"])

    if output_format in ("tsv", "csv"):
        if output_format == "csv":
            dialect = "excel"
        else:
            dialect = "excel-tab"

        headers = (
            "asin", "title", "subtitle", "extended_product_description", "authors", "narrators", "series_title",
            "series_sequence", "genres", "runtime_length_min", "is_finished",
            "percent_complete", "rating", "num_ratings", "date_added",
            "release_date", "cover_url", "purchase_date"
        )

        export_to_csv(output_filename, prepared_library, headers, dialect)

    elif output_format == "json":
        data = json.dumps(prepared_library, indent=4)
        output_filename.write_text(data)


@cli.command("list")
@timeout_option
@bunch_size_option
@click.option(
    "--resolve-podcasts",
    is_flag=True,
    help="Resolve podcasts to show all episodes"
)
@click.option(
    "--start-date",
    type=datetime_type,
    default="1970-1-1",
    help="Only considers books added to library on or after this UTC date."
)
@click.option(
    "--end-date",
    type=datetime_type,
    default=datetime.utcnow(),
    help="Only considers books added to library on or before this UTC date."
)
@pass_session
@pass_client
async def list_library(session, client, resolve_podcasts, start_date, end_date):
    """list titles in library"""

    @wrap_async
    def _prepare_item(item):
        purchase_date = datetime_type.convert(
            item.purchase_date, None, None
        )
        if not start_date <= purchase_date <= end_date:
            return ""

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

    purchased_after = start_date.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    library = await _get_library(session, client, purchased_after)

    if resolve_podcasts:
        await library.resolve_podcats()

    books = await asyncio.gather(
        *[_prepare_item(i) for i in library]
    )
    [echo(i) for i in sorted(books) if len(i) > 0]
