"""The status check on a voucher file read back from disk.

This was a bare ``assert`` until the S101 cleanup. Under ``python -O`` it was
dropped, and a denied licence went on to be read for a download url instead of
stopping the job.
"""

import asyncio
import json

import pytest

from audible_cli.cmds.cmd_download import _reuse_voucher
from audible_cli.exceptions import LicenseDenied


def sync(coro):
    return asyncio.run(coro)


def voucher(tmp_path, status_code, **license_fields):
    lr_file = tmp_path / "voucher.json"
    content_license = {"status_code": status_code, **license_fields}
    lr_file.write_text(json.dumps({"content_license": content_license}))
    return lr_file


GRANTED_CONTENT = {
    "content_metadata": {
        "content_url": {"offline_url": "https://example.invalid/book.aaxc"},
        "content_reference": {"content_format": "AAX_44_128"},
    }
}


class ItemWithoutClient:
    """Enough of a ``LibraryItem`` for the code past the status check."""

    _client = None


@pytest.mark.parametrize("status_code", ["Denied", "Rejected", "granted", ""])
def test_refuses_a_voucher_that_is_not_granted(tmp_path, status_code):
    lr_file = voucher(tmp_path, status_code)
    with pytest.raises(LicenseDenied) as excinfo:
        sync(_reuse_voucher(lr_file, ItemWithoutClient()))

    # The message has to name both the file and what it found, because the
    # user has to decide whether to delete the voucher and request a new one
    assert str(lr_file) in str(excinfo.value)
    assert status_code in str(excinfo.value)


def test_a_granted_voucher_is_read_back(tmp_path):
    lr_file = voucher(tmp_path, "Granted", **GRANTED_CONTENT)

    lr, url, codec = sync(_reuse_voucher(lr_file, ItemWithoutClient()))

    assert str(url) == "https://example.invalid/book.aaxc"
    assert codec == "AAX_44_128"
    assert lr["content_license"]["status_code"] == "Granted"
