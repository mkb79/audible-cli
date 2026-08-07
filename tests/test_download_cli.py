"""The download command must not report success after a failed job.

Covers the wiring for #256: `drain_queue()` collecting failures is only half
of it, the command also has to raise on them. Without this test, deleting the
`run.raise_for_errors()` call would leave every other test green.
"""

import pytest
from click.testing import CliRunner

from audible_cli.cmds import cmd_download
from audible_cli.config import Session
from audible_cli.exceptions import AudibleCliException
from audible_cli.models import Library


class FakeHTTPSession:
    """Stands in for the httpx session `pass_client` opens."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeApiClient:
    def __init__(self):
        self.session = FakeHTTPSession()


@pytest.fixture
def session(monkeypatch):
    """A session handing out a client that never reaches the network."""
    monkeypatch.setattr(Session, "get_client", lambda self, **kw: FakeApiClient())
    return Session()


@pytest.fixture
def one_item_library(monkeypatch):
    """Work on a single known item instead of fetching a real library."""

    async def fake_from_api_full_sync(cls, api_client, **request_params):
        return Library(
            {
                "items": [
                    {
                        "asin": "ASIN0001",
                        "title": "A Title",
                        "content_delivery_type": "SinglePartBook",
                        "has_children": False,
                        "purchase_date": "2020-01-01T00:00:00.000Z",
                    }
                ]
            },
            api_client=api_client,
        )

    monkeypatch.setattr(
        Library, "from_api_full_sync", classmethod(fake_from_api_full_sync)
    )


def download(session, tmp_path, *extra):
    # --chapter-type and --filename-mode are passed explicitly so the command
    # never falls back to reading the user's config file
    return CliRunner().invoke(
        cmd_download.cli,
        [
            "--asin",
            "ASIN0001",
            "--cover",
            "--output-dir",
            str(tmp_path),
            "--filename-mode",
            "ascii",
            "--chapter-type",
            "flat",
            *extra,
        ],
        obj=session,
    )


@pytest.mark.parametrize("extra", [(), ("--ignore-errors",)])
def test_raises_when_a_job_failed(
    monkeypatch, session, one_item_library, tmp_path, extra
):
    async def failing_cover(**kwargs):
        raise RuntimeError("cover download failed")

    monkeypatch.setattr(cmd_download, "download_cover", failing_cover)

    result = download(session, tmp_path, *extra)

    # cli.main() maps this to exit code 2; here the exception itself is what
    # proves the command did not swallow the failure
    assert isinstance(result.exception, AudibleCliException), result.output
    assert "job(s) failed" in str(result.exception)


def test_succeeds_when_every_job_worked(
    monkeypatch, session, one_item_library, tmp_path
):
    async def working_cover(**kwargs):
        return None

    monkeypatch.setattr(cmd_download, "download_cover", working_cover)

    result = download(session, tmp_path)

    assert result.exception is None, result.output
    assert result.exit_code == 0
