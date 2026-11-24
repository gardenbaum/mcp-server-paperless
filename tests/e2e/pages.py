"""What the live tests of get_document_pages share."""

import asyncio
import base64
import contextlib
import json
import re
import uuid
from typing import TYPE_CHECKING, cast

from fastmcp import Client
from harness import paperless_settings, server_settings
from mcp.types import ImageContent, TextContent
from rendering.files import opened

from e2e.helpers import PORT, consume
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import RetryPolicy, connect
from mcp_server_paperless.paperless.documents import PaperlessDocumentReader
from mcp_server_paperless.rendering.runner import ChildRenderer
from mcp_server_paperless.server import create_server
from mcp_server_paperless.services.pages import PageService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastmcp import FastMCP
    from fastmcp.client.client import CallToolResult
    from PIL import Image

    from e2e.helpers import Stack

type Json = dict[str, object]

REQUEST_LINE = re.compile(rb"(?m)^GET (\S+) HTTP")

PDF = "application/pdf"
UPRIGHT = ["red", "green", "blue", "yellow"]


class Scope:
    """The documents of one test: every id it made, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.ids: list[int] = []

    def document(self, content: bytes, filename: str, mime_type: str = PDF) -> int:
        """A document of `content`, which no other test has: a comment at its end is unique."""
        title = f"plmcp74-{uuid.uuid4().hex}"
        _, made = consume(self.stack.admin, title, content, filename=filename, mime_type=mime_type)
        assert made is not None, f"Paperless did not consume {filename}"
        self.ids.append(made)
        return made


def unique(content: bytes) -> bytes:
    """A PDF that is not a duplicate of an earlier one."""
    return content + f"\n%{uuid.uuid4().hex}\n".encode()


def server(stack: Stack, token: str | None = None) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, token or stack.admin_token),
        server_settings(toolsets=["core", "versions"]),
    )


def report(result: CallToolResult) -> Json:
    first = result.content[0]
    assert isinstance(first, TextContent)
    return cast("Json", json.loads(first.text))


def images(result: CallToolResult) -> list[Image.Image]:
    return [
        opened(base64.b64decode(block.data))
        for block in result.content
        if isinstance(block, ImageContent)
    ]


async def show(stack: Stack, document: int, **arguments: object) -> CallToolResult:
    async with Client(server(stack)) as client:
        return await client.call_tool("get_document_pages", {"document_id": document, **arguments})


async def refused(stack: Stack, document: int, **arguments: object) -> str:
    async with Client(server(stack)) as client:
        result = await client.call_tool(
            "get_document_pages", {"document_id": document, **arguments}, raise_on_error=False
        )
    assert result.is_error
    return str(result.content)


def dark(image: Image.Image) -> int:
    """How many pixels are dark: whether something is written there."""
    return sum(image.convert("L").histogram()[:100])


class Proxy:
    """Stands between the server and Paperless: notes the requests, and can send slowly.

    Attributes:
        requests: The paths asked for, in order.
        slow: Whether everything after the first 64 KiB of an answer comes drop by drop.
    """

    def __init__(self, *, slow: bool = False) -> None:
        self.slow: bool = slow
        self.requests: list[str] = []
        self.url: str = ""
        self._serving: set[asyncio.Task[None]] = set()

    async def _forward(self, source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
        while data := await source.read(65536):
            self.requests += [found[1].decode() for found in REQUEST_LINE.finditer(data)]
            target.write(data)
            await target.drain()
        target.close()

    async def _answer(self, source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
        seen = 0
        while data := await source.read(1024 if self.slow and seen > 65536 else 65536):
            seen += len(data)
            target.write(data)
            await target.drain()
            if self.slow and seen > 65536:
                await asyncio.sleep(0.05)
        target.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._serving.add(cast("asyncio.Task[None]", asyncio.current_task()))
        with contextlib.suppress(ConnectionError):
            upstream = await asyncio.open_connection("127.0.0.1", int(PORT))
            try:
                async with asyncio.TaskGroup() as group:
                    _ = group.create_task(self._forward(reader, upstream[1]))
                    _ = group.create_task(self._answer(upstream[0], writer))
            finally:
                # The transports go now: what is left of them at the end of the event loop is
                # reported in whatever test happens to run when it is collected.
                upstream[1].transport.abort()
                writer.transport.abort()

    @contextlib.asynccontextmanager
    async def listening(self) -> AsyncGenerator[Proxy]:
        """Run while the block runs; ``url`` is where."""
        server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])  # pyright: ignore[reportAny] -- (host, port)
        self.url = f"http://127.0.0.1:{port}"
        try:
            yield self
        finally:
            server.close()
            server.close_clients()
            for task in self._serving:
                _ = task.cancel()
            _ = await asyncio.gather(*self._serving, return_exceptions=True)
            await asyncio.sleep(0)  # an aborted transport closes its socket in the next round


@contextlib.asynccontextmanager
async def page_service(
    stack: Stack,
    *,
    url: str = "",
    wait_seconds: float = 30.0,
    download_seconds: float = 120.0,
    render_seconds: float = 30.0,
) -> AsyncGenerator[PageService]:
    """The service on the real adapter, with shorter waits than the server's."""
    async with connect(
        url or stack.base_url,
        StaticTokenProvider(stack.admin_token),
        timeout=30.0,
        upload_timeout=30.0,
        retry=RetryPolicy(),
    ) as client:
        yield PageService(
            PaperlessDocumentReader(client),
            ChildRenderer(seconds=render_seconds),
            web_url=stack.base_url,
            wait_seconds=wait_seconds,
            download_seconds=download_seconds,
        )
