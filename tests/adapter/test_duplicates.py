"""What the duplicate check reads, on answers as the source of Paperless 3.5.1 shapes them.

The adapter sends what 3.5.1 documents (schema guard); a rejected upload is a failed task without
an error message whose only related document is the existing one.
"""

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from adapter.conftest import streaming
from adapter.test_admin import TASK_WIRE
from adapter.test_tasks import FAILED, UUID, Tasks
from adapter.test_trash import DOCUMENT, VERSION, Chunks, Recorded, listing
from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    NotFound,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
)
from mcp_server_paperless.domain.tasks import REJECTED
from mcp_server_paperless.paperless.admin import PaperlessAdminReader
from mcp_server_paperless.paperless.documents import PaperlessDocumentReader
from mcp_server_paperless.paperless.tasks import PaperlessTaskReader
from mcp_server_paperless.paperless.trash import PaperlessTrashReader
from mcp_server_paperless.services.duplicates import Bounds, DuplicateFinder

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory
    from adapter.schema_guard import Handler
    from adapter.test_trash import Json

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-47")]

SHA = "b5989a99fade41ed36af961e918ef43da955b8b08a4c8aa78430f2e6b7da43cb"
REJECTION = {"duplicate_of": 12, "duplicate_in_trash": False}


def finder(answer: Handler) -> DuplicateFinder:
    """The whole check on the adapter, with a limit of 8,000 bytes for the trash."""
    client, _ = streaming(answer)
    return DuplicateFinder(
        PaperlessDocumentReader(client),
        PaperlessTrashReader(client),
        web_url="http://p.test",
        bounds=Bounds(trash_bytes=8_000),
    )


def empty() -> Recorded:
    return Recorded({"count": 0, "next": None, "previous": None, "results": []})


async def test_the_checksum_is_a_filter_of_the_search(make_client: ClientFactory) -> None:
    server = empty()

    _ = await PaperlessDocumentReader(make_client(server)).search_documents(
        DocumentQuery(checksum=SHA), page=1, page_size=10
    )

    [(_, path, params, _)] = server.sent
    assert (path, params["checksum__iexact"]) == ("/api/documents/", SHA)


@pytest.mark.parametrize("recent", [False, True])
async def test_the_versions_are_listed_without_anything_else(
    make_client: ClientFactory, *, recent: bool
) -> None:
    row = {k: DOCUMENT[k] for k in ("id", "title", "versions")}
    body = {
        "count": 3,
        "next": "http://p.test/api/documents/?page=3",
        "previous": None,
        "results": [row],
    }
    server = Recorded(body)

    page = await PaperlessDocumentReader(make_client(server)).list_versions(
        page=2, page_size=1, recent=recent
    )

    [(method, path, params, _)] = server.sent
    assert (method, path) == ("GET", "/api/documents/")
    assert params == {
        "fields": "id,title,versions",
        "ordering": "-modified,-id" if recent else "-id",
        "page": "2",
        "page_size": "1",
    }
    assert (page.count, page.next_page) == (3, 3)
    [found] = page.rows
    assert (found.id, found.title) == (3301, "plmcp27-probe-b")
    [version] = found.versions
    assert (version.id, version.is_root, version.checksum) == (3301, True, SHA)


async def test_a_page_of_versions_beyond_the_last_is_not_found(make_client: ClientFactory) -> None:
    reader = PaperlessDocumentReader(make_client(Recorded({"detail": "Invalid page."}, 404)))

    with pytest.raises(NotFound, match="Page 9 is beyond the last page"):
        _ = await reader.list_versions(page=9, page_size=1)


async def test_the_duplicates_are_read_from_the_single_document(
    make_client: ClientFactory,
) -> None:
    deleted = "2026-11-13T14:26:55.220835Z"
    server = Recorded(
        {
            "duplicate_documents": [
                {"id": 5, "title": "live", "deleted_at": None},
                {"id": 6, "title": "gone", "deleted_at": deleted},
            ]
        }
    )

    found = await PaperlessDocumentReader(make_client(server)).get_duplicates(3)

    assert server.sent == [
        ("GET", "/api/documents/3/", {"fields": "duplicate_documents"}, None),
    ]
    assert [(d.id, d.title, d.deleted_at) for d in found] == [
        (5, "live", None),
        (6, "gone", datetime(2026, 11, 13, 14, 26, 55, 220835, tzinfo=UTC)),
    ]


async def test_the_root_is_read_by_the_id_of_a_version(make_client: ClientFactory) -> None:
    server = Recorded({"root_id": 3})

    assert await PaperlessDocumentReader(make_client(server)).get_root(5) == 3
    assert server.sent[0][1] == "/api/documents/5/root/"


async def test_a_hidden_root_reads_like_a_missing_one(make_client: ClientFactory) -> None:
    texts: list[str] = []
    for server in (
        Recorded({"detail": "No Document matches the given query."}, 404),
        Recorded("Insufficient permissions", 403, html=True),
    ):
        with pytest.raises(NotFound) as raised:
            _ = await PaperlessDocumentReader(make_client(server)).get_root(5)
        texts.append(raised.value.describe())
    assert texts[0] == texts[1]
    assert "No document 5" in texts[0]


