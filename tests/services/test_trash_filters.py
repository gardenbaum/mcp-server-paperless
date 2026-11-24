"""Filters of the trash: what they match, the cap, and that acting by one only shows (PLMCP-78).

The fakes of the filter refusals are created without `allow_writes`: any write would raise.
"""

import asyncio
import itertools
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
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
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
    accepting,
)
from mcp_server_paperless.domain.trash import MAX_IDS, TrashFilter, TrashLookup
from mcp_server_paperless.services.documents import DocumentService
from mcp_server_paperless.services.trash import (
    AT_ONCE,
    CHECKED,
    NO_FILTER,
    NO_ID,
    NOT_ALL,
    ONE_OF,
    TrashService,
)
from mcp_server_paperless.services.trash_scan import (
    EXPLAINED,
    MAX_PAGES,
    NOT_LISTED_HINT,
    PAGE,
    SKIPPED,
    STOPPED,
    TOO_LARGE,
    TrashScan,
)
from mcp_server_paperless.services.trashed_documents import TrashedDocuments
from services.test_trash import DAY, WEB, Setup

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Sequence

    from mcp_server_paperless.domain.documents import DocumentPage, DocumentQuery, DocumentRecord
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
    assert both.notice == "Not found in the trash: 4242."
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
    assert (await found(setup, wanted, page=1, size=5)).next_page is None


class Repeating(FakePaperless):
    @override
    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        page = await super().read_trash(entries=entries, max_bytes=max_bytes)
        return page.model_copy(update={"entries": page.entries * 2})


async def test_a_row_paperless_repeats_counts_once() -> None:
    setup = Setup()
    one = await setup.trashed("doc")

    listed = await capped(setup, 10, Repeating(setup.state, user="admin")).list(
        TrashFilter(title="doc"), page=1, page_size=10
    )

    assert ([e.id for e in listed.entries], listed.count, listed.scanned) == ([one], 1, 1)


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
        "pages through the trash, and ids also looks beyond them."
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
        (
            TrashFilter(ids=(4242,)),
            "matches no entry: nothing was %s. Not found in the trash: 4242.",
        ),
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
    setup.state.documents[-1].versions = []  # the original upload is always there
    document, version = await setup.version("Report")
    await setup.writer.delete_document(document)
    lone, live_version = await setup.version("Other")
    [entry] = [d for d in setup.state.trash if d.id == live_version]
    setup.state.trash.remove(entry)  # listed for nobody below
    [live] = [d for d in setup.state.documents if d.id == lone]
    live.versions.insert(0, entry.versions[0])

    with pytest.raises(NotFound) as raised:
        _ = await setup.service.restore([alive, version, live_version, 4242])

    assert (
        raised.value.message == f"Not found in the trash: {alive}, {version}, {live_version}, 4242."
    )
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
        f"{shared} is in the trash, but was not found in the list: that may be because it is "
        f"listed for its owner only. {version} is a deleted version of document {document}, but "
        f"was not found in the list: that may be because it is listed for its owner only. "
        f"{NOT_LISTED_HINT}"
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

    after: UpstreamUnavailable | PermissionDenied | None = None
    single: UpstreamUnreachable | None = None
    refuse: Validation | PermissionDenied | UpstreamUnreachable | None = None
    takes: tuple[int, ...] = ()  # leave the trash although the write fails
    written: bool = False

    @override
    async def list_trash(self, *, page: int, page_size: int, max_bytes: int) -> TrashPage:
        # read_trash, too
        if self.written and self.after:
            raise self.after
        return await super().list_trash(page=page, page_size=page_size, max_bytes=max_bytes)

    @override
    async def get_root(self, document_id: int) -> int:
        if self.single:
            raise self.single
        return await super().get_root(document_id)

    @override
    async def document_exists(self, document_id: int) -> bool:
        if self.single:
            raise self.single
        return await super().document_exists(document_id)

    @override
    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        if self.single:
            raise self.single
        return await super().get_document_titles(document_ids)

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

    assert raised.value.message == (f"Paperless rejected the request. No longer listed: {one}.")
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
        f"Still in the trash: document {left} 'left'. {CHECKED} Paperless reported: "
        "Paperless could not be reached."
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
    fake.after = fake.single = OUTAGE

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

    assert [e.outcome for e in result.entries] == ["still_in_trash", "still_in_trash"]


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


