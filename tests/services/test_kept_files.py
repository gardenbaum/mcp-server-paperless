"""Keeping the copy whose file exists: refused or named, for the trash and for merges (PLMCP-110).

Fakes without `allow_writes` fail the test on any write, so a passing test proves none was made.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Literal, final, override

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakeDocument, FakePaperless, FakeState, FakeUser, seeded

from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    PaperlessMcpError,
    UpstreamTimeout,
    Validation,
    accepting,
)
from mcp_server_paperless.domain.versions import VersionUpload
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.duplicate_resolution import DuplicateResolver
from mcp_server_paperless.services.file_checks import check_files, check_for_trash
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.versions import VersionService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.documents import DocumentRecord, FileMetadata
    from mcp_server_paperless.domain.duplicate_resolution import (
        DuplicateResolution,
        ResolveAction,
    )
    from mcp_server_paperless.domain.tasks import Unconfirmed

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-63")]

WEB = "https://paperless.example"
FILE = b"%PDF-1.4 the same file"
ACTIONS: list[ResolveAction] = ["trash", "merge_as_versions"]
NO_ORIGINAL = "Paperless cannot find the original of document 10"
LOST_WARNING = f"WARNING: {NO_ORIGINAL}"
UNSURE = "Whether Paperless can find the file of document 10 could not be checked"
TIMEOUT = "Paperless did not respond in time."


@final
class Flaky(FakePaperless):
    """The fake, with metadata that cannot be read of some documents, and an unfollowed merge."""

    broken: frozenset[int] = frozenset()  # documents and versions
    slow: bool = False
    delay: float = 0
    once: frozenset[int] = frozenset()  # readable only the first time
    unreadable_version: int | None = None
    reads: tuple[int, ...] = ()
    unfollowed: bool = False
    failing: bool = False
    unread: bool = False

    @override
    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        self.reads += (document_id,)
        if self.slow:
            await asyncio.sleep(60)
        await asyncio.sleep(self.delay)
        again = document_id in self.once and self.reads.count(document_id) > 1
        if again or (document_id, version_id) == (self.unreadable_version, self.unreadable_version):
            raise UpstreamTimeout(TIMEOUT)
        if not self.broken.isdisjoint({document_id, version_id}):
            raise UpstreamTimeout(TIMEOUT)
        return await super().get_document_metadata(document_id, version_id=version_id)

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        if self.unread:
            raise UpstreamTimeout(TIMEOUT)
        return await super().get_document(document_id)

    @override
    async def merge_as_versions(
        self, root_id: int, source_ids: Sequence[int], label: str | None
    ) -> Unconfirmed | None:
        if self.failing:
            raise UpstreamTimeout(TIMEOUT)
        done = await super().merge_as_versions(root_id, source_ids, label)
        self.unread = self.unfollowed
        return done


class Setup:
    """Documents 10 (kept), 11 and 12 (duplicates) of the admin."""

    def __init__(self, *, writes: bool = False) -> None:
        self.state: FakeState = seeded()
        self.fake: Flaky = Flaky(self.state, user=ADMIN[0], allow_writes=writes)
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        self.duplicates: DuplicateResolver = DuplicateResolver(
            self.fake, self.fake, self.fake, taxonomy, web_url=WEB
        )
        self.versions: VersionService = VersionService(self.fake, self.fake, taxonomy, web_url=WEB)
        for number in (10, 11, 12):
            self.state.documents.append(
                FakeDocument(
                    id=number, title=f"Document {number}", original=FILE, archive=None, owner=2
                )
            )

    def get(self, number: int) -> FakeDocument:
        return next(d for d in self.state.documents if d.id == number)

    def lose(self, *numbers: int, part: Literal["original", "archive"] = "original") -> None:
        self.state.missing |= {(n, part) for n in numbers}

    async def version(self, number: int, content: bytes = b"newer") -> int:
        writer = FakePaperless(self.state, user=ADMIN[0], allow_writes=True)
        upload = VersionUpload(filename="v.txt", mime_type="text/plain", content=content)
        task = await writer.upload_document_version(number, upload)
        [version] = (await writer.get_task(task)).document_ids
        return version

    async def preview(
        self, action: ResolveAction, ids: Sequence[int] = (11, 12)
    ) -> DuplicateResolution:
        return await self.duplicates.resolve(10, ids, action, dry_run=True)

    async def done(
        self, action: ResolveAction, ids: Sequence[int] = (11, 12)
    ) -> DuplicateResolution:
        return await self.duplicates.resolve(10, ids, action, confirm=True)

    async def ask(
        self, action: ResolveAction, ids: Sequence[int] = (11, 12)
    ) -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await self.duplicates.resolve(10, ids, action)
        return raised.value

    async def ask_merge(self, ids: Sequence[int] = (11,)) -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await self.versions.merge(10, ids, None)
        return raised.value


# --- resolving duplicates ------------------------------------------------------------------------


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("confirm", [False, True])
async def test_a_kept_document_without_its_file_is_refused_while_a_copy_has_it(
    action: ResolveAction, *, confirm: bool
) -> None:
    setup = Setup()
    setup.lose(10)
    if action == "trash":
        setup.lose(11)
    ids = [11, 12] if action == "trash" else [12]

    preview = await setup.preview(action, ids)
    with pytest.raises(Validation) as refused:
        _ = await setup.duplicates.resolve(10, ids, action, confirm=confirm)

    why = "Paperless cannot find its original, and document 12 still has that file"
    assert [(d.id, d.why) for d in preview.not_resolved] == [(10, why)]
    assert preview.resolved == ()
    assert "It would be refused as a whole: document 10 'Document 10': " + why in preview.message
    assert refused.value.message.startswith(
        f"Nothing was changed: document 10 'Document 10': {why}. {LOST_WARNING}."
    )
    assert refused.value.hint == (
        "Keep a document whose file is there instead, or restore the missing file from a backup."
    )


async def test_every_copy_that_still_has_the_file_is_named() -> None:
    setup = Setup()
    setup.lose(10)

    preview = await setup.preview("trash")

    [kept] = preview.not_resolved
    assert kept.why == (
        "Paperless cannot find its original, and documents 11 and 12 still have that file"
    )


async def test_a_file_that_no_copy_has_either_is_named_everywhere() -> None:
    action: ResolveAction = "trash"
    setup = Setup(writes=True)
    setup.lose(10, 11, 12)

    preview = await setup.preview(action)
    asked = await setup.ask(action)
    done = await setup.done(action)

    for message in (preview.message, asked.message, done.message):
        for number in (10, 11, 12):
            assert f"Paperless cannot find the original of document {number}" in message
        assert LOST_WARNING in message
    assert [d.id for d in done.resolved] == [11, 12]


async def test_a_repeated_check_refuses_what_changed_after_the_confirmation() -> None:
    setup = Setup(writes=True)
    asked = await setup.ask("trash")
    setup.lose(10)

    with accepting(asked.fingerprint), pytest.raises(Validation, match="cannot find its original"):
        _ = await setup.duplicates.resolve(10, [11, 12], "trash")

    assert {11, 12} <= {d.id for d in setup.state.documents}


@pytest.mark.parametrize("action", ACTIONS)
async def test_an_acceptance_covers_only_the_missing_files_it_named(action: ResolveAction) -> None:
    setup = Setup()
    for number in (10, 11, 12):
        setup.get(number).archive = b"an archive"
    asked = await setup.ask(action)
    setup.lose(10, part="archive")
    setup.lose(11, 12, part="archive")

    with accepting(asked.fingerprint), pytest.raises(ConfirmationRequired) as again:
        _ = await setup.duplicates.resolve(10, [11, 12], action)

    assert "WARNING: Paperless cannot find the archive version of document 10." in (
        again.value.message
    )


async def test_the_archive_version_is_made_again_from_the_original_so_it_is_only_named() -> None:
    setup = Setup()
    for number in (10, 11):
        setup.get(number).archive = b"an archive"
    setup.lose(10, part="archive")

    asked = await setup.ask("trash", [11])

    assert "WARNING: Paperless cannot find the archive version of document 10." in asked.message


async def test_a_copy_with_only_an_archive_version_still_has_more_than_nothing() -> None:
    setup = Setup()
    for number in (10, 11):
        setup.get(number).archive = b"an archive"
    setup.lose(10, 11)
    asked = await setup.ask("trash", [11])
    both = "Paperless cannot find its original or its archive version"
    setup.lose(10, part="archive")

    preview = await setup.preview("trash", [11])

    assert f"{LOST_WARNING}." in asked.message
    assert [(d.id, d.why) for d in preview.not_resolved] == [
        (10, f"{both}, and document 11 still has an archive version")
    ]
    assert (
        "WARNING: Paperless cannot find the original and the archive version of document 10."
        in preview.message
    )


async def test_the_file_that_was_compared_counts_not_a_later_version() -> None:
    setup = Setup()
    version = await setup.version(10)
    setup.lose(10)

    refused = await setup.preview("trash")
    setup.state.missing = {(version, "original")}
    named = await setup.ask("trash")

    assert [d.id for d in refused.not_resolved] == [10]
    assert f"{LOST_WARNING} (version 10)." in refused.message
    assert f"{LOST_WARNING} (version {version})." in named.message


async def test_a_later_version_of_the_same_file_is_that_file() -> None:
    setup = Setup()
    _ = await setup.version(10, FILE)
    setup.lose(10)

    assert f"{LOST_WARNING} (version 10)." in (await setup.ask("trash")).message


async def test_the_copies_are_not_looked_at_while_the_kept_document_has_its_file() -> None:
    setup = Setup()

    assert "cannot find" not in (await setup.ask("trash")).message
    assert setup.fake.reads == (10,)


async def test_a_trash_that_cannot_be_checked_says_so_and_goes_on() -> None:
    setup = Setup(writes=True)
    setup.fake.broken = frozenset({10})

    preview = await setup.preview("trash")
    asked = await setup.ask("trash")
    done = await setup.done("trash")

    for message in (preview.message, asked.message, done.message):
        assert f"WARNING: {UNSURE}." in message
        assert TIMEOUT not in message
    assert [d.id for d in done.resolved] == [11, 12]


async def test_a_version_is_named_also_when_the_other_could_not_be_checked() -> None:
    setup = Setup()
    setup.fake.broken = frozenset({await setup.version(10)})
    setup.lose(10, 11, 12)

    message = (await setup.ask("trash")).message

    assert f"{LOST_WARNING} (version 10). WARNING: {UNSURE}." in message


async def test_trashing_duplicates_needs_no_right_to_change_the_kept_document() -> None:
    setup = Setup()
    setup.state.users.append(
        FakeUser(
            id=9,
            username="limited",
            permissions=frozenset({"view_document", "change_document", "delete_document"}),
        )
    )
    setup.get(10).owner, setup.get(11).owner = 2, 9
    setup.get(10).viewers = {9}
    fake = Flaky(setup.state, user="limited")
    taxonomy = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=3600.0)
    service = DuplicateResolver(fake, fake, fake, taxonomy, web_url=WEB)

    preview = await service.resolve(10, [11], "trash", dry_run=True)

    assert [d.id for d in preview.resolved] == [11]


async def test_a_lost_file_and_copies_that_cannot_be_checked_are_both_named() -> None:
    setup = Setup()
    setup.lose(10)
    setup.fake.broken = frozenset({11, 12})

    message = (await setup.ask("trash")).message

    assert f"{LOST_WARNING}." in message
    assert "Whether Paperless can find the file of document 11 could not be checked." in message
    assert "Whether Paperless can find the file of document 12 could not be checked." in message


@pytest.mark.parametrize("unchecked", [10, 11])
async def test_a_merge_that_cannot_be_checked_is_refused(unchecked: int) -> None:
    setup = Setup()
    setup.fake.broken = frozenset({unchecked})

    preview = await setup.preview("merge_as_versions")
    with pytest.raises(PaperlessMcpError) as refused:
        _ = await setup.done("merge_as_versions")

    why = "whether Paperless can find its file could not be checked (the reason: " + TIMEOUT[:-1]
    assert [(d.id, d.why) for d in preview.not_resolved] == [(unchecked, f"{why})")]
    assert f"document {unchecked} 'Document {unchecked}': {why})" in refused.value.message
    assert refused.value.hint == (
        "A merge is final, so every file is checked before it: try again in a moment, or use "
        "action trash."
    )


@pytest.mark.parametrize("confirm", [False, True])
async def test_a_duplicate_without_its_file_is_not_merged(*, confirm: bool) -> None:
    setup = Setup()
    setup.lose(11)

    preview = await setup.preview("merge_as_versions")
    with pytest.raises(Validation) as refused:
        _ = await setup.duplicates.resolve(10, [11, 12], "merge_as_versions", confirm=confirm)

    why = "Paperless cannot find its original, so a version of it would hold a missing file"
    assert [(d.id, d.why) for d in preview.not_resolved] == [(11, why)]
    assert f"document 11 'Document 11': {why}." in refused.value.message
    assert "Paperless cannot find the original of document 11." in refused.value.message
    assert refused.value.hint == (
        "Use action trash for a duplicate whose file is missing, or leave it out."
    )


async def test_a_duplicate_that_only_lacks_its_archive_version_is_merged_and_named() -> None:
    setup = Setup()
    setup.get(11).archive = b"an archive"
    setup.lose(11, part="archive")

    message = (await setup.ask("merge_as_versions")).message

    assert "Paperless cannot find the archive version of document 11." in message


async def test_the_kept_document_stays_lost_after_the_second_read_fails() -> None:
    setup = Setup(writes=True)
    setup.lose(10)
    setup.fake.once = frozenset({10})

    with pytest.raises(Validation, match="still have that file"):
        _ = await setup.done("trash")

    assert setup.fake.reads == (10, 11, 12)
    assert {11, 12} <= {d.id for d in setup.state.documents}


async def test_the_kept_document_and_the_copies_share_one_time_limit() -> None:
    setup = Setup()
    setup.lose(10)
    setup.fake.delay = 0.15
    keep, copy = [await setup.fake.get_document(n) for n in (10, 11)]

    started = time.monotonic()
    found = await check_for_trash(setup.fake, keep, [copy], seconds=0.2)

    assert time.monotonic() - started < 0.28
    assert found.lost(10)
    assert set(found.unchecked) == {11}


async def test_a_middle_version_of_the_same_file_is_that_file() -> None:
    setup = Setup()
    _ = await setup.version(10, FILE)
    newest = await setup.version(10)
    setup.lose(10, newest)

    message = (await setup.ask("trash")).message

    assert f"{LOST_WARNING} (version 10)." in message
    assert f"{LOST_WARNING} (version {newest})." in message


async def test_a_copy_whose_root_cannot_be_read_has_the_file_as_a_later_version() -> None:
    setup = Setup()
    _ = await setup.version(11, FILE)
    setup.lose(10)
    setup.fake.unreadable_version = 11

    [kept] = (await setup.preview("trash", [11])).not_resolved

    assert kept.why == "Paperless cannot find its original, and document 11 still has that file"


async def test_the_only_file_that_matched_is_not_trashed_if_the_kept_archive_version_is_lost() -> (
    None
):
    setup = Setup()
    setup.get(10).archive = b"matched"
    copy = FakeDocument(id=13, title="Document 13", original=b"matched", archive=None, owner=2)
    setup.state.documents.append(copy)
    setup.lose(10, part="archive")

    [kept] = (await setup.preview("trash", [13])).not_resolved

    assert kept.why == (
        "Paperless cannot find its archive version, the file that matched, and document 13 still "
        "has that file"
    )


async def test_a_merge_of_whole_documents_that_matched_by_an_archive_version_is_asked_for() -> None:
    setup = Setup()
    setup.get(10).archive = b"matched"
    copy = FakeDocument(id=13, title="Document 13", original=b"matched", archive=None, owner=2)
    setup.state.documents.append(copy)

    assert "cannot find" not in (await setup.ask("merge_as_versions", [13])).message


async def test_a_whole_document_without_a_known_checksum_has_its_file() -> None:
    setup = Setup()
    keep = setup.get(10)
    keep.versions = [keep.versions[0].model_copy(update={"checksum": None})]

    assert "cannot find" not in (await setup.ask("merge_as_versions")).message


async def test_a_copy_that_has_the_lost_original_as_its_archive_version_still_has_it() -> None:
    setup = Setup()
    copy = FakeDocument(id=13, title="Document 13", original=b"other", archive=FILE, owner=2)
    setup.state.documents.append(copy)
    setup.lose(10, 13)

    [kept] = (await setup.preview("trash", [13])).not_resolved
    setup.lose(13, part="archive")
    asked = await setup.ask("trash", [13])

    assert kept.why == "Paperless cannot find its original, and document 13 still has that file"
    assert "the original and the archive version of document 13." in asked.message


async def test_a_copy_that_matched_by_its_archive_version_has_an_original_of_its_own() -> None:
    setup = Setup()
    setup.get(10).archive = b"matched"
    copy = FakeDocument(id=13, title="Document 13", original=b"other", archive=b"matched", owner=2)
    setup.state.documents.append(copy)
    setup.lose(10)

    [kept] = (await setup.preview("trash", [13])).not_resolved

    assert kept.why == (
        "Paperless cannot find its original, only its archive version, and document 13 still has "
        "an original"
    )


async def test_a_failed_change_names_the_missing_file_too() -> None:
    setup = Setup()
    setup.get(10).archive = b"an archive"
    setup.lose(10, part="archive")
    setup.fake.failing = True

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.done("merge_as_versions")

    assert "WARNING: Paperless cannot find the archive version" in raised.value.message


async def test_everything_that_blocks_is_named_with_what_helps() -> None:
    setup = Setup()
    setup.lose(10)
    _ = await setup.version(11)

    with pytest.raises(Validation) as raised:
        _ = await setup.done("merge_as_versions")

    assert "it has 2 versions" in raised.value.message
    assert (
        "cannot find its original, and documents 11 and 12 still have that file"
        in raised.value.message
    )
    assert "Use action trash" in (raised.value.hint or "")
    assert "Keep a document whose file is there" in (raised.value.hint or "")


# --- merge_documents_as_versions -----------------------------------------------------------------


@pytest.mark.parametrize("lost", [10, 11])
async def test_a_merge_names_a_missing_file_in_the_confirmation_and_the_result(lost: int) -> None:
    setup = Setup(writes=True)
    setup.lose(lost)

    asked = await setup.ask_merge()
    done = await setup.versions.merge(10, [11], None, confirm=True)

    named = f"Paperless cannot find the original of document {lost}."
    assert named in asked.message
    assert done.message is not None
    assert named in done.message
    top = (
        "WARNING: document 11 becomes the newest version of document 10, which then shows a "
        "missing original until that version is deleted."
    )
    stays = "only its file stays, as a version of document 10"
    for message in (asked.message, done.message):
        assert (top in message) is (lost == 11)
    assert (stays in asked.message) is (lost == 10)
    assert ("then shows the file and text of document 11" in asked.message) is (lost == 10)
    assert done.versions is not None


async def test_a_merge_warns_of_the_last_source_only_and_promises_no_lost_file() -> None:
    setup = Setup()
    setup.lose(11)

    first = (await setup.ask_merge([11, 12])).message
    last = (await setup.ask_merge([12, 11])).message

    for message in (first, last):
        assert "only those of their files that Paperless can find stay, as versions" in message
    assert "becomes the newest version" not in first
    assert "then shows the file and text of document 12" in first
    assert "WARNING: document 11 becomes the newest version of document 10" in last
    assert "then shows the text of document 11" in last


async def test_a_merge_with_all_files_says_nothing_about_them() -> None:
    setup = Setup(writes=True)

    assert "cannot find" not in (await setup.ask_merge()).message
    assert (await setup.versions.merge(10, [11], None, confirm=True)).message is None


@pytest.mark.parametrize("unchecked", [10, 11])
async def test_a_merge_of_documents_that_cannot_be_checked_is_refused(unchecked: int) -> None:
    setup = Setup()
    setup.fake.broken = frozenset({unchecked})

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.versions.merge(10, [11, 12], None, confirm=True)

    assert raised.value.message == (
        f"Whether Paperless can find the file of document {unchecked} could not be checked, so "
        f"nothing was merged: a merge is final. The reason: {TIMEOUT}"
    )
    assert raised.value.hint == "Try again in a moment."


async def test_an_accepted_merge_does_not_cover_a_file_that_went_missing() -> None:
    setup = Setup()
    asked = await setup.ask_merge()
    setup.lose(10)

    with accepting(asked.fingerprint), pytest.raises(ConfirmationRequired) as again:
        _ = await setup.versions.merge(10, [11], None)

    assert f"{LOST_WARNING}." in again.value.message


async def test_a_merge_that_cannot_be_read_afterwards_still_names_the_missing_file() -> None:
    setup = Setup(writes=True)
    setup.lose(10)
    setup.fake.unfollowed = True

    done = await setup.versions.merge(10, [11], None, confirm=True)

    assert done.versions is None
    assert "could not be read afterwards" in (done.message or "")
    assert f"{LOST_WARNING}." in (done.message or "")


# --- the check -----------------------------------------------------------------------------------


async def test_a_check_that_takes_too_long_is_one_that_could_not_be_made() -> None:
    setup = Setup()
    setup.fake.slow = True
    documents = [await setup.fake.get_document(n) for n in (10, 11)]

    found = await check_files(setup.fake, documents, seconds=0.05)

    assert found.unchecked == dict.fromkeys((10, 11), "The check took longer than 0.05 seconds.")
