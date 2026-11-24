"""Contract of what the duplicate check reads: the same assertions on the fake and on 3.5.1.

Every test works on files of its own with unique bytes. The facts come from the source of 3.5.1:
the checksum is the SHA-256 of the file; the checksum filter knows the root file of documents
that are not in the trash; `duplicate_documents` compares root and archive files of documents,
includes the trash and what is shared with the user, and knows no versions.
"""

import hashlib
import uuid
from typing import TYPE_CHECKING

import pytest

from contract.test_document_upload import ended
from contract.test_document_versions import add_version
from contract.test_trash import find
from mcp_server_paperless.domain.documents import DocumentPatch, DocumentQuery, DocumentUpload
from mcp_server_paperless.domain.errors import AnswerTooLarge, NotFound, PermissionDenied

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from mcp_server_paperless.domain.versions import VersionRow

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-47")]

SEARCH = "DocumentReader.search_documents"
ROOT = "DocumentReader.get_root"
DUPLICATES = "DocumentReader.get_duplicates"
FIND = "TrashReader.find_in_trash"
ROWS = "DocumentReader.list_versions"
MISSING = 999_999
MIB = 1024 * 1024
SEEDED = 1


def unique() -> bytes:
    return f"plmcp77 {uuid.uuid4().hex}".encode()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def uploaded(backend: Backend, content: bytes) -> int:
    """A text document of the admin with this file."""
    name = f"plmcp77-{uuid.uuid4().hex}"
    upload = DocumentUpload(
        filename=f"{name}.txt", mime_type="text/plain", content=content, title=name
    )
    status = await ended(backend, await backend.admin.document_writes.upload_document(upload))
    assert status.state == "success"
    [document] = status.document_ids
    return document


async def with_checksum(ports: Ports, checksum: str) -> set[int]:
    found = await ports.documents.search_documents(
        DocumentQuery(checksum=checksum), page=1, page_size=25
    )
    assert found.count == len(found.hits)
    return {hit.id for hit in found.hits}


async def row_of(ports: Ports, document: int) -> VersionRow:
    """The row of a document that has just changed, so it is among the first."""
    page = await ports.documents.list_versions(page=1, page_size=100, recent=True)
    [row] = [r for r in page.rows if r.id == document]
    return row


@pytest.mark.contract(SEARCH)
async def test_the_checksum_filter_finds_the_root_file_of_live_documents_in_any_case(
    backend: Backend,
) -> None:
    content, other = unique(), unique()
    first, second = [await uploaded(backend, content) for _ in range(2)]

    assert await with_checksum(backend.admin, sha256(content)) == {first, second}
    assert await with_checksum(backend.admin, sha256(content).upper()) == {first, second}
    assert await with_checksum(backend.admin, sha256(other)) == set()

    _ = await add_version(backend, first, other.decode())
    await backend.admin.document_writes.delete_document(second)

    assert await with_checksum(backend.admin, sha256(content)) == {first}
    assert await with_checksum(backend.admin, sha256(other)) == set()


@pytest.mark.contract(SEARCH)
async def test_the_checksum_filter_tells_nothing_of_hidden_documents(backend: Backend) -> None:
    content = unique()
    _ = await uploaded(backend, content)

    assert await with_checksum(backend.reader, sha256(content)) == set()
    with pytest.raises(PermissionDenied):
        _ = await with_checksum(backend.blind, sha256(content))


@pytest.mark.contract(ROWS)
async def test_the_rows_hold_the_checksums_of_live_versions_the_last_changed_first(
    backend: Backend,
) -> None:
    content, newer = unique(), unique()
    document = await uploaded(backend, content)
    _ = await uploaded(backend, unique())

    version = await add_version(backend, document, newer.decode())
    row = await row_of(backend.admin, document)

    record = await backend.admin.documents.get_document(document)
    assert (row.title, row.versions) == (record.title, record.versions)
    assert [(v.id, v.is_root, v.checksum) for v in row.versions] == [
        (version, False, sha256(newer)),
        (document, True, sha256(content)),
    ]
    _ = await backend.admin.document_writes.delete_version(document, version)
    page = await backend.admin.documents.list_versions(page=1, page_size=500)
    assert all(v.id != version for r in page.rows for v in r.versions)


