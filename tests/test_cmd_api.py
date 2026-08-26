"""`audible api` asks the Audible API for JSON, and says so when it is not.

The command takes a path, never a URL: the marketplace is decided in one
place, by the profile or `--country-code`. Everything the caller typed as
a query reaches the API, and nothing the caller typed is silently dropped.
"""

import json

import httpx
import pytest
from audible.client import AsyncClient
from click.testing import CliRunner

from audible_cli.cmds import cmd_api
from audible_cli.config import Session
from audible_cli.exceptions import AudibleCliException


class FakeHTTPSession:
    """Stands in for the httpx session the client opens."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeClient:
    """Answers every verb, and remembers what it was asked."""

    def __init__(self, response=None):
        self.session = FakeHTTPSession()
        self.asked = None
        self._response = response

    def _answer(self, method, path, body, params, headers=None):
        self.asked = {
            "method": method,
            "path": path,
            "body": body,
            "params": list(params or []),
            "headers": headers,
        }
        if self._response is not None:
            return self._response
        return httpx.Response(
            200,
            json={"items": [{"asin": "B01"}]},
            request=httpx.Request(method, f"https://api.audible.de/1.0/{path}"),
        )

    async def get(self, path, response_callback=None, **kw):
        return self._answer("GET", path, None, kw.get("params"), kw.get("headers"))

    async def delete(self, path, response_callback=None, **kw):
        return self._answer("DELETE", path, None, kw.get("params"), kw.get("headers"))

    async def post(self, path, body, response_callback=None, **kw):
        return self._answer("POST", path, body, kw.get("params"), kw.get("headers"))

    async def put(self, path, body, response_callback=None, **kw):
        return self._answer("PUT", path, body, kw.get("params"), kw.get("headers"))


@pytest.fixture
def client(monkeypatch):
    """A session handing out a client that never reaches the network."""
    fake = FakeClient()
    monkeypatch.setattr(Session, "get_client", lambda self, **kw: fake)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))
    return fake


def run(*args, **kwargs):
    return CliRunner().invoke(cmd_api.cli, list(args), **kwargs)


def test_a_url_is_refused_and_says_where_to_go(client):
    # The endpoint used to be handed to the client as typed, so an
    # authenticated request went wherever it pointed.
    result = run("https://foreign.host/collect")

    assert result.exit_code == 2
    assert "takes a path" in result.stderr
    assert "audible request" in result.stderr
    assert client.asked is None, "nothing may be sent before the check"


@pytest.mark.parametrize(
    "endpoint", ["https://api.audible.de/1.0/library", "//foreign.host/x", "http://x/y"]
)
def test_every_shape_of_url_is_refused(client, endpoint):
    assert run(endpoint).exit_code == 2
    assert client.asked is None


def test_an_endpoint_that_is_no_url_at_all_is_a_usage_error(client):
    # httpx raises InvalidURL on the port, which used to leave the command
    # as an unexpected error with a traceback.
    result = run("http://x:bad/path")

    assert result.exit_code == 2
    assert "is not a path" in result.stderr


def test_a_refused_endpoint_never_builds_a_client(monkeypatch):
    def refuse(self, **kw):
        raise AssertionError("the credentials were loaded")

    monkeypatch.setattr(Session, "get_client", refuse)

    assert run("https://foreign.host/collect").exit_code == 2


@pytest.mark.parametrize("endpoint", ["", "/", "?num_results=5"])
def test_an_endpoint_is_required(client, endpoint):
    # httpx reads all three as the path `/`, which is not an endpoint.
    result = run(endpoint)

    assert result.exit_code == 2
    assert "no endpoint" in result.stderr
    assert client.asked is None


def test_a_fragment_is_refused(client):
    # It never reaches a server, so sending the path without it would be
    # answering a different question than the one that was asked.
    result = run("library#items")

    assert result.exit_code == 2
    assert "fragment" in result.stderr
    assert client.asked is None


def test_a_query_in_the_path_survives(client):
    # It used to be erased: the command always passed params={}, and httpx
    # replaces the query with it.
    run("library?num_results=5")

    assert client.asked["params"] == [("num_results", "5")]


def test_the_two_ways_of_giving_a_query_add_up(client):
    run("library?num_results=5", "-q", "response_groups=media")

    assert client.asked["params"] == [
        ("num_results", "5"),
        ("response_groups", "media"),
    ]


def test_a_value_may_contain_an_equals_sign(client):
    run("library", "-q", "continuation_token=abc==")

    assert client.asked["params"] == [("continuation_token", "abc==")]


def test_the_same_key_twice_reaches_the_api_twice(client):
    run("library", "-q", "asins=A", "-q", "asins=B")

    assert client.asked["params"] == [("asins", "A"), ("asins", "B")]


def test_a_key_on_its_own_is_a_query_without_a_value(client):
    # A valid query parameter. httpx sends it as `flag=`.
    run("library", "-q", "flag")

    assert client.asked["params"] == [("flag", "")]


def test_a_query_needs_at_least_a_name(client):
    result = run("library", "-q", "=nothing")

    assert result.exit_code == 2
    assert "has no name" in result.stderr
    assert client.asked is None


def test_a_query_in_the_path_needs_a_name_too(client):
    result = run("library?=nothing")

    assert result.exit_code == 2
    assert "without a name" in result.stderr
    assert client.asked is None


def test_the_old_spelling_still_works_and_says_it_is_old(client):
    result = run("library", "--param", "num_results=5")

    assert result.exit_code == 0
    assert client.asked["params"] == [("num_results", "5")]
    assert "The option 'param' is deprecated" in result.stderr


def test_a_body_that_is_not_json_is_a_usage_error(client):
    result = run("library", "-m", "POST", "-b", "{broken}")

    assert result.exit_code == 2
    assert "not valid JSON" in result.stderr
    assert client.asked is None


@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_a_body_reaches_the_verbs_that_carry_one(client, method):
    run("wishlist", "-m", method, "-b", '{"asin": "B01"}')

    assert client.asked["method"] == method
    assert client.asked["body"] == {"asin": "B01"}


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_a_body_needs_a_verb_that_carries_one(monkeypatch, method):
    # It used to be sent with any verb, and dropping it in silence would
    # answer a different request than the one that was written.
    def refuse(self, **kw):
        raise AssertionError("the credentials were loaded")

    monkeypatch.setattr(Session, "get_client", refuse)

    result = run("wishlist/B01", "-m", method, "-b", '{"asin": "B01"}')

    assert result.exit_code == 2
    assert "carries no body" in result.stderr


def test_a_verb_without_a_body_reaches_the_client(client):
    run("wishlist/B01", "-m", "DELETE")

    assert client.asked["method"] == "DELETE"
    assert client.asked["path"] == "wishlist/B01"


def test_the_country_code_reaches_the_client(monkeypatch):
    fake = FakeClient()
    asked = {}
    monkeypatch.setattr(
        Session, "get_client", lambda self, **kw: (asked.update(kw), fake)[1]
    )
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))

    assert run("library", "-c", "us").exit_code == 0
    assert asked == {"country_code": "us"}


@pytest.mark.parametrize("body", ["NaN", '{"x": Infinity}'])
def test_what_only_python_reads_as_json_is_refused(client, body):
    # json.loads takes these; a body carrying one cannot be serialised
    # back, and it used to fail after the credentials were loaded.
    result = run("wishlist", "-m", "POST", "-b", body)

    assert result.exit_code == 2
    assert "not valid JSON" in result.stderr
    assert client.asked is None


def test_a_body_of_null_says_it_cannot_be_sent(client):
    # `json=None` writes an empty body, so it would leave as no body.
    result = run("wishlist", "-m", "POST", "-b", "null")

    assert result.exit_code == 2
    assert "cannot be sent as a body" in result.stderr
    assert client.asked is None


def test_a_body_of_null_still_counts_as_a_body(client, tmp_path):
    # It parses to None like an option nobody gave, which used to let it
    # slip past the check below.
    path = tmp_path / "body.json"
    path.write_text('{"asin": "B01"}', encoding="utf-8")

    result = run("wishlist", "-m", "POST", "-b", "null", "--body-file", str(path))

    assert result.exit_code == 2
    assert "cannot both be given" in result.stderr
    assert client.asked is None


def test_an_indent_has_to_be_a_number(client):
    result = run("library", "-i", "vier")

    assert result.exit_code == 2
    assert client.asked is None


def test_the_answer_goes_to_stdout_as_json(client):
    result = run("library")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"items": [{"asin": "B01"}]}
    assert result.stderr == ""


def test_an_indent_reaches_the_output(client):
    result = run("library", "-i", "2")

    assert result.stdout.startswith('{\n  "items"')


def test_the_answer_can_go_to_a_file(client, tmp_path):
    path = tmp_path / "library.json"

    result = run("library", "-o", str(path))

    assert result.exit_code == 0
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "items": [{"asin": "B01"}]
    }
    assert result.stdout == ""


def test_a_python_dict_is_no_longer_an_output_format(client):
    # It printed the answer as a Python literal, which is not json, and
    # `--output` used to hand the dict itself to write_text.
    result = run("library", "-f", "dict")

    assert result.exit_code == 2
    assert "not 'json'" in result.stderr
    assert client.asked is None


def test_the_format_option_still_works_and_says_it_is_old(client):
    result = run("library", "-f", "json")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"items": [{"asin": "B01"}]}
    assert "The option 'format' is deprecated" in result.stderr


def test_an_answer_that_is_not_json_is_a_failure(monkeypatch):
    # A maintenance page used to be quoted into a JSON string and exit 0,
    # so a contract check saw valid JSON and passed.
    html = httpx.Response(
        200,
        text="<html>down for maintenance</html>",
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "https://api.audible.de/1.0/library"),
    )
    fake = FakeClient(response=html)
    monkeypatch.setattr(Session, "get_client", lambda self, **kw: fake)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))

    result = run("library")

    # cli.main() maps this to exit 2; here the exception is what shows the
    # command refused rather than quoting the page into JSON.
    assert isinstance(result.exception, AudibleCliException)
    assert result.stdout == ""
    assert "not JSON" in str(result.exception)
    assert "audible request" in str(result.exception)


def test_an_error_status_is_a_failure(monkeypatch):
    denied = httpx.Response(
        404,
        json={"message": "not found"},
        request=httpx.Request("GET", "https://api.audible.de/1.0/nope"),
    )
    fake = FakeClient(response=denied)
    monkeypatch.setattr(Session, "get_client", lambda self, **kw: fake)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))

    result = run("nope")

    assert isinstance(result.exception, AudibleCliException)
    assert "404" in str(result.exception)
    assert result.stdout == ""


def test_the_timeout_option_reaches_the_session(monkeypatch):
    seen = {}

    def get_client(self, **kw):
        seen["timeout"] = self.params.get("timeout")
        return FakeClient()

    monkeypatch.setattr(Session, "get_client", get_client)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))

    run("library", "--timeout", "5")

    assert seen["timeout"] == 5


def test_the_path_keeps_its_percent_escapes(client):
    # `httpx.URL(...).path` decodes them, which would turn `%2F` in an asin
    # into a path separator and ask for something else entirely.
    run("catalog/products/B%2F01")

    assert client.asked["path"] == "catalog/products/B%2F01"


def test_a_header_reaches_the_request(client):
    run("library", "-H", "Accept-Language: en-US")

    assert client.asked["headers"] == [("Accept-Language", "en-US")]


def test_headers_may_repeat_and_are_trimmed(client):
    # Both go out: http allows a name more than once, and a dict here
    # would have kept the last.
    run("library", "-H", "X-One:  a ", "-H", "X-One: b")

    assert client.asked["headers"] == [("X-One", "a"), ("X-One", "b")]


@pytest.mark.parametrize(
    "header",
    [
        "Authorization: Bearer x",
        "x-adp-token: stolen",
        "X-Amz-Access-Token: stolen",
        "Host: elsewhere",
        "content-type: text/xml",
        "Content-Length: 0",
    ],
)
def test_the_headers_that_carry_the_authentication_are_refused(client, header):
    result = run("library", "-H", header)

    assert result.exit_code == 2
    assert "cannot be given here" in result.stderr
    assert client.asked is None


def test_a_header_needs_a_colon(client):
    result = run("library", "-H", "nonsense")

    assert result.exit_code == 2
    assert "is not 'Name: value'" in result.stderr


def test_the_headers_can_be_written_to_a_file(client, tmp_path):
    # `continuation-token` and `total-count` only come back as headers, so
    # this is how a script pages through a long answer.
    path = tmp_path / "head.txt"

    result = run("library", "-D", str(path))

    assert result.exit_code == 0
    written = path.read_text(encoding="utf-8")
    assert written.startswith("HTTP/1.1 200 OK\n")
    assert "content-type: application/json" in written
    assert json.loads(result.stdout) == {"items": [{"asin": "B01"}]}


def test_the_headers_are_written_even_when_the_call_failed(monkeypatch, tmp_path):
    # A failed call is exactly when somebody wants to see what came back.
    denied = httpx.Response(
        429,
        json={"message": "slow down"},
        headers={"retry-after": "30"},
        request=httpx.Request("GET", "https://api.audible.de/1.0/library"),
    )
    fake = FakeClient(response=denied)
    monkeypatch.setattr(Session, "get_client", lambda self, **kw: fake)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))
    path = tmp_path / "head.txt"

    result = run("library", "-D", str(path))

    assert isinstance(result.exception, AudibleCliException)
    assert "retry-after: 30" in path.read_text(encoding="utf-8")


class NoAuth(httpx.Auth):
    """Stands in for the Authenticator, which the real client insists on."""

    def auth_flow(self, request):
        yield request


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["library"], "https://api.audible.de/1.0/library"),
        (
            ["library", "-q", "num_results=5"],
            "https://api.audible.de/1.0/library?num_results=5",
        ),
        (["library?num_results=5"], "https://api.audible.de/1.0/library?num_results=5"),
        (
            ["library?num_results=5", "-q", "rg=media"],
            "https://api.audible.de/1.0/library?num_results=5&rg=media",
        ),
        (
            ["library?a=1&a=2", "-q", "a=3"],
            "https://api.audible.de/1.0/library?a=1&a=2&a=3",
        ),
        (["library?flag"], "https://api.audible.de/1.0/library?flag="),
    ],
)
def test_the_url_that_actually_goes_out(monkeypatch, args, expected):
    # Through the real client, not a stand-in: httpx replaces the URL query
    # with whatever `params` says, so a query written into the endpoint only
    # survives because the command takes it apart and sends it along. A fake
    # client cannot show that.
    sent = {}

    def handler(request):
        sent["url"] = str(request.url)
        return httpx.Response(200, json={})

    def get_client(self, **kw):
        return AsyncClient(
            auth=NoAuth(),
            country_code=kw.get("country_code") or "de",
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(Session, "get_client", get_client)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))

    result = run(*args)

    assert result.exit_code == 0, result.exception
    assert sent["url"] == expected


def test_a_body_can_come_from_a_file(client, tmp_path):
    # Some bodies are long enough that a shell argument is the wrong place.
    path = tmp_path / "body.json"
    path.write_text('{"asins": ["B01", "B02"]}', encoding="utf-8")

    run("wishlist", "-m", "POST", "--body-file", str(path))

    assert client.asked["body"] == {"asins": ["B01", "B02"]}


def test_a_body_can_come_through_a_pipe(client):
    run("wishlist", "-m", "POST", "--body-file", "-", input='{"asin": "B01"}')

    assert client.asked["body"] == {"asin": "B01"}


def test_a_file_that_is_not_json_says_which_file(client, tmp_path):
    path = tmp_path / "body.json"
    path.write_text("{broken}", encoding="utf-8")

    result = run("wishlist", "-m", "POST", "--body-file", str(path))

    assert result.exit_code == 2
    assert "body.json is not valid JSON" in result.stderr
    assert client.asked is None


def test_the_two_ways_of_giving_a_body_exclude_each_other(client, tmp_path):
    path = tmp_path / "body.json"
    path.write_text("{}", encoding="utf-8")

    result = run("wishlist", "-m", "POST", "-b", "{}", "--body-file", str(path))

    assert result.exit_code == 2
    assert "cannot both be given" in result.stderr
    assert client.asked is None
