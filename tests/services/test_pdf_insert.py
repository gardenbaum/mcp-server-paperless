"""PageInserter: what is checked, what is written, and every way an insertion can end."""

import asyncio
import gc
import weakref
from dataclasses import replace
from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakeFile, FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.documents import CustomFieldValue, DocumentVersion
from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UnexpectedAnswer,
    UpstreamTimeout,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.domain.rendering import Assembled, AssembleRequest, RenderFailed
from mcp_server_paperless.services.pages import PageService
from mcp_server_paperless.services.pdf_insert import PageInserter
from mcp_server_paperless.services.tasks import TaskService
from services.test_pages import PDF, PNG, WEB, Renderer, Slow

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp_server_paperless.domain.documents import (
        DocumentFile,
        DocumentPage,
        DocumentQuery,
        DocumentUpload,
        FileMetadata,
        FileVersion,
    )
    from mcp_server_paperless.domain.pdf import FileOutput, PdfResult
    from mcp_server_paperless.domain.rendering import Reason
    from mcp_server_paperless.domain.tasks import TaskStatus
    from mcp_server_paperless.domain.versions import VersionUpload

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-68")]

MINE, OTHER = 50, 51
MIB = 1024 * 1024
MADE = b"%PDF-1.7 made"
LINK = f"https://paperless.example/documents/{MINE}/details"
NOTHING = "Nothing was written."
KEPT = (
    (
        f"document {MINE} 'Contract': title, tags, notes, custom fields, archive serial number, "
        "owner, grants and every other metadata"
    ),
    "every earlier version, until it is deleted",
    (
        f"bookmarks, attachments, form fields, annotations and properties of the file of document "
        f"{MINE}"
    ),
    (
        "what the inserted pages refer to, also where it is not visible: links, comments with the "
        "replies to them and the files in them, actions, private and page data, and resources "
        "they share with other pages of the source; fields they refer to come without values, "
        "except one that stands in a field above those of the pages"
    ),
    f"document {OTHER} 'Annex': unchanged",
)
LOST = (
    "a signature in the PDF is no longer valid",
    (
        "bookmarks, document attachments and scripts, properties, layer settings (a hidden layer "
        "can "
        "become visible) and the form of the inserted pages are not in the result: each page shows "
        "its fields as a picture, which cannot be filled in; hidden fields are left "
        "out, and characters their font lacks are missing"
    ),
    (
        "page labels (like iv) stay at their place in the file: pages behind the inserted ones "
        "show those of others"
    ),
    "accessibility tags and PDF/A conformance of the result are not checked",
)
READ_AGAIN = "the text is read again, so corrections of it are not in the new version"


class Assembler(Renderer):
    """Answers `made` and records what it was asked to put together."""

    def __init__(self, made: RenderFailed | None = None, pages: int = 4) -> None:
        super().__init__()
        self.made: RenderFailed | None = made
        self.pages: int = pages
        self.assembled: list[tuple[bytes, bytes, AssembleRequest]] = []
        self.meanwhile: Callable[[], None] = lambda: None

    @override
    async def assemble(self, target: bytes, source: bytes, request: AssembleRequest) -> Assembled:
        self.assembled.append((target, source, request))
        self.meanwhile()
        if self.made is not None:
            raise self.made
        return Assembled(content=MADE, source_pages=self.pages, inserted=(2, 3))


