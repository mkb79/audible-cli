"""Argument checks on the item methods that download the audio.

These were bare ``assert`` statements until the S101 cleanup, so ``python -O``
dropped them and an unsupported value reached the API request instead of being
refused. The tests pin the replacements down.
"""

import asyncio

import pytest
from helpers import FakeClient, library_item

from audible_cli.cmds.cmd_download import cli as download_cli
from audible_cli.models import CHAPTER_TYPES, QUALITIES, LibraryItem


def sync(coro):
    return asyncio.run(coro)


def item():
    return LibraryItem(library_item("B00TEST01"), api_client=FakeClient())


BAD_QUALITIES = ["", "Best", "HIGH", "low", "aax", None]


@pytest.mark.parametrize("quality", BAD_QUALITIES)
def test_get_codec_refuses_unknown_quality(quality):
    with pytest.raises(ValueError, match="quality"):
        item()._get_codec(quality)


@pytest.mark.parametrize("quality", BAD_QUALITIES)
def test_get_license_refuses_unknown_quality(quality):
    with pytest.raises(ValueError, match="quality"):
        sync(item().get_license(quality))


@pytest.mark.parametrize("quality", BAD_QUALITIES)
def test_get_content_metadata_refuses_unknown_quality(quality):
    with pytest.raises(ValueError, match="quality"):
        sync(item().get_content_metadata(quality))


@pytest.mark.parametrize("chapter_type", ["", "Nested", "flat tree", "Tree "])
def test_get_content_metadata_refuses_unknown_chapter_type(chapter_type):
    with pytest.raises(ValueError, match="chapter_type"):
        sync(item().get_content_metadata("best", chapter_type=chapter_type))


@pytest.mark.parametrize(
    ("chapter_type", "expected"),
    [("flat", "Flat"), ("TREE", "Tree"), ("tReE", "Tree"), ("Flat", "Flat")],
)
def test_get_content_metadata_keeps_accepting_any_casing(chapter_type, expected):
    # capitalize() runs before the check, which is what makes the click option
    # case_sensitive=False work. The check must not undo that, and the request
    # must still carry the normalized value.
    client = FakeClient()
    item = LibraryItem(library_item("B00TEST01"), api_client=client)
    sync(item.get_content_metadata("best", chapter_type=chapter_type))
    assert client.last_params["params"]["chapter_titles_type"] == expected


@pytest.mark.parametrize("quality", ["best", "high", "normal"])
def test_get_codec_accepts_every_supported_quality(quality):
    # Spelled out rather than parametrized over QUALITIES, so that dropping a
    # value from the constant fails a test instead of silently shrinking it.
    # No codecs on the item, so this returns early instead of reaching the API;
    # what matters is that the check lets the value through.
    assert item()._get_codec(quality) == (None, None)


def choices_of(option_name):
    param = next(p for p in download_cli.params if p.name == option_name)
    return param.type.choices


def test_the_supported_qualities_are_the_ones_the_cli_offers():
    # Guards against the two drifting apart. If --quality gains a value that
    # the item methods then reject, the download command breaks on an option
    # its own help advertises.
    assert set(QUALITIES) == set(choices_of("quality"))


def test_the_supported_chapter_types_are_the_ones_the_cli_offers():
    # "config" is resolved against the profile before any item method sees it,
    # so it is the one choice that must not reach CHAPTER_TYPES.
    assert set(CHAPTER_TYPES) == set(choices_of("chapter_type")) - {"config"}
