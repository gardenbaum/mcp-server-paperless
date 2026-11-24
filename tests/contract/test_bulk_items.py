"""What changing many documents one by one relies on, on the fake and on Paperless (decision-73)."""

import random
from typing import TYPE_CHECKING

import pytest

from mcp_server_paperless.domain.bulk import FieldsChange
from mcp_server_paperless.domain.documents import CustomFieldValue, DocumentPatch, DocumentState
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation

if TYPE_CHECKING:
    from pydantic import JsonValue

    from contract.conftest import Backend, Ports
    from mcp_server_paperless.domain.selection import ReadField

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-73")]

UPDATE = "DocumentWriter.update_document"
ADD = "DocumentWriter.add_note"
FIELDS: tuple[ReadField, ...] = (
    *("title", "created", "archive_serial_number", "owner", "correspondent", "document_type"),
    *("storage_path", "tags", "custom_fields", "user_can_change"),
)
SIZE = 1024 * 1024


async def field(ports: Ports, name: str) -> int:
    return next(f.id for f in await ports.taxonomy.list_taxonomy("custom_field") if f.name == name)


async def values(ports: Ports, document: int) -> dict[int, JsonValue]:
    record = await ports.documents.get_document(document)
    return {value.field: value.value for value in record.custom_fields}


async def put(ports: Ports, document: int, fields: dict[int, JsonValue]) -> None:
    patch = DocumentPatch(
        custom_fields=tuple(CustomFieldValue(field=n, value=v) for n, v in fields.items())
    )
    _ = await ports.document_writes.update_document(document, patch)


