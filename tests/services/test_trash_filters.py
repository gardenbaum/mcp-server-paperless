"""Filters of the trash: what they match, the cap, and that acting by one only shows (PLMCP-78).

The fakes of the filter refusals are created without `allow_writes`: any write would raise.
"""

import itertools
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import READER
from fakes.paperless import FakeDocument, FakePaperless

from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    AnswerTooLarge,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    UpstreamUnreachable,
    Validation,
    accepting,
)
from mcp_server_paperless.domain.trash import MAX_IDS, TrashFilter
from mcp_server_paperless.services.trash import CHECKED, NO_FILTER, NO_ID, ONE_OF, TrashService
from mcp_server_paperless.services.trash_scan import EXPLAINED, NOT_LISTED_HINT, PAGE, TrashScan
from services.test_trash import DAY, WEB, Setup

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.trash import TrashList, TrashPage

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-55")]


async def found(setup: Setup, wanted: TrashFilter, *, page: int = 1, size: int = 25) -> TrashList:
    return await setup.service.list(wanted, page=page, page_size=size)


async def ids(setup: Setup, wanted: TrashFilter) -> list[int]:
    return sorted(e.id for e in (await found(setup, wanted)).entries)


def deleted(setup: Setup, number: int, moment: datetime) -> None:
    [entry] = [d for d in setup.state.trash if d.id == number]
    entry.deleted_at = moment


def capped(setup: Setup, cap: int, fake: FakePaperless | None = None) -> TrashService:
    reader = fake or setup.fake
    scan = TrashScan(reader, reader, setup.taxonomy, cap=cap)
    return TrashService(reader, reader, reader, scan, setup.taxonomy, web_url=WEB)


# --- list ---------------------------------------------------------------------------------------


async def test_a_title_matches_in_part_and_ignoring_case() -> None:
    setup = Setup()
    one = await setup.trashed("Straßenrechnung March")
    _ = await setup.trashed("Letter")

    assert await ids(setup, TrashFilter(title="STRASSENRECHNUNG")) == [one]
    assert await ids(setup, TrashFilter(title="nothing like it")) == []


async def test_kind_separates_documents_from_versions() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    _, version = await setup.version("Report")

    assert await ids(setup, TrashFilter(kind="document")) == [one]
    assert await ids(setup, TrashFilter(kind="version")) == [version]


async def test_days_are_utc_and_inclusive() -> None:
    setup = Setup()
    first, last = await setup.trashed("first"), await setup.trashed("last")
    deleted(setup, first, datetime(2026, 11, 10, 0, 0, tzinfo=UTC))
    deleted(setup, last, datetime(2026, 11, 10, 23, 59, 59, tzinfo=UTC))
    before, after = await setup.trashed("before"), await setup.trashed("after")
    deleted(setup, before, datetime(2026, 11, 9, 23, 59, 59, tzinfo=UTC))
    deleted(setup, after, datetime(2026, 11, 11, 0, 0, tzinfo=UTC))
    day = date(2026, 11, 10)

    assert await ids(setup, TrashFilter(deleted_from=day, deleted_to=day)) == [first, last]
    assert await ids(setup, TrashFilter(deleted_from=day)) == [first, last, after]
    assert await ids(setup, TrashFilter(deleted_to=day)) == [first, last, before]


async def test_days_the_wrong_way_round_are_refused() -> None:
    with pytest.raises(Validation, match="deleted_from is after deleted_to"):
        _ = await found(
            Setup(), TrashFilter(deleted_from=date(2026, 2, 1), deleted_to=date(2026, 1, 1))
        )


async def test_a_document_type_is_resolved_and_a_version_has_that_of_its_document() -> None:
    setup = Setup()
    invoice = setup.state.document_types[0]
    one, _ = await setup.trashed("typed"), await setup.trashed("untyped")
    [entry] = [d for d in setup.state.trash if d.id == one]
    entry.document_type = invoice.id
    document, version = await setup.version("Report")
    [live] = [d for d in setup.state.documents if d.id == document]
    live.document_type = invoice.id
    _ = await setup.version("Other")

    for named in (invoice.name, invoice.id):
        assert await ids(setup, TrashFilter(document_type=named)) == [one, version]
    with pytest.raises(NotFound):
        _ = await found(setup, TrashFilter(document_type="no such type"))


