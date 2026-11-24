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
    from collections.abc import Callable, Sequence

    from mcp_server_paperless.domain.documents import DocumentPatch, DocumentRecord
    from mcp_server_paperless.domain.pdf import LookupProblem, PdfResult
    from mcp_server_paperless.domain.tasks import TaskState, TaskStatus
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-60")]

DOC, OTHER = 50, 51
YES = Options(confirm=True)
NUMBER = 777
WAY_BACK = (
    "stays there until the trash is emptied (by default after 30 days) and can be restored in the "
    "Paperless web UI, or with restore_from_trash if the trash toolset is enabled."
)
FIND = "find the new documents with search_documents (newest added first) and check them"
GONE = f"Now in the trash: document {DOC}. It {WAY_BACK}"


@final
class Paperless(FakePaperless):
    """The fake, with a switch for every way the work can go wrong."""

    def __init__(self, state: FakeState) -> None:
        super().__init__(state, user="admin", allow_writes=True)
        self.states: dict[int, TaskState] = {}  # by the place of the task
        self.empty: set[int] = set()  # tasks that succeed without a document
        self.double: bool = False
        self.same: int | None = None  # what every task names
        self.lose: int = 0
        self.unreadable: Callable[[int], bool] = lambda _: False
        self.wrong_pages: dict[int, int | None] = {}
        self.included: tuple[int, ...] | None = None
        self.refuse: Callable[[int, int | None], bool] = lambda _number, _asn: False
        self.refuse_deletes: set[int] = set()
        self.time_out: set[int] = set()  # no answer, and nothing happened so far
        self.lose_deletes: set[int] = set()  # done, but the answer is lost
        self.before_delete: Callable[[int], None] = lambda _: None
        self.doubt: str | None = None
        self.patches: list[tuple[int, int | None]] = []
        self.may: bool | None = True
        self.after_start: Callable[[], None] = lambda: None
        self.started: list[str] = []
        self.problem: LookupProblem | None = None
        self.no_fields: bool = False
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
        started = super()._seen_tasks(
            task_ids[:kept],
            len(task_ids) if expected is None else expected,
            sources[:kept],
            outputs[:kept],
            included if self.included is None else self.included or None,
        )
        return started.model_copy(update={"doubt": self.doubt, "problem": self.problem})

    @override
    async def may_delete(self, owners: Sequence[int | None]) -> bool | None:
        return self.may

    @override
    async def get_task(self, task_id: str) -> TaskStatus:
        status = await super().get_task(task_id)
        place = self.started.index(task_id)
        if place in self.states:
            return status.model_copy(update={"state": self.states[place], "document_ids": ()})
        if place in self.empty:
            return status.model_copy(update={"document_ids": ()})
        if self.same is not None:
            return status.model_copy(update={"document_ids": (self.same,)})
        if self.double:
            return status.model_copy(update={"document_ids": (*status.document_ids, 9)})
        return status

    @override
    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        if self.no_fields:
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        return await super().list_taxonomy(kind)

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
        self.patches.append((document_id, patch.archive_serial_number))
        return await super().update_document(document_id, patch)

    @override
    async def delete_document(self, document_id: int) -> None:
        self.before_delete(document_id)
        if document_id in self.time_out:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)
        if document_id in self.refuse_deletes:
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        await super().delete_document(document_id)
        if document_id in self.lose_deletes:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)


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


def after(
    fake: Paperless,
    *pages: int,
    then: str | None = None,
    unchanged: str = f"only if document {DOC} is unchanged",
) -> str:
    """The calls that finish a replacement whose tasks were not checked."""
    waits = ", then ".join(f'get_task(task_id="{t}", wait_seconds=30)' for t in fake.started)
    made = ", ".join(f"{n} page{'' if n == 1 else 's'}" for n in pages)
    return (
        f"{waits}; only if every one succeeded and made exactly one document (get_document), with "
        f"{made}, and {unchanged}: {then or f'delete_document(document_id={DOC})'}"
    )


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
    assert result.next == after(fake, 1, 3)
    made = ", ".join(str(n) for n in result.produced_ids)
    assert result.message == (
        "Nothing was moved to the trash: wait_seconds is 0, so nothing was checked. "
        f"What was made stays: documents {made}."
    )


async def test_without_a_wait_running_tasks_are_named_first() -> None:
    fake = paperless()
    fake.states = {0: "started"}

    result = await split(fake, Options(confirm=True, wait_seconds=0))

    assert result.next == after(fake, 1, 3)


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
    assert result.next == after(fake, 1, 3)


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
    assert result.next == "get_document(document_id=53)"  # never a deletion


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
    assert result.next == FIND


