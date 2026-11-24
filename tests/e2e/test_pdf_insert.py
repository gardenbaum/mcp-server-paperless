"""insert_pages through the assembled server on the E2E Paperless, on documents of its own.

Every document and version a test made is deleted again at its end and its trash entry emptied.
The PDFs have a text layer per page, so the order of the pages can be read from the text.
"""

import re
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash
from fastmcp import Client
from harness import paperless_settings, server_settings
from rendering.files import quadrants, saved

from e2e.helpers import ENCRYPTED_PDF, RESTRICTED_PDF, consume, num, pdf_pages
from e2e.pages import unique
from mcp_server_paperless.server import create_server
from mcp_server_paperless.services.pdf_insert import NOTHING, REWRITE, UNLOCK

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp.client.client import CallToolResult

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-68")]

type Json = dict[str, object]


class Scope:
    """The documents of one test: every id it made, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.ids: list[int] = []

    def document(self, content: bytes, filename: str, mime_type: str = "application/pdf") -> int:
        title = f"plmcp79-{uuid.uuid4().hex}"
        _, made = consume(self.stack.admin, title, content, filename=filename, mime_type=mime_type)
        assert made is not None
        self.ids.append(made)
        return made

    def pages(self, name: str, count: int) -> int:
        """A PDF whose pages say e.g. ``mine1 ... mine2``, each with text of its own."""
        own = uuid.uuid4().hex
        texts = [f"{name}{number} {own} {number}" for number in range(1, count + 1)]
        return self.document(pdf_pages(texts), f"{name}.pdf")

    async def call(
        self, tool: str, token: str | None = None, /, **arguments: object
    ) -> CallToolResult:
        server = create_server(
            paperless_settings(self.stack.base_url, token or self.stack.admin_token),
            server_settings(toolsets=["core", "pdf"]),
        )
        async with Client(server) as client:
            return await client.call_tool(tool, arguments, raise_on_error=False)

    async def run(self, tool: str, **arguments: object) -> Json:
        result = await self.call(tool, **arguments)
        assert result.structured_content is not None, result.content
        produced = cast("list[object]", result.structured_content.get("produced_ids", []))
        self.ids.extend(num(n) for n in produced)
        return result.structured_content

    async def refused(self, tool: str, token: str | None = None, /, **arguments: object) -> str:
        result = await self.call(tool, token, **arguments)
        assert result.is_error
        return str(result.content)

    def record(self, number: int) -> Json:
        answer = self.stack.admin.get(f"/api/documents/{number}/").raise_for_status()
        return cast("Json", answer.json())

    def versions(self, number: int) -> int:
        return len(cast("list[object]", self.record(number)["versions"]))

    def order(self, number: int) -> list[str]:
        """The names of the pages in the text of the document."""
        return re.findall(r"(?:mine|theirs)\d+", str(self.record(number)["content"]))


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


async def test_pages_are_previewed_and_inserted_in_the_middle_in_front_and_at_the_end(
    scope: Scope,
) -> None:
    target, source = scope.pages("mine", 2), scope.pages("theirs", 3)
    into = {"document_id": target, "source_document_id": source}

    preview = await scope.run("insert_pages", **into, pages="2-3", after_page=1, dry_run=True)
    assert preview["state"] == "not_started"
    assert "with 4 pages" in str(cast("Json", preview["plan"])["produces"])
    assert scope.versions(target) == 1

    first = await scope.run("insert_pages", **into, pages="3,2", after_page=1, wait_seconds=30)
    assert (first["state"], first["message"]) == ("success", None)
    assert scope.order(target) == ["mine1", "theirs3", "theirs2", "mine2"]
    # The newest file is longer than the first one: its last page is a place of its own (T1).
    last = await scope.run("insert_pages", **into, pages="1", after_page=4, wait_seconds=30)
    assert last["state"] == "success"
    front = await scope.run("insert_pages", **into, pages="1", after_page=0, wait_seconds=30)
    assert front["state"] == "success"
    assert scope.order(target) == ["theirs1", "mine1", "theirs3", "theirs2", "mine2", "theirs1"]
    assert (scope.record(target)["page_count"], scope.versions(target)) == (6, 4)
    assert (scope.record(source)["page_count"], scope.versions(source)) == (3, 1)

    cut = await scope.run(
        "delete_pages", document_id=target, pages="6", confirm=True, wait_seconds=30
    )
    assert cut["state"] == "success"
    assert scope.record(target)["page_count"] == 5


async def test_pages_make_a_new_document_and_both_stay(scope: Scope) -> None:
    target, source = scope.pages("mine", 1), scope.pages("theirs", 1)

    made = await scope.run(
        "insert_pages",
        document_id=target,
        source_document_id=source,
        after_page=1,
        output="new_document",
        wait_seconds=30,
    )

    assert made["state"] == "success"
    [number] = cast("list[int]", made["produced_ids"])
    assert scope.order(number) == ["mine1", "theirs1"]
    assert scope.record(number)["title"] == scope.record(target)["title"]
    assert (scope.versions(target), scope.versions(source)) == (1, 1)


async def test_the_archive_pdf_of_an_image_is_inserted(scope: Scope) -> None:
    picture = quadrants(612, 792)
    picture.putpixel((5, 5), tuple(uuid.uuid4().bytes[:3]))
    target = scope.pages("mine", 1)
    image = scope.document(saved(picture, "PNG", dpi=(100, 100)), "scan.png", "image/png")

    made = await scope.run(
        "insert_pages",
        document_id=target,
        source_document_id=image,
        after_page=1,
        wait_seconds=30,
    )

    assert made["state"] == "success"
    assert f"archive PDF of document {image}" in str(cast("Json", made["plan"])["produces"])
    assert scope.record(target)["page_count"] == 2
    assert f"Document {image} is not a PDF (image/png)" in await scope.refused(
        "insert_pages", document_id=image, source_document_id=target, after_page=0
    )


async def test_encrypted_files_and_wrong_pages_change_nothing(scope: Scope) -> None:
    target = scope.pages("mine", 1)
    locked = scope.document(unique(ENCRYPTED_PDF), "locked.pdf")
    into = {"document_id": target, "after_page": 1}

    source = await scope.refused("insert_pages", **into, source_document_id=locked)
    assert UNLOCK in source
    assert NOTHING in source
    other = await scope.refused(
        "insert_pages", document_id=locked, source_document_id=target, after_page=0
    )
    assert f"The page count of document {locked} is unknown" in other
    hidden = await scope.refused(
        "insert_pages",
        scope.stack.reader_token,
        document_id=target,
        source_document_id=scope.stack.seeded.private_document,
        after_page=0,
    )
    assert "may not" in hidden or "exists" in hidden
    assert scope.versions(target) == 1


async def test_a_restricted_file_is_refused_until_it_was_rewritten_as_the_message_says(
    scope: Scope,
) -> None:
    target = scope.pages("mine", 1)
    restricted = scope.document(unique(RESTRICTED_PDF), "restricted.pdf")
    into = {"document_id": target, "source_document_id": restricted, "after_page": 1}

    assert REWRITE in await scope.refused("insert_pages", **into)
    assert scope.versions(target) == 1
    rewritten = await scope.run(
        "edit_pdf",
        document_id=restricted,
        pages=[{"page": 1}, {"page": 2}],
        output="new_version",
        wait_seconds=30,
    )
    assert rewritten["state"] == "success"

    made = await scope.run("insert_pages", **into, wait_seconds=30)

    assert made["state"] == "success"
    assert scope.record(target)["page_count"] == 3
