"""Originals are trashed last: every way a composed operation can end, and the state after it."""

from typing import TYPE_CHECKING, final, override

import pytest
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded
from harness import pdf_service

from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    PermissionDenied,
    UpstreamTimeout,
)
from mcp_server_paperless.domain.pdf import StartedTasks
from mcp_server_paperless.services.pdf import Options

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp_server_paperless.domain.documents import DocumentPatch, DocumentRecord
    from mcp_server_paperless.domain.pdf import PdfResult
    from mcp_server_paperless.domain.tasks import TaskState, TaskStatus

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-60")]

DOC, OTHER = 50, 51
YES = Options(confirm=True)
NUMBER = 777
WAY_BACK = (
    "stays there until the trash is emptied (by default after 30 days) and can be restored in the "
    "Paperless web UI, or with restore_from_trash if the trash toolset is enabled."
)
GONE = f"Now in the trash: document {DOC}. It {WAY_BACK}"


@final
class Paperless(FakePaperless):
    """The fake, with a switch for every way the work can go wrong."""

    def __init__(self, state: FakeState) -> None:
        super().__init__(state, user="admin", allow_writes=True)
        self.states: dict[int, TaskState] = {}  # by the place of the task
        self.empty: set[int] = set()  # tasks that succeed without a document
        self.double: bool = False
        self.lose: int = 0
        self.unreadable: Callable[[int], bool] = lambda _: False
        self.wrong_pages: dict[int, int | None] = {}
        self.included: tuple[int, ...] | None = None
        self.refuse: Callable[[int, int | None], bool] = lambda _number, _asn: False
        self.refuse_deletes: set[int] = set()
        self.after_start: Callable[[], None] = lambda: None
        self.started: list[str] = []
        self.swap: bool = False
        self.hide_tasks: bool = False
        self.now: float = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay

    @override
    def _seen_tasks(
        self,
        task_ids: tuple[str, ...],
        expected: int | None = None,
        sources: tuple[int | None, ...] = (),
        outputs: tuple[int, ...] = (),
        included: tuple[int, ...] | None = None,
    ) -> StartedTasks:
        if self.swap:
            task_ids, outputs = task_ids[::-1], outputs[::-1]
        self.started.extend(task_ids)
        self.after_start()
        if self.hide_tasks:
            return StartedTasks(ids=(), expected=len(task_ids), problem="forbidden")
        kept = len(task_ids) - self.lose
        return super()._seen_tasks(
            task_ids[:kept],
            len(task_ids) if expected is None else expected,
            sources[:kept],
            outputs[:kept],
            included if self.included is None else self.included or None,
        )

    @override
    async def get_task(self, task_id: str) -> TaskStatus:
        status = await super().get_task(task_id)
        place = self.started.index(task_id)
        if place in self.states:
            return status.model_copy(update={"state": self.states[place], "document_ids": ()})
        if place in self.empty:
            return status.model_copy(update={"document_ids": ()})
        if self.double:
            return status.model_copy(update={"document_ids": (*status.document_ids, 9)})
        return status

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        if self.unreadable(document_id):
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)
        record = await super().get_document(document_id)
        if self.started and document_id > OTHER:
            place = document_id - OTHER - 1
            if place in self.wrong_pages:
                return record.model_copy(update={"page_count": self.wrong_pages[place]})
        return record

    @override
    async def update_document(self, document_id: int, patch: DocumentPatch) -> DocumentRecord:
        if self.refuse(document_id, patch.archive_serial_number):
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)
        return await super().update_document(document_id, patch)

    @override
    async def delete_document(self, document_id: int) -> None:
        if document_id in self.refuse_deletes:
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        await super().delete_document(document_id)


def paperless(asn: int | None = None) -> Paperless:
    state = seeded()
    state.documents.append(
        FakeDocument(id=DOC, title="Scan", owner=2, page_count=4, archive_serial_number=asn)
    )
    state.documents.append(FakeDocument(id=OTHER, title="Other", owner=2, page_count=2))
    return Paperless(state)


def document(fake: FakePaperless, number: int) -> FakeDocument:
    [found] = [d for d in (*fake.state.documents, *fake.state.trash) if d.id == number]
    return found


def trash(fake: FakePaperless) -> list[int]:
    return [d.id for d in fake.state.trash]


async def split(fake: Paperless, options: Options = YES) -> PdfResult:
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)
    return await service.split(DOC, ["1", "2-4"], output="replace_original", options=options)


async def shorten(fake: Paperless, options: Options = YES) -> PdfResult:
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)
    return await service.delete_pages(DOC, "1", output="replace_original", options=options)


async def merge(fake: Paperless, metadata: int | None = None) -> PdfResult:
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)
    return await service.merge([DOC, OTHER], metadata, delete_originals=True, confirm=True)