class Paperless(FakePaperless):
    """The fake, recording what was sent and what was served."""

    def __init__(self, state: FakeState, *, user: str = ADMIN[0], writes: bool = True) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.versions: list[tuple[int, VersionUpload]] = []
        self.uploads: list[DocumentUpload] = []
        self.served: list[tuple[int, str, int, int | None]] = []
        self.lose: PaperlessMcpError | None = None
        self.fail: str | None = None
        self.running: bool = False
        self.empty: bool = False
        self.no_tasks: bool = False

    @override
    async def upload_document_version(self, document_id: int, upload: VersionUpload) -> str:
        self.versions.append((document_id, upload))
        if self.lose:
            raise self.lose
        return await super().upload_document_version(document_id, upload)

    @override
    async def upload_document(self, upload: DocumentUpload) -> str:
        self.uploads.append(upload)
        if self.lose:
            raise self.lose
        return await super().upload_document(upload)

    @override
    async def get_task(self, task_id: str) -> TaskStatus:
        if self.no_tasks:
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        status = await super().get_task(task_id)
        if self.fail:
            changed = {"state": "failure", "message": self.fail, "document_ids": ()}
            return status.model_copy(update=changed)
        if self.running:
            return status.model_copy(update={"state": "started", "document_ids": ()})
        if self.empty:
            return status.model_copy(update={"document_ids": ()})
        return status

    @override
    async def get_document_file(
        self,
        document_id: int,
        version: FileVersion,
        *,
        max_bytes: int,
        version_id: int | None = None,
    ) -> DocumentFile:
        self.served.append((document_id, version, max_bytes, version_id))
        return await super().get_document_file(
            document_id, version, max_bytes=max_bytes, version_id=version_id
        )

    def wrote(self) -> bool:
        return bool(self.versions or self.uploads)


def state_of(
    *, target: FakeDocument | None = None, source: FakeDocument | None = None
) -> FakeState:
    state = seeded()
    state.documents += [
        target
        or FakeDocument(
            id=MINE, title="Contract", owner=2, page_count=3, original=b"%PDF t", file_name="c.pdf"
        ),
        source
        or FakeDocument(id=OTHER, title="Annex", owner=2, page_count=4, original=b"%PDF source"),
    ]
    return state


def annex(**values: object) -> FakeDocument:
    given = {"page_count": 4, "original": b"%PDF source", "archive": b"%PDF archive", **values}
    return replace(FakeDocument(id=OTHER, title="Annex", owner=2), **given)


class Now:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay


def inserter(  # noqa: PLR0913 -- one keyword per seam
    paperless: FakePaperless,
    renderer: Renderer | None = None,
    *,
    turn: asyncio.Semaphore | None = None,
    max_bytes: int = 100,
    wait: float = 5.0,
    download: float = 5.0,
) -> PageInserter:
    now = Now()
    return PageInserter(
        paperless,
        paperless,
        renderer or Assembler(),
        TaskService(paperless, sleep=now.sleep, clock=now),
        turn or asyncio.Semaphore(1),
        web_url=WEB,
        max_bytes=max_bytes,
        wait_seconds=wait,
        download_seconds=download,
    )


async def insert(  # noqa: PLR0913 -- one keyword per argument a test varies
    service: PageInserter,
    pages: str = "2-3",
    after_page: int = 1,
    *,
    document: int = MINE,
    source: int = OTHER,
    version: int | None = None,
    output: FileOutput = "new_version",
    dry_run: bool = False,
    wait: float = 30,
) -> PdfResult:
    return await service.insert(
        document,
        source,
        pages,
        after_page,
        source_version_id=version,
        output=output,
        dry_run=dry_run,
        wait_seconds=wait,
    )


async def test_the_pages_become_a_new_version_of_the_document() -> None:
    paperless, renderer = Paperless(state_of()), Assembler()

    found = await insert(inserter(paperless, renderer))

    [(number, upload)] = paperless.versions
    assert (number, upload.filename, upload.mime_type, upload.content, upload.label) == (
        *(MINE, "c.pdf", PDF, MADE, None),
    )
    assert renderer.assembled == [
        (
            b"%PDF t",
            b"%PDF source",
            AssembleRequest(pages="2-3", after_page=1, target_pages=3, max_bytes=100),
        )
    ]
    assert paperless.served == [(MINE, "original", 100, MINE), (OTHER, "original", 94, OTHER)]
    document = await paperless.get_document(MINE)
    version = document.versions[0].id
    assert len(document.versions) == 2
    task = found.tasks[0]
    assert found.model_dump() == {
        "operation": "insert_pages",
        "summary": (
            f"insert pages 2-3 of document {OTHER} 'Annex' after page 1 of document {MINE} "
            "'Contract'"
        ),
        "dry_run": False,
        "plan": {
            "produces": (
                "a new version of the document with 5 pages, which everybody can read who may "
                "see the document"
            ),
            "keeps": KEPT,
            "loses": (*LOST, READ_AGAIN),
            "needs_confirm": False,
        },
        "source_document_ids": (MINE, OTHER),
        "state": "success",
        "message": None,
        "source_links": (LINK, LINK.replace(str(MINE), str(OTHER))),
        "tasks": (
            {
                "task_id": task.task_id,
                "state": "success",
                "message": None,
                "document_ids": (version,),
            },
        ),
        "produced_ids": (version,),
        "produced_links": (LINK,),
        "trashed_ids": (),
        "next": None,
        "wait": "finished",
        "waited_seconds": found.waited_seconds,
    }


