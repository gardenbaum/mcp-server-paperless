"""insert_pages, extract_pages and split_document through the client when a step fails.

On the fake: tasks the user may not see, and steps Paperless refuses after the first one is done.
"""

from typing import cast

import pytest
from fakes.paperless import FakeDocument, FakePaperless, FakeUser, seeded
from fastmcp import Client
from harness import serve
from services.test_pdf_replacing import Paperless, document, paperless, trash

from mcp_server_paperless.domain.errors import PermissionDenied
from tools.test_insert import sized

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-67")]

type Json = dict[str, object]

DOC, OTHER = 50, 51
RIGHTS = frozenset(
    {
        *("view_document", "change_document", "add_document", "delete_document"),
        *("view_uisettings", "view_tag", "view_correspondent", "view_documenttype"),
        *("view_storagepath", "view_customfield"),
    }
)
SPLIT: Json = {
    "parts": ["1", "2-4"],
    "part_metadata": [{"title": "Letter"}, {"title": "Invoice"}],
    "output": "replace_original",
    "confirm": True,
}
EXTRACT: Json = {"pages": "2", "remove": True, "metadata": {"title": "Page"}, "confirm": True}


async def call(fake: FakePaperless, tool: str, **arguments: object) -> Json:
    if isinstance(fake, Paperless):
        server = serve(fake, toolsets=["pdf"], sleep=fake.sleep, clock=fake.clock)
    else:
        server = serve(fake, toolsets=["pdf"])
    async with Client(server) as client:
        result = await client.call_tool(
            tool, {"document_id": DOC, **arguments}, raise_on_error=False
        )
    assert result.structured_content is not None, result.content
    return result.structured_content


def shown(fake: FakePaperless) -> list[int]:
    """The versions of the document."""
    return [version.id for version in document(fake, DOC).versions]


def unseeing(target: bytes = b"%PDF-1.4 fake") -> FakePaperless:
    """Two documents of nobody, for a user who may do everything but view tasks."""
    state = seeded()
    state.users.append(FakeUser(id=90, username="unseeing", permissions=RIGHTS))
    state.documents += [
        FakeDocument(id=DOC, title="Scan", page_count=4, original=target),
        FakeDocument(id=OTHER, title="Annex", page_count=1, original=sized(501)),
    ]
    return FakePaperless(state, user="unseeing", allow_writes=True)


@pytest.mark.decision("decision-68")
@pytest.mark.parametrize(
    ("output", "following"),
    [
        ("new_version", f'get_document(document_id={DOC}, include=["versions"])'),
        ("new_document", "search_documents (newest added first)"),
    ],
)
async def test_an_insertion_nobody_can_follow_is_unknown_and_says_where_to_look(
    output: str, following: str
) -> None:
    fake = unseeing(sized(301, 302, 303, 304))

    result = await call(
        fake, "insert_pages", source_document_id=OTHER, after_page=1, output=output, wait_seconds=5
    )

    [task] = cast("list[Json]", result["tasks"])
    assert (result["state"], task["state"], result["produced_ids"]) == ("unknown", "unknown", [])
    assert task["task_id"] == fake.state.tasks[-1].task_id
    assert following in str(result["next"])
    assert "Look before repeating the call." in str(result["message"])


async def test_parts_nobody_can_follow_keep_the_original_and_say_how_to_go_on() -> None:
    fake = unseeing()

    result = await call(fake, "split_document", **SPLIT)

    parts = cast("list[Json]", result["parts"])
    assert [(p["state"], p["metadata"], p["document_id"]) for p in parts] == [
        ("unknown", "not_set", None),
        ("unknown", "not_set", None),
    ]
    assert 'title="Letter"' in str(parts[0]["next"])
    assert (result["state"], result["original"], trash(fake)) == ("unknown", "kept", [])
    assert "search_documents" in str(result["next"])


async def test_an_extraction_nobody_can_follow_removes_nothing() -> None:
    fake = unseeing()

    result = await call(fake, "extract_pages", **EXTRACT)

    [part] = cast("list[Json]", result["parts"])
    assert (part["state"], part["metadata"]) == ("unknown", "not_set")
    assert 'title="Page"' in str(part["next"])
    assert (result["state"], result["original"]) == ("unknown", "kept")
    assert shown(fake) == [DOC]


async def test_a_failed_part_keeps_the_original_and_the_other_part_gets_its_metadata() -> None:
    fake = paperless()
    fake.states = {0: "failure"}

    result = await call(fake, "split_document", **SPLIT)

    first, second = cast("list[Json]", result["parts"])
    assert (first["state"], first["metadata"]) == ("failure", "not_set")
    assert (second["state"], second["metadata"]) == ("success", "set")
    assert document(fake, cast("int", second["document_id"])).title == "Invoice"
    assert (result["state"], result["original"], trash(fake)) == ("failure", "kept", [])
    assert "a task failed" in str(result["message"])


async def test_a_part_whose_metadata_fails_keeps_the_original() -> None:
    fake = paperless()
    fake.refuse = lambda number, _: number == OTHER + 1

    result = await call(fake, "split_document", **SPLIT)

    first, second = cast("list[Json]", result["parts"])
    assert (first["metadata"], second["metadata"]) == ("not_set", "set")
    assert f"update_document(document_id={OTHER + 1}, title=" in str(first["next"])
    assert (result["original"], trash(fake)) == ("kept", [])
    assert "the metadata of part 1 is not set" in str(result["message"])
    assert f"delete_document(document_id={DOC})" in str(result["next"])


async def test_an_original_that_may_not_be_trashed_stays_beside_its_parts() -> None:
    fake = paperless()
    fake.refuse_deletes = {DOC}

    result = await call(fake, "split_document", **SPLIT)

    assert [p["metadata"] for p in cast("list[Json]", result["parts"])] == ["set", "set"]
    assert (result["original"], result["trashed_ids"], trash(fake)) == ("kept", [], [])
    assert f"Not moved to the trash: document {DOC}" in str(result["message"])


async def test_a_failed_new_document_leaves_the_pages() -> None:
    fake = paperless()
    fake.states = {0: "failure"}

    result = await call(fake, "extract_pages", **EXTRACT)

    assert (result["state"], result["original"], result["produced_ids"]) == ("failure", "kept", [])
    assert f"No page was removed from document {DOC}: a task failed" in str(result["message"])
    assert shown(fake) == [DOC]


async def test_the_pages_stay_without_the_metadata_of_the_new_document() -> None:
    fake = paperless()
    fake.refuse = lambda number, _: number == OTHER + 1

    result = await call(fake, "extract_pages", **EXTRACT)

    [part] = cast("list[Json]", result["parts"])
    assert (part["state"], part["metadata"], result["original"]) == ("success", "not_set", "kept")
    assert "the metadata of the new document is not set" in str(result["message"])
    assert f"delete_pages(document_id={DOC}" in str(result["next"])
    assert shown(fake) == [DOC]


async def test_a_refused_removal_names_the_new_document_that_is_left() -> None:
    fake = paperless()
    fake.refuse_edit = {"new_version": PermissionDenied("No.")}

    result = await call(fake, "extract_pages", **EXTRACT)

    [product] = cast("list[int]", result["produced_ids"])
    assert (document(fake, product).title, document(fake, product).page_count) == ("Page", 1)
    assert result["original"] == "kept"
    assert f"No page was removed from document {DOC}: No" in str(result["message"])
    assert f"delete_document(document_id={product}) to drop" in str(result["next"])
    assert shown(fake) == [DOC]
