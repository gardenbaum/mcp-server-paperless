"""rotate_documents where a date field hides the task: the new version tells (decision-74)."""

import asyncio
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeCopying, FakeDocument, FakePaperless, FakeState, seeded
from harness import pdf_service

from mcp_server_paperless.domain.errors import PermissionDenied, UpstreamTimeout
from mcp_server_paperless.domain.pdf import FoundCopies, StartedTasks
from mcp_server_paperless.domain.versions import VersionUpload

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.pdf import PdfResult, RotationSearch
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-74")]

ONE, TWO, PLAIN = 501, 502, 503
WEB = "https://paperless.example"
PASSING = "files of earlier operations of this user may still come"
UNTOLD = "Which new version is the rotation could not be told ({doubt})."
NONE_FOUND = (
    "Paperless accepted the request, but no task showed up for it (its custom fields hold a date, "
    "for which Paperless 3.5 lists no task: the work is done all the same)."
)


def told(documents: str, found: int, expected: int) -> str:
    versions = "version" if expected == 1 else "versions"
    return (
        f"Paperless lists no task for the rotation of {documents}, because a custom field holds a "
        f"date: {found} of {expected} new {versions} found and told by file name, time and user."
    )


class Paperless(FakePaperless):
    """The fake, with dated documents, whose versions take time and can be in doubt."""

    def __init__(self) -> None:
        state = seeded()
        super().__init__(state, user=ADMIN[0], allow_writes=True)
        [field] = [f.id for f in state.custom_fields if f.data_type == "date"]
        for number in (ONE, TWO, PLAIN):
            dates = {} if number == PLAIN else {field: "2026-01-15"}
            made = FakeDocument(id=number, title="D", owner=2, custom_fields=dict(dates))
            state.documents.append(made)
        self.now: float = 0.0
        self.searches: list[tuple[dict[int, int], bool]] = []
        self.after: float = 0.0  # when the versions are there
        self.first: int = 0  # how many of them are there at once
        self.doubt: str | None = None
        self.fails: bool = False
        self.slow: float = 0.0  # seconds a look takes by the clock
        self.hang: bool | None = None  # the look (final or not) that never ends
        self.looked_for: RotationSearch | None = None
        self.lose: bool = False
        self.foreign: FakePaperless | None = None
        self.no_fields: bool = False
        self.late_date: int | None = None  # gets its date after it was read

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay

    async def rotate(self, *numbers: int, wait: float = 30) -> PdfResult:
        service = pdf_service(self, sleep=self.sleep, clock=self.clock)
        return await service.rotate(numbers, 90, wait_seconds=wait)

    def newest(self, number: int) -> int:
        [found] = [d for d in self.state.documents if d.id == number]
        return found.versions[0].id

    @override
    async def rotate_documents(
        self, document_ids: Sequence[int], degrees: int, *, original_files: bool = False
    ) -> StartedTasks:
        if self.late_date is not None:
            [late] = [d for d in self.state.documents if d.id == self.late_date]
            late.custom_fields = dict(self.state.documents[-3].custom_fields)
        if self.foreign is not None:
            await add(self.foreign, document_ids[0])
        started = await super().rotate_documents(
            document_ids, degrees, original_files=original_files
        )
        if self.foreign is not None:
            await add(self.foreign, document_ids[0])
        if not self.lose:
            return started
        return started.model_copy(update={"problem": "failed"})

    @override
    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        if self.no_fields and kind == "custom_field":
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        return await super().list_taxonomy(kind)

    @override
    async def find_rotations(self, search: RotationSearch, *, final: bool = False) -> FoundCopies:
        before = search.before
        self.searches.append((dict(before), final))
        self.looked_for = search
        self.now += self.slow
        if self.hang == final:
            await asyncio.sleep(60)
        if self.fails:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)
        found = await super().find_rotations(search, final=final)
        if self.doubt is not None:
            nothing = (None,) * len(before)
            return FoundCopies(ids=nothing, doubt=self.doubt, passing=self.doubt == PASSING)
        if self.now >= self.after:
            return found
        there = found.ids[: self.first]
        return FoundCopies(ids=there + (None,) * (len(found.ids) - len(there)))


async def add(somebody: FakePaperless, number: int) -> None:
    """A version with the file name of a rotation that somebody else uploads."""
    upload = VersionUpload(
        filename=f"{number}_rotated.pdf", mime_type="application/pdf", content=b"%PDF-1.4 theirs"
    )
    _ = await somebody.upload_document_version(number, upload)


