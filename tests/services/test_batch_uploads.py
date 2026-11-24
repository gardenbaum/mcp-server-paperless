"""BatchUploadService: several files in one call, each with its own report (decision-50)."""

import asyncio
import hashlib
import os
from typing import TYPE_CHECKING, override

import pytest
from e2e.helpers import pdf
from fakes.accounts import ADMIN, READER, READER_PERMISSIONS
from fakes.paperless import FakeDocument, FakePaperless, FakeState, FakeUser, seeded

from mcp_server_paperless.domain.documents import MAX_UPLOAD_LIMIT, UploadMetadata
from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    UpstreamUnreachable,
    Validation,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.batch_uploads import (
    CHANGED,
    NOT_TAKEN,
    ONE_BY_ONE,
    UNEXPECTED,
    UNKNOWN,
    UNREAD,
    BatchUploadService,
    Limits,
    _File,  # pyright: ignore[reportPrivateUsage] -- for the seam of the tests below
)
from mcp_server_paperless.services.duplicates import (
    HIDDEN_REJECTED,
    REJECTED_FOR,
    UNCOMPARED,
    VERSIONS_AFTER,
    CheckMovedError,
    DuplicateFinder,
)
from mcp_server_paperless.services.tasks import TaskService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.uploads import UploadService

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from mcp_server_paperless.domain.documents import DocumentPage, DocumentQuery, DocumentUpload
    from mcp_server_paperless.domain.tasks import TaskStatus
    from mcp_server_paperless.domain.trash import TrashPage
    from mcp_server_paperless.domain.uploads import UploadBatch

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-50")]

WEB = "https://paperless.example"
DOWN = UpstreamTimeout("Paperless did not respond in time.", hint="Retry later.")


class Scripted(FakePaperless):
    """The fake: what happens before each upload, search or task read, by its number from 1."""

    def __init__(self, state: FakeState, user: str = ADMIN[0]) -> None:
        super().__init__(state, user=user, allow_writes=True)
        self.sent: list[DocumentUpload] = []
        self.uploads: int = 0
        self.searches: int = 0
        self.reads: int = 0
        self.trash_error: Exception | None = None
        self.on_upload: dict[int, Callable[[], None]] = {}
        self.on_search: dict[int, Callable[[], None]] = {}
        self.on_read: Callable[[], None] = lambda: None
        self.hang: bool = False
        self.pending: int = 0
        self.outcome: dict[str, object] = {}

    @override
    async def upload_document(self, upload: DocumentUpload) -> str:
        self.uploads += 1
        self.on_upload.get(self.uploads, lambda: None)()
        self.sent.append(upload)
        return await super().upload_document(upload)

    @override
    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        self.searches += 1
        self.on_search.get(self.searches, lambda: None)()
        return await super().search_documents(query, page=page, page_size=page_size)

    @override
    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        if self.trash_error:
            raise self.trash_error
        return await super().read_trash(entries=entries, max_bytes=max_bytes)

    @override
    async def get_task(self, task_id: str) -> TaskStatus:
        self.reads += 1
        self.on_read()
        if self.hang:
            await asyncio.sleep(30)
        status = await super().get_task(task_id)
        if self.reads <= self.pending:
            return status.model_copy(update={"state": "started", "document_ids": ()})
        return status.model_copy(update=self.outcome)


class Clock:
    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay

    def skip(self, seconds: float) -> Callable[[], None]:
        def act() -> None:
            self.now += seconds

        return act


def raising(error: Exception) -> Callable[[], None]:
    def act() -> None:
        raise error

    return act


