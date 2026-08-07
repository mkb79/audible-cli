"""Shared stand-ins for the tests.

Kept out of ``conftest.py`` on purpose: pytest discourages importing from a
conftest module, so anything the tests import directly lives here instead.
"""

import json

import httpx


class FakeClient:
    """Stands in for ``audible.AsyncClient`` in the library code paths.

    Only the two calls the models make are implemented. ``last_params``
    records what a request was sent with, so tests can assert on the
    serialized query.
    """

    def __init__(self, items=(), license_response=None):
        self.items = list(items)
        self.license_response = license_response
        self.last_params = None

    async def get(self, path, response_callback=None, **params):
        self.last_params = params
        body = json.dumps({"items": self.items}).encode()
        return httpx.Response(
            200,
            content=body,
            headers={
                "content-type": "application/json",
                "total-count": str(len(self.items)),
            },
            request=httpx.Request("GET", f"https://example.invalid/{path}"),
        )

    async def post(self, path, body=None, headers=None):
        return self.license_response


def library_item(asin, **fields):
    """A library item with only the fields the code under test reads."""
    item = {
        "asin": asin,
        "title": f"Title {asin}",
        "content_delivery_type": "SinglePartBook",
        "has_children": False,
    }
    item.update(fields)
    return item
