"""Bulk calls whose set changes, and bulk calls that are rate limited, on the fake and live.

The tools run as they are on the contract backends (contract/serving.py): confirmations and the end
of a call are no port methods of their own. HTTP 429 begins at a known request of a call, after
which the backend answers nothing else and notes what is still asked (tests/ratelimit.py).
"""

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest
from confirmation import YES, answering
from interleaving import before

from contract.conftest import SCRATCH_TAG
from contract.serving import output, refusal
from contract.test_workflow_runs import assign, workflow
from mcp_server_paperless.domain.documents import DocumentPatch
from mcp_server_paperless.domain.taxonomy import TaxonomyValues
from mcp_server_paperless.tools.confirmation import MAX_DIALOGS

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from contract.serving import Json

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-71"),
    pytest.mark.decision("decision-73"),
]

NOTE = "plmcp119 paid"


async def new_tag(ports: Ports) -> tuple[int, str]:
    name = f"{SCRATCH_TAG}{uuid.uuid4().hex}"
    made = await ports.taxonomy_writes.create_taxonomy_item("tag", name, TaxonomyValues())
    return made.id, name


async def tags(ports: Ports, document: int) -> tuple[int, ...]:
    return (await ports.documents.get_document(document)).tags


async def tag_with(ports: Ports, document: int, *more: int) -> None:
    patch = DocumentPatch(tags=(*await tags(ports, document), *more))
    _ = await ports.document_writes.update_document(document, patch)


async def tagged(ports: Ports, documents: list[int], tag: int) -> list[int]:
    return [number for number in documents if tag in await tags(ports, number)]


async def title(ports: Ports, document: int) -> str:
    return (await ports.documents.get_document(document)).title


async def notes(ports: Ports, document: int) -> list[str]:
    return [note.note for note in await ports.documents.list_document_notes(document)]


def statuses(answer: Json) -> list[object]:
    return [row["status"] for row in cast("list[Json]", answer["documents"])]


def writing(backend: Backend, method: str, at: int) -> Ports:
    """The limited admin, whose limit begins with call ``at`` of a write of documents."""
    ports, begin = backend.limited, backend.rate_limit.begin
    return replace(ports, document_writes=before(ports.document_writes, method, begin, at=at))


# --- the rate


async def test_a_rate_limit_ends_update_documents_and_nothing_more_is_sent(
    backend: Backend,
) -> None:
    documents = backend.new_documents(3)
    old = [await title(backend.admin, number) for number in documents]
    ports = writing(backend, "update_document", 2)

    answer = await output(
        ports,
        "update_documents",
        documents=[{"document_id": n, "title": f"plmcp119 {n}"} for n in documents],
        confirm=True,
    )

    assert statuses(answer) == ["changed", "failed", "not_started"]
    assert answer["left"] == documents[1:]
    assert "Stopped: " in str(answer["message"])
    assert len(backend.rate_limit.asked) == 1, backend.rate_limit.asked
    assert [await title(backend.admin, number) for number in documents] == [
        f"plmcp119 {documents[0]}",
        *old[1:],
    ]


async def test_a_rate_limit_on_the_read_before_a_change_ends_the_call_too(
    backend: Backend,
) -> None:
    documents = backend.new_documents(3)
    old = [await title(backend.admin, number) for number in documents]
    ports, begin = backend.limited, backend.rate_limit.begin
    ports = replace(ports, documents=before(ports.documents, "get_document", begin, at=2))

    answer = await output(
        ports,
        "update_documents",
        documents=[{"document_id": n, "title": f"plmcp119 {n}"} for n in documents],
        confirm=True,
    )

    assert statuses(answer) == ["changed", "failed", "not_started"]
    # A read is tried again by the client (decision-70), nothing else is asked.
    assert len(set(backend.rate_limit.asked)) == 1, backend.rate_limit.asked
    assert [await title(backend.admin, number) for number in documents] == [
        f"plmcp119 {documents[0]}",
        *old[1:],
    ]


