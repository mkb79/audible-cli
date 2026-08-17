"""Finding a download that is already on disk, before asking for a license.

A license costs a voucher whether or not the file turns out to be there, so
the download command guesses the name up front and skips the request when it
finds both the file and its voucher.

That guess only ever worked for AAXC. A podcast episode has no `aax_*`
codec, so `_get_codec` returns nothing, and its real name is not knowable
until the license says MPEG. Every run therefore spent a voucher on an
episode that had been downloaded long ago.
"""

import ast
import inspect
import pathlib

import pytest

from audible_cli.cmds import cmd_download
from audible_cli.cmds.cmd_download import _finished_downloads


class Item:
    """An item whose codec is whatever the test says it is."""

    def __init__(self, codec):
        self._codec = codec

    def _get_codec(self, quality):
        return self._codec, "enhanced"


def found(folder, item):
    return [p.name for p in _finished_downloads(folder, "a-title", item, "best")]


def test_an_audiobook_is_looked_for_by_the_codec_the_library_knows(tmp_path):
    # Nothing has to exist for this one: the name follows from the codec, so
    # the guess is exact and needs no directory listing.
    assert found(tmp_path, Item("AAX_44_128")) == ["a-title-AAX_44_128.aaxc"]


def test_a_podcast_episode_is_looked_for_as_the_mp3_it_was_saved_as(tmp_path):
    (tmp_path / "a-title-MPEG.mp3").touch()

    assert found(tmp_path, Item(None)) == ["a-title-MPEG.mp3"]


def test_an_episode_that_is_not_there_yet_turns_up_nothing(tmp_path):
    # Unlike the audiobook above, which is named rather than looked for
    assert found(tmp_path, Item(None)) == []


def test_a_voucher_left_by_a_failed_attempt_counts_as_a_find(tmp_path):
    # The voucher is written before the audio is renamed into place, so a
    # download that failed in between leaves one behind. Finding it lets
    # the retry reuse that license instead of buying another.
    (tmp_path / "a-title-MPEG.voucher").touch()

    assert found(tmp_path, Item(None)) == ["a-title-MPEG.mp3"]


def test_a_voucher_from_a_different_format_is_left_alone(tmp_path):
    # Vouchers are looked for as well as audio, and one from an AAXC
    # download names its own format. Taking it would offer an `.mp3` that
    # was never written and hand its license to the wrong kind of file.
    (tmp_path / "a-title-AAX_44_128.voucher").touch()

    assert found(tmp_path, Item(None)) == []


def test_a_title_is_offered_once_even_with_both_halves_on_disk(tmp_path):
    (tmp_path / "a-title-MPEG.mp3").touch()
    (tmp_path / "a-title-MPEG.voucher").touch()

    assert found(tmp_path, Item(None)) == ["a-title-MPEG.mp3"]


def test_the_casing_of_the_format_does_not_matter(tmp_path):
    # The name carries the codec as the license spelled it, and only MPEG
    # ever produces an `.mp3` here, so matching loosely cannot pick up a
    # different quality of the same title.
    (tmp_path / "a-title-mpeg.mp3").touch()

    assert found(tmp_path, Item(None)) == ["a-title-mpeg.mp3"]


def test_a_longer_title_starting_the_same_way_is_a_different_title(tmp_path):
    # Titles carry hyphens, so `a-title`'s wildcard also lists the files of
    # `a-title-longer`. Claiming one would skip a download that never
    # happened. (`a-title` against `a-title2` needs no filter: the wildcard
    # already demands the hyphen.)
    (tmp_path / "a-title-longer-MPEG.mp3").touch()
    (tmp_path / "a-title-MPEG.mp3").touch()

    assert found(tmp_path, Item(None)) == ["a-title-MPEG.mp3"]


def test_a_title_with_brackets_is_not_read_as_a_pattern(tmp_path):
    # Titles like "Star-Lord (Deutsch) [1]" are ordinary. Left unescaped,
    # the brackets would become a character class and match nothing.
    name = "Marvels Wastelanders [Star-Lord]"
    (tmp_path / f"{name}-MPEG.mp3").touch()

    got = [p.name for p in _finished_downloads(tmp_path, name, Item(None), "best")]

    assert got == [f"{name}-MPEG.mp3"]


@pytest.mark.parametrize("codec", ["AAX_44_128", None])
def test_it_never_looks_outside_the_output_directory(tmp_path, codec):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a-title-MPEG.mp3").touch()

    for candidate in _finished_downloads(tmp_path, "a-title", Item(codec), "best"):
        assert candidate.parent == pathlib.Path(tmp_path)


def test_the_download_command_actually_asks_where_to_look():
    """`download_aaxc` has to use the generator, not build a name itself.

    Read from the syntax tree: reaching that code for real needs a license
    and the network, and a hand-built name looks identical until a podcast
    episode is downloaded twice.
    """
    tree = ast.parse(inspect.getsource(cmd_download))
    body = next(
        ast.unparse(n)
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "download_aaxc"
    )

    assert "_finished_downloads(" in body
    assert ".aaxc'" not in body.split("_finished_downloads")[0], (
        "the pre-check must not build a name of its own any more"
    )
