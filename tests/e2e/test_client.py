"""Smoke tests of the Paperless client against the real Paperless stack."""

from typing import TYPE_CHECKING, override

import httpx2
import pytest
from pydantic import BaseModel

from mcp_server_paperless.domain.errors import AuthenticationFailed, NotFound
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy, opened
from mcp_server_paperless.paperless.pagination import Page, collect

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]


class TagName(BaseModel):
    id: int
    name: str


def paperless(http: httpx2.AsyncClient, token: str) -> PaperlessClient:
    return PaperlessClient(
        http, StaticTokenProvider(token), retry=RetryPolicy(), upload_timeout=60.0
    )


async def test_pages_and_collection_of_real_tags(stack: Stack) -> None:
    async with httpx2.AsyncClient(base_url=stack.base_url, timeout=30.0) as http:
        client = paperless(http, stack.admin_token)
        page = await client.get("/api/tags/", Page[TagName], params={"page": 2, "page_size": 1})
        collected = await collect(client, "/api/tags/", Page[TagName], cap=1000)

    assert (page.previous_page, page.next_page) == (1, 3)
    names = {tag.name for tag in collected.items}
    assert names >= {"e2e-finance", "e2e-invoices", "e2e-private"}
    assert (collected.count, collected.truncated) == (len(collected.items), False)


async def test_a_bad_token_is_an_authentication_error(stack: Stack) -> None:
    async with httpx2.AsyncClient(base_url=stack.base_url, timeout=30.0) as http:
        with pytest.raises(AuthenticationFailed):
            _ = await paperless(http, "not-a-valid-token").request("GET", "/api/tags/")


class Asking(httpx2.AsyncHTTPTransport):
    """Asks for one encoding and notes in which one the answers come."""

    def __init__(self, encoding: str) -> None:
        super().__init__()
        self.encoding: str = encoding
        self.seen: list[str] = []

    @override
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        request.headers["Accept-Encoding"] = self.encoding
        response = await super().handle_async_request(request)
        self.seen.append(response.headers.get("Content-Encoding", "identity"))
        return response


@pytest.mark.parametrize("encoding", ["identity", "gzip", "zstd"])
async def test_files_and_errors_are_read_however_paperless_compresses_them(
    stack: Stack, encoding: str
) -> None:
    number = stack.seeded.public_document
    path = f"/api/documents/{number}/download/"
    plain = stack.admin.get(path).raise_for_status().content
    asking = Asking(encoding)
    async with httpx2.AsyncClient(base_url=stack.base_url, timeout=30.0, transport=asking) as http:
        client = paperless(http, stack.admin_token)
        whole = await client.download(path, max_bytes=len(plain))
        async with client.stream("GET", path) as response:
            streamed = b"".join([chunk async for chunk in opened(response).chunks])
        archive = await client.stream_post(
            "/api/documents/bulk_download/",
            json={"documents": [number], "content": "originals", "compression": "none"},
            max_bytes=10_000_000,
            max_seconds=60,
        )
        with pytest.raises(NotFound):
            _ = await client.download("/api/documents/99999999/download/", max_bytes=10)

    assert asking.seen[:2] == [encoding] * 2
    assert (whole.content, whole.truncated) == (plain, False)
    assert streamed == plain
    assert archive.content.startswith(b"PK")
    assert not archive.truncated
