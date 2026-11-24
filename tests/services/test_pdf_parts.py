"""Metadata per part, and extracting with removal: every way they can end (decision-67)."""

import asyncio
from datetime import date
from typing import TYPE_CHECKING

import pytest
from fakes.paperless import FakeTag
from harness import pdf_service

from mcp_server_paperless.domain.errors import (
    Ambiguous,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    Validation,
)
from mcp_server_paperless.domain.pdf import PartMetadata
from mcp_server_paperless.services.pdf import Options
from services.test_pdf_replacing import (
    DOC,
    FIND,
    OTHER,
    Paperless,
    dated,
    document,
    paperless,
    trash,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    from mcp_server_paperless.domain.pdf import Output, PdfResult
    from mcp_server_paperless.domain.tasks import TaskState

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-67")]

YES = Options(confirm=True)
ONE, TWO = OTHER + 1, OTHER + 2  # what the parts become
CONTRACT = PartMetadata(title="Contract", tags=("e2e-finance",))
ANNEX = PartMetadata(
    title=" Annex ", document_type="e2e-invoice", correspondent=1, created=date(2026, 1, 2), tags=()
)
SET_ONE = f'update_document(document_id={ONE}, title="Contract", tags_set=[1])'
SET_TWO = (
    f'update_document(document_id={TWO}, title="Annex", created="2026-01-02", correspondent=1, '
    "document_type=1, tags_set=[])"
)
UNCHANGED = f"only if document {DOC} is unchanged"


async def split(
    fake: Paperless,
    metadata: Sequence[PartMetadata] | None = (CONTRACT, ANNEX),
    *,
    output: Output = "new_document",
    options: Options = YES,
) -> PdfResult:
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)
    return await service.split(
        DOC, ["1", "2-4"], output=output, part_metadata=metadata, options=options
    )


def tagged() -> Paperless:
    fake = paperless()
    document(fake, DOC).tags = [3]
    return fake


async def test_every_part_gets_its_own_metadata_and_keeps_the_rest() -> None:
    fake = tagged()

    result = await split(fake)

    one, two = document(fake, ONE), document(fake, TWO)
    assert (one.title, one.tags, one.document_type) == ("Contract", [1], None)
    assert (two.title, two.document_type, two.correspondent) == ("Annex", 1, 1)
    assert (two.created, two.tags) == (date(2026, 1, 2), [])
    assert one.created == document(fake, DOC).created
    assert (result.state, result.original, result.next, result.message) == (
        "success",
        "kept",
        None,
        None,
    )
    assert [p.model_dump() for p in result.parts] == [
        {
            "part": n,
            "pages": pages,
            "task_id": fake.started[n - 1],
            "state": "success",
            "document_id": made,
            "web_url": f"https://paperless.example/documents/{made}/details",
            "metadata": "set",
            "message": None,
            "next": None,
        }
        for n, pages, made in ((1, "page 1", ONE), (2, "pages 2-4", TWO))
    ]


async def test_with_metadata_the_call_waits_for_the_parts_without_it_does_not() -> None:
    running, plain = paperless(), paperless()
    running.states = plain.states = {0: "pending"}

    waited = await split(running, options=Options())
    at_once = await split(plain, None, options=Options())

    assert (waited.wait, waited.waited_seconds) == ("timed_out", 30.0)
    assert (at_once.wait, plain.now) == ("not_requested", 0.0)
    assert [p.metadata for p in at_once.parts] == ["not_requested", "not_requested"]


async def test_a_part_without_any_field_is_left_as_paperless_made_it() -> None:
    fake = tagged()

    result = await split(fake, (PartMetadata(), ANNEX))

    assert [p.metadata for p in result.parts] == ["not_requested", "set"]
    assert document(fake, ONE).title == "Scan"


