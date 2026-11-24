"""PaperlessClient: headers, retries, error mapping and redaction, on real endpoint shapes."""

import asyncio
import contextlib
import email.utils
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, cast, override

import httpx2
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from adapter.conftest import (
    NOW,
    TOKEN,
    ClientFactory,
    RecordingSleep,
    streaming,
    tag,
)
from mcp_server_paperless.domain.errors import (
    AuthenticationFailed,
    Conflict,
    FeatureUnavailable,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UnsupportedVersion,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import (
    ERROR_BODY_LIMIT,
    Method,
    PaperlessClient,
    RetryPolicy,
    retry_after,
    utc_now,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


pytestmark = pytest.mark.anyio

TAG_PAGE = {"count": 1, "next": None, "previous": None, "results": [tag(1, "inbox")]}


class TagStub(BaseModel):
    id: int
    name: str


def answer(status: int, body: object = None, **headers: str) -> httpx2.Response:
    return httpx2.Response(status, json=body, headers=headers)


class Script:
    """A handler that answers with the given responses in order and records the requests."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses: list[httpx2.Response] = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def raising(error: type[httpx2.TransportError]) -> Script:
    class Raising(Script):
        @override
        def __call__(self, request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            message = "simulated"
            raise error(message, request=request)

    return Raising()


@pytest.mark.decision("decision-2")
async def test_every_request_sends_api_version_10_and_the_credential(
    make_client: ClientFactory,
) -> None:
    script = Script(answer(200, tag(1, "inbox")), answer(201, tag(2, "new")))
    client = make_client(script)

    _ = await client.request("GET", "/api/tags/1/")
    _ = await client.request("POST", "/api/tags/", json={"name": "new"})

    for request in script.requests:
        assert request.headers["Accept"] == "application/json; version=10"
        assert request.headers["Authorization"] == f"Token {TOKEN}"


@pytest.mark.decision("decision-3")
async def test_requests_go_through_the_injected_client(make_client: ClientFactory) -> None:
    script = Script(answer(200, TAG_PAGE))

    _ = await make_client(script).request("GET", "/api/tags/", params={"page_size": 25})

    assert str(script.requests[0].url) == "http://paperless.test/api/tags/?page_size=25"


async def test_get_parses_the_body_into_the_model(make_client: ClientFactory) -> None:
    client = make_client(Script(answer(200, tag(7, "inbox"))))

    assert await client.get("/api/tags/7/", TagStub) == TagStub(id=7, name="inbox")


async def test_call_parses_any_method_through_one_path(make_client: ClientFactory) -> None:
    client = make_client(Script(answer(201, tag(9, "new")), answer(201, tag(10, "x"))))

    created = await client.call("POST", "/api/tags/", TagStub, json={"name": "new"})

    assert created == TagStub(id=9, name="new")
    with pytest.raises(PaperlessMcpError, match="unexpected response for POST /api/tags/"):
        _ = await client.call("POST", "/api/tags/", BaseModelWithMissingField, json={"name": "x"})


async def test_an_unexpected_body_is_a_client_safe_error(make_client: ClientFactory) -> None:
    client = make_client(Script(answer(200, tag(7, "inbox"))))

    with pytest.raises(PaperlessMcpError, match="unexpected response") as read:
        _ = await client.get("/api/tags/7/", BaseModelWithMissingField)

    assert read.value.hint is None


async def test_an_unexpected_answer_to_a_write_says_that_it_may_have_been_applied(
    make_client: ClientFactory,
) -> None:
    client = make_client(Script(answer(201, tag(7, "inbox"))))

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await client.call("POST", "/api/tags/", BaseModelWithMissingField, json={"name": "x"})

    assert raised.value.hint == (
        "The request may have been applied: read the data again before repeating it."
    )


class BaseModelWithMissingField(BaseModel):
    not_in_the_response: str


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_idempotent_requests_retry_transient_statuses(
    make_client: ClientFactory, sleep: RecordingSleep, status: int
) -> None:
    script = Script(answer(status, {"detail": "busy"}), answer(200, TAG_PAGE))

    response = await make_client(script).request("GET", "/api/tags/")

    assert response.status_code == 200
    assert len(script.requests) == 2
    assert len(sleep.delays) == 1


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
async def test_every_idempotent_method_retries(make_client: ClientFactory, method: Method) -> None:
    done = answer(204) if method == "DELETE" else answer(200, tag(1, "inbox"))
    script = Script(answer(503), done)
    body = {"name": "inbox"} if method == "PUT" else None

    _ = await make_client(script).request(method, "/api/tags/1/", json=body)

    assert len(script.requests) == 2


@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_post_and_patch_never_retry(
    make_client: ClientFactory, sleep: RecordingSleep, method: Method
) -> None:
    script = Script(answer(503))
    path = "/api/tags/" if method == "POST" else "/api/tags/1/"

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script).request(method, path, json={"name": "x"})

    assert len(script.requests) == 1
    assert sleep.delays == []


async def test_retry_after_seconds_are_honoured(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(
        answer(429, {"detail": "slow down"}, **{"Retry-After": "3"}), answer(200, TAG_PAGE)
    )

    _ = await make_client(script).request("GET", "/api/tags/")

    assert sleep.delays == [3.0]


async def test_retry_after_in_the_past_retries_at_once(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    past = "Wed, 21 Oct 2015 07:28:00 GMT"
    script = Script(answer(503, **{"Retry-After": past}), answer(200, TAG_PAGE))

    _ = await make_client(script).request("GET", "/api/tags/")

    assert sleep.delays == [0.0]


async def test_retry_after_date_is_measured_from_the_injected_clock(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    ahead = email.utils.format_datetime(NOW + timedelta(seconds=3), usegmt=True)
    script = Script(answer(503, **{"Retry-After": ahead}), answer(200, TAG_PAGE))

    _ = await make_client(script).request("GET", "/api/tags/")

    assert sleep.delays == [3.0]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Mon, 09 Nov 2026 12:00:05 -0000", 5.0),  # parses naive: read as UTC
        ("²", None),  # isdigit() but not an int
        ("-4", 0.0),
        ("9" * 400, None),  # too large for a float
        ("", None),
    ],
)
def test_retry_after_edge_cases(value: str, expected: float | None) -> None:
    assert retry_after(value, NOW) == expected


def test_the_default_clock_is_aware_utc() -> None:
    assert utc_now().utcoffset() == timedelta(0)


@given(st.text())
def test_retry_after_never_raises(value: str) -> None:
    delay = retry_after(value, NOW)
    assert delay is None or delay >= 0.0


async def test_retry_after_beyond_the_budget_gives_up_without_waiting(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(answer(503, **{"Retry-After": "Fri, 01 Jan 2100 00:00:00 GMT"}))

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script).request("GET", "/api/tags/")

    assert sleep.delays == []


async def test_unparsable_retry_after_falls_back_to_jittered_backoff(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(answer(503, **{"Retry-After": "soon"}), answer(503), answer(200, TAG_PAGE))
    policy = RetryPolicy(attempts=3, budget=10.0, backoff=0.5)

    _ = await make_client(script, retry=policy).request("GET", "/api/tags/")

    first, second = sleep.delays
    assert 0.0 <= first <= 0.5
    assert 0.0 <= second <= 1.0


async def test_attempts_are_bounded(make_client: ClientFactory, sleep: RecordingSleep) -> None:
    script = Script(*(answer(503) for _ in range(3)))

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script, retry=RetryPolicy(attempts=3)).request("GET", "/api/tags/")

    assert len(script.requests) == 3
    assert len(sleep.delays) == 2


async def test_total_wait_stays_within_the_budget(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(*(answer(503, **{"Retry-After": "4"}) for _ in range(5)))

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script, retry=RetryPolicy(attempts=5, budget=10.0)).request(
            "GET", "/api/tags/"
        )

    assert sleep.delays == [4.0, 4.0]


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"detail": "Invalid token."}, AuthenticationFailed),
        (403, {"detail": "You do not have permission to perform this action."}, PermissionDenied),
        (404, {"detail": "No Document matches the given query."}, NotFound),
        (406, {"detail": 'Invalid version in "Accept" header.'}, UnsupportedVersion),
        (409, {"detail": "Conflict."}, Conflict),
        (500, None, UpstreamUnavailable),
        (418, {"detail": "teapot"}, PaperlessMcpError),
    ],
)
async def test_http_errors_map_to_domain_errors(
    make_client: ClientFactory, status: int, body: object, error: type[PaperlessMcpError]
) -> None:
    with pytest.raises(error) as caught:
        _ = await make_client(Script(answer(status, body))).request("GET", "/api/documents/1/")
    assert type(caught.value) is error


async def test_validation_errors_carry_sanitised_field_messages(make_client: ClientFactory) -> None:
    body = {
        "name": ["Tag with this name already exists.", "Too\nlong\x00 " + "x" * 400],
        "non_field_errors": ["Owner is not valid."],
        "custom_fields": [{"value": ["Not a valid date."]}],
    }
    client = make_client(Script(answer(400, body)))

    with pytest.raises(Validation) as caught:
        _ = await client.request("POST", "/api/tags/", json={"name": "inbox"})

    message = caught.value.message
    assert "name: Tag with this name already exists." in message
    assert "Owner is not valid." in message
    assert "custom_fields.0.value: Not a valid date." in message
    assert "\n" not in message
    assert "\x00" not in message
    assert "x" * 201 not in message


async def test_validation_without_field_messages_still_explains(make_client: ClientFactory) -> None:
    client = make_client(Script(httpx2.Response(400, json=[])))

    with pytest.raises(Validation, match="rejected the request"):
        _ = await client.request("POST", "/api/tags/", json={"name": "inbox"})


async def test_validation_messages_are_capped(make_client: ClientFactory) -> None:
    body = {f"field{n}": [f"message {n}"] for n in range(30)}
    client = make_client(Script(answer(400, body)))

    with pytest.raises(Validation) as caught:
        _ = await client.request("POST", "/api/tags/", json={"name": "inbox"})

    assert "message 9" in caught.value.message
    assert "message 10" not in caught.value.message


async def test_unreachable_slow_and_revoked_are_distinct_client_safe_errors(
    make_client: ClientFactory,
) -> None:
    caught: list[PaperlessMcpError] = []
    for handler in (
        raising(httpx2.ConnectError),
        raising(httpx2.ReadTimeout),
        Script(answer(401, {"detail": "Invalid token."})),
    ):
        with pytest.raises(PaperlessMcpError) as error:
            _ = await make_client(handler).request("GET", "/api/tags/")
        caught.append(error.value)

    unreachable, slow, revoked = caught
    assert isinstance(unreachable, UpstreamUnreachable)
    assert isinstance(slow, UpstreamTimeout)
    assert isinstance(unreachable, UpstreamUnavailable)
    assert isinstance(slow, UpstreamUnavailable)
    assert isinstance(revoked, AuthenticationFailed)
    assert len({error.message for error in caught}) == 3
    assert all(error.hint for error in caught)
    assert "simulated" not in unreachable.message + slow.message


async def test_transport_errors_are_not_retried(make_client: ClientFactory) -> None:
    handler = raising(httpx2.ConnectError)

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(handler).request("GET", "/api/tags/")

    assert len(handler.requests) == 1


async def test_uploads_use_the_upload_timeout(make_client: ClientFactory) -> None:
    script = Script(httpx2.Response(200, json="0b7c3a8e-task-id"), answer(200, TAG_PAGE))
    client = make_client(script)

    _ = await client.request(
        "POST",
        "/api/documents/post_document/",
        data={"title": "Invoice"},
        files={"document": ("invoice.pdf", b"%PDF-1.4", "application/pdf")},
    )
    _ = await client.request("GET", "/api/tags/")

    upload, other = (cast("dict[str, float]", r.extensions["timeout"]) for r in script.requests)
    assert upload == {"connect": 300.0, "read": 300.0, "write": 300.0, "pool": 300.0}
    assert other["read"] == 5.0


@pytest.mark.decision("decision-9")
async def test_token_never_appears_in_logs_errors_or_reprs(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(
        answer(503, {"detail": "busy"}),
        answer(200, TAG_PAGE),
        answer(400, {"name": ["exists"]}),
        answer(401, {"detail": "Invalid token."}),
    )
    client = make_client(script)
    errors: list[BaseException] = []

    _ = await client.request("GET", "/api/tags/")
    calls: list[tuple[Method, str]] = [("POST", "/api/tags/"), ("GET", "/api/tags/")]
    for method, path in calls:
        with pytest.raises(PaperlessMcpError) as caught:
            _ = await client.request(method, path, json={"name": "x"} if method == "POST" else None)
        errors.append(caught.value)
    for handler in (raising(httpx2.ConnectError), raising(httpx2.ReadTimeout)):
        with pytest.raises(PaperlessMcpError) as caught:
            _ = await make_client(handler).request("GET", "/api/tags/")
        errors.append(caught.value)

    provider = StaticTokenProvider(TOKEN)
    texts = [caplog.text, repr(client), repr(provider), str(provider)]
    texts += [repr(provider.current()), str(provider.current())]
    for error in errors:
        texts += [str(error), repr(error), repr(error.__cause__), str(error.__cause__)]
    assert caplog.records  # the test saw log output at all
    assert not [text for text in texts if TOKEN in text]


class Chunks(httpx2.AsyncByteStream):
    """A response body of chunks that records how many were pulled and whether it was closed."""

    def __init__(self, *chunks: bytes) -> None:
        self.chunks: tuple[bytes, ...] = chunks
        self.pulled: int = 0
        self.closed: bool = False

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.pulled += 1
            yield chunk

    @override
    async def aclose(self) -> None:
        self.closed = True


async def test_a_download_within_the_limit_is_returned_whole_with_its_headers() -> None:
    body = Chunks(b"abc", b"def")
    client, _ = streaming(
        lambda _: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body)
    )

    found = await client.download("/api/documents/1/download/", max_bytes=6)

    assert (found.content, found.truncated) == (b"abcdef", False)
    assert found.headers["Content-Type"] == "text/plain"
    assert body.closed


async def test_a_download_reports_its_status_and_does_not_follow_a_redirect() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(302, headers={"Location": "http://evil.test/api/tags/"})

    client, _ = streaming(handler)

    found = await client.download("/api/tags", max_bytes=10)

    assert found.status == 302
    assert [str(request.url) for request in seen] == ["http://paperless.test/api/tags"]


@pytest.mark.parametrize(
    "path",
    [
        "http://evil.test/api/tags/",
        "https://paperless.test/api/tags/",
        "http://paperless.test:8000/api/tags/",
        "http://paperless.test@evil.test/api/tags/",
    ],
)
async def test_a_request_to_another_origin_than_the_base_url_is_refused_unsent(path: str) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    client, _ = streaming(handler)

    with pytest.raises(
        PaperlessMcpError,
        match=r"^The request would not go to the configured Paperless and was not sent\.$",
    ):
        _ = await client.request("GET", path)
    with pytest.raises(
        PaperlessMcpError,
        match=r"^The request would not go to the configured Paperless and was not sent\.$",
    ):
        _ = await client.download(path, max_bytes=10)

    assert seen == []


async def test_a_download_stops_reading_once_the_limit_is_passed() -> None:
    body = Chunks(b"abc", b"def", b"ghi", b"jkl")
    client, _ = streaming(lambda _: httpx2.Response(200, stream=body))

    found = await client.download("/api/documents/1/download/", max_bytes=4)

    assert (found.content, found.truncated) == (b"", True)
    assert body.pulled == 2
    assert body.closed


async def test_a_download_sends_the_credential_and_the_api_version() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=b"x")

    client, _ = streaming(handler)

    _ = await client.download(
        "/api/documents/1/download/", params={"original": "true"}, max_bytes=9
    )

    [request] = seen
    assert request.headers["Accept"] == "application/json; version=10"
    assert request.headers["Authorization"] == f"Token {TOKEN}"
    assert request.url.params["original"] == "true"


async def test_a_download_error_becomes_the_domain_error() -> None:
    client, _ = streaming(lambda _: httpx2.Response(400, json={"original": ["Bad."]}))

    with pytest.raises(Validation, match="original: Bad"):
        _ = await client.download("/api/documents/1/download/", max_bytes=9)


async def test_a_download_retries_transient_statuses_and_closes_each_response() -> None:
    first = Chunks(b"busy")
    answers = [httpx2.Response(503, stream=first), httpx2.Response(200, content=b"ok")]
    client, sleep = streaming(lambda _: answers.pop(0))

    found = await client.download("/api/documents/1/download/", max_bytes=9)

    assert found.content == b"ok"
    assert first.closed
    assert len(sleep.delays) == 1


class Failing(httpx2.AsyncByteStream):
    """A body that yields some bytes and then fails like a stalled or dropped connection."""

    def __init__(self, error: type[httpx2.TransportError], *chunks: bytes) -> None:
        self.error: type[httpx2.TransportError] = error
        self.chunks: tuple[bytes, ...] = chunks
        self.closed: bool = False

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        message = "simulated"
        raise self.error(message)

    @override
    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("error", "expected"),
    [(httpx2.ReadTimeout, UpstreamTimeout), (httpx2.ReadError, UpstreamUnreachable)],
)
async def test_a_body_that_fails_midway_is_a_client_safe_error_and_is_closed(
    error: type[httpx2.TransportError], expected: type[UpstreamUnavailable]
) -> None:
    body = Failing(error, b"abc")
    client, sleep = streaming(lambda _: httpx2.Response(200, stream=body))

    with pytest.raises(expected) as raised:
        _ = await client.download("/api/documents/1/download/", max_bytes=100)

    assert raised.value.hint
    assert body.closed
    assert sleep.delays == []  # a body that failed midway is not retried


async def test_an_error_response_whose_body_fails_is_still_the_transport_error() -> None:
    body = Failing(httpx2.ReadTimeout)
    client, _ = streaming(lambda _: httpx2.Response(500, stream=body))

    with pytest.raises(UpstreamTimeout):
        _ = await client.download("/api/documents/1/download/", max_bytes=100)

    assert body.closed


# --- Mutation-testing additions (PLMCP-21)

CLIENT_LOGGER = "mcp_server_paperless.paperless.client"


async def test_form_data_and_files_reach_the_wire_through_request(
    make_client: ClientFactory,
) -> None:
    script = Script(httpx2.Response(200, json="0b7c3a8e-task-id"))

    _ = await make_client(script).request(
        "POST",
        "/api/documents/post_document/",
        data={"title": "Invoice"},
        files={"document": ("invoice.pdf", b"%PDF-1.4", "application/pdf")},
    )

    body = script.requests[0].content
    assert b'name="title"' in body
    assert b"Invoice" in body
    assert b'filename="invoice.pdf"' in body


async def test_form_data_and_files_reach_the_wire_through_call_and_a_plain_post_has_none() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=tag(1, "x"))

    client, _ = streaming(handler)

    _ = await client.call(
        "POST",
        "/api/documents/post_document/",
        TagStub,
        data={"title": "Invoice"},
        files={"document": ("invoice.pdf", b"%PDF-1.4", "application/pdf")},
    )
    _ = await client.call("POST", "/api/tags/", TagStub, json={"name": "x"})

    assert b'name="title"' in seen[0].content
    assert b'filename="invoice.pdf"' in seen[0].content
    assert seen[1].content == b'{"name":"x"}'
    assert "multipart" not in seen[1].headers["Content-Type"]


async def test_request_reads_the_whole_body_before_returning() -> None:
    body = Chunks(b'{"ok": ', b"true}")
    client, _ = streaming(lambda _: httpx2.Response(200, stream=body))

    response = await client.request("GET", "/api/tags/")

    assert response.content == b'{"ok": true}'


async def test_a_retry_after_equal_to_the_budget_is_still_waited_for(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(answer(503, **{"Retry-After": "10"}), answer(200, TAG_PAGE))

    _ = await make_client(script, retry=RetryPolicy(attempts=2, budget=10.0)).request(
        "GET", "/api/tags/"
    )

    assert sleep.delays == [10.0]


async def test_a_second_wait_that_exactly_uses_up_the_budget_is_allowed(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(
        answer(503, **{"Retry-After": "4"}),
        answer(503, **{"Retry-After": "6"}),
        answer(200, TAG_PAGE),
    )

    _ = await make_client(script, retry=RetryPolicy(attempts=3, budget=10.0)).request(
        "GET", "/api/tags/"
    )

    assert sleep.delays == [4.0, 6.0]


async def test_backoff_doubles_with_every_attempt_below_a_jittered_cap(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(answer(503), answer(503), answer(503), answer(200, TAG_PAGE))
    policy = RetryPolicy(attempts=4, budget=1000.0, backoff=2.0)

    # The injected jitter takes the cap itself: caps of 2, 4 and 8 seconds.
    _ = await make_client(script, retry=policy, jitter=lambda cap: cap).request("GET", "/api/tags/")

    assert sleep.delays == [2.0, 4.0, 8.0]


async def test_the_default_jitter_is_a_random_wait_between_zero_and_the_cap(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    script = Script(*(answer(503) for _ in range(12)), answer(200, TAG_PAGE))
    policy = RetryPolicy(attempts=13, budget=1e9, backoff=1.0)

    _ = await make_client(script, retry=policy).request("GET", "/api/tags/")

    assert all(0.0 <= delay <= 2.0**n for n, delay in enumerate(sleep.delays))
    assert len(set(sleep.delays)) > 1


async def test_retries_and_attempts_are_logged_with_method_path_and_delay(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=CLIENT_LOGGER)
    script = Script(answer(503, **{"Retry-After": "2"}), answer(200, TAG_PAGE))

    _ = await make_client(script).request("GET", "/api/tags/")

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.DEBUG, "GET /api/tags/ -> 503 (attempt 1)"),
        (logging.WARNING, "Paperless answered 503 to GET /api/tags/; retrying in 2.0s"),
        (logging.DEBUG, "GET /api/tags/ -> 200 (attempt 2)"),
    ]


async def test_a_failed_request_is_logged_with_the_error_type_only(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=CLIENT_LOGGER)

    with pytest.raises(UpstreamTimeout):
        _ = await make_client(raising(httpx2.ReadTimeout)).request("GET", "/api/tags/")

    assert [r.getMessage() for r in caplog.records] == ["GET /api/tags/ failed: ReadTimeout"]


async def test_a_download_that_breaks_midway_is_logged_with_path_and_error_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=CLIENT_LOGGER)
    client, _ = streaming(lambda _: httpx2.Response(200, stream=Failing(httpx2.ReadError, b"a")))

    with pytest.raises(UpstreamUnreachable):
        _ = await client.download("/api/documents/1/download/", max_bytes=100)

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert messages == ["GET /api/documents/1/download/ failed: ReadError"]


# --- PLMCP-20: a bounded streaming POST (the Paperless AI chat) and per-request timeouts


class Hanging(httpx2.AsyncByteStream):
    """A body that yields some bytes and then never sends another one."""

    def __init__(self, *chunks: bytes) -> None:
        self.chunks: tuple[bytes, ...] = chunks
        self.closed: bool = False

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        _ = await asyncio.Event().wait()

    @override
    async def aclose(self) -> None:
        self.closed = True


async def test_a_streamed_post_returns_the_whole_body_within_the_limits() -> None:
    body = Chunks(b"abc", b"def")
    client, _ = streaming(lambda _: httpx2.Response(200, stream=body))

    found = await client.stream_post("/api/documents/chat/", json={}, max_bytes=6, max_seconds=5)

    assert (found.content, found.truncated) == (b"abcdef", False)
    assert body.closed


async def test_a_streamed_post_sends_the_json_the_credential_and_the_time_limit() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=b"x")

    client, _ = streaming(handler)

    _ = await client.stream_post(
        "/api/documents/chat/", json={"q": "why?"}, max_bytes=9, max_seconds=7
    )

    [request] = seen
    assert (request.method, request.url.path) == ("POST", "/api/documents/chat/")
    assert request.content == b'{"q":"why?"}'
    assert request.headers["Accept"] == "application/json; version=10"
    assert request.headers["Authorization"] == f"Token {TOKEN}"
    assert cast("dict[str, float]", request.extensions["timeout"])["read"] == 7


async def test_a_streamed_post_keeps_what_fits_and_stops_reading_at_the_size_limit() -> None:
    body = Chunks(b"abc", b"def", b"ghi", b"jkl")
    client, _ = streaming(lambda _: httpx2.Response(200, stream=body))

    found = await client.stream_post("/api/documents/chat/", json={}, max_bytes=4, max_seconds=5)

    assert (found.content, found.truncated) == (b"abcd", True)
    assert body.pulled == 2
    assert body.closed


async def test_a_body_of_exactly_the_size_limit_is_not_truncated() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200, stream=Chunks(b"ab", b"cd")))

    found = await client.stream_post("/api/documents/chat/", json={}, max_bytes=4, max_seconds=5)

    assert (found.content, found.truncated) == (b"abcd", False)


async def test_a_streamed_post_that_runs_out_of_time_keeps_what_arrived() -> None:
    body = Hanging(b"abc")
    client, _ = streaming(lambda _: httpx2.Response(200, stream=body))

    found = await client.stream_post(
        "/api/documents/chat/", json={}, max_bytes=100, max_seconds=0.05
    )

    assert (found.content, found.truncated) == (b"abc", True)
    assert body.closed


async def test_a_streamed_post_that_runs_out_of_time_before_any_byte_is_a_timeout() -> None:
    body = Hanging()
    client, _ = streaming(lambda _: httpx2.Response(200, stream=body))

    with pytest.raises(UpstreamTimeout) as raised:
        _ = await client.stream_post(
            "/api/documents/chat/", json={}, max_bytes=100, max_seconds=0.05
        )

    assert raised.value.hint
    assert body.closed


async def test_a_streamed_post_error_becomes_the_domain_error_and_is_not_retried() -> None:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(503, json={"detail": "busy"})

    client, sleep = streaming(handler)

    with pytest.raises(UpstreamUnavailable, match="HTTP 503"):
        _ = await client.stream_post("/api/documents/chat/", json={}, max_bytes=9, max_seconds=5)

    assert len(sent) == 1
    assert sleep.delays == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [(httpx2.ReadTimeout, UpstreamTimeout), (httpx2.ReadError, UpstreamUnreachable)],
)
async def test_a_streamed_post_whose_body_fails_midway_is_a_client_safe_error(
    error: type[httpx2.TransportError],
    expected: type[UpstreamUnavailable],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=CLIENT_LOGGER)
    body = Failing(error, b"abc")
    client, _ = streaming(lambda _: httpx2.Response(200, stream=body))

    with pytest.raises(expected) as raised:
        _ = await client.stream_post("/api/documents/chat/", json={}, max_bytes=100, max_seconds=5)

    assert raised.value.hint
    assert body.closed
    assert [r.getMessage() for r in caplog.records] == [
        f"POST /api/documents/chat/ failed: {error.__name__}"
    ]


async def test_a_request_can_carry_its_own_timeout(make_client: ClientFactory) -> None:
    script = Script(answer(200, tag(1, "inbox")), answer(200, tag(2, "other")))
    client = make_client(script)

    _ = await client.get("/api/tags/1/", TagStub, timeout=77)
    _ = await client.get("/api/tags/2/", TagStub)

    first, second = (cast("dict[str, float]", r.extensions["timeout"]) for r in script.requests)
    assert first["read"] == 77
    assert second["read"] != 77


# --- PLMCP-20 mutation-testing additions: what a streamed POST reports exactly


async def test_a_streamed_error_body_is_read_before_it_is_mapped_and_closed() -> None:
    body = Chunks(b"AI is required ", b"for this feature")
    client, _ = streaming(lambda _: httpx2.Response(400, stream=body))

    with pytest.raises(FeatureUnavailable, match="not enabled"):
        _ = await client.stream_post("/api/documents/chat/", json={}, max_bytes=9, max_seconds=5)

    assert body.closed


async def test_the_size_limit_counts_every_chunk_not_just_the_last() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200, stream=Chunks(b"ab", b"cd", b"ef")))

    found = await client.stream_post("/api/documents/chat/", json={}, max_bytes=5, max_seconds=5)

    assert (found.content, found.truncated) == (b"abcde", True)


async def test_running_out_of_time_without_a_byte_says_so_with_a_hint() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200, stream=Hanging()))

    with pytest.raises(UpstreamTimeout) as raised:
        _ = await client.stream_post("/api/documents/chat/", json={}, max_bytes=9, max_seconds=0.05)

    assert raised.value.message == "Paperless did not respond in time."
    assert raised.value.hint == "Try again in a moment."


async def test_the_time_limit_covers_the_wait_for_the_response_itself() -> None:
    async def never() -> None:
        _ = await asyncio.Event().wait()

    class Slow(httpx2.AsyncBaseTransport):
        @override
        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            await never()
            raise AssertionError

    http = httpx2.AsyncClient(base_url="http://paperless.test", transport=Slow())
    client = PaperlessClient(
        http, StaticTokenProvider(TOKEN), retry=RetryPolicy(), upload_timeout=1.0
    )

    with pytest.raises(UpstreamTimeout):
        _ = await client.stream_post("/api/documents/chat/", json={}, max_bytes=9, max_seconds=0.05)
    await http.aclose()


# --- PLMCP-20 review: an explicit opt-out of retries


async def test_a_request_can_opt_out_of_retries(make_client: ClientFactory) -> None:
    script = Script(answer(503, {"detail": "busy"}), answer(200, tag(1, "inbox")))

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script).request("GET", "/api/tags/1/", retry=False)

    assert len(script.requests) == 1


async def test_get_and_call_pass_the_opt_out_on(make_client: ClientFactory) -> None:
    script = Script(answer(503, {"detail": "busy"}), answer(200, tag(1, "inbox")))

    with pytest.raises(UpstreamUnavailable):
        _ = await make_client(script).get("/api/tags/1/", TagStub, retry=False)

    assert len(script.requests) == 1


async def test_retries_stay_the_default(make_client: ClientFactory) -> None:
    script = Script(answer(503, {"detail": "busy"}), answer(200, tag(1, "inbox")))

    _ = await make_client(script).get("/api/tags/1/", TagStub)

    assert len(script.requests) == 2


async def test_call_retries_by_default(make_client: ClientFactory) -> None:
    script = Script(answer(503, {"detail": "busy"}), answer(200, tag(1, "inbox")))

    _ = await make_client(script).call("GET", "/api/tags/1/", TagStub)

    assert len(script.requests) == 2


# --- Review findings: userinfo, base path prefix, retry switch, capped error bodies --------------


def client_on(
    base_url: str, handler: Callable[[httpx2.Request], httpx2.Response]
) -> PaperlessClient:
    http = httpx2.AsyncClient(base_url=base_url, transport=httpx2.MockTransport(handler))
    return PaperlessClient(
        http,
        StaticTokenProvider(TOKEN),
        retry=RetryPolicy(),
        upload_timeout=1.0,
        sleep=asyncio.sleep,
    )


async def test_a_base_url_with_userinfo_still_works() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    client = client_on("http://user:pw@paperless.test", handler)

    _ = await client.request("GET", "/api/tags/")

    assert [request.url.path for request in seen] == ["/api/tags/"]


async def test_userinfo_that_is_not_the_base_urls_is_refused() -> None:
    client = client_on("http://user:pw@paperless.test", lambda _: httpx2.Response(200, json={}))

    with pytest.raises(PaperlessMcpError, match="configured Paperless"):
        _ = await client.request("GET", "http://other:x@paperless.test/api/tags/")


async def test_a_base_path_prefix_is_kept_and_cannot_be_escaped() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    client = client_on("http://paperless.test/paperless/", handler)

    _ = await client.request("GET", "/api/tags/")
    for path in ("/api/../../x", "/../x"):
        with pytest.raises(PaperlessMcpError, match="configured Paperless"):
            _ = await client.request("GET", path)

    assert [request.url.path for request in seen] == ["/paperless/api/tags/"]


async def test_a_download_without_retry_asks_once_on_a_transient_status() -> None:
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(504)

    client, _ = streaming(handler)

    with pytest.raises(UpstreamUnavailable):
        _ = await client.download("/api/tags/", max_bytes=10, retry=False)
    assert calls == 1
    with pytest.raises(UpstreamUnavailable):
        _ = await client.download("/api/tags/", max_bytes=10)
    assert calls == 4


async def test_an_error_body_is_read_only_up_to_a_limit() -> None:
    body = Chunks(*([b"x" * 10_000] * 100))
    client, _ = streaming(lambda _: httpx2.Response(500, stream=body))

    with pytest.raises(UpstreamUnavailable):
        _ = await client.download("/api/tags/", max_bytes=10, retry=False)

    assert body.pulled < 10
    assert body.closed


async def test_an_error_body_that_is_read_still_gives_its_details() -> None:
    client, _ = streaming(lambda _: httpx2.Response(400, json={"name": ["is taken"]}))

    with pytest.raises(Validation, match="name: is taken"):
        _ = await client.download("/api/tags/", max_bytes=10)


async def test_an_error_body_of_exactly_the_limit_stops_the_read() -> None:
    body = Chunks(b" " * ERROR_BODY_LIMIT, b"more", b"more")
    client, _ = streaming(lambda _: httpx2.Response(500, stream=body))

    with pytest.raises(UpstreamUnavailable):
        _ = await client.download("/api/tags/", max_bytes=10, retry=False)

    assert body.pulled == 1


async def test_the_details_in_an_error_body_longer_than_the_limit_are_still_read() -> None:
    text = b'{"name": ["too big"]}' + b" " * (ERROR_BODY_LIMIT + 5000)
    client, _ = streaming(lambda _: httpx2.Response(400, content=text))

    with pytest.raises(Validation, match="name: too big"):
        _ = await client.download("/api/tags/", max_bytes=10)


async def test_the_target_url_is_the_base_url_with_the_path_and_the_query() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200))

    assert str(client.target("/api/tags/", {"page_size": 2})) == (
        "http://paperless.test/api/tags/?page_size=2"
    )
    assert str(client.target("/api/tags/")) == "http://paperless.test/api/tags/"
    assert str(client.base_url) == "http://paperless.test"


# --- PLMCP-76: a body handed over chunk by chunk


async def read_all(client: PaperlessClient) -> bytes:
    async with client.stream("GET", "/api/documents/1/download/") as response:
        return b"".join([chunk async for chunk in response.aiter_bytes()])


async def test_a_stream_hands_over_the_chunks_and_is_closed_afterwards() -> None:
    body = Chunks(b"abc", b"def", b"ghi")
    client, _ = streaming(
        lambda _: httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=body)
    )

    async with client.stream("GET", "/api/documents/1/download/") as response:
        assert response.headers["Content-Type"] == "text/plain"
        first = await anext(response.aiter_bytes())
        assert (first, body.pulled, body.closed) == (b"abc", 1, False)

    assert (body.pulled, body.closed) == (1, True)


async def test_a_stream_sends_the_method_the_body_the_credential_and_its_timeout() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=b"x")

    client, _ = streaming(handler)
    async with client.stream(
        "POST", "/api/documents/bulk_download/", json={"documents": [1]}, timeout=600.0
    ):
        pass
    async with client.stream("GET", "/api/documents/1/download/", params={"original": "true"}):
        pass

    posted, got = seen
    assert (posted.method, posted.content) == ("POST", b'{"documents":[1]}')
    timeouts = cast("dict[str, float]", posted.extensions["timeout"])
    assert timeouts["read"] == 600.0
    assert posted.headers["Authorization"] == f"Token {TOKEN}"
    assert posted.headers["Accept"] == "application/json; version=10"
    assert got.url.params["original"] == "true"
    assert cast("dict[str, float]", got.extensions["timeout"])["read"] != 600.0


async def test_an_unexpected_answer_to_a_streamed_post_is_no_write_that_may_have_been_applied() -> (
    None
):
    client, _ = streaming(lambda _: httpx2.Response(204))

    with pytest.raises(PaperlessMcpError) as raised:
        async with client.stream("POST", "/api/documents/bulk_download/"):
            raise AssertionError

    assert raised.value.hint is None


async def test_a_stream_that_is_refused_is_the_domain_error_and_closed() -> None:
    body = Chunks(b'{"original": ["Bad."]}')
    client, _ = streaming(lambda _: httpx2.Response(400, stream=body))

    with pytest.raises(Validation, match="original: Bad"):
        _ = await read_all(client)

    assert body.closed


async def test_a_stream_reads_no_more_of_an_error_than_its_limit() -> None:
    body = Chunks(*[b"x" * ERROR_BODY_LIMIT] * 3)
    client, _ = streaming(lambda _: httpx2.Response(500, stream=body))

    with pytest.raises(UpstreamUnavailable):
        _ = await read_all(client)

    assert (body.pulled, body.closed) == (1, True)


@pytest.mark.parametrize("status", [204, 206, 302])
async def test_a_stream_takes_a_whole_file_only_and_follows_no_redirect(status: int) -> None:
    headers = {"Location": "http://evil.test/"}
    client, _ = streaming(lambda _: httpx2.Response(status, headers=headers))

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await read_all(client)

    assert raised.value.message == (
        "Paperless sent an unexpected response for GET /api/documents/1/download/."
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [(httpx2.ReadTimeout, UpstreamTimeout), (httpx2.RemoteProtocolError, UpstreamUnreachable)],
)
async def test_a_stream_that_breaks_midway_is_an_error_and_never_a_short_file(
    error: type[httpx2.TransportError],
    expected: type[UpstreamUnavailable],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=CLIENT_LOGGER)
    body = Failing(error, b"half")
    client, sleep = streaming(lambda _: httpx2.Response(200, stream=body))

    with pytest.raises(expected):
        _ = await read_all(client)

    assert (body.closed, sleep.delays) == (True, [])
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == [
        f"GET /api/documents/1/download/ failed: {error.__name__}"
    ]


async def test_a_stream_that_cannot_be_decompressed_is_a_client_safe_error() -> None:
    body = Chunks(b"not gzip at all")
    client, _ = streaming(
        lambda _: httpx2.Response(200, headers={"Content-Encoding": "gzip"}, stream=body)
    )

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await read_all(client)

    assert raised.value.message == (
        "Paperless sent an unexpected response for GET /api/documents/1/download/."
    )
    assert body.closed


@pytest.mark.parametrize(("method", "asked"), [("GET", 2), ("POST", 1)])
async def test_only_a_get_stream_is_retried_and_only_before_its_body(
    method: Method, asked: int
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(503 if len(seen) == 1 else 200, content=b"ok")

    client, _ = streaming(handler)
    try:
        async with client.stream(method, "/api/documents/bulk_download/") as response:
            assert await response.aread() == b"ok"
    except UpstreamUnavailable:
        assert method == "POST"

    assert len(seen) == asked


async def test_an_error_of_the_reader_passes_through_a_stream_and_closes_it() -> None:
    body = Chunks(b"abc")
    client, _ = streaming(lambda _: httpx2.Response(200, stream=body))

    error = Validation("mine")
    with pytest.raises(Validation, match="mine"):
        async with client.stream("GET", "/api/documents/1/download/"):
            raise error

    assert body.closed


OLD_RELEASE = (
    "Paperless 3.4.2 is older than the minimum release 3.5.1: Paperless must be updated; until "
    "then some tools fail or answer differently"
)


@pytest.mark.decision("decision-57")
async def test_an_old_release_is_logged_once_by_the_first_answer_that_reports_it(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=CLIENT_LOGGER)
    old = answer(200, TAG_PAGE, **{"X-Version": "3.4.2"})
    client = make_client(Script(answer(200, TAG_PAGE), old, old))

    _ = await client.request("GET", "/api/tags/")
    assert caplog.records == []
    _ = await client.request("GET", "/api/tags/")
    _ = await client.request("GET", "/api/tags/")

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(logging.WARNING, OLD_RELEASE)]


@pytest.mark.decision("decision-57")
async def test_an_old_release_is_logged_when_it_is_a_stream_or_an_error_that_reports_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    for status in (200, 406):
        caplog.clear()
        client, _ = streaming(
            lambda _, status=status: httpx2.Response(status, headers={"X-Version": "3.4.2"})
        )
        with contextlib.suppress(UnsupportedVersion):
            _ = await client.download("/api/documents/1/download/", max_bytes=100)

        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == [
            OLD_RELEASE
        ]


@pytest.mark.decision("decision-57")
@pytest.mark.parametrize("version", [None, "", "dev", "3.4", "3.5.1", "3.6.0"])
async def test_any_other_version_is_not_logged(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture, version: str | None
) -> None:
    caplog.set_level(logging.WARNING, logger=CLIENT_LOGGER)
    headers = {} if version is None else {"X-Version": version}

    _ = await make_client(Script(answer(200, TAG_PAGE, **headers))).request("GET", "/api/tags/")

    assert caplog.records == []