async def test_a_user_who_may_not_view_tasks_keeps_the_original() -> None:
    fake = paperless()
    fake.hide_tasks = True

    result = await split(fake)

    assert (result.state, result.trashed_ids, trash(fake)) == ("unknown", (), [])
    assert result.next == FIND
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
        assert result.next == (
            f"only if document {DOC} is unchanged: delete_document(document_id={DOC})"
        )
        assert result.message is not None
        assert "the call took too long, though everything was made and checked" in result.message


async def test_an_original_that_cannot_be_trashed_is_named() -> None:
    fake, numbered = paperless(), paperless(NUMBER)
    fake.refuse_deletes = numbered.refuse_deletes = {DOC}
    named = (
        f"Not moved to the trash: document {DOC} (The Paperless user may not perform this action)."
    )

    plain, kept = await split(fake), await shorten(numbered)

    assert (plain.trashed_ids, trash(fake)) == ((), [])
    assert plain.message == named
    assert plain.next == (
        f"only if document {DOC} is unchanged: delete_document(document_id={DOC})"
    )
    # The number moved before the deletion: it goes back to the original that stays.
    assert (kept.trashed_ids, kept.message) == ((), named)
    assert document(numbered, DOC).archive_serial_number == NUMBER
    assert document(numbered, 52).archive_serial_number is None
    assert kept.next == f"only if document {DOC} is unchanged: {HAND_OVER}"


HAND_OVER = (
    f'update_document(document_id={DOC}, clear=["archive_serial_number"]), then '
    f"update_document(document_id=52, archive_serial_number={NUMBER}), then "
    f"delete_document(document_id={DOC})"
)


async def test_a_number_that_cannot_go_back_is_told_where_it_is() -> None:
    fake = paperless(NUMBER)
    fake.refuse_deletes = {DOC}
    fake.refuse = lambda number, asn: number == 52 and asn is None

    result = await shorten(fake)

    assert document(fake, 52).archive_serial_number == NUMBER
    assert result.message is not None
    assert result.message.endswith(f" Archive serial number {NUMBER} is on document 52.")
    assert result.next == (
        f"only if document {DOC} is unchanged: delete_document(document_id={DOC})"
    )


async def test_a_number_on_no_document_is_told_with_the_call_that_sets_it() -> None:
    fake = paperless(NUMBER)
    fake.refuse_deletes = {DOC}
    fake.refuse = lambda number, asn: number == DOC and asn is not None

    result = await shorten(fake)

    assert result.message is not None
    text = (
        f" Archive serial number {NUMBER} is neither on document {DOC} nor on document 52: set it "
        f"with update_document(document_id={DOC}, archive_serial_number={NUMBER})."
    )
    assert result.message.endswith(text)
    assert result.next is None


async def test_a_number_that_went_back_although_the_answer_was_lost_needs_no_word() -> None:
    fake = paperless(NUMBER)
    fake.refuse_deletes = {DOC}

    def lost(number: int, asn: int | None) -> bool:
        if number == DOC and asn is not None:
            document(fake, DOC).archive_serial_number = asn
            return True
        return False

    fake.refuse = lost

    result = await shorten(fake)

    assert result.message is not None
    assert "Archive serial number" not in result.message
    assert result.next == f"only if document {DOC} is unchanged: {HAND_OVER}"


async def test_a_number_that_cannot_be_found_is_told_as_unknown() -> None:
    fake = paperless(NUMBER)
    fake.refuse_deletes = {DOC}

    def blind(number: int) -> None:
        if number == DOC:
            fake.refuse_deletes = {DOC}
            fake.unreadable = lambda n: n == 52

    fake.before_delete = blind

    result = await shorten(fake)

    assert result.message is not None
    text = (
        f" It is not known where archive serial number {NUMBER} is: look at documents {DOC} and 52."
    )
    assert result.message.endswith(text)
    assert result.next is None


async def test_a_deletion_whose_answer_was_lost_is_looked_at() -> None:
    fake = paperless(NUMBER)
    fake.lose_deletes = {DOC}

    result = await shorten(fake)

    assert (result.trashed_ids, result.message, result.next) == ((DOC,), GONE, None)
    assert document(fake, 52).archive_serial_number == NUMBER


