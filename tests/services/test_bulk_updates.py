"""BulkUpdates: values of their own for many documents, nothing written that was not shown.

The fakes of the "nothing may be written" cases refuse writes: a write fails the test.
"""

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, FakeTag, seeded
from harness import bulk_updates
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.bulk_items import DocumentValues
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    AuthenticationFailed,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
    accepting,
)
from mcp_server_paperless.domain.selection import MAX_ANSWER
from mcp_server_paperless.services.bulk_updates import MAX_VALUE, NOT_YET

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.bulk_items import UpdateReport
    from mcp_server_paperless.domain.documents import DocumentPatch, DocumentRecord, SignedIn
    from mcp_server_paperless.domain.selection import DocumentRow, ReadField
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-73")]

TEXT, LINK = "e2e-string", "e2e-documentlink"
LIMITED = UpstreamUnavailable("Paperless is temporarily unavailable (HTTP 429).", status=429)
TIMEOUT = UpstreamTimeout("Paperless did not respond in time.")


class World(FakePaperless):
    """A fake whose requests can fail, and that counts them."""

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.now: float = 0.0
        # Seconds each read of one document takes.
        self.takes: float = 0.0
        self.lists: int = 0
        self.list_fails: PaperlessMcpError | None = None
        self.nobody: bool = False  # whether the user may not read who they are
        self.limits: list[int] = []
        self.changeable: set[int] = set()
        self.list_takes: float = 0.0
        self.tag_takes: float = 0.0
        self.listed: list[str] = []
        self.got: list[int] = []
        self.sent: list[int] = []
        self.patches: list[DocumentPatch] = []
        # Errors of the reads and writes of a document, in the order of the requests.
        self.read_fails: dict[int, list[PaperlessMcpError | None]] = {}
        self.write_fails: dict[int, PaperlessMcpError] = {}
        # Whether a write that fails was made all the same.
        self.applied: set[int] = set()
        self.tag_fails: dict[str, PaperlessMcpError] = {}
        # What others do right before a document is read again.
        self.before: dict[int, Callable[[], object]] = {}

    def rows_know_grants(self, rows: tuple[DocumentRow, ...]) -> tuple[DocumentRow, ...]:
        """A single read of the fake does not model grants to change; list rows do here."""
        return tuple(
            row.model_copy(update={"user_can_change": True}) if row.id in self.changeable else row
            for row in rows
        )

    @override
    async def read_rows(
        self, document_ids: Sequence[int], fields: Sequence[ReadField], *, max_bytes: int
    ) -> tuple[DocumentRow, ...]:
        self.lists += 1
        self.limits.append(max_bytes)
        self.now += self.list_takes
        if self.list_fails is not None:
            raise self.list_fails
        rows = await super().read_rows(document_ids, fields, max_bytes=max_bytes)
        return self.rows_know_grants(rows)

    @override
    async def get_signed_in(self) -> SignedIn | None:
        return None if self.nobody else await super().get_signed_in()

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        self.now += self.takes
        self.got.append(document_id)
        _ = self.before.pop(document_id, lambda: None)()
        fails = self.read_fails.get(document_id)
        error = fails.pop(0) if fails else None
        if error is not None:
            raise error
        return await super().get_document(document_id)

    @override
    async def update_document(self, document_id: int, patch: DocumentPatch) -> DocumentRecord:
        self.sent.append(document_id)
        self.patches.append(patch)
        error = self.write_fails.get(document_id)
        if error is not None and document_id not in self.applied:
            raise error
        found = await super().update_document(document_id, patch)
        if error is not None:
            raise error
        return found

    @override
    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        self.listed.append(kind)
        return await super().list_taxonomy(kind)

    @override
    async def create_tag(self, name: str) -> TaxonomyItem:
        self.now += self.tag_takes
        if name in self.tag_fails:
            raise self.tag_fails[name]
        return await super().create_tag(name)


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, writes: bool = True, count: int = 5) -> None:
        self.state: FakeState = seeded()
        self.state.documents += [
            FakeDocument(id=100 + n, title=f"Scan {n}", owner=2) for n in range(1, count + 1)
        ]
        self.fake: World = World(self.state, user=user, writes=writes)
        self.updates = bulk_updates(self.fake, clock=lambda: self.fake.now)

    def document(self, number: int) -> FakeDocument:
        return next(d for d in self.state.documents if d.id == number)

    def field(self, name: str) -> int:
        return next(f.id for f in self.state.custom_fields if f.name == name)

    def link(self, number: int, targets: list[JsonValue]) -> None:
        self.document(number).custom_fields[self.field(LINK)] = targets

    def links(self, number: int) -> JsonValue:
        return self.document(number).custom_fields.get(self.field(LINK))

    async def run(
        self,
        *items: dict[str, object],
        create: bool = False,
        dry_run: bool = False,
        confirm: bool = True,
    ) -> UpdateReport:
        values = [DocumentValues.model_validate(item) for item in items]
        return await self.updates.update(
            values, create_missing_tags=create, dry_run=dry_run, confirm=confirm
        )

    async def asked(self, *items: dict[str, object], create: bool = False) -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await self.run(*items, create=create, confirm=False)
        return raised.value

    async def refused(self, *items: dict[str, object], create: bool = False) -> Validation:
        with pytest.raises(Validation) as raised:
            _ = await self.run(*items, create=create)
        assert self.fake.sent == []
        return raised.value

    async def failed(self, *items: dict[str, object], create: bool = False) -> PaperlessMcpError:
        with pytest.raises(PaperlessMcpError) as raised:
            _ = await self.run(*items, create=create)
        return raised.value


def item(number: int, **values: object) -> dict[str, object]:
    return {"document_id": number, **values}


def named(number: int) -> dict[str, object]:
    return item(number, title=f"New {number}")


def statuses(report: UpdateReport) -> dict[int, str]:
    return {d.id: d.status for d in report.documents}


# --- one call, values of their own


async def test_each_document_gets_its_own_values_in_the_order_given() -> None:
    setup = Setup()

    report = await setup.run(
        item(103, title="C", correspondent="e2e-acme", created="2026-01-02"),
        item(101, tags_add=["e2e-invoices"], custom_fields={TEXT: "x"}),
        item(102, archive_serial_number=77, owner="e2e-reader"),
    )

    assert setup.fake.sent == [103, 101, 102]
    assert setup.fake.got == [103, 101, 102]  # each was read again right before
    assert setup.fake.lists == 1
    third = setup.document(103)
    assert (third.title, third.correspondent, third.created) == ("C", 1, date(2026, 1, 2))
    assert setup.document(101).tags == [2, 1]
    assert setup.document(101).custom_fields == {setup.field(TEXT): "x"}
    assert (setup.document(102).archive_serial_number, setup.document(102).owner) == (77, 3)
    assert statuses(report) == {103: "changed", 101: "changed", 102: "changed"}
    assert report.counts == {"changed": 3}
    assert (report.left, report.created_tags, report.also_changed) == ((), (), ())
    assert report.message == "Changed 3 documents."
    first, second, last = report.documents
    assert [(c.field, c.old, c.new) for c in first.changes] == [
        ("title", "Scan 3", "C"),
        ("created", "2026-11-11", "2026-01-02"),
        ("correspondent", None, "e2e-acme"),
    ]
    assert first.title == "Scan 3"
    assert first.web_url == "https://paperless.example/documents/103/details"
    assert [(c.field, c.old, c.new) for c in second.changes] == [
        ("tags", [], ["e2e-invoices", "e2e-finance"]),
        (f"custom_fields.{TEXT}", None, "x"),
    ]
    assert [t.name for t in second.implicit_tags] == ["e2e-finance"]
    assert [(c.field, c.old, c.new) for c in last.changes] == [
        ("archive_serial_number", None, 77),
        ("owner", "admin", "e2e-reader"),
    ]