async def test_ids_and_other_filters_are_combined_and_missing_ids_are_named() -> None:
    setup = Setup()
    one, two = await setup.trashed("Doc"), await setup.trashed("Letter")

    both = await found(setup, TrashFilter(ids=(two, one, two, 4242)))
    narrowed = await found(setup, TrashFilter(ids=(two, one), title="doc"))

    assert [e.id for e in both.entries] == [two, one]
    assert both.notice == "Not in the trash: 4242."
    assert (both.truncated, both.scanned) == (False, None)
    assert [e.id for e in narrowed.entries] == [one]
    assert narrowed.notice is None


async def test_rows_name_the_type_and_the_owner_if_the_user_may_know() -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    [entry] = [d for d in setup.state.trash if d.id == one]
    entry.document_type = setup.state.document_types[0].id

    [row] = (await found(setup, TrashFilter())).entries

    assert row.document_type
    assert row.document_type.name == setup.state.document_types[0].name
    assert row.owner
    assert row.owner.name == "admin"
    assert (row.mime_type, row.created) == ("application/pdf", entry.created)


async def test_matches_are_paged_here() -> None:
    setup = Setup()
    numbers = [await setup.trashed(f"doc {n}") for n in range(5)]
    wanted = TrashFilter(title="doc")

    pages = [await found(setup, wanted, page=p, size=2) for p in (1, 2, 3)]

    assert [p.next_page for p in pages] == [2, 3, None]
    assert {p.count for p in pages} == {5}
    assert {p.scanned for p in pages} == {5}
    assert sorted(e.id for p in pages for e in p.entries) == numbers
    with pytest.raises(NotFound, match="Page 4 of the matching entries"):
        _ = await found(setup, wanted, page=4, size=2)
    assert (await found(setup, TrashFilter(title="none"))).count == 0


async def test_the_cap_is_told_and_ids_reach_beyond_it() -> None:
    setup = Setup()
    numbers = [await setup.trashed(f"doc {n}") for n in range(4)]
    wanted = TrashFilter(title="doc")

    exact = await capped(setup, 4).list(wanted, page=1, page_size=10)
    over = await capped(setup, 3).list(wanted, page=1, page_size=10)
    beyond = await capped(setup, 3).list(TrashFilter(ids=tuple(numbers)), page=1, page_size=10)

    assert (exact.count, exact.scanned, exact.truncated, exact.notice) == (4, 4, False, None)
    assert (over.count, over.scanned, over.truncated) == (3, 3, True)
    assert over.notice == (
        "Only 3 of the 4 entries were looked at; more may match. list_trash without a filter "
        "pages through all, and ids finds any."
    )
    assert (beyond.count, beyond.truncated, beyond.notice) == (4, False, None)


async def test_the_default_cap_is_a_thousand() -> None:
    setup = Setup()
    for number in range(1001):
        setup.state.trash.append(
            FakeDocument(id=5000 + number, title="many", owner=2, deleted_at=DAY)
        )
    wanted = TrashFilter(title="many")

    over = await found(setup, wanted)
    _ = setup.state.trash.pop()
    exact = await found(setup, wanted)

    assert (over.count, over.truncated) == (1000, True)
    assert (exact.count, exact.truncated) == (1000, False)


class Bounded(FakePaperless):
    """A trash whose answer is too large beyond `fits` entries, or that comes compressed."""

    fits: int = 10**9
    compressed: bool = False

    @override
    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        if self.compressed:
            message = "compressed"
            raise AnswerCompressed(message)
        if entries > self.fits:
            message = "too large"
            raise AnswerTooLarge(message)
        return await super().read_trash(entries=entries, max_bytes=max_bytes)


def bounded(setup: Setup, *, fits: int = 10**9, compressed: bool = False) -> Bounded:
    fake = Bounded(setup.state, user="admin", allow_writes=True)
    fake.fits, fake.compressed = fits, compressed
    return fake


async def test_a_trash_with_large_texts_is_looked_at_in_part_and_that_is_said() -> None:
    setup = Setup()
    for number in range(PAGE + 1):
        _ = await setup.trashed(f"doc {number}")

    some = await capped(setup, 1000, bounded(setup, fits=PAGE)).list(
        TrashFilter(title="doc"), page=1, page_size=10
    )

    assert (some.count, some.scanned, some.truncated) == (PAGE, PAGE, True)
    with pytest.raises(AnswerTooLarge) as raised:
        _ = await capped(setup, 1000, bounded(setup, fits=PAGE - 1)).list(
            TrashFilter(title="doc"), page=1, page_size=10
        )
    assert raised.value.hint == "List them page by page with list_trash, without a filter."


