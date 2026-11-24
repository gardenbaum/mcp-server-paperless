"""What each service does with a document whose file Paperless cannot find.

Fakes without `allow_writes` raise on any write, so a refusal proves that nothing was sent.
"""

import asyncio
from typing import TYPE_CHECKING, override

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded
from harness import pdf_service

from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.services.admin_system import SystemAdmin
from mcp_server_paperless.services.documents import (
    CHECK_SECONDS,
    CHECKS_AT_ONCE,
    require_files,
)
from mcp_server_paperless.services.files import FileService
from mcp_server_paperless.services.pages import PageService
from mcp_server_paperless.services.sharing import SharingService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp_server_paperless.domain.documents import DocumentFile, FileMetadata, FileVersion
    from mcp_server_paperless.domain.rendering import Rendered, RenderRequest
    from mcp_server_paperless.services.pdf import PdfService

pytestmark = pytest.mark.anyio

WEB = "https://paperless.example"
WHOLE, LOST, ALSO = 71, 72, 73
STILL_READ = "Its text and metadata can still be read; restore the file from a backup."


class NoRenderer:
    async def render(self, content: bytes, request: RenderRequest) -> Rendered:
        raise AssertionError((content, request))


def state_with(*missing: int) -> FakeState:
    state = seeded()
    state.documents += [FakeDocument(id=n, title=f"doc {n}") for n in (WHOLE, LOST, ALSO)]
    state.missing |= {(n, "original") for n in missing}
    state.email_enabled = True
    return state


def fake_with(*missing: int) -> FakePaperless:
    return FakePaperless(state_with(*missing), user=ADMIN[0])


def files(fake: FakePaperless) -> FileService:
    return FileService(fake, web_url=WEB, max_bytes=100)


def pdf(fake: FakePaperless) -> PdfService:
    return pdf_service(fake)


def sharing(fake: FakePaperless) -> SharingService:
    return SharingService(fake, fake, fake, web_url=WEB)


FILE_CALLS: dict[str, Callable[[FakePaperless], Awaitable[object]]] = {
    "download": lambda fake: files(fake).download(LOST, "original"),
    "pages": lambda fake: PageService(fake, NoRenderer(), web_url=WEB).show(LOST, "1"),
    "part": lambda fake: files(fake).part(LOST, "original", offset=0),
}


@pytest.mark.parametrize("call", FILE_CALLS.values(), ids=FILE_CALLS)
async def test_what_needs_the_original_says_that_paperless_cannot_find_it(
    call: Callable[[FakePaperless], Awaitable[object]],
) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await call(fake_with(LOST))

    assert raised.value.message.startswith(f"Paperless cannot find the original of document {LOST}")
    assert raised.value.hint in {
        "Ask for the archive version, which is there.",
        'Call again with source="archive", the archive version, which is there.',
    }


async def test_a_document_without_file_names_is_not_one_without_its_file() -> None:
    fake = fake_with()
    fake.state.nameless.add(LOST)

    whole = await files(fake).download(LOST, "original")
    part = await files(fake).part(LOST, "original", offset=0)

    assert whole.data
    assert part.data == whole.data


@pytest.mark.parametrize(
    ("mime_type", "source", "missing", "archive", "message", "hint"),
    [
        # What cannot be shown is not offered.
        ("text/plain", None, ["archive"], b"a", "archive version", STILL_READ),
        (
            "application/pdf",
            "archive",
            ["archive"],
            b"a",
            "archive version",
            'Call again with source="original", the original, which is there.',
        ),
        ("application/pdf", None, ["original", "archive"], b"a", "original", STILL_READ),
        ("application/pdf", None, ["original"], None, "original", STILL_READ),
    ],
)
async def test_pages_name_the_other_source_only_if_it_can_be_shown(  # noqa: PLR0913, PLR0917 -- the cases
    mime_type: str,
    source: FileVersion | None,
    missing: list[FileVersion],
    archive: bytes | None,
    message: str,
    hint: str,
) -> None:
    state = seeded()
    state.documents.append(FakeDocument(id=LOST, title="t", mime_type=mime_type, archive=archive))
    state.missing |= {(LOST, kind) for kind in missing}
    service = PageService(FakePaperless(state, user=ADMIN[0]), NoRenderer(), web_url=WEB)

    with pytest.raises(NotFound) as raised:
        _ = await service.show(LOST, "1", source=source)

    assert raised.value.describe() == (
        f"Paperless cannot find the {message} of document {LOST} (version {LOST}). Hint: {hint}"
    )


async def test_another_refusal_of_the_file_for_pages_stays_as_it_is() -> None:
    class Gone(FakePaperless):
        @override
        async def get_document_file(
            self,
            document_id: int,
            version: FileVersion,
            *,
            max_bytes: int,
            version_id: int | None = None,
        ) -> DocumentFile:
            message = "No version 7 exists."
            raise NotFound(message)

    gone = Gone(state_with(), user=ADMIN[0])

    with pytest.raises(NotFound, match="No version 7 exists"):
        _ = await PageService(gone, NoRenderer(), web_url=WEB).show(LOST, "1")