async def test_what_is_not_named_stays_also_what_others_set_meanwhile() -> None:
    setup = Setup()
    setup.document(101).custom_fields = {setup.field("e2e-integer"): 5}

    def others() -> None:
        setup.document(101).custom_fields[setup.field("e2e-integer")] = 6
        setup.document(101).correspondent = 1

    setup.fake.before[101] = others
    _ = await setup.run(item(101, custom_fields={TEXT: "x"}))

    assert setup.document(101).custom_fields == {
        setup.field("e2e-integer"): 6,
        setup.field(TEXT): "x",
    }
    assert setup.document(101).correspondent == 1


async def test_names_whose_documents_changed_are_read_anew() -> None:
    setup = Setup()
    _ = await setup.run(item(101, tags_add=["e2e-inbox"]))
    before = setup.fake.listed.count("tag")

    _ = await setup.run(item(102, tags_add=["e2e-inbox"]))

    assert before == 1
    assert setup.fake.listed.count("tag") == 2


async def test_clearing_and_removing_work_too() -> None:
    setup = Setup()
    setup.document(101).correspondent = 1
    setup.document(101).tags = [4]
    setup.document(101).custom_fields = {setup.field(TEXT): "x"}

    report = await setup.run(
        item(101, clear=["correspondent"], tags_set=[], remove_custom_fields=[TEXT])
    )

    assert (setup.document(101).correspondent, setup.document(101).tags) == (None, [])
    assert setup.document(101).custom_fields == {}
    assert [(c.field, c.old, c.new) for c in report.documents[0].changes] == [
        ("correspondent", "e2e-acme", None),
        ("tags", ["e2e-inbox"], []),
        (f"custom_fields.{TEXT}", "x", None),
    ]


async def test_documents_that_already_are_as_asked_are_not_touched_and_need_no_confirm() -> None:
    setup = Setup(writes=False)

    report = await setup.run(item(101, title="Scan 1"), item(102, title="Scan 2"), confirm=False)

    assert statuses(report) == {101: "unchanged", 102: "unchanged"}
    assert setup.fake.got == []
    assert report.message == "Nothing to change: all 2 documents already are as asked."
    assert report.left == ()


async def test_repeating_a_call_changes_nothing_more() -> None:
    setup = Setup()
    items = [item(101, tags_add=["e2e-invoices"]), item(102, custom_fields={LINK: [103]})]
    _ = await setup.run(*items)
    setup.fake.allow_writes = False

    report = await setup.run(*items, confirm=False)

    assert report.counts == {"unchanged": 2}


async def test_one_hundred_documents_take_one_list_request_and_101_are_refused() -> None:
    setup = Setup(count=101)

    report = await setup.run(*(named(100 + n) for n in range(1, 101)))
    refusal = await Setup(count=101, writes=False).refused(*(named(100 + n) for n in range(1, 102)))

    assert report.counts == {"changed": 100}
    assert setup.fake.lists == 1
    assert refusal.message == "Name between 1 and 100 documents; got 101."
    assert (await Setup(writes=False).refused()).message.endswith("got 0.")


# --- preview


async def test_a_preview_shows_every_value_and_writes_nothing() -> None:
    setup = Setup(writes=False)

    report = await setup.run(
        item(101, title="Neu", tags_add=["brand new", "e2e-inbox"]),
        item(102, title="Scan 2"),
        item(103, custom_fields={LINK: [104]}),
        create=True,
        dry_run=True,
        confirm=False,
    )

    assert report.dry_run
    assert statuses(report) == {101: "would_change", 102: "unchanged", 103: "would_change"}
    assert [(c.field, c.old, c.new) for c in report.documents[0].changes] == [
        ("title", "Scan 1", "Neu"),
        ("tags", [], ["brand new", "e2e-inbox"]),
    ]
    assert report.created_tags == ("brand new",)
    assert [(r.id, r.name) for r in report.also_changed] == [(104, "Scan 4")]
    assert report.left == (101, 103)
    assert report.message == (
        "Dry run, nothing was changed. Confirming would create the tag 'brand new', private to you "
        "until shared; change 2 documents: 101 'Scan 1' (title 'Scan 1' -> 'Neu'; tags "
        f"+'brand new', +'e2e-inbox'), 103 'Scan 3' ('{LINK}' none -> [104 'Scan 4']); and, "
        "because document links are mirrored, also change 1 document besides "
        "(104 'Scan 4'). Paperless runs its workflows for changed documents on each. To go ahead, "
        "call again without dry_run; pass confirm=true only if the user already agreed."
    )
    assert setup.fake.got == []


async def test_a_preview_of_nothing_to_change_says_so() -> None:
    report = await Setup(writes=False).run(item(101, title="Scan 1"), dry_run=True)

    assert report.message == (
        "Dry run, nothing was changed. Nothing to change: the document already is as asked."
    )


async def test_a_preview_is_refused_like_the_call_itself() -> None:
    with pytest.raises(Validation, match="document 999"):
        _ = await Setup(writes=False).run(named(101), named(999), dry_run=True)


async def test_an_answer_that_is_too_large_is_cut_and_says_so() -> None:
    setup = Setup(writes=False, count=30)
    long = "e2e-longtext"
    for n in range(1, 31):
        setup.document(100 + n).custom_fields = {setup.field(long): "o" * 5000}

    report = await setup.run(
        *(item(100 + n, custom_fields={long: "n"}) for n in range(1, 31)), dry_run=True
    )
    small = await setup.run(item(101, custom_fields={long: "n"}), dry_run=True)

    assert report.truncated
    assert len(report.model_dump_json()) <= MAX_ANSWER
    assert report.documents[0].changes[0].old == "o" * (MAX_VALUE - 1) + "…"
    assert len(report.documents) == 30
    assert not small.truncated
    assert small.documents[0].changes[0].old == "o" * 5000


async def test_changes_are_left_out_from_the_end_when_cutting_is_not_enough() -> None:
    setup = Setup(writes=False, count=60)
    setup.state.tags += [FakeTag(id=1000 + n, name=f"x{n}" * 33) for n in range(10, 30)]
    every = [f"x{n}" * 33 for n in range(10, 30)]

    report = await setup.run(
        *(item(100 + n, tags_add=every, title="t" * 100) for n in range(1, 61)), dry_run=True
    )

    assert report.truncated
    assert len(report.model_dump_json()) <= MAX_ANSWER
    assert [d.id for d in report.documents] == [100 + n for n in range(1, 61)]
    assert report.documents[0].changes
    assert report.documents[-1].changes == ()