@pytest.mark.parametrize("how", ["too large", "compressed"])
async def test_ids_are_found_page_by_page_where_one_request_does_not_work(how: str) -> None:
    setup = Setup()
    one = await setup.trashed("Doc")
    fake = bounded(setup, fits=0) if how == "too large" else bounded(setup, compressed=True)

    result = await capped(setup, 1000, fake).restore([one])

    assert [e.outcome for e in result.entries] == ["restored"]


async def test_a_restricted_user_finds_owned_and_unowned_entries_only() -> None:
    setup = Setup(READER[0])
    _ = await setup.trashed("doc of admin", owner=2)
    shared = await setup.trashed("doc shared", owner=2)
    [entry] = [d for d in setup.state.trash if d.id == shared]
    entry.viewers.add(setup.fake.user.id)
    nobody = await setup.trashed("doc of nobody", owner=None)

    assert await ids(setup, TrashFilter(title="doc")) == [nobody]
    assert await ids(setup, TrashFilter(ids=(shared, nobody))) == [nobody]


# --- acting by a filter only shows ----------------------------------------------------------------


async def test_restoring_by_a_filter_shows_every_match_and_restores_nothing() -> None:
    setup = Setup(writes=False)
    older, newer = await setup.trashed("doc a"), await setup.trashed("doc b")
    deleted(setup, newer, DAY + timedelta(days=1))

    with pytest.raises(Validation) as raised:
        _ = await setup.service.restore(None, TrashFilter(title="doc"))

    assert raised.value.message == (
        "Nothing was restored. The filter matches 2 entries: "
        f"document {newer} 'doc b' (deleted 2026-11-11) and document {older} 'doc a' "
        "(deleted 2026-11-10)."
    )
    assert raised.value.hint == (
        "Show them to the user, then call again without filter and with "
        f"document_ids=[{newer}, {older}]."
    )
    assert sorted(setup.ids()) == [older, newer]


async def test_exactly_one_way_to_name_entries_is_taken() -> None:
    setup = Setup(writes=False)
    one = await setup.trashed("doc")

    for given in ((None, None), ([one], TrashFilter(title="doc"))):
        with pytest.raises(Validation) as raised:
            _ = await setup.service.restore(*given)
        assert raised.value.message == ONE_OF
    for named, wanted, everything in (
        ([one], TrashFilter(title="doc"), False),
        (None, TrashFilter(title="doc"), True),
        ([one], TrashFilter(title="doc"), True),
    ):
        with pytest.raises(Validation, match="exactly one of document_ids, filter and all=true"):
            _ = await setup.service.empty(named, wanted, everything=everything, confirm=True)


async def test_an_empty_filter_and_one_without_a_match_act_on_nothing() -> None:
    setup = Setup(writes=False)
    _ = await setup.trashed("doc")

    for wanted, said in (
        (TrashFilter(), NO_FILTER),
        (TrashFilter(ids=(4242,)), "matches no entry: nothing was %s. Not in the trash: 4242."),
    ):
        with pytest.raises(Validation) as restoring:
            _ = await setup.service.restore(None, wanted)
        with pytest.raises(Validation) as emptying:
            _ = await setup.service.empty(None, wanted, everything=False, confirm=True)
        assert said.replace("%s", "restored") in restoring.value.message
        assert said.replace("%s", "deleted") in emptying.value.message


@pytest.mark.parametrize("confirm", [False, True])
async def test_emptying_by_a_filter_asks_even_with_confirm_and_names_every_match(
    confirm: bool,  # noqa: FBT001 -- a parametrised flag, not a switch
) -> None:
    setup = Setup(writes=False)
    numbers = [await setup.trashed(f"doc {n:02d}") for n in range(12)]

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.empty(
            None, TrashFilter(title="doc"), everything=False, confirm=confirm
        )

    assert "Confirming would permanently delete 12 entries" in raised.value.message
    assert all(f"document {n} " in raised.value.message for n in numbers)
    assert raised.value.hint == (
        "Show them to the user; once they agree, call again without filter and with "
        f"document_ids={sorted(numbers, reverse=True)} and confirm=true. confirm=true does not "
        "count with a filter."
    )


