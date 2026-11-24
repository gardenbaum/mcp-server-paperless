"""Contract of PdfWriter: the same assertions on the fake and on a live Paperless.

Probed on 3.4.2: every PDF endpoint answers "OK" and names no task (the adapter finds the tasks);
rotating, and editing or unlocking as a new version, add a version to the same document and keep
the older one; the other forms make new documents; deleting originals sends them to the trash
after the new document exists; a user who may not view tasks gets the work done and no task id.
Every test works on documents of its own, never on a seeded one.
"""

import asyncio
import io
import time
import uuid
from datetime import date
from typing import TYPE_CHECKING

import pytest
from e2e.helpers import pdf_pages
from PIL import Image

from mcp_server_paperless.domain.bulk import TagsChange
from mcp_server_paperless.domain.documents import CustomFieldValue, DocumentPatch, DocumentQuery
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.domain.pdf import CopiedFiles, FoundCopies, PageSlot, StartedTasks
from mcp_server_paperless.domain.versions import VersionUpload

if TYPE_CHECKING:
    from collections.abc import Callable

    from contract.conftest import Backend, Ports

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

ROTATE = "PdfWriter.rotate_documents"
MERGE = "PdfWriter.merge_documents"
EDIT = "PdfWriter.edit_pdf"
PASSWORD = "PdfWriter.remove_password"
REPROCESS = "PdfWriter.reprocess_documents"
COPIES = "PdfWriter.find_copies"
PDF = "application/pdf"
MISSING = 999_999
SECONDS = 180.0
# The global permissions of a user who may change documents and make new ones (not delete).
EDITING = frozenset({"view_document", "change_document", "add_document"})


async def finished(backend: Backend, started: StartedTasks) -> list[int]:
    """Wait for the tasks (read as the admin), track what they made, return those ids."""
    produced: list[int] = []
    for task_id in started.ids:
        deadline = time.monotonic() + SECONDS
        while True:
            status = await backend.admin.tasks.get_task(task_id)
            if status.is_final:
                break
            assert time.monotonic() < deadline, f"Task {task_id} took too long"
            await asyncio.sleep(0.5)
        assert status.state == "success", status.message
        produced += status.document_ids
    for number in produced:
        backend.track_document(number)
    return produced


async def until(condition: Callable[[], bool]) -> None:
    """Wait until `condition` holds: Paperless deletes originals after the new file exists."""
    deadline = time.monotonic() + SECONDS
    while not condition():
        assert time.monotonic() < deadline, "Paperless did not finish in time"
        await asyncio.sleep(0.5)


async def pages(backend: Backend, document: int) -> int | None:
    return (await backend.admin.documents.get_document(document)).page_count


async def version_ids(backend: Backend, document: int) -> list[int]:
    return [v.id for v in (await backend.admin.documents.get_document(document)).versions]


@pytest.mark.contract(ROTATE)
async def test_rotating_adds_a_version_to_each_document_and_names_a_task_for_each(
    backend: Backend,
) -> None:
    first, second = backend.new_pdf(2), backend.new_pdf(1)

    tasks = await backend.admin.pdf_writes.rotate_documents((first, second), 90)

    assert (len(tasks.ids), tasks.expected, tasks.problem) == (2, 2, None)
    assert sorted(s for s in tasks.sources if s is not None) == sorted([first, second])
    produced = await finished(backend, tasks)
    for document, version in zip((first, second), produced, strict=True):
        assert await version_ids(backend, document) == [version, document]
    assert await pages(backend, first) == 2  # rotating keeps the pages


@pytest.mark.contract(ROTATE)
@pytest.mark.contract(MERGE)
@pytest.mark.contract(EDIT)
@pytest.mark.contract(PASSWORD)
@pytest.mark.contract(REPROCESS)
async def test_a_request_for_unknown_or_repeated_documents_is_refused(backend: Backend) -> None:
    writes, document = backend.admin.pdf_writes, backend.new_documents(1)[0]
    slot = (PageSlot(page=1),)

    for unknown in ((MISSING,), (document, document)):
        with pytest.raises(Validation, match="exist"):
            _ = await writes.rotate_documents(unknown, 90)
        with pytest.raises(Validation, match="exist"):
            _ = await writes.merge_documents(unknown, metadata_document_id=None)
        with pytest.raises(Validation, match="exist"):
            _ = await writes.reprocess_documents(unknown, remote_ocr=False)
    with pytest.raises(Validation, match="exist"):
        _ = await writes.edit_pdf(MISSING, slot, output="new_document")
    with pytest.raises(Validation, match="exist"):
        _ = await writes.remove_password(MISSING, "x", output="new_document")


