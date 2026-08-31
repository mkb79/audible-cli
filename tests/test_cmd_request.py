"""`audible request` sends what it was given, wherever it is allowed to.

The url decides the host, and only the hosts audible-cli itself talks to
are allowed, because the request carries the credentials of the profile.
Body and answer pass through untouched: this is the command for what
`audible api` cannot do.
"""

import gzip

import httpx
import pytest
from audible.client import AsyncClient
from click.testing import CliRunner

from audible_cli.cmds import cmd_request
from audible_cli.config import Session
from audible_cli.exceptions import AudibleCliException


class FakeHTTPSession:
    """Stands in for the httpx session the client opens."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class Streamed:
    """What `raw_request(stream=True)` hands back."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc_info):
        return False


class FakeClient:
    """Answers every request, and remembers what it was asked."""

    def __init__(self, response=None):
        self.session = FakeHTTPSession()
        self.asked = None
        self._response = response

    def raw_request(self, method, url, **kwargs):
        self.asked = {
            "method": method,
            "url": url,
            "stream": kwargs.get("stream"),
            "headers": kwargs.get("headers"),
            "content": kwargs.get("content"),
        }
        response = self._response
        if response is None:
            response = httpx.Response(
                200,
                content=b"answer",
                headers={"content-type": "text/plain"},
                request=httpx.Request(method, url),
            )
        return Streamed(response)


@pytest.fixture
def client(monkeypatch):
    """A session handing out a client that never reaches the network."""
    fake = FakeClient()
    monkeypatch.setattr(Session, "get_client", lambda self, **kw: fake)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))
    return fake


def answering(monkeypatch, response):
    """Hand out a client that gives back `response`."""
    fake = FakeClient(response)
    monkeypatch.setattr(Session, "get_client", lambda self, **kw: fake)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))
    return fake


def run(*args, **kwargs):
    return CliRunner().invoke(cmd_request.cli, list(args), **kwargs)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.audible.de/1.0/library",
        "https://www.audible.co.uk/library/download",
        "https://cds.audible.com/download",
        "https://api.amazon.com/user/profile",
        "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar",
    ],
)
def test_every_host_audible_cli_talks_to_is_allowed(client, url):
    assert run(url).exit_code == 0
    assert client.asked["url"] == url


def test_a_host_of_its_own_is_refused(client):
    # The request would hand the profile's credentials to whoever the url
    # names, so the check comes before a client exists.
    result = run("https://foreign.host/collect")

    assert result.exit_code == 2
    assert "not a host audible-cli talks to" in result.stderr
    assert client.asked is None


def test_a_refused_url_never_builds_a_client(monkeypatch):
    def refuse(self, **kw):
        raise AssertionError("the credentials were loaded")

    monkeypatch.setattr(Session, "get_client", refuse)

    assert run("https://foreign.host/collect").exit_code == 2


def test_plain_http_is_refused(client):
    result = run("http://api.audible.de/1.0/library")

    assert result.exit_code == 2
    assert "in the clear" in result.stderr
    assert client.asked is None


def test_a_path_says_which_command_takes_one(client):
    result = run("1.0/library")

    assert result.exit_code == 2
    assert "audible api" in result.stderr
    assert client.asked is None


def test_a_port_of_its_own_is_refused(client):
    result = run("https://api.audible.de:8443/1.0/library")

    assert result.exit_code == 2
    assert "port 8443" in result.stderr


def test_a_name_and_password_in_the_url_are_refused(client):
    result = run("https://someone:secret@api.audible.de/1.0/library")

    assert result.exit_code == 2
    assert "name and password of its own" in result.stderr


def test_a_url_that_does_not_parse_is_a_usage_error(client):
    result = run("https://api.audible.de:bad/x")

    assert result.exit_code == 2
    assert "is not a url" in result.stderr


def test_a_query_in_the_url_survives(client):
    run("https://api.audible.de/1.0/library?num_results=5")

    assert client.asked["url"] == "https://api.audible.de/1.0/library?num_results=5"


def test_the_two_ways_of_giving_a_query_add_up(client):
    run("https://api.audible.de/1.0/library?a=1", "-q", "b=2", "-q", "a=3")

    assert client.asked["url"] == ("https://api.audible.de/1.0/library?a=1&b=2&a=3")


