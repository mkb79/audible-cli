"""Repeating a CDE request that never got an answer.

Downloading a library with `-j 8` produced a run of failures, all of them
`download_annotations`, all reading `RequestError: Server disconnected
without sending a response.` Without `--ignore-errors` the first one ends
the whole run.

The endpoint itself is healthy: asked on its own it answers a clean 404 for
every title, sequentially and at eight at a time. The failure needs the rest
of the run, eight concurrent streaming downloads sharing one connection
pool, which is what a reused connection the server has already closed looks
like from httpx.
"""

import ast
import asyncio
import inspect

import httpx
import pytest
from audible.exceptions import (
    NetworkError,
    NotFoundError,
    NotResponding,
    RequestError,
    Unauthorized,
)

from audible_cli import models
from audible_cli.models import CDE_ATTEMPTS
from audible_cli.utils import is_transient, request_with_retry


class Answered:
    """Enough of a response for the library's status errors to build."""

    def __init__(self, status_code, reason_phrase="Not Found"):
        self.status_code = status_code
        self.reason_phrase = reason_phrase


def not_found():
    return NotFoundError(Answered(404), None)


def disconnected():
    """What the audible client turns a dropped connection into."""
    return RequestError(
        httpx.RemoteProtocolError("Server disconnected without sending a response.")
    )


class Answering:
    """Fails the first `failures` calls, then answers."""

    def __init__(self, error, failures):
        self.error = error
        self.failures = failures
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "the answer"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Nobody waits in a test run.

    The two tests about the wait itself replace this with their own
    recorder.
    """

    async def at_once(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", at_once)


def run(make_request):
    return asyncio.run(request_with_retry(make_request, "A request"))


# --- what counts as worth repeating ---------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        disconnected(),
        RequestError(httpx.ReadError("connection reset")),
        NetworkError(),
        NotResponding(),
        httpx.RemoteProtocolError("raised straight at us"),
        httpx.ConnectTimeout("no answer"),
    ],
)
def test_a_request_that_got_no_answer_is_repeated(error):
    assert is_transient(error), f"{type(error).__name__} should be retried"


@pytest.mark.parametrize(
    "error",
    [
        not_found(),  # the ordinary "this title has none"
        Unauthorized(
            Answered(401, "Unauthorized"), None
        ),  # will not heal by asking again
        httpx.LocalProtocolError("our own mistake"),
        ValueError("nothing to do with the network"),
    ],
)
def test_an_answer_is_not_repeated(error):
    assert not is_transient(error), f"{type(error).__name__} should not be retried"


def test_a_status_error_is_never_transient_although_it_is_a_request_error():
    # The trap: every StatusError is a RequestError too, so catching the
    # base class would repeat every 404 — and for annotations a 404 is the
    # normal answer for a title nobody has bookmarked.
    assert issubclass(NotFoundError, RequestError)
    assert not is_transient(not_found())


# --- and what the helper does with it -------------------------------------


def test_it_gives_up_after_the_last_attempt():
    always = Answering(disconnected(), failures=CDE_ATTEMPTS)

    with pytest.raises(RequestError):
        run(always)

    assert always.calls == CDE_ATTEMPTS


def test_it_returns_the_answer_once_one_arrives():
    twice = Answering(disconnected(), failures=2)

    assert run(twice) == "the answer"
    assert twice.calls == 3


def test_an_answer_ends_it_at_once():
    # A 404 is an answer. Asking again would be pointless traffic against a
    # host that is already being asked by eight workers.
    once = Answering(not_found(), failures=1)

    with pytest.raises(NotFoundError):
        run(once)

    assert once.calls == 1


def test_a_first_attempt_that_works_is_not_slowed_down():
    straight = Answering(disconnected(), failures=0)

    assert run(straight) == "the answer"
    assert straight.calls == 1


def test_the_wait_grows_and_is_not_the_same_for_everyone(monkeypatch):
    # Eight workers that fail together must not come back together.
    waits = []

    async def record(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    with pytest.raises(RequestError):
        run(Answering(disconnected(), failures=CDE_ATTEMPTS))

    assert len(waits) == CDE_ATTEMPTS - 1, "no wait after the last attempt"
    assert waits[1] > waits[0], "the wait grows"
    assert not any(w in (0.5, 1.0) for w in waits), "and carries jitter"


def test_it_says_so_when_it_repeats(caplog, monkeypatch):
    # A silent retry hides an outage that is getting worse.
    async def nowait(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", nowait)
    with caplog.at_level("WARNING", logger="audible_cli.models"):
        run(Answering(disconnected(), failures=1))

    assert "Server disconnected" in caplog.text
    assert "RequestError" in caplog.text


def test_both_cde_requests_go_through_the_retry():
    """The two calls to the CDE host, and only those, are repeated.

    Read from the syntax tree: reaching either one for real needs an
    authenticated client and the network, and a call that quietly bypasses
    the helper looks identical until a connection drops in production.
    """
    tree = ast.parse(inspect.getsource(models))
    cde_methods = set()
    wrapped = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        body = ast.unparse(node)
        if "FionaCDEServiceEngine" not in body:
            continue
        cde_methods.add(node.name)
        if "request_with_retry" in body:
            wrapped.add(node.name)

    assert cde_methods == {"get_annotations", "get_aax_url_old"}, cde_methods
    assert wrapped == cde_methods, f"not repeated: {cde_methods - wrapped}"


def test_the_caller_decides_how_often_and_how_long():
    # The helper is generic; the numbers are the caller's policy.
    stubborn = Answering(disconnected(), failures=4)

    assert asyncio.run(request_with_retry(stubborn, "A request", attempts=5)) == (
        "the answer"
    )
    assert stubborn.calls == 5


def test_the_first_delay_is_the_callers_too(monkeypatch):
    waits = []

    async def record(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    with pytest.raises(RequestError):
        asyncio.run(
            request_with_retry(
                Answering(disconnected(), failures=3),
                "A request",
                attempts=3,
                first_delay=10,
            )
        )

    assert waits[0] > 5, waits