@pytest.mark.parametrize(
    ("metadata", "error", "words"),
    [
        ((CONTRACT,), Validation, "2 parts, but metadata for 1"),
        ((), Validation, "2 parts, but metadata for 0"),
        ((CONTRACT, PartMetadata(tags=("nope",))), NotFound, "No tag is named 'nope'"),
        ((CONTRACT, PartMetadata(document_type="nope")), NotFound, "No document type is named"),
        ((CONTRACT, PartMetadata(correspondent=9)), NotFound, "No correspondent has id 9"),
        ((CONTRACT, PartMetadata(tags=("twice",))), Ambiguous, "2 tags match"),
        ((PartMetadata(title="  "), ANNEX), Validation, "title cannot be blank"),
        ((PartMetadata(title="x" * 129), ANNEX), Validation, "longer than 128"),
    ],
)
async def test_bad_metadata_refuses_the_call_before_anything_starts(
    metadata: tuple[PartMetadata, ...], error: type[PaperlessMcpError], words: str
) -> None:
    fake = paperless()
    fake.state.tags += [FakeTag(id=n, name="twice") for n in (70, 71)]

    with pytest.raises(error, match=words):
        _ = await split(fake, metadata)

    assert fake.started == []


async def test_a_part_whose_metadata_fails_does_not_stop_the_others() -> None:
    fake = paperless()
    fake.refuse = lambda number, _: number == ONE

    result = await split(fake)

    one, two = result.parts
    assert (one.metadata, one.document_id, one.next) == ("not_set", ONE, SET_ONE)
    assert one.message == "its metadata is not known to be set (Paperless did not respond in time)"
    assert (two.metadata, two.next) == ("set", None)
    assert document(fake, TWO).title == "Annex"
    assert (result.state, result.original, result.message) == (
        "success",
        "kept",
        "The metadata of part 1 is not set.",
    )


async def test_an_answer_with_other_values_is_not_taken_for_set() -> None:
    fake = paperless()
    fake.renames = {ONE}  # a workflow changes the part at once

    result = await split(fake)

    assert [p.metadata for p in result.parts] == ["not_set", "set"]
    assert result.parts[0].message == "Paperless answered with other values than asked for"
    assert result.parts[0].next == SET_ONE


def wait(fake: Paperless, place: int) -> str:
    return f'get_task(task_id="{fake.started[place]}", wait_seconds=30)'


NEW_TWO = SET_TWO.replace(str(TWO), "<the new document>", 1)
SEARCH_TWO = (
    f"find the new document of pages 2-4 of document {DOC} (search_documents, newest added first) "
    f"and check it, then {NEW_TWO}"
)


@pytest.mark.parametrize("state", ["pending", "started"])
async def test_an_unfinished_part_is_not_touched_and_the_next_calls_are_named(
    state: TaskState,
) -> None:
    fake = paperless()
    fake.states = {1: state}

    result = await split(fake)

    one, two = result.parts
    assert (one.metadata, two.state, two.metadata, two.document_id) == (
        "set",
        state,
        "not_set",
        None,
    )
    assert two.next == (
        f"{wait(fake, 1)}; only if it succeeded and made exactly one document with 3 pages "
        f"(get_document): {NEW_TWO}"
    )
    assert fake.patches == [(ONE, None)]


async def test_an_unfinished_part_without_metadata_names_its_task() -> None:
    fake = paperless()
    fake.states = {1: "started"}

    result = await split(fake, None, options=Options(wait_seconds=1))

    assert (result.parts[0].next, result.parts[1].next) == (None, wait(fake, 1))


async def test_a_failed_part_leaves_nothing_to_set() -> None:
    fake = paperless()
    fake.states = {1: "failure"}

    result = await split(fake)

    two = result.parts[1]
    assert (two.state, two.metadata, two.document_id, two.next) == (
        "failure",
        "not_set",
        None,
        None,
    )


async def test_a_task_without_exactly_one_document_is_no_part() -> None:
    rejected, again = paperless(), paperless()
    rejected.empty, again.double = {1}, True

    none, several = await split(rejected), await split(again)

    assert (
        none.parts[1].message == f"task {rejected.started[1]} made nothing instead of one document"
    )
    assert (none.parts[1].metadata, none.parts[1].next) == ("not_set", None)
    assert [p.metadata for p in several.parts] == ["not_set", "not_set"]
    assert again.patches == []


