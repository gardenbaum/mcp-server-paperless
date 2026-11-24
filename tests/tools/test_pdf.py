"""The pdf tools through the in-memory client on the seeded fake (PLMCP-23)."""

from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import admin, serve

from mcp_server_paperless.services.pdf import MAX_DOCUMENTS, MAX_PAGES, MAX_PARTS

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

type Json = dict[str, object]

TOOLS = (
    "rotate_documents",
    "merge_documents",
    "split_document",
    "extract_pages",
    "delete_pages",
    "edit_pdf",
    "remove_document_password",
    "reprocess_documents",
)
CONFIRM_DESCRIPTION = (
    "Must be true to go ahead at once. Without it a client that can show dialogs asks the "
    "user, and the tool runs only if they accept; with any other nothing changes and the "
    "error says what would happen. Describe that to the user and pass true only once they "
    "agree, or if they already asked for exactly this."
)
FIRST, SECOND, LOCKED = 10, 11, 12


def fake(*, writes: bool = True) -> FakePaperless:
    state: FakeState = seeded()
    state.documents.extend(
        [
            FakeDocument(id=FIRST, title="Scan one", owner=2, page_count=4),
            FakeDocument(id=SECOND, title="Scan two", owner=2, page_count=2),
            FakeDocument(id=LOCKED, title="Locked", owner=2, page_count=None, password="s3cret"),
        ]
    )
    return FakePaperless(state, user="admin", allow_writes=writes)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(serve(source, toolsets=["pdf"])) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(serve(source, toolsets=["pdf"])) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


async def test_the_pdf_tools_are_offered_only_when_the_toolset_is_selected() -> None:
    async def names(**settings: object) -> set[str]:
        async with Client(serve(admin(), **cast("Mapping[str, object]", settings))) as client:  # pyright: ignore[reportArgumentType] -- keyword passthrough
            return {tool.name for tool in await client.list_tools()}

    assert set(TOOLS).isdisjoint(await names())
    assert set(TOOLS) <= await names(toolsets=["pdf"])
    assert set(TOOLS) <= await names(toolsets=["all"])
    assert set(TOOLS).isdisjoint(await names(toolsets=["all"], read_only=True))
    assert "inspect_pdf" not in await names()
    assert await names(toolsets=["pdf"], read_only=True) == {"inspect_pdf"}