@pytest.mark.contract(ROTATE)
async def test_degrees_must_be_a_multiple_of_90(backend: Backend) -> None:
    document = backend.new_documents(1)[0]

    with pytest.raises(Validation, match="multiple of 90"):
        _ = await backend.admin.pdf_writes.rotate_documents((document,), 45)


@pytest.mark.contract(MERGE)
async def test_merging_makes_one_new_document_titled_after_the_metadata_document(
    backend: Backend,
) -> None:
    first, second = backend.new_pdf(2), backend.new_pdf(1)
    source = await backend.admin.documents.get_document(second)

    tasks = await backend.admin.pdf_writes.merge_documents(
        (first, second), metadata_document_id=second
    )

    assert tasks.doubt is None
    [merged] = await finished(backend, tasks)
    made = await backend.admin.documents.get_document(merged)
    assert (made.title, made.page_count) == (f"{source.title} (merged)", 3)
    assert not backend.in_trash(first)
    assert (await pages(backend, first), await pages(backend, second)) == (2, 1)


@pytest.mark.contract(EDIT)
async def test_an_edit_splits_pages_into_new_documents_and_leaves_the_original(
    backend: Backend,
) -> None:
    document = backend.new_pdf(3)
    slots = (
        PageSlot(page=3),
        PageSlot(page=1, rotate=90),
        PageSlot(page=2, document=1),
    )

    tasks = await backend.admin.pdf_writes.edit_pdf(document, slots, output="new_document")

    assert len(tasks.ids) == 2
    produced = await finished(backend, tasks)
    assert sorted([await pages(backend, n) for n in produced], key=lambda n: n or 0) == [1, 2]
    assert await pages(backend, document) == 3
    assert not backend.in_trash(document)


@pytest.mark.contract(EDIT)
async def test_an_edit_as_a_new_version_changes_what_the_document_shows_and_keeps_the_old(
    backend: Backend,
) -> None:
    document = backend.new_pdf(3)

    tasks = await backend.admin.pdf_writes.edit_pdf(
        document, (PageSlot(page=1), PageSlot(page=3)), output="new_version"
    )

    [version] = await finished(backend, tasks)
    assert await version_ids(backend, document) == [version, document]
    assert await pages(backend, document) == 2


@pytest.mark.contract(EDIT)
async def test_a_bad_edit_is_refused_before_anything_happens(backend: Backend) -> None:
    writes, document = backend.admin.pdf_writes, backend.new_pdf(2)
    text = backend.upload(f"contract-text-{document}", "plain.txt", b"plain text", "text/plain")

    with pytest.raises(Validation, match="out of bounds"):
        _ = await writes.edit_pdf(document, (PageSlot(page=3),), output="new_document")
    with pytest.raises(Validation, match="out of bounds"):
        _ = await writes.edit_pdf(
            document, (PageSlot(page=1), PageSlot(page=1, document=2)), output="new_document"
        )
    with pytest.raises(Validation, match="single output"):
        _ = await writes.edit_pdf(
            document, (PageSlot(page=1), PageSlot(page=2, document=1)), output="new_version"
        )
    with pytest.raises(Validation, match="multiple of 90"):
        _ = await writes.edit_pdf(document, (PageSlot(page=1, rotate=45),), output="new_document")
    with pytest.raises(Validation, match="empty"):
        _ = await writes.edit_pdf(document, (), output="new_document")
    with pytest.raises(Validation, match="PDF"):
        _ = await writes.edit_pdf(text, (PageSlot(page=1),), output="new_document")

    assert await version_ids(backend, document) == [document]


@pytest.mark.contract(PASSWORD)
async def test_a_password_is_removed_into_a_new_document(backend: Backend) -> None:
    locked = backend.new_encrypted("s3cret")

    tasks = await backend.admin.pdf_writes.remove_password(locked, "s3cret", output="new_document")

    assert tasks.doubt is None
    [unlocked] = await finished(backend, tasks)
    assert unlocked != locked
    assert await pages(backend, unlocked) == 1
    assert await version_ids(backend, locked) == [locked]