@pytest.mark.parametrize("readable", [True, False])
async def test_a_deletion_without_an_answer_may_still_happen(*, readable: bool) -> None:
    fake = paperless(NUMBER)
    fake.time_out = {DOC}

    def blind(number: int) -> None:
        if not readable:
            fake.unreadable = lambda n: n == number

    fake.before_delete = blind

    result = await shorten(fake)

    assert result.trashed_ids == ()
    assert document(fake, 52).archive_serial_number == NUMBER  # not back on what may still go
    assert result.message == (
        f"It is not known whether document {DOC} went to the trash (Paperless did not respond in "
        f"time). If it did, it {WAY_BACK} Archive serial number {NUMBER} is on document 52."
    )
    assert result.next == (
        f"only if get_document(document_id={DOC}) still finds it unchanged: "
        f"delete_document(document_id={DOC})"
    )


@pytest.mark.parametrize("how", ["refused", "late", "unread", "unknown"])
async def test_next_never_overwrites_a_number_the_new_document_has(how: str) -> None:
    fake = paperless(NUMBER)
    if how == "refused":
        fake.refuse_deletes = {DOC}
    elif how == "unknown":
        fake.time_out = {DOC}
    elif how == "unread":
        fake.may = None

    def barcode() -> None:
        fake.state.documents[-1].archive_serial_number = 5
        if how == "late":
            fake.now += 46

    fake.after_start = barcode

    result = await shorten(fake)

    condition = (
        f"only if get_document(document_id={DOC}) still finds it unchanged"
        if how == "unknown"
        else f"only if document {DOC} is unchanged"
    )
    assert result.next == (
        f"{condition}: delete_document(document_id={DOC}); archive serial number {NUMBER} of "
        f"document {DOC} does not move"
    )
    assert document(fake, DOC).archive_serial_number == NUMBER
    assert document(fake, 52).archive_serial_number == 5


@pytest.mark.parametrize("numbered", [False, True])
async def test_after_an_unknown_deletion_every_original_has_its_own_condition(
    *, numbered: bool
) -> None:
    fake = paperless()
    document(fake, OTHER).archive_serial_number = NUMBER if numbered else None
    fake.time_out = {DOC}

    result = await merge(fake)

    steps = (
        f'update_document(document_id={OTHER}, clear=["archive_serial_number"]), then '
        f"update_document(document_id=52, archive_serial_number={NUMBER}), then "
    )
    assert result.next == (
        f"only if get_document(document_id={DOC}) still finds it unchanged: "
        f"delete_document(document_id={DOC}). Whatever became of it, "
        f"only if get_document(document_id={OTHER}) still finds it unchanged: "
        f"{steps if numbered else ''}delete_document(document_id={OTHER})"
    )


async def test_originals_after_one_whose_deletion_is_not_known_stay_and_are_named() -> None:
    fake = paperless()
    document(fake, OTHER).archive_serial_number = NUMBER
    fake.time_out = {DOC}

    result = await merge(fake)

    assert (result.trashed_ids, trash(fake)) == ((), [])
    assert document(fake, OTHER).archive_serial_number == NUMBER
    assert result.message is not None
    assert result.message.endswith(
        f"If it did, it {WAY_BACK} Not moved to the trash: document {OTHER}."
    )


async def test_a_number_someone_set_on_the_original_is_not_overwritten() -> None:
    fake = paperless(NUMBER)
    fake.refuse_deletes = {DOC}

    def someone(number: int) -> None:
        document(fake, number).archive_serial_number = 900

    fake.before_delete = someone

    result = await shorten(fake)

    assert document(fake, DOC).archive_serial_number == 900
    assert document(fake, 52).archive_serial_number == NUMBER
    assert result.message is not None
    assert result.message.endswith(
        f" Archive serial number {NUMBER} is on document 52; document {DOC} has another one now."
    )
    assert result.next is None


async def test_a_number_that_got_lost_while_someone_set_another_is_told() -> None:
    fake = paperless(NUMBER)

    def refuse(number: int, asn: int | None) -> bool:
        if number == 52 and asn is not None:
            document(fake, DOC).archive_serial_number = 900
            return True
        return False

    fake.refuse = refuse

    result = await shorten(fake)

    assert document(fake, DOC).archive_serial_number == 900
    assert result.message is not None
    text = (
        f" Archive serial number {NUMBER} is on neither document {DOC}, which has another one "
        "now, nor document 52."
    )
    assert result.message.endswith(text)
    assert result.next is None