async def test_the_pages_go_in_front_with_zero() -> None:
    found = await insert(inserter(Paperless(state_of())), after_page=0, dry_run=True)

    assert found.summary == (
        f"insert pages 2-3 of document {OTHER} 'Annex' in front of document {MINE} 'Contract'"
    )


async def test_a_dry_run_puts_the_file_together_and_writes_nothing() -> None:
    paperless, renderer = Paperless(state_of(), writes=False), Assembler()

    found = await insert(inserter(paperless, renderer), dry_run=True)

    assert len(renderer.assembled) == 1
    assert not paperless.wrote()
    assert (found.dry_run, found.state, found.tasks, found.produced_ids) == (
        *(True, "not_started", (), ()),
    )
    assert (found.wait, found.waited_seconds, found.next) == ("not_requested", 0, None)
    assert found.plan.keeps == KEPT
    assert "with 5 pages" in found.plan.produces


async def test_a_new_document_takes_the_metadata_of_the_target() -> None:
    state = state_of()
    target = next(d for d in state.documents if d.id == MINE)
    field = state.custom_fields[0].id
    target.correspondent = state.correspondents[0].id
    target.document_type = state.document_types[0].id
    target.storage_path = state.storage_paths[0].id
    target.tags = [state.tags[0].id]
    target.archive_serial_number = 4711
    target.custom_fields = {field: None}
    paperless = Paperless(state)

    found = await insert(inserter(paperless), output="new_document")

    [upload] = paperless.uploads
    assert not paperless.versions
    assert (upload.filename, upload.mime_type, upload.content) == ("c.pdf", PDF, MADE)
    assert (upload.title, upload.created) == ("Contract", target.created)
    assert upload.correspondent == target.correspondent
    assert upload.document_type == target.document_type
    assert upload.storage_path == target.storage_path
    assert upload.tags == tuple(target.tags)
    assert upload.custom_fields == (CustomFieldValue(field=field, value=None),)
    assert upload.archive_serial_number is None
    [made] = found.produced_ids
    assert found.produced_links == (LINK.replace(str(MINE), str(made)),)
    assert found.summary.endswith(", as a new document")
    assert found.plan.produces == "1 new document with 5 pages"
    assert found.plan.keeps == (
        (
            "the new document takes title, correspondent, document type, storage path, tags, "
            f"created date and custom field values of document {MINE} 'Contract'"
        ),
        KEPT[2],
        f"document {MINE} 'Contract': unchanged",
        *KEPT[3:],
        f"archive serial number 4711 stays with document {MINE}",
    )
    assert found.plan.loses == (
        *LOST,
        "view and change grants are not copied",
        (
            "notes, history, earlier versions, share links, corrections of the text and the back "
            "links of document links are not copied"
        ),
        (
            "a new document belongs to the calling user, gets the inbox tags and is processed like "
            "an upload: workflows, matching and barcodes can change it, split it again or stop it"
        ),
    )
    assert not found.plan.needs_confirm


async def test_the_metadata_is_that_of_the_moment_of_the_upload() -> None:
    state, renderer = state_of(), Assembler()
    target = next(d for d in state.documents if d.id == MINE)
    paperless = Paperless(state)

    def rename() -> None:
        target.title = "Renamed"

    renderer.meanwhile = rename
    _ = await insert(inserter(paperless, renderer), output="new_document")

    assert paperless.uploads[0].title == "Renamed"