# --- phase 1: everything that can be known is checked before the first write


async def test_every_wrong_item_is_named_and_nothing_is_written() -> None:
    setup = Setup(writes=False)

    refusal = await setup.refused(
        item(101, title="fine"),
        item(102, correspondent="nobody"),
        item(103, title="  "),
        item(999, title="x"),
        item(104),
        item(105, custom_fields={"e2e-integer": "many"}),
    )

    assert refusal.message.startswith("Nothing was changed: 5 of 6 documents are refused. ")
    for told in (
        "document 102: No correspondent is named 'nobody'.",
        "document 103: The title cannot be blank.",
        "document 999: It does not exist or is hidden from you.",
        "document 104: Nothing to change: no field was given.",
        "document 105: Custom field 'e2e-integer' takes a whole number",
    ):
        assert told in refusal.message
    assert "document 101" not in refusal.message
    assert refusal.hint == "Correct these items or leave them out; the others were not changed."


async def test_one_wrong_document_is_told_in_the_singular() -> None:
    refusal = await Setup(writes=False).refused(named(101), named(999))

    assert "1 of 2 documents is refused" in refusal.message


async def test_a_document_named_twice_is_refused() -> None:
    refusal = await Setup(writes=False).refused(named(101), named(102), item(101, title="B"))

    assert refusal.message == "Nothing was changed: documents named twice: 101."


async def test_a_document_the_user_may_not_change_is_refused() -> None:
    setup = Setup(user=READER[0], writes=False)
    setup.document(101).viewers = {3}

    refusal = await setup.refused(named(101), named(102))

    assert "document 101: You may not change it." in refusal.message
    assert "document 102: It does not exist or is hidden from you." in refusal.message


async def test_an_error_that_is_not_about_an_item_ends_the_call() -> None:
    setup = Setup(writes=False)
    setup.fake.user = replace(setup.fake.user, is_superuser=False, permissions=frozenset())

    with pytest.raises(PermissionDenied):
        _ = await setup.run(named(101))


async def test_removing_a_parent_whose_child_stays_is_refused() -> None:
    setup = Setup(writes=False)
    setup.document(101).tags = [1, 2]

    refusal = await setup.refused(item(101, tags_remove=["e2e-finance"]))

    assert "also removes its child tags" in refusal.message


async def test_a_missing_tag_is_refused_unless_it_may_be_created() -> None:
    refusal = await Setup(writes=False).refused(item(101, tags_add=["brand new"]))

    assert "No tag is named 'brand new'." in refusal.message
    assert "create_missing_tags=true" in refusal.message


# serial numbers


async def test_the_same_serial_number_twice_is_refused_for_both() -> None:
    refusal = await Setup(writes=False).refused(
        item(101, archive_serial_number=70), item(102, archive_serial_number=70)
    )

    assert "document 101: Archive serial number 70 is given to several documents" in refusal.message
    assert "document 102: Archive serial number 70 is given" in refusal.message


async def test_a_serial_number_moves_if_its_holder_releases_it_first() -> None:
    setup = Setup()
    setup.document(101).archive_serial_number = 70

    report = await setup.run(
        item(101, clear=["archive_serial_number"]), item(102, archive_serial_number=70)
    )

    assert report.counts == {"changed": 2}
    assert setup.document(102).archive_serial_number == 70


@pytest.mark.parametrize("holder", [{"clear": ["archive_serial_number"]}, {"title": "x"}])
async def test_a_serial_number_of_a_later_document_of_the_call_is_refused(
    holder: dict[str, object],
) -> None:
    setup = Setup(writes=False)
    setup.document(101).archive_serial_number = 70

    refusal = await setup.refused(item(102, archive_serial_number=70), item(101, **holder))

    told = (
        "document 102: Document 101 of this call has archive serial number 70 at that point; "
        "release it there first."
    )
    assert told in refusal.message
    assert "document 101:" not in refusal.message


async def test_a_swap_of_serial_numbers_is_refused() -> None:
    setup = Setup(writes=False)
    setup.document(101).archive_serial_number = 70
    setup.document(102).archive_serial_number = 71

    refusal = await setup.refused(
        item(101, archive_serial_number=71), item(102, archive_serial_number=70)
    )

    assert "document 101: Document 102 of this call has archive serial number 71" in refusal.message


async def test_a_serial_number_taken_elsewhere_stops_only_that_document() -> None:
    setup = Setup()

    report = await setup.run(named(101), item(102, archive_serial_number=2, title="x"), named(103))

    assert statuses(report) == {101: "changed", 102: "failed", 103: "changed"}
    why = report.documents[1].why
    assert why is not None
    assert "archive serial number already exists" in why
    assert setup.document(102).title == "Scan 2"


# document links


async def test_a_link_to_a_hidden_document_is_refused() -> None:
    setup = Setup(user=READER[0], writes=False)
    setup.document(101).owner = 3

    refusal = await setup.refused(item(101, custom_fields={LINK: [2, 999]}))

    told = (
        f"document 101: '{LINK}' links to document 2, 999, which you cannot see or may not change; "
        "Paperless would refuse it."
    )
    assert told in refusal.message


async def test_a_link_to_a_document_that_may_not_be_changed_is_refused() -> None:
    setup = Setup(user=READER[0], writes=False)
    setup.document(101).owner = 3
    setup.document(102).viewers = {3}

    refusal = await setup.refused(item(101, custom_fields={LINK: [102]}))

    assert f"'{LINK}' links to document 102, which" in refusal.message


async def test_a_stored_link_to_a_trashed_document_is_named_before_paperless_refuses_it() -> None:
    setup = Setup(writes=False)
    setup.link(101, [4711])

    refusal = await setup.refused(item(101, custom_fields={TEXT: "x"}))

    assert f"'{LINK}' links to document 4711" in refusal.message


async def test_a_stored_link_does_not_matter_where_no_custom_field_changes() -> None:
    setup = Setup()
    setup.link(101, [4711])

    report = await setup.run(named(101))

    assert report.counts == {"changed": 1}


async def test_links_between_documents_of_the_call_are_expected() -> None:
    setup = Setup()

    report = await setup.run(
        item(101, custom_fields={LINK: [102]}), item(102, custom_fields={LINK: [101, 103]})
    )

    assert report.counts == {"changed": 2}
    assert (setup.links(101), setup.links(102), setup.links(103)) == ([102], [101, 103], [102])
    assert [r.id for r in report.also_changed] == [103]


async def test_a_document_an_earlier_link_already_made_as_asked_is_not_sent() -> None:
    setup = Setup()

    report = await setup.run(
        item(101, custom_fields={LINK: [102]}), item(102, custom_fields={LINK: [101]})
    )

    assert setup.fake.sent == [101]
    assert statuses(report) == {101: "changed", 102: "unchanged"}
    assert report.documents[1].why == "By the time it was read again it was as asked."