def test_a_query_is_sent_exactly_as_it_was_written(client):
    # A delivery url is signed over these bytes. Reading the query into
    # pairs and writing it back would turn `%FF` into the replacement
    # character, `%20` into `+`, and `flag` into `flag=`.
    run("https://cds.audible.de/f.aax?sig=%FF&a=b%20c&flag")

    assert client.asked["url"] == "https://cds.audible.de/f.aax?sig=%FF&a=b%20c&flag"


def test_an_added_query_leaves_the_written_one_alone(client):
    run("https://cds.audible.de/f.aax?sig=%FF", "-q", "z=1", "-q", "z=2")

    assert client.asked["url"] == "https://cds.audible.de/f.aax?sig=%FF&z=1&z=2"


def test_a_query_needs_a_name(client):
    result = run("https://www.audible.de/x", "-q", "=nothing")

    assert result.exit_code == 2
    assert "has no name" in result.stderr


@pytest.mark.parametrize(
    "method", ["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH", "OPTIONS"]
)
def test_every_method_reaches_the_client(client, method):
    # Not a fixed list: a host that answers PATCH is not the client's
    # business to argue with.
    run("https://www.audible.de/x", "-m", method.lower())

    assert client.asked["method"] == method


def test_something_that_is_no_method_is_a_usage_error(client):
    result = run("https://www.audible.de/x", "-m", "GET;DROP")

    assert result.exit_code == 2
    assert "not an http method" in result.stderr
    assert client.asked is None


def test_the_answer_is_streamed(client):
    # A file from the delivery host runs to gigabytes, and reading it
    # whole into memory before writing it out would not end well.
    run("https://cds.audible.de/f.aax")

    assert client.asked["stream"] is True


def test_a_body_goes_out_as_it_was_written(client):
    # Not json, and not turned into any: what is typed is what is sent.
    run("https://www.audible.de/x", "-m", "POST", "-b", "a=1&b=2")

    assert client.asked["content"] == b"a=1&b=2"


def test_a_body_can_come_from_a_file(client, tmp_path):
    path = tmp_path / "body.bin"
    path.write_bytes(b"\x00\x01 raw")

    run("https://www.audible.de/x", "-m", "POST", "--body-file", str(path))

    assert client.asked["content"] == b"\x00\x01 raw"


def test_a_body_can_come_through_a_pipe(client):
    run("https://www.audible.de/x", "-m", "POST", "--body-file", "-", input="piped")

    assert client.asked["content"] == b"piped"


def test_the_two_ways_of_giving_a_body_exclude_each_other(client, tmp_path):
    path = tmp_path / "body.bin"
    path.write_bytes(b"x")

    result = run("https://www.audible.de/x", "-b", "y", "--body-file", str(path))

    assert result.exit_code == 2
    assert "cannot both be given" in result.stderr
    assert client.asked is None


def test_a_header_reaches_the_request(client):
    run("https://www.audible.de/x", "-H", "Accept: text/html")

    assert client.asked["headers"] == [("Accept", "text/html")]


def test_the_content_type_is_the_callers_to_set(client):
    # `api` refuses it, because it writes the body itself. Here the body
    # is whatever was given, so its type has to be too.
    run(
        "https://www.audible.de/x",
        "-m",
        "POST",
        "-b",
        "a=1",
        "-H",
        "Content-Type: application/x-www-form-urlencoded",
    )

    assert client.asked["headers"] == [
        ("Content-Type", "application/x-www-form-urlencoded")
    ]


@pytest.mark.parametrize(
    "header",
    [
        "Authorization: Bearer x",
        "x-adp-token: x",
        "X-Amz-Access-Token: x",
        "Host: foreign.host",
    ],
)
def test_the_headers_that_carry_the_authentication_are_refused(client, header):
    result = run("https://www.audible.de/x", "-H", header)

    assert result.exit_code == 2
    assert "cannot be given here" in result.stderr
    assert client.asked is None


def test_the_answer_reaches_stdout_as_it_arrived(monkeypatch):
    # Bytes, not text: this command is what fetches a cover or a pdf.
    answering(
        monkeypatch,
        httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\n",
            request=httpx.Request("GET", "https://www.audible.de/x"),
        ),
    )

    result = run("https://www.audible.de/x")

    assert result.exit_code == 0
    assert result.stdout_bytes == b"\x89PNG\r\n\x1a\n"