@pytest.mark.parametrize(
    ("name", "sent"),
    [
        ("scan.PDF", "scan.pdf"),
        ("scan.old.pdf", "scan.old.pdf"),
        ("a/b\\c.pdf", "c.pdf"),
        ("bad\u202ename.pdf", f"document-{MINE}.pdf"),
        ("x" * 300 + ".pdf", f"document-{MINE}.pdf"),
        (None, f"document-{MINE}.pdf"),
    ],
)
async def test_the_file_has_the_name_of_the_target(name: str | None, sent: str) -> None:
    class Named(Paperless):
        @override
        async def get_document_metadata(
            self, document_id: int, *, version_id: int | None = None
        ) -> FileMetadata:
            found = await super().get_document_metadata(document_id, version_id=version_id)
            return found.model_copy(update={"original_filename": name})

    paperless = Named(state_of())

    _ = await insert(inserter(paperless))

    assert paperless.versions[0][1].filename == sent


def versioned() -> FakeState:
    """Both documents with a newer file: versions 60 and 61."""
    state = state_of()
    for document, number in zip(state.documents[-2:], (60, 61), strict=True):
        root = document.versions[0]
        newer = root.model_copy(update={"id": number, "is_root": False})
        document.versions = [newer, root]
        first = FakeFile(document.original, document.archive, "", PDF, 3)
        document.version_files = {
            document.id: first,
            number: FakeFile(b"%PDF newer", None, "", PDF, 2),
        }
        document.original, document.archive = b"%PDF newer", None
        document.page_count = 2
    return state


async def test_the_newest_files_are_read() -> None:
    paperless, renderer = Paperless(versioned()), Assembler()

    _ = await insert(inserter(paperless, renderer), "1-2")

    assert [(n, v) for n, _, _, v in paperless.served] == [(MINE, 60), (OTHER, 61)]
    assert renderer.assembled[0][2].target_pages == 2


async def test_an_older_version_of_the_source_can_be_taken() -> None:
    paperless = Paperless(versioned())

    _ = await insert(inserter(paperless), "3", version=OTHER)

    assert paperless.served[1][3] == OTHER
    with pytest.raises(Validation, match="Page 4 does not exist: the document has 3 pages"):
        _ = await insert(inserter(paperless), "4", version=OTHER)
    with pytest.raises(NotFound, match=f"Document {OTHER} has no version 60"):
        _ = await insert(inserter(paperless), version=60)


async def test_the_archive_stands_in_for_a_source_that_is_no_pdf() -> None:
    source = annex(mime_type=PNG, original=b"PNG", page_count=None)
    paperless, renderer = Paperless(state_of(source=source)), Assembler(pages=1)

    found = await insert(inserter(paperless, renderer))

    assert paperless.served[1][:2] == (OTHER, "archive")
    assert renderer.assembled[0][1] == b"%PDF archive"
    assert found.plan.produces == (
        f"a new version of the document with 5 pages, from the archive PDF of document {OTHER} "
        "(its file is image/png), which everybody can read who may see the document"
    )


@pytest.mark.parametrize(
    ("change", "error", "message"),
    [
        ({"source": MINE}, Validation, "A document cannot be its own source."),
        ({"document": 999}, NotFound, "999"),
        ({"source": 999}, NotFound, "999"),
        ({"after_page": 4}, Validation, f"Page 4 does not exist: document {MINE} has 3 pages."),
        ({"after_page": -1}, Validation, f"Page -1 does not exist: document {MINE} has 3 pages."),
        ({"pages": "5"}, Validation, "Page 5 does not exist: the document has 4 pages."),
        ({"pages": ""}, Validation, "No pages given."),
        ({"pages": "x"}, Validation, "'x' is not a page or a range."),
    ],
)
async def test_what_can_be_seen_beforehand_is_refused_before_a_file_is_read(
    change: dict[str, object], error: type[PaperlessMcpError], message: str
) -> None:
    paperless = Paperless(state_of())
    arguments: dict[str, object] = {"pages": "2", "after_page": 1, **change}

    with pytest.raises(error) as raised:
        _ = await insert(inserter(paperless), **arguments)  # pyright: ignore[reportArgumentType] -- keyword passthrough

    assert message in raised.value.message
    assert not paperless.served
    assert not paperless.wrote()


async def test_the_first_and_the_last_place_are_allowed() -> None:
    for place in (0, 3):
        paperless = Paperless(state_of())
        _ = await insert(inserter(paperless), after_page=place)
        assert paperless.wrote()


