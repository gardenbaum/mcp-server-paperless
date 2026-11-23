"""What the live tests of get_document_pages share."""

import base64
import json
import uuid
from typing import TYPE_CHECKING, cast

from fastmcp import Client
from harness import paperless_settings, server_settings
from mcp.types import ImageContent, TextContent
from rendering.files import opened

from e2e.helpers import consume
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.client.client import CallToolResult
    from PIL import Image

    from e2e.helpers import Stack

type Json = dict[str, object]

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