@pytest.mark.contract(PASSWORD)
async def test_a_password_can_be_removed_as_a_new_version_and_a_wrong_one_is_refused(
    backend: Backend,
) -> None:
    locked = backend.new_encrypted("s3cret")
    writes = backend.admin.pdf_writes

    with pytest.raises(Validation, match="password"):
        _ = await writes.remove_password(locked, "wrong", output="new_version")
    tasks = await writes.remove_password(locked, "s3cret", output="new_version")

    [version] = await finished(backend, tasks)
    assert await version_ids(backend, locked) == [version, locked]
    assert await pages(backend, locked) == 1


@pytest.mark.contract(PASSWORD)
async def test_a_file_without_a_password_starts_no_task_and_a_text_file_is_refused(
    backend: Backend,
) -> None:
    writes = backend.admin.pdf_writes
    document = backend.new_pdf(1)
    text = backend.upload(f"contract-text-{document}", "plain.txt", b"plain text", "text/plain")

    tasks = await writes.remove_password(document, "x", output="new_document")
    with pytest.raises(Validation, match="PDF"):
        _ = await writes.remove_password(text, "x", output="new_document")

    assert tasks.ids == ()


@pytest.mark.contract(REPROCESS)
async def test_reprocessing_starts_one_task_per_document(backend: Backend) -> None:
    first, second = backend.new_documents(2)

    tasks = await backend.admin.pdf_writes.reprocess_documents((first, second), remote_ocr=False)

    assert len(tasks.ids) == 2
    assert await finished(backend, tasks) == []  # reprocessing names no document


@pytest.mark.contract(ROTATE)
@pytest.mark.contract(MERGE)
@pytest.mark.contract(EDIT)
@pytest.mark.contract(PASSWORD)
@pytest.mark.contract(REPROCESS)
async def test_only_a_user_who_may_change_the_documents_can_edit_them(backend: Backend) -> None:
    first, second = backend.new_documents(2)
    viewer = backend.restricted(EDITING, view=[first, second])

    for user in (viewer, backend.blind):
        writes = user.pdf_writes
        with pytest.raises(PermissionDenied):
            _ = await writes.rotate_documents((first,), 90)
        with pytest.raises(PermissionDenied):
            _ = await writes.edit_pdf(first, (PageSlot(page=1),), output="new_document")
        with pytest.raises(PermissionDenied):
            _ = await writes.remove_password(first, "x", output="new_document")
        with pytest.raises(PermissionDenied):
            _ = await writes.reprocess_documents((first,), remote_ocr=False)
        with pytest.raises(PermissionDenied):
            _ = await writes.merge_documents((first, second), metadata_document_id=None)


@pytest.mark.contract(ROTATE)
@pytest.mark.contract(EDIT)
@pytest.mark.contract(PASSWORD)
@pytest.mark.contract(REPROCESS)
async def test_a_change_grant_reprocesses_but_rotating_and_editing_need_ownership(
    backend: Backend,
) -> None:
    document = backend.new_documents(1)[0]
    granted = backend.restricted(EDITING, change=[document])
    writes = granted.pdf_writes

    with pytest.raises(PermissionDenied):
        _ = await writes.rotate_documents((document,), 90)
    with pytest.raises(PermissionDenied):
        _ = await writes.edit_pdf(document, (PageSlot(page=1),), output="new_document")
    with pytest.raises(PermissionDenied):
        _ = await writes.remove_password(document, "x", output="new_document")
    assert (await writes.reprocess_documents((document,), remote_ocr=False)).ids == ()


@pytest.mark.contract(EDIT)
async def test_making_new_documents_needs_the_right_to_add_them(backend: Backend) -> None:
    document = backend.new_pdf(1)
    _ = await backend.admin.document_writes.update_document(document, DocumentPatch(owner=None))
    changer = backend.restricted(frozenset({"view_document", "change_document"}))

    with pytest.raises(PermissionDenied):
        _ = await changer.pdf_writes.edit_pdf(document, (PageSlot(page=1),), output="new_document")
    tasks = await changer.pdf_writes.edit_pdf(document, (PageSlot(page=1),), output="new_version")

    assert tasks.ids == ()  # this user may not view tasks
    await until_versions(backend, document, 2)


@pytest.mark.contract(ROTATE)
async def test_a_user_who_may_not_view_tasks_gets_the_work_done_and_no_task_id(
    backend: Backend,
) -> None:
    document = backend.new_pdf(1)
    _ = await backend.admin.document_writes.update_document(document, DocumentPatch(owner=None))
    editor = backend.restricted(EDITING)

    tasks = await editor.pdf_writes.rotate_documents((document,), 90)

    assert (tasks.ids, tasks.expected, tasks.problem) == ((), 1, "forbidden")
    await until_versions(backend, document, 2)


