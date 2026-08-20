"""Resolving podcasts has to take every parent out of the library.

The parents are replaced by their episodes. One left behind is picked up
again as a job, and the command then makes a directory for episodes that
are already queued elsewhere.
"""

import pytest
from click.testing import CliRunner

from audible_cli.cmds import cmd_download
from audible_cli.config import Session
from audible_cli.models import Library


class FakeHTTPSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeApiClient:
    def __init__(self):
        self.session = FakeHTTPSession()


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(Session, "get_client", lambda self, **kw: FakeApiClient())
    return Session()


def an_item(asin, title, parent=False):
    item = {
        "asin": asin,
        "title": title,
        "content_delivery_type": "PodcastParent" if parent else "SinglePartBook",
        "has_children": parent,
        "purchase_date": "2020-01-01T00:00:00.000Z",
    }
    if parent:
        item["content_type"] = "Podcast"
    return item


@pytest.fixture
def library_of_neighbouring_parents(monkeypatch):
    """Three parents in a row, which is what the old removal skipped over."""

    async def fake_from_api_full_sync(cls, api_client, **request_params):
        return Library(
            {
                "items": [
                    an_item("BOOK0001", "A Book"),
                    an_item("CAST0001", "First Show", parent=True),
                    an_item("CAST0002", "Second Show", parent=True),
                    an_item("CAST0003", "Third Show", parent=True),
                    an_item("BOOK0002", "Another Book"),
                ]
            },
            api_client=api_client,
        )

    async def fake_resolve(self, **kwargs):
        """What the real one does: add the episodes, leave the parents."""
        episodes = [
            an_item(f"{parent.asin}EP", f"{parent.title} Episode")
            for parent in self
            if parent.is_parent_podcast()
        ]
        self.data.extend(Library({"items": episodes}, api_client=self._client).data)

    monkeypatch.setattr(
        Library, "from_api_full_sync", classmethod(fake_from_api_full_sync)
    )
    monkeypatch.setattr(Library, "resolve_podcasts", fake_resolve)


def test_resolving_queues_the_episodes_and_no_parent(
    monkeypatch, session, library_of_neighbouring_parents, tmp_path
):
    queued = []

    async def record(*args, **kwargs):
        queued.append(kwargs.get("item", args[3] if len(args) > 3 else None).asin)
        return True

    monkeypatch.setattr(cmd_download, "download_cover", record)

    result = CliRunner().invoke(
        cmd_download.cli,
        [
            "--all",
            "--resolve-podcasts",
            "--cover",
            "--output-dir",
            str(tmp_path),
            "--filename-mode",
            "ascii",
            "--chapter-type",
            "flat",
        ],
        obj=session,
    )

    assert result.exception is None, result.output
    # The books and every episode, and not one of the shows they came from
    assert sorted(queued) == [
        "BOOK0001",
        "BOOK0002",
        "CAST0001EP",
        "CAST0002EP",
        "CAST0003EP",
    ], queued
    # A parent left behind is picked up as a job of its own and makes a
    # directory for episodes that are queued already
    left_behind = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert left_behind == [], f"a show was still in the library: {left_behind}"
