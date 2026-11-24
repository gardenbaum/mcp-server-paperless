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
from mcp_server_paperless.domain.rendering import (
    MAX_IMAGE_BYTES,
    ContactSheet,
    Overview,
    PageImage,
    PageRun,
    PageShape,
    Rendered,
)

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult

    from mcp_server_paperless.domain.rendering import OverviewRequest, PageRenderer, RenderRequest

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

    async def inspect(self, content: bytes, request: OverviewRequest) -> Overview:
        assert (content, request.sheets) == (b"x", True)
        size = MAX_IMAGE_BYTES // 5
        sheets = tuple(
            ContactSheet(
                pages=tuple(range(n * 12 + 1, n * 12 + 13)),
                width=1568,
                height=1176,
                data=os.urandom(size),
            )
            for n in range(5)
        )
        # The longest report: as many runs as there can be, of pages none next to another.
        runs = tuple(
            PageRun(
                pages=tuple(range(100_001 + n, 200_000, 20_000)),
                shape=PageShape(width_mm=1_234_567.8, height_mm=1_234_567.8, rotation=270),
            )
            for n in range(100)
        )
        rest = tuple(range(61, 200, 2))
        return Overview(page_count=200, runs=runs, layout_rest=rest, sheets=sheets, rest=rest)


def reader(original: bytes, mime_type: str) -> FakePaperless:
    state = seeded()
    state.documents.append(FakeDocument(id=7, title="Scan", original=original, mime_type=mime_type))
    return FakePaperless(state, user="admin")


async def over_http(
    fake: FakePaperless,
    renderer: PageRenderer | None,
    mode: str,
    arguments: dict[str, object],
    tool: str = "get_document_pages",
) -> CallToolResult:
    app = build_app(
        serve(fake, renderer=renderer, toolsets=["core", "pdf"]),
        http_settings(with_token=False),
        max_upload_bytes=DEFAULT,
    )
    with listening(app) as url:
        async with Client(StreamableHttpTransport(url), mode=mode) as client:
            return await client.call_tool(tool, {"document_id": 7, **arguments})


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


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_the_sheets_of_a_call_fit_into_an_event_of_the_mcp_client(mode: str) -> None:
    result = await over_http(reader(b"x", "application/pdf"), Full(5), mode, {}, "inspect_pdf")

    assert [len(image) for image in images(result)] == [MAX_IMAGE_BYTES // 5] * 5


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
server = serve(FakePaperless(state, user="admin"), stdio=True, toolsets=["core", "pdf"])
server.run(transport="stdio", show_banner=False)
"""


async def test_pages_are_shown_over_stdio_and_the_stream_stays_clean() -> None:
    tests = os.path.dirname(os.path.dirname(__file__))  # noqa: PTH120 -- a string for sys.path
    transport = StdioTransport(
        sys.executable, ["-c", STDIO.format(tests=tests)], env={}, keep_alive=False
    )

    async with Client(transport) as client:
        first = await client.call_tool("get_document_pages", {"document_id": 7})
        second = await client.call_tool("get_document_pages", {"document_id": 7, "size": 256})

        sheet = await client.call_tool("inspect_pdf", {"document_id": 7})

    assert len(images(first)) == len(images(second)) == len(images(sheet)) == 1


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
