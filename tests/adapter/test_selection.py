"""PaperlessDocumentReader: a whole selection in one request, and rows with chosen fields."""

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, override

import httpx2
import pytest

from adapter.conftest import streaming
from mcp_server_paperless.domain.documents import CustomFieldValue, DocumentQuery, Note
from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    AnswerTooLarge,
    NotFound,
    PermissionDenied,
    UpstreamUnavailable,
)
from mcp_server_paperless.paperless.documents import FIELDS, PaperlessDocumentReader

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from adapter.conftest import RecordingSleep

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-69")]

type Json = dict[str, object]

ROW: Json = {
    "id": 7,
    "title": "Invoice",
    "created": "2026-11-11",
    "added": "2026-11-11T19:47:09Z",
    "correspondent": 1,
    "document_type": None,
    "storage_path": None,
    "tags": [1, 3],
    "custom_fields": [{"value": True, "field": 4}],
    "user_can_change": True,
}


class Chunked(httpx2.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body: bytes = body

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.body


class Listing:
    """Answers the document list with `rows` and keeps the requests."""

    def __init__(self, *rows: Json, status: int = 200, encoding: str | None = None) -> None:
        self.rows: tuple[Json, ...] = rows
        self.status: int = status
        self.headers: dict[str, str] = {} if encoding is None else {"Content-Encoding": encoding}
        self.sent: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append(request)
        body = {"count": len(self.rows), "next": None, "previous": None, "results": self.rows}
        content = json.dumps(body if self.status == HTTPStatus.OK else {"detail": "x"})
        return httpx2.Response(
            self.status,
            headers={"Content-Type": "application/json", **self.headers},
            stream=Chunked(content.encode()),
        )


def reader(answer: Listing) -> tuple[PaperlessDocumentReader, RecordingSleep]:
    client, sleep = streaming(answer)
    return PaperlessDocumentReader(client), sleep


async def test_a_selection_is_one_uncompressed_request_for_ordinary_rows() -> None:
    server = Listing(ROW)
    documents, _ = reader(server)

    page = await documents.select_documents(
        DocumentQuery(title_contains="x", ids=(7, 8)), limit=1001, max_bytes=10_000
    )

    [request] = server.sent
    assert dict(request.url.params) == {
        "title__icontains": "x",
        "id__in": "7,8",
        "fields": FIELDS,
        "page": "1",
        "page_size": "1001",
    }
    assert request.url.path == "/api/documents/"
    assert request.headers["Accept-Encoding"] == "identity"
    assert request.extensions["timeout"]["read"] == 60.0
    assert (page.count, page.next_page, [hit.id for hit in page.hits]) == (1, None, [7])
    assert page.hits[0].user_can_change is True


async def test_a_selection_over_the_byte_limit_or_compressed_is_refused() -> None:
    documents, _ = reader(Listing(ROW))
    with pytest.raises(AnswerTooLarge):
        _ = await documents.select_documents(DocumentQuery(), limit=1001, max_bytes=100)

    documents, _ = reader(Listing(ROW, encoding="gzip"))
    with pytest.raises(AnswerCompressed):
        _ = await documents.select_documents(DocumentQuery(), limit=1001, max_bytes=10_000)


async def test_a_selection_like_a_hidden_document_is_not_found() -> None:
    documents, _ = reader(Listing(status=403))

    with pytest.raises(NotFound, match="document 5"):
        _ = await documents.select_documents(DocumentQuery(more_like=5), limit=1, max_bytes=1000)
    with pytest.raises(PermissionDenied):
        _ = await documents.select_documents(DocumentQuery(), limit=1, max_bytes=1000)


async def test_rows_ask_for_the_named_fields_only() -> None:
    server = Listing({"id": 7, "title": "Invoice"})
    documents, _ = reader(server)

    [row] = await documents.read_rows([7, 8], ("title", "version_count", "notes"), max_bytes=1000)

    [request] = server.sent
    assert dict(request.url.params) == {
        "id__in": "7,8",
        "fields": "id,title,versions,notes",
        "page": "1",
        "page_size": "2",
    }
    assert request.headers["Accept-Encoding"] == "identity"
    assert request.extensions["timeout"]["read"] == 60.0
    assert (row.id, row.title, row.notes, row.version_count) == (7, "Invoice", None, None)


async def test_rows_map_every_field() -> None:
    server = Listing(
        {
            **ROW,
            "modified": "2026-11-11T19:59:00Z",
            "archive_serial_number": 2,
            "owner": 3,
            "original_file_name": "a.pdf",
            "mime_type": "application/pdf",
            "page_count": 4,
            "notes": [{"id": 1, "note": "n", "user": {"username": "u"}}],
            "versions": [
                {"id": n, "added": "2026-11-11T19:47:09Z", "is_root": n == 7} for n in (9, 7)
            ],
        }
    )
    documents, _ = reader(server)

    [row] = await documents.read_rows([7], ("title",), max_bytes=10_000)

    assert row.title == "Invoice"
    assert row.created is not None
    assert (row.created.isoformat(), row.correspondent, row.document_type) == (
        "2026-11-11",
        1,
        None,
    )
    assert row.added is not None
    assert row.modified is not None
    assert (row.added.hour, row.modified.minute) == (19, 59)
    assert row.storage_path is None
    assert row.tags == (1, 3)
    assert row.custom_fields == (CustomFieldValue(field=4, value=True),)
    assert (row.archive_serial_number, row.owner, row.page_count) == (2, 3, 4)
    assert (row.original_file_name, row.mime_type) == ("a.pdf", "application/pdf")
    assert row.notes == (Note(id=1, note="n", created=None, user="u"),)
    assert (row.version_count, row.user_can_change) == (2, True)


async def test_no_ids_are_no_request() -> None:
    server = Listing()
    documents, _ = reader(server)

    assert await documents.read_rows([], ("title",), max_bytes=10) == ()
    assert server.sent == []


async def test_rows_over_the_byte_limit_are_refused() -> None:
    documents, _ = reader(Listing(ROW))

    with pytest.raises(AnswerTooLarge):
        _ = await documents.read_rows([7], ("title",), max_bytes=100)


async def test_a_429_is_told_as_one() -> None:
    documents, _ = reader(Listing(status=429))

    with pytest.raises(UpstreamUnavailable) as raised:
        _ = await documents.select_documents(DocumentQuery(), limit=1, max_bytes=1000)

    assert raised.value.status == HTTPStatus.TOO_MANY_REQUESTS
