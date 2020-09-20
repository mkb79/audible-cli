import asyncio
import csv
import pathlib

import click

from .models import Library
from .utils import pass_config


@click.group()
def cli():
    """interact with library"""


async def _export_library(auth, **params):
    library = await Library.get_from_api(
        auth,
        response_groups=("contributors, media, price, product_attrs, "
                         "product_desc, product_extended_attrs, "
                         "product_plan_details, product_plans, rating, sample, "
                         "sku, series, reviews, ws4v, origin, relationships, "
                         "review_attrs, categories, badge_types, "
                         "category_ladders, claim_code_url, is_downloaded, "
                         "is_finished, is_returnable, origin_asin, pdf_url, "
                         "percent_complete, provided_review"),
        num_results=1000
    )

    infos = ("asin", "title", "subtitle", "authors", "narrators", "series", "runtime_length_min", "is_finished", "percent_complete", "rating")

    f = pathlib.Path(params.get("output"))
    fnames = ["asin", "title", "subtitle", "authors", "narrators", "series_title", "series_sequence", "runtime_length_min", "is_finished", "percent_complete", "rating", "num_ratings"]
    writer = csv.DictWriter(f.open("w"), fieldnames=fnames, dialect="excel-tab")
    writer.writeheader()

    for i in library:
        item_data = {}
        for x in infos:
            v = getattr(i, x)
            if x in ("authors", "narrators"):
                item_data[x] = ", ".join([y["name"] for y in v])
            elif x == "series":
                item_data["series_title"] = v[0]["title"] if v else ""
                item_data["series_sequence"] = v[0]["sequence"] if v else ""
            elif x == "rating":
                item_data["rating"] = v["overall_distribution"]["display_average_rating"]
                item_data["num_ratings"] = v["overall_distribution"]["num_ratings"]
            else:
                item_data[x] = v if v is not None else ""
        writer.writerow(item_data)


@cli.command("export")
@click.option(
    "--output", "-o",
    type=click.Path(),
    default=pathlib.Path().cwd() / "library.csv",
    show_default=True,
    help="output file"
)
@pass_config
def export_library(config, **params):
    """export library"""
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(_export_library(config.auth, **params))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
