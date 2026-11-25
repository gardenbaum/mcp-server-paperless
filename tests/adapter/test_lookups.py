"""Counts over a selection, word suggestions and trying a storage path on the 3.5.1 wire format."""

import json
from typing import TYPE_CHECKING

import httpx2
import pytest

from adapter.test_taxonomy import STORAGE_PATH
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.paperless.documents import PaperlessDocumentReader
from mcp_server_paperless.paperless.taxonomy import PaperlessTaxonomyReader

if TYPE_CHECKING:
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