@pytest.mark.contract("DocumentReader.read_rows")
async def test_a_row_and_a_read_of_one_document_have_the_same_values(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    text = await field(ports, "e2e-string")
    await put(ports, document, {text: "x"})
    number = random.randrange(1_000_000, 4_000_000)  # noqa: S311 -- a free number, no secret
    patch = DocumentPatch(title="Renamed", archive_serial_number=number)
    _ = await ports.document_writes.update_document(document, patch)

    [row] = await ports.documents.read_rows([document], FIELDS, max_bytes=SIZE)
    record = await ports.documents.get_document(document)

    assert DocumentState.model_validate(row) == DocumentState.model_validate(record)
    assert row.archive_serial_number == number


@pytest.mark.contract(UPDATE)
async def test_sending_a_stored_link_again_restores_a_missing_link_back(backend: Backend) -> None:
    ports = backend.admin
    first, second = backend.new_document(), backend.new_document()
    link, text = await field(ports, "e2e-documentlink"), await field(ports, "e2e-string")
    await put(ports, first, {link: [second]})
    # A bulk edit to a shorter list leaves the other side as it is.
    _ = await ports.bulk_writes.bulk_edit_documents((second,), FieldsChange(values={link: []}))
    assert (await values(ports, first), await values(ports, second)) == (
        {link: [second]},
        {link: []},
    )

    await put(ports, first, {link: [second], text: "x"})

    assert await values(ports, second) == {link: [first]}


@pytest.mark.contract(UPDATE)
async def test_a_shorter_list_takes_the_link_off_the_other_document(backend: Backend) -> None:
    ports = backend.admin
    first, second, third = (backend.new_document() for _ in range(3))
    link = await field(ports, "e2e-documentlink")
    await put(ports, first, {link: [second, third]})

    await put(ports, first, {link: [third]})

    assert await values(ports, second) == {link: []}
    assert await values(ports, third) == {link: [first]}


@pytest.mark.contract(UPDATE)
async def test_a_stored_link_to_a_trashed_document_refuses_a_change_of_the_fields(
    backend: Backend,
) -> None:
    ports = backend.admin
    first, second = backend.new_document(), backend.new_document()
    link, text = await field(ports, "e2e-documentlink"), await field(ports, "e2e-string")
    await put(ports, first, {link: [second]})
    await ports.document_writes.delete_document(second)

    with pytest.raises(Validation, match="don't exist"):
        await put(ports, first, {link: [second], text: "x"})

    assert await values(ports, first) == {link: [second]}


@pytest.mark.contract(UPDATE)
async def test_a_serial_number_is_free_once_its_holder_released_it_but_not_in_the_trash(
    backend: Backend,
) -> None:
    ports = backend.admin
    first, second = backend.new_document(), backend.new_document()
    number = random.randrange(1_000_000, 4_000_000)  # noqa: S311 -- a free number, no secret
    numbered = DocumentPatch(archive_serial_number=number)
    _ = await ports.document_writes.update_document(first, numbered)

    with pytest.raises(Validation, match="archive_serial_number"):
        _ = await ports.document_writes.update_document(second, numbered)
    released = DocumentPatch(archive_serial_number=None)
    _ = await ports.document_writes.update_document(first, released)
    moved = await ports.document_writes.update_document(second, numbered)
    await ports.document_writes.delete_document(second)
    with pytest.raises(Validation, match="in the trash"):
        _ = await ports.document_writes.update_document(first, numbered)

    assert moved.archive_serial_number == number


@pytest.mark.contract(UPDATE)
async def test_a_document_in_the_trash_is_not_found(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    await ports.document_writes.delete_document(document)

    with pytest.raises(NotFound):
        _ = await ports.document_writes.update_document(document, DocumentPatch(title="x"))
    with pytest.raises(NotFound):
        _ = await ports.document_writes.add_note(document, "x")


@pytest.mark.contract(ADD)
async def test_a_note_is_stored_without_the_whitespace_around_it(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()

    older = await ports.document_writes.add_note(document, "older")

    # Inside, the text stays as it is: line ends, tabs, doubled spaces, markup, decomposed letters.
    text = "one\r\n two\t <b>x</b> & e\u0301  \u202f end"
    note = await ports.document_writes.add_note(document, f"\u00a0 {text} \n")

    assert note.note == text
    assert note.id > older.id
    assert await ports.documents.list_document_notes(document) == (older, note)
    [row] = await ports.documents.read_rows([document], ["notes"], max_bytes=SIZE)
    assert row.notes == (older, note)


CHANGER = frozenset({"view_document", "change_document", "view_customfield", "view_uisettings"})


@pytest.mark.contract("DocumentReader.get_signed_in")
async def test_who_is_signed_in_is_known_to_users_who_may_read_their_settings(
    backend: Backend,
) -> None:
    mine = await backend.admin.documents.get_signed_in()
    other = await backend.restricted(CHANGER).documents.get_signed_in()
    hidden = await backend.restricted(frozenset({"view_document"})).documents.get_signed_in()

    assert mine is not None
    assert mine.is_superuser
    assert other is not None
    assert not other.is_superuser
    assert other.id != mine.id
    assert hidden is None


@pytest.mark.contract(UPDATE)
async def test_a_link_to_a_document_that_may_not_be_changed_is_refused(backend: Backend) -> None:
    ports, (first, second) = backend.admin, backend.new_documents(2)
    link, text = await field(ports, "e2e-documentlink"), await field(ports, "e2e-string")
    changer = backend.restricted(CHANGER, view=[second])
    unowned = DocumentPatch(owner=None)
    _ = await ports.document_writes.update_document(first, unowned)

    await put(changer, first, {text: "x"})
    with pytest.raises(PermissionDenied):
        await put(changer, first, {link: [second], text: "x"})

    assert await values(ports, first) == {text: "x"}
    assert link not in await values(ports, second)


@pytest.mark.contract(UPDATE)
async def test_an_owner_gives_a_document_away_and_sees_it_in_the_answer_only(
    backend: Backend,
) -> None:
    ports, (document,) = backend.admin, backend.new_documents(1)
    changer = backend.restricted(CHANGER)
    user = await changer.documents.get_signed_in()
    admin = await ports.documents.get_signed_in()
    assert user is not None
    assert admin is not None
    _ = await ports.document_writes.update_document(document, DocumentPatch(owner=user.id))

    answer = await changer.document_writes.update_document(document, DocumentPatch(owner=admin.id))
    with pytest.raises(NotFound):
        _ = await changer.documents.get_document(document)
    with pytest.raises(NotFound):
        _ = await changer.document_writes.update_document(document, DocumentPatch(title="x"))

    assert answer.owner == admin.id


@pytest.mark.contract(UPDATE)
async def test_another_than_the_owner_may_not_change_the_owner(backend: Backend) -> None:
    ports, (document,) = backend.admin, backend.new_documents(1)
    changer = backend.restricted(CHANGER, change=[document])
    user = await changer.documents.get_signed_in()
    assert user is not None

    with pytest.raises(PermissionDenied):
        _ = await changer.document_writes.update_document(document, DocumentPatch(owner=user.id))

    assert (await ports.documents.get_document(document)).owner != user.id


@pytest.mark.contract(ADD)
async def test_a_note_needs_the_right_to_add_notes_and_to_change_the_document(
    backend: Backend,
) -> None:
    ports, (first, second) = backend.admin, backend.new_documents(2)
    reading = frozenset({"view_document", "change_document", "view_note"})

    viewer = backend.restricted(reading, change=[first])
    assert await viewer.documents.list_document_notes(first) == ()
    with pytest.raises(PermissionDenied):
        _ = await viewer.document_writes.add_note(first, "x")
    adder = backend.restricted(reading | {"add_note"}, view=[second])
    assert await adder.documents.list_document_notes(second) == ()
    with pytest.raises(PermissionDenied):
        _ = await adder.document_writes.add_note(second, "x")

    assert await ports.documents.list_document_notes(first) == ()
    assert await ports.documents.list_document_notes(second) == ()