@pytest.mark.parametrize(
    ("target", "message", "hint"),
    [
        (
            {"mime_type": PNG},
            f"Document {MINE} is not a PDF (image/png).",
            "merge_documents with archive_fallback",
        ),
        (
            {"page_count": None},
            f"The page count of document {MINE} is unknown.",
            "It may be password protected",
        ),
    ],
)
async def test_a_target_that_is_no_usable_pdf_is_refused(
    target: dict[str, object], message: str, hint: str
) -> None:
    document = replace(FakeDocument(id=MINE, title="Contract", owner=2, page_count=3), **target)
    paperless = Paperless(state_of(target=document))

    with pytest.raises(Validation) as raised:
        _ = await insert(inserter(paperless))

    assert raised.value.message == message
    assert hint in (raised.value.hint or "")
    assert not paperless.served


async def test_a_source_without_a_pdf_is_refused() -> None:
    source = annex(mime_type=PNG, original=b"PNG", archive=None)
    paperless = Paperless(state_of(source=source))

    with pytest.raises(Validation) as raised:
        _ = await insert(inserter(paperless))

    assert raised.value.message == (
        f"Document {OTHER} is not a PDF (image/png) and has no archive PDF to take pages from."
    )


@pytest.mark.parametrize(
    ("lost", "source", "message"),
    [
        ((MINE, "original"), None, f"cannot find the original of document {MINE}"),
        ((OTHER, "original"), None, f"cannot find the original of document {OTHER}"),
        (
            (OTHER, "archive"),
            annex(mime_type=PNG, original=b"PNG"),
            f"cannot find the archive version of document {OTHER}",
        ),
    ],
)
async def test_a_file_paperless_cannot_find_is_named(
    lost: tuple[int, str], source: FakeDocument | None, message: str
) -> None:
    state = state_of(source=source)
    state.missing.add(lost)  # pyright: ignore[reportArgumentType] -- a literal of the case
    paperless = Paperless(state)

    with pytest.raises(NotFound, match=message):
        _ = await insert(inserter(paperless))

    assert not paperless.served


async def test_changing_the_document_needs_the_right_to_change_it() -> None:
    state = state_of()
    for document in state.documents[-2:]:
        document.viewers = {u.id for u in state.users}
    paperless = Paperless(state, user=READER[0])

    with pytest.raises(PermissionDenied):
        _ = await insert(inserter(paperless))
    with pytest.raises(PermissionDenied):
        _ = await insert(inserter(paperless), output="new_document")

    assert not paperless.versions
    assert len(paperless.uploads) == 1  # a view is enough to copy: Paperless says no to adding


async def test_a_file_the_user_may_not_see_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await insert(inserter(Paperless(state_of(), user=READER[0])))


@pytest.mark.parametrize(("limit", "fits"), [(17, True), (16, False)])
async def test_both_files_together_have_the_limit(limit: int, *, fits: bool) -> None:
    class Understating(Paperless):
        @override
        async def get_document_metadata(
            self, document_id: int, *, version_id: int | None = None
        ) -> FileMetadata:
            found = await super().get_document_metadata(document_id, version_id=version_id)
            return found.model_copy(update={"original_size": 1})

    for kind in (Paperless, Understating):
        paperless = kind(state_of())
        if fits:
            _ = await insert(inserter(paperless, max_bytes=limit))
            assert paperless.wrote()
            continue
        with pytest.raises(PaperlessMcpError) as raised:
            _ = await insert(inserter(paperless, max_bytes=limit))
        assert raised.value.message == (
            f"The files of documents {MINE} and {OTHER} are together over 16 bytes, the most that "
            f"is put together. {NOTHING}"
        )
        assert bool(paperless.served) is (kind is Understating)
        assert not paperless.wrote()


async def test_a_target_over_the_limit_is_refused_alone() -> None:
    class Unknown(Paperless):
        @override
        async def get_document_metadata(
            self, document_id: int, *, version_id: int | None = None
        ) -> FileMetadata:
            found = await super().get_document_metadata(document_id, version_id=version_id)
            return found.model_copy(update={"original_size": 1})

    paperless = Unknown(state_of())

    with pytest.raises(PaperlessMcpError, match="together over 5 bytes"):
        _ = await insert(inserter(paperless, max_bytes=5))

    assert [n for n, *_ in paperless.served] == [MINE]