class Setup:
    def __init__(  # noqa: PLR0913 -- one keyword per thing a test varies
        self,
        root: Path,
        *,
        limits: Limits | None = None,
        max_bytes: int = 10_000,
        user: str = ADMIN[0],
        state: FakeState | None = None,
        kind: type[BatchUploadService] = BatchUploadService,
    ) -> None:
        self.root: Path = root
        root.mkdir(exist_ok=True)
        self.state: FakeState = state or seeded()
        self.fake: Scripted = Scripted(self.state, user)
        self.clock: Clock = Clock()
        finder = DuplicateFinder(self.fake, self.fake, web_url=WEB)
        tasks = TaskService(self.fake, sleep=self.clock.sleep, clock=self.clock, duplicates=finder)
        uploads = UploadService(
            self.fake,
            self.fake,
            TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0),
            tasks,
            finder,
            web_url=WEB,
            max_bytes=max_bytes,
        )
        self.service: BatchUploadService = kind(
            uploads,
            self.fake,
            tasks,
            finder,
            web_url=WEB,
            max_bytes=max_bytes,
            upload_dirs=[str(root)],
            limits=limits,
            clock=self.clock,
        )

    def file(self, name: str, content: bytes | None = None) -> str:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(pdf(name) if content is None else content)
        return str(path)

    def known(self, number: int, content: bytes) -> None:
        self.state.documents.append(
            FakeDocument(id=number, title=f"Document {number}", owner=2, original=content)
        )

    async def directory(self, *, wait: float = 0, **given: object) -> UploadBatch:
        return await self.service.from_directory(
            str(self.root),
            UploadMetadata(),
            wait_seconds=wait,
            **given,  # pyright: ignore[reportArgumentType] -- keyword passthrough
        )

    async def paths(self, *paths: str, wait: float = 0, dry_run: bool = False) -> UploadBatch:
        return await self.service.from_paths(
            paths, UploadMetadata(), wait_seconds=wait, dry_run=dry_run
        )

    def rows(self, batch: UploadBatch) -> list[tuple[str, str]]:
        return [(f.path.removeprefix(f"{self.root}/"), f.status) for f in batch.files]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "allowed"


# --- outcomes ------------------------------------------------------------------------------------


async def test_the_files_of_a_directory_are_uploaded_one_after_the_other(root: Path) -> None:
    setup = Setup(root)
    for name in ("b.pdf", "a.pdf", "c.txt"):
        _ = setup.file(name, b"plain text" if name == "c.txt" else None)

    batch = await setup.directory()

    assert setup.rows(batch) == [(n, "uploaded") for n in ("a.pdf", "b.pdf", "c.txt")]
    assert [u.filename for u in setup.fake.sent] == ["a.pdf", "b.pdf", "c.txt"]
    assert [u.content for u in setup.fake.sent] == [pdf("a.pdf"), pdf("b.pdf"), b"plain text"]
    assert batch.counts == {"uploaded": 3}
    assert (batch.left_out, batch.left_out_count, batch.next_after) == ((), 0, None)
    assert (batch.directories, batch.notes, batch.dry_run) == ((), (), False)
    first, _, last = batch.files
    task = setup.state.tasks[0]
    assert (first.task_id, first.state, first.document_id) == (
        task.task_id,
        "success",
        task.document_ids[0],
    )
    assert first.web_url == f"{WEB}/documents/{first.document_id}/details"
    assert (last.size_bytes, last.mime_type, last.reason) == (10, "text/plain", None)
    assert first.path == f"{root}/a.pdf"


async def test_every_outcome_in_one_call(root: Path) -> None:
    setup = Setup(root)
    known = pdf("known")
    setup.known(50, known)
    _ = setup.file("a-ok.pdf")
    _ = setup.file("b-known.pdf", known)
    _ = setup.file("c-empty.pdf", b"")
    _ = setup.file("d-large.pdf", b"%PDF-" + bytes(10_000))
    _ = setup.file("e-type.exe", b"MZ\x90\x00" + bytes(64))
    _ = setup.file("f-fails.pdf")
    _ = setup.file("g-ok.pdf")
    os.mkfifo(root / "h-pipe")
    setup.fake.on_upload[2] = raising(Validation("Paperless rejected the request."))

    batch = await setup.directory()

    assert setup.rows(batch) == [
        ("a-ok.pdf", "uploaded"),
        ("b-known.pdf", "duplicate"),
        ("c-empty.pdf", "refused"),
        ("d-large.pdf", "refused"),
        ("e-type.exe", "refused"),
        ("f-fails.pdf", "failed"),
        ("g-ok.pdf", "uploaded"),
        ("h-pipe", "refused"),
    ]
    reasons = [f.reason or "" for f in batch.files]
    assert reasons[1] == "The same file is already in Paperless: document 50 'Document 50'."
    assert "empty" in reasons[2]
    assert "larger than the limit of 10000 bytes" in reasons[3]
    assert "not a type Paperless can consume" in reasons[4]
    assert reasons[5] == "Paperless rejected the request."
    assert reasons[7] == "No file was found at that path inside the allowed directories."
    assert batch.counts == {"uploaded": 2, "duplicate": 1, "refused": 4, "failed": 1}
    duplicate = batch.files[1].duplicates
    assert duplicate is not None
    assert [m.document_id for m in duplicate.before] == [50]
    assert (duplicate.before_count, duplicate.not_checked) == (1, ())
    assert UNCOMPARED in batch.not_checked
    assert VERSIONS_AFTER in batch.not_checked
    assert batch.notes == (ONE_BY_ONE,)