@pytest.mark.parametrize("state", ["started", "unknown"])
async def test_a_document_left_out_is_named_whatever_the_wait_was(state: str) -> None:
    fake = paperless()
    fake.included = (DOC,)
    fake.states = {0: "started"}
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)

    unwaited = await service.merge(
        [DOC, OTHER], None, delete_originals=True, confirm=True, wait_seconds=0
    )
    fake.started.clear()
    if state == "unknown":
        fake.states = {}
        document(fake, OTHER).mime_type, document(fake, OTHER).page_count = "image/png", None
        fake.included = ()
    waited = await service.merge(
        [DOC, OTHER],
        None,
        delete_originals=True,
        archive_fallback=True,
        confirm=True,
        wait_seconds=0 if state == "unknown" else 5,
    )

    out = f"Nothing was moved to the trash: Paperless left document {OTHER} out."
    untold = "it cannot be told whether every document is in the merged one"
    assert unwaited.message is not None
    assert out in unwaited.message
    assert waited.message is not None
    assert (out if state == "started" else untold) in waited.message
    assert (unwaited.next, waited.next) == (None, None)


@pytest.mark.parametrize("operation", ["split", "merge", "unlock"])
async def test_a_user_who_may_not_delete_is_refused_before_anything_starts(operation: str) -> None:
    fake = paperless()
    fake.may = False
    document(fake, OTHER).password, document(fake, OTHER).page_count = "s3cret", None
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)
    calls = {
        "split": lambda: split(fake, Options(dry_run=True)),
        "merge": lambda: service.merge([DOC, DOC + 2], None, delete_originals=True, confirm=True),
        "unlock": lambda: service.remove_password(
            OTHER, "s3cret", output="replace_original", options=YES
        ),
    }
    fake.state.documents.append(FakeDocument(id=DOC + 2, title="More", owner=2))

    with pytest.raises(PermissionDenied) as raised:
        _ = await calls[operation]()

    assert raised.value.message == (
        "The Paperless user may not delete the original, so nothing was started."
        if operation != "merge"
        else "The Paperless user may not delete every original, so nothing was started."
    )
    assert raised.value.hint == (
        "Trashing needs the permission to delete documents and ownership (or no owner). Keep the "
        "originals, or ask their owner."
    )
    assert fake.started == []


async def test_a_right_that_cannot_be_known_does_not_stop_the_call_and_the_plan_says_so() -> None:
    fake = paperless()
    fake.may = None
    unread = (
        "the rights of this Paperless user cannot be read (view_uisettings): the originals then "
        "stay, and the result names what was started and how to go on"
    )
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)

    one = await split(fake, Options(dry_run=True))
    two = await service.merge([DOC, OTHER], None, delete_originals=True, dry_run=True)
    with pytest.raises(ConfirmationRequired) as asked:
        _ = await split(fake, Options())

    assert unread in one.plan.keeps
    assert unread in two.plan.keeps
    assert unread in asked.value.message
    assert unread not in (await split(paperless())).plan.keeps


UNREAD = "the rights of this Paperless user could not be read"


@pytest.mark.parametrize("operation", ["split", "merge", "unlock"])
async def test_originals_stay_when_the_rights_could_not_be_read_whatever_comes_later(
    operation: str,
) -> None:
    fake = paperless(NUMBER)
    fake.may = None  # one read failed; every later one succeeds
    if operation == "unlock":
        document(fake, DOC).password, document(fake, DOC).page_count = "s3cret", None
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)

    if operation == "split":
        result = await split(fake)
    elif operation == "merge":
        result = await merge(fake)
    else:
        result = await service.remove_password(
            DOC, "s3cret", output="replace_original", options=YES
        )

    assert (result.state, result.trashed_ids, trash(fake)) == ("success", (), [])
    assert fake.patches == []  # no number moved either
    assert result.message is not None
    assert result.message.startswith(f"Nothing was moved to the trash: {UNREAD}. What was made")
    assert result.next is not None
    assert result.next.startswith("only if document")


@pytest.mark.parametrize("operation", ["split", "merge"])
@pytest.mark.parametrize(("shown", "now"), [(None, True), (True, None)])
async def test_a_confirmation_covers_only_what_was_known_when_it_was_shown(
    operation: str, *, shown: bool | None, now: bool | None
) -> None:
    fake = paperless()
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)

    async def ask() -> PdfResult:
        if operation == "split":
            return await split(fake, Options())
        return await service.merge([DOC, OTHER], None, delete_originals=True)

    fake.may = shown
    with pytest.raises(ConfirmationRequired) as first:
        _ = await ask()
    fake.may = now
    with pytest.raises(ConfirmationRequired) as second:
        _ = await ask()

    assert first.value.fingerprint != second.value.fingerprint
    assert fake.started == []