async def test_every_part_verified_the_original_goes_and_nothing_is_left_to_do() -> None:
    fake = paperless()

    result = await split(fake)

    assert (result.state, result.trashed_ids, trash(fake)) == ("success", (DOC,), [DOC])
    assert (result.message, result.next, result.wait) == (GONE, None, "finished")
    assert [document(fake, n).page_count for n in result.produced_ids] == [1, 3]


async def test_without_a_wait_nothing_is_checked_and_the_next_calls_are_named() -> None:
    fake = paperless()

    result = await split(fake, Options(confirm=True, wait_seconds=0))

    assert (result.trashed_ids, trash(fake)) == ((), [])
    assert result.next == f"delete_document(document_id={DOC})"
    made = ", ".join(str(n) for n in result.produced_ids)
    assert result.message == (
        "Nothing was moved to the trash: wait_seconds is 0, so nothing was checked. "
        f"What was made stays: documents {made}."
    )


async def test_without_a_wait_running_tasks_are_named_first() -> None:
    fake = paperless()
    fake.states = {0: "started"}

    result = await split(fake, Options(confirm=True, wait_seconds=0))

    assert result.next == (
        f'get_task(task_id="{fake.started[0]}", wait_seconds=30), then '
        f"delete_document(document_id={DOC})"
    )


@pytest.mark.parametrize("state", ["pending", "started"])
async def test_an_unfinished_part_keeps_the_original_and_names_how_to_go_on(
    state: TaskState,
) -> None:
    fake = paperless()
    fake.states = {1: state}

    result = await split(fake)

    assert (result.wait, result.waited_seconds, trash(fake)) == ("timed_out", 30.0, [])
    assert result.message is not None
    assert "Nothing was moved to the trash: Paperless is not known to be done." in result.message
    assert result.next == (
        f'get_task(task_id="{fake.started[1]}", wait_seconds=30), then '
        f"delete_document(document_id={DOC})"
    )


@pytest.mark.parametrize("state", ["failure", "revoked"])
async def test_a_failed_part_keeps_the_original(state: TaskState) -> None:
    fake = paperless()
    fake.states = {1: state}

    result = await split(fake)

    assert (result.trashed_ids, trash(fake), result.next) == ((), [], None)
    [made] = result.produced_ids
    assert result.message == (
        f"Nothing was moved to the trash: a task failed. What was made stays: document {made}."
    )


async def test_a_part_that_made_no_document_or_several_keeps_the_original() -> None:
    rejected, split_again = paperless(), paperless()
    rejected.empty = {1}
    split_again.double = True

    none, several = await split(rejected), await split(split_again)

    for result, fake in ((none, rejected), (several, split_again)):
        assert (result.state, result.trashed_ids, trash(fake)) == ("success", (), [])
    assert none.message is not None
    assert f"task {rejected.started[1]} made nothing instead of one document" in none.message
    assert several.message is not None
    assert "made documents 52, 9 instead of one document" in several.message


async def test_a_part_that_cannot_be_read_keeps_the_original() -> None:
    fake = paperless()
    fake.unreadable = lambda number: number == OTHER + 2

    result = await split(fake)

    assert (result.trashed_ids, trash(fake)) == ((), [])
    assert result.message is not None
    assert "document 53 could not be read (Paperless did not respond in time)" in result.message
    assert result.next == f"delete_document(document_id={DOC})"


@pytest.mark.parametrize(
    ("pages", "words"),
    [(2, "has 2 pages instead of 3"), (None, "has an unknown number of pages instead of 3")],
)
async def test_a_part_with_the_wrong_pages_keeps_the_original(
    pages: int | None, words: str
) -> None:
    fake = paperless()
    fake.wrong_pages = {1: pages}

    result = await split(fake)

    assert (result.trashed_ids, trash(fake), result.next) == ((), [], None)
    assert result.message is not None
    assert words in result.message


async def test_parts_are_held_against_their_own_number_not_their_order() -> None:
    fake = paperless()
    fake.swap = True

    result = await split(fake)

    assert [document(fake, n).page_count for n in result.produced_ids] == [3, 1]
    assert (result.trashed_ids, trash(fake)) == ((DOC,), [DOC])


async def test_a_task_that_never_showed_up_keeps_the_original() -> None:
    fake = paperless()
    fake.lose = 1

    result = await split(fake)

    assert (result.state, result.trashed_ids, trash(fake)) == ("unknown", (), [])
    assert result.message is not None
    assert "what Paperless makes could not be followed" in result.message
    assert result.next == (
        "find the new documents with search_documents (newest added first) and check them, then "
        f"delete_document(document_id={DOC})"
    )