async def test_a_part_that_is_not_as_expected_is_not_touched() -> None:
    unread, wrong, same = paperless(), paperless(), paperless()
    unread.unreadable = lambda number: number == TWO
    wrong.wrong_pages = {1: 2}
    same.same = DOC

    first, second, third = await split(unread), await split(wrong), await split(same)

    assert (first.parts[1].metadata, first.parts[1].document_id) == ("not_set", TWO)
    assert first.parts[1].next == (
        f"get_document(document_id={TWO}), only if it has 3 pages: {SET_TWO}"
    )
    assert second.parts[1].message == f"document {TWO} has 2 pages instead of 3"
    assert (second.parts[1].metadata, second.parts[1].next) == ("not_set", None)
    assert third.parts[0].message == f"document {DOC} is not a new document of its own"
    assert (unread.patches, wrong.patches, same.patches) == ([(ONE, None)], [(ONE, None)], [])


async def test_without_a_wait_nothing_is_set() -> None:
    fake = paperless()

    result = await split(fake, options=Options(wait_seconds=0))

    assert [p.metadata for p in result.parts] == ["not_set", "not_set"]
    assert result.parts[1].next is not None
    assert result.parts[1].next.startswith(wait(fake, 1))
    assert fake.patches == []


async def test_parts_nobody_can_follow_are_named_by_their_pages() -> None:
    fake = paperless()
    fake.hide_tasks = True

    result = await split(fake)

    assert [(p.state, p.metadata, p.task_id) for p in result.parts] == [
        ("unknown", "not_set", None),
        ("unknown", "not_set", None),
    ]
    assert result.parts[1].next == SEARCH_TWO
    assert fake.patches == []


async def test_a_request_that_could_not_be_followed_names_its_queued_task() -> None:
    fake = paperless()
    fake.problem, fake.lose = "unconfirmed", 1

    result = await split(fake)

    assert [p.task_id for p in result.parts] == [None, None]
    assert result.parts[1].next == f"{wait(fake, 0)}, then {SEARCH_TWO}"
    assert fake.patches == []


@pytest.mark.parametrize("output", ["new_document", "replace_original"])
async def test_a_part_that_may_be_another_requests_is_never_touched(output: Output) -> None:
    fake = paperless()
    fake.doubt = "another operation of this user ran at the same time"

    result = await split(fake, output=output)

    assert result.parts[1].message == f"it cannot be told that it is this part ({fake.doubt})"
    assert (result.parts[1].metadata, result.parts[1].next) == ("not_set", SEARCH_TWO)
    assert (fake.patches, trash(fake), result.original) == ([], [], "kept")


@pytest.mark.parametrize(("seconds", "states"), [(45.0, "set"), (45.1, "not_set")])
async def test_no_metadata_is_set_later_than_45_seconds_after_the_start(
    seconds: float, states: str
) -> None:
    fake = paperless()

    def late() -> None:
        fake.now += seconds

    fake.after_start = late

    result = await split(fake)

    assert {p.metadata for p in result.parts} == {states}
    if states == "not_set":
        assert result.parts[0].message == "the call took too long to set its metadata"
        assert result.parts[0].next == SET_ONE


async def test_the_original_goes_once_every_part_has_its_metadata() -> None:
    fake = paperless()

    result = await split(fake, output="replace_original")

    assert (result.original, result.trashed_ids, trash(fake)) == ("trashed", (DOC,), [DOC])
    assert document(fake, TWO).title == "Annex"


async def test_the_original_stays_if_a_part_lacks_its_metadata() -> None:
    fake = paperless()
    fake.refuse = lambda number, _: number == TWO

    result = await split(fake, output="replace_original")

    assert (result.original, result.trashed_ids, trash(fake)) == ("kept", (), [])
    assert result.message == (
        "Nothing was moved to the trash: the metadata of part 2 is not set. "
        f"What was made stays: documents {ONE}, {TWO}."
    )
    assert result.next == (
        f"{UNCHANGED}: the calls in next of every part, then delete_document(document_id={DOC})"
    )


