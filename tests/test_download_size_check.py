"""The size check that runs after a download finished.

Three defects hid each other here. `check_download_size` was never in the
postprocessing list, so nobody noticed that it reported a mismatch whenever
both sizes were known, or that the dispatcher never handed it the head
response it needs. The net effect was that a truncated audiobook was renamed
to its final name and counted as a success.
"""

import asyncio
import datetime

import pytest

from audible_cli.downloader import (
    Downloader,
    File,
    ResponseInfo,
    Status,
    check_download_size,
)


def sync(coro):
    return asyncio.run(coro)


class FakeResponse:
    """Enough of an httpx response for ResponseInfo."""

    def __init__(self, content_length=None, content_type="audio/aax", status_code=200):
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        self.status_code = status_code
        self.url = "https://example.invalid/book.aaxc"
        self.request = None
        self.history = []
        self.elapsed = datetime.timedelta(seconds=1)


def head_for(content_length):
    return ResponseInfo(FakeResponse(content_length=content_length))


def written(tmp_path, size):
    f = tmp_path / "book.aaxc.tmp"
    f.write_bytes(b"x" * size)
    return File(f)


@pytest.mark.parametrize("size", [0, 1, 4096, 10_000_000])
def test_matching_size_passes(tmp_path, size):
    status = sync(
        check_download_size(
            tmp_file=written(tmp_path, size),
            target_file=File(tmp_path / "book.aaxc"),
            head_response=head_for(size),
        )
    )
    assert status is Status.Success


@pytest.mark.parametrize(
    ("written_size", "expected_size"),
    [
        (500, 1000),  # truncated, the case that matters
        (0, 1000),  # nothing arrived at all
        (1000, 500),  # more than announced
    ],
)
def test_differing_size_is_reported(tmp_path, written_size, expected_size):
    status = sync(
        check_download_size(
            tmp_file=written(tmp_path, written_size),
            target_file=File(tmp_path / "book.aaxc"),
            head_response=head_for(expected_size),
        )
    )
    assert status is Status.DownloadSizeMismatch


def test_unknown_content_length_cannot_fail(tmp_path):
    # No Content-Length means nothing to compare against, which must not be
    # turned into a failure.
    status = sync(
        check_download_size(
            tmp_file=written(tmp_path, 1234),
            target_file=File(tmp_path / "book.aaxc"),
            head_response=head_for(None),
        )
    )
    assert status is Status.Success


@pytest.mark.parametrize(
    "encoding",
    [
        "gzip",
        "br",  # brotli
        "zstd",
        "deflate",
        "compress",
        "gzip, br",  # chained
        " BR ",  # the header is case-insensitive and may carry spaces
        "x-gzip",
        "something-nobody-has-heard-of",
    ],
)
def test_an_encoded_transfer_is_not_measured(tmp_path, encoding):
    # Content-Length may describe the encoded bytes while what reaches the
    # file may have been decoded, so the comparison could reject a sound
    # download. Which scheme it is does not matter, so this is an allowlist:
    # everything that is not plain counts as encoded, including schemes that
    # do not exist yet.
    head = ResponseInfo(FakeResponse(content_length=1000))
    head.headers["Content-Encoding"] = encoding
    status = sync(
        check_download_size(
            tmp_file=written(tmp_path, 4000),
            target_file=File(tmp_path / "book.aaxc"),
            head_response=head,
        )
    )
    assert status is Status.Success


@pytest.mark.parametrize("encoding", ["identity", "IDENTITY", " identity "])
def test_an_unencoded_transfer_is_still_measured(tmp_path, encoding):
    # `identity` means the bytes were not touched, so the two lengths do
    # describe the same thing and a mismatch is a real one.
    head = ResponseInfo(FakeResponse(content_length=1000))
    head.headers["Content-Encoding"] = encoding
    status = sync(
        check_download_size(
            tmp_file=written(tmp_path, 4000),
            target_file=File(tmp_path / "book.aaxc"),
            head_response=head,
        )
    )
    assert status is Status.DownloadSizeMismatch


def test_the_encoding_may_sit_on_either_response(tmp_path):
    # The probe and the real request are separate exchanges; either one
    # reporting an encoding makes the comparison meaningless.
    head = ResponseInfo(FakeResponse(content_length=1000))
    actual = ResponseInfo(FakeResponse(content_length=1000))
    actual.headers["Content-Encoding"] = "br"
    status = sync(
        check_download_size(
            tmp_file=written(tmp_path, 4000),
            target_file=File(tmp_path / "book.aaxc"),
            head_response=head,
            response=actual,
        )
    )
    assert status is Status.Success


# --- the wiring, proven through behaviour rather than by reading source ---


def postprocess(tmp_path, written_size, announced_size, content_type="audio/aax"):
    """Run the real _postprocessing over a prepared tmp file."""
    dl = Downloader(
        source="https://example.invalid/book.aaxc",
        client=None,
        expected_types=["audio/aax"],
    )
    head = ResponseInfo(FakeResponse(content_length=announced_size))

    async def fake_head(force_recreate=False):
        return head

    dl.get_head_response = fake_head

    tmp_file = written(tmp_path, written_size)
    target_file = File(tmp_path / "book.aaxc")
    response = ResponseInfo(
        FakeResponse(content_length=announced_size, content_type=content_type)
    )
    return sync(
        dl._postprocessing(
            tmp_file=tmp_file,
            target_file=target_file,
            response=response,
            force_reload=False,
        )
    )


def test_postprocessing_rejects_a_short_download(tmp_path):
    # Fails if the check is not in the list, and also if head_response is
    # not handed to it — that would raise TypeError instead.
    result = postprocess(tmp_path, written_size=500, announced_size=1000)

    assert result.status is Status.DownloadSizeMismatch
    assert not (tmp_path / "book.aaxc").exists(), "must not become the real file"


def test_postprocessing_accepts_a_complete_download(tmp_path):
    result = postprocess(tmp_path, written_size=1000, announced_size=1000)

    assert result.status is Status.Success
    assert (tmp_path / "book.aaxc").exists()


def test_a_parts_message_still_wins_over_the_size(tmp_path):
    # The text response is far shorter than announced. It has to be reported
    # through its own message, so the caller still learns the book must be
    # fetched in parts instead of being told the size was wrong.
    dl = Downloader(
        source="https://example.invalid/book.aaxc",
        client=None,
        expected_types=["audio/aax"],
    )
    head = ResponseInfo(FakeResponse(content_length=99999))

    async def fake_head(force_recreate=False):
        return head

    dl.get_head_response = fake_head

    # Must contain the exact phrase the detection looks for, lowercase.
    message = "Sorry, please download individual parts for this title."
    tmp_file = File(tmp_path / "book.aaxc.tmp")
    tmp_file.path.write_text(message, encoding="utf-8")
    response = ResponseInfo(
        FakeResponse(content_length=len(message), content_type="text/plain")
    )
    result = sync(
        dl._postprocessing(
            tmp_file=tmp_file,
            target_file=File(tmp_path / "book.aaxc"),
            response=response,
            force_reload=False,
        )
    )

    assert result.status is Status.DownloadIndividualParts
    assert not (tmp_path / "book.aaxc").exists()
