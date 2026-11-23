"""get_document_pages over both transports, with the real child process (PLMCP-74, AC 6)."""

import base64
import os
import sys
from hashlib import shake_256
from typing import TYPE_CHECKING

import pytest
from fakes.paperless import FakeDocument, FakePaperless, seeded
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from harness import TOKEN, refused_url, serve
from mcp.types import ImageContent
from PIL import Image
from rendering.files import saved
from test_http_uploads import DEFAULT, http_settings, listening

from mcp_server_paperless.app import build_app
from mcp_server_paperless.domain.rendering import MAX_IMAGE_BYTES, PageImage, Rendered

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult

    from mcp_server_paperless.domain.rendering import PageRenderer, RenderRequest

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-42")]


class Full:
    """Answers images of exactly the bytes of a call, in `parts` images."""

    def __init__(self, parts: int) -> None:
        self._parts: int = parts

    async def render(self, content: bytes, request: RenderRequest) -> Rendered:
        assert (content, request.pages) == (b"x", "1")
        sizes = [MAX_IMAGE_BYTES // self._parts] * self._parts
        sizes[0] += MAX_IMAGE_BYTES - sum(sizes)
        images = tuple(
            PageImage(page=n, width=2576, height=2576, data=os.urandom(size))
            for n, size in enumerate(sizes, start=1)
        )
        # The longest report: every page that does not fit, none next to another.
        rest = tuple(range(6, 200, 2))
        return Rendered(page_count=200, images=images, rest=rest)


def reader(original: bytes, mime_type: str) -> FakePaperless:
    state = seeded()
    state.documents.append(FakeDocument(id=7, title="Scan", original=original, mime_type=mime_type))
    return FakePaperless(state, user="admin")


async def over_http(
    fake: FakePaperless, renderer: PageRenderer | None, mode: str, arguments: dict[str, object]
) -> CallToolResult:
    app = build_app(
        serve(fake, renderer=renderer), http_settings(with_token=False), max_upload_bytes=DEFAULT
    )
    with listening(app) as url:
        async with Client(StreamableHttpTransport(url), mode=mode) as client:
            return await client.call_tool("get_document_pages", {"document_id": 7, **arguments})


def images(result: CallToolResult) -> list[bytes]:
    return [base64.b64decode(b.data) for b in result.content if isinstance(b, ImageContent)]


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("parts", [1, 5])
async def test_the_bytes_of_a_call_fit_into_an_event_of_the_mcp_client(
    parts: int, mode: str
) -> None:
    result = await over_http(reader(b"x", "image/png"), Full(parts), mode, {})

    found = images(result)
    assert (len(found), sum(map(len, found))) == (parts, MAX_IMAGE_BYTES)


async def test_a_page_that_hardly_compresses_fits_over_http_and_says_it_is_reduced() -> None:
    noise = Image.frombytes("RGB", (1000, 1000), shake_256(b"pages").digest(3_000_000))
    fake = reader(saved(noise, "PNG"), "image/png")

    result = await over_http(fake, None, "legacy", {"size": 2576})

    [image] = images(result)
    assert len(image) <= MAX_IMAGE_BYTES
    assert '"reduced":true' in str(result.content[0])


# The server over stdio with a fake Paperless behind it: the child must not touch its streams.
STDIO = """
import sys
sys.path[:0] = [{tests!r}]
from fakes.paperless import FakeDocument, FakePaperless, seeded
from harness import serve
from rendering.files import QUADRANTS, pdf_of
state = seeded()
state.documents.append(FakeDocument(id=7, title="Scan", original=pdf_of([QUADRANTS])))
serve(FakePaperless(state, user="admin"), stdio=True).run(transport="stdio", show_banner=False)
"""


async def test_pages_are_shown_over_stdio_and_the_stream_stays_clean() -> None:
    tests = os.path.dirname(os.path.dirname(__file__))  # noqa: PTH120 -- a string for sys.path
    transport = StdioTransport(
        sys.executable, ["-c", STDIO.format(tests=tests)], env={}, keep_alive=False
    )

    async with Client(transport) as client:
        first = await client.call_tool("get_document_pages", {"document_id": 7})
        second = await client.call_tool("get_document_pages", {"document_id": 7, "size": 256})

    assert len(images(first)) == len(images(second)) == 1


async def test_the_console_script_offers_the_tool_and_reports_an_unreachable_paperless() -> None:
    transport = StdioTransport(
        sys.executable,
        ["-m", "mcp_server_paperless"],
        env={"PAPERLESS_URL": refused_url(), "PAPERLESS_TOKEN": TOKEN},
        keep_alive=False,
    )

    async with Client(transport) as client:
        names = {tool.name for tool in await client.list_tools()}
        result = await client.call_tool(
            "get_document_pages", {"document_id": 7}, raise_on_error=False
        )

    assert "get_document_pages" in names
    assert result.is_error