@pytest.mark.parametrize("failing", [1, 2, 3])
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PermissionDenied("Not allowed."), "Not allowed."),
        (RuntimeError("secret detail"), UNKNOWN.format(reason="unexpected error")),
        (DOWN, UNKNOWN.format(reason=DOWN.message)),
        (
            PaperlessMcpError("Paperless answered with HTTP 418."),
            UNKNOWN.format(reason="Paperless answered with HTTP 418."),
        ),
    ],
)
async def test_one_failing_upload_neither_stops_nor_hides_the_others(
    root: Path, failing: int, error: Exception, reason: str
) -> None:
    setup = Setup(root)
    paths = [setup.file(f"{n}.pdf") for n in (1, 2, 3)]
    setup.fake.on_upload[failing] = raising(error)

    batch = await setup.paths(*paths)

    expected = ["uploaded"] * 3
    expected[failing - 1] = "failed"
    assert [f.status for f in batch.files] == expected
    assert batch.files[failing - 1].reason == reason
    assert len(setup.state.tasks) == 2


async def test_an_unexpected_error_of_the_check_is_one_report(
    root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    setup = Setup(root)
    paths = [setup.file(f"{n}.pdf") for n in (1, 2, 3)]
    # Three lookups before the snapshot, then one per file.
    setup.fake.on_search[5] = raising(RuntimeError("secret detail"))

    batch = await setup.paths(*paths)

    assert [f.status for f in batch.files] == ["uploaded", "failed", "uploaded"]
    assert batch.files[1].reason == UNEXPECTED
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text


async def test_an_unexpected_error_of_the_reading_is_one_report(root: Path) -> None:
    class Odd(BatchUploadService):
        @override
        def _read(self, file: _File) -> tuple[str, bytes]:
            if "2.pdf" in file.path:
                raise RuntimeError
            return super()._read(file)

    setup = Setup(root, kind=Odd)
    paths = [setup.file(f"{n}.pdf") for n in (1, 2, 3)]

    batch = await setup.paths(*paths)

    assert [f.status for f in batch.files] == ["uploaded", "failed", "uploaded"]
    assert batch.files[1].reason == UNEXPECTED


async def test_a_dry_run_checks_everything_and_sends_nothing(root: Path) -> None:
    setup = Setup(root)
    setup.known(50, pdf("known"))
    _ = setup.file("a.pdf")
    _ = setup.file("b.pdf", pdf("known"))
    _ = setup.file("c.pdf", pdf("a.pdf"))
    _ = setup.file("d.pdf", b"")

    batch = await setup.directory(dry_run=True)

    assert setup.rows(batch) == [
        ("a.pdf", "would_upload"),
        ("b.pdf", "duplicate"),
        ("c.pdf", "duplicate"),
        ("d.pdf", "refused"),
    ]
    assert batch.dry_run
    assert setup.fake.sent == []
    assert batch.files[0].task_id is None


async def test_the_metadata_goes_to_every_file(root: Path) -> None:
    setup = Setup(root)
    paths = [setup.file("a.pdf"), setup.file("b.pdf")]

    _ = await setup.service.from_paths(
        paths, UploadMetadata(correspondent="e2e-acme", tags=("e2e-inbox",))
    )

    assert [(u.correspondent, u.tags) for u in setup.fake.sent] == [(1, (4,)), (1, (4,))]
    assert [u.title for u in setup.fake.sent] == [None, None]


async def test_unknown_metadata_refuses_the_whole_call(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf")

    with pytest.raises(NotFound, match="nope"):
        _ = await setup.service.from_directory(str(root), UploadMetadata(correspondent="nope"))
    assert setup.fake.sent == []


# --- duplicates ----------------------------------------------------------------------------------


async def test_the_same_content_twice_in_a_call_is_sent_once(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf", pdf("x"))
    _ = setup.file("b.pdf", pdf("x"))
    (root / "c.pdf").symlink_to(root / "a.pdf")

    batch = await setup.directory()

    assert [f.status for f in batch.files] == ["uploaded", "duplicate", "duplicate"]
    assert batch.files[1].reason == (
        f"The same content as {root}/a.pdf, which comes earlier in this call."
    )
    assert len(setup.fake.sent) == 1


@pytest.mark.parametrize(
    ("error", "second"),
    [(DOWN, "duplicate"), (PermissionDenied("no"), "uploaded"), (RuntimeError(), "duplicate")],
)
async def test_a_twin_is_sent_only_if_the_first_certainly_was_not(
    root: Path, error: Exception, second: str
) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf", pdf("x"))
    _ = setup.file("b.pdf", pdf("x"))
    setup.fake.on_upload[1] = raising(error)

    batch = await setup.directory()

    assert [f.status for f in batch.files] == ["failed", second]


def test_the_errors_after_which_a_file_certainly_was_not_taken() -> None:
    assert not any(issubclass(UpstreamUnreachable, error) for error in NOT_TAKEN)
    assert PaperlessMcpError not in NOT_TAKEN


async def test_a_rejected_file_is_a_duplicate_and_a_failed_one_is_not(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf")
    setup.known(50, b"other")
    setup.fake.outcome = {"state": "failure", "duplicate_of": 50, "document_ids": ()}
    rejected = await setup.directory()
    setup.fake.outcome = {"state": "failure", "duplicate_of": 999, "document_ids": ()}
    _ = setup.file("a.pdf", pdf("second"))
    hidden = await setup.directory()
    setup.fake.outcome = {"state": "failure", "message": "Broken file.", "document_ids": ()}
    _ = setup.file("a.pdf", pdf("third"))
    failed = await setup.directory()
    setup.fake.outcome = {"state": "revoked", "document_ids": ()}
    _ = setup.file("a.pdf", pdf("fourth"))
    revoked = await setup.directory()

    [file] = rejected.files
    assert (file.status, file.state) == ("duplicate", "failure")
    assert file.reason == REJECTED_FOR.format(where="document 50 'Document 50'")
    assert file.duplicates is not None
    assert file.duplicates.rejected_for is not None
    assert rejected.counts == {"duplicate": 1}
    assert rejected.notes == ()
    assert (hidden.files[0].status, hidden.files[0].reason) == ("duplicate", HIDDEN_REJECTED)
    assert (failed.files[0].status, failed.files[0].reason) == ("failed", "Broken file.")
    assert failed.files[0].task_id
    assert revoked.files[0].status == "failed"


async def test_a_check_that_cannot_run_stops_the_whole_call(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf")
    setup.fake.trash_error = DOWN

    with pytest.raises(UpstreamTimeout):
        _ = await setup.directory()
    assert setup.fake.sent == []


async def test_a_check_that_fails_later_fails_that_file_only(root: Path) -> None:
    setup = Setup(root)
    paths = [setup.file(f"{n}.pdf") for n in (1, 2, 3)]
    setup.fake.on_search[5] = raising(CheckMovedError("a page was gone"))
    setup.fake.on_search[6] = raising(CheckMovedError("a page was gone"))

    batch = await setup.paths(*paths)

    assert [f.status for f in batch.files] == ["uploaded", "failed", "uploaded"]
    assert "did not add up" in (batch.files[1].reason or "")
    assert len(setup.fake.sent) == 2


async def test_files_that_are_all_refused_need_no_check(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf", b"")
    setup.fake.trash_error = DOWN

    batch = await setup.directory()

    assert setup.rows(batch) == [("a.pdf", "refused")]
    assert (setup.fake.searches, batch.not_checked) == (0, ())


async def test_a_restricted_user_gets_the_gaps_once_and_no_hidden_document(root: Path) -> None:
    state = seeded()
    state.users = [
        FakeUser(
            id=u.id,
            username=u.username,
            permissions=frozenset({*READER_PERMISSIONS, "add_document"}),
        )
        if u.username == READER[0]
        else u
        for u in state.users
    ]
    setup = Setup(root, user=READER[0], state=state)
    setup.known(4711, pdf("known"))  # owned by the admin
    _ = setup.file("a.pdf", pdf("known"))
    _ = setup.file("b.pdf")

    batch = await setup.directory()

    assert [f.status for f in batch.files] == ["uploaded", "uploaded"]
    assert "4711" not in batch.model_dump_json()
    assert len(set(batch.not_checked)) == len(batch.not_checked)


# --- limits --------------------------------------------------------------------------------------


async def test_the_approved_limits() -> None:
    assert Limits() == Limits(
        files=25, total_bytes=100 * 1024 * 1024, looking=10, starting=40, ending=55
    )
    assert Limits().total_bytes == MAX_UPLOAD_LIMIT


async def test_two_calls_cover_a_directory_once(root: Path) -> None:
    setup = Setup(root, limits=Limits(files=2))
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        _ = setup.file(name)

    first = await setup.directory()
    second = await setup.directory(after=first.next_after)

    assert setup.rows(first) == [("a.pdf", "uploaded"), ("b.pdf", "uploaded")]
    assert (first.left_out, first.left_out_count) == ((f"{root}/c.pdf",), 1)
    assert first.next_after == f"{root}/b.pdf"
    assert first.notes == (
        "Files not started: 1 (at most 2 files per call); call again with after=next_after.",
    )
    assert setup.rows(second) == [("c.pdf", "uploaded")]
    assert (second.next_after, second.left_out_count) == (None, 0)


async def test_exactly_the_number_of_files_leaves_nothing_out(root: Path) -> None:
    setup = Setup(root, limits=Limits(files=2))
    _ = setup.file("a.pdf")
    _ = setup.file("b.pdf", b"")  # counts too

    batch = await setup.directory()

    assert (len(batch.files), batch.left_out_count) == (2, 0)


async def test_at_most_25_names_are_left_out_by_name(root: Path) -> None:
    setup = Setup(root, limits=Limits(files=1))
    for number in range(28):
        _ = setup.file(f"{number:02}.txt", f"text {number}".encode())

    batch = await setup.directory()

    assert (len(batch.left_out), batch.left_out_count) == (25, 27)


async def test_the_total_is_exact_and_counts_what_was_read(root: Path) -> None:
    size = 100
    contents = [f"{n}".encode() * size for n in (1, 2, 3)]
    setup = Setup(root, limits=Limits(total_bytes=2 * size))
    for number, content in enumerate(contents, 1):
        _ = setup.file(f"{number}.txt", content)

    batch = await setup.directory()

    assert setup.rows(batch) == [("1.txt", "uploaded"), ("2.txt", "uploaded")]
    assert batch.next_after == f"{root}/2.txt"
    assert "at most 200 bytes per call" in batch.notes[0]

    small = Setup(root, limits=Limits(total_bytes=2 * size - 1))
    assert len((await small.directory()).files) == 1


async def test_a_refused_file_counts_if_its_bytes_were_read(root: Path) -> None:
    setup = Setup(root, limits=Limits(total_bytes=150))
    _ = setup.file("a.exe", b"MZ\x90\x00" + bytes(96))
    _ = setup.file("b.txt", b"x" * 100)

    batch = await setup.directory()

    assert setup.rows(batch) == [("a.exe", "refused")]
    assert batch.left_out_count == 1


async def test_no_file_is_started_late_and_the_rest_is_left_out(root: Path) -> None:
    setup = Setup(root)
    for name in ("a.pdf", "b.pdf", "c.pdf", "d.pdf"):
        _ = setup.file(name, b"" if name == "d.pdf" else None)
    setup.fake.on_upload[2] = setup.clock.skip(40.5)

    batch = await setup.directory()

    assert setup.rows(batch) == [("a.pdf", "uploaded"), ("b.pdf", "uploaded")]
    # d.pdf was looked at already; it is reported by the next call.
    assert batch.left_out == (f"{root}/c.pdf", f"{root}/d.pdf")
    assert batch.next_after == f"{root}/b.pdf"
    assert "no file is started after 40 seconds" in batch.notes[0]


async def test_the_first_file_is_always_tried_so_every_call_gets_on(root: Path) -> None:
    setup = Setup(root)
    for name in ("a.pdf", "b.pdf"):
        _ = setup.file(name)
    setup.fake.on_search[1] = setup.clock.skip(100)

    batch = await setup.directory()

    assert setup.rows(batch) == [("a.pdf", "uploaded")]
    assert batch.next_after == f"{root}/a.pdf"


async def test_reading_that_takes_too_long_is_cut_and_gets_on(root: Path) -> None:
    class Slow(BatchUploadService):
        @override
        def _read(self, file: _File) -> tuple[str, bytes]:
            setup.clock.now += 6
            return super()._read(file)

    setup = Setup(root, kind=Slow)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        _ = setup.file(name)

    batch = await setup.directory()

    assert setup.rows(batch) == [("a.pdf", "uploaded"), ("b.pdf", "uploaded")]
    assert "reading took longer than 10 seconds" in batch.notes[0]


async def test_paths_are_continued_by_name(root: Path) -> None:
    setup = Setup(root, limits=Limits(total_bytes=150))
    paths = [setup.file(f"{n}.txt", f"{n}".encode() * 100) for n in (2, 1)]

    batch = await setup.paths(*paths)

    assert [f.path for f in batch.files] == paths[:1]  # in the order given
    assert (batch.left_out, batch.next_after) == ((paths[1],), None)
    assert batch.notes[0].endswith("call again with the paths in left_out.")


async def test_repeated_paths_count_once_and_too_many_are_refused(root: Path) -> None:
    setup = Setup(root, limits=Limits(files=2))
    one, two = setup.file("a.pdf"), setup.file("b.pdf")

    batch = await setup.paths(one, one, two)
    assert [f.path for f in batch.files] == [one, two]
    with pytest.raises(Validation, match="1 to 2 paths, not 3"):
        _ = await setup.paths(one, two, setup.file("c.pdf"))
    with pytest.raises(Validation, match="not 0"):
        _ = await setup.paths()


async def test_an_outage_is_not_waited_for_by_every_file(root: Path) -> None:
    setup = Setup(root)
    for number in range(5):
        _ = setup.file(f"{number}.pdf")
    setup.fake.on_upload[2] = raising(DOWN)
    setup.fake.on_search[8] = raising(UpstreamUnavailable("Down.", hint="Later."))

    batch = await setup.directory()

    assert [f.status for f in batch.files] == ["uploaded", "failed", "failed"]
    assert batch.files[2].reason == "Down. Hint: Later."
    assert batch.left_out_count == 2
    assert batch.next_after == f"{root}/2.pdf"
    assert "Paperless did not answer for 2 files in a row" in batch.notes[0]


async def test_two_outages_with_another_outcome_between_are_not_in_a_row(root: Path) -> None:
    setup = Setup(root)
    for number in range(5):
        _ = setup.file(f"{number}.pdf")
    setup.fake.on_upload[1] = raising(DOWN)
    setup.fake.on_upload[2] = raising(PermissionDenied("no"))
    setup.fake.on_upload[3] = raising(DOWN)
    setup.fake.on_upload[4] = raising(RuntimeError())

    batch = await setup.directory()

    assert [f.status for f in batch.files] == [*["failed"] * 4, "uploaded"]


# --- following -----------------------------------------------------------------------------------


async def test_one_wait_covers_all_files(root: Path) -> None:
    setup = Setup(root)
    for name in ("a.pdf", "b.pdf"):
        _ = setup.file(name)
    setup.fake.pending = 100
    start = setup.clock.now

    batch = await setup.directory(wait=10)

    assert [f.state for f in batch.files] == ["started", "started"]
    assert [f.status for f in batch.files] == ["uploaded", "uploaded"]
    assert setup.clock.now - start == 10


async def test_the_wait_ends_with_the_task_and_is_cut_to_the_longest(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf")
    setup.fake.pending = 2
    start = setup.clock.now

    assert (await setup.directory(wait=1000)).files[0].state == "success"
    assert setup.clock.now - start == 4

    setup.fake.reads, setup.fake.pending = 0, 1000
    _ = setup.file("a.pdf", pdf("other"))
    start = setup.clock.now
    _ = await setup.directory(wait=1000)
    assert setup.clock.now - start == 30


async def test_no_task_is_read_when_the_call_must_end(root: Path) -> None:
    setup = Setup(root)
    for name in ("a.pdf", "b.pdf"):
        _ = setup.file(name)
    setup.fake.on_read = setup.clock.skip(56)

    batch = await setup.directory()

    first, second = batch.files
    assert first.state == "success"
    assert (second.state, second.status, second.reason) == ("unknown", "uploaded", UNREAD)
    assert second.task_id
    assert setup.fake.reads == 1


async def test_a_task_that_cannot_be_read_in_time_is_unknown(root: Path) -> None:
    setup = Setup(root, limits=Limits(ending=0.05))
    _ = setup.file("a.pdf")
    setup.fake.hang = True

    batch = await setup.directory()

    [file] = batch.files
    assert (file.state, file.reason) == ("unknown", UNREAD)


async def test_an_unreadable_task_is_unknown_and_the_file_counts_as_uploaded(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf")
    setup.fake.on_read = raising(PermissionDenied("No tasks."))

    [file] = (await setup.directory()).files

    assert (file.status, file.state) == ("uploaded", "unknown")
    assert "No tasks." in (file.reason or "")


# --- the directory -------------------------------------------------------------------------------


async def test_what_is_not_read_is_named_and_dot_names_are_only_counted(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("sub/a.pdf")
    _ = setup.file(".secret/b.pdf")
    _ = setup.file(".env")

    batch = await setup.directory()

    assert batch.files == ()
    assert batch.directories == (f"{root}/sub",)
    assert batch.notes == (
        "Subdirectories not read: 1. recursive=true reads them.",
        "Entries left out because their name starts with a dot: 2.",
    )
    assert "secret" not in batch.model_dump_json()
    recursive = await setup.directory(recursive=True)
    assert setup.rows(recursive) == [("sub/a.pdf", "uploaded")]
    assert recursive.directories == ()


async def test_names_that_hide_something_are_refused_and_escaped(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("a\u202eb.pdf", b"text")
    _ = setup.file("c\nd.pdf", b"text")

    batch = await setup.directory()

    assert setup.rows(batch) == [("a\\u202eb.pdf", "refused"), ("c\\nd.pdf", "refused")]


@pytest.mark.parametrize("after", ["/etc", "b.pdf", ""])
async def test_a_cursor_of_another_directory_is_refused(root: Path, after: str) -> None:
    setup = Setup(root)

    with pytest.raises(Validation, match="after"):
        _ = await setup.directory(after=after)


async def test_a_cursor_is_text_only(root: Path) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf")
    _ = setup.file("c.pdf")

    batch = await setup.directory(after=f"{root}/../nothing")  # sorts before every name

    assert len(batch.files) == 2
    assert setup.rows(await setup.directory(after=f"{root}/b")) == [("c.pdf", "duplicate")]


@pytest.mark.parametrize("change", ["swap", "grow", "remove", "link"])
async def test_a_file_that_changes_between_the_two_readings_is_not_sent(
    root: Path, change: str, tmp_path: Path
) -> None:
    setup = Setup(root)
    _ = setup.file("a.pdf")
    _ = setup.file("b.pdf")
    target = root / "b.pdf"
    secret = tmp_path / "secret.pdf"
    _ = secret.write_bytes(pdf("secret"))

    def act() -> None:
        if change == "swap":
            target.unlink()
            target.symlink_to(secret)
        elif change == "grow":
            _ = target.write_bytes(pdf("b.pdf") + b"more")
        elif change == "remove":
            target.unlink()
        else:
            target.unlink()
            os.link(secret, target)

    setup.fake.on_upload[1] = act
    batch = await setup.directory()

    assert [u.filename for u in setup.fake.sent] == ["a.pdf"]
    file = batch.files[1]
    if change == "grow":
        assert (file.status, file.reason) == ("failed", CHANGED)
    else:
        assert file.status == "refused"
        assert "No file was found" in (file.reason or "")


async def test_a_file_swapped_after_the_listing_is_refused(root: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.pdf"
    _ = secret.write_bytes(pdf("secret"))

    class Swapping(BatchUploadService):
        @override
        def _read(self, file: _File) -> tuple[str, bytes]:
            (root / "a.pdf").unlink()
            (root / "a.pdf").symlink_to(secret)
            return super()._read(file)

    setup = Setup(root, kind=Swapping)
    _ = setup.file("a.pdf")

    batch = await setup.directory()

    assert setup.rows(batch) == [("a.pdf", "refused")]
    assert "secret" not in batch.model_dump_json()


async def test_the_hash_is_of_what_is_sent(root: Path) -> None:
    setup = Setup(root)
    content = pdf("x")
    _ = setup.file("a.pdf", content)
    setup.known(50, b"else")

    _ = await setup.directory()

    assert [d.id for d in setup.state.documents if d.original == content]
    assert hashlib.sha256(setup.fake.sent[0].content).digest() == hashlib.sha256(content).digest()