async def test_an_unfinished_part_with_metadata_keeps_the_original_and_says_what_comes_first() -> (
    None
):
    fake = paperless()
    fake.states = {1: "started"}

    result = await split(fake, output="replace_original")

    assert (result.original, trash(fake)) == ("kept", [])
    assert result.next is not None
    assert result.next.endswith(
        f"{UNCHANGED}: the calls in next of every part, then delete_document(document_id={DOC})"
    )


async def test_a_deletion_without_an_answer_leaves_the_original_unknown() -> None:
    fake = paperless()
    fake.time_out = {DOC}

    result = await split(fake, output="replace_original")

    assert (result.original, result.trashed_ids) == ("unknown", ())


async def test_the_preview_and_the_confirmation_name_the_metadata() -> None:
    fake = paperless()
    fake.state.tags[0].name = "Tag"
    metadata = (PartMetadata(title="Contract", tags=(1,)), ANNEX)
    words = (
        "part 1 (page 1) gets title 'Contract' and tags 'Tag'",
        (
            "part 2 (pages 2-4) gets title 'Annex', document type 'e2e-invoice', correspondent "
            "'e2e-acme', created 2026-01-02 and no tags"
        ),
    )

    preview = await split(fake, metadata, options=Options(dry_run=True))
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await split(fake, metadata, output="replace_original", options=Options())

    assert preview.plan.keeps[-2:] == words
    assert all(line in raised.value.message for line in words)
    assert (preview.parts, preview.original, fake.started) == ((), None, [])


async def test_a_confirmation_binds_what_the_names_of_the_metadata_mean() -> None:
    fake = paperless()

    async def asked() -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await split(fake, (CONTRACT, ANNEX), output="replace_original", options=Options())
        return raised.value

    before = await asked()
    fake.state.tags[0].id = 99
    after = await asked()

    assert before.message == after.message
    assert before.fingerprint != after.fingerprint


# --- extract_pages ---------------------------------------------------------------------------


async def extract(
    fake: Paperless,
    pages: str = "3,2",
    *,
    remove: bool = True,
    metadata: PartMetadata | None = CONTRACT,
    options: Options = YES,
) -> PdfResult:
    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock)
    return await service.extract(DOC, pages, remove=remove, metadata=metadata, options=options)


DELETE = f'delete_pages(document_id={DOC}, pages="2-3", output="new_version")'
DROP = f"delete_document(document_id={ONE}) to drop the new document"
REMOVED = "No page was removed from document 50"


def shown(fake: Paperless) -> tuple[int | None, int]:
    """The pages the document shows, and how many versions it has."""
    original = document(fake, DOC)
    return original.page_count, len(original.versions)


async def test_pages_are_extracted_in_the_order_given_and_the_source_is_unchanged() -> None:
    fake = paperless()

    result = await extract(fake, remove=False, options=Options(wait_seconds=30))

    made = document(fake, ONE)
    assert (made.title, made.page_count, made.tags) == ("Contract", 2, [1])
    assert shown(fake) == (4, 1)
    assert fake.edits == [([3, 2], "new_document")]
    assert (result.operation, result.state, result.original) == ("extract_pages", "success", "kept")
    assert (result.parts[0].pages, result.parts[0].metadata) == (
        "pages 3,2 (in this order)",
        "set",
    )
    assert (result.produced_ids, result.next) == ((ONE,), None)


async def test_extracting_needs_no_confirm_and_waits_only_for_metadata() -> None:
    fake = paperless()
    fake.states = {0: "pending"}

    plain = await extract(fake, remove=False, metadata=None, options=Options())

    assert (plain.wait, fake.now) == ("not_requested", 0.0)


async def test_the_pages_are_removed_once_the_new_document_is_there_and_named() -> None:
    fake = paperless()

    result = await extract(fake)

    assert fake.edits == [([3, 2], "new_document"), ([1, 4], "new_version")]
    assert (shown(fake), document(fake, ONE).page_count) == ((2, 2), 2)
    assert (result.original, result.state, result.wait, result.next) == (
        "shortened",
        "success",
        "finished",
        None,
    )
    version = document(fake, DOC).versions[0].id
    assert result.produced_ids == (ONE, version)
    assert result.produced_links == tuple(
        f"https://paperless.example/documents/{n}/details" for n in (ONE, DOC)
    )
    assert [t.task_id for t in result.tasks] == fake.started
    assert (result.message, len(result.parts), trash(fake)) == (None, 1, [])