async def test_a_rate_limit_ends_add_notes_and_nothing_more_is_sent(backend: Backend) -> None:
    documents = backend.new_documents(3)
    ports = writing(backend, "add_note", 2)

    answer = await output(ports, "add_notes", document_ids=documents, note=NOTE, confirm=True)

    assert statuses(answer) == ["added", "failed", "not_started"]
    assert answer["left"] == documents[1:]
    assert len(backend.rate_limit.asked) == 1, backend.rate_limit.asked
    assert [await notes(backend.admin, number) for number in documents] == [[NOTE], [], []]


async def test_a_rate_limit_on_a_bulk_edit_changes_nothing_and_nothing_more_is_sent(
    backend: Backend,
) -> None:
    documents = backend.new_documents(2)
    tag, name = await new_tag(backend.admin)
    ports, begin = backend.limited, backend.rate_limit.begin
    ports = replace(ports, bulk_writes=before(ports.bulk_writes, "bulk_edit_documents", begin))

    told = await refusal(
        ports,
        "bulk_edit_documents",
        document_ids=documents,
        operation={"op": "add_tags", "tags": [name]},
        confirm=True,
    )

    assert "429" in told
    assert len(backend.rate_limit.asked) == 1, backend.rate_limit.asked
    assert await tagged(backend.admin, documents, tag) == []


async def test_a_rate_limit_on_run_workflow_runs_nothing_and_nothing_more_is_sent(
    backend: Backend,
) -> None:
    documents = backend.new_documents(2)
    tag, _ = await new_tag(backend.admin)
    flow = await workflow(backend.admin, assign(assign_tags=[tag]))
    ports, begin = backend.limited, backend.rate_limit.begin
    ports = replace(ports, workflow_writes=before(ports.workflow_writes, "run_workflow", begin))

    told = await refusal(
        ports, "run_workflow", workflow=flow.name, document_ids=documents, confirm=True
    )

    assert "429" in told
    assert len(backend.rate_limit.asked) == 1, backend.rate_limit.asked
    assert await tagged(backend.admin, documents, tag) == []


async def test_a_rate_limit_ends_reading_many_documents_and_names_the_rest(
    backend: Backend,
) -> None:
    documents = backend.new_documents(3)
    ports, begin = backend.limited, backend.rate_limit.begin
    ports = replace(ports, documents=before(ports.documents, "get_document_content", begin, at=2))

    answer = await output(
        ports, "read_documents", document_ids=documents, fields=["title", "content"]
    )

    assert answer["left"] == documents[1:]
    assert any("Reading text stopped" in str(n) for n in cast("list[object]", answer["notices"]))
    assert len(set(backend.rate_limit.asked)) == 1, backend.rate_limit.asked


# --- a changed set between preview and confirm


async def test_ids_that_no_longer_match_their_filter_change_nothing(backend: Backend) -> None:
    ports, documents = backend.admin, backend.new_documents(2)
    chosen, name = await new_tag(ports)
    added, _ = await new_tag(ports)
    for number in documents:
        await tag_with(ports, number, chosen)
    filters = {"tags_all": [name]}
    operation = {"op": "add_tags", "tags": [added]}

    preview = await output(
        ports, "bulk_edit_documents", filters=filters, operation=operation, dry_run=True
    )
    assert set(cast("list[int]", preview["selected_ids"])) == set(documents)
    await ports.document_writes.delete_document(documents[0])
    told = await refusal(
        ports,
        "bulk_edit_documents",
        filters=filters,
        operation=operation,
        document_ids=documents,
        confirm=True,
    )

    assert str(documents[0]) in told
    assert added not in await tags(ports, documents[1])


