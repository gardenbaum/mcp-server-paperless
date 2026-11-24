"""Contract of a whole selection in one request and of rows with chosen fields (decision-69)."""

from typing import TYPE_CHECKING, cast

import pytest
from fakes.accounts import PUBLIC_ASN, PUBLIC_NOTE

from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    NotFound,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.selection import DocumentRow, SelectionFilters

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from mcp_server_paperless.domain.documents import DocumentPage
    from mcp_server_paperless.domain.selection import ReadField

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-69")]

SELECT, ROWS = "DocumentReader.select_documents", "DocumentReader.read_rows"
PUBLIC, PRIVATE = "e2e-public-invoice", "e2e-private-note"
MISSING = 999_999_999
LIMIT = 1001
BYTES = 64 * 1024 * 1024
FIELDS = cast("tuple[ReadField, ...]", tuple(n for n in DocumentRow.model_fields if n != "id"))


async def select(ports: Ports, query: DocumentQuery, limit: int = LIMIT) -> DocumentPage:
    return await ports.documents.select_documents(query, limit=limit, max_bytes=BYTES)


async def seeded(ports: Ports, title: str) -> int:
    query = DocumentQuery(title_contains=title)
    [hit] = (await ports.documents.search_documents(query, page=1, page_size=2)).hits
    return hit.id


async def row(ports: Ports, document: int, *fields: ReadField) -> DocumentRow:
    [found] = await ports.documents.read_rows([document], fields, max_bytes=BYTES)
    return found


async def tag(ports: Ports, name: str) -> int:
    tags = await ports.taxonomy.list_taxonomy("tag")
    return next(item.id for item in tags if item.name == name)


@pytest.mark.contract(SELECT)
async def test_a_selection_gives_what_a_search_gives(backend: Backend) -> None:
    ports = backend.admin
    finance, private = await tag(ports, "e2e-finance"), await tag(ports, "e2e-private")
    found = await ports.documents.search_documents(
        DocumentQuery(title_contains=PUBLIC), page=1, page_size=1
    )
    day, added = found.hits[0].created, found.hits[0].added.date()
    mine = DocumentQuery(title_contains="e2e-p")
    cases: dict[str, dict[str, object]] = {
        "query": {"title_contains": None, "query": "acme"},
        "more_like_id": {
            "title_contains": None,
            "more_like": await seeded(ports, PRIVATE),
        },
        "title_contains": {"title_contains": "e2e-public"},
        "correspondent": {"correspondent": found.hits[0].correspondent},
        "document_type": {"document_type": found.hits[0].document_type},
        "storage_path": {"storage_path": found.hits[0].storage_path},
        "has_correspondent": {"correspondent_isnull": True},
        "has_document_type": {"document_type_isnull": True},
        "has_storage_path": {"storage_path_isnull": False},
        "has_tags": {"tagged": True},
        "tags_all": {"tags_all": (finance, await tag(ports, "e2e-invoices"))},
        "tags_any": {"tags_any": (finance, private)},
        "tags_none": {"tags_none": (private,)},
        "created_from": {"created_from": day},
        "created_to": {"created_to": day},
        "added_from": {"added_from": added},
        "added_to": {"added_to": added},
        "owner": {"owner": (await row(ports, await seeded(ports, PRIVATE), "owner")).owner},
        "inbox": {"inbox": True},
        "custom_field_query": {"custom_field_query": ["e2e-string", "exact", "hello"]},
        "has_duplicates": {"has_duplicates": False},
    }
    backend.rebuild_search_index()

    for name, filters in cases.items():
        query = mine.model_copy(update=filters)
        searched = await ports.documents.search_documents(query, page=1, page_size=100)
        # Not the order: a full-text search ranks, and equal ranks may change places.
        assert {h.id for h in (await select(ports, query)).hits} == {h.id for h in searched.hits}, (
            name
        )
        assert searched.hits, name
    assert set(cases) == set(SelectionFilters.model_fields)


@pytest.mark.contract(SELECT)
async def test_a_selection_tells_the_count_beyond_its_limit(backend: Backend) -> None:
    query = DocumentQuery(title_contains="e2e-p", ordering="title")

    cut, whole = await select(backend.admin, query, 1), await select(backend.admin, query)

    assert [hit.title for hit in cut.hits] == [PRIVATE]
    assert (cut.count, cut.next_page) == (2, 2)
    assert [hit.title for hit in whole.hits] == [PRIVATE, PUBLIC]
    assert (whole.count, whole.next_page) == (2, None)


@pytest.mark.contract(SELECT)
async def test_ids_combine_with_other_filters(backend: Backend) -> None:
    public, private = await seeded(backend.admin, PUBLIC), await seeded(backend.admin, PRIVATE)
    finance = await tag(backend.admin, "e2e-finance")
    query = DocumentQuery(ids=(public, private, MISSING), tags_all=(finance,))

    assert [hit.id for hit in (await select(backend.admin, query)).hits] == [public]


