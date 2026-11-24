"""The pdf toolset through the assembled server on the E2E Paperless, on documents of its own.

Titles start with `plmcp23-`. Every document and version a test made is deleted again at its end
and its trash entry emptied, so nothing is left on the shared stack, also when a test fails. The
PDFs have a text layer per page, so OCR has little to do.
"""

import asyncio
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, live_in_trash
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import (
    ENCRYPTED_PASSWORD,
    ENCRYPTED_PDF,
    consume,
    consume_text,
    num,
    pdf_pages,
    results,
)
from e2e.uploading import Scope as People
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


class Scope:
    """The documents of one test: every id it made, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.ids: list[int] = []

    def pdf(self, pages: int) -> int:
        title = f"plmcp23-{uuid.uuid4().hex}"
        texts = [f"{title} page {n}" for n in range(1, pages + 1)]
        _, created = consume(self.stack.admin, title, pdf_pages(texts))
        assert created is not None
        self.ids.append(created)
        return created

    def locked(self) -> int:
        title = f"plmcp23-{uuid.uuid4().hex}"
        _, created = consume(self.stack.admin, title, ENCRYPTED_PDF)
        assert created is not None
        self.ids.append(created)
        return created

    def made(self, result: Json) -> list[int]:
        """The ids a result produced, tracked for the cleanup."""
        produced = [num(n) for n in cast("list[object]", result["produced_ids"])]
        self.ids.extend(produced)
        return produced


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


def server(stack: Stack, *, read_only: bool = False) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, stack.admin_token),
        server_settings(toolsets=["pdf"], read_only=read_only),
    )


async def run(stack: Stack, tool: str, **arguments: object) -> Json:
    async with Client(server(stack)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(stack: Stack, tool: str, **arguments: object) -> str:
    async with Client(server(stack)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def document(stack: Stack, number: int) -> Json:
    return cast("Json", stack.admin.get(f"/api/documents/{number}/").raise_for_status().json())


def versions(stack: Stack, number: int) -> list[int]:
    return [
        num(cast("Json", v)["id"])
        for v in cast("list[object]", document(stack, number)["versions"])
    ]


async def test_the_main_flow_rotates_cuts_splits_and_merges_a_document(
    stack: Stack, scope: Scope
) -> None:
    source = scope.pdf(3)

    rotated = await run(
        stack, "rotate_documents", document_ids=[source], degrees=90, wait_seconds=30
    )
    assert (rotated["state"], rotated["wait"]) == ("success", "finished")
    [version] = scope.made(rotated)
    assert versions(stack, source) == [version, source]

    unconfirmed = await refused(stack, "delete_pages", document_id=source, pages="2")
    assert "Nothing was changed" in unconfirmed
    assert f"delete page 2 of document {source}" in unconfirmed
    assert len(versions(stack, source)) == 2
    too_far = await refused(stack, "delete_pages", document_id=source, pages="4", confirm=True)
    assert "Page 4 does not exist: the document has 3 pages" in too_far
    cut = await run(
        stack, "delete_pages", document_id=source, pages="2", confirm=True, wait_seconds=30
    )
    assert cut["state"] == "success"
    [shorter] = scope.made(cut)
    assert document(stack, source)["page_count"] == 2
    assert versions(stack, source) == [shorter, version, source]

    split = await run(
        stack, "split_document", document_id=source, parts=["1", "2"], wait_seconds=30
    )
    assert split["state"] == "success"
    halves = scope.made(split)
    assert [document(stack, n)["page_count"] for n in halves] == [1, 1]

    merged = await run(
        stack,
        "merge_documents",
        document_ids=halves,
        metadata_document_id=halves[0],
        wait_seconds=30,
    )
    assert merged["state"] == "success"
    [together] = scope.made(merged)
    assert document(stack, together)["page_count"] == 2
    assert [live_in_trash(stack, n) for n in halves] == [False, False]


async def test_deleting_originals_needs_confirm_and_sends_them_to_the_trash(
    stack: Stack, scope: Scope
) -> None:
    first, second = scope.pdf(1), scope.pdf(1)

    unconfirmed = await refused(
        stack, "merge_documents", document_ids=[first, second], delete_originals=True
    )
    assert "Nothing was changed" in unconfirmed
    assert not live_in_trash(stack, first)

    merged = await run(
        stack,
        "merge_documents",
        document_ids=[first, second],
        delete_originals=True,
        confirm=True,
        wait_seconds=30,
    )

    [together] = scope.made(merged)
    assert (merged["state"], merged["trashed_ids"]) == ("success", [first, second])
    assert document(stack, together)["page_count"] == 2
    assert live_in_trash(stack, first)
    assert live_in_trash(stack, second)


async def test_a_replaced_original_goes_once_its_parts_exist_and_hands_its_number_over(
    stack: Stack, scope: Scope
) -> None:
    source, number = scope.pdf(3), uuid.uuid4().int % 1_000_000 + 8_000_000
    _ = stack.admin.patch(
        f"/api/documents/{source}/", json={"archive_serial_number": number}
    ).raise_for_status()
    shorter = {"document_id": source, "pages": "2", "output": "replace_original"}

    preview = await run(stack, "delete_pages", **shorter, dry_run=True)
    unwaited = await run(stack, "delete_pages", **shorter, confirm=True, wait_seconds=0)
    await run_until_done(stack, unwaited, scope)
    assert (preview["state"], preview["dry_run"]) == ("not_started", True)
    assert (unwaited["trashed_ids"], live_in_trash(stack, source)) == ([], False)
    assert f"delete_document(document_id={source})" in str(unwaited["next"])
    replaced = await run(stack, "delete_pages", **shorter, confirm=True)

    [made] = scope.made(replaced)
    assert (replaced["state"], replaced["trashed_ids"]) == ("success", [source])
    assert live_in_trash(stack, source)
    assert document(stack, made)["page_count"] == 2
    assert document(stack, made)["archive_serial_number"] == number


async def run_until_done(stack: Stack, result: Json, scope: Scope) -> None:
    """Follow the tasks of a result that was not waited for, for the cleanup."""
    async with Client(
        create_server(
            paperless_settings(stack.base_url, stack.admin_token),
            server_settings(toolsets=["core"]),
        )
    ) as client:
        for task in cast("list[Json]", result["tasks"]):
            done = await client.call_tool(
                "get_task", {"task_id": task["task_id"], "wait_seconds": 30}
            )
            ids = cast("list[object]", cast("Json", done.structured_content)["document_ids"])
            scope.ids.extend(num(n) for n in ids)


async def test_a_document_longer_than_its_first_file_is_edited_on_its_last_page(
    stack: Stack, scope: Scope
) -> None:
    source = scope.pdf(1)
    doubled = [{"page": 1}, {"page": 1}, {"page": 1, "rotate": 90}]
    longer = await run(
        stack,
        "edit_pdf",
        document_id=source,
        pages=doubled,
        output="new_version",
        wait_seconds=30,
    )
    [version] = scope.made(longer)
    assert document(stack, source)["page_count"] == 3

    last = await run(stack, "edit_pdf", document_id=source, pages=[{"page": 3}], wait_seconds=30)
    [page] = scope.made(last)
    assert document(stack, page)["page_count"] == 1
    back = await refused(
        stack,
        "edit_pdf",
        document_id=source,
        pages=[{"page": 1}],
        output="new_version",
        version_id=source,
    )
    assert f"from its older version {source}" in back
    assert versions(stack, source) == [version, source]


async def test_a_merge_refuses_what_paperless_would_leave_out(stack: Stack, scope: Scope) -> None:
    first, locked = scope.pdf(1), scope.locked()
    text = consume_text(stack.admin, f"plmcp23-{uuid.uuid4().hex}")
    scope.ids.append(text)

    encrypted = await refused(stack, "merge_documents", document_ids=[first, locked])
    plain = await refused(
        stack, "merge_documents", document_ids=[first, text], archive_fallback=True
    )

    assert f"Document {locked} cannot be merged" in encrypted
    assert f"Document {text} is not a PDF (text/plain) and has no archive PDF" in plain


async def test_edit_pdf_reorders_and_turns_pages_into_a_new_document(
    stack: Stack, scope: Scope
) -> None:
    source = scope.pdf(2)

    edited = await run(
        stack,
        "edit_pdf",
        document_id=source,
        pages=[{"page": 2}, {"page": 1, "rotate": 180}],
        wait_seconds=30,
    )

    [made] = scope.made(edited)
    assert document(stack, made)["page_count"] == 2
    assert document(stack, source)["page_count"] == 2


async def test_a_password_is_removed_and_the_unlocked_copy_is_read(
    stack: Stack, scope: Scope
) -> None:
    locked = scope.locked()
    wrong = await refused(stack, "remove_document_password", document_id=locked, password="nope")
    assert "password" in wrong
    assert "nope" not in wrong

    result = await run(
        stack,
        "remove_document_password",
        document_id=locked,
        password=ENCRYPTED_PASSWORD,
        output="new_version",
        wait_seconds=30,
    )

    assert result["state"] == "success"
    [version] = scope.made(result)
    assert versions(stack, locked) == [version, locked]
    assert "secret page one" in str(document(stack, locked)["content"])
    assert ENCRYPTED_PASSWORD not in str(result)


async def test_reprocessing_needs_confirm_and_starts_a_task(stack: Stack, scope: Scope) -> None:
    number = consume_text(stack.admin, f"plmcp23-{uuid.uuid4().hex}")
    scope.ids.append(number)

    unconfirmed = await refused(stack, "reprocess_documents", document_ids=[number])
    assert "Nothing was changed" in unconfirmed

    result = await run(
        stack, "reprocess_documents", document_ids=[number], confirm=True, wait_seconds=30
    )

    assert result["state"] == "success"
    [task] = cast("list[Json]", result["tasks"])
    assert task["document_ids"] == []


async def test_a_user_who_may_not_view_tasks_keeps_the_original_and_is_told_how_to_go_on(
    stack: Stack, scope: Scope
) -> None:
    people = People(stack)
    source = scope.pdf(2)
    try:
        token = people.user("view_document", "change_document", "add_document", "delete_document")
        _ = stack.admin.patch(f"/api/documents/{source}/", json={"owner": None}).raise_for_status()
        answer = await people.call(
            "delete_pages",
            token,
            document_id=source,
            pages="1",
            output="replace_original",
            confirm=True,
        )
        result = cast("Json", answer.structured_content)

        assert (result["state"], result["trashed_ids"]) == ("unknown", [])
        assert "search_documents" in str(result["next"])
        assert "delete_document" not in str(result["next"])
        assert not live_in_trash(stack, source)
        title = str(document(stack, source)["title"])
        made: list[int] = []
        for _ in range(60):
            found = results(stack.admin.get("/api/documents/", params={"title__iexact": title}))
            made = [num(row["id"]) for row in found if row["id"] != source]
            if made:
                break
            await asyncio.sleep(1)
        scope.ids.extend(made)
        assert [document(stack, n)["page_count"] for n in made] == [1]
    finally:
        people.clean_up()


LETTER: Json = {"title": "plmcp79 letter", "tags": ["e2e-finance"], "created": "2026-01-02"}
INVOICE: Json = {"document_type": "e2e-invoice", "correspondent": "e2e-acme"}


async def test_a_split_with_metadata_replaces_the_original_once_every_part_has_its_own(
    stack: Stack, scope: Scope
) -> None:
    source = scope.pdf(3)
    title = str(document(stack, source)["title"])
    call: Json = {
        "document_id": source,
        "parts": ["1", "2-3"],
        "part_metadata": [LETTER, INVOICE],
        "output": "replace_original",
    }

    preview = await run(stack, "split_document", **call, dry_run=True)
    refusal = await refused(stack, "split_document", **call)
    unknown = await refused(
        stack,
        "split_document",
        document_id=source,
        parts=["1", "2"],
        part_metadata=[{}, {"tags": ["plmcp79-no-such-tag"]}],
    )
    assert "part 1 (page 1) gets title 'plmcp79 letter'" in str(preview["plan"])
    assert "part 2 (pages 2-3) gets document type 'e2e-invoice'" in refusal
    assert "No tag is named 'plmcp79-no-such-tag'" in unknown
    assert not live_in_trash(stack, source)
    split = await run(stack, "split_document", **call, confirm=True)

    _ = scope.made(split)
    assert (split["state"], split["original"]) == ("success", "trashed"), split
    assert live_in_trash(stack, source)
    parts = cast("list[Json]", split["parts"])
    assert [p["metadata"] for p in parts] == ["set", "set"]
    letter, invoice = (document(stack, num(p["document_id"])) for p in parts)
    tags = {row["name"]: row["id"] for row in results(stack.admin.get("/api/tags/"))}
    assert (letter["title"], letter["tags"]) == ("plmcp79 letter", [tags["e2e-finance"]])
    assert (letter["created"], letter["page_count"]) == ("2026-01-02", 1)
    assert (invoice["title"], invoice["page_count"]) == (title, 2)
    assert invoice["document_type"] is not None
    assert invoice["correspondent"] is not None


async def test_pages_are_extracted_and_then_removed_from_the_document(
    stack: Stack, scope: Scope
) -> None:
    source = scope.pdf(3)

    copied = await run(stack, "extract_pages", document_id=source, pages="3,1", wait_seconds=30)
    [first] = scope.made(copied)
    assert (copied["original"], document(stack, first)["page_count"]) == ("kept", 2)
    assert versions(stack, source) == [source]
    call: Json = {"document_id": source, "pages": "2", "remove": True, "metadata": LETTER}
    preview = await run(stack, "extract_pages", **call, dry_run=True)
    refusal = await refused(stack, "extract_pages", **call)
    every = await refused(stack, "extract_pages", document_id=source, pages="1-", remove=True)
    assert "without these pages" in str(preview["plan"])
    assert f"extract page 2 of document {source}" in refusal
    assert "Removing every page leaves nothing" in every
    assert versions(stack, source) == [source]
    removed = await run(stack, "extract_pages", **call, confirm=True)

    made = scope.made(removed)
    assert (removed["state"], removed["original"]) == ("success", "shortened"), removed
    assert len(made) == 2
    new, shorter = document(stack, made[0]), document(stack, source)
    assert (new["title"], new["page_count"]) == ("plmcp79 letter", 1)
    assert (shorter["page_count"], versions(stack, source)) == (2, [made[1], source])
    assert not live_in_trash(stack, source)


async def test_parts_of_a_document_with_a_date_field_are_found_by_their_file_names(
    stack: Stack, scope: Scope
) -> None:
    people = People(stack)
    source = scope.pdf(2)
    fields = {row["name"]: row["id"] for row in results(stack.admin.get("/api/custom_fields/"))}
    value = {"field": fields["e2e-date"], "value": "2026-03-04"}
    _ = stack.admin.patch(
        f"/api/documents/{source}/", json={"owner": None, "custom_fields": [value]}
    ).raise_for_status()
    try:
        token = people.user(
            *("view_document", "change_document", "add_document", "delete_document"),
            *("view_paperlesstask", "view_uisettings", "view_customfield", "view_tag"),
            *("view_documenttype", "view_correspondent", "view_storagepath"),
        )
        answer = await people.call(
            "split_document",
            token,
            document_id=source,
            parts=["1", "2"],
            part_metadata=[LETTER, {"title": "plmcp79 second"}],
            output="replace_original",
            confirm=True,
        )
        result = cast("Json", answer.structured_content)

        # DIAGNOSTIC PLMCP-79.3
        import httpx2

        from e2e.diag793 import dump
        from mcp_server_paperless.paperless.auth import StaticTokenProvider
        from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy

        async with httpx2.AsyncClient(base_url=stack.base_url) as http:
            client = PaperlessClient(
                http, StaticTokenProvider(token), retry=RetryPolicy(), upload_timeout=10.0
            )
            rows = await client.get(
                "/api/tasks/",
                __import__("e2e.diag793").diag793.Raw,
                params={"task_type": "bulk_update", "ordering": "-date_created"},
            )
            newest = next(r for r in rows.root["results"] if r["owner"] is not None)
            await dump(client, newest["task_id"])

        made = scope.made(result)
        assert (result["state"], result["original"]) == ("success", "trashed"), result["message"]
        assert live_in_trash(stack, source)
        assert [t["task_id"] for t in cast("list[Json]", result["tasks"])] == [None, None]
        assert [document(stack, n)["title"] for n in made] == ["plmcp79 letter", "plmcp79 second"]
        assert [document(stack, n)["custom_fields"] for n in made] == [[value], [value]]
    finally:
        people.clean_up()


async def test_parts_nobody_can_follow_keep_their_metadata_and_the_original(
    stack: Stack, scope: Scope
) -> None:
    people = People(stack)
    source = scope.pdf(2)
    _ = stack.admin.patch(f"/api/documents/{source}/", json={"owner": None}).raise_for_status()
    title = str(document(stack, source)["title"])
    try:
        token = people.user(
            *("view_document", "change_document", "add_document", "delete_document", "view_tag")
        )
        answer = await people.call(
            "split_document",
            token,
            document_id=source,
            parts=["1", "2"],
            part_metadata=[{"title": "plmcp79 first"}, {}],
            output="replace_original",
            confirm=True,
        )
        result = cast("Json", answer.structured_content)

        parts = cast("list[Json]", result["parts"])
        assert [(p["state"], p["metadata"]) for p in parts] == [
            ("unknown", "not_set"),
            ("unknown", "not_requested"),
        ]
        assert 'title="plmcp79 first"' in str(parts[0]["next"])
        assert (result["original"], live_in_trash(stack, source)) == ("kept", False)
        made: list[int] = []
        for _ in range(60):
            found = results(stack.admin.get("/api/documents/", params={"title__iexact": title}))
            made = [num(row["id"]) for row in found if row["id"] != source]
            if len(made) == 2:
                break
            await asyncio.sleep(1)
        scope.ids.extend(made)
        assert len(made) == 2
    finally:
        people.clean_up()
