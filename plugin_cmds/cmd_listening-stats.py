import asyncio
import json
import logging
import pathlib
from datetime import datetime

import click

from audible_cli.decorators import pass_client


logger = logging.getLogger("audible_cli.cmds.cmd_listening-stats")

current_year = datetime.now().year


def ms_to_hms(milliseconds):
    seconds = int((milliseconds / 1000) % 60)
    minutes = int((milliseconds / (1000 * 60)) % 60)
    hours = int((milliseconds / (1000 * 60 * 60)) % 24)
    return {"hours": hours, "minutes": minutes, "seconds": seconds}


async def _get_stats_year(client, year):
    stats_year = {}
    stats = await client.get(
        "stats/aggregates",
        monthly_listening_interval_duration="12",
        monthly_listening_interval_start_date=f"{year}-01",
        store="Audible",
    )
    # iterate over each month
    for stat in stats["aggregated_monthly_listening_stats"]:
        stats_year[stat["interval_identifier"]] = ms_to_hms(stat["aggregated_sum"])
    return stats_year


@click.command("listening-stats")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=pathlib.Path),
    default=pathlib.Path().cwd() / "listening-stats.json",
    show_default=True,
    help="output file",
)
@click.option(
    "--signup-year",
    "-s",
    type=click.IntRange(1997, current_year),
    default="2010",
    show_default=True,
    help="start year for collecting listening stats",
)
@pass_client
async def cli(client, output, signup_year):
    """Get and analyse listening statistics."""
    year_range = list(range(signup_year, current_year + 1))

    r = await asyncio.gather(*[_get_stats_year(client, y) for y in year_range])

    aggregated_stats = {}
    for i in r:
        for k, v in i.items():
            aggregated_stats[k] = v

    aggregated_stats = json.dumps(aggregated_stats, indent=4)
    output.write_text(aggregated_stats)
