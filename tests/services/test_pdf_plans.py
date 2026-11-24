"""What a PDF operation says before it runs, and which file it reads.

The fakes of previews and refusals have no `allow_writes`, so a passing test proves nothing was
sent to Paperless.
"""

from dataclasses import replace
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.paperless import FakeDocument, FakePaperless, seeded
from harness import pdf_service

from mcp_server_paperless.domain.documents import DocumentVersion
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    UpstreamTimeout,
    Validation,
)
from mcp_server_paperless.domain.pdf import PageSlot
from mcp_server_paperless.services.pdf import Options

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp_server_paperless.domain.documents import DocumentRecord
    from mcp_server_paperless.domain.pdf import PdfResult, StartedTasks
    from mcp_server_paperless.services.pdf import PdfService

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

# A document whose newest version (61) has 4 pages and whose first file has 2.
LONGER, NEWEST = 60, 61
PLAIN, TEXT, SCAN, LOCKED = 70, 71, 72, 73
PREVIEW = Options(dry_run=True)
YES = Options(confirm=True)


def paperless(*, writes: bool = True) -> FakePaperless:
    state = seeded()
    longer = FakeDocument(id=LONGER, title="Longer", owner=2, page_count=2)
    newer = DocumentVersion(
        id=NEWEST, added=longer.added, version_label=None, is_root=False, checksum="61"
    )
    longer.version_files[LONGER] = longer.shown_file()
    longer.page_count = 4
    longer.version_files[NEWEST] = longer.shown_file()
    longer.versions.insert(0, newer)
    state.documents += [
        longer,
        FakeDocument(id=PLAIN, title="Plain", owner=2, page_count=3),
        FakeDocument(id=TEXT, title="Text", owner=2, mime_type="text/plain", archive=None),
        FakeDocument(id=SCAN, title="Scan", owner=2, mime_type="image/png", page_count=1),
        FakeDocument(id=LOCKED, title="Locked", owner=2, page_count=None, password="s3cret"),
    ]
    return FakePaperless(state, user="admin", allow_writes=writes)


def document(fake: FakePaperless, number: int) -> FakeDocument:
    [found] = [d for d in fake.state.documents if d.id == number]
    return found


async def refused(call: Awaitable[object]) -> tuple[str, str | None]:
    with pytest.raises(Validation) as raised:
        _ = await call
    return raised.value.message, raised.value.hint


async def asked(call: Awaitable[object]) -> str:
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await call
    return raised.value.message


type Call = Callable[[PdfService, Options], Awaitable[PdfResult]]

PAGE = [PageSlot(page=1)]
FILES: dict[str, tuple[bool, Call]] = {
    "split": (False, lambda s, o: s.split(PLAIN, ["1", "2"], options=o)),
    "split-replace": (
        True,
        lambda s, o: s.split(PLAIN, ["1", "2"], output="replace_original", options=o),
    ),
    "delete": (True, lambda s, o: s.delete_pages(PLAIN, "1", options=o)),
    "delete-new": (
        False,
        lambda s, o: s.delete_pages(PLAIN, "1", output="new_document", options=o),
    ),
    "edit": (False, lambda s, o: s.edit(PLAIN, PAGE, options=o)),
    "edit-version": (True, lambda s, o: s.edit(PLAIN, PAGE, output="new_version", options=o)),
    "edit-whole": (
        False,
        lambda s, o: s.edit(
            PLAIN, [PageSlot(page=n) for n in (3, 2, 1)], output="new_version", options=o
        ),
    ),
    "edit-replace": (True, lambda s, o: s.edit(PLAIN, PAGE, output="replace_original", options=o)),
    "unlock": (False, lambda s, o: s.remove_password(LOCKED, "s3cret", options=o)),
    "unlock-version": (
        False,
        lambda s, o: s.remove_password(LOCKED, "s3cret", output="new_version", options=o),
    ),
    "unlock-replace": (
        True,
        lambda s, o: s.remove_password(LOCKED, "s3cret", output="replace_original", options=o),
    ),
    "rotate": (
        False,
        lambda s, o: s.rotate(
            [PLAIN], 90, confirm=o.confirm, dry_run=o.dry_run, wait_seconds=o.wait_seconds or 0
        ),
    ),
    "rotate-first": (
        True,
        lambda s, o: s.rotate(
            [PLAIN, LONGER], 90, use_original_files=True, confirm=o.confirm, dry_run=o.dry_run
        ),
    ),
    "merge": (
        False,
        lambda s, o: s.merge([PLAIN, LONGER], None, confirm=o.confirm, dry_run=o.dry_run),
    ),
    "merge-delete": (
        True,
        lambda s, o: s.merge(
            [PLAIN, LONGER],
            PLAIN,
            delete_originals=True,
            confirm=o.confirm,
            dry_run=o.dry_run,
        ),
    ),
    "reprocess": (
        True,
        lambda s, o: s.reprocess([PLAIN, TEXT], confirm=o.confirm, dry_run=o.dry_run),
    ),
}