async def test_removing_needs_a_confirm_that_says_what_vanishes() -> None:
    fake = paperless()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await extract(fake, options=Options())

    assert raised.value.message == (
        "Nothing was changed. Confirming would extract pages 3,2 (in this order) of document 50 "
        "'Scan' into a new document and remove them from it: the document then shows "
        "a new version without them, and the earlier version with all pages "
        "stays until that version is deleted; the new document gets title "
        "'Contract' and tags 'e2e-finance'."
    )
    assert fake.started == []


async def test_a_preview_of_an_extraction_says_both_steps() -> None:
    fake = paperless()

    plain = await extract(fake, remove=False, metadata=None, options=Options(dry_run=True))
    removing = await extract(fake, options=Options(dry_run=True))
    bare = await extract(fake, options=Options(dry_run=True, keep_metadata=False))

    assert (plain.plan.produces, plain.plan.needs_confirm) == ("1 new document", False)
    assert "document 50 'Scan': unchanged" in plain.plan.keeps
    assert removing.plan.produces == (
        "1 new document, then a new version of the document without these pages"
    )
    assert removing.plan.needs_confirm
    assert "document 50 'Scan': unchanged" not in removing.plan.keeps
    assert "every earlier version, until it is deleted" in removing.plan.keeps
    assert any("the text is read again" in line for line in removing.plan.loses)
    assert any("new documents take nothing of" in line for line in bare.plan.loses)
    assert fake.started == []


@pytest.mark.parametrize(
    ("pages", "words"),
    [("1-4", "Removing every page"), ("5", "Page 5 does not exist"), ("", "No pages given")],
)
async def test_what_cannot_be_removed_is_refused(pages: str, words: str) -> None:
    fake = paperless()

    with pytest.raises(Validation, match=words):
        _ = await extract(fake, pages)

    assert fake.started == []


async def test_every_page_can_be_extracted_but_not_removed() -> None:
    fake = paperless()

    result = await extract(fake, "1-", remove=False, metadata=None)

    assert (result.parts[0].pages, document(fake, ONE).page_count) == ("pages 1-4", 4)


def with_older(fake: Paperless) -> int:
    """Give the document a newer version with three pages; its first one is returned."""
    return fake.add_version(DOC, 3)


async def test_pages_are_removed_from_the_newest_version_only() -> None:
    fake = paperless()
    newest = with_older(fake)

    older = await extract(fake, "4", remove=False, options=Options(version_id=DOC, wait_seconds=5))
    with pytest.raises(Validation, match="newer versions") as raised:
        _ = await extract(fake, "1", options=Options(version_id=DOC, confirm=True))
    newer = await extract(fake, "1", options=Options(version_id=newest, confirm=True))

    assert older.state == "success"
    assert raised.value.hint == "Extract without remove, or leave version_id out."
    assert (newer.original, document(fake, DOC).page_count) == ("shortened", 2)


# The rows of the failure table of the design, with the state afterwards.


@pytest.mark.parametrize("remove", [False, True])
async def test_row_1_a_refused_extraction_leaves_nothing(*, remove: bool) -> None:
    fake = paperless()
    fake.refuse_edit = {"new_document": PermissionDenied("The user may not.")}

    with pytest.raises(PermissionDenied):
        _ = await extract(fake, remove=remove)

    assert (shown(fake), len(fake.state.documents), fake.patches) == ((4, 1), BEFORE, [])


BEFORE = len(paperless().state.documents)


@pytest.mark.parametrize("state", ["pending", "started"])
async def test_row_2_an_unfinished_new_document_leaves_the_source(state: TaskState) -> None:
    fake = paperless()
    fake.states = {0: state}

    result = await extract(fake)

    assert (shown(fake), result.original) == ((4, 1), "kept")
    assert result.message is not None
    assert f"{REMOVED}: Paperless is not known to be done." in result.message
    assert result.next == (
        f"{wait(fake, 0)}; only if every one succeeded and made exactly one document "
        f"(get_document), with 2 pages, and {UNCHANGED}: the calls in next of every part, then "
        f"{DELETE}"
    )


