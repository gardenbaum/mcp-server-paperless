"""DuplicateFinder: the same file in documents, versions and the trash, and what was not read."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.errors import (
    NotFound,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
)
from mcp_server_paperless.domain.versions import VersionUpload
from mcp_server_paperless.services.duplicates import (
    HIDDEN,
    OWN_TRASH,
    UNCOMPARED,
    UNREAD_AFTER,
    UNREAD_REJECTED,
    VERSION_AFTER,
    VERSIONS_AFTER,
    Bounds,
    DuplicateFinder,
)

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import DocumentPage, DocumentQuery, FileMetadata
    from mcp_server_paperless.domain.duplicates import DuplicateMatch, DuplicateRecord
    from mcp_server_paperless.domain.trash import TrashPage
    from mcp_server_paperless.domain.versions import VersionPage

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-47")]

WEB = "https://paperless.example"
FILE = b"%PDF-1.4 the same file"
SHA = hashlib.sha256(FILE).hexdigest()
DELETED = datetime(2026, 11, 20, tzinfo=UTC)
DENIED = PermissionDenied("The Paperless user may not perform this action.")
DOWN = UpstreamTimeout("Paperless did not respond in time.")


class Failing(FakePaperless):
    """The fake, with methods that fail on demand and a record of what was read."""

    def __init__(self, state: FakeState, user: str = ADMIN[0]) -> None:
        super().__init__(state, user=user)
        self.errors: dict[str, Exception] = {}
        self.calls: list[str] = []

    def note(self, name: str) -> None:
        self.calls.append(name)
        if name in self.errors:
            raise self.errors[name]

    @override
    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        self.note("search_documents")
        return await super().search_documents(query, page=page, page_size=page_size)

    @override
    async def list_versions(self, *, page: int, page_size: int) -> VersionPage:
        self.note(f"list_versions {page}")
        self.note("list_versions")
        return await super().list_versions(page=page, page_size=page_size)

    @override
    async def list_trash(self, *, page: int, page_size: int) -> TrashPage:
        self.note(f"list_trash {page}")
        self.note("list_trash")
        return await super().list_trash(page=page, page_size=page_size)

    @override
    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        self.note("get_document_metadata")
        return await super().get_document_metadata(document_id, version_id=version_id)

    @override
    async def get_duplicates(self, document_id: int) -> tuple[DuplicateRecord, ...]:
        self.note("get_duplicates")
        return await super().get_duplicates(document_id)

    @override
    async def get_root(self, document_id: int) -> int:
        self.note("get_root")
        return await super().get_root(document_id)


class Setup:
    def __init__(self, user: str = ADMIN[0], bounds: Bounds | None = None) -> None:
        self.state: FakeState = seeded()
        self.fake: Failing = Failing(self.state, user)
        self.finder: DuplicateFinder = DuplicateFinder(
            self.fake, self.fake, web_url=WEB, bounds=bounds or Bounds()
        )

    def document(self, number: int, **given: object) -> FakeDocument:
        values: dict[str, object] = {"original": FILE, "owner": 2} | given
        made = FakeDocument(id=number, title=f"Document {number}", **values)  # pyright: ignore[reportArgumentType] -- keyword passthrough
        self.state.documents.append(made)
        return made

    async def version(self, document: int, content: bytes = FILE) -> int:
        writer = FakePaperless(self.state, user=ADMIN[0], allow_writes=True)
        upload = VersionUpload(filename="v.pdf", mime_type="application/pdf", content=content)
        _ = await writer.upload_document_version(document, upload)
        return self.state.tasks[-1].document_ids[0]

    async def delete(self, document: int, version: int | None = None) -> None:
        writer = FakePaperless(self.state, user=ADMIN[0], allow_writes=True)
        if version is None:
            await writer.delete_document(document)
        else:
            _ = await writer.delete_version(document, version)

    async def matches(self) -> list[tuple[int, int | None, bool]]:
        found = await self.finder.before(SHA, await self.finder.snapshot())
        return keys(found.matches)

    async def gaps(self) -> tuple[str, ...]:
        return (await self.finder.before(SHA, await self.finder.snapshot())).not_checked


def keys(matches: tuple[DuplicateMatch, ...]) -> list[tuple[int, int | None, bool]]:
    return [(m.document_id, m.version_id, m.in_trash) for m in matches]


def restrict(state: FakeState, permissions: set[str]) -> None:
    state.users = [
        replace(u, permissions=frozenset(permissions)) if u.username == READER[0] else u
        for u in state.users
    ]


# --- before sending ------------------------------------------------------------------------------


async def test_no_match_and_what_was_not_compared() -> None:
    setup = Setup()

    found = await setup.finder.before(SHA, await setup.finder.snapshot())

    assert (found.matches, found.count) == ((), 0)
    assert found.not_checked == (UNCOMPARED, OWN_TRASH, HIDDEN)


async def test_a_document_a_version_and_the_trash_are_found() -> None:
    setup = Setup()
    _ = setup.document(10)
    _ = setup.document(11, original=b"other")
    version = await setup.version(11)
    _ = setup.document(12)
    await setup.delete(12)
    _ = setup.document(13, original=b"third")
    gone = await setup.version(13)
    await setup.delete(13, gone)

    found = await setup.finder.before(SHA, await setup.finder.snapshot())

    assert keys(found.matches) == [
        (10, None, False),
        (11, version, False),
        (12, None, True),
        (13, gone, True),
    ]
    assert found.count == 4
    first, second, third, fourth = found.matches
    assert (first.title, first.web_url, first.deleted_at) == (
        "Document 10",
        f"{WEB}/documents/10/details",
        None,
    )
    assert second.web_url == f"{WEB}/documents/11/details"
    assert (third.web_url, third.title) == (None, "Document 12")
    assert third.deleted_at is not None
    assert (fourth.title, fourth.web_url) == ("Document 13", None)


async def test_another_file_is_no_match_in_any_place() -> None:
    setup = Setup()
    _ = setup.document(10, original=b"a")
    _ = await setup.version(10, b"b")
    gone = await setup.version(10, b"c")
    await setup.delete(10, gone)
    _ = setup.document(11, original=b"d")
    await setup.delete(11)

    assert await setup.matches() == []


async def test_the_checksum_is_compared_in_any_case() -> None:
    setup = Setup()
    _ = setup.document(10, original=b"other")
    version = await setup.version(10)
    [newest, *rest] = setup.state.documents[-1].versions
    setup.state.documents[-1].versions = [
        newest.model_copy(update={"checksum": SHA.upper()}),
        *[v.model_copy(update={"checksum": None}) for v in rest],
    ]

    assert await setup.matches() == [(10, version, False)]


async def test_the_versions_of_the_target_are_compared_whatever_the_scan_reached() -> None:
    setup = Setup(bounds=Bounds(version_pages=0, trash_pages=0))
    _ = setup.document(10)
    version = await setup.version(10, b"newer")
    same = await setup.version(10)
    own = await setup.fake.get_document(10)

    found = await setup.finder.before(SHA, await setup.finder.snapshot(), own=own)

    assert keys(found.matches) == [(10, None, False), (10, same, False)]
    assert version != same


async def test_what_is_found_in_two_places_counts_once() -> None:
    setup = Setup()
    _ = setup.document(10)
    version = await setup.version(10)
    own = await setup.fake.get_document(10)

    found = await setup.finder.before(SHA, await setup.finder.snapshot(), own=own)

    assert keys(found.matches) == [(10, None, False), (10, version, False)]
    assert found.count == 2


async def test_more_documents_than_a_page_are_counted() -> None:
    setup = Setup(bounds=Bounds(documents=2))
    for number in (10, 11, 12):
        _ = setup.document(number)

    found = await setup.finder.before(SHA, await setup.finder.snapshot())

    assert (len(found.matches), found.count) == (2, 3)


async def test_a_hidden_document_is_not_found() -> None:
    setup = Setup(READER[0])
    _ = setup.document(10)
    _ = setup.document(11, original=b"other")
    _ = await setup.version(11)
    _ = setup.document(12)
    await setup.delete(12)

    assert await setup.matches() == []


# --- bounds --------------------------------------------------------------------------------------


async def test_the_shipped_bounds() -> None:
    shipped = Bounds()

    assert shipped.version_pages * shipped.version_page == 5_000
    assert shipped.trash_pages * shipped.trash_page == 500
    assert shipped.trashed_versions == 20


async def test_only_the_most_recently_changed_documents_are_read_and_it_is_said() -> None:
    setup = Setup(bounds=Bounds(version_pages=2, version_page=1))
    _ = setup.document(10, original=b"first")
    old = await setup.version(10)
    for number in (11, 12):
        _ = setup.document(number, original=b"x%d" % number)
        _ = await setup.version(number, b"y%d" % number)

    assert (10, old, False) not in await setup.matches()
    assert "list_versions 3" not in setup.fake.calls
    assert (
        "Versions: only the 2 most recently changed of 5 documents were read." in await setup.gaps()
    )


async def test_a_scan_that_reaches_the_end_exactly_has_no_gap() -> None:
    setup = Setup(bounds=Bounds(version_pages=2, version_page=1, trash_pages=1, trash_page=1))
    _ = setup.document(10)
    setup.state.documents = setup.state.documents[:2]

    assert await setup.gaps() == (UNCOMPARED, OWN_TRASH, HIDDEN)


async def test_only_so_many_trash_entries_are_read_and_it_is_said() -> None:
    setup = Setup(bounds=Bounds(trash_pages=1, trash_page=1))
    for number in (10, 11):
        _ = setup.document(number)
        await setup.delete(number)

    assert await setup.matches() == [(11, None, True)]
    assert "Trash: only 1 of 2 entries were read." in await setup.gaps()


async def test_only_so_many_trashed_versions_are_read_and_it_is_said() -> None:
    setup = Setup(bounds=Bounds(trashed_versions=1))
    _ = setup.document(10, original=b"first")
    for _ in range(2):
        version = await setup.version(10)
        await setup.delete(10, version)

    assert len(await setup.matches()) == 1
    assert setup.fake.calls.count("get_document_metadata") == 1
    assert "Trash: only 1 of 2 deleted versions were read." in await setup.gaps()


@pytest.mark.parametrize("what", ["list_versions 2", "list_trash 2"])
async def test_a_page_that_is_gone_meanwhile_ends_the_scan(what: str) -> None:
    setup = Setup(bounds=Bounds(version_page=1, trash_page=1))
    for number in (10, 11):
        _ = setup.document(number)
        await setup.delete(number)
    setup.fake.errors[what] = NotFound("Page 2 is beyond the last page.")

    assert await setup.gaps() == (UNCOMPARED, OWN_TRASH, HIDDEN)


@pytest.mark.parametrize("what", ["list_versions", "list_trash"])
async def test_a_first_page_that_is_not_found_is_an_error(what: str) -> None:
    setup = Setup()
    setup.fake.errors[what] = NotFound("no such endpoint")

    with pytest.raises(NotFound):
        _ = await setup.finder.snapshot()


# --- what the user may not read, and outages -----------------------------------------------------


@pytest.mark.parametrize(
    ("what", "gap"),
    [
        ("search_documents", "Documents: this user may not list them."),
        ("list_versions", "Versions: this user may not list them."),
        ("list_trash", "Trash: this user may not list it."),
        (
            "get_document_metadata",
            "Trash: deleted versions this user could not read: 1.",
        ),
    ],
)
async def test_what_the_user_may_not_read_is_named_and_the_rest_is_checked(
    what: str, gap: str
) -> None:
    setup = Setup()
    _ = setup.document(10)
    _ = setup.document(11, original=b"other")
    version = await setup.version(11)
    _ = setup.document(12, original=b"third")
    gone = await setup.version(12)
    await setup.delete(12, gone)
    setup.fake.errors[what] = DENIED

    found = await setup.finder.before(SHA, await setup.finder.snapshot())

    assert gap in found.not_checked
    assert len(found.matches) == 2
    assert ((11, version, False) in keys(found.matches)) == (what != "list_versions")


async def test_a_trashed_version_that_vanished_is_no_match_and_named() -> None:
    setup = Setup()
    _ = setup.document(10)
    gone = await setup.version(10, b"x")
    await setup.delete(10, gone)
    setup.fake.errors["get_document_metadata"] = NotFound("gone")

    assert any("deleted versions" in gap for gap in await setup.gaps())


@pytest.mark.parametrize(
    "what", ["search_documents", "list_versions", "list_trash", "get_document_metadata"]
)
@pytest.mark.parametrize("error", [DOWN, UpstreamUnavailable("HTTP 500", status=500)])
async def test_an_outage_is_never_no_duplicates(what: str, error: Exception) -> None:
    setup = Setup()
    _ = setup.document(10)
    gone = await setup.version(10, b"x")
    await setup.delete(10, gone)
    setup.fake.errors[what] = error

    with pytest.raises(type(error)):
        _ = await setup.matches()


# --- after consumption ---------------------------------------------------------------------------


async def test_paperless_own_list_is_reported_for_a_document() -> None:
    setup = Setup()
    for number in (10, 11, 12):
        _ = setup.document(number)
    await setup.delete(12)

    report = await setup.finder.after(10)

    assert report.after is not None
    assert keys(report.after) == [(11, None, False), (12, None, True)]
    assert (report.after[0].title, report.after[0].web_url) == (
        "Document 11",
        f"{WEB}/documents/11/details",
    )
    assert report.after[1].deleted_at is not None
    assert report.after[1].web_url is None
    assert report.not_checked == (VERSIONS_AFTER, HIDDEN)
    assert report.rejected_for is None


async def test_a_version_is_compared_by_this_server() -> None:
    setup = Setup()
    _ = setup.document(10, original=b"other")
    first = await setup.version(10)
    second = await setup.version(10)
    _ = setup.document(11)

    report = await setup.finder.after(second)

    assert report.after is not None
    assert keys(report.after) == [(11, None, False), (10, first, False)]
    assert report.not_checked == (VERSION_AFTER, HIDDEN)


async def test_a_version_equal_to_its_own_document_names_the_document() -> None:
    setup = Setup()
    _ = setup.document(10)
    version = await setup.version(10)

    report = await setup.finder.after(version)

    assert report.after is not None
    assert keys(report.after) == [(10, None, False)]


async def test_a_version_without_a_checksum_or_gone_again_is_not_readable() -> None:
    setup = Setup()
    _ = setup.document(10)
    version = await setup.version(10)
    document = setup.state.documents[-1]
    document.versions = [v.model_copy(update={"checksum": None}) for v in document.versions]

    assert (await setup.finder.after(version)).after is None
    await setup.delete(10, version)
    assert (await setup.finder.after(version)).after is None


@pytest.mark.parametrize("error", [DOWN, DENIED, RuntimeError("bug"), NotFound("gone")])
async def test_reading_the_duplicates_afterwards_never_fails(error: Exception) -> None:
    setup = Setup()
    _ = setup.document(10)
    setup.fake.errors["get_duplicates"] = error

    report = await setup.finder.after(10)

    assert (report.after, report.not_checked) == (None, (UNREAD_AFTER,))


# --- rejected ------------------------------------------------------------------------------------


async def test_a_rejection_names_the_document_the_version_or_the_trash() -> None:
    setup = Setup()
    _ = setup.document(10)
    version = await setup.version(10)
    deleted = await setup.version(10)
    await setup.delete(10, deleted)
    _ = setup.document(50)
    await setup.delete(50)

    document, message = await setup.finder.rejected(10)
    assert document.rejected_for is not None
    assert keys((document.rejected_for,)) == [(10, None, False)]
    assert document.rejected_for.web_url == f"{WEB}/documents/10/details"
    assert message == (
        "Paperless rejected the file: it already holds the same file, document 10 'Document 10'."
    )
    for number, key, words in (
        (version, (10, version, False), f"version {version} of document 10 'Document 10'."),
        (
            deleted,
            (10, deleted, True),
            f"version {deleted} of document 10 'Document 10' in the trash.",
        ),
        (50, (50, None, True), "document 50 'Document 50' in the trash."),
    ):
        found, message = await setup.finder.rejected(number)
        assert found.rejected_for is not None
        assert keys((found.rejected_for,)) == [key]
        assert message.endswith(words)
        if key[2] and key[1] is None:
            assert found.rejected_for.deleted_at is not None


async def test_a_trashed_document_whose_entry_is_not_listed_has_no_title() -> None:
    setup = Setup(bounds=Bounds(trash_pages=0))
    _ = setup.document(11)
    await setup.delete(11)

    found, message = await setup.finder.rejected(11)

    assert found.rejected_for is not None
    assert (found.rejected_for.title, found.rejected_for.in_trash) == (None, True)
    assert message.endswith("the same file, document 11 in the trash.")


async def test_a_trash_the_user_may_not_list_leaves_the_title_out() -> None:
    setup = Setup()
    _ = setup.document(11)
    await setup.delete(11)
    setup.fake.errors["list_trash"] = DENIED

    found, _ = await setup.finder.rejected(11)

    assert found.rejected_for is not None
    assert found.rejected_for.title is None


async def test_a_hidden_or_missing_document_is_never_named() -> None:
    setup = Setup(READER[0])
    _ = setup.document(4711)

    for number in (4711, 99_999):
        report, message = await setup.finder.rejected(number)
        assert report.rejected_for is None
        assert message == (
            "Paperless rejected the file: it already holds the same file, in a document that this "
            "user cannot see or that no longer exists."
        )
        assert str(number) not in message + report.model_dump_json()


@pytest.mark.parametrize("error", [DOWN, RuntimeError("bug")])
async def test_an_outage_while_naming_the_document_is_said(error: Exception) -> None:
    setup = Setup()
    _ = setup.document(10)
    setup.fake.errors["get_root"] = error

    report, message = await setup.finder.rejected(10)

    assert report.rejected_for is None
    assert message == UNREAD_REJECTED
    assert "10" not in message


async def test_hostile_titles_are_quoted_and_cut() -> None:
    setup = Setup()
    document = setup.document(10)
    document.title = "x" * 200 + chr(0x202E)

    _, message = await setup.finder.rejected(10)

    assert "x" * 60 not in message
    assert "…'" in message