async def test_an_accepted_filter_deletes_exactly_what_was_shown() -> None:
    setup = Setup()
    one = await setup.trashed("doc one")
    other = await setup.trashed("letter")
    wanted = TrashFilter(title="doc")

    with pytest.raises(ConfirmationRequired) as first:
        _ = await setup.service.empty(None, wanted, everything=False)
    with accepting(first.value.fingerprint):
        result = await setup.service.empty(None, wanted, everything=False)

    assert result.document_ids == (one,)
    assert setup.ids() == [other]


async def test_an_acceptance_does_not_cover_entries_that_came_since() -> None:
    setup = Setup()
    _ = await setup.trashed("doc one")
    wanted = TrashFilter(title="doc")
    with pytest.raises(ConfirmationRequired) as first:
        _ = await setup.service.empty(None, wanted, everything=False)
    _ = await setup.trashed("doc two")

    with accepting(first.value.fingerprint), pytest.raises(ConfirmationRequired) as second:
        _ = await setup.service.empty(None, wanted, everything=False)

    assert second.value.fingerprint != first.value.fingerprint
    assert len(setup.ids()) == 2


async def test_over_a_hundred_matches_the_newest_deletions_are_taken_and_the_rest_counted() -> None:
    setup = Setup()
    for number in range(MAX_IDS + 3):
        one = await setup.trashed(f"doc {number}")
        deleted(setup, one, DAY + timedelta(minutes=number))
    newest = sorted(setup.ids(), reverse=True)[:MAX_IDS]
    wanted = TrashFilter(title="doc")

    with pytest.raises(Validation) as restoring:
        _ = await setup.service.restore(None, wanted)
    with pytest.raises(ConfirmationRequired) as emptying:
        _ = await setup.service.empty(None, wanted, everything=False)

    assert "The filter matches 103 entries: " in restoring.value.message
    assert "permanently delete 100 entries" in emptying.value.message
    for raised in (restoring, emptying):
        assert "(3 more match; repeat for them afterwards)" in raised.value.message
        assert f"document_ids={newest}" in (raised.value.hint or "")


async def test_a_truncated_filter_says_that_more_may_match() -> None:
    setup = Setup(writes=False)
    for number in range(3):
        _ = await setup.trashed(f"doc {number}")
    service = capped(setup, 2)
    wanted = TrashFilter(title="doc")

    with pytest.raises(Validation, match="Only 2 of the 3 entries were looked at"):
        _ = await service.restore(None, wanted)
    with pytest.raises(ConfirmationRequired, match="Only 2 of the 3 entries were looked at"):
        _ = await service.empty(None, wanted, everything=False)


# --- what blocks a call ---------------------------------------------------------------------------


async def test_every_id_that_blocks_is_named_with_the_reason_paperless_lets_it_know() -> None:
    setup = Setup(writes=False)
    alive = setup.document("Alive")
    document, version = await setup.version("Report")
    await setup.writer.delete_document(document)
    lone, live_version = await setup.version("Other")
    [entry] = [d for d in setup.state.trash if d.id == live_version]
    setup.state.trash.remove(entry)  # listed for nobody below
    [live] = [d for d in setup.state.documents if d.id == lone]
    live.versions.insert(0, entry.versions[0])

    with pytest.raises(NotFound) as raised:
        _ = await setup.service.restore([alive, version, live_version, 4242])

    assert raised.value.message == f"Not in the trash: {alive}, {version}, {live_version}, 4242."
    assert raised.value.hint == (
        f"{alive} is not deleted. {version} is a version of document {document}, which is in the "
        f"trash itself: use {document}. {live_version} is not deleted. {NOT_LISTED_HINT}"
    )


async def test_an_entry_only_shared_with_the_user_is_told_as_the_owners() -> None:
    setup = Setup(READER[0])
    shared = await setup.trashed("shared")
    [entry] = [d for d in setup.state.trash if d.id == shared]
    entry.viewers.add(setup.fake.user.id)
    document, version = await setup.version("shared too")
    [live] = [d for d in setup.state.documents if d.id == document]
    live.viewers.add(setup.fake.user.id)
    for stub in setup.state.trash:
        stub.viewers.add(setup.fake.user.id)

    with pytest.raises(NotFound) as raised:
        _ = await setup.service.empty([shared, version], everything=False, confirm=True)

    assert raised.value.hint == (
        f"{shared} is in the trash, but listed only for its owner. {version} is a deleted version "
        f"of document {document}, but not listed for this user. {NOT_LISTED_HINT}"
    )