async def test_row_2_without_a_wait_it_stops_after_the_new_document() -> None:
    fake = paperless()

    result = await extract(fake, metadata=None, options=Options(confirm=True, wait_seconds=0))

    assert (shown(fake), result.original) == ((4, 1), "kept")
    assert result.message is not None
    assert "wait_seconds is 0, so nothing was checked" in result.message
    assert result.next is not None
    assert result.next.endswith(f"{UNCHANGED}: {DELETE}")


async def test_row_2_hidden_tasks_leave_the_source() -> None:
    fake = paperless()
    fake.hide_tasks = True

    result = await extract(fake)

    assert (shown(fake), result.original, result.next) == ((4, 1), "kept", FIND)


async def test_row_2_a_doubtful_document_leaves_the_source() -> None:
    fake = paperless()
    fake.doubt = "another operation of this user ran at the same time"

    result = await extract(fake)

    assert (shown(fake), result.next, fake.patches) == ((4, 1), FIND, [])


@pytest.mark.parametrize("state", ["failure", "revoked"])
async def test_row_2_a_failed_new_document_leaves_the_source(state: TaskState) -> None:
    fake = paperless()
    fake.states = {0: state}

    result = await extract(fake)

    assert (shown(fake), result.original, result.next) == ((4, 1), "kept", None)
    assert result.message is not None
    assert f"{REMOVED}: a task failed." in result.message


async def test_row_2_a_stopped_or_wrong_new_document_leaves_the_source() -> None:
    empty, wrong = paperless(), paperless()
    empty.empty, wrong.wrong_pages = {0}, {0: 1}

    none, short = await extract(empty), await extract(wrong)

    for fake, result in ((empty, none), (wrong, short)):
        assert (shown(fake), result.original, result.next) == ((4, 1), "kept", None)
    assert "made nothing instead of one document" in (none.message or "")
    assert f"document {ONE} has 1 pages instead of 2" in (short.message or "")


async def test_row_3_without_its_metadata_the_pages_stay() -> None:
    fake = paperless()
    fake.refuse = lambda number, _: number == ONE

    result = await extract(fake)

    assert (shown(fake), result.original) == ((4, 1), "kept")
    assert result.message == (
        f"{REMOVED}: the metadata of the new document is not set. "
        f"What was made stays: document {ONE}."
    )
    assert result.parts[0].next == SET_ONE
    assert result.next == f"{UNCHANGED}: the calls in next of every part, then {DELETE}"


async def test_row_4_a_source_that_changed_meanwhile_is_left_alone() -> None:
    fake = paperless()

    def change() -> None:
        _ = fake.add_version(DOC, 3)

    fake.after_start = change

    result = await extract(fake)

    assert (shown(fake), result.original) == ((3, 2), "kept")
    assert result.message == (
        f"{REMOVED}: document {DOC} was changed meanwhile; look at it again. "
        f"What was made stays: document {ONE}."
    )
    assert result.next == (
        f"get_document(document_id={DOC}), only if document {DOC} still shows these pages: "
        f"{DELETE}; or {DROP}"
    )


async def test_row_4_a_source_that_cannot_be_read_again_is_left_alone() -> None:
    fake = paperless()

    def unreadable() -> None:
        fake.unreadable = lambda number: number == DOC

    fake.after_start = unreadable

    result = await extract(fake, metadata=None)

    assert (len(fake.edits), result.original) == (1, "kept")
    assert "could not be read again" in (result.message or "")
    assert result.next == (
        f"get_document(document_id={DOC}) once it can be read, {UNCHANGED}: {DELETE}; or {DROP}"
    )


async def test_row_4_nothing_is_removed_later_than_45_seconds_after_the_start() -> None:
    fake = paperless()

    def late() -> None:
        fake.now += 45.1

    fake.after_start = late

    result = await extract(fake, metadata=None)

    assert (shown(fake), result.next) == ((4, 1), f"{UNCHANGED}: {DELETE}")


