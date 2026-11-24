"""DuplicateResolver: only verified duplicates, whole or refused, and every document named.

Fakes without `allow_writes` fail the test on any write, so a passing test proves none was made.
"""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, FakeUser, seeded

from mcp_server_paperless.domain.duplicate_resolution import MAX_DUPLICATES
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    Validation,
    accepting,
)
from mcp_server_paperless.domain.tasks import Unconfirmed
from mcp_server_paperless.domain.versions import VersionUpload
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.duplicate_resolution import (
    CHOOSE,
    HAS_VERSIONS_HINT,
    MAY_NOT,
    OWNERS,
    DuplicateResolver,
)
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from syrupy.assertion import SnapshotAssertion

    from mcp_server_paperless.domain.bulk import DocumentChange
    from mcp_server_paperless.domain.documents import DocumentRecord
    from mcp_server_paperless.domain.duplicate_resolution import ResolveAction
    from mcp_server_paperless.domain.duplicates import DuplicateRecord

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-51")]

WEB = "https://paperless.example"
FILE = b"%PDF-1.4 the same file"
ACTIONS: list[ResolveAction] = ["trash", "merge_as_versions"]
UNCONFIRMED = Unconfirmed(task_id="t-1", reason="it did not show up in the task list")
FAILED = Validation("Paperless could not apply the bulk edit: its task failed.", hint="Read again.")


@final
class Acting(FakePaperless):
    """The fake, with a write that ends otherwise and reads that fail after it."""

    # What happens instead of the write, and what it then returns or raises.
    instead: Callable[[], None] | None = None
    outcome: Unconfirmed | Exception | None = None
    # What happens after the write.
    then: Callable[[], None] | None = None
    unreadable: bool = False
    gone: frozenset[int] = frozenset()
    written: bool = False

    def _ended(self) -> Unconfirmed | None:
        self.written = True
        if self.then is not None:
            self.then()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    @override
    async def bulk_edit_documents(
        self, document_ids: Sequence[int], change: DocumentChange
    ) -> Unconfirmed | None:
        if self.instead is None:
            _ = await super().bulk_edit_documents(document_ids, change)
        else:
            self.instead()
        return self._ended()

    @override
    async def merge_as_versions(
        self, root_id: int, source_ids: Sequence[int], label: str | None
    ) -> Unconfirmed | None:
        if self.instead is None:
            _ = await super().merge_as_versions(root_id, source_ids, label)
        else:
            self.instead()
        return self._ended()

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        if document_id in self.gone:
            message = "gone"
            raise NotFound(message)
        return await super().get_document(document_id)

    @override
    async def get_duplicates(self, document_id: int) -> tuple[DuplicateRecord, ...]:
        if self.written and self.unreadable:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)
        return await super().get_duplicates(document_id)

    @override
    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        if self.written and self.unreadable:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)
        return await super().get_document_titles(document_ids)