@pytest.mark.contract(ROWS)
async def test_the_rows_are_paged_and_only_those_the_user_may_see(backend: Backend) -> None:
    document = await uploaded(backend, unique())

    first = await backend.admin.documents.list_versions(page=1, page_size=1)

    assert (len(first.rows), first.next_page) == (1, 2)
    assert first.count >= 2
    seen = await backend.reader.documents.list_versions(page=1, page_size=100)
    assert document not in {r.id for r in seen.rows}
    with pytest.raises(NotFound):
        _ = await backend.admin.documents.list_versions(page=first.count + 1000, page_size=1)
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.documents.list_versions(page=1, page_size=1)


@pytest.mark.contract(ROWS)
async def test_an_edit_does_not_move_a_document_unless_the_last_changed_are_asked_for(
    backend: Backend,
) -> None:
    first, second = [await uploaded(backend, unique()) for _ in range(2)]
    patch = DocumentPatch(title=f"plmcp77-{uuid.uuid4().hex}")
    _ = await backend.admin.document_writes.update_document(first, patch)

    async def order(*, recent: bool) -> list[int]:
        page = await backend.admin.documents.list_versions(page=1, page_size=100, recent=recent)
        return [r.id for r in page.rows if r.id in {first, second}]

    assert await order(recent=False) == [second, first]
    assert await order(recent=True) == [first, second]


@pytest.mark.contract("DocumentReader.get_version_checksum")
async def test_the_checksum_of_a_version_also_of_a_deleted_one(backend: Backend) -> None:
    content = unique()
    document = await uploaded(backend, unique())
    version = await add_version(backend, document, content.decode())

    async def read(ports: Ports) -> str | None:
        return await ports.documents.get_version_checksum(document, version)

    assert await read(backend.admin) == sha256(content)
    with pytest.raises(PermissionDenied):
        _ = await read(backend.reader)
    _ = await backend.admin.document_writes.delete_version(document, version)
    assert await read(backend.admin) == sha256(content)
    with pytest.raises(NotFound):
        _ = await backend.admin.documents.get_version_checksum(document, MISSING)


@pytest.mark.contract("TrashReader.list_trash")
async def test_a_document_in_the_trash_has_the_checksum_of_its_root_file(backend: Backend) -> None:
    content, deleted = unique(), unique()
    document = await uploaded(backend, content)
    ports = backend.admin
    version = await add_version(backend, document, deleted.decode())
    _ = await ports.document_writes.delete_version(document, version)

    # The entry of a version does not tell its checksum; the metadata of the live document does.
    entry = (await find(ports, {version}))[version]
    assert (entry.version_of, entry.checksum) == (document, None)
    assert await ports.documents.get_version_checksum(document, version) == sha256(deleted)

    await ports.document_writes.delete_document(document)

    entry = (await find(ports, {document}))[document]
    assert (entry.version_of, entry.checksum) == (None, sha256(content))
    with pytest.raises(NotFound):
        _ = await ports.documents.get_version_checksum(document, version)


@pytest.mark.contract(DUPLICATES)
async def test_the_duplicates_are_other_documents_with_the_file_the_trash_included(
    backend: Backend,
) -> None:
    content = unique()
    first, second, third = [await uploaded(backend, content) for _ in range(3)]
    ports = backend.admin
    await ports.document_writes.delete_document(third)

    found = {d.id: d for d in await ports.documents.get_duplicates(first)}

    assert found.keys() == {second, third}
    assert found[second].deleted_at is None
    assert found[third].deleted_at is not None
    assert found[second].title == (await ports.documents.get_document(second)).title
    assert await ports.documents.get_duplicates(await uploaded(backend, unique())) == ()