async def test_the_archive_that_is_missing_is_not_offered_for_what_has_no_pages() -> None:
    fake = fake_with()
    fake.state.documents[-1].mime_type = "text/plain"
    fake.state.missing.add((ALSO, "archive"))
    service = PageService(fake, NoRenderer(), web_url=WEB)

    with pytest.raises(Validation) as raised:
        _ = await service.show(ALSO, "1", source="original")

    assert raised.value.hint == "Read its text with get_document_content."


async def test_an_original_that_is_missing_is_not_offered_for_an_archive_there_is_not() -> None:
    fake = fake_with(LOST)
    [document] = [d for d in fake.state.documents if d.id == LOST]
    document.archive = None
    pages = PageService(fake, NoRenderer(), web_url=WEB)

    for call in (
        lambda: files(fake).download(LOST, "archive"),
        lambda: pages.show(LOST, "1", source="archive"),
    ):
        with pytest.raises(NotFound, match="has no archive version") as raised:
            _ = await call()
        assert raised.value.hint == STILL_READ


async def test_the_archive_version_is_still_delivered_whole_and_in_parts() -> None:
    fake = fake_with(LOST)
    fake.state.nameless.add(LOST)

    whole = await files(fake).download(LOST, "archive")
    part = await files(fake).part(LOST, "archive", offset=0)

    assert whole.data
    assert part.data == whole.data


async def test_the_other_file_is_not_offered_when_it_is_missing_too() -> None:
    fake = fake_with(LOST)
    fake.state.missing.add((LOST, "archive"))

    for kind, noun in (("original", "original"), ("archive", "archive version")):
        with pytest.raises(NotFound) as raised:
            _ = await files(fake).download(LOST, "archive" if kind == "archive" else "original")
        assert raised.value.describe() == (
            f"Paperless cannot find the {noun} of document {LOST}. Hint: {STILL_READ}"
        )


async def test_a_thumbnail_that_is_missing_is_named() -> None:
    fake = fake_with()
    fake.state.missing.add((LOST, "thumbnail"))

    with pytest.raises(NotFound, match=f"cannot find the thumbnail of document {LOST}"):
        _ = await files(fake).thumbnail(LOST)


async def test_an_archive_names_the_documents_to_leave_out() -> None:
    fake = fake_with(LOST, ALSO)
    service = SystemAdmin(fake, fake, max_bytes=10**6)

    with pytest.raises(NotFound, match=f"a file of documents {LOST} and {ALSO}"):
        _ = await service.download([WHOLE, LOST, ALSO], content="originals")

    assert (await service.download([WHOLE, LOST, ALSO], content="archive")).data


PDF_CALLS: dict[str, tuple[int, Callable[[PdfService], Awaitable[object]]]] = {
    "rotate": (2, lambda service: service.rotate([WHOLE, LOST], 90)),
    "merge": (2, lambda service: service.merge([WHOLE, LOST], None, delete_originals=True)),
    "split": (1, lambda service: service.split(LOST, ["1", "1"])),
    "delete_pages": (1, lambda service: service.delete_pages(LOST, "1")),
    "remove_password": (1, lambda service: service.remove_password(LOST, "x")),
    "reprocess": (2, lambda service: service.reprocess([WHOLE, LOST])),
}


@pytest.mark.parametrize(("chosen", "call"), PDF_CALLS.values(), ids=PDF_CALLS)
async def test_a_pdf_operation_on_a_missing_file_starts_nothing(
    chosen: int,
    call: Callable[[PdfService], Awaitable[object]],
) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await call(pdf(fake_with(LOST)))

    several = (
        "(1 of the 2 chosen). Hint: Leave it out; its text and metadata can still be read. "
        "Restore the file from a backup."
    )
    assert raised.value.describe() == (
        f"Paperless cannot find a file of document {LOST}. Hint: {STILL_READ}"
        if chosen == 1
        else f"Paperless cannot find a file of document {LOST} {several}"
    )


async def test_a_pdf_operation_names_every_document_without_its_file() -> None:
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await pdf(fake_with(LOST, ALSO)).rotate([ALSO, WHOLE, LOST], 90)

    assert raised.value.describe() == (
        f"Paperless cannot find a file of documents {ALSO} and {LOST} (2 of the 3 chosen). Hint: "
        "Leave them out; their text and metadata can still be read. Restore the files from a "
        "backup."
    )


@pytest.mark.parametrize("confirm", [False, True])
async def test_an_e_mail_with_a_missing_file_is_not_sent(*, confirm: bool) -> None:
    fake = fake_with(LOST)

    with pytest.raises(NotFound, match=f"cannot find a file of document {LOST}"):
        _ = await sharing(fake).email(
            [WHOLE, LOST],
            recipients=["a@example.org"],
            subject="s",
            message="m",
            use_archive_version=False,
            confirm=confirm,
        )

    assert fake.state.outbox == []