class Setup:
    """Documents 10 (kept), 11 and 12 (duplicates) of the admin, and 20, another file."""

    def __init__(self, user: str = ADMIN[0], *, writes: bool = True) -> None:
        self.state: FakeState = seeded()
        self.fake: Acting = Acting(self.state, user=user, allow_writes=writes)
        self.taxonomy: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.service: DuplicateResolver = DuplicateResolver(
            self.fake, self.fake, self.fake, self.taxonomy, web_url=WEB
        )
        for number in (10, 11, 12):
            _ = self.document(number)
        _ = self.document(20, original=b"another file")

    def document(self, number: int, **given: object) -> FakeDocument:
        values: dict[str, object] = {"original": FILE, "owner": 2, "archive": None} | given
        made = FakeDocument(id=number, title=f"Document {number}", **values)  # pyright: ignore[reportArgumentType] -- keyword passthrough
        self.state.documents.append(made)
        return made

    def get(self, number: int) -> FakeDocument:
        return next(d for d in self.state.documents if d.id == number)

    def user(self, *permissions: str) -> FakeUser:
        made = FakeUser(id=9, username="limited", permissions=frozenset(permissions))
        self.state.users.append(made)
        self.fake = Acting(self.state, user="limited", allow_writes=True)
        self.service = DuplicateResolver(
            self.fake, self.fake, self.fake, self.taxonomy, web_url=WEB
        )
        return made

    async def version(self, number: int, content: bytes = b"newer") -> int:
        writer = FakePaperless(self.state, user=ADMIN[0], allow_writes=True)
        upload = VersionUpload(filename="v.txt", mime_type="text/plain", content=content)
        task = await writer.upload_document_version(number, upload)
        [version] = (await writer.get_task(task)).document_ids
        return version

    async def delete(self, number: int) -> None:
        writer = FakePaperless(self.state, user=ADMIN[0], allow_writes=True)
        await writer.delete_document(number)

    def live(self) -> set[int]:
        return {d.id for d in self.state.documents}

    def trash(self) -> set[int]:
        return {d.id for d in self.state.trash}

    async def ask(self, action: ResolveAction, ids: Sequence[int] = (11,)) -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await self.service.resolve(10, ids, action)
        return raised.value


# --- both ways ----------------------------------------------------------------------------------


async def test_the_duplicates_go_to_the_trash_and_the_kept_one_stays() -> None:
    setup = Setup()
    _ = setup.document(30)
    await setup.delete(30)

    done = await setup.service.resolve(10, [11, 12], "trash", confirm=True)

    assert {11, 12, 30} <= setup.trash()
    assert {10, 20} <= setup.live()
    assert (done.action, done.dry_run, done.applied) == ("trash", False, True)
    assert (done.kept.id, done.kept.title) == (10, "Document 10")
    assert [(d.id, d.title) for d in done.resolved] == [(11, "Document 11"), (12, "Document 12")]
    assert (done.not_resolved, done.versions, done.unconfirmed_task_id) == ((), None, None)
    assert [(o.document_id, o.in_trash) for o in done.other_duplicates] == [(30, True)]
    assert done.other_duplicates_count == 1
    assert done.web_url == f"{WEB}/documents/10/details"


async def test_the_duplicates_become_versions_for_good() -> None:
    setup = Setup()

    done = await setup.service.resolve(10, [11, 12], "merge_as_versions", confirm=True)

    assert setup.trash().isdisjoint({11, 12})
    assert setup.live().isdisjoint({11, 12})
    assert done.versions is not None
    assert [v.id for v in done.versions] == [12, 11, 10]
    assert [d.id for d in done.resolved] == [11, 12]
    assert done.message == (
        "Versions of document 10 now: document 11 'Document 11', document 12 'Document 12'."
    )


@pytest.mark.parametrize("action", ACTIONS)
async def test_without_confirm_nothing_is_written_and_the_accepted_call_runs(
    action: ResolveAction,
) -> None:
    setup = Setup(writes=False)
    refusal = await setup.ask(action)

    setup.fake.allow_writes = True
    with accepting(refusal.fingerprint):
        done = await setup.service.resolve(10, [11], action)

    assert [d.id for d in done.resolved] == [11]


@pytest.mark.parametrize("action", ACTIONS)
async def test_a_dry_run_says_what_confirming_would_do_and_writes_nothing(
    action: ResolveAction,
) -> None:
    setup = Setup(writes=False)

    preview = await setup.service.resolve(10, [11], action, dry_run=True, confirm=True)
    refusal = await setup.ask(action)

    assert (preview.dry_run, preview.applied) == (True, False)
    assert [d.id for d in preview.resolved] == [11]
    effect = refusal.message.removeprefix("Nothing was changed. ")
    assert preview.message.startswith(f"Dry run, nothing was changed. {effect} To go ahead")