async def test_a_link_mirrored_into_another_field_of_the_call_does_not_count_as_changed() -> None:
    setup = Setup()
    setup.link(102, [103])
    setup.link(103, [102])

    report = await setup.run(
        item(101, custom_fields={LINK: [102]}),
        item(102, custom_fields={LINK: [101, 103], TEXT: "x"}),
        item(103, title="C"),
    )

    assert report.counts == {"changed": 3}
    assert setup.links(102) == [103, 101]


async def test_a_removed_link_is_taken_off_the_other_document_and_expected_there() -> None:
    setup = Setup()
    setup.link(101, [102])
    setup.link(102, [101, 103])
    setup.link(103, [102])

    report = await setup.run(
        item(101, remove_custom_fields=[LINK]),
        item(102, custom_fields={LINK: [103], TEXT: "x"}),
    )

    assert report.counts == {"changed": 2}
    assert setup.links(102) == [103]


@pytest.mark.parametrize(
    "other", [{"custom_fields": {LINK: [103]}}, {"remove_custom_fields": [LINK]}]
)
@pytest.mark.parametrize("first", [True, False])
async def test_items_that_contradict_each_other_through_a_link_are_refused(
    other: dict[str, object], *, first: bool
) -> None:
    setup = Setup(writes=False)
    setup.link(102, [104])
    setup.link(104, [102])
    items = [item(101, custom_fields={LINK: [102]}), item(102, **other)]

    refusal = await setup.refused(*(items if first else items[::-1]))

    told = (
        f"document 101: It links to document 102 in '{LINK}', but the item of 102 sets that field "
        "without 101."
    )
    assert told in refusal.message
    assert "document 102: It sets" in refusal.message


async def test_an_item_that_is_as_asked_contradicts_one_that_drops_its_link() -> None:
    setup = Setup(writes=False)
    setup.link(101, [102])
    setup.link(102, [101])

    refusal = await setup.refused(
        item(101, custom_fields={LINK: [102]}), item(102, custom_fields={LINK: []})
    )

    assert "document 101: It links to document 102" in refusal.message


async def test_a_document_of_the_call_that_a_link_changes_besides_is_named_for_it() -> None:
    setup = Setup(writes=False)

    report = await setup.run(item(101, custom_fields={LINK: [102]}), named(102), dry_run=True)

    assert [r.id for r in report.also_changed] == [102]


async def test_a_one_sided_link_that_a_change_would_mirror_again_contradicts_too() -> None:
    setup = Setup(writes=False)
    setup.link(101, [102])  # 102 does not link back; sending 101's fields mirrors it
    setup.link(102, [104])
    setup.link(104, [102])

    refusal = await setup.refused(
        item(101, custom_fields={TEXT: "x"}), item(102, custom_fields={LINK: []})
    )

    assert "document 101: It links to document 102" in refusal.message


async def test_a_one_sided_link_that_is_mirrored_again_is_named() -> None:
    setup = Setup(writes=False)
    setup.link(101, [102, 101])

    report = await setup.run(item(101, custom_fields={TEXT: "x"}), dry_run=True)

    assert [r.id for r in report.also_changed] == [102]


async def test_hidden_documents_that_lose_a_link_are_named_by_number() -> None:
    setup = Setup(user=READER[0], writes=False)
    setup.document(101).owner = 3
    setup.link(101, [2])

    report = await setup.run(item(101, remove_custom_fields=[LINK]), dry_run=True)

    assert [(r.id, r.name) for r in report.also_changed] == [(2, None)]
    assert "besides (#2)" in report.message


async def test_targets_are_read_in_chunks_of_one_hundred() -> None:
    setup = Setup(count=150, writes=False)

    report = await setup.run(
        item(101, custom_fields={LINK: [100 + n for n in range(2, 151)]}), dry_run=True
    )

    assert setup.fake.lists == 3
    assert len(report.also_changed) == 149


# --- what was shown is what is written


async def test_the_confirmation_shows_values_of_twenty_documents_and_the_fields_of_the_rest() -> (
    None
):
    setup = Setup(count=22, writes=False)

    refusal = await setup.asked(*(named(100 + n) for n in range(1, 23)))

    assert "120 'Scan 20' (title 'Scan 20' -> 'New 120')" in refusal.message
    assert "121 'Scan 21' (title), 122 'Scan 22' (title)" in refusal.message


async def test_owner_changes_and_new_tags_are_shown_in_full_also_beyond_twenty() -> None:
    setup = Setup(count=23, writes=False)
    long = "a new tag " + "x" * 118

    refusal = await setup.asked(
        *(named(100 + n) for n in range(1, 21)),
        item(121, owner="e2e-reader"),
        item(122, clear=["owner"]),
        item(123, tags_add=[long, "Other"]),
        item(1, tags_add=["OTHER"]),
        create=True,
    )

    # As every text the server did not write: cut to 60 characters (decision-44), but bound.
    shown = long[:59] + "…"
    also = "'Other' (also for 'OTHER')"
    assert f"create the tags '{shown}', {also}, private to you" in refusal.message
    start = "Nothing was changed. Confirming would change the owner of document 121"
    assert refusal.message.startswith(start)
    places = [refusal.message.index(part) for part in (start, "create the tags", "change 24")]
    assert places == sorted(places)
    told = (
        "change the owner of document 121 'Scan 21' to 'e2e-reader' (id 3) and of document 122 "
        "'Scan 22' to no owner (every user who may view documents sees it): a new owner takes it "
        "over and you may lose access to it"
    )
    assert told in refusal.message


async def test_an_owner_whose_name_cannot_be_listed_is_shown_by_number() -> None:
    setup = Setup(writes=False)
    setup.fake.user = replace(
        setup.fake.user,
        is_superuser=False,
        permissions=frozenset({"view_document", "change_document"}),
    )

    refusal = await setup.asked(item(101, owner=3))

    assert "to user 3" in refusal.message


async def test_one_confirmation_covers_the_call_and_an_accepted_dialog_runs_it() -> None:
    setup = Setup()
    items = [named(101), item(102, owner="e2e-reader"), item(103, custom_fields={LINK: [104]})]
    refusal = await setup.asked(*items)
    assert setup.fake.sent == []

    with accepting(refusal.fingerprint):
        report = await setup.run(*items, confirm=False)

    assert report.counts == {"changed": 3}


def _title(setup: Setup) -> None:
    setup.document(101).title = "Someone else's"


def _hidden_value(setup: Setup) -> None:
    # Beyond the first 20 values are not shown, but bound.
    setup.document(125).correspondent = 9


def _as_asked(setup: Setup) -> None:
    setup.document(103).title = "New 103"


def _other_owner(setup: Setup) -> None:
    setup.document(104).owner = 3


def _new_link(setup: Setup) -> None:
    setup.link(105, [104])


def _tag(setup: Setup) -> None:
    setup.state.tags.append(FakeTag(id=99, name="brand new"))


