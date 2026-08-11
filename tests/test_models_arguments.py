"""Argument checks on the item methods that download the audio.

These were bare ``assert`` statements until the S101 cleanup, so ``python -O``
dropped them and an unsupported value reached the API request instead of being
refused. The tests pin the replacements down.
"""

import asyncio

import pytest
from helpers import FakeClient, library_item

from audible_cli.constants import CLI_CHAPTER_TYPE_CONFIG, QUALITIES
from audible_cli.models import LibraryItem, api_quality


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


@pytest.mark.parametrize(
    ("quality", "sent"),
    [
        # Spelled out rather than taken from the constants, so that changing a
        # wire value has to be a deliberate edit here too
        ("normal", "Normal"),
        ("high", "High"),
        # "best" never crosses the wire under that name
        ("best", "High"),
    ],
)
def test_quality_is_translated_for_the_api(quality, sent):
    assert api_quality(quality) == sent

    client = FakeClient()
    item = LibraryItem(library_item("B00TEST01"), api_client=client)
    sync(item.get_content_metadata(quality))
    assert client.last_params["params"]["quality"] == sent


def test_every_supported_quality_is_sent_as_high_or_normal():
    # The set the API is given is smaller than the set the CLI offers, which
    # is the whole reason api_quality exists
    assert set(map(api_quality, QUALITIES)) == {"High", "Normal"}


@pytest.mark.parametrize("quality", BAD_QUALITIES)
def test_api_quality_refuses_what_it_cannot_translate(quality):
    # It is reachable from a plugin without going through a method that
    # validates first, so it has to refuse rather than fall back to a quality
    # nobody asked for
    with pytest.raises(ValueError, match="quality"):
        api_quality(quality)


def test_config_never_reaches_a_metadata_request():
    # --chapter-type accepts it, but the download command resolves it against
    # the profile first. Reaching an item method means that resolution was
    # skipped, so it has to be refused rather than sent as a chapter style.
    with pytest.raises(ValueError, match="chapter_type"):
        sync(item().get_content_metadata("best", chapter_type=CLI_CHAPTER_TYPE_CONFIG))