async def test_files_that_cannot_be_told_apart_keep_the_original() -> None:
    fake = paperless()
    fake.doubt = "another operation of this user ran at the same time"

    result = await split(fake)

    assert (result.trashed_ids, trash(fake), result.next) == ((), [], FIND)
    assert result.message is not None
    text = (
        "Nothing was moved to the trash: it cannot be told which files this call made (another "
        "operation of this user ran at the same time)."
    )
    assert result.message.startswith(text)


async def test_an_operation_that_was_not_followed_names_its_queued_task() -> None:
    fake = paperless()
    fake.problem = "unconfirmed"

    result = await split(fake)

    assert trash(fake) == []
    assert result.next == f'get_task(task_id="{fake.started[0]}", wait_seconds=30)'


async def test_unlocking_a_file_that_is_not_locked_suggests_nothing() -> None:
    fake = paperless()
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)

    result = await service.remove_password(DOC, "x", output="replace_original", options=YES)

    assert (result.trashed_ids, result.next) == ((), None)
    assert result.message == (
        "Paperless accepted the request, but no task showed up for it (the file may not be "
        "password protected). Nothing was moved to the trash: what Paperless makes could not be "
        "followed."
    )


@pytest.mark.parametrize("twice", [True, False])
async def test_products_are_distinct_from_each_other_and_from_the_originals(twice: bool) -> None:  # noqa: FBT001 -- parametrised
    fake = paperless()
    fake.same = 52 if twice else DOC

    result = await split(fake)

    assert trash(fake) == []
    assert result.message is not None
    assert f"document {fake.same} is not a new document of its own" in result.message


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
    assert result.next == f"only if document {DOC} is unchanged: {HAND_OVER}"


async def test_a_serial_number_that_cannot_be_put_back_either_is_named_with_both() -> None:
    fake = paperless(NUMBER)
    fake.refuse = lambda _, asn: asn is not None

    result = await shorten(fake)

    assert trash(fake) == []
    assert result.message is not None
    both = (
        f" Archive serial number {NUMBER} is neither on document {DOC} nor on document 52: set it "
        f"with update_document(document_id={DOC}, archive_serial_number={NUMBER})."
    )
    assert result.message.endswith(both)


async def test_a_number_that_never_left_is_not_put_back() -> None:
    fake = paperless(NUMBER)
    fake.refuse = lambda number, _: number == DOC

    result = await shorten(fake)

    assert document(fake, DOC).archive_serial_number == NUMBER
    assert result.message is not None
    assert "Archive serial number" not in result.message
    assert fake.patches == []


async def test_a_number_whose_move_was_lost_on_the_way_back_is_where_it_is() -> None:
    """The product got it although the answer was lost: it is taken back from there."""
    fake = paperless(NUMBER)
    lost = [True]

    def refuse(number: int, asn: int | None) -> bool:
        if number == 52 and asn is not None and lost:
            lost.clear()
            document(fake, 52).archive_serial_number = asn
            return True
        return False

    fake.refuse = refuse

    _ = await shorten(fake)

    assert document(fake, DOC).archive_serial_number == NUMBER
    assert document(fake, 52).archive_serial_number is None


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
    assert result.next == (
        f"only if document {OTHER} is unchanged: delete_document(document_id={OTHER})"
    )
    assert result.message is not None
    assert result.message.startswith(f"{GONE} Not moved to the trash: document {OTHER} (")


async def test_a_kept_merge_names_every_original_it_would_have_trashed() -> None:
    fake = paperless(NUMBER)
    document(fake, OTHER).archive_serial_number = 8
    fake.states = {0: "started"}

    result = await merge(fake)

    assert result.next == after(
        fake,
        6,
        unchanged=f"only if documents {DOC}, {OTHER} are unchanged",
        then=(
            "only if the new document has no archive serial number "
            f'(update_document(document_id={DOC}, clear=["archive_serial_number"]), then '
            f"update_document(document_id=<the new document>, archive_serial_number={NUMBER})), "
            f"then delete_document(document_id={DOC}), then delete_document(document_id={OTHER}); "
            f"archive serial number 8 of document {OTHER} does not move"
        ),
    )