async def test_a_trashed_document_has_its_roots_checksum_and_a_version_none(
    make_client: ClientFactory,
) -> None:
    newer = {"id": 3302, "added": "2026-11-13T14:26:54Z", "checksum": "c0", "is_root": False}
    both = {**DOCUMENT, "versions": [newer, *cast("list[Json]", DOCUMENT["versions"])]}
    server = Recorded(listing(both, VERSION, {**DOCUMENT, "versions": []}))

    page = await PaperlessTrashReader(make_client(server)).list_trash(page=1, page_size=25)

    assert [e.checksum for e in page.entries] == [SHA, None, None]


async def test_a_rejected_upload_names_no_document_the_task_made(
    make_client: ClientFactory,
) -> None:
    row = {**FAILED, "result_data": REJECTION, "related_document_ids": [12]}

    status = await PaperlessTaskReader(make_client(Tasks(row))).get_task(UUID)

    assert (status.state, status.message, status.document_ids) == ("failure", REJECTED, ())
    assert status.duplicate_of == 12


@pytest.mark.parametrize("result", [None, "x", {"duplicate_of": "12"}, {"document_id": 3}])
async def test_other_results_are_no_rejection(make_client: ClientFactory, result: object) -> None:
    row = {**FAILED, "result_data": result, "related_document_ids": [3]}

    status = await PaperlessTaskReader(make_client(Tasks(row))).get_task(UUID)

    assert (status.duplicate_of, status.document_ids) == (None, (3,))


async def test_the_admin_list_does_not_show_the_existing_document(
    make_client: ClientFactory,
) -> None:
    rejected = {
        **TASK_WIRE,
        "status": "failure",
        "result_data": REJECTION,
        "related_document_ids": [12],
    }
    server = Recorded({"count": 1, "next": None, "previous": None, "results": [rejected]})

    page = await PaperlessAdminReader(make_client(server)).list_tasks(
        state=None, task_type=None, acknowledged=None, page=1, page_size=25
    )

    [task] = page.rows
    assert (task.document_ids, task.message) == ((), REJECTED)
    assert "12" not in (task.message or "")


async def test_a_listing_without_the_permission_is_denied(make_client: ClientFactory) -> None:
    server = Recorded({"detail": "You do not have permission."}, 403)

    with pytest.raises(PermissionDenied):
        _ = await PaperlessDocumentReader(make_client(server)).list_versions(page=1, page_size=1)


@pytest.mark.parametrize("checksum", [SHA, None])
async def test_the_checksum_of_a_version_needs_nothing_else_of_the_metadata(
    checksum: str | None,
) -> None:
    """Paperless answers sizes and names as null when the file is gone, but keeps the checksum.

    Without the schema guard: the schema says they are never null (documents/views.py does not).
    """
    server = Recorded(
        {
            "original_checksum": checksum,
            "original_size": None,
            "original_mime_type": "text/plain",
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
    )

    client, _ = streaming(server)

    found = await PaperlessDocumentReader(client).get_version_checksum(3, 5)

    assert found == checksum
    assert server.sent == [("GET", "/api/documents/3/metadata/", {"version": "5"}, None)]


async def test_a_version_that_is_gone_has_no_checksum(make_client: ClientFactory) -> None:
    reader = PaperlessDocumentReader(make_client(Recorded({"detail": "Not found."}, 404)))

    with pytest.raises(NotFound, match="No version 5"):
        _ = await reader.get_version_checksum(3, 5)


@pytest.mark.decision("decision-52")
async def test_a_trash_over_the_byte_limit_is_only_counted() -> None:
    row = {**DOCUMENT, "content": "x" * 5_000}
    sizes: list[str] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        if request.url.path != "/api/trash/":
            return httpx2.Response(200, json=listing())
        sizes.append(dict(request.url.params)["page_size"])
        rows = [row] if sizes[-1] == "1" else [row, row]
        body = json.dumps(listing(*rows, count=2)).encode()
        return httpx2.Response(200, stream=Chunks(body))

    found = await finder(answer).cleared(b"file", "upload", confirm=False)

    assert sizes == ["500", "1", "1"]
    assert found.matches == ()
    assert [g for g in found.not_checked if g.startswith("Trash: neither")]


@pytest.mark.decision("decision-52")
@pytest.mark.parametrize(
    ("failing", "error"),
    [
        (httpx2.Response(500), UpstreamUnavailable),
        (
            httpx2.Response(200, headers={"Content-Encoding": "gzip"}, stream=Chunks(b"x")),
            AnswerCompressed,
        ),
        (httpx2.ReadTimeout("slow"), UpstreamTimeout),
        (httpx2.ConnectError("down"), UpstreamUnavailable),
    ],
)
async def test_every_other_failure_of_the_trash_fails_the_check(
    failing: httpx2.Response | httpx2.TransportError, error: type[Exception]
) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        if request.url.path != "/api/trash/":
            return httpx2.Response(200, json=listing())
        if isinstance(failing, httpx2.TransportError):
            raise failing
        return failing

    with pytest.raises(error):
        _ = await finder(answer).cleared(b"file", "upload", confirm=True)