def test_include_writes_the_head_before_the_body(monkeypatch):
    answering(
        monkeypatch,
        httpx.Response(
            200,
            content=b"body",
            headers={"x-total-count": "7"},
            request=httpx.Request("GET", "https://www.audible.de/x"),
        ),
    )

    result = run("https://www.audible.de/x", "-i")

    head, _, body = result.stdout_bytes.partition(b"\n\n")
    assert head.startswith(b"HTTP/1.1 200 OK\n")
    assert b"x-total-count: 7" in head
    assert body == b"body"


def test_the_answer_can_go_to_a_file(monkeypatch, tmp_path):
    answering(
        monkeypatch,
        httpx.Response(
            200,
            content=b"\x00\x01",
            request=httpx.Request("GET", "https://www.audible.de/x"),
        ),
    )
    path = tmp_path / "out.bin"

    result = run("https://www.audible.de/x", "-o", str(path))

    assert path.read_bytes() == b"\x00\x01"
    assert result.stdout_bytes == b""


def test_the_headers_can_be_written_to_a_file(monkeypatch, tmp_path):
    answering(
        monkeypatch,
        httpx.Response(
            200,
            content=b"body",
            headers={"continuation-token": "abc"},
            request=httpx.Request("GET", "https://www.audible.de/x"),
        ),
    )
    path = tmp_path / "head.txt"

    run("https://www.audible.de/x", "-D", str(path))

    written = path.read_text(encoding="utf-8")
    assert written.startswith("HTTP/1.1 200 OK\n")
    assert "continuation-token: abc" in written


def test_an_error_status_ends_the_command_after_the_answer(monkeypatch, tmp_path):
    # The body of a refusal says why it was refused, so it is written
    # before the status is reported.
    answering(
        monkeypatch,
        httpx.Response(
            403,
            content=b'{"message": "no"}',
            request=httpx.Request("GET", "https://www.audible.de/x"),
        ),
    )
    path = tmp_path / "head.txt"

    result = run("https://www.audible.de/x", "-D", str(path))

    assert isinstance(result.exception, AudibleCliException)
    assert "403" in str(result.exception)
    assert result.stdout_bytes == b'{"message": "no"}'
    assert path.read_text(encoding="utf-8").startswith("HTTP/1.1 403 Forbidden")


def test_a_transport_error_is_reported_as_a_failure(monkeypatch):
    class Failing(FakeClient):
        def raw_request(self, method, url, **kwargs):
            raise httpx.ConnectTimeout("too slow")

    monkeypatch.setattr(Session, "get_client", lambda self, **kw: Failing())
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))

    result = run("https://www.audible.de/x")

    assert isinstance(result.exception, AudibleCliException)
    assert "too slow" in str(result.exception)


def test_the_timeout_option_reaches_the_session(monkeypatch):
    seen = {}

    def get_client(self, **kw):
        seen["timeout"] = self.params.get("timeout")
        return FakeClient()

    monkeypatch.setattr(Session, "get_client", get_client)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))

    assert run("https://www.audible.de/x", "--timeout", "60").exit_code == 0
    assert seen["timeout"] == 60


class NoAuth(httpx.Auth):
    """Stands in for the Authenticator, which the real client insists on."""

    def auth_flow(self, request):
        yield request


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["https://www.audible.de/x"], "https://www.audible.de/x"),
        (["https://www.audible.de/x?a=1"], "https://www.audible.de/x?a=1"),
        (
            ["https://www.audible.de/x?a=1", "-q", "a=2"],
            "https://www.audible.de/x?a=1&a=2",
        ),
        # `flag` keeps its shape: a query written into the url is not
        # read into pairs and written back.
        (["https://www.audible.de/x?flag"], "https://www.audible.de/x?flag"),
        (
            ["https://www.audible.de/x?sig=%FF&a=b%20c"],
            "https://www.audible.de/x?sig=%FF&a=b%20c",
        ),
    ],
)
def test_the_url_that_actually_goes_out(monkeypatch, args, expected):
    # Through the real client, not a stand-in. httpx erases a url query
    # when it is handed a `params` of its own, so the command takes the
    # query apart and sends every pair along.
    sent = {}

    def handler(request):
        sent["url"] = str(request.url)
        return httpx.Response(200, content=b"")

    def get_client(self, **kw):
        return AsyncClient(
            auth=NoAuth(),
            country_code="de",
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(Session, "get_client", get_client)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))

    result = run(*args)

    assert result.exit_code == 0, result.exception
    assert sent["url"] == expected