async def test_reasons_are_looked_up_for_the_first_few_ids_only() -> None:
    setup = Setup(writes=False)
    alive = [setup.document(f"alive {n}") for n in range(EXPLAINED + 1)]

    with pytest.raises(NotFound) as raised:
        _ = await setup.service.restore(alive)

    assert f"{alive[EXPLAINED - 1]} is not deleted" in (raised.value.hint or "")
    assert f"{alive[EXPLAINED]} is not deleted" not in (raised.value.hint or "")
    assert str(alive[EXPLAINED]) in raised.value.message


class Moody(FakePaperless):
    """Fails as told: the reads after the write, the single reads, or the write itself."""

    after: UpstreamUnreachable | None = None
    single: UpstreamUnreachable | None = None
    refuse: Validation | PermissionDenied | UpstreamUnreachable | None = None
    takes: tuple[int, ...] = ()  # leave the trash although the write fails
    written: bool = False

    @override
    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        if self.written and self.after:
            raise self.after
        return await super().read_trash(entries=entries, max_bytes=max_bytes)

    @override
    async def get_root(self, document_id: int) -> int:
        if self.single:
            raise self.single
        return await super().get_root(document_id)

    async def _failing(self) -> None:
        self.written = True
        if self.refuse is None:
            return
        if self.takes:
            await super().restore_from_trash(list(self.takes))
        raise self.refuse

    @override
    async def restore_from_trash(self, document_ids: Sequence[int]) -> None:
        await self._failing()
        await super().restore_from_trash(document_ids)

    @override
    async def empty_trash(self, document_ids: Sequence[int]) -> None:
        await self._failing()
        await super().empty_trash(document_ids)


def moody(setup: Setup) -> Moody:
    return Moody(setup.state, user="admin", allow_writes=True)


OUTAGE = UpstreamUnreachable("Paperless could not be reached.")


async def test_an_id_that_vanished_before_a_refusal_is_named() -> None:
    setup = Setup()
    one, two = await setup.trashed("one"), await setup.trashed("two")
    fake = moody(setup)
    fake.refuse, fake.takes = Validation("Paperless rejected the request.", hint="h"), (one,)

    with pytest.raises(Validation) as raised:
        _ = await setup.on(fake).restore([one, two])

    assert raised.value.message == (
        f"Paperless rejected the request. No longer in the trash: {one}."
    )
    assert raised.value.hint == "h"


async def test_a_refusal_with_every_id_still_listed_says_that_none_is_named() -> None:
    setup = Setup()
    one = await setup.trashed("one")
    fake = moody(setup)
    fake.refuse = PermissionDenied("You may not.")

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.on(fake).empty([one], everything=False, confirm=True)
    assert raised.value.message == f"You may not. {NO_ID}"

    fake.after, fake.written = OUTAGE, False
    with pytest.raises(PermissionDenied, match="Which id it refuses is unknown"):
        _ = await setup.on(fake).empty([one], everything=False, confirm=True)


# --- what became of each entry --------------------------------------------------------------------


async def test_an_emptying_that_paperless_swallowed_is_seen() -> None:
    setup = Setup()
    gone, stuck = await setup.trashed("gone"), await setup.trashed("stuck")
    setup.state.stuck = {stuck}

    result = await setup.service.empty([gone, stuck], everything=False, confirm=True)
    alone = await setup.service.empty([stuck], everything=False, confirm=True)
    everything = await setup.service.empty(None, everything=True, confirm=True)

    assert [(e.id, e.outcome) for e in result.entries] == [
        (gone, "deleted"),
        (stuck, "still_in_trash"),
    ]
    assert (result.document_ids, result.count) == ((gone,), 1)
    assert result.message == (
        f"Permanently deleted 1 entry from the trash: document {gone} 'gone'. "
        f"Still in the trash: document {stuck} 'stuck'. {CHECKED}"
    )
    for nothing in (alone, everything):
        assert (nothing.document_ids, nothing.count) == ((), 0)
        assert nothing.message.startswith("No entry was seen to be deleted. Still in the trash: ")