@pytest.mark.contract(SELECT)
async def test_a_tag_that_is_gone_matches_nothing_and_excludes_nothing(backend: Backend) -> None:
    # What a saved view with a deleted tag in a rule becomes.
    mine = DocumentQuery(title_contains="e2e-p")

    async def count(**filters: object) -> int:
        return (await select(backend.admin, mine.model_copy(update=filters))).count

    assert await count(tags_all=(MISSING,)) == 0
    assert await count(tags_any=(MISSING,)) == 0
    assert await count(tags_none=(MISSING,)) == 2


@pytest.mark.contract(SELECT)
async def test_a_restricted_user_selects_only_documents_it_may_see(backend: Backend) -> None:
    mine = DocumentQuery(title_contains="e2e-p")

    assert [hit.title for hit in (await select(backend.reader, mine)).hits] == [PUBLIC]
    with pytest.raises(PermissionDenied):
        _ = await select(backend.blind, mine)
    private = await seeded(backend.admin, PRIVATE)
    with pytest.raises(NotFound, match=f"document {private}"):
        _ = await select(backend.reader, DocumentQuery(more_like=private))


@pytest.mark.contract(SELECT)
async def test_a_selection_paperless_rejects_or_that_is_too_large(backend: Backend) -> None:
    with pytest.raises(Validation, match="e2e-nope"):
        _ = await select(
            backend.admin, DocumentQuery(custom_field_query=["e2e-nope", "exact", "x"])
        )
    with pytest.raises(AnswerTooLarge):
        _ = await backend.admin.documents.select_documents(
            DocumentQuery(title_contains="e2e-p"), limit=LIMIT, max_bytes=50
        )


@pytest.mark.parametrize("name", FIELDS)
@pytest.mark.contract(ROWS)
async def test_a_row_has_the_field_asked_for_and_no_other(
    backend: Backend, name: ReadField
) -> None:
    public = await seeded(backend.admin, PUBLIC)

    found = await row(backend.admin, public, name)

    given = found.model_dump(exclude_none=True)
    # Nothing of the seed sets an owner on the public document.
    assert set(given) == ({"id"} if name == "owner" else {"id", name})
    assert found.id == public


@pytest.mark.contract(ROWS)
async def test_rows_tell_what_a_single_read_tells(backend: Backend) -> None:
    ports = backend.admin
    for title in (PUBLIC, PRIVATE):
        one = await ports.documents.get_document(await seeded(ports, title))

        found = await row(ports, one.id, *FIELDS)

        told, both = found.model_dump(), one.model_dump()
        same = set(FIELDS) - {"notes", "version_count"}
        assert {name: told[name] for name in same} == {name: both[name] for name in same}
        assert found.version_count == len(one.versions)
        assert found.notes == await ports.documents.list_document_notes(one.id)
    public = await row(ports, await seeded(ports, PUBLIC), *FIELDS)
    assert public.archive_serial_number == PUBLIC_ASN
    assert [(note.note, note.user) for note in public.notes or ()] == [(PUBLIC_NOTE, "admin")]
    private = await row(ports, await seeded(ports, PRIVATE), "owner")
    assert private.owner is not None


@pytest.mark.contract(ROWS)
async def test_rows_leave_out_what_the_user_may_not_see(backend: Backend) -> None:
    public, private = await seeded(backend.admin, PUBLIC), await seeded(backend.admin, PRIVATE)
    wanted = [private, public, MISSING]

    rows = await backend.reader.documents.read_rows(wanted, ("title",), max_bytes=BYTES)

    assert [(found.id, found.title) for found in rows] == [(public, PUBLIC)]
    assert await backend.reader.documents.read_rows([], ("title",), max_bytes=BYTES) == ()
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.documents.read_rows([public], ("title",), max_bytes=BYTES)
    with pytest.raises(AnswerTooLarge):
        _ = await backend.admin.documents.read_rows([public], ("title",), max_bytes=10)


@pytest.mark.contract(ROWS)
async def test_rows_hold_notes_also_for_a_user_who_may_not_view_notes(backend: Backend) -> None:
    # The reader lacks `view_note`: the notes endpoint refuses, the list does not.
    public = await seeded(backend.admin, PUBLIC)

    found = await row(backend.reader, public, "notes")

    with pytest.raises(PermissionDenied):
        _ = await backend.reader.documents.list_document_notes(public)
    assert [note.note for note in found.notes or ()] == [PUBLIC_NOTE]


@pytest.mark.contract(ROWS)
async def test_a_row_says_whether_the_user_may_change_it(backend: Backend) -> None:
    [viewed, changed] = backend.new_documents(2)
    grantee = backend.restricted(frozenset({"view_document"}), view=[viewed], change=[changed])

    rows = await grantee.documents.read_rows(
        [viewed, changed], ("user_can_change",), max_bytes=BYTES
    )

    assert {found.id: found.user_can_change for found in rows} == {viewed: False, changed: True}
