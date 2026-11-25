"""Counts over a selection, word suggestions and trying a storage path on the 3.5.1 wire format."""

import gzip
import json
from typing import TYPE_CHECKING, override

import httpx2
import pytest

from adapter.conftest import streaming
from adapter.test_taxonomy import STORAGE_PATH
from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    AnswerTooLarge,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.paperless.documents import PaperlessDocumentReader
from mcp_server_paperless.paperless.taxonomy import PATH_BYTES, PaperlessTaxonomyReader

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio


class Server:
    """Gives one answer and keeps the requests."""

    def __init__(self, answer: object, status: int = 200) -> None:
        self.answer: object = answer
        self.status: int = status
        self.sent: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append(request)
        return httpx2.Response(self.status, json=self.answer)

    def asked(self) -> list[tuple[str, str, dict[str, str], object]]:
        return [
            (r.method, r.url.path, dict(r.url.params), json.loads(r.content) if r.content else None)
            for r in self.sent
        ]


def counted(*pairs: tuple[int, int]) -> list[dict[str, int]]:
    return [{"id": number, "document_count": count} for number, count in pairs]


async def test_only_what_the_documents_have_is_counted(make_client: ClientFactory) -> None:
    server = Server(
        {
            "selected_correspondents": counted((1, 2), (2, 0)),
            "selected_tags": counted((4, 1), (5, 2)),
            "selected_document_types": counted((1, 0)),
            "selected_storage_paths": counted((3, 1)),
            "selected_custom_fields": [],
        }
    )

    found = await PaperlessDocumentReader(make_client(server)).count_selection([7, 8])

    assert found == {
        "correspondent": {1: 2},
        "tag": {4: 1, 5: 2},
        "document_type": {},
        "storage_path": {3: 1},
        "custom_field": {},
    }
    assert server.asked() == [("POST", "/api/documents/selection_data/", {}, {"documents": [7, 8]})]


@pytest.mark.parametrize(
    ("status", "answer", "error"),
    [
        (400, {"documents": ["Some documents in documents don't exist"]}, Validation),
        (403, {"detail": "x"}, PermissionDenied),
    ],
)
async def test_a_document_that_is_gone_or_hidden_is_an_error(
    make_client: ClientFactory, status: int, answer: object, error: type[Exception]
) -> None:
    reader = PaperlessDocumentReader(make_client(Server(answer, status)))

    with pytest.raises(error):
        _ = await reader.count_selection([7])


async def test_words_are_asked_for_by_their_beginning(make_client: ClientFactory) -> None:
    server = Server(["invoice", "inventory"])

    found = await PaperlessDocumentReader(make_client(server)).suggest_words("inv", 2)

    assert found == ("invoice", "inventory")
    assert server.asked() == [
        ("GET", "/api/search/autocomplete/", {"term": "inv", "limit": "2"}, None)
    ]


async def test_the_template_of_a_storage_path_is_read(make_client: ClientFactory) -> None:
    server = Server(STORAGE_PATH)

    found = await PaperlessTaxonomyReader(make_client(server)).get_storage_path_template(1)

    assert found == "e2e/{{ created_year }}/{{ title }}"
    assert server.asked() == [("GET", "/api/storage_paths/1/", {}, None)]


async def test_a_storage_path_that_is_gone_is_not_found(make_client: ClientFactory) -> None:
    reader = PaperlessTaxonomyReader(make_client(Server({"detail": "x"}, 404)))

    with pytest.raises(NotFound, match="storage path 1"):
        _ = await reader.get_storage_path_template(1)


@pytest.mark.parametrize("answer", ["2026/Invoice.pdf", ""])
async def test_a_template_is_tried_on_a_document(make_client: ClientFactory, answer: str) -> None:
    server = Server(answer)

    found = await PaperlessTaxonomyReader(make_client(server)).render_storage_path(7, "{{ title }}")

    assert found == answer
    assert server.asked() == [
        ("POST", "/api/storage_paths/test/", {}, {"document": 7, "path": "{{ title }}"})
    ]


async def test_null_comes_as_an_empty_answer(make_client: ClientFactory) -> None:
    # 3.5.1 sends no body for null (probed, PLMCP-83.2).
    client = make_client(lambda _: httpx2.Response(200, content=b""))

    assert await PaperlessTaxonomyReader(client).render_storage_path(7, "{{ nope }}") is None


class Endless(httpx2.AsyncByteStream):
    """A body that goes on for ever; it counts what was taken of it."""

    def __init__(self) -> None:
        self.taken: int = 0
        self.closed: bool = False

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'"'
        while True:
            self.taken += 1000
            yield b"a" * 1000

    @override
    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.decision("decision-100")
async def test_a_path_without_end_is_not_read_to_its_end() -> None:
    body = Endless()
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, stream=body, headers={"Content-Type": "application/json"})

    client, _ = streaming(handler)
    with pytest.raises(AnswerTooLarge) as raised:
        _ = await PaperlessTaxonomyReader(client).render_storage_path(7, "{{ 'a' * 500000000 }}")

    assert body.taken <= PATH_BYTES + 1000
    assert body.closed
    assert f"more than {PATH_BYTES} bytes" in raised.value.message
    assert len(sent) == 1
    # A compressed body could unpack to any size before it is measured.
    assert sent[0].headers["Accept-Encoding"] == "identity"


@pytest.mark.decision("decision-100")
async def test_a_path_up_to_the_limit_is_read_and_one_byte_more_is_not() -> None:
    sizes: list[int] = [PATH_BYTES, PATH_BYTES + 1]
    client, _ = streaming(
        lambda _: httpx2.Response(200, content=b'"' + b"a" * (sizes.pop(0) - 2) + b'"')
    )
    reader = PaperlessTaxonomyReader(client)

    assert len(await reader.render_storage_path(7, "x") or "") == PATH_BYTES - 2
    with pytest.raises(AnswerTooLarge):
        _ = await reader.render_storage_path(7, "x")


async def test_a_compressed_or_odd_answer_is_not_taken() -> None:
    answers = [
        httpx2.Response(200, content=gzip.compress(b'"a"'), headers={"Content-Encoding": "gzip"}),
        httpx2.Response(200, json={"path": "a"}),
    ]
    client, _ = streaming(lambda _: answers.pop(0))
    reader = PaperlessTaxonomyReader(client)

    with pytest.raises(AnswerCompressed):
        _ = await reader.render_storage_path(7, "x")
    with pytest.raises(PaperlessMcpError, match="unexpected response"):
        _ = await reader.render_storage_path(7, "x")


@pytest.mark.parametrize(
    ("status", "error"), [(400, Validation), (403, PermissionDenied), (500, UpstreamUnavailable)]
)
async def test_a_refused_template_is_the_error_of_its_status(
    make_client: ClientFactory, status: int, error: type[PaperlessMcpError]
) -> None:
    reader = PaperlessTaxonomyReader(make_client(Server({"detail": "x"}, status)))

    with pytest.raises(error):
        _ = await reader.render_storage_path(7, "x")