@pytest.mark.parametrize("name", FILES)
async def test_a_preview_says_what_a_run_says_and_changes_nothing(name: str) -> None:
    needs_confirm, call = FILES[name]
    reading = paperless(writes=False)

    preview = await call(pdf_service(reading), PREVIEW)
    run = await call(pdf_service(paperless()), YES)

    assert (preview.dry_run, preview.state, preview.wait) == (True, "not_started", "not_requested")
    assert (preview.tasks, preview.produced_ids, preview.trashed_ids) == ((), (), ())
    assert (preview.message, preview.next) == (None, None)
    assert reading.state.tasks == []
    assert preview.plan.needs_confirm is needs_confirm
    assert (preview.plan, preview.summary) == (run.plan, run.summary)
    assert (preview.operation, preview.source_links) == (run.operation, run.source_links)
    assert (run.dry_run, run.state) == (False, "success")
    if needs_confirm:
        _ = await asked(call(pdf_service(paperless(writes=False)), Options()))
    else:
        assert (await call(pdf_service(paperless()), Options())).state == "success"


async def plan_of(name: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    plan = (await FILES[name][1](pdf_service(paperless(writes=False)), PREVIEW)).plan
    return plan.produces, plan.keeps, plan.loses


METADATA = (
    "title, correspondent, document type, storage path, tags, created date, custom field values "
    "and view and change grants"
)
NEVER = "notes, history, earlier versions, share links and corrections of the text are not copied"
PIPELINE = (
    "a new document belongs to the calling user and is processed like an upload: workflows, "
    "matching and barcodes can change it, split it again or stop it"
)
BLOCKED = (
    "an archive serial number stays with the trashed original: restoring brings it back, and it "
    "is blocked until the trash is emptied"
)
MOVES = "the archive serial number, if there is one, moves to the new document"
STAYS = "an archive serial number stays with the original"
READ_AGAIN = "the text is read again, so corrections of it are not in the new version"


def trashed(names: str) -> str:
    return (
        f"{names}: moved to the Paperless trash once everything that was made is checked; can be "
        "restored from there"
    )


def version_keeps(name: str) -> tuple[str, str]:
    return (
        (
            f"{name}: title, tags, notes, custom fields, archive serial number, owner, grants and "
            "every other metadata"
        ),
        "every earlier version, until it is deleted",
    )


async def test_the_plans_of_new_documents() -> None:
    name = "document 70 'Plain'"
    assert await plan_of("split") == (
        "2 new documents",
        (f"new documents take {METADATA} of {name}", f"{name}: unchanged", STAYS),
        ("page 3: in no new document", NEVER, PIPELINE),
    )
    assert await plan_of("split-replace") == (
        "2 new documents",
        (f"new documents take {METADATA} of {name}",),
        ("page 3: in no new document", NEVER, PIPELINE, BLOCKED, trashed(name)),
    )
    assert await plan_of("edit-replace") == (
        "1 new document",
        (f"new documents take {METADATA} of {name}", MOVES),
        ("pages 2-3: not in the result", NEVER, PIPELINE, trashed(name)),
    )
    locked = "document 73 'Locked'"
    assert await plan_of("unlock-replace") == (
        "1 new document",
        (f"new documents take {METADATA} of {locked}",),
        (NEVER, PIPELINE, BLOCKED, trashed(locked)),
    )


async def test_the_plans_of_versions() -> None:
    name = "document 70 'Plain'"
    assert await plan_of("delete") == (
        "a new version of the document",
        version_keeps(name),
        ("page 1: not in the result", READ_AGAIN),
    )
    assert await plan_of("rotate") == (
        "a new version of 1 document",
        version_keeps("the document"),
        (READ_AGAIN,),
    )
    assert await plan_of("rotate-first") == (
        "a new version of 2 documents",
        version_keeps("each document"),
        ("document 60 'Longer' no longer shows its newest file", READ_AGAIN),
    )


VERSION_OF_A_MERGE = (
    "Paperless always makes a new document from a merge. To make it a version, merge, then "
    "merge_documents_as_versions (toolset versions): the merged document is absorbed for good, "
    "with its title, tags and notes"
)


async def test_the_plans_of_a_merge_and_of_reprocessing() -> None:
    names = "document 70 'Plain' and document 60 'Longer'"
    assert await plan_of("merge") == (
        "1 new document with 7 pages",
        (f"{names}: unchanged", STAYS),
        (
            f"new documents take nothing of {METADATA} of {names}",
            NEVER,
            PIPELINE,
            VERSION_OF_A_MERGE,
        ),
    )
    assert await plan_of("merge-delete") == (
        "1 new document with 7 pages",
        (f"new documents take {METADATA} of document 70 (its title with ' (merged)')", MOVES),
        (NEVER, PIPELINE, trashed(names), VERSION_OF_A_MERGE),
    )
    assert await plan_of("reprocess") == (
        "nothing new: the archived PDF and the text of the newest version again",
        ("the original file, earlier versions and every metadata",),
        ("the archived PDF, the extracted text and manual changes to the text",),
    )


async def test_new_documents_without_the_metadata() -> None:
    fake = paperless()
    document(fake, PLAIN).tags = [1]

    preview = await pdf_service(fake).edit(
        PLAIN, PAGE, options=Options(keep_metadata=False, dry_run=True)
    )
    result = await pdf_service(fake).edit(PLAIN, PAGE, options=Options(keep_metadata=False))

    assert preview.plan.keeps == ("document 70 'Plain': unchanged", STAYS)
    assert preview.plan.loses == (
        "pages 2-3: not in the result",
        f"new documents take nothing of {METADATA} of document 70 'Plain'",
        NEVER,
        PIPELINE,
    )
    assert document(fake, result.produced_ids[0]).tags == []


async def test_a_version_cannot_leave_the_metadata_out() -> None:
    service = pdf_service(paperless(writes=False))

    assert await refused(
        service.delete_pages(PLAIN, "1", options=Options(keep_metadata=False))
    ) == (
        "A new version has no metadata of its own: the document keeps its metadata.",
        "Leave keep_metadata out, or use new_document.",
    )


# --- which file is read -----------------------------------------------------------------------


async def test_every_page_of_a_newest_version_longer_than_the_first_file_can_be_edited() -> None:
    fake = paperless()

    result = await pdf_service(fake).edit(
        LONGER, [PageSlot(page=n) for n in (4, 3, 2, 1)], output="new_version"
    )

    assert (result.state, document(fake, LONGER).page_count) == ("success", 4)


async def test_pages_are_checked_against_the_version_that_is_read() -> None:
    service = pdf_service(paperless(writes=False))

    with pytest.raises(Validation, match="Page 3 does not exist: the document has 2 pages"):
        _ = await service.edit(LONGER, [PageSlot(page=3)], options=Options(version_id=LONGER))


async def test_a_version_the_document_does_not_list_is_not_found() -> None:
    service = pdf_service(paperless(writes=False))

    with pytest.raises(NotFound, match="Document 60 has no version 70"):
        _ = await service.edit(LONGER, PAGE, options=Options(version_id=PLAIN))


async def test_the_newest_version_by_its_id_is_no_older_version() -> None:
    fake = paperless()

    result = await pdf_service(fake).edit(
        LONGER,
        [PageSlot(page=n) for n in (1, 2, 3, 4)],
        output="new_version",
        options=Options(version_id=NEWEST),
    )

    assert result.plan.needs_confirm is False


async def test_going_back_to_an_older_file_needs_confirm() -> None:
    service = pdf_service(paperless(writes=False))
    older = Options(version_id=LONGER)
    both = [PageSlot(page=1), PageSlot(page=2)]
    back = (
        "make a new version of document 60 'Longer' from its older version 60: the document then "
        "shows that content again, and its newest file stays as an earlier version."
    )

    whole = await asked(service.edit(LONGER, both, output="new_version", options=older))
    part = await asked(service.delete_pages(LONGER, "1", options=older))

    assert whole == f"Nothing was changed. Confirming would {back}"
    assert part == f"Nothing was changed. Confirming would delete page 1 and {back}"


async def test_an_older_file_into_a_new_document_needs_no_confirm() -> None:
    fake = paperless()

    result = await pdf_service(fake).edit(LONGER, PAGE, options=Options(version_id=LONGER))

    assert (result.state, document(fake, result.produced_ids[0]).page_count) == ("success", 1)


NEWER = (
    "Document 60 has newer versions, which the new document would not hold, so it is not moved to "
    "the trash for it."
)


async def test_an_older_file_cannot_replace_the_original() -> None:
    service = pdf_service(paperless(writes=False))

    assert await refused(
        service.split(
            LONGER,
            ["1", "2"],
            output="replace_original",
            options=Options(version_id=LONGER, confirm=True),
        )
    ) == (NEWER, "Leave the original where it is, or leave version_id out.")
    assert await refused(
        service.merge(
            [PLAIN, LONGER], None, use_original_files=True, delete_originals=True, confirm=True
        )
    ) == (NEWER, "Leave the original where it is, or leave use_original_files out.")


async def test_an_older_locked_file_is_unlocked_by_its_id() -> None:
    fake = paperless()
    _ = await pdf_service(fake).remove_password(LOCKED, "s3cret", output="new_version")

    nothing = await pdf_service(fake).remove_password(LOCKED, "s3cret")
    again = await pdf_service(fake).remove_password(
        LOCKED, "s3cret", options=Options(version_id=LOCKED)
    )

    assert (nothing.state, again.state) == ("unknown", "success")


async def test_rotating_first_files_says_which_documents_go_back() -> None:
    service = pdf_service(paperless(writes=False))

    assert await asked(service.rotate([PLAIN, LONGER], 90, use_original_files=True)) == (
        "Nothing was changed. Confirming would rotate the first file of document 60 'Longer' as a "
        "new version: the document then shows the older content again, and its newest file stays "
        "as an earlier version."
    )


async def test_a_rotated_first_file_is_shown_again() -> None:
    fake = paperless()

    result = await pdf_service(fake).rotate([LONGER], 90, use_original_files=True, confirm=True)

    assert result.state == "success"
    assert document(fake, LONGER).page_count == 2


async def test_first_files_of_documents_without_versions_need_no_confirm_and_can_be_replaced() -> (
    None
):
    fake = paperless()
    service = pdf_service(fake)

    rotated = await service.rotate([LOCKED], 90, use_original_files=True)
    merged = await service.merge(
        [PLAIN, SCAN],
        None,
        use_original_files=True,
        archive_fallback=True,
        delete_originals=True,
        confirm=True,
    )

    assert (rotated.state, rotated.plan.needs_confirm) == ("success", False)
    assert merged.trashed_ids == (PLAIN, SCAN)


async def test_a_merge_of_first_files_has_their_pages() -> None:
    fake = paperless()

    result = await pdf_service(fake).merge([PLAIN, LONGER], None, use_original_files=True)

    assert result.plan.produces == "1 new document with 5 pages"
    assert document(fake, result.produced_ids[0]).page_count == 5


async def test_the_type_of_the_file_that_is_read_counts() -> None:
    fake = paperless()
    files = document(fake, LONGER).version_files
    files[NEWEST] = replace(files[NEWEST], mime_type="text/plain")

    with pytest.raises(Validation, match=r"Document 60 is not a PDF \(text/plain\)"):
        _ = await pdf_service(fake).edit(LONGER, PAGE)
    first = await pdf_service(fake).edit(LONGER, PAGE, options=Options(version_id=LONGER))
    assert first.state == "success"


# --- what a merge would leave out -------------------------------------------------------------


async def test_what_a_merge_would_leave_out_is_refused_before_it_starts() -> None:
    service = pdf_service(paperless(writes=False))

    assert await refused(service.merge([PLAIN, LOCKED], None)) == (
        "Document 73 cannot be merged: it is encrypted or unreadable.",
        "Remove the password first with remove_document_password.",
    )
    assert await refused(service.merge([PLAIN, SCAN], None)) == (
        "Document 72 is not a PDF (image/png).",
        "archive_fallback merges the archive PDF of other files, where there is one.",
    )
    for lost in (False, True):
        fake = paperless(writes=False)
        number, kind = (SCAN, "image/png") if lost else (TEXT, "text/plain")
        if lost:
            fake.state.missing.add((SCAN, "archive"))
        assert await refused(
            pdf_service(fake).merge([PLAIN, number], None, archive_fallback=True)
        ) == (
            f"Document {number} is not a PDF ({kind}) and has no archive PDF to merge.",
            "Leave the document out.",
        )


async def test_a_merge_takes_the_archive_of_another_file() -> None:
    fake = paperless()

    result = await pdf_service(fake).merge([PLAIN, SCAN], None, archive_fallback=True)

    assert document(fake, result.produced_ids[0]).page_count == 4
    assert result.message is None


@final
class Odd(FakePaperless):
    """A merge that leaves a document out or loses pages after all."""

    included: tuple[int, ...] | None = (PLAIN,)
    pages: int | None = 3
    unreadable: bool = False

    @override
    def _seen_tasks(
        self,
        task_ids: tuple[str, ...],
        expected: int | None = None,
        sources: tuple[int | None, ...] = (),
        outputs: tuple[int, ...] = (),
        included: tuple[int, ...] | None = None,
    ) -> StartedTasks:
        return super()._seen_tasks(task_ids, expected, sources, outputs, self.included)

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        record = await super().get_document(document_id)
        if document_id <= LOCKED:
            return record
        if self.unreadable:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)
        return record.model_copy(update={"page_count": self.pages})