@pytest.mark.parametrize("action", ACTIONS)
async def test_the_document_counts_are_read_again_also_after_a_failure(
    action: ResolveAction,
) -> None:
    setup = Setup()
    setup.get(11).tags = [1]

    async def count() -> int | None:
        items = await setup.taxonomy.items("tag")
        return next(i.document_count for i in items if i.id == 1)

    before = await count()
    assert before is not None
    setup.fake.outcome = FAILED
    with pytest.raises(PaperlessMcpError):
        _ = await setup.service.resolve(10, [11], action, confirm=True)

    assert await count() == before - 1


# --- what the preview says ----------------------------------------------------------------------


async def test_the_words_of_the_confirmations(snapshot: SnapshotAssertion) -> None:
    setup = Setup(writes=False)
    keep, plain, rich = (setup.get(n) for n in (10, 11, 12))
    keep.archive = b"its archive"
    rich.tags, rich.custom_fields = [1, 2], {1: "x"}
    rich.archive_serial_number = 7
    _ = await setup.version(12)
    _ = setup.document(13, original=b"its archive")
    unknown = setup.document(14)
    unknown.versions = [unknown.versions[0].model_copy(update={"checksum": None})]
    _ = setup.document(15)
    await setup.delete(15)
    assert plain.notes == []

    assert (await setup.ask("trash", [11, 12, 13])).describe() == snapshot(name="trash")
    merged = await setup.ask("merge_as_versions", [11, 13, 14])
    assert merged.describe() == snapshot(name="merge")
    single = Setup(writes=False)
    _ = await single.version(10)
    await single.delete(12)
    assert (await single.ask("trash")).describe() == snapshot(name="trash one")
    assert (await single.ask("merge_as_versions")).describe() == snapshot(name="merge one")


async def test_the_preview_says_what_is_final_and_what_can_be_restored() -> None:
    setup = Setup(writes=False)
    trash = (await setup.ask("trash")).message
    merge = (await setup.ask("merge_as_versions")).message

    assert "It can be restored until the trash is emptied" in trash
    assert "vanishes for good, not even into the trash" in merge
    assert "notes and other metadata are lost" in merge
    assert "With action trash it could be restored instead" in merge


async def test_many_other_duplicates_are_cut_and_counted_and_all_of_them_confirmed() -> None:
    setup = Setup(writes=False)
    for number in range(100, 110):
        _ = setup.document(number)

    refusal = await setup.ask("trash")
    setup.get(109).id = 110  # behind "and 1 more"
    other = await setup.ask("trash")
    preview = await setup.service.resolve(10, [11], "trash", dry_run=True)

    assert "document 108 'Document 108' and 1 more. Documents this user" in refusal.message
    assert other.message == refusal.message
    assert other.fingerprint != refusal.fingerprint
    assert (len(preview.other_duplicates), preview.other_duplicates_count) == (10, 11)


async def test_exactly_ten_other_duplicates_are_all_named() -> None:
    setup = Setup(writes=False)
    for number in range(100, 109):
        _ = setup.document(number)

    refusal = await setup.ask("trash")

    assert "document 108 'Document 108'. Documents this user" in refusal.message


@pytest.mark.parametrize(
    ("action", "changed"), [("trash", 10), ("trash", 11), ("merge_as_versions", 10)]
)
async def test_an_acceptance_does_not_cover_another_file(
    action: ResolveAction, changed: int
) -> None:
    setup = Setup(writes=False)
    newer = setup.get(changed).versions[0].model_copy(update={"id": 900, "is_root": False})
    setup.get(changed).versions.insert(0, newer)
    ids = [11, 12]
    first = await setup.ask(action, ids)
    setup.get(changed).versions[0] = newer.model_copy(update={"id": 901})
    second = await setup.ask(action, ids)

    assert second.message == first.message
    with accepting(first.fingerprint):
        assert (await setup.ask(action, ids)).fingerprint == second.fingerprint


# --- only verified duplicates -------------------------------------------------------------------


