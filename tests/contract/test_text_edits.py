"""Contract of DocumentWriter.set_document_text: the same assertions on the fake and live.

Every test works on documents of its own. Read in the source of 3.5.1: the text belongs to one
version; the root's is stripped and validated, another version's is stored as it is; `fields`
without `content` would answer 200 and change nothing. Reading the text of versions is in
test_document_versions.py.
"""

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

import pytest

from contract.test_document_versions import CHANGING, MISSING, add_version
from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.paperless.documents import unseen_version

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-48"),
    pytest.mark.contract("DocumentWriter.set_document_text"),
]

INDEX_SECONDS = 60.0


async def text(ports: Ports, document: int, version: int | None = None) -> str:
    return (await ports.documents.get_document_content(document, version_id=version)).content


async def found(ports: Ports, word: str) -> list[int]:
    """The documents a full-text search for ``word`` finds."""
    page = await ports.documents.search_documents(DocumentQuery(query=word), page=1, page_size=100)
    return [hit.id for hit in page.hits]


async def finds(ports: Ports, word: str, expected: list[int]) -> bool:
    """Whether a search finds exactly ``expected``, waiting for the index."""
    deadline = time.monotonic() + INDEX_SECONDS
    while await found(ports, word) != expected:
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.5)
    return True


async def test_the_text_of_a_document_is_replaced_and_comes_back(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    before = await ports.documents.get_document(document)

    stored = await ports.document_writes.set_document_text(document, document, "plmcp75 typed")

    assert stored == "plmcp75 typed"
    assert await text(ports, document) == "plmcp75 typed"
    after = await ports.documents.get_document(document)
    assert after.modified >= before.modified
    assert after.model_copy(update={"modified": before.modified}) == before


async def test_only_the_named_version_changes(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    root = await text(ports, document)
    middle = await add_version(backend, document, "plmcp75 middle")
    newest = await add_version(backend, document, "plmcp75 newest")
    expected = {document: root, middle: "plmcp75 middle", newest: "plmcp75 newest"}

    for version in (newest, middle, document):
        expected[version] = f"plmcp75 corrected {version}"
        stored = await ports.document_writes.set_document_text(document, version, expected[version])
        assert stored == expected[version]
        assert {v: await text(ports, document, v) for v in expected} == expected

    assert await text(ports, document) == expected[newest]


async def test_only_the_text_of_the_root_loses_outer_whitespace(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    version = await add_version(backend, document, "plmcp75 whitespace")
    padded = "  plmcp75\r\none\ttwo  \n"

    root = await ports.document_writes.set_document_text(document, document, padded)
    other = await ports.document_writes.set_document_text(document, version, padded)

    assert root == await text(ports, document, document) == padded.strip()
    assert other == await text(ports, document, version) == padded


async def test_a_text_can_be_emptied_and_holds_any_letters(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()

    for content in ("", "Zürich 日本 \U0001f600 e\u0301\x0cx"):
        assert await ports.document_writes.set_document_text(document, document, content) == content
        assert await text(ports, document) == content


async def test_a_nul_character_in_the_text_of_the_root_is_rejected(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    before = await text(ports, document)

    with pytest.raises(Validation):
        _ = await ports.document_writes.set_document_text(document, document, "a\x00b")

    assert await text(ports, document) == before


async def test_a_version_or_document_that_does_not_exist_is_not_found(backend: Backend) -> None:
    ports, document, other = backend.admin, backend.new_document(), backend.new_document()
    before = await text(ports, other)

    for missing in (MISSING, other):
        with pytest.raises(NotFound) as raised:
            _ = await ports.document_writes.set_document_text(document, missing, "plmcp75 no")
        assert raised.value.message == unseen_version(document, missing).message
    with pytest.raises(NotFound):
        _ = await ports.document_writes.set_document_text(MISSING, MISSING, "plmcp75 no")
    assert await text(ports, other) == before


async def test_paperless_writes_the_text_of_a_deleted_version(backend: Backend) -> None:
    """So callers check that the document lists the version."""
    ports, document = backend.admin, backend.new_document()
    version = await add_version(backend, document, "plmcp75 deleted")
    _ = await ports.document_writes.delete_version(document, version)
    before = await text(ports, document)

    stored = await ports.document_writes.set_document_text(document, version, "plmcp75 trashed")

    assert stored == "plmcp75 trashed"
    assert await text(ports, document) == before


async def test_changing_the_text_needs_the_permission_and_a_grant(backend: Backend) -> None:
    document = backend.new_document()
    before = await text(backend.admin, document)
    viewer = backend.restricted(CHANGING, view=[document])
    no_change = backend.restricted(frozenset({"view_document"}), change=[document])
    granted = backend.restricted(CHANGING, change=[document])
    stranger = backend.restricted(CHANGING)

    for denied in (viewer, no_change, backend.blind):
        with pytest.raises(PermissionDenied):
            _ = await denied.document_writes.set_document_text(document, document, "plmcp75 no")
    with pytest.raises(NotFound):
        _ = await stranger.document_writes.set_document_text(document, document, "plmcp75 no")
    assert await text(backend.admin, document) == before

    stored = await granted.document_writes.set_document_text(document, document, "plmcp75 granted")
    assert stored == await text(backend.admin, document) == "plmcp75 granted"


async def test_search_finds_the_new_text_of_the_newest_version_only(backend: Backend) -> None:
    ports, document = backend.admin, backend.new_document()
    old, new, hidden = (f"plmcp75{uuid.uuid4().hex}" for _ in range(3))
    version = await add_version(backend, document, "plmcp75 search")
    _ = await ports.document_writes.set_document_text(document, version, old)
    assert await finds(ports, old, [document])

    _ = await ports.document_writes.set_document_text(document, document, hidden)
    _ = await ports.document_writes.set_document_text(document, version, new)

    assert await finds(ports, new, [document])
    assert await finds(ports, old, [])
    assert await found(ports, hidden) == []
