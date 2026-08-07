"""Date filtering and publication checks on library items.

Covers the crash from #264, the naive/aware comparison risk from #266 and
the missing-field handling from #268.
"""

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
from helpers import FakeClient, library_item

from audible_cli.models import Library, LibraryItem


def sync(coro):
    return asyncio.run(coro)


def filtered(items, **bounds):
    client = FakeClient(items)
    library = sync(Library.from_api(client, **bounds))
    return sorted(i.asin for i in library), client


ITEMS = [
    library_item("WITHFRAC", purchase_date="2019-11-29T11:40:49.000Z"),
    # The shape that used to crash: an episode without fractional seconds
    library_item("NOFRAC__", purchase_date="2019-11-29T11:40:49Z"),
    library_item("TOOOLD__", purchase_date="2007-01-01T09:00:00Z"),
    library_item(
        "FALLBACK",
        purchase_date=None,
        library_status={"date_added": "2020-05-05T10:00:00Z"},
    ),
    library_item("NODATE__", purchase_date=None, library_status=None),
]


def test_start_date_keeps_both_timestamp_shapes():
    kept, _ = filtered(ITEMS, start_date=datetime(2008, 1, 1))

    # TOOOLD__ is the only item before the bound; the undatable one is kept
    assert kept == ["FALLBACK", "NODATE__", "NOFRAC__", "WITHFRAC"]


def test_naive_bounds_do_not_clash_with_aware_timestamps():
    # A programmatic caller may still pass naive datetimes
    kept, client = filtered(ITEMS, start_date=datetime(2008, 1, 1))

    assert kept
    assert client.last_params["purchased_after"] == "2008-01-01T00:00:00.000000Z"


def test_aware_bounds_are_converted_before_serialization():
    berlin = timezone(timedelta(hours=2))

    kept, client = filtered(ITEMS, start_date=datetime(2008, 1, 1, 2, tzinfo=berlin))

    assert kept == ["FALLBACK", "NODATE__", "NOFRAC__", "WITHFRAC"]
    assert client.last_params["purchased_after"] == "2008-01-01T00:00:00.000000Z"


def test_end_date_drops_later_items():
    kept, _ = filtered(
        ITEMS, start_date=datetime(2008, 1, 1), end_date=datetime(2020, 1, 1)
    )

    assert "FALLBACK" not in kept  # added 2020-05, past the bound


@pytest.mark.parametrize(
    "fields",
    [
        {"purchase_date": None},  # library_status missing entirely
        {"purchase_date": None, "library_status": None},
        {"purchase_date": None, "library_status": {"date_added": None}},
    ],
)
def test_items_without_any_date_are_kept_not_crashed_on(fields):
    kept, _ = filtered([library_item("A", **fields)], start_date=datetime(2008, 1, 1))

    assert kept == ["A"]


def test_date_added_branch_still_filters():
    items = [
        library_item(
            "KEEP", purchase_date=None, library_status={"date_added": "2020-05-05T10:00:00Z"}
        ),
        library_item(
            "DROP", purchase_date=None, library_status={"date_added": "2001-01-01T10:00:00Z"}
        ),
    ]

    kept, _ = filtered(items, start_date=datetime(2008, 1, 1))

    assert kept == ["KEEP"]


@pytest.mark.parametrize(
    "publication_datetime, expected",
    [
        ("2019-11-29T11:40:49Z", True),
        ("2019-11-29T11:40:49.000Z", True),
        ("2999-01-01T00:00:00Z", False),
        ("2999-01-01T00:00:00.000Z", False),
        (None, True),  # unknown is not the same as unpublished
    ],
)
def test_is_published(client, publication_datetime, expected):
    item = LibraryItem(
        library_item("X", publication_datetime=publication_datetime),
        api_client=client,
    )

    assert item.is_published() is expected


@pytest.mark.parametrize(
    "parent_date, child_date, expected",
    [
        # The parent date wins while it is there
        ("2999-01-01T00:00:00Z", "2019-01-01T00:00:00Z", False),
        ("2019-01-01T00:00:00Z", "2999-01-01T00:00:00Z", True),
        # Without one, the part's own date still counts
        (None, "2999-01-01T00:00:00Z", False),
        (None, "2019-01-01T00:00:00Z", True),
        # Only when neither is known is the part assumed published
        (None, None, True),
    ],
)
def test_audiopart_falls_back_to_its_own_publication_date(
    client, parent_date, child_date, expected
):
    parent = LibraryItem(
        library_item(
            "P", publication_datetime=parent_date, content_delivery_type="MultiPartBook"
        ),
        api_client=client,
    )
    child = LibraryItem(
        library_item(
            "C", publication_datetime=child_date, content_delivery_type="AudioPart"
        ),
        api_client=client,
    )
    child._parent = parent

    assert child.is_published() is expected


def test_publication_check_compares_against_utc_now(client):
    # Guards against a naive/aware TypeError in the comparison
    soon = datetime.now(UTC) + timedelta(days=1)
    item = LibraryItem(
        library_item("X", publication_datetime=soon.strftime("%Y-%m-%dT%H:%M:%SZ")),
        api_client=client,
    )

    assert item.is_published() is False