@pytest.mark.parametrize("action", ACTIONS)
async def test_what_is_no_live_visible_duplicate_is_named_and_nothing_is_sent(
    action: ResolveAction,
) -> None:
    setup = Setup(writes=False)
    version = await setup.version(12)
    _ = setup.document(13)
    await setup.delete(13)

    for dry_run in (False, True):
        with pytest.raises(Validation) as raised:
            _ = await setup.service.resolve(
                10, [11, 20, 13, 999, version], action, dry_run=dry_run, confirm=True
            )

        assert raised.value.message == (
            "Nothing was changed: document 20: Paperless does not take it for a duplicate of "
            "document 10; document 13: it is in the trash already; document 999: it does not "
            "exist outside the trash as a document this user may see; document "
            f"{version}: it does not exist outside the trash as a document this user may see."
        )
        assert raised.value.hint == CHOOSE


async def test_a_hidden_duplicate_is_told_like_a_missing_one_and_the_kept_one_must_be_seen() -> (
    None
):
    setup = Setup()
    _ = setup.user("view_document", "change_document", "delete_document")
    setup.get(10).owner = setup.get(11).owner = 9

    texts: set[str] = set()
    for number in (12, 999):
        with pytest.raises(Validation) as raised:
            _ = await setup.service.resolve(10, [11, number], "trash", confirm=True)
        texts.add(raised.value.message.replace(str(number), "N"))
    assert len(texts) == 1
    for number in (12, 999):
        with pytest.raises(NotFound):
            _ = await setup.service.resolve(number, [11], "trash", confirm=True)
    with pytest.raises(ConfirmationRequired) as refused:
        _ = await setup.service.resolve(10, [11], "trash")
    assert "document 12" not in refused.value.message
    assert setup.live() >= {10, 11, 12}


async def test_a_duplicate_that_goes_away_while_it_is_read_is_refused() -> None:
    setup = Setup(writes=False)
    setup.fake.gone = frozenset({11})

    with pytest.raises(Validation, match="document 11: it does not exist outside the trash"):
        _ = await setup.service.resolve(10, [11], "trash", confirm=True)


@pytest.mark.parametrize(
    ("ids", "message"),
    [
        ([], f"Name between 1 and {MAX_DUPLICATES} duplicates; got 0."),
        (range(30, 51), f"Name between 1 and {MAX_DUPLICATES} duplicates; got 21."),
        ([11, 10], "Document 10 is the one to keep, so it cannot be among the duplicates."),
        ([11, 12, 11], "Document 11 is listed twice."),
    ],
)
async def test_a_selection_that_cannot_be_meant_is_refused(
    ids: Sequence[int], message: str
) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.service.resolve(10, list(ids), "trash", confirm=True)

    assert raised.value.message == message
    assert raised.value.hint


async def test_the_most_duplicates_of_one_call_are_taken() -> None:
    setup = Setup()
    ids = list(range(30, 30 + MAX_DUPLICATES))
    for number in ids:
        _ = setup.document(number)

    done = await setup.service.resolve(10, ids, "trash", confirm=True)

    assert [d.id for d in done.resolved] == ids


# --- what may not be changed --------------------------------------------------------------------


@pytest.mark.parametrize("action", ACTIONS)
async def test_documents_the_user_may_not_change_are_named_and_nothing_is_sent(
    action: ResolveAction,
) -> None:
    setup = Setup(READER[0], writes=False)
    for number in (10, 11, 12):
        setup.get(number).owner, setup.get(number).viewers = 2, {3}
    setup.get(12).editors = {3}

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.service.resolve(10, [11, 12], action, confirm=True)
    preview = await setup.service.resolve(10, [11, 12], action, dry_run=True)

    keep = "document 10 'Document 10': this user may not change it; "
    named = (
        keep if action == "merge_as_versions" else ""
    ) + "document 11 'Document 11': this user may not change it."
    assert raised.value.message == f"Nothing was changed: {named}"
    assert raised.value.hint == OWNERS[action]
    assert preview.message == (
        f"Dry run, nothing was changed. It would be refused as a whole: {named}"
    )
    assert preview.resolved == ()
    expected = [10, 11] if action == "merge_as_versions" else [11]
    assert [(d.id, d.why) for d in preview.not_resolved] == [(n, MAY_NOT) for n in expected]