@pytest.mark.contract(DUPLICATES)
async def test_duplicates_in_versions_are_not_found(backend: Backend) -> None:
    content = unique()
    document, other = await uploaded(backend, unique()), await uploaded(backend, content)
    version = await add_version(backend, document, content.decode())

    assert await backend.admin.documents.get_duplicates(other) == ()
    assert await backend.admin.documents.get_duplicates(document) == ()
    with pytest.raises(NotFound):
        _ = await backend.admin.documents.get_duplicates(version)


@pytest.mark.contract(DUPLICATES)
async def test_a_file_that_is_the_archive_of_another_document_is_a_duplicate(
    backend: Backend,
) -> None:
    ports = backend.admin
    document = backend.new_pdf(1)
    archive = await ports.documents.get_document_file(document, "archive", max_bytes=10_000_000)
    assert archive.content
    copy = backend.upload(
        f"plmcp77-{uuid.uuid4().hex}", "archive.pdf", archive.content, "application/pdf"
    )

    assert [d.id for d in await ports.documents.get_duplicates(copy)] == [document]


@pytest.mark.contract(DUPLICATES)
async def test_hidden_duplicates_are_not_named_and_shared_ones_are_also_in_the_trash(
    backend: Backend,
) -> None:
    content = unique()
    hidden, shared = [await uploaded(backend, content) for _ in range(2)]
    reader = backend.restricted(frozenset({"view_document"}), view=[shared])

    assert await reader.documents.get_duplicates(shared) == ()
    with pytest.raises(NotFound):
        _ = await reader.documents.get_duplicates(hidden)
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.documents.get_duplicates(shared)

    both = backend.restricted(frozenset({"view_document"}), view=[hidden, shared])
    await backend.admin.document_writes.delete_document(shared)

    assert [d.id for d in await both.documents.get_duplicates(hidden)] == [shared]
    assert shared not in await find(both, {shared})


@pytest.mark.contract(ROOT)
async def test_the_root_of_documents_and_versions_also_in_the_trash(backend: Backend) -> None:
    ports = backend.admin
    document = await uploaded(backend, unique())
    version = await add_version(backend, document, unique().decode())
    deleted = await add_version(backend, document, unique().decode())
    _ = await ports.document_writes.delete_version(document, deleted)

    for number in (document, version, deleted):
        assert await ports.documents.get_root(number) == document
    await ports.document_writes.delete_document(document)
    for number in (document, version):
        assert await ports.documents.get_root(number) == document


@pytest.mark.contract(ROOT)
async def test_a_hidden_document_has_no_root_like_a_missing_one(backend: Backend) -> None:
    document = await uploaded(backend, unique())

    texts: set[str] = set()
    for ports, number in (
        (backend.admin, MISSING),
        (backend.reader, document),
        (backend.reader, MISSING),
        (backend.blind, SEEDED),
    ):
        with pytest.raises(NotFound) as raised:
            _ = await ports.documents.get_root(number)
        texts.add(raised.value.message.replace(str(number), "N"))
    assert len(texts) == 1


@pytest.mark.contract(SEARCH)
async def test_has_duplicates_is_true_for_a_copy_in_the_trash_and_not_for_hidden_ones(
    backend: Backend,
) -> None:
    content = unique()
    first, second = [await uploaded(backend, content) for _ in range(2)]
    reader = backend.restricted(frozenset({"view_document"}), view=[first])
    await backend.admin.document_writes.delete_document(second)

    async def duplicates(ports: Ports) -> int:
        query = DocumentQuery(has_duplicates=True, ids=(first,))
        return (await ports.documents.search_documents(query, page=1, page_size=1)).count

    assert await duplicates(backend.admin) == 1
    assert await duplicates(reader) == 0