async def test_files_that_do_not_come_in_time_are_given_up() -> None:
    class Stuck(Paperless):
        @override
        async def get_document_file(
            self,
            document_id: int,
            version: FileVersion,
            *,
            max_bytes: int,
            version_id: int | None = None,
        ) -> DocumentFile:
            if document_id == OTHER:
                _ = await asyncio.Event().wait()
            return await super().get_document_file(
                document_id, version, max_bytes=max_bytes, version_id=version_id
            )

    paperless, turn = Stuck(state_of()), asyncio.Semaphore(1)

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await insert(inserter(paperless, turn=turn, download=0.05))

    assert raised.value.message == (
        f"Paperless did not send the files of documents {MINE} and {OTHER} within 0 seconds. "
        f"{NOTHING}"
    )
    assert not turn.locked()
    assert not paperless.wrote()


async def test_the_turn_is_shared_with_the_tools_that_show_pages() -> None:
    paperless, slow = Paperless(state_of()), Slow()
    turn = asyncio.Semaphore(1)
    pages = PageService(paperless, slow, web_url=WEB, turn=turn)
    showing = asyncio.create_task(pages.show(MINE, "1"))
    async with asyncio.timeout(5):
        _ = await slow.began.wait()

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await insert(inserter(paperless, turn=turn, wait=0.01))
    slow.go.set()
    _ = await showing
    _ = await insert(inserter(paperless, turn=turn))

    assert raised.value.message == "Another document is being rendered."
    assert "up to 150 seconds" in (raised.value.hint or "")
    assert len(paperless.versions) == 1
    assert not turn.locked()


UNLOCK = "remove_document_password (toolset pdf) makes a version without the password."
REWRITE = (
    "It opens without a password but restricts what may be done with it. edit_pdf with all its "
    'pages and output="new_version" makes a version without that: it changes that document and '
    "needs ownership of it."
)


@pytest.mark.parametrize(
    ("reason", "of_source", "message", "hint"),
    [
        ("encrypted", False, f"The original of document {MINE} is encrypted.", UNLOCK),
        ("encrypted", True, f"The original of document {OTHER} is encrypted.", UNLOCK),
        ("restricted", False, f"The original of document {MINE} is encrypted.", REWRITE),
        ("restricted", True, f"The original of document {OTHER} is encrypted.", REWRITE),
        (
            "damaged",
            False,
            f"The original of document {MINE} is damaged and cannot be put together.",
            None,
        ),
        (
            "damaged",
            True,
            f"The original of document {OTHER} is damaged and cannot be put together.",
            None,
        ),
        (
            "differs",
            False,
            (
                f"The original of document {MINE} does not have the 3 pages that Paperless "
                "counted: it is damaged."
            ),
            None,
        ),
        ("count", True, f"The original of document {OTHER} has more than 200000 pages.", None),
        ("count", False, "The result would have more than 200000 pages.", None),
        (
            "size",
            True,
            (
                f"The original of document {OTHER} has a page that is too large to be put together "
                "(over 3.5 km)."
            ),
            None,
        ),
        (
            "fields",
            True,
            (
                f"The original of document {OTHER} has a page whose form fields cannot be taken "
                "out of it."
            ),
            "Insert the other pages, or upload a copy that was printed to a new PDF.",
        ),
        ("large", False, "The result would be over 100 bytes.", "Insert fewer pages."),
        (
            "timeout",
            False,
            "Putting the files together took longer than 30 seconds and was stopped.",
            "Insert fewer pages.",
        ),
        (
            "memory",
            False,
            "Putting the files together needed more than 1 GiB of memory and was stopped.",
            "Insert fewer pages.",
        ),
        ("crashed", False, "The files could not be put together.", None),
    ],
)
@pytest.mark.parametrize("output", ["new_version", "new_document"])
async def test_files_that_cannot_be_put_together_change_nothing(
    reason: Reason,
    of_source: bool,  # noqa: FBT001 -- a parameter of the case
    message: str,
    hint: str | None,
    output: FileOutput,
) -> None:
    paperless = Paperless(state_of())
    renderer = Assembler(RenderFailed(reason=reason, of_source=of_source))

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await insert(inserter(paperless, renderer), output=output)

    assert (raised.value.message, raised.value.hint) == (f"{message} {NOTHING}", hint)
    assert not paperless.wrote()


