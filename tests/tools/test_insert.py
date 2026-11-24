"""insert_pages through the in-memory client, with the real renderer on the seeded fake."""

from typing import TYPE_CHECKING, cast

import pypdfium2
import pytest
from e2e.helpers import RESTRICTED_PDF
from fakes.accounts import READER
from fakes.paperless import FakeDocument, FakePaperless, seeded
from fastmcp import Client
from harness import admin, serve
from rendering.files import ENCRYPTED_PDF
from rendering.test_overview import mixed

from mcp_server_paperless.services.pdf_insert import NOTHING, REWRITE, UNLOCK

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-68")]

type Json = dict[str, object]

NAME = "insert_pages"
TARGET, SOURCE = 50, 51


def sized(*widths: int) -> bytes:
    return mixed(*[f"/MediaBox [0 0 {width} 800]" for width in widths])


def widths(content: bytes) -> list[int]:
    pdf = pypdfium2.PdfDocument(content)
    return [round(pdf.get_page_size(n)[0]) for n in range(len(pdf))]


def fake(
    target: bytes | None = None,
    source: bytes | None = None,
    *,
    pages: int = 2,
    user: str = "admin",
    writes: bool = True,
) -> FakePaperless:
    state = seeded()
    state.documents += [
        FakeDocument(
            id=TARGET, title="Contract", page_count=pages, original=target or sized(301, 302)
        ),
        FakeDocument(
            id=SOURCE,
            title="Annex",
            page_count=3,
            original=source or sized(501, 502, 503),
            owner=2,
        ),
    ]
    return FakePaperless(state, user=user, allow_writes=writes)


def document(paperless: FakePaperless, number: int) -> FakeDocument:
    return next(d for d in paperless.state.documents if d.id == number)


async def call(paperless: FakePaperless, **arguments: object) -> CallToolResult:
    given = {"document_id": TARGET, "source_document_id": SOURCE, "after_page": 1, **arguments}
    async with Client(serve(paperless, toolsets=["pdf"])) as client:
        return await client.call_tool(NAME, given, raise_on_error=False)


async def insert(paperless: FakePaperless, **arguments: object) -> Json:
    result = await call(paperless, **arguments)
    assert result.structured_content is not None, result.content
    return result.structured_content


async def refusal(paperless: FakePaperless, **arguments: object) -> str:
    result = await call(paperless, **arguments)
    assert result.is_error
    return str(result.content)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"pages": "2", "after_page": 0}, [502, 301, 302]),
        ({"pages": "3,1"}, [301, 503, 501, 302]),
        ({"after_page": 2}, [301, 302, 501, 502, 503]),
    ],
    ids=["front", "middle", "all at the end"],
)
async def test_pages_become_a_new_version_and_the_source_stays(
    arguments: Json, expected: list[int]
) -> None:
    paperless = fake()
    before = document(paperless, SOURCE).original

    result = await insert(paperless, **arguments, wait_seconds=5)

    target = document(paperless, TARGET)
    assert widths(target.original) == expected
    assert len(target.versions) == 2
    assert document(paperless, SOURCE).original == before
    assert (result["operation"], result["state"]) == (NAME, "success")
    assert result["produced_ids"] == [target.versions[0].id]
    assert target.page_count == len(expected)


async def test_pages_become_a_new_document_and_both_stay() -> None:
    paperless = fake()
    count = len(paperless.state.documents)

    result = await insert(paperless, pages="1", output="new_document")

    assert len(paperless.state.documents) == count + 1
    [made] = cast("list[int]", result["produced_ids"])
    assert document(paperless, made).title == "Contract"
    assert widths(document(paperless, made).original) == [301, 501, 302]
    assert len(document(paperless, TARGET).versions) == 1
    assert widths(document(paperless, TARGET).original) == [301, 302]