async def test_an_e_mail_takes_the_file_of_the_upload_not_of_the_newest_version() -> None:
    state = state_with()
    [document] = [d for d in state.documents if d.id == LOST]
    newest = document.versions[0].model_copy(update={"id": 99, "is_root": False})
    document.versions.insert(0, newest)
    state.missing.add((99, "original"))
    fake = FakePaperless(state, user=ADMIN[0], allow_writes=True)

    sent = await sharing(fake).email(
        [LOST],
        recipients=["a@example.org"],
        subject="s",
        message="m",
        use_archive_version=False,
        confirm=True,
    )

    assert sent.documents == (LOST,)


async def mailed(fake: FakePaperless, *, archive: bool) -> None:
    _ = await sharing(fake).email(
        [WHOLE, LOST],
        recipients=["a@example.org"],
        subject="s",
        message="m",
        use_archive_version=archive,
        confirm=True,
    )


@pytest.mark.parametrize(("archive", "other"), [(True, "originals"), (False, "archive versions")])
async def test_an_e_mail_names_the_kind_that_would_work(*, archive: bool, other: str) -> None:
    state = state_with()
    state.missing.add((LOST, "archive" if archive else "original"))
    fake = FakePaperless(state, user=ADMIN[0], allow_writes=True)

    with pytest.raises(NotFound) as raised:
        await mailed(fake, archive=archive)
    assert raised.value.describe() == (
        f"Paperless cannot find a file of document {LOST} (1 of the 2 chosen). Hint: Choosing the "
        f"{other} instead works for all of them. Leave it out; its text and metadata can still be "
        "read. Restore the file from a backup."
    )
    assert fake.state.outbox == []

    await mailed(fake, archive=not archive)
    assert len(fake.state.outbox) == 1


class Unsure(FakePaperless):
    """A Paperless that cannot give the metadata of some documents, or never answers for them."""

    failing: frozenset[int] = frozenset()
    stuck: frozenset[int] = frozenset()
    running: int = 0
    most: int = 0

    @override
    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        self.running += 1
        self.most = max(self.most, self.running)
        try:
            await asyncio.sleep(0)
            if document_id in self.stuck:
                await asyncio.sleep(60)
            if document_id in self.failing:
                message = "Paperless is temporarily unavailable (HTTP 500)."
                raise UpstreamUnavailable(message, status=500)
            return await super().get_document_metadata(document_id, version_id=version_id)
        finally:
            self.running -= 1


def unsure(*missing: int, failing: set[int] | None = None) -> Unsure:
    fake = Unsure(state_with(*missing), user=ADMIN[0], allow_writes=True)
    fake.failing = frozenset(failing or ())
    return fake


async def test_what_cannot_be_checked_does_not_stop_an_operation_but_a_merge() -> None:
    fake = unsure(failing={LOST})

    assert (await pdf(fake).rotate([WHOLE, LOST], 90)).tasks
    assert (await pdf(fake).reprocess([WHOLE, LOST], confirm=True)).tasks
    await mailed(fake, archive=False)
    assert len(fake.state.outbox) == 1
    before = len(fake.state.tasks)
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await pdf(fake).merge([WHOLE, LOST], None)

    assert len(fake.state.tasks) == before
    assert raised.value.describe() == (
        f"The file of document {LOST} could not be checked, so nothing was merged: Paperless "
        "leaves a document whose file is missing out of a merge without saying so. Hint: Try again "
        "in a moment."
    )


@pytest.mark.parametrize("certain", [False, True])
async def test_a_missing_file_is_refused_also_if_others_could_not_be_checked(
    *, certain: bool
) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await require_files(
            unsure(LOST, failing={WHOLE}),
            dict.fromkeys([WHOLE, LOST]),
            "original",
            certain=certain,
        )

    assert raised.value.describe() == (
        f"Paperless cannot find a file of document {LOST} (1 of the 2 chosen). Hint: Leave it out; "
        "its text and metadata can still be read. Restore the file from a backup. The list may be "
        "incomplete: 1 could not be checked."
    )


async def test_the_check_is_bounded_and_keeps_what_it_saw() -> None:
    fake = unsure(LOST)
    fake.stuck = frozenset({WHOLE, ALSO})

    _ = await require_files(fake, dict.fromkeys([WHOLE, ALSO]), "original", seconds=0.05)
    with pytest.raises(PaperlessMcpError, match=f"documents {WHOLE} and {ALSO} could not"):
        _ = await require_files(
            fake, dict.fromkeys([WHOLE, ALSO]), "original", certain=True, seconds=0.05
        )
    with pytest.raises(NotFound, match=f"document {LOST} "):
        _ = await require_files(fake, dict.fromkeys([WHOLE, LOST]), "original", seconds=0.05)

    assert fake.running == 0
    assert CHECK_SECONDS == 30


async def test_the_documents_are_checked_a_few_at_a_time() -> None:
    fake = unsure()
    fake.state.documents += [FakeDocument(id=n, title=f"doc {n}") for n in range(80, 100)]

    _ = await require_files(fake, dict.fromkeys(range(80, 100)), "original")

    assert fake.most == CHECKS_AT_ONCE