def test_include_puts_the_head_wherever_the_body_goes(monkeypatch, tmp_path):
    # With `-o` the file is the answer, so the head belongs in it. curl
    # does the same, and `-D` stays the way to keep them apart.
    answering(
        monkeypatch,
        httpx.Response(
            200,
            content=b"body",
            request=httpx.Request("GET", "https://www.audible.de/x"),
        ),
    )
    path = tmp_path / "out.txt"

    result = run("https://www.audible.de/x", "-i", "-o", str(path))

    assert path.read_bytes().endswith(b"\n\nbody")
    assert path.read_bytes().startswith(b"HTTP/1.1 200 OK\n")
    assert result.stdout_bytes == b""


def test_a_header_that_came_twice_is_written_twice(monkeypatch, tmp_path):
    # Two `set-cookie` are not one `set-cookie` with a comma in it.
    answering(
        monkeypatch,
        httpx.Response(
            200,
            content=b"",
            headers=[("set-cookie", "a=1"), ("set-cookie", "b=2")],
            request=httpx.Request("GET", "https://www.audible.de/x"),
        ),
    )
    path = tmp_path / "head.txt"

    run("https://www.audible.de/x", "-D", str(path))

    written = path.read_text(encoding="utf-8")
    assert "set-cookie: a=1\n" in written
    assert "set-cookie: b=2\n" in written


def test_a_compressed_answer_arrives_readable(monkeypatch):
    # httpx unpacks it on the way through. The headers still describe
    # what came over the wire, which is what the server said.
    answering(
        monkeypatch,
        httpx.Response(
            200,
            content=gzip.compress(b"plain text"),
            headers={"content-encoding": "gzip"},
            request=httpx.Request("GET", "https://www.audible.de/x"),
        ),
    )

    result = run("https://www.audible.de/x", "-i")

    head, _, body = result.stdout_bytes.partition(b"\n\n")
    assert body == b"plain text"
    assert b"content-encoding: gzip" in head


def test_an_error_body_lands_in_the_output_file(monkeypatch, tmp_path):
    answering(
        monkeypatch,
        httpx.Response(
            500,
            content=b"the server is unwell",
            request=httpx.Request("GET", "https://www.audible.de/x"),
        ),
    )
    path = tmp_path / "out.txt"

    result = run("https://www.audible.de/x", "-o", str(path))

    assert isinstance(result.exception, AudibleCliException)
    assert path.read_bytes() == b"the server is unwell"


@pytest.mark.parametrize(
    "url",
    [
        "https://192.0.2.1/x",
        "https://[2001:db8::1]/x",
        "https://api.audible.de.evil.test/x",
        "https://api.audible.de.",
        "https://xn--pi-audible-33a.de/x",
    ],
)
def test_a_host_that_only_looks_right_is_refused(client, url):
    assert run(url).exit_code == 2
    assert client.asked is None


def test_the_host_is_matched_whatever_the_shift_key_did(client):
    assert run("https://API.Audible.DE/1.0/library").exit_code == 0


def test_a_redirect_is_not_followed(monkeypatch):
    # Through the real client: `follow_redirects=True` would send the
    # credentials to wherever `location` points, and a stand-in client
    # cannot show that it stays off.
    asked = []

    def handler(request):
        asked.append(str(request.url))
        if request.url.path == "/first":
            return httpx.Response(
                302, headers={"location": "https://cds.audible.de/second"}
            )
        return httpx.Response(200, content=b"followed")

    def get_client(self, **kw):
        return AsyncClient(
            auth=NoAuth(),
            country_code="de",
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(Session, "get_client", get_client)
    monkeypatch.setattr(Session, "auth", property(lambda self: "AUTH"))

    result = run("https://cde-ta-g7g.amazon.com/first", "-i")

    assert asked == ["https://cde-ta-g7g.amazon.com/first"]
    assert b"location: https://cds.audible.de/second" in result.stdout_bytes
    assert isinstance(result.exception, AudibleCliException) is False