@pytest.mark.parametrize(
    "change", [_title, _hidden_value, _as_asked, _other_owner, _new_link, _tag]
)
async def test_an_accepted_dialog_does_not_cover_what_changed_while_it_was_open(
    change: Callable[[Setup], None],
) -> None:
    setup = Setup(count=25, writes=False)
    items = [
        *(named(100 + n) for n in range(1, 4)),
        item(104, owner="e2e-reader"),
        item(105, custom_fields={LINK: [106]}, tags_add=["brand new"]),
        *(named(100 + n) for n in range(6, 25)),
        item(125, correspondent="e2e-acme", title="New 125"),
    ]
    refusal = await setup.asked(*items, create=True)
    change(setup)
    setup.updates = bulk_updates(setup.fake, clock=lambda: setup.fake.now)  # a fresh cache

    with accepting(refusal.fingerprint):
        again = await setup.asked(*items, create=True)

    assert again.fingerprint != refusal.fingerprint


async def test_other_documents_behind_the_same_count_ask_again() -> None:
    # Of other documents at most 100 are named; which of the rest lack the link back is bound.
    messages: list[str] = []
    prints: list[str] = []
    for linked_back in (240, 241):
        setup = Setup(writes=False, count=150)
        setup.link(linked_back, [101])
        refusal = await setup.asked(item(101, custom_fields={LINK: [*range(102, 251)]}))
        messages.append(refusal.message)
        prints.append(refusal.fingerprint)

    assert "and 48 more" in messages[0]
    assert messages[0] == messages[1]
    assert prints[0] != prints[1]


async def test_other_new_tags_behind_the_same_text_ask_again() -> None:
    setup = Setup(writes=False)
    start = "x" * 70

    one = await setup.asked(item(101, tags_add=[start + "1"]), create=True)
    two = await setup.asked(item(101, tags_add=[start + "2"]), create=True)

    assert one.message == two.message
    assert one.fingerprint != two.fingerprint


async def test_other_new_values_behind_the_same_text_ask_again() -> None:
    setup = Setup(writes=False, count=21)
    first = [named(100 + n) for n in range(1, 21)]

    one = await setup.asked(*first, item(121, title="one"))
    two = await setup.asked(*first, item(121, title="two"))

    assert one.message == two.message
    assert one.fingerprint != two.fingerprint


@pytest.mark.parametrize(
    ("values", "change"),
    [
        ({"title": "New"}, {"title": "Someone else's"}),
        ({"title": "Scan 1", "created": "2026-01-01"}, {"title": "Someone else's"}),
        ({"tags_add": ["e2e-inbox"]}, {"tags": [1]}),
        ({"clear": ["owner"]}, {"owner": 3}),
        ({"correspondent": "e2e-acme"}, {"correspondent": 9}),
        ({"archive_serial_number": 70}, {"archive_serial_number": 71}),
        ({"custom_fields": {TEXT: "x"}}, {"custom_fields": {1: "y"}}),
        ({"remove_custom_fields": [LINK], "title": "New"}, {"custom_fields": {8: [102]}}),
    ],
)
async def test_a_touched_value_someone_changed_in_between_is_not_overwritten(
    values: dict[str, object], change: dict[str, object]
) -> None:
    setup = Setup()
    setup.fake.before[101] = lambda: setup.document(101).__dict__.update(change)

    report = await setup.run(item(101, **values), named(102))

    assert setup.fake.sent == [102]
    assert statuses(report) == {101: "changed_meanwhile", 102: "changed"}
    assert report.documents[0].why == (
        "A value this call touches changed after it was checked, so nothing was written."
    )
    assert report.left == (101,)
    assert report.message.startswith("1 of 2 documents is not as asked: 1 changed_meanwhile. ")


async def test_a_document_that_became_as_asked_meanwhile_is_not_sent() -> None:
    setup = Setup()
    setup.fake.before[101] = lambda: setup.document(101).__dict__.update(title="New 101")

    report = await setup.run(named(101), named(102))

    assert setup.fake.sent == [102]
    assert statuses(report) == {101: "unchanged", 102: "changed"}
    assert report.left == ()


# --- phase 2: what can still go wrong


REFUSED: list[PaperlessMcpError] = [
    Validation("Paperless rejected the request: archive_serial_number: taken."),
    PermissionDenied("The Paperless user may not perform this action."),
    NotFound("No document 102 exists that this user may see."),
]


@pytest.mark.parametrize("error", REFUSED)
async def test_a_refusal_of_one_document_leaves_the_others(error: PaperlessMcpError) -> None:
    setup = Setup()
    setup.fake.write_fails[102] = error

    report = await setup.run(named(101), named(102), named(103))

    assert statuses(report) == {101: "changed", 102: "failed", 103: "changed"}
    assert report.documents[1].why == error.message
    assert report.counts == {"changed": 2, "failed": 1}
    assert report.left == (102,)
    assert report.message == (
        "1 of 3 documents is not as asked: 1 failed. Changed 2 documents. Documents not as asked: "
        f"102 (failed): {error.message} Pass these items again once the reason is gone; what is "
        "done stays as it is."
    )


async def test_a_document_that_vanished_is_named_and_the_others_go_on() -> None:
    setup = Setup()
    setup.fake.before[102] = lambda: setup.state.documents.remove(setup.document(102))

    report = await setup.run(named(101), named(102), named(103))

    assert statuses(report) == {101: "changed", 102: "failed", 103: "changed"}
    assert setup.fake.sent == [101, 103]


async def test_a_refused_link_says_what_it_needs() -> None:
    setup = Setup()
    setup.fake.write_fails[101] = PermissionDenied("The Paperless user may not.")

    error = await setup.failed(item(101, custom_fields={LINK: [102]}))

    assert "A document link needs change permission on the linked document." in error.message


async def test_a_refused_owner_change_may_have_taken_links_off() -> None:
    # Paperless unlinks the other documents before it checks the owner; beforehand it is refused
    # where the user is known.
    setup = Setup()
    setup.fake.nobody = True
    setup.link(101, [102])
    setup.fake.write_fails[101] = PermissionDenied("The Paperless user may not.")

    error = await setup.failed(item(101, owner="e2e-reader", remove_custom_fields=[LINK]))
    other = await setup.failed(item(101, owner="e2e-reader"))

    assert "Links to it may be gone on the documents it linked to." in error.message
    assert "Links" not in other.message


LOST: list[PaperlessMcpError] = [
    TIMEOUT,
    UpstreamUnavailable("Paperless is temporarily unavailable (HTTP 502).", status=502),
    UpstreamUnreachable("Paperless is unreachable."),
    PaperlessMcpError("Paperless sent an unexpected response."),
]


