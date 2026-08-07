"""`ItemNotPublished` must never fail while building its message.

Covers #268, where a missing publication date reached the parser and turned
a "not published yet" notice into a stack trace.
"""

import pytest

from audible_cli.exceptions import ItemNotPublished


def test_reports_the_countdown_for_a_real_date():
    message = str(ItemNotPublished("ASIN123", "2999-01-01T00:00:00.000Z"))

    assert "ASIN123" in message
    assert "will be available in" in message


@pytest.mark.parametrize(
    "pub_date",
    [
        None,
        "",
        "garbage",
        "9999-12-31T23:59:59-23:59",  # overflows when converted to UTC
    ],
)
def test_falls_back_to_naming_the_item(pub_date):
    assert str(ItemNotPublished("ASIN123", pub_date)) == "ASIN123 is not published."