@pytest.mark.parametrize("action", ACTIONS)
async def test_a_refusal_by_paperless_changes_nothing_and_says_so(action: ResolveAction) -> None:
    setup = Setup()
    _ = setup.user("view_document", "change_document")
    for number in (10, 11):
        setup.get(number).owner = 9

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.service.resolve(10, [11], action, confirm=True)

    assert raised.value.message == (
        "Paperless refused it; it checks every document first, so nothing was changed."
    )
    assert raised.value.hint == OWNERS[action]
    assert setup.live() >= {10, 11}


async def test_a_duplicate_with_versions_is_trashed_but_not_merged() -> None:
    setup = Setup()
    version = await setup.version(11)

    with pytest.raises(Validation) as raised:
        _ = await setup.service.resolve(10, [11, 12], "merge_as_versions", confirm=True)
    preview = await setup.service.resolve(10, [11, 12], "merge_as_versions", dry_run=True)
    done = await setup.service.resolve(10, [11], "trash", confirm=True)

    why = "it has 2 versions, so it cannot be merged"
    assert raised.value.message == f"Nothing was changed: document 11 'Document 11': {why}."
    assert raised.value.hint == HAS_VERSIONS_HINT
    assert [(d.id, d.why) for d in preview.not_resolved] == [(11, why)]
    assert [d.id for d in done.resolved] == [11]
    assert {11, version} <= setup.trash()


async def test_the_kept_document_may_have_versions_when_merging() -> None:
    setup = Setup()
    version = await setup.version(10)

    done = await setup.service.resolve(10, [11], "merge_as_versions", confirm=True)

    assert done.versions is not None
    assert [v.id for v in done.versions] == [11, version, 10]


# --- failures -----------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ACTIONS)
async def test_a_failed_change_names_what_is_gone_and_what_is_still_there(
    action: ResolveAction,
) -> None:
    setup = Setup()

    def partly() -> None:
        document = setup.get(11)
        setup.state.documents.remove(document)
        document.deleted_at = datetime.now(UTC)
        setup.state.trash.append(document)

    setup.fake.instead, setup.fake.outcome = partly, FAILED
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.service.resolve(10, [11, 12], action, confirm=True)

    gone = (
        "In the trash now: document 11 'Document 11'. Not known to be resolved: "
        if action == "trash"
        else (
            "Not known to be resolved: document 11 'Document 11': it was neither a document of "
            "its own nor a version when read again; "
        )
    )
    assert raised.value.message == (
        f"{FAILED.message} {gone}document 12 'Document 12': it was still there when read again."
    )
    assert raised.value.hint == "Read again."


@pytest.mark.parametrize("action", ACTIONS)
async def test_what_cannot_be_read_after_a_failure_is_unknown(action: ResolveAction) -> None:
    setup = Setup()
    setup.fake.outcome, setup.fake.unreadable = UpstreamTimeout("Timed out."), True

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.service.resolve(10, [11], action, confirm=True)

    assert raised.value.message == (
        "Timed out. Not known to be resolved: document 11 'Document 11': what became of it could "
        "not be read."
    )


@pytest.mark.parametrize("action", ACTIONS)
async def test_a_change_that_cannot_be_followed_is_not_called_done(action: ResolveAction) -> None:
    setup = Setup()
    setup.fake.outcome, setup.fake.instead = UNCONFIRMED, lambda: None

    done = await setup.service.resolve(10, [11], action, confirm=True)

    assert (done.applied, done.unconfirmed_task_id, done.resolved) == (True, "t-1", ())
    assert [(d.id, d.why) for d in done.not_resolved] == [
        (11, "it was still there when read again")
    ]
    assert done.message == (
        "Not confirmed: Paperless queued task t-1, but it did not show up in the task list. Not "
        "known to be resolved: document 11 'Document 11': it was still there when read again. "
        "Read the task with get_task and the documents again before repeating the call."
    )


