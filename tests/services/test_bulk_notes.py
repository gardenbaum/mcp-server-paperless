"""BulkNotes: one note for a selection or a note per document, and never the same text twice.

The fakes of the "nothing may be written" cases refuse writes: a write fails the test.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeSavedView, FakeState, seeded
from harness import bulk_notes
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.bulk_items import DocumentNote
from mcp_server_paperless.domain.documents import Note
from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    AnswerTooLarge,
    AuthenticationFailed,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    Validation,
    accepting,
)
from mcp_server_paperless.domain.selection import Selection
from mcp_server_paperless.domain.views import FilterRule

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mcp_server_paperless.domain.bulk_items import NotesReport
    from mcp_server_paperless.domain.documents import NoteFormat
    from mcp_server_paperless.domain.selection import DocumentRow, ReadField

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-73")]

TEXT = "Paid on Friday"
DAY = date(2026, 1, 1)
SCANS = {"title_contains": "Scan"}
LIMITED = UpstreamUnavailable("Paperless is temporarily unavailable (HTTP 429).", status=429)
TIMEOUT = UpstreamTimeout("Paperless did not respond in time.")


def note(number: int, text: str = TEXT, note_format: str = "plain") -> Note:
    return Note(
        id=number,
        note=text,
        format=note_format,
        created=datetime(2026, 1, 1, tzinfo=UTC),
        user="e2e-reader",
    )


class World(FakePaperless):
    """A fake whose requests can fail, and that counts them."""

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.now: float = 0.0
        self.takes: float = 0.0
        self.lists: int = 0
        self.limits: list[int] = []
        self.list_fails: PaperlessMcpError | None = None
        self.checking: bool = False
        self.read: list[int] = []
        self.sent: list[tuple[int, str]] = []
        self.read_fails: dict[int, list[PaperlessMcpError | None]] = {}
        self.write_fails: dict[int, PaperlessMcpError] = {}
        # Whether a note whose answer is lost was added all the same.
        self.applied: set[int] = set()
        self.before: dict[int, Callable[[], object]] = {}

    @override
    async def read_rows(
        self, document_ids: Sequence[int], fields: Sequence[ReadField], *, max_bytes: int
    ) -> tuple[DocumentRow, ...]:
        self.lists += 1
        self.limits.append(max_bytes)
        if self.list_fails is not None:
            raise self.list_fails
        rows = await super().read_rows(document_ids, fields, max_bytes=max_bytes)
        self.checking = True
        return rows

    @override
    async def list_document_notes(self, document_id: int) -> tuple[Note, ...]:
        self.now += self.takes
        if self.checking:  # whether the user may view notes
            self.checking = False
            return await super().list_document_notes(document_id)
        self.read.append(document_id)
        _ = self.before.pop(document_id, lambda: None)()
        fails = self.read_fails.get(document_id)
        error = fails.pop(0) if fails else None
        if error is not None:
            raise error
        return await super().list_document_notes(document_id)

    @override
    async def add_note(
        self, document_id: int, text: str, note_format: NoteFormat = "plain"
    ) -> Note:
        self.sent.append((document_id, text))
        error = self.write_fails.get(document_id)
        if error is not None and document_id not in self.applied:
            raise error
        found = await super().add_note(document_id, text, note_format)
        if error is not None:
            raise error
        return found


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, writes: bool = True, scans: int = 3) -> None:
        self.state: FakeState = seeded()
        self.state.documents += [
            # A search shows the newest first.
            FakeDocument(id=100 + n, title=f"Scan {n}", owner=2, created=DAY - timedelta(days=n))
            for n in range(1, scans + 1)
        ]
        self.fake: World = World(self.state, user=user, writes=writes)
        self.notes = bulk_notes(self.fake, clock=lambda: self.fake.now)

    def document(self, number: int) -> FakeDocument:
        return next(d for d in self.state.documents if d.id == number)

    def texts(self, number: int) -> list[str]:
        return [n.note for n in self.document(number).notes]

    async def one(
        self,
        text: str | None = TEXT,
        *,
        note_format: NoteFormat = "plain",
        dry_run: bool = False,
        confirm: bool = True,
        **selection: object,
    ) -> NotesReport:
        """One note for a selection."""
        chosen = Selection.model_validate(selection or {"document_ids": (101, 102)})
        return await self.notes.add(
            chosen, text, (), note_format=note_format, dry_run=dry_run, confirm=confirm
        )

    async def each(
        self,
        *notes: tuple[int, str],
        note_format: NoteFormat = "plain",
        dry_run: bool = False,
        confirm: bool = True,
    ) -> NotesReport:
        """A note per document."""
        given = [DocumentNote(document_id=number, note=text) for number, text in notes]
        return await self.notes.add(
            Selection(), None, given, note_format=note_format, dry_run=dry_run, confirm=confirm
        )

    async def asked(self, *, confirm: bool = False, **selection: object) -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await self.one(
                TEXT, note_format="plain", dry_run=False, confirm=confirm, **selection
            )
        return raised.value


def statuses(report: NotesReport) -> dict[int, str]:
    return {d.document_id: d.status for d in report.documents}


# --- adding


async def test_one_note_is_added_to_many_documents() -> None:
    setup = Setup()

    report = await setup.one(document_ids=(102, 101))

    assert setup.fake.sent == [(102, TEXT), (101, TEXT)]
    assert setup.fake.read == [102, 101]  # each was read again right before
    assert (setup.texts(101), setup.texts(102), setup.texts(103)) == ([TEXT], [TEXT], [])
    first = report.documents[0]
    assert (first.document_id, first.title, first.status) == (102, "Scan 2", "added")
    assert first.note_id == setup.document(102).notes[0].id
    assert first.web_url == "https://paperless.example/documents/102/details"
    assert report.counts == {"added": 2}
    assert (report.matched, report.selected_ids, report.left, report.remaining) == (
        2,
        (102, 101),
        (),
        0,
    )
    assert report.message == "Added 2 notes."


async def test_each_document_gets_its_own_note_also_two_different_ones() -> None:
    setup = Setup()

    report = await setup.each((101, "a"), (102, "b"), (101, "c"))

    assert (setup.texts(101), setup.texts(102)) == (["a", "c"], ["b"])
    assert [d.document_id for d in report.documents] == [101, 102, 101]
    assert (report.matched, report.selected_ids) == (2, (101, 102))


async def test_a_note_whose_text_is_already_there_is_skipped() -> None:
    setup = Setup()
    setup.document(101).notes = [note(50, "another"), note(51, f" {TEXT}\n")]

    report = await setup.one()

    assert setup.fake.sent == [(102, TEXT)]
    assert statuses(report) == {101: "already_there", 102: "added"}
    assert report.documents[0].note_id == 51
    assert report.left == ()
    assert report.message == "Added 1 note; 1 was already there."


async def test_repeating_a_call_adds_nothing_twice_and_needs_no_confirm() -> None:
    setup = Setup()
    # Paperless stores a note without the whitespace around it.
    _ = await setup.one(f"  {TEXT}\n")
    setup.fake.allow_writes = False

    report = await setup.one(f"  {TEXT}\n", confirm=False)

    assert setup.texts(101) == [TEXT]
    assert report.counts == {"already_there": 2}
    assert report.message == "Nothing to add: every note is already there."


async def test_a_note_that_appears_right_before_it_would_be_added_is_not_added_twice() -> None:
    setup = Setup()
    setup.fake.before[101] = lambda: setup.document(101).notes.append(note(60))

    report = await setup.one()

    assert setup.fake.sent == [(102, TEXT)]
    assert statuses(report) == {101: "already_there", 102: "added"}


async def test_one_hundred_notes_take_one_list_request_and_101_are_refused() -> None:
    setup = Setup(scans=101)

    report = await setup.each(*((100 + n, TEXT) for n in range(1, 101)))
    with pytest.raises(Validation, match=r"between 1 and 100 notes; got 101"):
        _ = await Setup(scans=101, writes=False).each(*((100 + n, TEXT) for n in range(1, 102)))
    with pytest.raises(Validation, match="between 1 and 100 documents"):
        _ = await Setup(scans=101, writes=False).one(
            document_ids=tuple(100 + n for n in range(1, 102))
        )

    assert report.counts == {"added": 100}
    assert setup.fake.lists == 1


# --- refused before anything is sent


@pytest.mark.parametrize("text", ["", " \n"])
async def test_an_empty_note_is_refused(text: str) -> None:
    with pytest.raises(Validation, match="A note cannot be empty"):
        _ = await Setup(writes=False).one(text)
    with pytest.raises(Validation, match="document 102: A note cannot be empty"):
        _ = await Setup(writes=False).each((101, "x"), (102, text))


async def test_a_note_holds_ten_thousand_characters() -> None:
    setup = Setup()

    _ = await setup.one("x" * 10_000)
    with pytest.raises(Validation, match=r"at most 10000 characters; this one has 10001"):
        _ = await setup.one("x" * 10_001)
    with pytest.raises(Validation, match="document 101: A note holds at most"):
        _ = await setup.each((101, "x" * 10_001))

    assert len(setup.fake.sent) == 2


async def test_note_and_notes_exclude_each_other_and_one_is_needed() -> None:
    setup = Setup(writes=False)
    some = [DocumentNote(document_id=101, note="x")]

    for selection, text, notes in (
        (Selection(document_ids=(101,)), None, ()),
        (Selection(document_ids=(101,)), None, some),
        (Selection(), TEXT, some),
        (Selection(), None, ()),
        (Selection(exclude_ids=(101,)), None, some),
    ):
        with pytest.raises(Validation, match="Pass either note with"):
            _ = await setup.notes.add(selection, text, notes, dry_run=False, confirm=True)
    with pytest.raises(Validation, match="No document is chosen"):
        _ = await setup.notes.add(Selection(), TEXT, (), dry_run=False, confirm=True)


async def test_the_same_note_twice_for_one_document_is_refused() -> None:
    with pytest.raises(Validation, match="given twice for document 101, 102"):
        _ = await Setup(writes=False).each(
            (101, "x"), (102, "y"), (101, " x "), (102, "y"), (103, "x")
        )


async def test_documents_that_are_missing_or_hidden_refuse_the_call() -> None:
    setup = Setup(user=READER[0], writes=False)
    setup.fake.user = replace(
        setup.fake.user, permissions=setup.fake.user.permissions | {"view_note"}
    )

    with pytest.raises(NotFound, match="No documents 102, 999 exist"):
        _ = await setup.each((1, "x"), (102, "x"), (999, "x"))
    with pytest.raises(NotFound, match="No document 999 exists"):
        _ = await setup.one(document_ids=(1, 999))


async def test_a_user_who_may_not_view_notes_is_refused() -> None:
    setup = Setup(user=READER[0], writes=False)

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.each((1, "x"))

    assert "may not view notes" in raised.value.message
    assert raised.value.hint == (
        "Whether a note is already there cannot be told without it; add_note adds one note."
    )


async def test_documents_that_vanished_before_the_notes_were_read_refuse_the_call() -> None:
    setup = Setup(writes=False)
    setup.state.documents = [d for d in setup.state.documents if d.id != 101]

    with pytest.raises(NotFound, match="No document 101 exists"):
        _ = await setup.each((101, "x"))


def viewer_only(setup: Setup) -> None:
    setup.fake.user = replace(
        setup.fake.user,
        is_superuser=False,
        permissions=setup.fake.user.permissions | {"view_document", "view_note", "add_note"},
    )
    setup.document(102).owner = 3
    setup.document(102).viewers = {2}


async def test_a_document_the_user_may_not_change_refuses_the_call_and_shows_in_the_preview() -> (
    None
):
    setup = Setup(writes=False)
    viewer_only(setup)

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.one()
    preview = await setup.one(dry_run=True)

    assert raised.value.message == (
        "You may not change 1 document (102 'Scan 2'), so notes cannot be added there; nothing "
        "was sent."
    )
    assert statuses(preview) == {101: "would_add", 102: "blocked"}
    assert "It would be refused as a whole: you may not change 1 document" in preview.message


# --- what was shown


async def test_the_confirmation_names_the_note_and_every_document() -> None:
    setup = Setup(writes=False)

    refusal = await setup.asked()

    assert refusal.message == (
        "Nothing was changed. Confirming would add the note 'Paid on Friday' (14 characters) to 2 "
        "documents (101 'Scan 1', 102 'Scan 2')."
    )
    assert refusal.hint == CONFIRM_HINT


async def test_different_notes_are_shown_for_the_first_twenty_documents() -> None:
    setup = Setup(writes=False, scans=22)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.each(
            *((100 + n, f"text {n} " + "x" * 90) for n in range(1, 23)), confirm=False
        )

    shown = "x" * 72
    assert "add 22 notes: to 101 'Scan 1' 'text 1 " + shown in raised.value.message
    assert "to 120 'Scan 20' 'text 20 " in raised.value.message
    assert raised.value.message.endswith(
        "to 121 'Scan 21' (98 characters), to 122 'Scan 22' (98 characters)."
    )


async def test_other_text_behind_the_same_confirmation_asks_again() -> None:
    setup = Setup(writes=False)
    start = "x" * 100

    with pytest.raises(ConfirmationRequired) as one:
        _ = await setup.one(start + "a", confirm=False)
    with pytest.raises(ConfirmationRequired) as two:
        _ = await setup.one(start + "b", confirm=False)

    assert one.value.message == two.value.message
    assert one.value.fingerprint != two.value.fingerprint


async def test_an_accepted_dialog_adds_what_it_named() -> None:
    setup = Setup()
    refusal = await setup.asked()

    with accepting(refusal.fingerprint):
        report = await setup.one(confirm=False)

    assert report.counts == {"added": 2}


async def test_a_note_that_appeared_while_the_dialog_was_open_asks_again() -> None:
    setup = Setup(writes=False)
    refusal = await setup.asked()
    setup.document(101).notes = [note(50)]

    with accepting(refusal.fingerprint):
        again = await setup.asked()

    assert again.fingerprint != refusal.fingerprint


async def test_a_preview_names_everything_and_adds_nothing() -> None:
    setup = Setup(writes=False)
    setup.document(101).notes = [note(50)]

    report = await setup.one(dry_run=True, confirm=False)

    assert report.dry_run
    assert statuses(report) == {101: "already_there", 102: "would_add"}
    assert report.selected_ids == (102,)
    assert report.left == (102,)
    assert report.message == (
        "Dry run, nothing was changed. Confirming would add the note 'Paid on Friday' (14 "
        "characters) to 1 document (102 'Scan 2'); 1 was already there. To go ahead, call again "
        "without dry_run; pass confirm=true only if the user already agreed."
    )


# --- by filter or view


@pytest.mark.parametrize("confirm", [True, False])
async def test_a_filter_never_acts_by_itself_and_names_the_next_call(*, confirm: bool) -> None:
    setup = Setup(writes=False)

    refusal = await setup.asked(confirm=confirm, filters=SCANS)

    assert "to 3 documents (101 'Scan 1', 102 'Scan 2', 103 'Scan 3')" in refusal.message
    assert refusal.hint == (
        "Show the user these documents and the effect. Once they agree, call again with the same "
        "filters (or saved_view), exclude_ids and note, and with document_ids=[101, 102, 103] and "
        "confirm=true: exactly these are changed, and none if one of them no longer matches. With "
        "filters or saved_view alone, confirm=true is not enough."
    )


async def test_the_next_call_adds_the_notes_and_names_what_matches_besides() -> None:
    setup = Setup()
    setup.document(102).notes = [note(50)]

    report = await setup.one(filters=SCANS, document_ids=(101,))

    assert setup.fake.sent == [(101, TEXT)]
    assert (report.matched, report.also_matching, report.remaining) == (3, (103,), 1)
    assert report.message == (
        "Added 1 note. 1 other document matches and was not touched (also_matching)."
    )


async def test_the_next_call_is_refused_if_a_document_no_longer_matches() -> None:
    setup = Setup(writes=False)
    setup.document(102).title = "Another"

    with pytest.raises(Validation, match="no longer matches: 102"):
        _ = await setup.one(filters=SCANS, document_ids=(101, 102))


async def test_an_accepted_dialog_resolves_the_filter_again() -> None:
    setup = Setup(writes=False)
    refusal = await setup.asked(filters=SCANS)
    setup.document(103).title = "Another"

    with accepting(refusal.fingerprint):
        again = await setup.asked(filters=SCANS)

    assert "to 2 documents" in again.message


async def test_a_filter_converges_one_hundred_at_a_time() -> None:
    setup = Setup(scans=150)
    setup.document(101).notes = [note(50)]
    refusal = await setup.asked(filters=SCANS)

    with accepting(refusal.fingerprint):
        first = await setup.one(confirm=False, filters=SCANS)
    second = await setup.asked(filters=SCANS)
    with accepting(second.fingerprint):
        rest = await setup.one(confirm=False, filters=SCANS)
    done = await setup.one(confirm=False, filters=SCANS)

    assert (first.counts, first.remaining, first.matched) == (
        {"added": 100, "already_there": 1},
        49,
        150,
    )
    assert len(first.documents) == 100
    assert "49 more documents match: call again with the same filters or saved_view to go on." in (
        first.message
    )
    assert (rest.counts["added"], rest.remaining) == (49, 0)
    assert done.counts == {"already_there": 150}
    assert done.message == "Nothing to add: every note is already there."


async def test_one_more_match_is_told_in_the_singular_and_a_preview_says_how_to_go_on() -> None:
    setup = Setup(scans=101, writes=False)

    report = await setup.one(filters=SCANS, dry_run=True)
    none = await setup.one(filters={"title_contains": "nothing like it"}, dry_run=True)

    assert report.remaining == 1
    assert "with document_ids set to selected_ids" in report.message
    assert report.message.endswith(
        " 1 more document matches: call again with the same filters or saved_view to go on."
    )
    assert none.message == "Dry run, nothing was changed. Nothing to add: no document matches."


async def test_an_exclusion_that_is_not_there_is_refused_also_with_ids() -> None:
    setup = Setup(writes=False)
    rules = (FilterRule(rule_type=0, value="Scan"), FilterRule(rule_type=17, value="77"))
    setup.state.saved_views = [FakeSavedView(id=9, name="scans", owner=2, filter_rules=rules)]

    with pytest.raises(Validation, match="the selection excludes tag 77"):
        _ = await setup.one(saved_view=9)
    with pytest.raises(Validation, match="the selection excludes tag 77"):
        _ = await setup.one(saved_view=9, document_ids=(101,), dry_run=True)


async def test_an_exclusion_that_could_not_be_checked_is_told() -> None:
    setup = Setup(user=READER[0], writes=False)
    for number in (101, 102, 103):
        setup.document(number).owner = setup.fake.user.id
    setup.fake.user = replace(
        setup.fake.user, permissions=frozenset({"view_document", "view_note", "view_savedview"})
    )
    rules = (FilterRule(rule_type=0, value="Scan"), FilterRule(rule_type=35, value="2"))
    setup.state.saved_views = [FakeSavedView(id=9, name="scans", filter_rules=rules)]

    refusal = await setup.asked(saved_view=9)

    assert "; whether user 2 still exists, which the selection excludes" in refusal.message


# --- what can go wrong


async def test_a_refusal_of_one_document_leaves_the_others() -> None:
    setup = Setup()
    setup.fake.write_fails[101] = PermissionDenied("The Paperless user may not.")

    report = await setup.one(document_ids=(101, 102, 103))

    assert statuses(report) == {101: "failed", 102: "added", 103: "added"}
    assert report.documents[0].why == "The Paperless user may not."
    assert report.left == (101,)
    assert report.message == (
        "1 of 3 notes is not known to be added: 1 failed. Added 2 notes. 101 (failed): The "
        "Paperless user may not. Pass these documents again once the reason is gone; a note that "
        "is there is not added twice."
    )


LOST: list[PaperlessMcpError] = [
    TIMEOUT,
    UpstreamUnavailable("Paperless is temporarily unavailable (HTTP 502).", status=502),
    PaperlessMcpError("Paperless sent an unexpected response."),
    # Paperless also answers "could not save" when only what comes after saving failed.
    Validation("Paperless could not save the note."),
]


@pytest.mark.parametrize("error", LOST)
async def test_a_lost_answer_is_looked_up_once_and_never_repeated(error: PaperlessMcpError) -> None:
    setup = Setup()
    setup.fake.write_fails = {101: error, 103: error}
    setup.fake.applied = {101}

    report = await setup.one(document_ids=(101, 102, 103))

    last = "failed" if isinstance(error, Validation) else "unknown"
    assert statuses(report) == {101: "added", 102: "added", 103: last}
    assert report.documents[0].note_id == setup.document(101).notes[0].id
    assert report.documents[0].why == f"{error.message} Read again: the note is there."
    assert report.documents[2].why == f"{error.message} Read again: the note is not there" + (
        "." if last == "failed" else ", but may still appear."
    )
    assert setup.fake.read == [101, 101, 102, 103, 103]
    assert [number for number, _ in setup.fake.sent] == [101, 102, 103]


@pytest.mark.parametrize("error", [TIMEOUT, Validation("Paperless could not save the note.")])
async def test_a_lost_answer_that_cannot_be_looked_up_stays_unknown(
    error: PaperlessMcpError,
) -> None:
    setup = Setup()
    setup.fake.write_fails[101] = error
    setup.fake.read_fails[101] = [None, NotFound("gone")]

    report = await setup.one()

    assert statuses(report) == {101: "unknown", 102: "added"}
    assert report.documents[0].why == f"{error.message} The notes could not be read again: gone"


async def test_two_lost_answers_in_a_row_stop_also_if_both_were_looked_up() -> None:
    setup = Setup(scans=4)
    setup.fake.write_fails = dict.fromkeys(range(101, 105), TIMEOUT)
    setup.fake.applied = {101, 102, 103, 104}

    report = await setup.one(document_ids=(101, 102, 103, 104))

    assert report.counts == {"added": 2, "not_started": 2}


async def test_two_lost_answers_in_a_row_stop_the_call() -> None:
    setup = Setup(scans=4)
    setup.fake.write_fails = {102: TIMEOUT, 103: TIMEOUT}

    report = await setup.one(document_ids=(101, 102, 103, 104))

    assert statuses(report) == {101: "added", 102: "unknown", 103: "unknown", 104: "not_started"}
    assert "Stopped: Paperless did not answer twice in a row." in report.message
    assert report.left == (102, 103, 104)


@pytest.mark.parametrize("error", [LIMITED, AuthenticationFailed("Paperless rejected the token.")])
@pytest.mark.parametrize("reading", [True, False])
async def test_a_rate_limit_or_a_rejected_credential_ends_the_call(
    error: PaperlessMcpError, *, reading: bool
) -> None:
    setup = Setup()
    if reading:
        setup.fake.read_fails[102] = [error]
    else:
        setup.fake.write_fails[102] = error

    report = await setup.one(document_ids=(101, 102, 103))

    assert statuses(report) == {101: "added", 102: "failed", 103: "not_started"}
    assert setup.fake.read == [101, 102]  # nothing more is asked
    assert f"Stopped: {error.message}" in report.message


async def test_a_rate_limit_while_looking_a_lost_answer_up_ends_the_call() -> None:
    setup = Setup()
    setup.fake.write_fails[102] = TIMEOUT
    setup.fake.read_fails[102] = [None, LIMITED]

    report = await setup.one(document_ids=(101, 102, 103))

    assert statuses(report) == {101: "added", 102: "unknown", 103: "not_started"}


async def test_a_read_that_fails_adds_nothing_there() -> None:
    setup = Setup()
    setup.fake.read_fails = {102: [TIMEOUT], 103: [TIMEOUT]}

    report = await setup.one(document_ids=(101, 102, 103))

    assert statuses(report) == {101: "added", 102: "failed", 103: "failed"}
    assert setup.fake.sent == [(101, TEXT)]


async def test_nothing_is_started_after_forty_seconds() -> None:
    setup = Setup(scans=4)
    setup.fake.takes = 15.0  # the check of the permission to view notes included

    report = await setup.one(document_ids=(101, 102, 103, 104))

    assert statuses(report) == {
        101: "added",
        102: "added",
        103: "not_started",
        104: "not_started",
    }
    assert report.message == (
        "2 of 4 notes are not known to be added: 2 not_started. Added 2 notes. Stopped: 40 seconds "
        "have passed. Pass the documents in left again; a note that is there is not added twice."
    )


async def test_after_a_stop_a_filter_says_what_remains() -> None:
    setup = Setup(scans=4)
    setup.fake.takes = 15.0
    refusal = await setup.asked(filters=SCANS)
    setup.fake.now = 0.0

    with accepting(refusal.fingerprint):
        report = await setup.one(confirm=False, filters=SCANS)

    assert report.counts == {"added": 2, "not_started": 2}
    assert report.remaining == 2


async def test_a_call_in_which_nothing_was_added_and_something_failed_is_an_error() -> None:
    setup = Setup()
    setup.document(103).notes = [note(50)]
    setup.fake.write_fails = {101: PermissionDenied("The user may not."), 102: TIMEOUT}

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.one(document_ids=(101, 102, 103))

    assert raised.value.message == (
        "No note is known to be added; 2 of 3 notes are not known to be added: 1 failed, 1 "
        "unknown. 101 (failed): The user may not. 102 (unknown): Paperless did not respond in "
        "time. Read again: the note is not there, but may still appear."
    )
    assert raised.value.hint == "Calling again is safe: a note that is there is not added twice."


# --- properties

outcomes = st.sampled_from(["fine", "refused", "lost", "late", "limited", "vanished", "there"])


@given(
    plan=st.lists(outcomes, min_size=1, max_size=5), dry_run=st.booleans(), by_filter=st.booleans()
)
async def test_no_document_ends_with_the_note_twice_and_every_one_is_accounted_for(
    *, plan: list[str], dry_run: bool, by_filter: bool
) -> None:
    setup = Setup(scans=len(plan))
    numbers = [101 + at for at in range(len(plan))]
    for number, ends in zip(numbers, plan, strict=True):
        if ends in {"refused", "lost", "late", "limited"}:
            setup.fake.write_fails[number] = {
                "refused": PermissionDenied("no"),
                "lost": TIMEOUT,
                "late": TIMEOUT,
                "limited": LIMITED,
            }[ends]
        if ends == "late":
            setup.fake.applied.add(number)
        if ends == "vanished":
            setup.fake.read_fails[number] = [NotFound("gone")]
        if ends == "there":
            setup.document(number).notes = [note(50)]
    selection: dict[str, object] = {"filters": SCANS} if by_filter else {}

    try:
        report = await setup.one(
            TEXT,
            note_format="plain",
            dry_run=dry_run,
            confirm=True,
            document_ids=tuple(numbers),
            **selection,
        )
    except PaperlessMcpError:
        assert not dry_run
        before = [n for n, ends in zip(numbers, plan, strict=True) if ends == "there"]
        assert [n for n in numbers if setup.texts(n)] == before
        return
    assert [d.document_id for d in report.documents] == numbers
    assert sum(report.counts.values()) == len(numbers)
    done = {d.document_id for d in report.documents if d.status in {"added", "already_there"}}
    assert {*report.left} == {*numbers} - done
    assert [n for n in numbers if len(setup.texts(n)) == 1] == sorted(done)
    assert all(len(setup.texts(n)) <= 1 for n in numbers)
    if dry_run:
        assert setup.fake.sent == []


@given(dry_run=st.booleans(), confirm=st.booleans())
async def test_no_call_by_filter_alone_writes(*, dry_run: bool, confirm: bool) -> None:
    setup = Setup(writes=False)

    try:
        report = await setup.one(filters=SCANS, dry_run=dry_run, confirm=confirm)
    except ConfirmationRequired:
        return
    assert report.dry_run


async def test_notes_too_large_to_read_are_refused() -> None:
    setup = Setup(writes=False)
    setup.fake.list_fails = AnswerTooLarge("Too large.")

    with pytest.raises(AnswerTooLarge) as raised:
        _ = await setup.each((101, "x"))

    assert raised.value.message == "The notes of these documents are larger than 64 MiB."


async def test_a_document_with_two_notes_is_named_and_counted_once() -> None:
    setup = Setup(writes=False)
    viewer_only(setup)

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.each((102, "a"), (102, "b"), confirm=True)

    assert raised.value.message.startswith("You may not change 1 document (102 'Scan 2'), so")


async def test_two_notes_of_one_document_that_are_left_count_one_document() -> None:
    setup = Setup()
    setup.fake.write_fails[102] = PermissionDenied("no")

    report = await setup.each((101, "x"), (102, "a"), (102, "b"))

    assert (report.left, report.remaining, report.matched) == ((102,), 1, 2)
    assert report.counts == {"added": 1, "failed": 2}


async def test_the_list_request_is_bounded_by_64_mib() -> None:
    setup = Setup(writes=False)

    _ = await setup.one(dry_run=True)

    assert setup.fake.limits == [64 * 1024 * 1024]


# --- the format


async def test_the_same_text_in_another_format_is_another_note() -> None:
    setup = Setup()
    setup.document(101).notes = [note(50)]
    setup.document(102).notes = [note(51, note_format="markdown")]

    report = await setup.one(note_format="markdown")
    again = await setup.one(note_format="markdown", confirm=False)

    assert statuses(report) == {101: "added", 102: "already_there"}
    assert [(n.note, n.format) for n in setup.document(101).notes] == [
        (TEXT, "plain"),
        (TEXT, "markdown"),
    ]
    assert again.counts == {"already_there": 2}


async def test_the_format_is_one_for_all_notes_of_a_call() -> None:
    setup = Setup()

    _ = await setup.each((101, "a"), (102, "b"), note_format="markdown")

    assert {n.format for d in (101, 102) for n in setup.document(d).notes} == {"markdown"}


async def test_the_format_is_part_of_what_is_confirmed() -> None:
    setup = Setup()

    plain = await setup.asked()
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.one(note_format="markdown", confirm=False)
    with pytest.raises(ConfirmationRequired) as each:
        _ = await setup.each((101, "a"), note_format="markdown", confirm=False)

    assert "markdown" not in plain.message
    assert f"add the markdown note '{TEXT}'" in raised.value.message
    assert "add 1 markdown note: " in each.value.message
    with accepting(plain.fingerprint), pytest.raises(ConfirmationRequired):
        _ = await setup.one(note_format="markdown", confirm=False)


async def test_a_lost_answer_is_looked_up_by_text_and_format() -> None:
    setup = Setup()
    setup.document(101).notes = [note(50)]
    setup.fake.write_fails[101] = TIMEOUT

    with pytest.raises(PaperlessMcpError, match="not there, but may still appear"):
        _ = await setup.one(note_format="markdown", document_ids=(101,))