@pytest.mark.parametrize("error", LOST)
async def test_a_lost_answer_is_looked_up_once_and_the_others_go_on(
    error: PaperlessMcpError,
) -> None:
    setup = Setup()
    setup.fake.write_fails[101] = error
    setup.fake.applied = {101}
    setup.fake.write_fails[103] = error

    report = await setup.run(named(101), named(102), named(103))

    assert statuses(report) == {101: "changed", 102: "changed", 103: "unknown"}
    assert report.documents[0].why == f"{error.message} Read again: it is as asked."
    assert report.documents[2].why == f"{error.message} {NOT_YET}"
    assert report.documents[2].changes == ()
    assert [c.new for c in report.documents[0].changes] == ["New 101"]
    assert setup.fake.got == [101, 101, 102, 103, 103]
    assert setup.fake.sent == [101, 102, 103]  # never repeated
    assert report.left == (103,)


async def test_a_change_that_was_made_only_partly_shows_what_was_seen() -> None:
    setup = Setup()
    setup.fake.write_fails[101] = TIMEOUT

    def partly() -> None:
        setup.document(101).title = "New 101"

    setup.fake.before[101] = lambda: setup.fake.before.update({101: partly})
    report = await setup.run(item(101, title="New 101", correspondent="e2e-acme"), named(102))

    assert statuses(report) == {101: "unknown", 102: "changed"}
    assert [(c.field, c.new) for c in report.documents[0].changes] == [("title", "New 101")]


async def test_a_lost_answer_that_cannot_be_looked_up_stays_unknown() -> None:
    setup = Setup()
    setup.fake.write_fails[101] = TIMEOUT
    setup.fake.read_fails[101] = [None, NotFound("gone")]

    report = await setup.run(item(101, owner="e2e-reader"), named(102))

    assert statuses(report) == {101: "unknown", 102: "changed"}
    assert report.documents[0].why == f"{TIMEOUT.message} It could not be read again: gone"


async def test_two_lost_answers_in_a_row_stop_the_call() -> None:
    setup = Setup()
    setup.fake.write_fails = {102: TIMEOUT, 103: TIMEOUT}

    report = await setup.run(*(named(n) for n in range(101, 106)))

    assert statuses(report) == {
        101: "changed",
        102: "unknown",
        103: "unknown",
        104: "not_started",
        105: "not_started",
    }
    assert setup.fake.sent == [101, 102, 103]
    assert report.left == (102, 103, 104, 105)
    assert "Stopped: Paperless did not answer twice in a row." in report.message


async def test_two_lost_answers_that_are_not_in_a_row_do_not_stop() -> None:
    setup = Setup()
    setup.fake.write_fails = {101: TIMEOUT, 103: TIMEOUT}

    report = await setup.run(*(named(n) for n in range(101, 106)))

    assert report.counts == {"unknown": 2, "changed": 3}


async def test_lost_answers_that_were_looked_up_do_not_stop_the_call() -> None:
    setup = Setup()
    setup.fake.write_fails = dict.fromkeys(range(101, 106), TIMEOUT)
    setup.fake.applied = {*range(101, 106)}

    report = await setup.run(*(named(n) for n in range(101, 106)))

    assert report.counts == {"changed": 5}


async def test_reads_that_fail_write_nothing_and_count_as_lost_answers() -> None:
    setup = Setup()
    setup.fake.read_fails = {102: [TIMEOUT], 103: [TIMEOUT]}

    report = await setup.run(*(named(n) for n in range(101, 105)))

    assert statuses(report) == {
        101: "changed",
        102: "failed",
        103: "failed",
        104: "not_started",
    }
    assert setup.fake.sent == [101]


ENDING: list[PaperlessMcpError] = [LIMITED, AuthenticationFailed("Paperless rejected the token.")]


@pytest.mark.parametrize("error", ENDING)
@pytest.mark.parametrize("reading", [True, False])
async def test_a_rate_limit_or_a_rejected_credential_ends_the_call(
    error: PaperlessMcpError, *, reading: bool
) -> None:
    setup = Setup()
    if reading:
        setup.fake.read_fails[102] = [error]
    else:
        setup.fake.write_fails[102] = error

    report = await setup.run(named(101), named(102), named(103))

    assert statuses(report) == {101: "changed", 102: "failed", 103: "not_started"}
    assert setup.fake.got == [101, 102]  # nothing more is asked
    assert setup.fake.sent == ([101] if reading else [101, 102])
    assert f"Stopped: {error.message}" in report.message


@pytest.mark.parametrize("error", ENDING)
async def test_a_rate_limit_while_looking_a_lost_answer_up_ends_the_call(
    error: PaperlessMcpError,
) -> None:
    setup = Setup()
    setup.fake.write_fails[102] = TIMEOUT
    setup.fake.read_fails[102] = [None, error]

    report = await setup.run(named(101), named(102), named(103))

    assert statuses(report) == {101: "changed", 102: "unknown", 103: "not_started"}


async def test_nothing_is_started_after_forty_seconds() -> None:
    setup = Setup()
    setup.fake.takes = 20.0

    report = await setup.run(*(named(n) for n in range(101, 106)))

    assert statuses(report) == {
        101: "changed",
        102: "changed",
        103: "not_started",
        104: "not_started",
        105: "not_started",
    }
    assert report.left == (103, 104, 105)
    assert report.message == (
        "3 of 5 documents are not as asked: 3 not_started. Changed 2 documents. Stopped: 40 "
        "seconds have passed. Pass the items in left again; what is done stays as it is."
    )


async def test_a_call_that_ran_out_of_time_before_the_first_write_is_no_error() -> None:
    setup = Setup(writes=False)
    times = iter([0.0, 0.0, 40.0])
    setup.updates = bulk_updates(setup.fake, clock=lambda: next(times))

    report = await setup.run(named(101))

    assert statuses(report) == {101: "not_started"}


async def test_a_call_in_which_nothing_was_changed_and_something_failed_is_an_error() -> None:
    setup = Setup()
    setup.fake.write_fails = {101: REFUSED[0], 102: TIMEOUT}

    error = await setup.failed(named(101), named(102), item(103, title="Scan 3"))

    assert error.message == (
        "No document is known to be changed; 2 of 3 documents are not as asked: 1 failed, 1 "
        f"unknown. Documents not as asked: 101 (failed): {REFUSED[0].message} 102 (unknown): "
        f"{TIMEOUT.message} {NOT_YET}"
    )
    assert error.hint == "Read the documents again before repeating the call."


# tags


async def test_missing_tags_are_created_once_before_the_first_change() -> None:
    setup = Setup()

    report = await setup.run(
        item(101, tags_add=["Brand new"]),
        item(102, tags_set=["brand NEW", "e2e-inbox"]),
        create=True,
    )

    [tag] = [t for t in setup.state.tags if t.name == "Brand new"]
    assert (setup.document(101).tags, setup.document(102).tags) == ([tag.id], [tag.id, 4])
    assert report.created_tags == ("Brand new",)
    assert report.documents[0].changes[0].new == ["Brand new"]
    assert report.documents[0].implicit_tags == ()


async def test_a_tag_that_cannot_be_created_changes_no_document_and_names_those_that_were() -> None:
    setup = Setup()
    setup.fake.tag_fails["two"] = LIMITED

    error = await setup.failed(item(101, tags_add=["one", "two"]), named(102), create=True)

    assert setup.fake.sent == []
    assert error.message == f"{LIMITED.message} Tags created before this failed: 'one'."


