"""get_document_pages on Paperless 3.5.1: pages, versions, users and transports.

The file types and the limits are in test_pages_files.py.
"""

import base64
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from mcp.types import ImageContent
from rendering.files import QUADRANTS, corners, pdf_of
from test_http_uploads import DEFAULT, http_settings, listening

from e2e.helpers import pdf_pages
from e2e.pages import (
    UPRIGHT,
    Json,
    Scope,
    dark,
    images,
    refused,
    report,
    server,
    show,
    unique,
)
from e2e.test_stdio import console_script
from mcp_server_paperless.app import build_app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


async def test_the_original_and_the_archive_of_a_pdf_are_shown(stack: Stack) -> None:
    number = stack.seeded.public_document

    original = await show(stack, number)
    archive = await show(stack, number, source="archive")

    for result, source in ((original, "original"), (archive, "archive")):
        [page] = images(result)
        assert max(page.size) == 1568
        assert dark(page) > 100
        assert (report(result)["source"], report(result)["page_count"]) == (source, 1)


async def test_pages_and_a_region_of_a_pdf_are_shown(stack: Stack, scope: Scope) -> None:
    texts = [f"page {n} {uuid.uuid4().hex[:6]}" for n in range(1, 8)]
    document = scope.document(unique(pdf_pages(texts)), "seven.pdf")

    some = await show(stack, document, pages="6-7,2", size=512)
    part = await show(stack, document, region=[0.05, 0.05, 0.55, 0.15], size=1000)
    first = await show(stack, document, pages="1-", size=256)

    assert [i["page"] for i in cast("list[Json]", report(some)["images"])] == [6, 7, 2]
    assert len(images(some)) == 3
    [enlarged] = images(part)
    assert enlarged.width == 1000
    assert dark(enlarged) > 500
    assert len(images(first)) == 5
    assert (report(first)["not_shown"], report(first)["next_pages"]) == ("pages 6-7", "6-7")
    rest = await show(stack, document, pages=str(report(first)["next_pages"]), size=256)
    assert len(images(rest)) == 2
    assert report(rest)["next_pages"] is None


async def test_every_version_is_shown_and_a_deleted_one_is_not(stack: Stack, scope: Scope) -> None:
    document = scope.document(unique(pdf_of([QUADRANTS])), "first.pdf")
    blue = unique(pdf_of(["0 0 1 rg 0 0 600 800 re f"]))
    async with Client(server(stack)) as client:
        uploaded = await client.call_tool(
            "upload_document_version",
            {
                "document_id": document,
                "content_base64": base64.b64encode(blue).decode(),
                "filename": "second.pdf",
                "wait_seconds": 30,
            },
        )
        output = uploaded.structured_content
        assert output is not None
        listed = await client.call_tool("list_document_versions", {"document_id": document})
        versions = cast("list[Json]", (listed.structured_content or {})["versions"])
        newest = max(cast("int", version["id"]) for version in versions)

        latest = await show(stack, document, size=256)
        root = await show(stack, document, size=256, version_id=document)
        archive = await show(stack, document, size=256, version_id=document, source="archive")
        _ = await client.call_tool(
            "delete_document_version",
            {"document_id": document, "version_id": newest, "confirm": True},
        )

    assert (report(latest)["version_id"], corners(images(latest)[0])) == (newest, ["blue"] * 4)
    assert (report(root)["version_id"], corners(images(root)[0])) == (document, UPRIGHT)
    assert corners(images(archive)[0]) == UPRIGHT
    assert f"Document {document} has no version {newest}." in await refused(
        stack, document, version_id=newest
    )


async def test_a_text_original_is_refused_with_the_reason(stack: Stack, scope: Scope) -> None:
    document = scope.document(uuid.uuid4().hex.encode(), "note.txt", "text/plain")

    said = await refused(stack, document)

    assert "is text/plain, which cannot be shown as pages" in said
    assert "Read its text with get_document_content." in said


async def test_a_page_past_the_end_and_a_missing_document_are_refused(stack: Stack) -> None:
    number = stack.seeded.public_document

    assert "Page 2 does not exist: the document has 1 page." in await refused(
        stack, number, pages="2"
    )
    assert "No document 999999 exists" in await refused(stack, 999_999)


async def test_a_restricted_user_sees_the_pages_of_their_documents_only(stack: Stack) -> None:
    async with Client(server(stack, stack.reader_token)) as client:
        seen = await client.call_tool(
            "get_document_pages", {"document_id": stack.seeded.public_document, "size": 256}
        )
        hidden = await client.call_tool(
            "get_document_pages",
            {"document_id": stack.seeded.private_document},
            raise_on_error=False,
        )

    assert len(images(seen)) == 1
    assert hidden.is_error
    assert not [block for block in hidden.content if isinstance(block, ImageContent)]


async def test_pages_are_shown_by_the_console_script_over_stdio(stack: Stack) -> None:
    transport = StdioTransport(
        console_script(),
        [],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token},
        keep_alive=False,
    )

    async with Client(transport) as client:
        result = await client.call_tool(
            "get_document_pages", {"document_id": stack.seeded.public_document}
        )

    assert dark(images(result)[0]) > 100


async def test_pages_are_shown_over_http(stack: Stack) -> None:
    app = build_app(server(stack), http_settings(with_token=False), max_upload_bytes=DEFAULT)
    with listening(app) as url:
        async with Client(StreamableHttpTransport(url), mode="legacy") as client:
            result = await client.call_tool(
                "get_document_pages", {"document_id": stack.seeded.public_document}
            )

    assert dark(images(result)[0]) > 100
