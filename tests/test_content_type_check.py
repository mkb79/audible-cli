"""What a finished download is allowed to have arrived as.

The check guards against a response that is not the audio that was asked
for — an error page, or the note that a title has to be fetched in parts.
It reads the header only, so it cannot vouch for the body.

The two download paths keep separate lists on purpose: an AAX download
always writes a `.aax` file, while an AAXC download names the file after
its codec and writes `.mp3` for MPEG. Accepting an MP3 payload on the AAX
path would store it under the wrong extension.
"""

import ast
import asyncio
import inspect

import pytest

from audible_cli.cmds import cmd_download
from audible_cli.constants import AAX_CONTENT_TYPES, AAXC_CONTENT_TYPES
from audible_cli.downloader import (
    File,
    ResponseInfo,
    Status,
    check_content_type,
    check_status_for_message,
)


class Response:
    def __init__(self, content_type):
        self.headers = {} if content_type is None else {"Content-Type": content_type}
        self.status_code = 200


def check(tmp_path, content_type, expected):
    tmp_file = File(tmp_path / "book.tmp")
    tmp_file.path.write_bytes(b"audio")
    return asyncio.run(
        check_content_type(
            response=ResponseInfo(Response(content_type)),
            target_file=File(tmp_path / "book"),
            tmp_file=tmp_file,
            expected_types=list(expected),
        )
    )


# Spelled out rather than derived from the constants: a list that generates
# its own expectations shrinks along with the thing it is meant to guard.
AAX_ACCEPTS = ["audio/aax", "audio/vnd.audible.aax", "audio/audible", "audio/mp4"]
AAXC_ALSO_ACCEPTS = ["audio/mpeg", "audio/mp3", "audio/x-m4a"]


def test_the_two_paths_expect_what_they_are_documented_to_expect():
    assert list(AAX_CONTENT_TYPES) == AAX_ACCEPTS
    assert list(AAXC_CONTENT_TYPES) == AAX_ACCEPTS + AAXC_ALSO_ACCEPTS


@pytest.mark.parametrize("content_type", AAX_ACCEPTS)
def test_both_paths_take_the_aax_family(tmp_path, content_type):
    assert check(tmp_path, content_type, AAX_CONTENT_TYPES) is Status.Success
    assert check(tmp_path, content_type, AAXC_CONTENT_TYPES) is Status.Success


@pytest.mark.parametrize("content_type", AAXC_ALSO_ACCEPTS)
def test_only_the_aaxc_path_takes_mp3_and_m4a(tmp_path, content_type):
    # The AAXC path names the file after the codec and writes `.mp3` for
    # MPEG; the AAX path would file the same bytes as `.aax`.
    assert check(tmp_path, content_type, AAXC_CONTENT_TYPES) is Status.Success
    assert (
        check(tmp_path, content_type, AAX_CONTENT_TYPES)
        is Status.DownloadContentTypeMismatch
    )


def test_the_unregistered_mp3_type_is_accepted(tmp_path):
    # Audible sends `audio/mp3` for podcast episodes, not only the
    # registered `audio/mpeg`, and seven downloads were rejected over it.
    assert check(tmp_path, "audio/mp3", AAXC_CONTENT_TYPES) is Status.Success


@pytest.mark.parametrize(
    "header",
    [
        "AUDIO/MP3",  # casing is the server's choice
        "audio/mpeg; charset=binary",  # so are parameters
        "  audio/mp4  ",
        "Audio/X-M4A ;q=1",
    ],
)
def test_the_header_is_compared_by_its_media_type(tmp_path, header):
    assert check(tmp_path, header, AAXC_CONTENT_TYPES) is Status.Success


@pytest.mark.parametrize(
    "content_type", ["text/html", "application/json", "text/plain"]
)
def test_something_that_is_not_audio_is_rejected(tmp_path, content_type):
    assert (
        check(tmp_path, content_type, AAXC_CONTENT_TYPES)
        is Status.DownloadContentTypeMismatch
    )


@pytest.mark.parametrize("header", [None, "", "   ", "; charset=binary"])
def test_a_header_that_names_nothing_is_rejected(tmp_path, header):
    # Normalising both sides must not make an empty header match an empty
    # entry somebody left in the list.
    assert (
        check(tmp_path, header, [*AAXC_CONTENT_TYPES, ""])
        is Status.DownloadContentTypeMismatch
    )


def test_no_expectation_accepts_anything(tmp_path):
    # Callers that pass no list are asking for no check at all
    assert check(tmp_path, "text/html", []) is Status.Success


@pytest.mark.parametrize(
    "header", ["text/plain", "Text/Plain", "TEXT/PLAIN; charset=utf-8"]
)
def test_the_parts_message_is_found_whatever_the_header_looks_like(tmp_path, header):
    # A title that has to be fetched in parts answers with a short text. The
    # message check runs before the content type one and reads the body only
    # for a text response, so a capitalised header sent it to the wrong
    # branch and the caller was told the type was wrong instead.
    tmp_file = File(tmp_path / "book.tmp")
    tmp_file.path.write_text(
        "Sorry, please download individual parts for this title.", encoding="utf-8"
    )
    status = asyncio.run(
        check_status_for_message(
            response=ResponseInfo(Response(header)), tmp_file=tmp_file
        )
    )

    assert status is Status.DownloadIndividualParts


def test_each_download_path_keeps_to_its_own_list():
    """The AAX path must not start accepting what the AAXC path accepts.

    An AAX download always writes `.aax` because the codec is part of the
    request: `_get_codec` only ever returns an `aax_*` codec and the URL
    call raises `NotDownloadableAsAAX` otherwise. The AAXC path takes its
    format from the license instead and names the file after it, which is
    why it alone accepts MP3 and M4A. Pointing the AAX call at the wider
    list would file an MP3 under a `.aax` name.

    Read from the syntax tree rather than the running functions: reaching
    the `NewDownloader` call needs a license, a URL and a written voucher,
    and stubbing all of that would test the stubs.
    """
    tree = ast.parse(inspect.getsource(cmd_download))
    used = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            for keyword in call.keywords:
                if keyword.arg == "expected_types":
                    used[node.name] = ast.unparse(keyword.value)

    assert used == {
        "download_aax": "list(AAX_CONTENT_TYPES)",
        "download_aaxc": "list(AAXC_CONTENT_TYPES)",
    }, used