async def test_looking_up_one_by_one_ends_after_its_time() -> None:
    setup = Setup()
    ids = [await setup.trashed(f"doc {number}") for number in range(AT_ONCE)]

    class Slow(FakePaperless):
        looks: int = 0

        @override
        async def document_exists(self, document_id: int) -> bool:
            self.looks += 1
            if document_id == ids[4]:
                await asyncio.sleep(30)
            return await super().document_exists(document_id)

    fake = Slow(setup.state, user="admin", allow_writes=True)
    scan = TrashScan(fake, fake, setup.taxonomy)
    service = TrashService(fake, fake, fake, scan, setup.taxonomy, web_url=WEB, check_seconds=0.05)

    result = await service.empty(None, everything=True, confirm=True)

    # What was answered meanwhile is kept.
    assert fake.looks == AT_ONCE
    assert {e.id: e.outcome for e in result.entries if e.outcome != "deleted"} == {
        ids[4]: "unverified"
    }


class Endless(FakePaperless):
    """Always names a next page: the same one, or the one after, with or without entries."""

    next_is: str = "same"
    empty: bool = False
    pages: int = 0

    def _next(self, page: int) -> int:
        self.pages += 1
        assert self.pages <= MAX_PAGES + 1, "read on without end"
        return page if self.next_is == "same" else page + 1

    @override
    async def list_trash(self, *, page: int, page_size: int, max_bytes: int) -> TrashPage:
        found = await super().list_trash(page=1, page_size=page_size, max_bytes=max_bytes)
        entries = () if self.empty else found.entries
        return found.model_copy(update={"entries": entries, "next_page": self._next(page)})

    @override
    async def look_up_in_trash(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> TrashLookup:
        return TrashLookup(entry=None, next_page=self._next(page))


@pytest.mark.parametrize(
    ("next_is", "empty", "listed", "looked"),  # the one request before the pages counts, too
    [
        ("same", False, 2, 2),
        ("after", True, 2, MAX_PAGES + 1),
        ("after", False, MAX_PAGES + 1, MAX_PAGES + 1),
    ],
)
async def test_an_answer_that_always_names_a_next_page_ends(
    next_is: str,
    empty: bool,  # noqa: FBT001 -- a parametrised flag, not a switch
    listed: int,
    looked: int,
) -> None:
    setup = Setup()
    for number in range(2):
        _ = await setup.trashed(f"doc {number}")
    fake = Endless(setup.state, user="admin")
    fake.next_is, fake.empty = next_is, empty
    scan = TrashScan(fake, fake, setup.taxonomy, cap=1)
    documents = DocumentService(fake, fake, setup.taxonomy, web_url=WEB)
    looking = TrashedDocuments(fake, fake, fake, documents, scan, web_url=WEB, max_bytes=10)

    found, _ = await scan.locate([4242])
    pages, fake.pages = fake.pages, 0
    with pytest.raises(NotFound):
        _ = await looking.entry(4242, max_chars=1)

    assert (found, pages, fake.pages) == ({}, listed, looked)


# --- findings of the review -----------------------------------------------------------------------

EAST = timezone(timedelta(hours=1))


async def test_the_day_of_a_deletion_is_the_utc_day_whatever_zone_paperless_answers_in() -> None:
    setup = Setup()
    one = await setup.trashed("doc")
    deleted(setup, one, datetime(2026, 11, 10, 0, 30, tzinfo=EAST))
    ninth, tenth = date(2026, 11, 9), date(2026, 11, 10)

    assert await ids(setup, TrashFilter(deleted_from=ninth, deleted_to=ninth)) == [one]
    assert await ids(setup, TrashFilter(deleted_from=tenth)) == []
    with pytest.raises(ConfirmationRequired, match="deleted 2026-11-09"):
        _ = await setup.service.empty([one], everything=False)


TRANSIENT = [
    UpstreamTimeout("Paperless did not answer in time."),
    UpstreamUnavailable("Paperless is temporarily unavailable."),
    UpstreamUnreachable("Paperless could not be reached."),
]


class Straining(FakePaperless):
    """Reads of more than `fits` entries fail with `error`."""

    error: Exception = TRANSIENT[0]
    fits: int = 0

    @override
    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        if entries > self.fits:
            raise self.error
        return await super().read_trash(entries=entries, max_bytes=max_bytes)


def straining(setup: Setup, error: Exception, *, fits: int = 0) -> Straining:
    fake = Straining(setup.state, user="admin", allow_writes=True)
    fake.error, fake.fits = error, fits
    return fake


@pytest.mark.parametrize("error", TRANSIENT)
async def test_a_large_request_that_fails_for_now_falls_back_to_small_ones(
    error: Exception,
) -> None:
    setup = Setup()
    back, gone = await setup.trashed("back"), await setup.trashed("gone")
    for number in range(PAGE):
        _ = await setup.trashed(f"doc {number}")
    service = capped(setup, 1000, straining(setup, error))

    listed = await service.list(TrashFilter(ids=(back,)), page=1, page_size=5)
    restored = await service.restore([back])
    emptied = await service.empty([gone], everything=False, confirm=True)
    some = await capped(setup, 1000, straining(setup, error, fits=PAGE)).list(
        TrashFilter(title="doc"), page=1, page_size=5
    )

    assert [e.id for e in listed.entries] == [back]
    assert [e.outcome for e in restored.entries] == ["restored"]
    assert [e.outcome for e in emptied.entries] == ["deleted"]
    assert (some.scanned, some.truncated) == (PAGE, False)
    with pytest.raises(type(error)):
        _ = await service.list(TrashFilter(title="doc"), page=1, page_size=5)


async def test_a_missing_permission_is_not_tried_again_in_smaller_requests() -> None:
    setup = Setup()
    one = await setup.trashed("doc")
    denied = PermissionDenied("The Paperless user may not perform this action.")

    with pytest.raises(PermissionDenied):
        _ = await capped(setup, 1000, straining(setup, denied, fits=PAGE)).list(
            TrashFilter(title="doc"), page=1, page_size=5
        )
    with pytest.raises(PermissionDenied):
        _ = await capped(setup, 1000, straining(setup, denied)).restore([one])


async def test_ids_that_pages_may_have_skipped_are_not_said_to_be_missing_for_sure() -> None:
    setup = Setup()
    one = await setup.trashed("doc")
    _ = await setup.trashed("other")

    partial = await capped(setup, 1).list(TrashFilter(ids=(one, 4242)), page=1, page_size=5)
    whole = await setup.service.list(TrashFilter(ids=(one, 4242)), page=1, page_size=5)

    assert partial.truncated is True
    assert partial.notice == (
        "Not found in the trash: 4242. The trash could not be read at once, and its pages can "
        "skip entries."
    )
    assert (whole.truncated, whole.notice) == (False, "Not found in the trash: 4242.")


async def test_a_version_is_listed_and_found_with_the_title_and_type_its_document_has_now() -> None:
    setup = Setup()
    invoice = setup.state.document_types[0]
    document, version = await setup.version("Old title")
    [live] = [d for d in setup.state.documents if d.id == document]
    live.title, live.document_type = "New title", invoice.id

    [row] = (await found(setup, TrashFilter())).entries
    assert (row.title, row.document_type and row.document_type.id) == ("New title", invoice.id)
    assert await ids(setup, TrashFilter(title="new title")) == [version]
    assert await ids(setup, TrashFilter(title="old title")) == []
    with pytest.raises(ConfirmationRequired, match="'New title'"):
        _ = await setup.service.empty([version], everything=False)
    with pytest.raises(ConfirmationRequired, match="'New title'"):
        _ = await setup.service.empty(None, everything=True)


class NoSearch(FakePaperless):
    @override
    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        raise OUTAGE


async def test_acting_on_versions_does_not_need_the_title_of_their_document() -> None:
    setup = Setup()
    _, version = await setup.version("Report")
    fake = NoSearch(setup.state, user="admin", allow_writes=True)

    assert (await setup.on(fake).restore([version])).entries[0].title == "Report"


async def test_a_document_that_answers_is_never_called_deleted() -> None:
    """Someone restored it at the same moment: it left the trash, but is not gone."""
    setup = Setup()
    one = await setup.trashed("doc")
    _, version = await setup.version("Report")

    class Raced(FakePaperless):
        @override
        async def empty_trash(self, document_ids: Sequence[int]) -> None:
            await self.restore_from_trash(document_ids)

    raced = Raced(setup.state, user="admin", allow_writes=True)
    result = await setup.on(raced).empty([one, version], everything=False, confirm=True)

    assert [e.outcome for e in result.entries] == ["unverified", "unverified"]
    assert (result.count, result.document_ids) == (0, ())


async def test_what_left_the_trash_but_is_not_seen_again_is_not_called_restored() -> None:
    setup = Setup()
    one = await setup.trashed("doc")
    _, version = await setup.version("Report")

    class Raced(FakePaperless):
        @override
        async def restore_from_trash(self, document_ids: Sequence[int]) -> None:
            await self.empty_trash(document_ids)

    raced = Raced(setup.state, user="admin", allow_writes=True)
    result = await setup.on(raced).restore([one, version])

    assert [e.outcome for e in result.entries] == ["unverified", "unverified"]


async def test_a_version_covered_by_its_document_in_the_trash_is_still_there() -> None:
    setup = Setup()
    document, version = await setup.version("Report")

    class Raced(FakePaperless):
        @override
        async def empty_trash(self, document_ids: Sequence[int]) -> None:
            await self.delete_document(document)

    raced = Raced(setup.state, user="admin", allow_writes=True)
    result = await setup.on(raced).empty([version], everything=False, confirm=True)

    assert [e.outcome for e in result.entries] == ["still_in_trash"]


async def test_a_document_that_cannot_be_read_leaves_the_reason_out() -> None:
    setup = Setup()
    alive = setup.document("Alive")

    class NoDocument(FakePaperless):
        @override
        async def get_document(self, document_id: int) -> DocumentRecord:
            raise OUTAGE

    with pytest.raises(NotFound) as raised:
        _ = await setup.on(NoDocument(setup.state, user="admin")).restore([alive])

    assert raised.value.hint == NOT_LISTED_HINT


async def test_the_list_works_while_the_titles_of_documents_cannot_be_looked_up() -> None:
    setup = Setup()
    _, version = await setup.version("Report")
    service = setup.on(NoSearch(setup.state, user="admin"))

    for wanted in (
        TrashFilter(),
        TrashFilter(kind="version"),
        TrashFilter(deleted_from=DAY.date()),
        TrashFilter(ids=(version,)),
    ):
        listed = await service.list(wanted, page=1, page_size=5)
        assert [(e.id, e.title) for e in listed.entries] == [(version, "Report")]
    for wanted in (
        TrashFilter(title="Report"),
        TrashFilter(document_type=setup.state.document_types[0].id),
    ):
        with pytest.raises(UpstreamUnreachable):
            _ = await service.list(wanted, page=1, page_size=5)


class Counting(Straining):
    sizes: tuple[int, ...] = ()

    @override
    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        self.sizes = (*self.sizes, entries)
        return await super().read_trash(entries=entries, max_bytes=max_bytes)


async def test_a_large_request_that_failed_is_not_made_again_after_the_write() -> None:
    setup = Setup()
    numbers = [await setup.trashed(f"doc {n}") for n in range(4)]
    acts: list[tuple[int, Callable[[TrashService], Awaitable[object]]]] = [
        (PAGE, lambda s: s.restore([numbers[0]])),
        (0, lambda s: s.restore([numbers[1]])),
        (PAGE, lambda s: s.empty([numbers[2]], everything=False, confirm=True)),
    ]
    for fits, act in acts:
        fake = Counting(setup.state, user="admin", allow_writes=True)
        fake.fits = fits
        _ = await act(capped(setup, 1000, fake))
        assert fake.sizes == (1000, PAGE, PAGE)


async def test_too_many_requests_do_not_lead_to_more() -> None:
    setup = Setup()
    one = await setup.trashed("doc")
    limited = UpstreamUnavailable("Paperless is temporarily unavailable.", status=429)

    calls: list[Callable[[TrashService], Awaitable[object]]] = [
        lambda s: s.restore([one]),
        lambda s: s.list(TrashFilter(title="doc"), page=1, page_size=5),
    ]
    for call in calls:
        fake = Counting(setup.state, user="admin")
        fake.error = limited
        with pytest.raises(UpstreamUnavailable):
            _ = await call(capped(setup, 1000, fake))
        assert fake.sizes == (1000,)


async def test_too_many_requests_after_the_write_leave_it_unverified() -> None:
    setup = Setup()
    one = await setup.trashed("doc")
    fake = moody(setup)
    fake.after = UpstreamUnavailable("Paperless is temporarily unavailable.", status=429)

    result = await setup.on(fake).restore([one])

    assert [e.outcome for e in result.entries] == ["unverified"]


async def test_an_entry_that_left_the_list_but_still_answers_is_not_called_deleted() -> None:
    """Its owner changed at the same moment: it is in the trash, but listed for someone else."""
    setup = Setup()
    one = await setup.trashed("doc")
    document, version = await setup.version("Report")
    await setup.writer.delete_document(document)
    stay = {one, document, version}

    class Hidden(FakePaperless):
        @override
        async def empty_trash(self, document_ids: Sequence[int]) -> None:
            self.state.stuck = stay

        @override
        async def list_trash(self, *, page: int, page_size: int, max_bytes: int) -> TrashPage:
            found = await super().list_trash(page=page, page_size=page_size, max_bytes=max_bytes)
            if not self.state.stuck:
                return found
            return found.model_copy(update={"entries": (), "count": 0})

    hidden = Hidden(setup.state, user="admin", allow_writes=True)
    result = await setup.on(hidden).empty([one], everything=False, confirm=True)

    assert [e.outcome for e in result.entries] == ["still_in_trash"]


async def test_one_lookup_that_fails_leaves_that_entry_unverified() -> None:
    setup = Setup()
    first, second = await setup.trashed("first"), await setup.trashed("second")

    class NoRoot(FakePaperless):
        @override
        async def document_exists(self, document_id: int) -> bool:
            if document_id == first:
                raise OUTAGE
            return await super().document_exists(document_id)

    fake = NoRoot(setup.state, user="admin", allow_writes=True)
    result = await setup.on(fake).empty([first, second], everything=False, confirm=True)

    assert [e.outcome for e in result.entries] == ["unverified", "deleted"]


async def test_an_entry_whose_owner_changed_meanwhile_is_not_called_deleted() -> None:
    """Paperless swallowed the emptying, and the entry is hidden from this user now (403)."""
    setup = Setup()
    one = await setup.trashed("doc", owner=None)
    deleter = replace(setup.fake.user, id=77, username="deleter", is_superuser=False)
    setup.state.users.append(
        replace(deleter, permissions=frozenset({"view_document", "delete_document"}))
    )

    class Raced(FakePaperless):
        @override
        async def empty_trash(self, document_ids: Sequence[int]) -> None:
            for entry in self.state.trash:
                entry.owner = 2

    raced = Raced(setup.state, user="deleter", allow_writes=True)
    result = await setup.on(raced).empty([one], everything=False, confirm=True)

    assert [e.outcome for e in result.entries] == ["unverified"]
    assert (result.count, setup.ids()) == (0, [one])


async def test_a_permission_lost_after_the_write_leaves_it_unverified_without_more_requests() -> (
    None
):
    """The change is done and reported, but the trash may not be read any more."""
    setup = Setup()
    back, gone = await setup.trashed("back"), await setup.trashed("gone")
    fake = moody(setup)
    fake.after = PermissionDenied("The Paperless user may not perform this action.")

    restored = await setup.on(fake).restore([back])
    fake.written = False
    emptied = await setup.on(fake).empty([gone], everything=False, confirm=True)

    # The single lookups, which would answer here, are not made after a refusal.
    assert [e.outcome for e in (*restored.entries, *emptied.entries)] == ["unverified"] * 2
    assert f"Could not be verified: document {back} 'back'." in restored.message
    assert emptied.count == 0
    assert back in setup.alive()
    assert gone not in setup.ids()


# --- pages that are too large (decision-77) -------------------------------------------------------


class Heavy(FakePaperless):
    """A trash whose entries weigh 1 each, or too much; more than `most` is too large at once."""

    most: int = 10**9
    huge: tuple[int, ...] = ()
    asked: tuple[tuple[int, int], ...] = ()

    @override
    async def list_trash(self, *, page: int, page_size: int, max_bytes: int) -> TrashPage:
        assert max_bytes == 64 * 1024 * 1024
        self.asked = (*self.asked, (page, page_size))
        found = await super().list_trash(page=page, page_size=page_size, max_bytes=max_bytes)
        if sum(10**6 if e.id in self.huge else 1 for e in found.entries) > self.most:
            message = "Paperless sent too many bytes for GET /api/trash/."
            raise AnswerTooLarge(message)
        return found


def heavy(setup: Setup, most: int, *huge: int) -> Heavy:
    fake = Heavy(setup.state, user="admin", allow_writes=True)
    fake.most, fake.huge = most, huge
    return fake


async def many(setup: Setup, count: int) -> list[int]:
    """Entries in the order of the trash."""
    made = [await setup.trashed(f"doc {n}") for n in range(count)]
    return made[::-1]


async def test_ids_are_found_in_smaller_pages_where_larger_ones_are_too_large() -> None:
    setup = Setup()
    numbers = await many(setup, 125)
    fake = heavy(setup, 9, numbers[12])

    result = await capped(setup, 1000, fake).restore([numbers[21], numbers[120]])

    assert [e.outcome for e in result.entries] == ["restored", "restored"]


async def test_after_a_page_that_is_too_large_the_next_ones_are_large_again() -> None:
    setup = Setup()
    numbers = await many(setup, 205)
    fake = heavy(setup, 100, numbers[3])

    found, _ = await TrashScan(fake, fake, setup.taxonomy).locate([numbers[204]])

    assert found.keys() == {numbers[204]}
    ones = tuple((n, 1) for n in range(1, 11))
    tens = tuple((n, 10) for n in range(2, 11))
    assert fake.asked[2:] == ((1, 100), (1, 10), *ones, *tens, (2, 100), (3, 100))


async def test_a_page_grows_back_at_once_at_the_end_of_a_block() -> None:
    setup = Setup()
    numbers = await many(setup, 205)
    fake = heavy(setup, 100, numbers[99])

    _ = await TrashScan(fake, fake, setup.taxonomy).locate([numbers[204]])

    ones = tuple((n, 1) for n in range(91, 101))
    tens = tuple((n, 10) for n in range(1, 11))
    assert fake.asked[2:] == ((1, 100), *tens, *ones, (2, 100), (3, 100))


async def test_pages_are_only_read_for_what_is_missing() -> None:
    setup = Setup()
    numbers = await many(setup, 205)

    whole, some, early = heavy(setup, 10**9), heavy(setup, 10**9), heavy(setup, 10**9)
    _ = await TrashScan(whole, whole, setup.taxonomy).locate([4242])
    _ = await TrashScan(some, some, setup.taxonomy, cap=10).locate([numbers[3]])
    _ = await TrashScan(early, early, setup.taxonomy, cap=10).locate([numbers[120]])

    assert whole.asked == ((1, 1000),)
    assert some.asked == ((1, 10),)
    assert early.asked == ((1, 10), (1, 100), (2, 100))


@pytest.mark.parametrize("at", [0, 4])  # the first and the last one
async def test_an_entry_that_is_too_large_alone_hides_neither_the_rest_nor_the_reasons(
    at: int,
) -> None:
    setup = Setup()
    numbers = await many(setup, 5)
    alive = setup.document("alive")
    fake = heavy(setup, 100, numbers[at])
    service = capped(setup, 1000, fake)

    with pytest.raises(NotFound) as raised:
        _ = await service.restore([numbers[2], numbers[at], alive])
    shown = await service.list(TrashFilter(ids=(numbers[2], numbers[at])), page=1, page_size=5)
    found = await service.restore([numbers[2]])

    assert raised.value.message == f"Not found in the trash: {numbers[at]}, {alive}."
    assert raised.value.hint == (
        f"{numbers[at]} is in the trash, but was not found in the list: that may be because it is "
        f"listed for its owner only. {alive} is not deleted. {TOO_LARGE} {SKIPPED} "
        f"{NOT_LISTED_HINT}"
    )
    assert TOO_LARGE == (
        "An entry over the limit of 64 MiB was not read, so it may be one of these: look in the "
        "Paperless web UI."
    )
    assert [e.id for e in shown.entries] == [numbers[2]]
    assert shown.notice == f"Not found in the trash: {numbers[at]}. {TOO_LARGE} {SKIPPED}"
    assert [e.outcome for e in found.entries] == ["restored"]


async def test_a_refused_id_beside_an_entry_that_is_too_large_is_not_called_gone() -> None:
    setup = Setup()
    numbers = await many(setup, 3)

    class Grown(Heavy):
        @override
        async def restore_from_trash(self, document_ids: Sequence[int]) -> None:
            self.most: int = 100
            self.huge: tuple[int, ...] = (numbers[0],)
            message = "Paperless refused."
            raise Validation(message)

    fake = Grown(setup.state, user="admin", allow_writes=True)

    with pytest.raises(Validation, match="Which id it refuses is unknown"):
        _ = await capped(setup, 1000, fake).restore([numbers[0]])


async def test_the_pages_search_makes_a_thousand_requests_at_most() -> None:
    setup = Setup()
    _ = await many(setup, 3)

    class Wide(Heavy):
        @override
        async def list_trash(self, *, page: int, page_size: int, max_bytes: int) -> TrashPage:
            found = await super().list_trash(page=1, page_size=page_size, max_bytes=max_bytes)
            if page_size > 1:
                message = "too large"
                raise AnswerTooLarge(message)
            return found.model_copy(update={"next_page": page + 1})

    fake = Wide(setup.state, user="admin")
    scan = TrashScan(fake, fake, setup.taxonomy)

    with pytest.raises(NotFound) as raised:
        _ = await scan.require([4242])

    assert len(fake.asked) == 2 + 1000
    assert STOPPED in (raised.value.hint or "")
    assert STOPPED == "The trash was looked through in 1000 requests and is longer."


async def test_a_page_of_the_list_that_is_too_large_says_how_to_go_on() -> None:
    setup = Setup()
    numbers = await many(setup, 3)
    service = capped(setup, 1000, heavy(setup, 1, numbers[0]))

    for size, hint in (
        (2, "List fewer entries at once with a smaller page_size."),
        (1, "This entry is too large on its own; go on with the next page."),
    ):
        with pytest.raises(AnswerTooLarge) as raised:
            _ = await service.list(TrashFilter(), page=1, page_size=size)
        assert raised.value.message == (
            "The entries of this page are too large to be read at once (over 64 MiB)."
        )
        assert raised.value.hint == hint
    second = await service.list(TrashFilter(), page=2, page_size=1)
    assert [e.id for e in second.entries] == [numbers[1]]


async def test_everything_is_emptied_in_pages_where_one_request_is_too_large() -> None:
    setup = Setup()
    numbers = await many(setup, 25)
    service = capped(setup, 1000, heavy(setup, 10))

    with pytest.raises(ConfirmationRequired, match="delete ALL 25 entries"):
        _ = await service.empty(None, everything=True)
    result = await service.empty(None, everything=True, confirm=True)

    assert sorted(result.document_ids) == sorted(numbers)
    assert setup.ids() == []


@pytest.mark.parametrize("why", ["an entry too large", "pages overlap", "the trash changes"])
async def test_everything_is_refused_where_the_pages_do_not_show_every_entry(why: str) -> None:
    setup = Setup(writes=False)
    numbers = await many(setup, 12)

    class Odd(Heavy):
        @override
        async def list_trash(self, *, page: int, page_size: int, max_bytes: int) -> TrashPage:
            overlap = why == "pages overlap" and page_size < PAGE and page == 2
            found = await super().list_trash(
                page=1 if overlap else page, page_size=page_size, max_bytes=max_bytes
            )
            if overlap:
                return found.model_copy(update={"next_page": None})
            if why == "the trash changes" and page == 2:
                return found.model_copy(update={"count": 11})
            return found

    fake = Odd(setup.state, user="admin")
    fake.most = 11
    if why == "an entry too large":
        fake.huge = (numbers[5],)

    with pytest.raises(AnswerTooLarge) as raised:
        _ = await capped(setup, 1000, fake).empty(None, everything=True, confirm=True)

    assert raised.value.hint == "Empty in steps with document_ids from list_trash."
    assert raised.value.message == (
        "An entry over the limit of 64 MiB was not read: nothing was emptied."
        if why == "an entry too large"
        else NOT_ALL
    )
    assert len(setup.ids()) == 12


async def test_the_pages_are_closed_when_everything_is_refused() -> None:
    setup = Setup(writes=False)
    _ = await many(setup, 12)
    fake = heavy(setup, 10)
    closed: list[bool] = []

    class Watched(TrashScan):
        @override
        async def pages(self) -> AsyncGenerator[TrashPage | str]:
            try:
                async for page in super().pages():
                    yield page
            finally:
                closed.append(True)

    scan = Watched(fake, fake, setup.taxonomy)
    service = TrashService(fake, fake, fake, scan, setup.taxonomy, web_url=WEB, max_all=11)

    async with asyncio.timeout(5):
        with pytest.raises(Validation):
            _ = await service.empty(None, everything=True)
        assert closed == [True]


async def test_everything_in_pages_keeps_the_most_it_handles() -> None:
    setup = Setup(writes=False)
    _ = await many(setup, 12)
    fake = heavy(setup, 10)
    scan = TrashScan(fake, fake, setup.taxonomy)
    service = TrashService(fake, fake, fake, scan, setup.taxonomy, web_url=WEB, max_all=11)

    with pytest.raises(Validation, match="all=true handles at most 11 at once"):
        _ = await service.empty(None, everything=True, confirm=True)

    assert fake.asked == ((1, 12), (1, 100), (1, 10))