async def until_versions(backend: Backend, document: int, count: int) -> None:
    deadline = time.monotonic() + SECONDS
    while len(await version_ids(backend, document)) < count:
        assert time.monotonic() < deadline, "Paperless did not finish in time"
        await asyncio.sleep(0.5)
    for number in await version_ids(backend, document):
        backend.track_document(number)


async def add_version(backend: Backend, document: int, pages: int) -> int:
    """A newest version with this many pages, uploaded by the admin."""
    name = f"contract-version-{uuid.uuid4().hex}"
    content = pdf_pages([f"{name} page {n}" for n in range(1, pages + 1)])
    upload = VersionUpload(filename=f"{name}.pdf", mime_type=PDF, content=content)
    task = await backend.admin.document_writes.upload_document_version(document, upload)
    [version] = await finished(backend, StartedTasks(ids=(task,), expected=1))
    return version


@pytest.mark.contract(EDIT)
@pytest.mark.contract("DocumentReader.get_version_pages")
async def test_a_page_past_the_first_file_is_edited_by_the_id_of_the_newest_version(
    backend: Backend,
) -> None:
    document = backend.new_pdf(2)
    version = await add_version(backend, document, 4)
    reader, writes = backend.admin.documents, backend.admin.pdf_writes
    last = (PageSlot(page=4),)

    # The document says 4 pages, but Paperless holds a request for the root against its first file.
    assert await pages(backend, document) == 4
    assert await reader.get_version_pages(document, document) == 2
    assert await reader.get_version_pages(document, version) == 4
    with pytest.raises(Validation, match="out of bounds for document with 2 pages"):
        _ = await writes.edit_pdf(document, last, output="new_document")
    tasks = await writes.edit_pdf(document, last, output="new_document", version_id=version)

    [made] = await finished(backend, tasks)
    title = (await reader.get_document(document)).title
    assert ((await reader.get_document(made)).title, await pages(backend, made)) == (title, 1)
    assert await version_ids(backend, document) == [version, document]


@pytest.mark.contract(EDIT)
async def test_the_first_file_can_be_edited_when_the_newest_has_fewer_pages(
    backend: Backend,
) -> None:
    document = backend.new_pdf(3)
    version = await add_version(backend, document, 1)
    writes, first = backend.admin.pdf_writes, tuple(PageSlot(page=n) for n in (1, 2, 3))

    # The request passes against the first file, and the queued task fails on the newest.
    with pytest.raises(Validation, match="page that does not exist"):
        _ = await writes.edit_pdf(document, first, output="new_document")
    tasks = await writes.edit_pdf(document, first, output="new_version", version_id=document)

    [made] = await finished(backend, tasks)
    assert await version_ids(backend, document) == [made, version, document]
    assert await pages(backend, document) == 3


@pytest.mark.contract(ROTATE)
@pytest.mark.contract(MERGE)
async def test_rotating_and_merging_can_take_the_first_file(backend: Backend) -> None:
    document, other = backend.new_pdf(3), backend.new_pdf(1)
    version = await add_version(backend, document, 1)
    writes = backend.admin.pdf_writes

    newest = await writes.merge_documents((document, other), metadata_document_id=None)
    first = await writes.merge_documents(
        (document, other), metadata_document_id=None, original_files=True
    )
    [small], [large] = await finished(backend, newest), await finished(backend, first)
    assert (await pages(backend, small), await pages(backend, large)) == (2, 4)
    rotated = await writes.rotate_documents((document,), 90, original_files=True)

    [turned] = await finished(backend, rotated)
    assert await version_ids(backend, document) == [turned, version, document]
    assert await pages(backend, document) == 3


@pytest.mark.contract("DocumentReader.get_version_pages")
async def test_the_pages_of_a_version_the_document_does_not_list_are_not_found(
    backend: Backend,
) -> None:
    document, other = backend.new_documents(2)

    for number, version in ((document, other), (document, MISSING), (MISSING, MISSING)):
        with pytest.raises(NotFound):
            _ = await backend.admin.documents.get_version_pages(number, version)