async def test_row_5_a_refused_removal_leaves_the_source_and_names_the_leftover() -> None:
    fake = paperless()
    fake.refuse_edit = {"new_version": PermissionDenied("The user may not.")}

    result = await extract(fake)

    assert (shown(fake), result.state, result.original) == ((4, 1), "success", "kept")
    assert result.message == (f"{REMOVED}: The user may not. What was made stays: document {ONE}.")
    assert result.next == f"{UNCHANGED}: {DELETE}; or {DROP}"


async def test_row_5_a_failed_removal_leaves_the_source_and_names_the_leftover() -> None:
    fake = paperless()
    fake.states = {1: "failure"}

    result = await extract(fake)

    assert (result.state, result.original) == ("failure", "kept")
    assert result.next == f"{UNCHANGED}: {DELETE}; or {DROP}"
    assert f"What was made stays: document {ONE}." in (result.message or "")


async def test_row_5_a_removal_without_an_answer_is_not_known() -> None:
    fake = paperless()
    fake.refuse_edit = {"new_version": UpstreamTimeout("Paperless did not respond in time.")}

    result = await extract(fake)

    assert (result.state, result.original) == ("unknown", "unknown")
    assert result.message == (
        f"It is not known whether the pages were removed from document {DOC} (Paperless did not "
        f"respond in time): document {ONE} holds them."
    )
    assert (
        result.next
        == f'get_document(document_id={DOC}, include=["versions"]), {UNCHANGED}: {DELETE}'
    )


@pytest.mark.parametrize("state", ["pending", "started"])
async def test_row_5_an_unfinished_removal_names_its_task(state: TaskState) -> None:
    fake = paperless()
    fake.states = {1: state}

    result = await extract(fake)

    assert (result.state, result.original, result.wait) == (state, "unknown", "timed_out")
    assert result.next == wait(fake, 1)
    assert result.produced_ids == (ONE,)


async def test_row_5_a_removal_whose_task_is_not_found_is_not_known() -> None:
    fake = paperless()
    fake.lose_version = True

    result = await extract(fake)

    assert (result.state, result.original) == ("unknown", "unknown")
    assert (
        result.next
        == f'get_document(document_id={DOC}, include=["versions"]), {UNCHANGED}: {DELETE}'
    )


async def test_row_5_a_removal_stopped_without_a_version_is_kept() -> None:
    fake = paperless()
    fake.empty = {1}

    result = await extract(fake)

    assert (result.original, result.next) == (
        "kept",
        f"{UNCHANGED}: {DELETE}; or {DROP}",
    )


async def test_the_removal_waits_for_what_is_left_of_the_wait() -> None:
    fake = paperless()
    fake.states = {0: "started", 1: "started"}

    def first_done() -> None:
        if fake.now >= 20:
            _ = fake.states.pop(0, None)

    fake.ticked = first_done

    result = await extract(fake, options=Options(confirm=True, wait_seconds=300))

    assert (result.waited_seconds, fake.now) == (30.0, 30.0)
    assert (result.original, result.next) == ("unknown", wait(fake, 1))


async def test_an_unexpected_error_while_metadata_is_set_stays_with_its_part() -> None:
    fake = paperless()
    fake.update_error, fake.breaks = RuntimeError("secret"), {ONE}

    result = await split(fake)

    one, two = result.parts
    assert (one.metadata, one.next) == ("not_set", SET_ONE)
    assert one.message == "its metadata is not known to be set (unexpected error)"
    assert two.metadata == "set"
    assert "secret" not in str(result)


async def test_an_unexpected_error_of_the_removal_leaves_it_unknown() -> None:
    fake = paperless()
    fake.refuse_edit = {"new_version": RuntimeError("secret")}

    result = await extract(fake)

    assert (result.state, result.original) == ("unknown", "unknown")
    assert "(unexpected error)" in (result.message or "")
    assert f"document {ONE} holds them" in (result.message or "")
    assert "secret" not in str(result)


STOPPED = "An unexpected error stopped the call after Paperless accepted the request."