async def test_a_tag_whose_creation_answer_was_lost_is_found_by_the_next_call() -> None:
    setup = Setup()
    setup.fake.tag_fails["one"] = TIMEOUT
    _ = await setup.failed(item(101, tags_add=["one"]), create=True)
    setup.state.tags.append(FakeTag(id=90, name="one", owner=2))

    report = await setup.run(item(101, tags_add=["one"]), create=True)

    assert (report.created_tags, setup.document(101).tags) == ((), [90])


async def test_created_tags_are_named_when_every_document_failed_after_that() -> None:
    setup = Setup()
    setup.fake.write_fails[101] = REFUSED[0]

    error = await setup.failed(item(101, tags_add=["one"]), create=True)

    assert "Tags created: 'one'." in error.message


# --- properties

outcomes = st.sampled_from(["fine", "refused", "lost", "late", "limited", "vanished", "done"])


@given(plan=st.lists(outcomes, min_size=1, max_size=5), dry_run=st.booleans())
async def test_every_document_ends_in_exactly_one_status_and_left_names_the_rest(
    *, plan: list[str], dry_run: bool
) -> None:
    setup = Setup()
    numbers = [101 + at for at in range(len(plan))]
    for number, ends in zip(numbers, plan, strict=True):
        if ends in {"refused", "lost", "late", "limited"}:
            setup.fake.write_fails[number] = {
                "refused": REFUSED[0],
                "lost": TIMEOUT,
                "late": TIMEOUT,
                "limited": LIMITED,
            }[ends]
        if ends == "late":
            setup.fake.applied.add(number)
        if ends == "vanished":
            setup.fake.read_fails[number] = [NotFound("gone")]
        if ends == "done":
            setup.document(number).title = f"New {number}"

    try:
        report = await setup.run(*(named(n) for n in numbers), dry_run=dry_run)
    except PaperlessMcpError:
        assert not dry_run
        before = [n for n, ends in zip(numbers, plan, strict=True) if ends == "done"]
        assert [n for n in numbers if setup.document(n).title == f"New {n}"] == before
        return
    assert [d.id for d in report.documents] == numbers
    assert sum(report.counts.values()) == len(numbers)
    done = {d.id for d in report.documents if d.status in {"changed", "unchanged"}}
    assert {*report.left} == {*numbers} - done
    assert len(report.left) == len(numbers) - len(done)
    if dry_run:
        assert setup.fake.sent == []
    for number in done:
        assert setup.document(number).title == f"New {number}"


async def test_a_one_sided_link_that_is_dropped_changes_nothing_on_the_other_side() -> None:
    setup = Setup()
    setup.link(101, [102])
    setup.link(102, [103])
    setup.link(103, [102])

    report = await setup.run(
        item(101, custom_fields={LINK: []}), item(102, custom_fields={LINK: [], TEXT: "x"})
    )

    assert report.counts == {"changed": 2}
    assert (setup.links(102), setup.links(103)) == ([], [])


async def test_documents_too_large_to_read_are_refused() -> None:
    setup = Setup(writes=False)
    setup.fake.list_fails = AnswerTooLarge("Too large.")

    with pytest.raises(AnswerTooLarge) as raised:
        _ = await setup.run(named(101))

    assert raised.value.message == "The documents of this call are larger than 64 MiB."
    assert raised.value.hint == "Name fewer documents per call."


async def test_a_message_that_alone_is_too_large_is_cut_as_well() -> None:
    setup = Setup(writes=False, count=20)
    setup.state.tags += [FakeTag(id=1000 + n, name=f"x{n}" * 12) for n in range(200, 400)]
    every = [f"x{n}" * 12 for n in range(200, 400)]

    report = await setup.run(*(item(100 + n, tags_add=every) for n in range(1, 21)), dry_run=True)

    assert report.truncated
    assert len(report.model_dump_json()) <= MAX_ANSWER
    assert report.message.startswith("Dry run, nothing was changed. Confirming would change 20")
    assert report.message.endswith("…")


async def test_the_reasons_a_result_repeats_are_limited_those_of_an_error_are_not() -> None:
    setup = Setup(count=13)
    for n in range(1, 13):
        setup.fake.write_fails[100 + n] = Validation("no " + "x" * 400)

    report = await setup.run(*(named(100 + n) for n in range(1, 14)))
    error = await setup.failed(*(named(100 + n) for n in range(1, 13)))

    assert report.message.count("(failed)") == 10
    assert " And 2 more. " in report.message
    assert error.message.count("(failed): no " + "x" * 296 + "…") == 12


# --- the review round


async def test_a_refusal_of_one_hundred_long_values_is_bounded_and_names_every_item() -> None:
    setup = Setup(writes=False, count=100)

    refusal = await setup.refused(*(item(100 + n, tags_add=["x" * 5000]) for n in range(1, 101)))

    assert len(refusal.message) < 70_000
    assert all(f"document {100 + n}: " in refusal.message for n in range(1, 101))
    assert "create_missing_tags=true" in refusal.message


async def test_many_contradictions_of_one_item_are_bounded_too() -> None:
    setup = Setup(writes=False, count=100)
    every = [100 + n for n in range(2, 101)]

    refusal = await setup.refused(
        item(101, custom_fields={LINK: every}),
        *(item(n, custom_fields={LINK: []}) for n in every),
    )

    assert len(refusal.message) < 70_000
    assert all(f"document {n}: " in refusal.message for n in [101, *every])


async def test_the_largest_serial_number_is_checked_beforehand() -> None:
    setup = Setup()

    refusal = await setup.refused(item(101, archive_serial_number=4_294_967_296))
    report = await setup.run(item(101, archive_serial_number=4_294_967_295))

    assert "document 101: An archive serial number is at most 4294967295." in refusal.message
    assert report.counts == {"changed": 1}


def restricted(setup: Setup) -> None:
    """The user becomes one with every permission a change needs, but no superuser."""
    setup.fake.user = replace(
        setup.fake.user,
        is_superuser=False,
        permissions=frozenset(
            {
                *("view_document", "change_document", "view_uisettings", "view_user"),
                *("view_customfield", "view_tag"),
            }
        ),
    )


def granted(setup: Setup, number: int) -> None:
    """The document is someone else's, with a grant to change it (the fake's rows know grants)."""
    setup.document(number).owner = 3
    setup.document(number).viewers = setup.document(number).editors = {2}
    setup.fake.changeable.add(number)


@pytest.mark.parametrize("values", [{}, {"remove_custom_fields": [LINK]}])
async def test_an_owner_change_by_another_than_the_owner_is_refused_beforehand(
    values: dict[str, object],
) -> None:
    setup = Setup(writes=False)
    restricted(setup)
    granted(setup, 101)
    setup.link(101, [102])

    refusal = await setup.refused(item(101, owner=2, **values), named(102))

    assert "document 101: Only its owner may change the owner." in refusal.message
    assert "document 102:" not in refusal.message