async def test_a_damaged_archive_is_named_as_the_archive() -> None:
    source = annex(mime_type=PNG, original=b"PNG", page_count=None)
    renderer = Assembler(RenderFailed(reason="damaged", of_source=True))

    with pytest.raises(PaperlessMcpError, match=f"The archive version of document {OTHER} is dam"):
        _ = await insert(inserter(Paperless(state_of(source=source)), renderer))


@pytest.mark.parametrize(
    "source",
    [annex(mime_type=PNG, original=b"PNG", page_count=None), annex(page_count=None)],
    ids=["archive", "not counted"],
)
async def test_pages_only_the_file_can_tell_are_refused_in_the_words_of_this_server(
    source: FakeDocument,
) -> None:
    paperless = Paperless(state_of(source=source))
    renderer = Assembler(RenderFailed(reason="pages", page_count=1, of_source=True))

    with pytest.raises(Validation, match="Page 2 does not exist: the document has 1 page"):
        _ = await insert(inserter(paperless, renderer))

    assert not paperless.wrote()


@pytest.mark.parametrize("output", ["new_version", "new_document"])
@pytest.mark.parametrize("changed", [MINE, OTHER])
async def test_nothing_is_written_if_a_document_changed_meanwhile(
    changed: int, output: FileOutput
) -> None:
    state, renderer = state_of(), Assembler()
    paperless = Paperless(state)
    document = next(d for d in state.documents if d.id == changed)

    def newer() -> None:
        root = document.versions[0]
        version = DocumentVersion.model_validate({**root.model_dump(), "id": 70, "is_root": False})
        document.versions = [version, root]

    renderer.meanwhile = newer
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await insert(inserter(paperless, renderer), output=output)

    assert raised.value.message == f"Document {changed} got another file meanwhile. {NOTHING}"
    assert raised.value.hint == "Look at it and call the tool again."
    assert not paperless.wrote()


async def test_another_version_of_the_source_does_not_matter_for_a_chosen_one() -> None:
    state, renderer = state_of(), Assembler()
    paperless = Paperless(state)
    document = next(d for d in state.documents if d.id == OTHER)

    def newer() -> None:
        root = document.versions[0]
        document.versions = [root.model_copy(update={"id": 70, "is_root": False}), root]

    renderer.meanwhile = newer
    _ = await insert(inserter(paperless, renderer), version=OTHER)

    assert paperless.wrote()


async def test_a_document_that_went_away_meanwhile_gets_nothing() -> None:
    state, renderer = state_of(), Assembler()
    paperless = Paperless(state)

    def vanish() -> None:
        state.documents = [d for d in state.documents if d.id != OTHER]

    renderer.meanwhile = vanish
    with pytest.raises(NotFound):
        _ = await insert(inserter(paperless, renderer))

    assert not paperless.wrote()


@pytest.mark.parametrize(
    ("output", "what", "hint"),
    [
        (
            "new_version",
            "the new version",
            (
                f"Look at the versions of document {MINE} (get_document with include versions) "
                "before you repeat the call."
            ),
        ),
        (
            "new_document",
            "the new document",
            "Look for it with search_documents (newest added first) before you repeat the call.",
        ),
    ],
)
@pytest.mark.parametrize(
    "lost",
    [
        UpstreamTimeout("Paperless did not respond in time.", hint="Try again."),
        UpstreamUnavailable("Paperless did not respond in time.", status=503),
        UnexpectedAnswer("Paperless did not respond in time.", hint="It may have been applied."),
    ],
)
async def test_a_lost_answer_says_that_the_outcome_is_not_known(
    output: FileOutput, what: str, hint: str, lost: PaperlessMcpError
) -> None:
    paperless = Paperless(state_of())
    paperless.lose = lost

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await insert(inserter(paperless), output=output)

    assert raised.value.message == (
        f"It is not known whether Paperless took {what}: Paperless did not respond in time."
    )
    assert raised.value.hint == hint


