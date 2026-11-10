"""Empty search strings must not crash title matching.

`Library.search_item_by_title` scores every item through
`BaseItem.substring_in_title_accuracy`, which divides the longest-match
size by the length of the query. An empty query therefore divided by
zero instead of simply matching nothing.
"""

from audible_cli.utils import LongestSubString


def test_empty_search_string_scores_zero():
    assert LongestSubString("", "Some Title").percentage == 0.0


def test_empty_search_string_has_empty_match():
    assert LongestSubString("", "Some Title").longest_match == ""


def test_non_empty_search_still_scores():
    assert LongestSubString("Some Title", "Some Title").percentage == 100.0