async def test_an_unfollowed_change_that_is_seen_is_reported_with_its_task() -> None:
    setup = Setup()
    setup.fake.outcome = UNCONFIRMED

    done = await setup.service.resolve(10, [11], "merge_as_versions", confirm=True)

    assert [d.id for d in done.resolved] == [11]
    assert done.message.startswith("Not confirmed: ")


async def test_a_success_that_cannot_be_read_back_says_so() -> None:
    setup = Setup()
    setup.fake.unreadable = True

    done = await setup.service.resolve(10, [11], "trash", confirm=True)

    assert (done.resolved, done.unconfirmed_task_id) == ((), None)
    assert [d.why for d in done.not_resolved] == ["what became of it could not be read"]


async def test_a_confirmed_trash_says_where_the_documents_are() -> None:
    done = await Setup().service.resolve(10, [11], "trash", confirm=True)

    assert done.message == (
        "In the trash now: document 11 'Document 11'. It can be restored "
        "until the trash is emptied (by default after 30 days), in the Paperless web UI or with "
        "restore_from_trash (toolset trash)."
    )


# --- what is seen afterwards --------------------------------------------------------------------


@pytest.mark.parametrize("action", ACTIONS)
async def test_a_kept_document_that_is_gone_afterwards_is_said_loudly(
    action: ResolveAction,
) -> None:
    setup = Setup()

    def crossed() -> None:
        kept = setup.get(10)
        setup.state.documents.remove(kept)
        kept.deleted_at = datetime.now(UTC)
        setup.state.trash.append(kept)

    setup.fake.then = crossed
    done = await setup.service.resolve(10, [11], action, confirm=True)

    assert done.kept_seen is False
    assert done.resolved == ()
    assert [d.why for d in done.not_resolved] == ["what became of it could not be seen"]
    expected = (
        "WARNING: the kept document 10 'Document 10' was gone when read again (deleted, merged or "
        "hidden meanwhile). Look for both in the trash with list_trash, and restore one if none "
        "is left. Not known to be resolved: document 11 'Document 11'"
    )
    assert done.message.startswith(expected)


async def test_a_kept_document_that_is_gone_leaves_a_duplicate_that_is_still_there() -> None:
    setup = Setup()
    setup.fake.instead = lambda: setup.state.documents.remove(setup.get(10))

    done = await setup.service.resolve(10, [11], "trash", confirm=True)

    assert [d.why for d in done.not_resolved] == ["it was still there when read again"]
    assert done.kept_seen is False


@pytest.mark.parametrize("action", ACTIONS)
async def test_a_duplicate_that_was_only_hidden_is_not_called_resolved(
    action: ResolveAction,
) -> None:
    setup = Setup()
    _ = setup.user("view_document", "change_document", "delete_document")
    setup.get(10).owner = setup.get(11).owner = 9

    def hide() -> None:
        setup.get(11).owner = 2

    setup.fake.instead = hide
    done = await setup.service.resolve(10, [11], action, confirm=True)

    assert (done.resolved, done.kept_seen) == ((), True)
    assert [d.id for d in done.not_resolved] == [11]
    assert "could be restored" not in done.message
    assert "can be restored" not in done.message


async def test_the_kept_document_is_seen_or_unknown() -> None:
    seen = await Setup().service.resolve(10, [11], "trash", confirm=True)
    setup = Setup()
    setup.fake.unreadable = True
    unknown = await setup.service.resolve(10, [11], "trash", confirm=True)
    preview = await Setup().service.resolve(10, [11], "trash", dry_run=True)

    assert (seen.kept_seen, unknown.kept_seen, preview.kept_seen) == (True, None, True)