async def test_an_unexpected_error_after_the_start_still_returns_what_is_known(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = paperless()

    def broken(number: int) -> bool:
        if number == TWO:
            message = "secret"
            raise RuntimeError(message)
        return False

    fake.unreadable = broken

    result = await split(fake, output="replace_original")

    assert (result.state, result.original, trash(fake)) == ("unknown", "kept", [])
    assert result.message == STOPPED
    assert result.produced_ids == (ONE, TWO)
    assert result.next == f"{wait(fake, 0)}, then {wait(fake, 1)}"
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text + str(result)


async def test_an_unexpected_error_before_anything_is_read_names_where_to_look() -> None:
    fake = paperless()
    dated(fake, DOC)
    fake.copies_error = RuntimeError("secret")

    result = await split(fake, None, options=Options(wait_seconds=1))
    listed = paperless()
    listed.task_error = RuntimeError("secret")
    known = await extract(listed, remove=False, metadata=None, options=Options())

    assert (result.state, result.original, result.next) == ("unknown", "kept", FIND)
    assert (result.message, result.produced_ids) == (STOPPED, ())
    assert (known.state, known.original) == ("unknown", "kept")


async def test_an_unexpected_error_while_trashing_leaves_the_original_unknown() -> None:
    fake = paperless()
    fake.delete_error = RuntimeError("secret")

    result = await split(fake, output="replace_original")

    assert (result.state, result.original, result.trashed_ids) == ("unknown", "unknown", ())
    assert [p.metadata for p in result.parts] == ["set", "set"]


async def test_a_cancelled_call_stays_cancelled() -> None:
    fake = paperless()
    fake.update_error, fake.breaks = asyncio.CancelledError(), {ONE}

    with pytest.raises(asyncio.CancelledError):
        _ = await split(fake)


@pytest.mark.parametrize("wanted", [("e2e-finance",), ()])
async def test_tags_that_were_not_asked_for_are_not_set(wanted: tuple[str, ...]) -> None:
    fake = tagged()
    fake.extra_tags = (3,)

    result = await split(fake, (PartMetadata(tags=wanted), ANNEX), output="replace_original")

    assert [p.metadata for p in result.parts] == ["not_set", "not_set"]
    assert result.parts[0].message == "Paperless answered with other values than asked for"
    assert (result.original, trash(fake)) == ("kept", [])


async def test_the_parent_of_a_tag_belongs_to_it() -> None:
    fake = paperless()

    result = await split(fake, (PartMetadata(tags=("e2e-invoices",)), ANNEX))

    assert [p.metadata for p in result.parts] == ["set", "set"]
    assert set(document(fake, ONE).tags) == {1, 2}


async def test_the_order_of_pages_is_part_of_what_is_confirmed() -> None:
    fake = paperless()

    service = pdf_service(fake)

    async def asked(call: Awaitable[PdfResult]) -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await call
        return raised.value

    down, up = [await asked(extract(fake, p, options=Options())) for p in ("3,2", "2,3")]
    first, second = [
        await asked(service.split(DOC, [p, "3-4"], output="replace_original"))
        for p in ("2,1", "1,2")
    ]

    assert down.fingerprint != up.fingerprint
    assert "pages 3,2 (in this order)" in down.message
    assert first.fingerprint != second.fingerprint
    assert "pages 2,1 (in this order)" in first.message


async def test_a_version_that_is_not_what_was_asked_for_is_not_known_to_be_the_removal() -> None:
    short, foreign, unread = paperless(), paperless(), paperless()
    short.few_pages = True
    foreign.foreign_version = True

    def lost(number: int) -> bool:
        return number == DOC and len(unread.edits) == 2

    unread.unreadable = lost

    few, other, none = await extract(short), await extract(foreign), await extract(unread)

    look = f'get_document(document_id={DOC}, include=["versions"])'
    for result in (few, other, none):
        assert (result.original, result.next) == ("unknown", look)
    [made, *_] = document(short, DOC).versions
    newest, version, *_ = document(foreign, DOC).versions
    assert f"Document {DOC} shows version {made.id} with 1 pages instead of version {made.id} " in (
        few.message or ""
    )
    assert f"shows version {newest.id} with 9 pages instead of version {version.id} with 2" in (
        other.message or ""
    )
    assert f"What document {DOC} shows could not be read" in (none.message or "")
