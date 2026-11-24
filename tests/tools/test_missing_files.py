"""Documents whose file Paperless cannot find, through the in-memory client."""

from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakeDocument, FakePaperless, seeded
from fastmcp import Client
from harness import serve
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

if TYPE_CHECKING:
    from fastmcp import FastMCP

pytestmark = pytest.mark.anyio

LOST = 7
MESSAGE = f"Paperless cannot find the original of document {LOST}"
A_FILE = f"Paperless cannot find a file of document {LOST}"


def server() -> FastMCP:
    state = seeded()
    state.email_enabled = True
    state.documents.append(FakeDocument(id=LOST, title="lost", archive=None))
    state.missing = {(LOST, "original"), (LOST, "thumbnail")}
    state.nameless = {LOST}
    return serve(
        FakePaperless(state, user="admin"), toolsets=["core", "admin", "pdf", "sharing", "versions"]
    )


async def test_the_document_its_versions_and_its_file_metadata_are_read() -> None:
    async with Client(server()) as client:
        result = await client.call_tool(
            "get_document", {"document_id": LOST, "include": ["metadata", "versions"]}
        )
        text = await client.call_tool("get_document_content", {"document_id": LOST})
        versions = await client.call_tool("list_document_versions", {"document_id": LOST})

    assert result.structured_content is not None
    facts = cast("dict[str, object]", result.structured_content["file_metadata"])
    assert (facts["original_size"], facts["original_filename"], facts["media_filename"]) == (
        None,
        None,
        None,
    )
    assert facts["original_checksum"]
    assert not text.is_error
    assert not versions.is_error


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("download_document", {"document_id": LOST}, f"{MESSAGE}."),
        ("download_document", {"document_id": LOST, "offset": 0}, f"{MESSAGE} (version {LOST})."),
        ("get_document_pages", {"document_id": LOST}, f"{MESSAGE} (version {LOST})."),
        (
            "get_document_thumbnail",
            {"document_id": LOST},
            f"Paperless cannot find the thumbnail of document {LOST}.",
        ),
        ("bulk_download", {"document_ids": [1, LOST]}, A_FILE),
        ("rotate_documents", {"document_ids": [LOST], "degrees": 90}, A_FILE),
        (
            "email_documents",
            {
                "document_ids": [1, LOST],
                "recipients": ["a@example.org"],
                "subject": "s",
                "message": "m",
            },
            A_FILE,
        ),
    ],
)
async def test_what_needs_the_file_says_that_paperless_cannot_find_it(
    tool: str, arguments: dict[str, object], message: str
) -> None:
    async with Client(server()) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)

    assert result.is_error
    [text] = result.content
    assert isinstance(text, TextContent)
    assert text.text.startswith(message)
    assert "Hint: " in text.text


@pytest.mark.parametrize("kind", ["thumbnail", "original"])
async def test_the_resources_say_it_too(kind: str) -> None:
    async with Client(server()) as client:
        with pytest.raises(MCPError, match="cannot find"):
            _ = await client.read_resource(f"paperless://documents/{LOST}/{kind}")
