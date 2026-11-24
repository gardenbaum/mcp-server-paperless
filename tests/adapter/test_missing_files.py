"""Documents whose file Paperless 3.5.1 cannot find, as documents/views.py answers for them.

The metadata has a null size; a file or thumbnail is a 404, as for a document that does not exist;
the bulk download is a 500. Without the schema guard: the schema says sizes are never null.
"""

import asyncio
from typing import TYPE_CHECKING, override

import httpx2
import pytest

from adapter.conftest import streaming
from mcp_server_paperless.domain.errors import NotFound, PaperlessMcpError, UpstreamUnavailable
from mcp_server_paperless.paperless.admin import LOOKUP_SECONDS, PaperlessAdminReader
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy
from mcp_server_paperless.paperless.documents import PaperlessDocumentReader

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from mcp_server_paperless.domain.admin import ArchiveContent
    from mcp_server_paperless.domain.documents import FileVersion

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

LOST: Json = {
    "original_checksum": "a" * 64,
    "original_size": None,
    "original_mime_type": "application/pdf",
    "media_filename": None,
    "has_archive_version": False,
    "original_metadata": None,
    "archive_checksum": None,
    "archive_media_filename": None,
    "original_filename": None,
    "archive_size": None,
    "archive_metadata": None,
    "barcodes": [],
    "lang": "en",
}
KEPT: Json = {**LOST, "original_size": 5, "media_filename": "a.pdf", "original_filename": "a.pdf"}
ARCHIVED: Json = {"has_archive_version": True, "archive_checksum": "b" * 64}
RESTORE = "Its text and metadata can still be read; restore the file from a backup."


class Server:
    """Answers the metadata of documents; everything else with `status`."""

    def __init__(self, metadata: dict[int, Json], status: int = 404) -> None:
        self.metadata: dict[int, Json] = metadata
        self.status: int = status
        self.sent: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append(str(request.url).removeprefix("http://paperless.test"))
        number, _, tail = request.url.path.removeprefix("/api/documents/").partition("/")
        if tail == "metadata/" and int(number) in self.metadata:
            return httpx2.Response(200, json=self.metadata[int(number)])
        status = 404 if tail == "metadata/" else self.status
        return httpx2.Response(status, json={"detail": "Not found."})


def reader(server: Server) -> PaperlessDocumentReader:
    client, _ = streaming(server)
    return PaperlessDocumentReader(client)


async def test_metadata_without_size_and_names_is_read() -> None:
    found = await reader(Server({3: LOST})).get_document_metadata(3)

    assert found.model_dump() == {
        "original_filename": None,
        "original_mime_type": "application/pdf",
        "original_size": None,
        "original_checksum": "a" * 64,
        "media_filename": None,
        "has_archive_version": False,
        "archive_size": None,
        "archive_checksum": None,
        "archive_media_filename": None,
    }


@pytest.mark.parametrize(
    ("facts", "kind", "message", "hint"),
    [
        (LOST, "original", "the original of document 3", RESTORE),
        # Paperless answers the original when there is no archive version.
        (LOST, "archive", "the original of document 3", RESTORE),
        (
            {**LOST, **ARCHIVED, "archive_size": 7},
            "original",
            "the original of document 3",
            "Ask for the archive version, which is there.",
        ),
        (
            {**KEPT, **ARCHIVED},
            "archive",
            "the archive version of document 3",
            "Ask for the original, which is there.",
        ),
        (
            {**LOST, **ARCHIVED},
            "archive",
            "the archive version of document 3",
            RESTORE,
        ),
    ],
)
async def test_a_file_that_is_missing_is_not_a_missing_document(
    facts: Json, kind: FileVersion, message: str, hint: str
) -> None:
    server = Server({3: facts})

    with pytest.raises(NotFound) as raised:
        _ = await reader(server).get_document_file(3, kind, max_bytes=100)
    with pytest.raises(NotFound) as opened:
        async with reader(server).open_document_file(3, kind, version_id=5):
            raise AssertionError

    assert raised.value.message == f"Paperless cannot find {message}."
    assert raised.value.hint == hint
    assert opened.value.message == f"Paperless cannot find {message} (version 5)."
    original = "true" if kind == "original" else "false"
    assert server.sent == [
        f"/api/documents/3/download/?original={original}",
        "/api/documents/3/metadata/",
        f"/api/documents/3/download/?original={original}&version=5",
        "/api/documents/3/metadata/?version=5",
    ]


@pytest.mark.parametrize("kind", ["original", "archive"])
async def test_a_file_that_is_listed_but_not_sent_is_not_called_missing(kind: FileVersion) -> None:
    server = Server({3: {**KEPT, **ARCHIVED, "archive_size": 7}})

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await reader(server).get_document_file(3, kind, max_bytes=100)

    assert not isinstance(raised.value, NotFound)
    assert raised.value.describe() == (
        "Paperless did not send the file of document 3 although it lists it. Hint: Try again; if "
        "it stays so, an administrator can check the media directory of Paperless."
    )


async def test_without_an_archive_version_the_original_that_was_not_sent_is_neutral() -> None:
    with pytest.raises(PaperlessMcpError, match="although it lists it"):
        _ = await reader(Server({3: KEPT})).get_document_file(3, "archive", max_bytes=100)