def another(state: FakeState) -> FakePaperless:
    """Another user, who may change the documents."""
    users = [u for u in state.users if u.username != READER[0]]
    [restricted] = [u for u in state.users if u.username == READER[0]]
    rights = frozenset({"view_document", "change_document", "add_document"})
    state.users = [*users, replace(restricted, permissions=rights)]
    for document in state.documents:
        document.editors.add(restricted.id)
    return FakePaperless(state, user=READER[0], allow_writes=True)


async def test_a_finished_rotation_is_reported_although_a_date_hides_its_task() -> None:
    fake = Paperless()

    result = await fake.rotate(ONE, TWO)

    assert (result.state, result.wait) == ("success", "finished")
    assert result.produced_ids == (fake.newest(ONE), fake.newest(TWO))
    assert result.produced_links == tuple(f"{WEB}/documents/{n}/details" for n in (ONE, TWO))
    assert [(t.task_id, t.state) for t in result.tasks] == [(None, "success")] * 2
    assert result.message == told(f"documents {ONE}, {TWO}", 2, 2)
    assert fake.searches == [({ONE: ONE, TWO: TWO}, False)]


async def test_the_version_the_document_showed_before_is_what_it_is_compared_with() -> None:
    fake = Paperless()
    _ = await fake.rotate(ONE)
    before = fake.newest(ONE)

    result = await fake.rotate(ONE)

    assert fake.searches[-1] == ({ONE: before}, False)
    assert (result.state, result.produced_ids) == ("success", (fake.newest(ONE),))
    assert result.message == told(f"document {ONE}", 1, 1)


async def test_listed_tasks_and_hidden_ones_are_reported_together() -> None:
    fake = Paperless()

    result = await fake.rotate(PLAIN, ONE)

    assert fake.searches == [({ONE: ONE}, False)]
    assert (result.state, result.wait) == ("success", "finished")
    assert result.produced_ids == (fake.newest(ONE), fake.newest(PLAIN))
    assert result.produced_links == tuple(f"{WEB}/documents/{n}/details" for n in (ONE, PLAIN))
    assert result.message == told(f"document {ONE}", 1, 1)


async def test_the_call_waits_for_the_versions() -> None:
    fake = Paperless()
    fake.after, fake.first = 4.0, 1

    result = await fake.rotate(ONE, TWO)

    assert (result.state, result.wait, result.waited_seconds) == ("success", "finished", 4.0)
    assert len(fake.searches) > 1


async def test_what_is_not_there_in_time_is_unknown_and_what_is_there_is_reported() -> None:
    fake = Paperless()
    fake.after, fake.first = 100.0, 1

    result = await fake.rotate(ONE, TWO, wait=2)

    assert (result.state, result.wait, result.waited_seconds) == ("unknown", "timed_out", 2.0)
    assert result.produced_ids == (fake.newest(ONE),)
    assert result.produced_links == (f"{WEB}/documents/{ONE}/details",)
    assert [t.state for t in result.tasks] == ["success", "unknown"]
    assert (
        result.tasks[1].message
        == f"There is no new version with the file name {TWO}_rotated.pdf yet."
    )
    assert result.message is not None
    assert result.message.endswith(told(f"documents {ONE}, {TWO}", 1, 2))
    assert [final for _, final in fake.searches][-2:] == [False, True]


async def test_a_version_of_somebody_else_is_never_taken_for_the_rotation() -> None:
    fake = Paperless()
    fake.foreign = another(fake.state)

    result = await fake.rotate(ONE)

    [document] = [d for d in fake.state.documents if d.id == ONE]
    theirs, ours, first, _ = [v.id for v in document.versions]
    assert result.produced_ids == (ours,)
    assert not {theirs, first} & {*result.produced_ids}


async def test_a_version_of_somebody_else_alone_leaves_the_answer_unknown() -> None:
    fake = Paperless()
    fake.foreign = another(fake.state)
    fake.state.audit_log_enabled = False  # nothing says who added a version

    result = await fake.rotate(ONE)

    assert (result.state, result.produced_ids, result.tasks) == ("unknown", (), ())
    doubt = "the history, which names who added a version, may not be read"
    assert result.message == f"{UNTOLD.format(doubt=doubt)} {NONE_FOUND}"
    assert len(fake.searches) == 1