@pytest.mark.contract(EDIT)
async def test_an_owner_may_edit_by_version_id_and_a_change_grant_is_not_enough(
    backend: Backend,
) -> None:
    document = backend.new_pdf(1)
    version = await add_version(backend, document, 2)
    granted = backend.restricted(EDITING, change=[document])
    last = (PageSlot(page=2),)

    with pytest.raises(PermissionDenied):
        _ = await granted.pdf_writes.edit_pdf(
            document, last, output="new_version", version_id=version
        )
    _ = await backend.admin.document_writes.update_document(document, DocumentPatch(owner=None))
    tasks = await granted.pdf_writes.edit_pdf(
        document, last, output="new_version", version_id=version
    )

    assert tasks.ids == ()  # this user may not view tasks
    await until_versions(backend, document, 3)
    assert await pages(backend, document) == 1


@pytest.mark.contract(EDIT)
async def test_the_parts_of_a_split_are_known_by_their_number(backend: Backend) -> None:
    document = backend.new_pdf(3)
    slots = (PageSlot(page=1), PageSlot(page=2, document=1), PageSlot(page=3, document=1))

    tasks = await backend.admin.pdf_writes.edit_pdf(document, slots, output="new_document")

    assert tasks.doubt is None
    produced = dict(zip(tasks.outputs, await finished(backend, tasks), strict=True))
    assert [await pages(backend, produced[n]) for n in (0, 1)] == [1, 2]


@pytest.mark.contract(EDIT)
@pytest.mark.parametrize("keep", [True, False])
async def test_what_a_new_document_takes_over_and_what_it_never_does(
    backend: Backend, *, keep: bool
) -> None:
    ports, document = backend.admin, backend.new_pdf(2)
    number = uuid.uuid4().int % 1_000_000 + 9_000_000
    tags = {tag.name: tag.id for tag in await ports.taxonomy.list_taxonomy("tag")}
    kinds = {f.name: f.id for f in await ports.taxonomy.list_taxonomy("custom_field")}
    value = CustomFieldValue(field=kinds["e2e-string"], value="kept")
    patch = DocumentPatch(
        tags=(tags["e2e-finance"],),
        created=date(2020, 2, 3),
        archive_serial_number=number,
        custom_fields=(value,),
    )
    source = await ports.document_writes.update_document(document, patch)
    _ = await ports.document_writes.add_note(document, "stays here")

    tasks = await ports.pdf_writes.edit_pdf(
        document, (PageSlot(page=2),), output="new_document", keep_metadata=keep
    )

    [made] = await finished(backend, tasks)
    record = await ports.documents.get_document(made)
    assert record.title == (source.title if keep else f"{document}_edit_1")
    assert (tags["e2e-finance"] in record.tags) is keep
    assert tags["e2e-inbox"] in record.tags  # like every document that is consumed
    assert record.custom_fields == ((value,) if keep else ())
    assert (record.created == date(2020, 2, 3)) is keep
    assert (record.archive_serial_number, record.notes_count) == (None, 0)
    assert len(record.versions) == 1


