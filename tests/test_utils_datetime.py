"""Parsing of API timestamps and the UTC handling around it.

Covers the crash from #264, where the API returns the same field with and
without fractional seconds, and the timezone-aware conversion added in #266.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from audible_cli.utils import datetime_type, parse_api_datetime, to_utc_datetime


@pytest.mark.parametrize(
    "value, expected",
    [
        # The two shapes the API actually returns. Top-level library items
        # usually carry the fraction, podcast episodes usually do not.
        ("2019-11-29T11:40:49.000Z", datetime(2019, 11, 29, 11, 40, 49, tzinfo=UTC)),
        ("2019-11-29T11:40:49Z", datetime(2019, 11, 29, 11, 40, 49, tzinfo=UTC)),
        # More precision than datetime keeps is truncated, not rejected
        (
            "2019-11-29T11:40:49.1234567Z",
            datetime(2019, 11, 29, 11, 40, 49, 123456, tzinfo=UTC),
        ),
        # An offset other than Z denotes the same instant
        ("2019-11-29T13:40:49+02:00", datetime(2019, 11, 29, 11, 40, 49, tzinfo=UTC)),
    ],
)
def test_parse_api_datetime_accepts_and_normalizes(value, expected):
    parsed = parse_api_datetime(value)

    assert parsed == expected
    assert parsed.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "value",
    [
        "2019-11-29T11:40:49",  # no timezone, so the instant is ambiguous
        "2019-11-29",
        "garbage",
        "",
        None,
    ],
)
def test_parse_api_datetime_rejects_unusable_values(value):
    # ValueError rather than TypeError even for None, so callers only have to
    # handle one kind of failure
    with pytest.raises(ValueError):
        parse_api_datetime(value)


def test_to_utc_datetime_reads_naive_as_utc():
    assert to_utc_datetime(datetime(2020, 1, 1, 12)) == datetime(
        2020, 1, 1, 12, tzinfo=UTC
    )


def test_to_utc_datetime_converts_other_offsets():
    berlin = timezone(timedelta(hours=2))

    assert to_utc_datetime(datetime(2020, 1, 1, 14, tzinfo=berlin)) == datetime(
        2020, 1, 1, 12, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "value",
    [
        "2019-11-29",
        "2019-11-29T11:40:49",
        "2019-11-29 11:40:49",
        "2019-11-29T11:40:49.000Z",
        "2019-11-29T11:40:49Z",
    ],
)
def test_datetime_type_always_yields_utc(value):
    # Every accepted format either ends in a literal Z or carries no timezone
    # at all, and both mean UTC for the --start-date/--end-date options
    converted = datetime_type.convert(value, None, None)

    assert converted.utcoffset() == timedelta(0)


def test_datetime_type_normalizes_an_already_parsed_datetime():
    # click hands a datetime straight back, so the conversion has to catch it
    converted = datetime_type.convert(datetime(2020, 1, 1, 12), None, None)

    assert converted == datetime(2020, 1, 1, 12, tzinfo=UTC)
