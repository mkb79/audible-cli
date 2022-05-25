import asyncio
import json
import logging
import pathlib

import click
import httpx
import questionary
from click import echo

from ..decorators import timeout_option, pass_client, wrap_async
from ..models import Catalog, Wishlist
from ..utils import export_to_csv


logger = logging.getLogger("audible_cli.cmds.cmd_wishlist")

# audible api raises a 500 status error when to many requests
# where made to wishlist endpoint in short time
limits = httpx.Limits(max_keepalive_connections=1, max_connections=1)


async def _get_wishlist(client):
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
@pass_client
async def export_wishlist(client, **params):
    """export wishlist"""

    @wrap_async
    def _prepare_item(item):
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
        return data_row

    output_format = params.get("format")
    output_filename: pathlib.Path = params.get("output")
    if output_filename.suffix == r".{format}":
        suffix = "." + output_format
        output_filename = output_filename.with_suffix(suffix)

    wishlist = await _get_wishlist(client)

    keys_with_raw_values = (
        "asin", "title", "subtitle", "runtime_length_min", "is_finished",
        "percent_complete", "release_date"
    )

    prepared_wishlist = await asyncio.gather(
        *[_prepare_item(i) for i in wishlist]
    )
    prepared_wishlist.sort(key=lambda x: x["asin"])

    if output_format in ("tsv", "csv"):
        if output_format == "csv":
            dialect = "excel"
        else:
            dialect = "excel-tab"

        headers = (
            "asin", "title", "subtitle", "authors", "narrators", "series_title",
            "series_sequence", "genres", "runtime_length_min", "is_finished",
            "percent_complete", "rating", "num_ratings", "date_added",
            "release_date", "cover_url"
        )

        export_to_csv(
            output_filename, prepared_wishlist, headers, dialect
        )

    elif output_format == "json":
        data = json.dumps(prepared_wishlist, indent=4)
        output_filename.write_text(data)


@cli.command("list")
@timeout_option
@pass_client
async def list_wishlist(client):
    """list titles in wishlist"""

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

    wishlist = await _get_wishlist(client)

    books = await asyncio.gather(
        *[_prepare_item(i) for i in wishlist]
    )

    for i in sorted(books):
        echo(i)


@cli.command("add")
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
@timeout_option
@pass_client(limits=limits)
async def add_wishlist(client, asin, title):
    """add asin(s) to wishlist

    Run the command without any option for interactive mode.
    """

    async def add_asin(asin):
        body = {"asin": asin}
        r = await client.post("wishlist", body=body)
        return r

    asin = list(asin)
    title = list(title)

    if not asin and not title:
        q = await questionary.select(
            "Do you want to add an item by asin or title?",
            choices=[
                questionary.Choice(title="by title", value="title"),
                questionary.Choice(title="by asin", value="asin")
            ]
        ).unsafe_ask_async()

        if q == 'asin':
            q = await questionary.text("Please enter the asin").unsafe_ask_async()
            asin.append(q)
        else:
            q = await questionary.text("Please enter the title").unsafe_ask_async()
            title.append(q)

    for t in title:
        catalog = await Catalog.from_api(
            client,
            title=t,
            num_results=50
        )

        match = catalog.search_item_by_title(t)
        full_match = [i for i in match if i[1] == 100]

        if match:
            choices = []
            for i in full_match or match:
                c = questionary.Choice(title=i[0].full_title, value=i[0].asin)
                choices.append(c)

            answer = await questionary.checkbox(
                f"Found the following matches for '{t}'. Which you want to add?",
                choices=choices
            ).unsafe_ask_async()

            if answer is not None:
                [asin.append(i) for i in answer]
        else:
            logger.error(
                f"Skip title {t}: Not found in library"
            )

    jobs = [add_asin(a) for a in asin]
    await asyncio.gather(*jobs)

    wishlist = await _get_wishlist(client)
    for a in asin:
        if wishlist.has_asin(a):
            item = wishlist.get_item_by_asin(a)
            logger.info(f"{a} ({item.full_title}) added to wishlist")
        else:
            logger.error(f"{a} was not added to wishlist")


@cli.command("remove")
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
@timeout_option
@pass_client(limits=limits)
async def remove_wishlist(client, asin, title):
    """remove asin(s) from wishlist

    Run the command without any option for interactive mode.
    """

    async def remove_asin(rasin):
        r = await client.delete(f"wishlist/{rasin}")
        item = wishlist.get_item_by_asin(rasin)
        logger.info(f"{rasin} ({item.full_title}) removed from wishlist")
        return r

    asin = list(asin)
    wishlist = await _get_wishlist(client)

    if not asin and not title:
        # interactive mode
        choices = []
        for i in wishlist:
            c = questionary.Choice(title=i.full_title, value=i.asin)
            choices.append(c)

        asin = await questionary.checkbox(
            "Select item(s) which you want to remove from whishlist",
            choices=choices
        ).unsafe_ask_async()

    for t in title:
        match = wishlist.search_item_by_title(t)
        full_match = [i for i in match if i[1] == 100]

        if match:
            choices = []
            for i in full_match or match:
                c = questionary.Choice(title=i[0].full_title, value=i[0].asin)
                choices.append(c)

            answer = await questionary.checkbox(
                f"Found the following matches for '{t}'. Which you want to remove?",
                choices=choices
            ).unsafe_ask_async()

            if answer is not None:
                [asin.append(i) for i in answer]
        else:
            logger.error(
                f"Skip title {t}: Not found in library"
            )

    if asin:
        jobs = []
        for a in asin:
            if wishlist.has_asin(a):
                jobs.append(remove_asin(a))
            else:
                logger.error(f"{a} not in wishlist")

        await asyncio.gather(*jobs)