async def test_a_document_that_joins_under_the_dialog_is_asked_about_again(
    backend: Backend,
) -> None:
    ports, documents, seen = backend.admin, backend.new_documents(2), list[str]()
    chosen, name = await new_tag(ports)
    added, _ = await new_tag(ports)
    await tag_with(ports, documents[0], chosen)

    async def joins() -> None:
        if len(seen) == 1:
            await tag_with(ports, documents[1], chosen)

    answer = await output(
        ports,
        "bulk_edit_documents",
        handler=answering("accept", YES, seen=seen, before=joins),
        filters={"tags_all": [name]},
        operation={"op": "add_tags", "tags": [added]},
        dry_run=False,
    )

    second = await title(ports, documents[1])
    assert [second in message for message in seen] == [False, True]
    assert set(cast("list[int]", answer["selected_ids"])) == set(documents)
    assert await tagged(ports, documents, added) == documents


async def test_a_set_that_keeps_changing_is_not_acted_on(backend: Backend) -> None:
    ports, seen = backend.admin, list[str]()
    documents = backend.new_documents(MAX_DIALOGS + 1)
    chosen, name = await new_tag(ports)
    added, _ = await new_tag(ports)
    await tag_with(ports, documents[0], chosen)

    async def joins() -> None:
        await tag_with(ports, documents[len(seen)], chosen)

    _ = await refusal(
        ports,
        "bulk_edit_documents",
        mode="legacy",
        handler=answering("accept", YES, seen=seen, before=joins),
        filters={"tags_all": [name]},
        operation={"op": "add_tags", "tags": [added]},
        dry_run=False,
    )

    assert len(seen) == MAX_DIALOGS
    assert await tagged(ports, documents, added) == []


async def test_notes_by_filter_are_asked_about_again_when_the_set_changes(
    backend: Backend,
) -> None:
    ports, documents, seen = backend.admin, backend.new_documents(2), list[str]()
    chosen, name = await new_tag(ports)
    await tag_with(ports, documents[0], chosen)

    async def joins() -> None:
        if len(seen) == 1:
            await tag_with(ports, documents[1], chosen)

    answer = await output(
        ports,
        "add_notes",
        handler=answering("accept", YES, seen=seen, before=joins),
        filters={"tags_all": [name]},
        note=NOTE,
    )

    assert len(seen) == 2
    assert statuses(answer) == ["added", "added"]
    assert [await notes(ports, number) for number in documents] == [[NOTE], [NOTE]]


async def test_a_value_that_changes_under_the_dialog_is_asked_about_again(
    backend: Backend,
) -> None:
    ports, (document,), seen = backend.admin, backend.new_documents(1), list[str]()

    async def renamed() -> None:
        if len(seen) == 1:
            patch = DocumentPatch(title="plmcp119 meanwhile")
            _ = await ports.document_writes.update_document(document, patch)

    _ = await output(
        ports,
        "update_documents",
        handler=answering("accept", YES, seen=seen, before=renamed),
        documents=[{"document_id": document, "title": "plmcp119 asked"}],
    )

    assert len(seen) == 2
    assert "plmcp119 meanwhile" in seen[1]
    assert await title(ports, document) == "plmcp119 asked"


async def test_a_document_that_changes_after_the_confirmation_is_left_alone(
    backend: Backend,
) -> None:
    ports, (first, second) = backend.admin, backend.new_documents(2)

    async def renamed(*_args: object) -> None:
        patch = DocumentPatch(title="plmcp119 meanwhile")
        _ = await ports.document_writes.update_document(second, patch)

    acting = replace(
        ports, document_writes=before(ports.document_writes, "update_document", renamed)
    )
    answer = await output(
        acting,
        "update_documents",
        documents=[{"document_id": n, "title": f"plmcp119 {n}"} for n in (first, second)],
        confirm=True,
    )

    assert statuses(answer) == ["changed", "changed_meanwhile"]
    assert answer["left"] == [second]
    assert await title(ports, second) == "plmcp119 meanwhile"