async def test_a_merge_says_what_it_is_known_to_lack() -> None:
    fake = Odd(paperless().state, user="admin", allow_writes=True)
    service = pdf_service(fake)

    one = await service.merge([PLAIN, LONGER], None)
    fake.included, fake.unreadable = (LONGER,), True
    other = await service.merge([LONGER, PLAIN], None)
    fake.included, fake.unreadable, fake.pages = None, False, 7
    unknown = await service.merge([LONGER, PLAIN], None)

    assert one.message == ("Paperless left document 60 out. Document 74 has 3 pages instead of 7.")
    assert other.message == "Paperless left document 70 out."
    assert unknown.message is None


async def test_several_documents_left_out_are_all_named() -> None:
    fake = Odd(paperless().state, user="admin", allow_writes=True)
    fake.unreadable = True

    result = await pdf_service(fake).merge([PLAIN, LONGER, SCAN], None, archive_fallback=True)

    assert result.message == "Paperless left documents 60 and 72 out."


async def fingerprint(call: Awaitable[object]) -> str:
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await call
    return raised.value.fingerprint


@pytest.mark.decision("decision-61")
async def test_a_confirmation_binds_the_file_the_number_and_the_metadata() -> None:
    fake = paperless(writes=False)
    service = pdf_service(fake)

    def replaced(*, keep: bool = True) -> Awaitable[object]:
        options = Options(keep_metadata=keep)
        return service.split(PLAIN, ["1", "2"], output="replace_original", options=options)

    def merged() -> Awaitable[object]:
        return service.merge([PLAIN, LONGER], PLAIN, delete_originals=True)

    def going_back() -> Awaitable[object]:
        return service.rotate([LONGER], 90, use_original_files=True)

    async def all_of() -> list[str]:
        calls = (replaced(), replaced(keep=False), merged(), going_back())
        return [await fingerprint(call) for call in calls]

    first = await all_of()
    assert len(set(first)) == 4
    document(fake, PLAIN).archive_serial_number = 5
    numbered = await all_of()
    assert [a == b for a, b in zip(first, numbered, strict=True)] == [False, False, False, True]
    newer = document(fake, LONGER).versions[0].model_copy(update={"id": 99})
    document(fake, LONGER).versions.insert(0, newer)
    again = await all_of()
    assert [a == b for a, b in zip(numbered, again, strict=True)] == [True, True, False, False]


async def test_the_confirmation_of_a_merge_names_the_number_that_moves() -> None:
    fake = paperless(writes=False)
    document(fake, LONGER).archive_serial_number = 5

    message = await asked(pdf_service(fake).merge([PLAIN, LONGER], PLAIN, delete_originals=True))

    number = (
        "(they can be restored from there); archive serial number 5 (of document 60) moves to the "
        "new document, any other stays with its trashed original and is blocked."
    )
    assert message.endswith(number)
