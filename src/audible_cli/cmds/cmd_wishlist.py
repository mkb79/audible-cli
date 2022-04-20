import asyncio
import csv
import json
import logging
import pathlib

import click
from click import echo

from ..decorators import run_async, timeout_option, pass_session
from ..models import Wishlist
from ..utils import export_to_csv


logger = logging.getLogger("audible_cli.cmds.cmd_wishlist")


async def _get_wishlist(session):
    async with session.get_client() as client:
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
@timeout_option
@click.option(
    "--format", "-f",
    type=click.Choice(["tsv", "csv", "json"]),
    default="tsv",
    show_default=True,
    help="Output format"
)
@pass_session
@run_async
async def export_wishlist(session, **params):
    """export wishlist"""
    output_format = params.get("format")
    output_filename: pathlib.Path = params.get("output")
    if output_filename.suffix == r".{format}":
        suffix = "." + output_format
        output_filename = output_filename.with_suffix(suffix)

    wishlist = await _get_wishlist(session)

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
        export_to_csv(output_filename, prepared_wishlist, headers, dialect)

    if output_format == "json":
        data = json.dumps(prepared_wishlist, indent=4)
        output_filename.write_text(data)


@cli.command("list")
@timeout_option
@pass_session
@run_async
async def list_wishlist(session, **params):
    """list titles in wishlist"""
    wishlist = await _get_wishlist(session)

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


@cli.command("add")
@click.option(
    "--asin", "-a",
    multiple=True,
    help="asin of the audiobook"
)
@timeout_option
@pass_session
@run_async
async def add_wishlist(session, asin):
    """add asin(s) to wishlist"""

    async def add_asin(asin):
        body = {"asin": asin}
        r = await client.post("wishlist", body=body)
        return r

    async with session.get_client() as client:
        jobs = [add_asin(a) for a in asin]
        await asyncio.gather(*jobs)

    wishlist = await _get_wishlist(session)
    for a in asin:
        if not wishlist.has_asin(a):
            logger.error(f"{a} was not added to wishlist")
        else:
            item = wishlist.get_item_by_asin(a)
            logger.info(f"{a} ({item.full_title}) added to wishlist")

@cli.command("remove")
@click.option(
    "--asin", "-a",
    multiple=True,
    help="asin of the audiobook"
)
@timeout_option
@pass_session
@run_async
async def remove_wishlist(session, asin):
    """remove asin(s) from wishlist"""

    async def remove_asin(rasin):
        r = await client.delete(f"wishlist/{rasin}")
        item = wishlist.get_item_by_asin(rasin)
        logger.info(f"{rasin} ({item.full_title}) removed from wishlist")
        return r

    jobs = []
    wishlist = await _get_wishlist(session)
    for a in asin:
        if not wishlist.has_asin(a):
            logger.error(f"{a} not in wishlist")
        else:
            jobs.append(remove_asin(a))

    async with session.get_client() as client:
        await asyncio.gather(*jobs)