async def test_the_number_of_a_merged_original_that_stays_goes_back_to_it() -> None:
    fake = paperless()
    document(fake, OTHER).archive_serial_number = NUMBER
    fake.refuse_deletes = {OTHER}

    result = await merge(fake)

    assert result.trashed_ids == (DOC,)
    assert document(fake, OTHER).archive_serial_number == NUMBER
    assert document(fake, 52).archive_serial_number is None


async def test_the_number_of_a_merged_original_that_went_stays_with_the_merged_one() -> None:
    fake = paperless(NUMBER)
    fake.refuse_deletes = {OTHER}

    result = await merge(fake)

    assert result.trashed_ids == (DOC,)
    assert document(fake, 52).archive_serial_number == NUMBER


@pytest.mark.parametrize("what", ["late", "file", "number"])
async def test_every_original_is_looked_at_right_before_its_own_deletion(what: str) -> None:
    fake = paperless()

    def change(number: int) -> None:
        if number != DOC:
            return
        other = document(fake, OTHER)
        if what == "late":
            fake.now += 104
        elif what == "file":
            newer = other.versions[0].model_copy(update={"id": 900, "is_root": False})
            other.versions.insert(0, newer)
        else:
            other.archive_serial_number = 9

    fake.before_delete = change

    result = await merge(fake)

    assert (result.trashed_ids, trash(fake)) == ((DOC,), [DOC])
    assert result.message is not None
    cause = (
        "the call took too long, though everything was made and checked"
        if what == "late"
        else f"document {OTHER} was changed meanwhile; look at it again"
    )
    assert result.message.endswith(f"Not moved to the trash: document {OTHER} ({cause}).")


async def test_a_refusal_comes_before_anything_is_started() -> None:
    fake = paperless()

    with pytest.raises(ConfirmationRequired):
        _ = await split(fake, Options())

    assert fake.started == []


DATED = (
    "its custom fields hold a date, for which Paperless 3.5 lists no task: the work is done all "
    "the same"
)
NO_TASK = "Paperless accepted the request, but no task showed up for it"


def dated(fake: Paperless, number: int) -> None:
    [field] = [f.id for f in fake.state.custom_fields if f.data_type == "date"]
    document(fake, number).custom_fields = {field: "2026-01-15"}


async def test_a_document_with_a_date_field_keeps_the_original_and_says_why() -> None:
    fake = paperless()
    dated(fake, DOC)
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)

    replaced = await shorten(fake)
    rotated = await service.rotate([DOC, OTHER], 90)
    merged = await service.merge([DOC, OTHER], DOC)
    version = await service.delete_pages(DOC, "1", options=YES)

    assert (replaced.state, replaced.trashed_ids, trash(fake)) == ("unknown", (), [])
    assert replaced.message is not None
    assert replaced.message.startswith(f"{NO_TASK} ({DATED}). Nothing was moved to the trash: ")
    assert replaced.next == FIND
    assert (len(rotated.tasks), rotated.state) == (1, "unknown")
    assert rotated.message is not None
    assert rotated.message.endswith(f"the state covers that one only ({DATED}).")
    assert merged.message == f"{NO_TASK} ({DATED})."
    assert version.state == "success"


async def test_unlocking_a_document_with_a_date_field_names_both_causes() -> None:
    fake = paperless()
    document(fake, DOC).password, document(fake, DOC).page_count = "s3cret", None
    dated(fake, DOC)
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)

    unlocked = await service.remove_password(DOC, "s3cret", output="replace_original", options=YES)
    bare = await service.remove_password(
        DOC, "s3cret", options=Options(keep_metadata=False, wait_seconds=5)
    )

    assert unlocked.message is not None
    assert unlocked.message.startswith(
        f"{NO_TASK} (the file may not be password protected, or {DATED})."
    )
    assert unlocked.next == FIND
    assert bare.state == "success"


async def test_other_custom_fields_and_empty_dates_are_no_reason() -> None:
    fake = paperless()
    [date, text] = [
        next(f.id for f in fake.state.custom_fields if f.data_type == kind)
        for kind in ("date", "string")
    ]
    document(fake, DOC).custom_fields = {date: None, text: "2026-01-15"}
    fake.lose = 1

    result = await split(fake, Options(confirm=True, keep_metadata=True))

    assert "custom fields" not in (result.message or "")


async def test_an_unreadable_field_list_is_no_reason() -> None:
    fake = paperless()
    dated(fake, DOC)
    fake.no_fields = True

    result = await shorten(fake)

    assert result.message is not None
    assert result.message.startswith(f"{NO_TASK}. Nothing")
