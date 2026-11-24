"""What each service does with a document whose file Paperless cannot find.

Fakes without `allow_writes` raise on any write, so a refusal proves that nothing was sent.
"""

from typing import TYPE_CHECKING

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakeDocument, FakeFile, FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.errors import NotFound, PaperlessMcpError
from mcp_server_paperless.services.admin_system import SystemAdmin
from mcp_server_paperless.services.files import FileService
from mcp_server_paperless.services.pages import PageService
from mcp_server_paperless.services.pdf import PdfService
from mcp_server_paperless.services.sharing import SharingService
from mcp_server_paperless.services.tasks import TaskService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp_server_paperless.domain.rendering import Rendered, RenderRequest

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
    return PdfService(fake, fake, TaskService(fake), web_url=WEB)


def sharing(fake: FakePaperless) -> SharingService:
    return SharingService(fake, fake, fake, web_url=WEB)


FILE_CALLS: dict[str, Callable[[FakePaperless], Awaitable[object]]] = {
    "download": lambda fake: files(fake).download(LOST, "original"),
    "pages": lambda fake: PageService(fake, NoRenderer(), web_url=WEB).show(LOST, "1"),
    "part": lambda fake: files(fake).part(LOST, "original", offset=0),
}


@pytest.mark.parametrize("call", FILE_CALLS.values(), ids=FILE_CALLS)
@pytest.mark.parametrize("nameless", [False, True])
async def test_what_needs_the_original_says_that_paperless_cannot_find_it(
    call: Callable[[FakePaperless], Awaitable[object]], *, nameless: bool
) -> None:
    fake = fake_with() if nameless else fake_with(LOST)
    if nameless:
        fake.state.nameless.add(LOST)

    with pytest.raises(NotFound) as raised:
        _ = await call(fake)

    assert raised.value.message.startswith(f"Paperless cannot find the original of document {LOST}")
    assert raised.value.hint == "Ask for the archive version, which is there."


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


PDF_CALLS: dict[str, Callable[[PdfService], Awaitable[object]]] = {
    "rotate": lambda service: service.rotate([WHOLE, LOST], 90),
    "merge": lambda service: service.merge([WHOLE, LOST], None, delete_originals=True),
    "split": lambda service: service.split(LOST, ["1", "1"]),
    "delete_pages": lambda service: service.delete_pages(LOST, "1"),
    "remove_password": lambda service: service.remove_password(LOST, "x"),
    "reprocess": lambda service: service.reprocess([WHOLE, LOST]),
}


@pytest.mark.parametrize("call", PDF_CALLS.values(), ids=PDF_CALLS)
async def test_a_pdf_operation_on_a_missing_file_starts_nothing(
    call: Callable[[PdfService], Awaitable[object]],
) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await call(pdf(fake_with(LOST)))

    assert raised.value.describe() == (
        f"Paperless cannot find a file of document {LOST}. Hint: {STILL_READ}"
    )


async def test_a_pdf_operation_names_every_document_without_its_file() -> None:
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await pdf(fake_with(LOST, ALSO)).rotate([ALSO, WHOLE, LOST], 90)

    assert raised.value.describe() == (
        f"Paperless cannot find a file of documents {ALSO} and {LOST}. Hint: Leave them out; "
        "their text and metadata can still be read. Restore the files from a backup."
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
    document.version_files = {
        number: FakeFile(document.original, None, "", document.mime_type) for number in (LOST, 99)
    }
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