async def test_a_thumbnail_that_is_missing_is_not_a_missing_document() -> None:
    server = Server({3: KEPT})

    with pytest.raises(NotFound) as raised:
        _ = await reader(server).get_document_thumbnail(3)

    assert raised.value.message == "Paperless cannot find the thumbnail of document 3."
    assert raised.value.hint == (
        "Paperless makes it anew when the document is reprocessed, if it still has the file."
    )
    assert server.sent == ["/api/documents/3/thumb/", "/api/documents/3/metadata/"]


DOCUMENTS = {1: KEPT, 2: {**LOST, **ARCHIVED, "archive_size": 7}, 3: {**KEPT, **ARCHIVED}, 4: LOST}


@pytest.mark.parametrize(
    ("content", "lost"),
    [
        ("originals", "2 and 4 (2 of the 4 chosen)"),
        ("archive", "3 and 4 (2 of the 4 chosen)"),
        ("both", "2, 3 and 4 (3 of the 4 chosen)"),
    ],
)
async def test_an_archive_without_a_file_names_the_documents(
    content: ArchiveContent, lost: str
) -> None:
    server = Server(DOCUMENTS, status=500)
    client, _ = streaming(server)
    found = PaperlessAdminReader(client)

    with pytest.raises(NotFound) as raised:
        _ = await found.download_documents(
            [1, 2, 3, 4],
            content=content,
            compression="none",
            follow_formatting=False,
            max_bytes=10,
        )
    with pytest.raises(NotFound) as opened:
        async with found.open_archive(
            [1, 2, 3, 4],
            content=content,
            compression="none",
            follow_formatting=False,
            wait_seconds=1,
        ):
            raise AssertionError

    assert raised.value.describe() == (
        f"Paperless cannot find a file of documents {lost}. Hint: Leave them out; their text and "
        "metadata can still be read. Restore the files from a backup."
    )
    assert opened.value.describe() == raised.value.describe()


def archive_of(server: Server, numbers: list[int], content: ArchiveContent) -> Awaitable[object]:
    client, _ = streaming(server)
    return PaperlessAdminReader(client).download_documents(
        numbers, content=content, compression="none", follow_formatting=False, max_bytes=10
    )


@pytest.mark.parametrize(
    ("content", "numbers", "hint"),
    [
        ("originals", [1, 2], "Choosing the archive versions instead works for all of them. "),
        ("both", [1, 2], "Choosing the archive versions instead works for all of them. "),
        ("archive", [1, 3], "Choosing the originals instead works for all of them. "),
        ("archive", [2, 3], ""),
        ("both", [2, 3], ""),
    ],
)
async def test_the_other_content_is_named_if_it_works_for_all(
    content: ArchiveContent, numbers: list[int], hint: str
) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await archive_of(Server(DOCUMENTS, status=500), numbers, content)

    assert raised.value.hint is not None
    assert raised.value.hint.startswith(hint)
    assert ("instead works" in raised.value.hint) is bool(hint)


async def test_a_document_that_cannot_be_checked_does_not_hide_the_others() -> None:
    with pytest.raises(NotFound) as raised:
        _ = await archive_of(Server(DOCUMENTS, status=500), [99, 4], "archive")

    assert raised.value.describe() == (
        "Paperless cannot find a file of document 4 (1 of the 2 chosen). Hint: Leave it out; its "
        "text and metadata can still be read. Restore the file from a backup. The list may be "
        "incomplete: 1 could not be checked."
    )


@pytest.mark.parametrize(
    ("server", "asked"),
    [
        (Server({1: KEPT}, status=500), 2),
        # Cannot be told: the failure stays what it was.
        (Server({}, status=500), 2),
        # Not the failure of Paperless itself.
        (Server({1: LOST}, status=502), 1),
    ],
)
async def test_another_failure_of_an_archive_stays_what_it_is(server: Server, asked: int) -> None:
    client, _ = streaming(server)

    with pytest.raises(UpstreamUnavailable, match=f"HTTP {server.status}"):
        _ = await PaperlessAdminReader(client).download_documents(
            [1], content="both", compression="none", follow_formatting=False, max_bytes=10
        )

    assert len(server.sent) == asked


class Stuck(httpx2.AsyncBaseTransport):
    """Answers the archive with a 500 and the metadata of document 4 only."""

    @override
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            return httpx2.Response(500)
        if request.url.path != "/api/documents/4/metadata/":
            await asyncio.sleep(60)
        return httpx2.Response(200, json=LOST)


async def test_the_search_for_missing_files_ends_with_what_was_found() -> None:
    http = httpx2.AsyncClient(base_url="http://paperless.test", transport=Stuck())
    client = PaperlessClient(
        http, StaticTokenProvider("t"), retry=RetryPolicy(), upload_timeout=1.0
    )
    seen = PaperlessAdminReader(client, lookup_seconds=0.05)

    for document_ids, message in ([4, 5], "a file of document 4"), ([5, 4], "HTTP 500"):
        with pytest.raises((NotFound, UpstreamUnavailable), match=message):
            _ = await seen.download_documents(
                document_ids,
                content="both",
                compression="none",
                follow_formatting=False,
                max_bytes=10,
            )

    assert LOOKUP_SECONDS == 30
    await http.aclose()


async def test_a_failure_of_whoever_reads_an_opened_archive_stays_as_it_is() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200, content=b"PK"))
    error = UpstreamUnavailable("mine", status=500)

    with pytest.raises(UpstreamUnavailable, match="mine"):
        async with PaperlessAdminReader(client).open_archive(
            [1], content="archive", compression="none", follow_formatting=False, wait_seconds=1
        ):
            raise error
