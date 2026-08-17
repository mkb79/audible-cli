"""Asking for a cover size a title does not have."""

import asyncio
import logging

from audible_cli.cmds.cmd_download import download_cover


class Item:
    full_title = "Sternengeschichten Folge 716"

    def get_cover_url(self, res):
        return None


def test_a_missing_size_is_a_warning_not_an_error(tmp_path, caplog):
    # It aborts nothing, so reporting it as an error made a podcast run
    # look like it had gone wrong.
    with caplog.at_level(logging.DEBUG, logger="audible_cli.cmds.cmd_download"):
        asyncio.run(
            download_cover(
                client=None,
                output_dir=tmp_path,
                base_filename="a-title",
                item=Item(),
                res=500,
                overwrite_existing=False,
            )
        )

    said = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert said == [
        ("WARNING", "No COVER with size 500 found for Sternengeschichten Folge 716")
    ]