@pytest.mark.contract(SEARCH)
async def test_has_duplicates_is_true_for_an_archive_copy_and_not_for_a_version(
    backend: Backend,
) -> None:
    ports = backend.admin
    content = unique()
    other = await uploaded(backend, content)
    versioned = await uploaded(backend, unique())
    _ = await add_version(backend, versioned, content.decode())
    original = backend.new_pdf(1)
    archive = await ports.documents.get_document_file(original, "archive", max_bytes=10_000_000)
    copy = backend.upload(
        f"plmcp86-{uuid.uuid4().hex}", "archive.pdf", archive.content, "application/pdf"
    )

    async def found(*, has: bool) -> set[int]:
        ids = (original, copy, other, versioned)
        query = DocumentQuery(has_duplicates=has, ids=ids)
        page = await ports.documents.search_documents(query, page=1, page_size=10)
        return {hit.id for hit in page.hits}

    assert await found(has=True) == {original, copy}
    assert await found(has=False) == {other, versioned}


@pytest.mark.contract("TrashReader.read_trash")
async def test_the_trash_is_read_at_once_within_a_byte_limit(backend: Backend) -> None:
    content = unique()
    document = await uploaded(backend, content)
    await backend.admin.document_writes.delete_document(document)
    ports = backend.admin

    one = await ports.trash.read_trash(entries=1, max_bytes=MIB)
    everything = await ports.trash.read_trash(entries=100_000, max_bytes=64 * MIB)

    assert len(one.entries) == 1
    assert one.count == everything.count == len(everything.entries)
    [entry] = [e for e in everything.entries if e.id == document]
    assert (entry.checksum, entry.version_of) == (sha256(content), None)
    with pytest.raises(AnswerTooLarge):
        _ = await ports.trash.read_trash(entries=100_000, max_bytes=10)
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.trash.read_trash(entries=1, max_bytes=MIB)


@pytest.mark.contract(FIND)
async def test_documents_in_the_trash_are_found_with_what_tells_them_apart(
    backend: Backend,
) -> None:
    content = unique()
    ports = backend.admin
    first, second, live = [await uploaded(backend, content) for _ in range(3)]
    tag = await ports.taxonomy_writes.create_tag(f"contract-tag-{uuid.uuid4().hex}")
    number = uuid.uuid4().int % 4_000_000_000
    patch = DocumentPatch(tags=(tag.id,), archive_serial_number=number, owner=None)
    before = await ports.document_writes.update_document(first, patch)
    version = await add_version(backend, live, unique().decode())
    _ = await ports.document_writes.delete_version(live, version)
    for document in (first, second):
        await ports.document_writes.delete_document(document)

    found = await ports.trash.find_in_trash(
        [first, second, live, version, MISSING], entries=100_000, max_bytes=64 * MIB
    )

    assert found.keys() == {first, second}
    entry = found[first]
    assert (entry.checksum, entry.tags, entry.archive_serial_number, entry.owner) == (
        sha256(content),
        (tag.id,),
        number,
        None,
    )
    for name in ("title", "created", "added", "original_file_name", "mime_type"):
        assert getattr(entry, name) == getattr(before, name)
    # Every user with view_document sees the trash entries nobody owns; a grant is not enough.
    seen = await backend.reader.trash.find_in_trash(
        [first, second], entries=100_000, max_bytes=64 * MIB
    )
    assert seen.keys() == {first}


@pytest.mark.contract(FIND)
async def test_finding_in_the_trash_is_bounded_and_needs_the_permission(backend: Backend) -> None:
    document = await uploaded(backend, unique())
    await backend.admin.document_writes.delete_document(document)

    with pytest.raises(AnswerTooLarge):
        _ = await backend.admin.trash.find_in_trash([document], entries=100_000, max_bytes=10)
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.trash.find_in_trash([document], entries=1, max_bytes=MIB)
