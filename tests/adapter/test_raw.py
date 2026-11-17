"""PaperlessRawReader: one GET to the configured Paperless, never anywhere else (decision-25)."""

import logging
from typing import TYPE_CHECKING, override

import httpx2
import pytest

from adapter.conftest import BASE_URL, TOKEN, streaming, tag
from paperless_mcp.domain.errors import NotFound, PaperlessMcpError, PermissionDenied, Validation
from paperless_mcp.observability import QUIET_LOGGERS
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.paperless.client import PaperlessClient, RetryPolicy
from paperless_mcp.paperless.raw import PaperlessRawReader

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory
    from paperless_mcp.paperless.client import Params

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-25")]

PAGE = {"count": 1, "next": None, "previous": None, "results": [tag(1, "inbox")]}


async def test_a_get_returns_status_type_and_body_with_the_credential_sent(
    make_client: ClientFactory,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=PAGE)

    reply = await PaperlessRawReader(make_client(handler)).get(
        "/api/tags/", {"page_size": "2"}, max_bytes=100_000
    )

    assert (reply.status, reply.content_type, reply.truncated) == (200, "application/json", False)
    assert b'"inbox"' in reply.body
    [request] = seen
    assert str(request.url) == f"{BASE_URL}/api/tags/?page_size=2"
    assert request.method == "GET"
    assert request.headers["Authorization"] == f"Token {TOKEN}"
    assert request.headers["Accept"] == "application/json; version=10"


async def test_a_body_over_the_limit_is_not_kept(make_client: ClientFactory) -> None:
    reader = PaperlessRawReader(make_client(lambda _: httpx2.Response(200, json=PAGE)))

    reply = await reader.get("/api/tags/", {}, max_bytes=10)

    assert (reply.body, reply.truncated) == (b"", True)


async def test_an_empty_answer_has_no_content_type() -> None:
    client, _ = streaming(lambda _: httpx2.Response(204))

    reply = await PaperlessRawReader(client).get("/api/tags/1/", {}, max_bytes=10)

    assert (reply.status, reply.content_type, reply.body) == (204, "", b"")


@pytest.mark.parametrize(
    ("status", "error"), [(404, NotFound), (403, PermissionDenied), (400, Validation)]
)
async def test_error_statuses_become_domain_errors(status: int, error: type[Exception]) -> None:
    client, _ = streaming(lambda _: httpx2.Response(status, json={"detail": "x"}))

    with pytest.raises(error):
        _ = await PaperlessRawReader(client).get("/api/tags/", {}, max_bytes=10)


async def test_a_redirect_is_not_followed_and_does_not_reveal_its_target() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(301, headers={"Location": "http://evil.test/api/tags/"})

    client, _ = streaming(handler)

    with pytest.raises(PaperlessMcpError) as refused:
        _ = await PaperlessRawReader(client).get("/api/tags", {}, max_bytes=10)

    assert len(seen) == 1
    assert "evil" not in refused.value.describe()
    assert "trailing slash" in refused.value.describe()


HOSTILE = (
    "http://evil.test/api/tags/",
    "https://paperless.test/api/tags/",
    "//evil.test/api/tags/",
    "/apix/tags/",
    "/admin/",
    "api/tags/",
    "/api/../admin/",
    "/api/tags/../../admin/",
    "/api/%2e%2e/admin/",
    "/api/tags/%2f",
    "/api\\tags/",
    "/api/tags/?x=1",
    "/api/tags/#x",
    "/api/tags@evil.test/",
    "/api/tags/\r\n",
    "/api/tags/\x00",
    "/api//tags/",
)


@pytest.mark.parametrize("path", HOSTILE)
async def test_the_adapter_refuses_a_hostile_path_itself_without_sending(path: str) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    client, _ = streaming(handler)

    with pytest.raises(Validation):
        _ = await PaperlessRawReader(client).get(path, {}, max_bytes=10)

    assert seen == []


async def test_the_query_text_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    # What configure_logging does in the running server: httpx2 logs URLs with their query.
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    client, _ = streaming(lambda _: httpx2.Response(200, json={}))

    _ = await PaperlessRawReader(client).get(
        "/api/documents/", {"query": "SECRET-TEXT"}, max_bytes=9
    )

    assert "SECRET-TEXT" not in caplog.text


async def test_the_adapter_refusal_and_the_redirect_error_have_exact_words() -> None:
    client, _ = streaming(lambda _: httpx2.Response(301, headers={"Location": "/x"}))
    reader = PaperlessRawReader(client)

    with pytest.raises(Validation) as refused:
        _ = await reader.get("/admin/", {}, max_bytes=10)
    assert refused.value.message == "The adapter refuses a path that is not plain and under /api/."
    with pytest.raises(PaperlessMcpError) as redirected:
        _ = await reader.get("/api/tags", {}, max_bytes=10)
    assert redirected.value.message == (
        "Paperless redirected the request; redirects are not followed."
    )
    assert redirected.value.hint == (
        "The path is probably wrong or lacks its trailing slash; "
        "ask for an exact path such as /api/tags/."
    )


async def test_the_adapter_never_retries() -> None:
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(504)

    client, _ = streaming(handler)

    with pytest.raises(PaperlessMcpError):
        _ = await PaperlessRawReader(client).get("/api/tags/", {}, max_bytes=10)

    assert calls == 1


async def test_a_base_url_with_a_path_prefix_gets_the_path_below_it() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    http = httpx2.AsyncClient(
        base_url="http://paperless.test/paperless/", transport=httpx2.MockTransport(handler)
    )
    client = PaperlessClient(
        http, StaticTokenProvider(TOKEN), retry=RetryPolicy(), upload_timeout=1.0
    )

    _ = await PaperlessRawReader(client).get("/api/tags/", {}, max_bytes=10)

    assert [request.url.path for request in seen] == ["/paperless/api/tags/"]


class Misrouting(PaperlessClient):
    """A client whose URL building went wrong: it says where it would go, and that is elsewhere."""

    @override
    def target(self, path: str, params: Params | None = None) -> httpx2.URL:
        return httpx2.URL("http://paperless.test/other/")


async def test_the_adapter_checks_the_built_url_against_the_base_path_and_the_path() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    http = httpx2.AsyncClient(base_url=BASE_URL, transport=httpx2.MockTransport(handler))
    client = Misrouting(http, StaticTokenProvider(TOKEN), retry=RetryPolicy(), upload_timeout=1.0)

    with pytest.raises(
        Validation, match=r"^The built URL is not the configured Paperless base and the path\.$"
    ):
        _ = await PaperlessRawReader(client).get("/api/tags/", {}, max_bytes=10)

    assert seen == []