async def test_a_dry_run_returns_the_plan_and_writes_nothing() -> None:
    result = await insert(fake(writes=False), dry_run=True)

    assert (result["dry_run"], result["state"], result["tasks"]) == (True, "not_started", [])
    plan = cast("Json", result["plan"])
    assert "with 5 pages" in str(plan["produces"])
    assert plan["needs_confirm"] is False


async def test_an_older_version_of_the_source_is_read() -> None:
    assert "has no version 99" in await refusal(fake(), source_version_id=99)
    paperless = fake()
    _ = await insert(paperless, pages="1", source_version_id=SOURCE)

    assert widths(document(paperless, TARGET).original) == [301, 501, 302]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"pages": "4"}, "Page 4 does not exist: the document has 3 pages."),
        ({"pages": "1," * 101}, "pages"),
        ({"after_page": 3}, f"Page 3 does not exist: document {TARGET} has 2 pages."),
        ({"after_page": -1}, "after_page"),
        ({"after_page": None}, "after_page"),
        ({"source_document_id": TARGET}, "A document cannot be its own source."),
        ({"source_document_id": 999}, "No document 999 exists"),
        ({"output": "replace_original"}, "output"),
        ({"wait_seconds": -1}, "wait_seconds"),
    ],
)
async def test_bad_arguments_are_tool_errors(arguments: Json, message: str) -> None:
    assert message in await refusal(fake(writes=False), **arguments)


@pytest.mark.parametrize(
    ("paperless", "message"),
    [
        (fake(target=ENCRYPTED_PDF, writes=False), UNLOCK),
        (fake(source=ENCRYPTED_PDF, writes=False), UNLOCK),
        (fake(target=RESTRICTED_PDF, writes=False), REWRITE),
        (fake(source=RESTRICTED_PDF, writes=False), REWRITE),
        (fake(source=b"%PDF-1.4 broken", writes=False), f"The original of document {SOURCE} is d"),
        (fake(pages=1, writes=False), "does not have the 1 page that Paperless counted"),
    ],
)
async def test_hostile_and_encrypted_files_change_nothing(
    paperless: FakePaperless, message: str
) -> None:
    text = await refusal(paperless, after_page=1)

    assert message in text
    assert NOTHING in text


async def test_a_user_who_may_not_see_a_document_is_told_nothing() -> None:
    assert f"No document {SOURCE} exists" in await refusal(fake(user=READER[0], writes=False))


async def test_the_tool_is_offered_with_its_toolset_only_and_not_in_read_only_mode() -> None:
    async def names(*, read_only: bool = False) -> set[str]:
        async with Client(serve(admin(), toolsets=["pdf"], read_only=read_only)) as client:
            return {tool.name for tool in await client.list_tools()}

    assert NAME in await names()
    assert NAME not in await names(read_only=True)
    async with Client(serve(admin())) as client:
        assert NAME not in {tool.name for tool in await client.list_tools()}


async def test_the_tool_destroys_nothing_and_says_what_is_lost() -> None:
    [tool] = [t for t in await serve(admin(), toolsets=["pdf"]).list_tools() if t.name == NAME]

    assert tool.annotations is not None
    assert (tool.annotations.destructive_hint, tool.annotations.read_only_hint) == (False, False)
    assert tool.annotations.idempotent_hint is False
    text = " ".join((tool.description or "").split())
    for said in (
        "bookmarks",
        "hidden layer",
        "signature",
        "100 MiB",
        "everybody who may see",
        "before repeating",
    ):
        assert said in text
    properties = cast("dict[str, Json]", tool.parameters["properties"])
    assert set(properties) == {
        *("document_id", "source_document_id", "after_page", "pages", "source_version_id"),
        *("output", "dry_run", "wait_seconds"),
    }
    assert (properties["after_page"]["minimum"], properties["pages"]["default"]) == (0, "1-")
    assert properties["output"]["default"] == "new_version"
    assert "after_page" in cast("list[str]", tool.parameters["required"])