async def test_the_input_schemas_bound_the_selections() -> None:
    async with Client(serve(admin(), toolsets=["pdf"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    def properties(tool: str) -> dict[str, Json]:
        return cast("dict[str, Json]", tools[tool].input_schema["properties"])

    rotate = properties("rotate_documents")
    assert set(rotate) == {
        *("document_ids", "degrees", "use_original_files"),
        *("dry_run", "confirm", "wait_seconds"),
    }
    assert (rotate["document_ids"]["minItems"], rotate["document_ids"]["maxItems"]) == (
        1,
        MAX_DOCUMENTS,
    )
    assert rotate["degrees"]["enum"] == [90, 180, 270]
    merge = properties("merge_documents")
    assert set(merge) == {
        "document_ids",
        "metadata_document_id",
        "delete_originals",
        "archive_fallback",
        "use_original_files",
        "dry_run",
        "confirm",
        "wait_seconds",
    }
    assert merge["document_ids"]["minItems"] == 2
    assert merge["confirm"]["description"] == CONFIRM_DESCRIPTION
    one = {"document_id", "output", "version_id", "keep_metadata", "dry_run", "confirm"}
    one |= {"wait_seconds"}
    split = properties("split_document")
    assert set(split) == one | {"parts", "part_metadata"}
    [entries, _] = cast("list[Json]", split["part_metadata"]["anyOf"])
    assert (entries["minItems"], entries["maxItems"]) == (2, MAX_PARTS)
    metadata = cast("Json", entries["items"])["properties"]
    assert set(cast("Json", metadata)) == {
        *("title", "document_type", "correspondent", "created", "tags")
    }
    extract = properties("extract_pages")
    assert set(extract) == (one - {"output"}) | {"pages", "remove", "metadata"}
    assert (extract["remove"]["default"], extract["keep_metadata"]["default"]) == (False, True)
    assert (split["parts"]["minItems"], split["parts"]["maxItems"]) == (2, MAX_PARTS)
    assert split["output"]["enum"] == ["new_document", "replace_original"]
    pages = properties("delete_pages")
    assert set(pages) == one | {"pages"}
    assert pages["output"]["enum"] == ["new_document", "new_version", "replace_original"]
    assert pages["output"]["default"] == "new_version"
    edit = properties("edit_pdf")
    assert set(edit) == one | {"pages"}
    assert (edit["pages"]["minItems"], edit["pages"]["maxItems"]) == (1, MAX_PAGES)
    password = properties("remove_document_password")
    assert set(password) == one | {"password"}
    assert password["password"]["minLength"] == 1
    reprocess = properties("reprocess_documents")
    assert set(reprocess) == {
        *("document_ids", "remote_ocr", "dry_run", "confirm", "wait_seconds"),
    }
    for tool in TOOLS:
        wait = properties(tool)["wait_seconds"]
        if tool in {"rotate_documents", "reprocess_documents"}:
            assert (wait["minimum"], wait["default"]) == (0, 0)
        else:
            [number, _] = cast("list[Json]", wait["anyOf"])
            assert (number["minimum"], wait["default"]) == (0, None)


async def test_rotating_returns_the_result_of_the_operation() -> None:
    source = fake()

    result = await call(source, "rotate_documents", document_ids=[FIRST, SECOND], degrees=90)

    assert result["operation"] == "rotate"
    assert result["summary"] == "rotate 2 documents by 90 degrees clockwise"
    assert result["source_document_ids"] == [FIRST, SECOND]
    assert (result["state"], result["wait"]) == ("success", "not_requested")
    assert len(cast("list[Json]", result["tasks"])) == 2


async def test_a_wait_returns_the_finished_tasks_and_the_documents_they_made() -> None:
    source = fake()

    result = await call(source, "merge_documents", document_ids=[FIRST, SECOND], wait_seconds=5)

    assert (result["state"], result["wait"]) == ("success", "finished")
    [made] = cast("list[int]", result["produced_ids"])
    assert [d.page_count for d in source.state.documents if d.id == made] == [6]


async def test_deleting_the_originals_of_a_merge_is_refused_without_confirm() -> None:
    source = fake(writes=False)

    text = await refusal(
        source, "merge_documents", document_ids=[FIRST, SECOND], delete_originals=True
    )

    assert "Nothing was changed. Confirming would merge" in text
    assert "confirm=true" in text
    assert source.state.trash == []


async def test_a_confirmed_merge_trashes_the_originals() -> None:
    source = fake()

    _ = await call(
        source,
        "merge_documents",
        document_ids=[FIRST, SECOND],
        delete_originals=True,
        confirm=True,
    )

    assert {d.id for d in source.state.trash} == {FIRST, SECOND}


async def test_splitting_makes_a_document_per_part() -> None:
    source = fake()

    result = await call(
        source, "split_document", document_id=FIRST, parts=["1-2", "3-4"], wait_seconds=5
    )

    assert len(cast("list[int]", result["produced_ids"])) == 2
    assert (
        result["summary"] == f"split document {FIRST} into 2 new documents: pages 1-2 | pages 3-4"
    )


async def test_a_split_gives_each_part_its_metadata() -> None:
    source = fake()

    result = await call(
        source,
        "split_document",
        document_id=FIRST,
        parts=["1-2", "3-4"],
        part_metadata=[
            {"title": "Letter", "tags": ["e2e-finance"], "created": "2026-01-02"},
            {"document_type": "e2e-invoice", "correspondent": 1},
        ],
    )

    parts = cast("list[Json]", result["parts"])
    assert [p["metadata"] for p in parts] == ["set", "set"]
    made = {d.id: d for d in source.state.documents}
    letter, invoice = (made[cast("int", p["document_id"])] for p in parts)
    assert (letter.title, letter.tags, letter.created.isoformat()) == ("Letter", [1], "2026-01-02")
    assert (invoice.title, invoice.document_type, invoice.correspondent) == ("Scan one", 1, 1)
    assert (result["original"], result["wait"]) == ("kept", "finished")


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("split_document", {"parts": ["1", "2"], "part_metadata": [{"tile": "x"}, {}]}),
        ("split_document", {"parts": ["1", "2"], "part_metadata": [{"title": "x"}]}),
        ("extract_pages", {"pages": "1", "metadata": {"tile": "x"}}),
    ],
)
async def test_metadata_the_tool_does_not_know_is_refused(tool: str, arguments: Json) -> None:
    source = fake(writes=False)

    assert await refusal(source, tool, document_id=FIRST, **arguments)


async def test_extracting_with_removal_needs_confirm_and_then_shortens_the_document() -> None:
    arguments: Json = {"document_id": FIRST, "pages": "2", "remove": True}
    text = await refusal(fake(writes=False), "extract_pages", **arguments)
    assert "extract page 2 of document 10 'Scan one' into a new document and remove them" in text

    source = fake()
    result = await call(
        source, "extract_pages", **arguments, metadata={"title": "Page two"}, confirm=True
    )

    assert (result["state"], result["original"]) == ("success", "shortened")
    made = {d.id: d for d in source.state.documents}
    [part] = cast("list[Json]", result["parts"])
    new = made[cast("int", part["document_id"])]
    assert (new.title, new.page_count, made[FIRST].page_count) == ("Page two", 1, 3)


async def test_extracting_alone_needs_no_confirm_and_the_options_reach_paperless() -> None:
    source = fake()
    made = {d.id: d for d in source.state.documents}
    made[FIRST].tags = [1]

    result = await call(
        source, "extract_pages", document_id=FIRST, pages="3,1", keep_metadata=False,
        version_id=FIRST, wait_seconds=5,
    )  # fmt: skip

    [new] = cast("list[int]", result["produced_ids"])
    assert [1 in d.tags for d in source.state.documents if d.id == new] == [False]
    assert made[FIRST].page_count == 4


async def test_replacing_the_original_by_a_split_needs_confirm() -> None:
    text = await refusal(
        fake(writes=False),
        "split_document",
        document_id=FIRST,
        parts=["1-2", "3-4"],
        output="replace_original",
    )

    assert "move the original to the Paperless trash" in text


async def test_deleting_pages_needs_confirm_and_then_makes_a_shorter_version() -> None:
    text = await refusal(fake(writes=False), "delete_pages", document_id=FIRST, pages="2")
    assert "delete page 2 of document 10 'Scan one'" in text

    source = fake()
    result = await call(source, "delete_pages", document_id=FIRST, pages="2", confirm=True)

    assert result["summary"] == f"delete page 2 of document {FIRST} as a new version"
    assert [d.page_count for d in source.state.documents if d.id == FIRST] == [3]


async def test_a_page_beyond_the_end_is_refused_naming_the_page_count() -> None:
    text = await refusal(fake(writes=False), "delete_pages", document_id=SECOND, pages="3")

    assert "Page 3 does not exist: the document has 2 pages" in text


async def test_an_edit_takes_pages_with_rotation_and_output_file() -> None:
    source = fake()

    result = await call(
        source,
        "edit_pdf",
        document_id=FIRST,
        pages=[{"page": 4}, {"page": 1, "rotate": 90}, {"page": 2, "document": 1}],
        wait_seconds=5,
    )

    assert len(cast("list[int]", result["produced_ids"])) == 2


async def test_removing_a_password_keeps_the_password_out_of_the_result() -> None:
    source = fake()

    result = await call(
        source, "remove_document_password", document_id=LOCKED, password="s3cret", wait_seconds=5
    )

    assert result["state"] == "success"
    assert "s3cret" not in str(result)


async def test_a_wrong_password_is_an_error_that_does_not_repeat_it() -> None:
    text = await refusal(
        fake(), "remove_document_password", document_id=LOCKED, password="guess-1234"
    )

    assert "password" in text
    assert "guess-1234" not in text


async def test_reprocessing_needs_confirm_and_then_starts_the_tasks() -> None:
    text = await refusal(fake(writes=False), "reprocess_documents", document_ids=[FIRST])
    assert "reprocess document 10 'Scan one'" in text

    source = fake()
    result = await call(
        source, "reprocess_documents", document_ids=[FIRST], confirm=True, wait_seconds=5
    )

    assert (result["state"], result["produced_ids"]) == ("success", [])
    assert source.state.reprocessed == [FIRST]


ONE: Json = {"document_id": FIRST, "dry_run": True}
PREVIEWS: dict[str, Json] = {
    "rotate_documents": {"document_ids": [FIRST], "degrees": 90, "dry_run": True},
    "merge_documents": {"document_ids": [FIRST, SECOND], "delete_originals": True, "dry_run": True},
    "split_document": {**ONE, "parts": ["1", "2"], "output": "replace_original"},
    "extract_pages": {**ONE, "pages": "1", "remove": True, "metadata": {"title": "One"}},
    "delete_pages": {**ONE, "pages": "1"},
    "edit_pdf": {**ONE, "pages": [{"page": 1}], "version_id": FIRST, "keep_metadata": False},
    "remove_document_password": {**ONE, "document_id": LOCKED, "password": "s3cret"},
    "reprocess_documents": {"document_ids": [FIRST], "dry_run": True},
}
CONFIRMED = {
    *("merge_documents", "split_document", "extract_pages", "delete_pages", "reprocess_documents")
}


@pytest.mark.parametrize("tool", TOOLS)
async def test_every_tool_previews_without_a_confirmation_and_changes_nothing(tool: str) -> None:
    source = fake(writes=False)

    result = await call(source, tool, **PREVIEWS[tool])

    assert (result["dry_run"], result["state"], result["tasks"]) == (True, "not_started", [])
    plan = cast("Json", result["plan"])
    assert plan["needs_confirm"] is (tool in CONFIRMED)
    assert plan["produces"]
    assert "s3cret" not in str(result)


async def test_the_next_calls_of_an_unfinished_replacement_are_accepted_by_their_tools() -> None:
    source = fake()

    result = await call(
        source,
        "delete_pages",
        document_id=FIRST,
        pages="1",
        output="replace_original",
        confirm=True,
        wait_seconds=0,
    )

    assert (result["trashed_ids"], source.state.trash) == ([], [])
    [task] = cast("list[Json]", result["tasks"])
    assert result["next"] == (
        f'get_task(task_id="{task["task_id"]}", wait_seconds=30); only if every one succeeded and '
        "made exactly one document (get_document), with 3 pages, and "
        f"only if document {FIRST} is unchanged: delete_document(document_id={FIRST})"
    )
    async with Client(serve(source, toolsets=["core", "pdf"])) as client:
        _ = await client.call_tool("delete_document", {"document_id": FIRST, "confirm": True})
    assert [d.id for d in source.state.trash] == [FIRST]
    # The other calls `next` can name, with the arguments it names.
    async with Client(serve(source, toolsets=["core"])) as client:
        answer = await client.call_tool(
            "get_task", {"task_id": task["task_id"], "wait_seconds": 30}
        )
        numbered = await client.call_tool(
            "update_document", {"document_id": SECOND, "archive_serial_number": 5}
        )
        cleared = await client.call_tool(
            "update_document", {"document_id": SECOND, "clear": ["archive_serial_number"]}
        )
        looked = await client.call_tool(
            "get_document", {"document_id": SECOND, "include": ["versions"]}
        )
        named = await client.call_tool(
            "update_document",
            {
                "document_id": SECOND,
                "title": "Annex",
                "created": "2026-01-02",
                "correspondent": 1,
                "document_type": 1,
                "tags_set": [],
            },
        )
    async with Client(serve(source, toolsets=["pdf"])) as client:
        removed = await client.call_tool(
            "delete_pages",
            {"document_id": SECOND, "pages": "1", "output": "new_version", "confirm": True},
        )
    assert not any(a.is_error for a in (answer, numbered, cleared, looked, named, removed))


async def test_the_options_reach_paperless() -> None:
    source = fake()
    document = next(d for d in source.state.documents if d.id == FIRST)
    document.tags = [1]
    source.state.documents.append(
        FakeDocument(id=13, title="Photo", owner=2, mime_type="image/png", page_count=1)
    )

    bare = await call(
        source, "edit_pdf", document_id=FIRST, pages=[{"page": 1}], keep_metadata=False
    )
    merged = await call(source, "merge_documents", document_ids=[FIRST, 13], archive_fallback=True)
    both = await call(
        source, "merge_documents", document_ids=[FIRST, SECOND], use_original_files=True
    )

    [made] = cast("list[int]", bare["produced_ids"])
    assert [1 in d.tags for d in source.state.documents if d.id == made] == [False]
    assert cast("Json", merged["plan"])["produces"] == "1 new document with 5 pages"
    assert cast("Json", both["plan"])["produces"] == "1 new document with 6 pages"


async def test_the_merge_tool_does_not_claim_to_take_pdfs_only() -> None:
    async with Client(serve(admin(), toolsets=["pdf"])) as client:
        [merge] = [t for t in await client.list_tools() if t.name == "merge_documents"]

    ids = cast("dict[str, Json]", merge.input_schema["properties"])["document_ids"]
    assert "Only PDF files" not in (merge.description or "")
    assert "other files need archive_fallback" in (merge.description or "")
    assert str(ids["description"]).startswith("Ids of the documents to merge")