@pytest.mark.parametrize("owner", [2, None])
async def test_the_owner_and_anyone_without_an_owner_may_change_it(owner: int | None) -> None:
    setup = Setup()
    restricted(setup)
    setup.document(101).owner = owner

    report = await setup.run(item(101, owner="e2e-reader"))

    assert report.counts == {"changed": 1}


async def test_a_link_to_a_document_an_earlier_item_gives_away_is_refused() -> None:
    setup = Setup(writes=False)
    restricted(setup)

    refusal = await setup.refused(
        item(101, owner="e2e-reader"), item(102, custom_fields={LINK: [101]})
    )

    told = (
        f"document 102: '{LINK}' links to document 101, which an earlier item of this call gives "
        "away; put the link first."
    )
    assert told in refusal.message


async def test_the_link_first_works_and_so_does_a_superuser_in_any_order() -> None:
    items = [item(101, owner="e2e-reader"), item(102, custom_fields={LINK: [101]})]
    mine, superuser = Setup(), Setup()
    restricted(mine)

    first = await mine.run(*reversed(items))
    second = await superuser.run(*items)

    assert (first.counts, second.counts) == ({"changed": 2}, {"changed": 2})


@pytest.mark.parametrize("values", [{"clear": ["owner"]}, {"owner": 2}])
async def test_a_document_that_anyone_or_the_user_then_owns_can_still_be_linked(
    values: dict[str, object],
) -> None:
    setup = Setup(writes=False)
    restricted(setup)
    setup.document(101).owner = None if "owner" in values else 2

    report = await setup.run(
        item(101, **values), item(102, custom_fields={LINK: [101]}), dry_run=True
    )

    assert report.counts == {"would_change": 2}


async def test_a_link_after_giving_away_is_refused_where_the_user_is_not_known() -> None:
    setup = Setup(writes=False)
    setup.fake.nobody = True

    refusal = await setup.refused(
        item(101, owner="e2e-reader"), item(102, custom_fields={LINK: [101]})
    )

    assert "which an earlier item of this call gives away" in refusal.message


async def test_who_is_signed_in_is_asked_for_owner_changes_only() -> None:
    setup = Setup(writes=False)
    setup.fake.user = replace(
        setup.fake.user, is_superuser=False, permissions=frozenset({"view_document"})
    )

    report = await setup.run(named(101), dry_run=True)

    assert report.counts == {"would_change": 1}


async def test_only_what_was_sent_counts_for_the_other_documents() -> None:
    setup = Setup()
    setup.fake.write_fails = {102: REFUSED[0], 103: LIMITED}

    report = await setup.run(
        item(101, custom_fields={LINK: [1]}),
        item(102, custom_fields={LINK: [2]}),
        item(103, custom_fields={LINK: [105]}),
        item(104, custom_fields={LINK: [105]}),
    )

    assert statuses(report) == {101: "changed", 102: "failed", 103: "failed", 104: "not_started"}
    assert [r.id for r in report.also_changed] == [1]


async def test_a_lost_change_counts_as_sent() -> None:
    setup = Setup()
    setup.fake.write_fails[102] = TIMEOUT

    report = await setup.run(named(101), item(102, custom_fields={LINK: [103]}))

    assert [r.id for r in report.also_changed] == [103]


async def test_one_document_that_was_changed_partly_is_a_result_not_an_error() -> None:
    setup = Setup()
    setup.fake.write_fails[101] = TIMEOUT

    def partly() -> None:
        setup.document(101).title = "New 101"

    setup.fake.before[101] = lambda: setup.fake.before.update({101: partly})
    report = await setup.run(item(101, title="New 101", correspondent="e2e-acme"))

    assert statuses(report) == {101: "unknown"}
    assert [c.new for c in report.documents[0].changes] == ["New 101"]
    assert report.left == (101,)


async def test_every_list_request_is_bounded_by_64_mib() -> None:
    setup = Setup(writes=False)

    _ = await setup.run(item(101, custom_fields={LINK: [1]}), dry_run=True)

    assert setup.fake.limits == [64 * 1024 * 1024] * 2


async def test_no_tag_is_created_after_forty_seconds_and_those_that_were_are_named() -> None:
    setup = Setup()
    setup.fake.tag_takes = 20.0

    report = await setup.run(item(101, tags_add=["a", "b", "c"]), create=True)

    assert statuses(report) == {101: "not_started"}
    assert report.created_tags == ("a", "b")
    assert [t.name for t in setup.state.tags if t.name in {"a", "b", "c"}] == ["a", "b"]
    assert "Tags created: 'a', 'b'." in report.message
    assert setup.fake.got == []


async def test_checks_that_take_longer_than_forty_seconds_end_the_call() -> None:
    setup = Setup(writes=False, count=150)
    setup.fake.list_takes = 25.0
    link = item(101, custom_fields={LINK: [*range(102, 251)]})

    error = await setup.failed(link)
    preview = await setup.run(link, dry_run=True)

    assert error.message == (
        "Nothing was changed: checking the documents and their links took longer than 40 seconds."
    )
    assert error.hint == "Name fewer documents or links per call."
    assert preview.counts == {"would_change": 1}  # a preview writes nothing and may take its time


async def test_two_spellings_of_a_new_tag_are_both_shown_and_bound() -> None:
    setup = Setup(writes=False)

    one = await setup.asked(
        item(101, tags_add=["Brand new"]), item(102, tags_add=["BRAND new"]), create=True
    )
    two = await setup.asked(
        item(101, tags_add=["Brand new"]), item(102, tags_add=["Brand new"]), create=True
    )

    assert "create the tag 'Brand new' (also for 'BRAND new'), private" in one.message
    assert "also for" not in two.message


async def test_a_document_whose_fields_are_not_sent_drops_no_link() -> None:
    # 103 makes the call know the link fields; of 101 only the title is sent.
    setup = Setup(writes=False)
    setup.link(101, [102])
    setup.link(102, [101])

    report = await setup.run(named(101), item(103, custom_fields={TEXT: "x"}), dry_run=True)

    assert report.also_changed == ()


async def test_only_a_missing_right_is_explained_by_the_link() -> None:
    setup = Setup()
    setup.fake.write_fails[101] = Validation("Paperless rejected the request: no.")

    error = await setup.failed(item(101, custom_fields={LINK: [102]}))

    assert "A document link needs" not in error.message


async def test_created_tags_stay_when_every_document_changed_meanwhile() -> None:
    setup = Setup()
    setup.fake.before[101] = lambda: setup.document(101).__dict__.update(tags=[1])

    error = await setup.failed(item(101, tags_add=["one"]), create=True)

    assert "Tags created: 'one'." in error.message
    assert [t.name for t in setup.state.tags].count("one") == 1


async def test_what_is_left_says_that_a_document_given_away_cannot_be_repeated() -> None:
    setup = Setup()
    setup.fake.write_fails[102] = REFUSED[0]

    report = await setup.run(item(101, owner="e2e-reader"), named(102))
    other = await Setup().run(named(101), named(102))

    told = "Leave out what was given away: you may no longer see or change it."
    assert report.message.endswith(told)
    assert told not in other.message