async def test_a_user_who_may_not_view_tasks_keeps_the_original() -> None:
    fake = paperless()
    fake.hide_tasks = True

    result = await split(fake)

    assert (result.state, result.trashed_ids, trash(fake)) == ("unknown", (), [])
    assert result.next is not None
    assert result.next.startswith("find the new documents with search_documents")
    assert [d.page_count for d in fake.state.documents[-2:]] == [1, 3]


@pytest.mark.parametrize("what", ["file", "serial", "unreadable"])
async def test_an_original_that_changed_meanwhile_is_kept(what: str) -> None:
    fake = paperless()

    def change() -> None:
        original = document(fake, DOC)
        if what == "file":
            newer = original.versions[0].model_copy(update={"id": 900, "is_root": False})
            original.versions.insert(0, newer)
        elif what == "serial":
            original.archive_serial_number = NUMBER
        else:
            fake.unreadable = lambda number: number == DOC

    fake.after_start = change

    result = await split(fake)

    assert (result.trashed_ids, trash(fake), result.next) == ((), [], None)
    assert result.message is not None
    unread = "document 50 could not be read again (Paperless did not respond in time)"
    changed = "document 50 was changed meanwhile; look at it again"
    words = unread if what == "unreadable" else changed
    assert words in result.message


@pytest.mark.parametrize(("seconds", "gone"), [(45.0, (DOC,)), (45.1, ())])
async def test_nothing_is_trashed_later_than_45_seconds_after_the_start(
    seconds: float, gone: tuple[int, ...]
) -> None:
    fake = paperless()

    def wait() -> None:
        fake.now += seconds

    fake.after_start = wait

    result = await split(fake)

    assert result.trashed_ids == gone
    if not gone:
        assert result.next == f"delete_document(document_id={DOC})"
        assert result.message is not None
        assert "the call took too long, though everything was made and checked" in result.message


async def test_an_original_that_cannot_be_trashed_is_named() -> None:
    fake = paperless()
    fake.refuse_deletes = {DOC}

    result = await split(fake)

    assert (result.trashed_ids, trash(fake)) == ((), [])
    assert result.message == (
        f"Not moved to the trash: document {DOC} (The Paperless user may not perform this action)."
    )
    assert result.next == f"delete_document(document_id={DOC})"


async def test_a_failed_password_removal_keeps_the_locked_original() -> None:
    fake = paperless()
    locked = document(fake, DOC)
    locked.password, locked.page_count, locked.archive_serial_number = "s3cret", None, NUMBER
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)
    fake.empty = {0}

    kept = await service.remove_password(DOC, "s3cret", output="replace_original", options=YES)
    fake.empty = set()
    fake.started.clear()
    gone = await service.remove_password(DOC, "s3cret", output="replace_original", options=YES)

    assert (kept.trashed_ids, gone.trashed_ids, trash(fake)) == ((), (DOC,), [DOC])
    assert document(fake, DOC).archive_serial_number == NUMBER  # it is not handed over
    assert document(fake, gone.produced_ids[0]).archive_serial_number is None


async def test_an_unlocked_copy_without_pages_keeps_the_locked_original() -> None:
    fake = paperless()
    document(fake, DOC).password, document(fake, DOC).page_count = "s3cret", None
    fake.wrong_pages = {0: None}
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)

    result = await service.remove_password(DOC, "s3cret", output="replace_original", options=YES)

    assert trash(fake) == []
    assert result.message is not None
    assert "has an unknown number of pages. What" in result.message


# --- the archive serial number --------------------------------------------------------------


async def test_the_serial_number_moves_to_the_only_new_document() -> None:
    fake = paperless(NUMBER)

    result = await shorten(fake)

    [made] = result.produced_ids
    assert (result.trashed_ids, result.message) == ((DOC,), GONE)
    assert document(fake, made).archive_serial_number == NUMBER
    assert document(fake, DOC).archive_serial_number is None


async def test_several_new_documents_leave_the_serial_number_with_the_original() -> None:
    fake = paperless(NUMBER)

    result = await split(fake)

    assert result.trashed_ids == (DOC,)
    assert document(fake, DOC).archive_serial_number == NUMBER
    assert [document(fake, n).archive_serial_number for n in result.produced_ids] == [None, None]


async def test_a_new_document_with_a_serial_number_of_its_own_keeps_it() -> None:
    fake = paperless(NUMBER)

    def barcode() -> None:
        fake.state.documents[-1].archive_serial_number = 5

    fake.after_start = barcode

    result = await shorten(fake)

    assert result.trashed_ids == (DOC,)
    assert document(fake, DOC).archive_serial_number == NUMBER
    assert result.message == (
        f"{GONE} Document 52 has the archive serial number 5 already, so document {DOC} keeps "
        f"{NUMBER}."
    )