async def test_too_many_requests_are_a_refusal() -> None:
    paperless = Paperless(state_of())
    paperless.lose = UpstreamUnavailable("Paperless is busy.", hint="Try again.", status=429)

    with pytest.raises(UpstreamUnavailable) as raised:
        _ = await insert(inserter(paperless))

    assert (raised.value.message, raised.value.hint) == ("Paperless is busy.", "Try again.")


async def test_the_built_file_is_not_kept_while_the_task_is_followed() -> None:
    class Watched(Assembler):
        @override
        async def assemble(
            self, target: bytes, source: bytes, request: AssembleRequest
        ) -> Assembled:
            made = await super().assemble(target, source, request)
            built.append(weakref.ref(made))
            return made

    class Following(Paperless):
        @override
        async def get_task(self, task_id: str) -> TaskStatus:
            _ = gc.collect()
            alive.append(built[0]() is not None)
            return await super().get_task(task_id)

    built: list[weakref.ref[Assembled]] = []
    alive: list[bool] = []
    _ = await insert(inserter(Following(state_of()), Watched()))

    assert alive
    assert not any(alive)


async def test_what_paperless_refuses_is_said_in_its_words() -> None:
    state = state_of()
    state.users = [
        replace(u, is_superuser=False, permissions=frozenset({"view_document", "change_document"}))
        if u.username == ADMIN[0]
        else u
        for u in state.users
    ]

    with pytest.raises(PermissionDenied, match="may not perform this action"):
        _ = await insert(inserter(Paperless(state)))


async def test_a_failed_consumption_leaves_the_document_as_it_was() -> None:
    paperless = Paperless(state_of())
    paperless.fail = "Not consuming: broken."

    version = await insert(inserter(paperless))
    document = await insert(inserter(paperless), output="new_document")

    assert (version.state, version.tasks[0].state, version.next) == ("failure", "failure", None)
    assert version.message == (
        f"Not consuming: broken. Document {MINE} shows the file it showed before."
    )
    assert (version.produced_ids, version.produced_links) == ((), ())
    assert document.message == "Not consuming: broken."


async def test_without_a_wait_the_task_is_named() -> None:
    paperless = Paperless(state_of())
    paperless.running = True

    found = await insert(inserter(paperless), wait=0)

    task_id = found.tasks[0].task_id
    assert (found.state, found.wait, found.produced_ids) == ("started", "not_requested", ())
    assert found.next == f'get_task(task_id="{task_id}", wait_seconds=30)'


async def test_the_wait_is_cut_at_the_usual_bound() -> None:
    paperless = Paperless(state_of())
    paperless.running = True

    found = await insert(inserter(paperless), wait=1000)

    assert found.wait == "timed_out"
    assert found.waited_seconds <= 30


@pytest.mark.parametrize(
    ("output", "following", "named"),
    [
        (
            "new_version",
            f'get_document(document_id={MINE}, include=["versions"])',
            "names no version for it",
        ),
        (
            "new_document",
            "find the new documents with search_documents (newest added first) and check them",
            "names no document for it",
        ),
    ],
)
async def test_what_cannot_be_followed_says_where_to_look(
    output: FileOutput, following: str, named: str
) -> None:
    hidden, empty = Paperless(state_of()), Paperless(state_of())
    hidden.no_tasks, empty.empty = True, True

    unread = await insert(inserter(hidden), output=output)
    unnamed = await insert(inserter(empty), output=output)

    assert (unread.state, unread.next) == ("unknown", following)
    assert "its task could not be read" in (unread.message or "")
    assert (unnamed.state, unnamed.next) == ("success", following)
    assert named in (unnamed.message or "")
    assert (unnamed.produced_ids, unnamed.produced_links) == ((), ())


async def test_the_same_file_twice_is_not_looked_up_as_a_duplicate() -> None:
    """The bytes of the result differ every time (decision-68 reads decision-47 this way)."""

    class Unsearched(Paperless):
        @override
        async def search_documents(
            self, query: DocumentQuery, *, page: int, page_size: int
        ) -> DocumentPage:
            raise AssertionError(query)

    paperless = Unsearched(state_of())
    _ = await insert(inserter(paperless), output="new_document")
    again = await insert(inserter(paperless), output="new_document")

    assert (again.state, len(paperless.uploads)) == ("success", 2)