async def test_a_restore_that_failed_half_way_says_what_came_back() -> None:
    setup = Setup()
    back, left = await setup.trashed("back"), await setup.trashed("left")
    fake = moody(setup)
    fake.refuse, fake.takes = OUTAGE, (back,)

    result = await setup.on(fake).restore([back, left])

    assert [e.outcome for e in result.entries] == ["restored", "still_in_trash"]
    assert result.message == (
        f"Restored 1 document from the trash: document {back} 'back'. "
        f"Still in the trash: document {left} 'left'. {CHECKED}"
    )


async def test_a_request_that_failed_without_a_trace_is_the_error() -> None:
    setup = Setup()
    one = await setup.trashed("one")
    fake = moody(setup)
    fake.refuse = OUTAGE

    with pytest.raises(UpstreamUnreachable):
        _ = await setup.on(fake).restore([one])


async def test_a_restore_without_an_effect_is_not_called_restored() -> None:
    setup = Setup()
    one = await setup.trashed("one")
    setup.state.stuck = {one}

    result = await setup.service.restore([one])

    assert [e.outcome for e in result.entries] == ["still_in_trash"]
    assert result.message == (
        f"No entry was seen to be restored. Still in the trash: document {one} 'one'. {CHECKED}"
    )


async def test_a_failed_look_afterwards_never_hides_what_was_done() -> None:
    setup = Setup()
    one, two = await setup.trashed("one"), await setup.trashed("two")
    fake = moody(setup)
    fake.after = OUTAGE

    restored = await setup.on(fake).restore([one])
    fake.written = False
    emptied = await setup.on(fake).empty([two], everything=False, confirm=True)

    assert [e.outcome for e in (*restored.entries, *emptied.entries)] == ["unverified"] * 2
    assert f"Could not be verified: document {one} 'one'. {CHECKED}" in restored.message
    assert (emptied.count, emptied.document_ids) == (0, ())


@pytest.mark.parametrize("how", ["truncated", "too large", "compressed"])
async def test_entries_beyond_one_request_are_checked_one_by_one(how: str) -> None:
    setup = Setup()
    for number in range(3):
        _ = await setup.trashed(f"rest {number}")
    document, version = await setup.version("Report")
    back, gone, stuck = [await setup.trashed(t) for t in ("back", "gone", "stuck")]
    setup.state.stuck = {stuck}
    fake = {
        "truncated": bounded(setup),
        "too large": bounded(setup, fits=0),
        "compressed": bounded(setup, compressed=True),
    }[how]
    for entry in setup.state.trash:  # the stuck one is beyond the cap
        entry.created = date(2026, 1, 1) if entry.id == stuck else date(2026, 6, 1)
    service = capped(setup, 1, fake)

    restored = await service.restore([back, version])
    emptied = await service.empty([gone, stuck], everything=False, confirm=True)

    assert [e.outcome for e in restored.entries] == ["restored", "restored"]
    assert document in setup.alive()
    assert [e.outcome for e in emptied.entries] == ["deleted", "still_in_trash"]


async def test_one_by_one_a_restored_entry_has_to_be_seen() -> None:
    setup = Setup()
    for number in range(3):
        _ = await setup.trashed(f"rest {number}")
    _, version = await setup.version("Report")
    document = await setup.trashed("stuck")
    setup.state.stuck = {version, document}
    for entry in setup.state.trash:
        if entry.id in setup.state.stuck:
            entry.created = date(2026, 1, 1)

    result = await capped(setup, 1, bounded(setup)).restore([version, document])

    assert [e.outcome for e in result.entries] == ["unverified", "unverified"]


async def test_one_by_one_an_outage_ends_the_checks() -> None:
    setup = Setup()
    numbers = [await setup.trashed(f"doc {n}") for n in range(4)]
    fake = moody(setup)
    fake.single = OUTAGE
    scan = TrashScan(fake, fake, setup.taxonomy, cap=1)
    service = TrashService(fake, fake, fake, scan, setup.taxonomy, web_url=WEB)

    result = await service.empty(numbers[:2], everything=False, confirm=True)

    assert {e.outcome for e in result.entries} == {"unverified"}
    assert list(itertools.islice(setup.ids(), 3)) == numbers[2:]


async def test_a_lookup_that_fails_leaves_the_reason_out() -> None:
    setup = Setup()
    alive = setup.document("Alive")
    fake = moody(setup)
    fake.single = OUTAGE

    with pytest.raises(NotFound) as raised:
        _ = await setup.on(fake).restore([alive])

    assert raised.value.hint == NOT_LISTED_HINT
