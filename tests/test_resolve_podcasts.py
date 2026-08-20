"""Resolving podcasts, and who takes the parents out.

The episodes are added to the library and the parents stay: they are
ordinary entries to list and to export. Only a download asks for them to
go, because a parent carries no audio of its own.
"""

import asyncio

import pytest
from click.testing import CliRunner

from audible_cli.cmds import cmd_download
from audible_cli.config import Session
from audible_cli.models import Catalog, Library


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


def a_library(*items, api_client=None):
    return Library({"items": list(items)}, api_client=api_client)


def a_catalog(*items, api_client=None):
    # `response_groups` is not optional here: `_prepare_data` splits it
    # without looking first.
    return Catalog(
        {"products": list(items), "response_groups": "product_desc"},
        api_client=api_client,
    )


@pytest.fixture
def neighbouring_parents(monkeypatch):
    """Three shows in a row, which is what removing one at a time skipped.

    Each hands back one episode, the way the real call does.
    """

    async def fake_children(self, **request_params):
        # `_children` as the real one leaves it, or a show that survived a
        # resolve would trip over None before it could show the symptom
        # this file is about.
        self._children = a_library(an_item(f"{self.asin}EP", f"{self.title} Episode"))
        return self._children

    monkeypatch.setattr("audible_cli.models.LibraryItem.get_child_items", fake_children)
    return a_library(
        an_item("BOOK0001", "A Book"),
        an_item("CAST0001", "First Show", parent=True),
        an_item("CAST0002", "Second Show", parent=True),
        an_item("CAST0003", "Third Show", parent=True),
        an_item("BOOK0002", "Another Book"),
    )


# --- the model ------------------------------------------------------------


def test_the_shows_stay_unless_they_are_asked_to_go(neighbouring_parents):
    asyncio.run(neighbouring_parents.resolve_podcasts())

    assert sorted(i.asin for i in neighbouring_parents) == [
        "BOOK0001",
        "BOOK0002",
        "CAST0001",
        "CAST0001EP",
        "CAST0002",
        "CAST0002EP",
        "CAST0003",
        "CAST0003EP",
    ]


def test_asking_takes_every_show_out_including_neighbours(neighbouring_parents):
    asyncio.run(neighbouring_parents.resolve_podcasts(remove_parents=True))

    assert sorted(i.asin for i in neighbouring_parents) == [
        "BOOK0001",
        "BOOK0002",
        "CAST0001EP",
        "CAST0002EP",
        "CAST0003EP",
    ], "a show was left behind, or something else went with them"


def test_a_catalog_resolves_the_same_way(monkeypatch):
    # The two lists share the body, so the switch has to work on both.
    async def fake_children(self, **request_params):
        self._children = a_library(an_item(f"{self.asin}EP", f"{self.title} Episode"))
        return self._children

    monkeypatch.setattr("audible_cli.models.LibraryItem.get_child_items", fake_children)
    shows = [
        an_item("CAST0001", "First Show", parent=True),
        an_item("CAST0002", "Second Show", parent=True),
        an_item("CAST0003", "Third Show", parent=True),
    ]

    kept = a_catalog(*shows)
    asyncio.run(kept.resolve_podcasts())
    assert sorted(i.asin for i in kept) == [
        "CAST0001",
        "CAST0001EP",
        "CAST0002",
        "CAST0002EP",
        "CAST0003",
        "CAST0003EP",
    ], "the shows should still be there beside the episodes"

    stripped = a_catalog(*shows)
    asyncio.run(stripped.resolve_podcasts(remove_parents=True))
    assert sorted(i.asin for i in stripped) == [
        "CAST0001EP",
        "CAST0002EP",
        "CAST0003EP",
    ]


# --- and the command that asks ---------------------------------------------


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


def test_the_download_command_asks_for_the_shows_to_go(
    monkeypatch, session, neighbouring_parents, tmp_path
):
    asked = {}
    queued = []

    async def fake_from_api_full_sync(cls, api_client, **request_params):
        return neighbouring_parents

    real_resolve = Library.resolve_podcasts

    async def note(self, **kwargs):
        asked.update(kwargs)
        await real_resolve(self, **kwargs)

    async def record(*args, **kwargs):
        queued.append(kwargs["item"].asin if "item" in kwargs else args[3].asin)
        return True

    monkeypatch.setattr(
        Library, "from_api_full_sync", classmethod(fake_from_api_full_sync)
    )
    monkeypatch.setattr(Library, "resolve_podcasts", note)
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
    assert asked.get("remove_parents") is True, asked
    # The books and every episode, and not one of the shows they came from
    assert sorted(queued) == [
        "BOOK0001",
        "BOOK0002",
        "CAST0001EP",
        "CAST0002EP",
        "CAST0003EP",
    ], queued
    # A show left behind is picked up as a job of its own and makes a
    # directory for episodes that are queued already
    left = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert left == [], f"a show was still in the library: {left}"


def test_the_dates_reach_the_episodes(monkeypatch):
    # A library resolves within a date range, and the range is the whole
    # reason the caller passed one: it decides which episodes come back.
    asked = []

    async def fake_children(self, **request_params):
        asked.append(request_params)
        return a_library()

    monkeypatch.setattr("audible_cli.models.LibraryItem.get_child_items", fake_children)
    library = a_library(an_item("CAST0001", "A Show", parent=True))

    asyncio.run(library.resolve_podcasts(start_date="from", end_date="until"))

    assert asked == [{"start_date": "from", "end_date": "until"}], asked


def test_the_deprecated_spelling_actually_resolves(monkeypatch, recwarn):
    # `resolve_podcats` handed back the coroutine of the method it forwards
    # to instead of awaiting it, so it warned and then did nothing.
    async def fake_children(self, **request_params):
        self._children = a_library(an_item(f"{self.asin}EP", "An Episode"))
        return self._children

    monkeypatch.setattr("audible_cli.models.LibraryItem.get_child_items", fake_children)
    library = a_library(an_item("CAST0001", "A Show", parent=True))

    asyncio.run(library.resolve_podcats())

    assert sorted(i.asin for i in library) == ["CAST0001", "CAST0001EP"]
    assert any(issubclass(w.category, DeprecationWarning) for w in recwarn)