async def test_a_doubt_that_stays_ends_the_wait_and_one_that_may_pass_does_not() -> None:
    stays, passes = Paperless(), Paperless()
    stays.doubt, passes.doubt = "another operation", PASSING

    ended = await stays.rotate(ONE, PLAIN, wait=5)
    waited = await passes.rotate(ONE, wait=5)

    assert (ended.state, ended.produced_ids) == ("unknown", (stays.newest(PLAIN),))
    assert ended.message is not None
    assert ended.message.startswith(UNTOLD.format(doubt="another operation"))
    assert "only 1 of the 2 tasks" in ended.message
    assert "hold a date" in ended.message
    assert len(stays.searches) == 1
    assert (waited.state, waited.waited_seconds) == ("unknown", 5.0)
    assert passes.searches[-1] == ({ONE: ONE}, True)


async def test_a_search_that_breaks_leaves_the_answer_unknown() -> None:
    fake = Paperless()
    fake.fails = True

    result = await fake.rotate(ONE)

    why = "Paperless did not respond in time."
    assert (result.state, result.message) == (
        "unknown",
        f"{UNTOLD.format(doubt=why.rstrip('.'))} {NONE_FOUND}",
    )


async def test_nothing_is_looked_for_without_a_wait_a_date_or_a_readable_task_list() -> None:
    unwaited, undated, unread, unknown = (Paperless() for _ in range(4))
    unread.lose = True
    unknown.no_fields = True

    assert (await unwaited.rotate(ONE, wait=0)).state == "unknown"
    assert (await undated.rotate(PLAIN)).state == "success"
    assert (await unread.rotate(ONE)).state == "unknown"
    message = (await unknown.rotate(ONE)).message
    for fake in (unwaited, undated, unread, unknown):
        assert fake.searches == []
    assert "may hold a date (their definitions cannot be read)" in (message or "")


async def test_a_document_without_a_date_whose_task_is_missing_stays_unknown() -> None:
    fake = Paperless()
    fake.late_date = PLAIN  # its task is hidden, but nothing that was read says why

    result = await fake.rotate(ONE, PLAIN, wait=1)

    assert (result.state, result.produced_ids) == ("unknown", (fake.newest(ONE),))
    assert result.message == (
        f"{told(f'document {ONE}', 1, 1)} Paperless accepted the request, but only 1 of the 2 "
        "tasks it starts showed up: the state covers that one only."
    )


def unsettled(fake: Paperless) -> None:
    """An earlier rotation of the user whose version is not there."""
    earlier = FakeCopying(str(uuid.uuid4()), 2, datetime.now(UTC), "_rotated.pdf")
    fake.state.copying.append(earlier)


async def test_the_other_documents_of_a_mixed_request_settle_no_earlier_rotation() -> None:
    fake, settled = Paperless(), Paperless()
    unsettled(fake)
    _ = await settled.rotate(TWO)

    doubted = await fake.rotate(ONE, PLAIN, wait=2)
    done = await settled.rotate(ONE, PLAIN, wait=2)

    assert fake.looked_for is not None
    assert (dict(fake.looked_for.before), fake.looked_for.documents) == ({ONE: ONE}, (ONE, PLAIN))
    assert (doubted.state, doubted.produced_ids) == ("unknown", (fake.newest(PLAIN),))
    assert UNTOLD.format(doubt=PASSING) in (doubted.message or "")
    assert done.state == "success"
    assert done.produced_ids == (settled.newest(ONE), settled.newest(PLAIN))


async def test_the_time_a_look_takes_is_reported_also_where_it_is_over_the_bound() -> None:
    fake = Paperless()
    fake.slow, fake.after = 3.0, 100.0

    result = await fake.rotate(ONE, wait=2)

    assert (result.state, result.waited_seconds) == ("unknown", 6.0)
    assert [final for _, final in fake.searches] == [False, True]


async def test_a_look_is_cut_at_the_bound_and_the_last_one_still_made() -> None:
    fake = Paperless()
    fake.hang = False

    began = time.monotonic()
    result = await fake.rotate(ONE, wait=0.05)
    assert time.monotonic() - began < 5

    assert [final for _, final in fake.searches] == [False, True]
    assert result.state == "success"


async def test_a_last_look_that_does_not_end_leaves_the_answer_unknown() -> None:
    fake = Paperless()
    fake.hang, fake.after = True, 100.0

    service = pdf_service(fake, sleep=fake.sleep, clock=fake.clock, last_look=0.05)
    result = await service.rotate([ONE], 90, wait_seconds=1)

    assert result.state == "unknown"
    assert "the last look took too long" in (result.message or "")