@pytest.mark.contract(EDIT)
async def test_a_new_version_keeps_what_the_document_has(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_pdf(2)
    number = uuid.uuid4().int % 1_000_000 + 9_000_000
    before = await ports.document_writes.update_document(
        document, DocumentPatch(archive_serial_number=number)
    )
    _ = await ports.document_writes.add_note(document, "stays here")

    tasks = await ports.pdf_writes.edit_pdf(
        document, (PageSlot(page=2),), output="new_version", keep_metadata=False
    )

    _ = await finished(backend, tasks)
    after = await ports.documents.get_document(document)
    assert (after.title, after.tags, after.created) == (before.title, before.tags, before.created)
    assert (after.archive_serial_number, after.notes_count) == (number, 1)


@pytest.mark.contract(PASSWORD)
async def test_an_unlocked_copy_takes_no_serial_number(backend: Backend) -> None:
    ports, locked = backend.admin, backend.new_encrypted("s3cret")
    number = uuid.uuid4().int % 1_000_000 + 9_000_000
    _ = await ports.document_writes.update_document(
        locked, DocumentPatch(archive_serial_number=number)
    )

    tasks = await ports.pdf_writes.remove_password(
        locked, "s3cret", output="new_document", version_id=locked, keep_metadata=False
    )

    [made] = await finished(backend, tasks)
    record = await ports.documents.get_document(made)
    assert (record.title, record.archive_serial_number) == (f"{locked}_unprotected", None)


@pytest.mark.contract(MERGE)
async def test_a_merge_leaves_out_what_it_cannot_read_and_names_the_rest(
    backend: Backend,
) -> None:
    first, locked = backend.new_pdf(2), backend.new_encrypted("s3cret")
    text = backend.upload(f"contract-text-{first}", "plain.txt", b"plain text", "text/plain")
    writes = backend.admin.pdf_writes

    for fallback in (False, True):  # a text file has no archive PDF
        tasks = await writes.merge_documents(
            (first, locked, text), metadata_document_id=None, archive_fallback=fallback
        )
        [merged] = await finished(backend, tasks)
        record = await backend.admin.documents.get_document(merged)
        assert tasks.included == (first,)
        assert (record.page_count, record.title) == (2, f"{first}_merged")


@pytest.mark.contract(EDIT)
async def test_a_date_among_the_custom_fields_hides_the_task_of_a_new_document_only(
    backend: Backend,
) -> None:
    ports, document = backend.admin, backend.new_pdf(1)
    kinds = {f.name: f.id for f in await ports.taxonomy.list_taxonomy("custom_field")}
    dated = CustomFieldValue(field=kinds["e2e-date"], value="2026-01-15")
    _ = await ports.document_writes.update_document(document, DocumentPatch(custom_fields=(dated,)))
    title = f"contract-dated-{uuid.uuid4().hex}"
    _ = await ports.document_writes.update_document(document, DocumentPatch(title=title))
    page = (PageSlot(page=1),)

    version = await ports.pdf_writes.edit_pdf(document, page, output="new_version")
    _ = await finished(backend, version)
    hidden = await ports.pdf_writes.edit_pdf(document, page, output="new_document")
    bare = await ports.pdf_writes.edit_pdf(
        document, page, output="new_document", keep_metadata=False
    )

    assert (len(version.ids), hidden.ids, len(bare.ids)) == (1, (), 1)
    assert (hidden.expected, hidden.problem) == (1, None)
    _ = await finished(backend, bare)
    # The document is made all the same, with the title and the date.
    deadline = time.monotonic() + SECONDS
    while True:
        query = DocumentQuery(title_contains=title)
        listed = await ports.documents.search_documents(query, page=1, page_size=10)
        made = [hit.id for hit in listed.hits if hit.id != document]
        if made:
            break
        assert time.monotonic() < deadline, "Paperless did not finish in time"
        await asyncio.sleep(0.5)
    for number in made:
        backend.track_document(number)
    assert (await ports.documents.get_document(made[0])).custom_fields == (dated,)


# A user of its own: what others of the suite did in the last hour is not its doing.
FINDING = EDITING | {
    *("view_paperlesstask", "view_uisettings", "view_customfield", "view_tag", "view_documenttype"),
    *("view_correspondent", "view_storagepath"),
}


async def dated(backend: Backend, document: int) -> CustomFieldValue:
    """Give the document a date among its custom fields, and no owner."""
    ports = backend.admin
    kinds = {f.name: f.id for f in await ports.taxonomy.list_taxonomy("custom_field")}
    value = CustomFieldValue(field=kinds["e2e-date"], value="2026-01-15")
    patch = DocumentPatch(custom_fields=(value,), owner=None)
    _ = await ports.document_writes.update_document(document, patch)
    return value


async def copies(backend: Backend, user: Ports, files: CopiedFiles | None) -> FoundCopies:
    """Wait until every file of the request is found, or a doubt; tracks the documents."""
    assert files is not None
    deadline = time.monotonic() + SECONDS
    while True:
        found = await user.pdf_writes.find_copies(files)
        if found.doubt is not None and not found.passing:
            return found
        if found.doubt is None and None not in found.ids:
            for number in found.ids:
                if number is not None:
                    backend.track_document(number)
            return found
        assert time.monotonic() < deadline, "Paperless did not finish in time"
        await asyncio.sleep(0.5)


@pytest.mark.contract(COPIES)
@pytest.mark.contract(EDIT)
@pytest.mark.decision("decision-67")
async def test_parts_whose_tasks_a_date_hides_are_found_by_their_file_names(
    backend: Backend,
) -> None:
    document, other = backend.new_pdf(3), backend.new_pdf(1)
    value = await dated(backend, document)
    _ = await dated(backend, other)
    # A metadata change of somebody, and a PDF operation of a user who is deleted since: neither
    # has an owner afterwards, and everybody sees them.
    tags = {tag.name: tag.id for tag in await backend.admin.taxonomy.list_taxonomy("tag")}
    change = TagsChange(add=(tags["e2e-finance"],))
    _ = await backend.admin.bulk_writes.bulk_edit_documents((other,), change)
    name = f"contract-gone-{uuid.uuid4().hex[:12]}"
    gone = backend.restricted(FINDING, name=name)
    _ = await gone.pdf_writes.edit_pdf(other, (PageSlot(page=1),), output="new_version")
    await until_versions(backend, other, 2)
    accounts = await backend.admin.permissions.list_users(name_contains=name, page=1, page_size=1)
    [account] = accounts.rows
    await backend.admin.management_writes.delete_user(account.id)
    user = backend.restricted(FINDING)
    slots = (PageSlot(page=1), PageSlot(page=2, document=1), PageSlot(page=3, document=1))

    first = await user.pdf_writes.edit_pdf(other, (PageSlot(page=1),), output="new_document")
    [one] = (await copies(backend, user, first.files)).ids
    tasks = await user.pdf_writes.edit_pdf(document, slots, output="new_document")
    found = await copies(backend, user, tasks.files)

    assert (tasks.ids, tasks.problem) == ((), None)
    assert one is not None
    # The earlier operation is known to have made something else: its file is there.
    assert found.doubt is None
    assert [await pages(backend, n) for n in found.ids if n is not None] == [1, 2]
    for n, number in enumerate(found.ids, start=1):
        assert number is not None
        record = await user.documents.get_document(number)
        assert record.original_file_name == f"{document}_edit_{n}.pdf"
        assert record.custom_fields == (value,)


@pytest.mark.contract(COPIES)
@pytest.mark.contract(EDIT)
@pytest.mark.decision("decision-67")
async def test_a_second_request_for_the_same_document_leaves_both_in_doubt(
    backend: Backend,
) -> None:
    document = backend.new_pdf(1)
    _ = await dated(backend, document)
    user, page = backend.restricted(FINDING), (PageSlot(page=1),)

    first = await user.pdf_writes.edit_pdf(document, page, output="new_document")
    assert (await copies(backend, user, first.files)).doubt is None
    second = await user.pdf_writes.edit_pdf(document, page, output="new_document")

    assert first.files is not None
    assert second.files is not None
    later = await user.pdf_writes.find_copies(second.files, final=True)
    earlier = await user.pdf_writes.find_copies(first.files)
    assert (later.doubt, later.ids) == (
        "files of earlier operations of this user may still come",
        (None,),
    )
    assert earlier.doubt is not None
    assert earlier.ids == (None,)
    await two_made(backend, user, f"{document}_edit_1.pdf")


async def two_made(backend: Backend, user: Ports, name: str) -> None:
    """Wait for both documents of a name, for the cleanup."""
    deadline = time.monotonic() + SECONDS
    while True:
        listed = await user.documents.search_documents(
            DocumentQuery(ordering="-added"), page=1, page_size=25
        )
        rows = [await user.documents.get_document(hit.id) for hit in listed.hits]
        made = [row.id for row in rows if row.original_file_name == name]
        if len(made) == 2:
            break
        assert time.monotonic() < deadline, "Paperless did not finish in time"
        await asyncio.sleep(0.5)
    for number in made:
        backend.track_document(number)


@pytest.mark.contract(COPIES)
@pytest.mark.contract(MERGE)
@pytest.mark.contract(PASSWORD)
@pytest.mark.decision("decision-67")
async def test_a_merged_and_an_unlocked_document_are_found_by_their_file_names(
    backend: Backend,
) -> None:
    document, second, locked = (
        backend.new_pdf(2),
        backend.new_pdf(1),
        backend.new_encrypted("s3cret"),
    )
    for number in (document, second, locked):
        _ = await dated(backend, number)
    merging, unlocking = backend.restricted(FINDING), backend.restricted(FINDING)

    merged = await merging.pdf_writes.merge_documents(
        (document, second), metadata_document_id=document
    )
    unlocked = await unlocking.pdf_writes.remove_password(locked, "s3cret", output="new_document")

    [together] = (await copies(backend, merging, merged.files)).ids
    [opened] = (await copies(backend, unlocking, unlocked.files)).ids
    assert merged.files is not None
    assert merged.files.included == (document, second)
    assert together is not None
    assert opened is not None
    assert (await pages(backend, together), await pages(backend, opened)) == (3, 1)


@pytest.mark.contract(COPIES)
@pytest.mark.decision("decision-67")
async def test_only_who_may_view_tasks_and_is_known_finds_the_files(backend: Backend) -> None:
    files = CopiedFiles(request=str(uuid.uuid4()), names=("1_edit_1.pdf",))
    viewing = backend.restricted(frozenset({"view_document", "view_uisettings"}))
    unknown = backend.restricted(frozenset({"view_document", "view_paperlesstask"}))
    known = backend.restricted(FINDING)

    with pytest.raises(PermissionDenied):
        _ = await viewing.pdf_writes.find_copies(files)
    assert (await unknown.pdf_writes.find_copies(files)).doubt == "the current user is not known"
    assert (await known.pdf_writes.find_copies(files)).doubt == (
        "the times of its queued task are not known"
    )


def photo() -> bytes:
    """A PNG; Paperless archives it as a PDF of one page and counts none for the document."""
    out = io.BytesIO()
    colour = (uuid.uuid4().int % 255, 10, 20)
    Image.new("RGB", (300, 400), colour).save(out, format="PNG", dpi=(100, 100))
    return out.getvalue()


@pytest.mark.contract(MERGE)
async def test_a_merge_can_take_the_archive_pdf_of_an_image(backend: Backend) -> None:
    ports, first = backend.admin, backend.new_pdf(2)
    image = backend.upload(f"contract-photo-{first}", "photo.png", photo(), "image/png")

    without = await ports.pdf_writes.merge_documents((first, image), metadata_document_id=None)
    taken = await ports.pdf_writes.merge_documents(
        (first, image), metadata_document_id=None, archive_fallback=True
    )

    assert (without.included, taken.included) == ((first,), (first, image))
    [short], [whole] = await finished(backend, without), await finished(backend, taken)
    assert (await pages(backend, image), await pages(backend, short)) == (None, 2)
    assert await pages(backend, whole) == 3


@pytest.mark.contract("DocumentReader.may_delete")
async def test_who_may_delete_is_known_to_users_who_may_read_their_settings(
    backend: Backend,
) -> None:
    ports, document = backend.admin, backend.new_documents(1)[0]
    owner = (await ports.documents.get_document(document)).owner
    settings = frozenset({"view_uisettings", "view_document"})
    deleting = backend.restricted(settings | {"delete_document"})
    viewing = backend.restricted(settings)
    hidden = backend.restricted(frozenset({"view_document", "delete_document"}))

    assert await ports.documents.may_delete([owner, None]) is True
    assert await deleting.documents.may_delete([None]) is True
    assert await deleting.documents.may_delete([None, owner]) is False
    assert await viewing.documents.may_delete([None]) is False
    assert await hidden.documents.may_delete([None]) is None


@pytest.mark.contract(ROTATE)
@pytest.mark.contract(MERGE)
@pytest.mark.contract(PASSWORD)
async def test_a_date_hides_the_tasks_of_rotating_merging_and_unlocking(backend: Backend) -> None:
    ports = backend.admin
    document, locked = backend.new_pdf(1), backend.new_encrypted("s3cret")
    kinds = {f.name: f.id for f in await ports.taxonomy.list_taxonomy("custom_field")}
    dated = CustomFieldValue(field=kinds["e2e-date"], value="2026-01-15")
    title = f"contract-dated-{uuid.uuid4().hex}"
    for number in (document, locked):
        patch = DocumentPatch(custom_fields=(dated,), title=f"{title} {number}")
        _ = await ports.document_writes.update_document(number, patch)
    writes = ports.pdf_writes

    rotated = await writes.rotate_documents((document,), 90)
    merged = await writes.merge_documents((document,), metadata_document_id=document)
    unlocked = await writes.remove_password(locked, "s3cret", output="new_document")
    version = await writes.remove_password(locked, "s3cret", output="new_version")
    bare = await writes.remove_password(
        locked, "s3cret", output="new_document", version_id=locked, keep_metadata=False
    )

    assert (rotated.ids, merged.ids, unlocked.ids) == ((), (), ())
    assert (len(version.ids), len(bare.ids)) == (1, 1)
    _ = await finished(backend, version)
    _ = await finished(backend, bare)
    await until_versions(backend, document, 2)
    deadline = time.monotonic() + SECONDS
    while True:
        query = DocumentQuery(title_contains=title)
        listed = await ports.documents.search_documents(query, page=1, page_size=10)
        made = [hit.id for hit in listed.hits if hit.id not in {document, locked}]
        if len(made) == 2:
            break
        assert time.monotonic() < deadline, "Paperless did not finish in time"
        await asyncio.sleep(0.5)
    for number in made:
        backend.track_document(number)