# --- what an acceptance binds -------------------------------------------------------------------


def _modified(document: FakeDocument) -> None:
    document.modified = datetime(2027, 1, 1, tzinfo=UTC)  # a note's text shows only here


def _checksum(document: FakeDocument) -> None:
    newest = document.versions[0]
    document.versions[0] = newest.model_copy(update={"checksum": "f" * 64})


CHANGES: dict[str, Callable[[FakeDocument], None]] = {
    "tags": lambda d: d.tags.__setitem__(0, 2),
    "custom field": lambda d: d.custom_fields.__setitem__(1, "other"),
    "correspondent": lambda d: vars(d).update(correspondent=1),
    "created": lambda d: vars(d).update(created=date(2020, 1, 1)),
    "end of the title": lambda d: vars(d).update(title=d.title[:-1] + "!"),
    "modified": _modified,
    "version checksum": _checksum,
}


@pytest.mark.parametrize(
    ("action", "changed", "change"),
    [
        (action, changed, change)
        for action in ACTIONS
        for changed in (10, 11)
        for change in CHANGES
        # A duplicate with versions is not merged.
        if (action, changed, change) != ("merge_as_versions", 11, "version checksum")
    ],
)
async def test_an_acceptance_does_not_cover_changed_metadata(
    action: ResolveAction, changed: int, change: str
) -> None:
    setup = Setup(writes=False)
    document = setup.get(changed)
    document.title, document.tags, document.custom_fields = "T" * 100, [1], {1: "x"}
    if change == "version checksum":
        _ = await setup.version(changed)
    first = await setup.ask(action)
    CHANGES[change](document)
    second = await setup.ask(action)

    assert second.message == first.message
    assert second.fingerprint != first.fingerprint
    with accepting(first.fingerprint):
        _ = await setup.ask(action)


async def test_an_acceptance_covers_the_same_documents_whatever_the_order_of_their_tags() -> None:
    setup = Setup(writes=False)
    document = setup.get(11)
    document.tags, document.custom_fields = [1, 2], {1: "x", 2: "https://y"}
    first = await setup.ask("trash")
    document.tags, document.custom_fields = [2, 1], {2: "https://y", 1: "x"}

    assert (await setup.ask("trash")).fingerprint == first.fingerprint


async def test_an_acceptance_binds_where_the_unnamed_other_duplicates_are() -> None:
    setup = Setup(writes=False)
    for number in range(100, 111):
        _ = setup.document(number)
    first = await setup.ask("trash")
    # Behind "and 2 more": the last one goes to the trash, the text stays.
    await setup.delete(110)
    second = await setup.ask("trash")

    assert second.message == first.message
    assert second.fingerprint != first.fingerprint


async def test_the_case_of_a_checksum_is_no_difference() -> None:
    setup = Setup(writes=False)
    version = await setup.version(10, FILE)
    kept = setup.get(10)
    kept.versions = [
        v.model_copy(update={"checksum": (v.checksum or "").upper()}) if v.id == version else v
        for v in kept.versions
    ]

    assert "newest file" not in (await setup.ask("trash")).message


# --- one and many -------------------------------------------------------------------------------


async def test_the_texts_are_singular_for_one_and_plural_for_two() -> None:
    setup = Setup(writes=False)
    one, two = await setup.ask("trash", [11]), await setup.ask("trash", [11, 12])
    merged = await setup.ask("merge_as_versions", [11, 12])
    _ = setup.document(13)
    both = await setup.ask("trash", [11])

    assert "It can be restored" in one.message
    assert "Another duplicate of document 10 stays: document 12 'Document 12'." in one.message
    assert "They can be restored" in two.message
    assert "With action trash they could be restored instead" in merged.message
    assert "Other duplicates of document 10 stay: " in both.message
