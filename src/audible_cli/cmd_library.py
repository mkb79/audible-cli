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

    f = pathlib.Path(params.get("output"))

    headers = ["asin", "title", "subtitle", "authors", "narrators", "series_title", "series_sequence", "genres", "runtime_length_min", "is_finished", "percent_complete", "rating", "num_ratings", "date_added", "release_date", "cover_url"]

    writer = csv.DictWriter(f.open("w"), fieldnames=headers, dialect="excel-tab")
    writer.writeheader()

    keys_to_extract = ("asin", "title", "subtitle", "runtime_length_min", "is_finished", "percent_complete", "release_date") 

    for item in library:
        data_row = {}
        for key in item:
            v = getattr(item, key)
            if v is None:
                pass
            elif key in keys_to_extract:
                data_row[key] = v
            elif key in ("authors", "narrators"):
                data_row[key] = ", ".join([i["name"] for i in v])
            elif key == "series":
                data_row["series_title"] = v[0]["title"]
                data_row["series_sequence"] = v[0]["sequence"]
            elif key == "rating":
                data_row["rating"] = v["overall_distribution"]["display_average_rating"]
                data_row["num_ratings"] = v["overall_distribution"]["num_ratings"]
            elif key == "library_status":
                data_row["date_added"] = v["date_added"]
            elif key == "product_images":
                data_row["cover_url"] = v["500"]
            elif key == "category_ladders":
                genres = []
                for genre in v:
                    for ladder in genre["ladder"]:
                        genres.append(ladder["name"])
                data_row["genres"] = ", ".join(genres)

        writer.writerow(data_row)


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