async def test_a_serial_number_that_cannot_move_is_put_back_and_nothing_is_trashed() -> None:
    fake = paperless(NUMBER)
    fake.refuse = lambda number, _: number != DOC

    result = await shorten(fake)

    assert (result.trashed_ids, trash(fake)) == ((), [])
    assert document(fake, DOC).archive_serial_number == NUMBER
    assert result.message == (
        f"Nothing was moved to the trash: archive serial number {NUMBER} could not be moved to "
        "document 52 (Paperless did not respond in time)."
    )
    assert result.next == f"delete_document(document_id={DOC})"


async def test_a_serial_number_that_cannot_be_put_back_either_is_named_with_both() -> None:
    fake = paperless(NUMBER)
    fake.refuse = lambda _, asn: asn is not None

    result = await shorten(fake)

    assert trash(fake) == []
    assert result.message is not None
    both = (
        f"Archive serial number {NUMBER} could not be put back on document {DOC} either: look at "
        f"documents {DOC} and 52, and set it with update_document."
    )
    assert result.message.endswith(both)


# --- merge ----------------------------------------------------------------------------------


async def test_a_merge_trashes_the_originals_once_the_merged_document_is_checked() -> None:
    fake = paperless()

    result = await merge(fake)

    assert (result.trashed_ids, trash(fake)) == ((DOC, OTHER), [DOC, OTHER])
    assert result.message == f"Now in the trash: documents {DOC}, {OTHER}. Each {WAY_BACK}"
    assert result.next is None


@pytest.mark.parametrize(
    ("metadata", "numbers", "moved"),
    [(None, (None, 8), 8), (OTHER, (7, 8), 8), (None, (7, 8), 7), (OTHER, (7, None), 7)],
)
async def test_a_merge_hands_over_the_number_of_the_metadata_document_or_the_first(
    metadata: int | None, numbers: tuple[int | None, int | None], moved: int
) -> None:
    fake = paperless()
    for number, asn in zip((DOC, OTHER), numbers, strict=True):
        document(fake, number).archive_serial_number = asn

    result = await merge(fake, metadata)

    assert document(fake, result.produced_ids[0]).archive_serial_number == moved
    left = [document(fake, n).archive_serial_number for n in (DOC, OTHER)]
    assert left == [None if asn == moved else asn for asn in numbers]


@pytest.mark.parametrize("included", [(DOC,), (OTHER,)])
async def test_a_document_paperless_left_out_keeps_every_original(
    included: tuple[int, ...],
) -> None:
    fake = paperless()
    fake.included = included
    missing = OTHER if included == (DOC,) else DOC

    result = await merge(fake)

    assert trash(fake) == []
    assert result.message is not None
    assert f"Nothing was moved to the trash: Paperless left document {missing} out." in (
        result.message
    )


async def test_a_merge_with_fewer_pages_keeps_every_original() -> None:
    fake = paperless()
    fake.wrong_pages = {0: 4}

    result = await merge(fake)

    assert trash(fake) == []
    assert result.message is not None
    assert "document 52 has 4 pages instead of 6" in result.message


async def test_a_merge_that_can_be_checked_neither_by_name_nor_by_pages_keeps_originals() -> None:
    fake = paperless()
    document(fake, OTHER).mime_type, document(fake, OTHER).page_count = "image/png", None
    fake.included = ()
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)

    result = await service.merge(
        [DOC, OTHER], None, delete_originals=True, archive_fallback=True, confirm=True
    )

    assert trash(fake) == []
    assert result.message is not None
    assert "it cannot be told whether every document is in the merged one" in result.message


async def test_the_second_original_that_cannot_be_trashed_is_named_and_the_first_is_gone() -> None:
    fake = paperless()
    fake.refuse_deletes = {OTHER}

    result = await merge(fake)

    assert (result.trashed_ids, trash(fake)) == ((DOC,), [DOC])
    assert result.next == f"delete_document(document_id={OTHER})"
    assert result.message is not None
    assert result.message.startswith(f"{GONE} Not moved to the trash: document {OTHER} (")


async def test_a_kept_merge_names_every_original_it_would_have_trashed() -> None:
    fake = paperless()
    fake.states = {0: "started"}

    result = await merge(fake)

    assert result.next == (
        f'get_task(task_id="{fake.started[0]}", wait_seconds=30), then '
        f"delete_document(document_id={DOC}), then delete_document(document_id={OTHER})"
    )


async def test_a_refusal_comes_before_anything_is_started() -> None:
    fake = paperless()

    with pytest.raises(ConfirmationRequired):
        _ = await split(fake, Options())

    assert fake.started == []
