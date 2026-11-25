"""Contract of counts over a selection, word suggestions and trying a storage path (PLMCP-83.2)."""

import uuid
from typing import TYPE_CHECKING

import pytest

from mcp_server_paperless.domain.documents import DocumentPatch, DocumentQuery
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.lookups import Lookups
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from mcp_server_paperless.domain.selection import CountedKind
    from mcp_server_paperless.domain.taxonomy import TaxonomyKind

pytestmark = pytest.mark.anyio

COUNT, WORDS = "DocumentReader.count_selection", "DocumentReader.suggest_words"
TEMPLATE, RENDER = "TaxonomyReader.get_storage_path_template", "TaxonomyReader.render_storage_path"
MISSING = 999_999_999


async def seeded(ports: Ports, title: str) -> int:
    query = DocumentQuery(title_contains=title)
    [hit] = (await ports.documents.search_documents(query, page=1, page_size=2)).hits
    return hit.id


async def number(ports: Ports, kind: TaxonomyKind, name: str) -> int:
    rows = await ports.taxonomy.list_taxonomy(kind)
    return next(row.id for row in rows if row.name == name)


@pytest.mark.decision("decision-100")
@pytest.mark.contract(COUNT)
async def test_the_chosen_documents_are_counted_by_what_they_have(backend: Backend) -> None:
    ports = backend.admin
    public = await seeded(ports, "e2e-public-invoice")
    private = await seeded(ports, "e2e-private-note")

    both = await ports.documents.count_selection([public, private])
    one = await ports.documents.count_selection([private])

    named: dict[CountedKind, str] = {
        "correspondent": "e2e-acme",
        "document_type": "e2e-invoice",
        "storage_path": "e2e-archive",
        "tag": "e2e-finance",
        "custom_field": "e2e-string",
    }
    for kind, name in named.items():
        item = await number(ports, kind, name)
        assert both[kind][item] == 1, kind
        assert item not in one[kind], kind
    assert all(count > 0 for found in both.values() for count in found.values())


@pytest.mark.decision("decision-100")
@pytest.mark.contract(COUNT)
async def test_a_tag_that_is_hidden_from_the_user_is_counted_too(backend: Backend) -> None:
    ports = backend.admin
    [document] = backend.new_documents(1)
    tag = (await ports.taxonomy_writes.create_tag(f"contract-tag-{uuid.uuid4().hex}")).id
    _ = await ports.document_writes.update_document(document, DocumentPatch(tags=(tag,)))
    viewer = backend.restricted(frozenset({"view_document", "view_tag"}), view=[document])

    found = await viewer.documents.count_selection([document])

    assert found["tag"] == {tag: 1}
    assert tag not in {row.id for row in await viewer.taxonomy.list_taxonomy("tag")}


@pytest.mark.contract(COUNT)
async def test_gone_repeated_and_hidden_documents_are_refused(backend: Backend) -> None:
    ports = backend.admin
    public = await seeded(ports, "e2e-public-invoice")
    private = await seeded(ports, "e2e-private-note")

    for documents in ([MISSING], [public, public]):
        with pytest.raises(Validation):
            _ = await ports.documents.count_selection(documents)
    for other, documents in ((backend.reader, [private]), (backend.blind, [public])):
        with pytest.raises(PermissionDenied):
            _ = await other.documents.count_selection(documents)


@pytest.mark.contract(WORDS)
async def test_words_begin_with_the_term_whatever_its_case_and_accents(backend: Backend) -> None:
    backend.rebuild_search_index()
    words = backend.admin.documents.suggest_words

    found = await words("invo", 10)

    assert "invoice" in found
    assert all(word.startswith("invo") for word in found)
    assert await words("INVÓ", 10) == found
    assert len(await words("e", 1)) == 1
    assert await words("zzzqqqxxx", 10) == ()


@pytest.mark.contract(WORDS)
async def test_words_come_from_documents_the_user_sees(backend: Backend) -> None:
    backend.rebuild_search_index()

    admin = await backend.admin.documents.suggest_words("priva", 10)
    reader = await backend.reader.documents.suggest_words("priva", 10)

    assert "private" in admin
    assert "private" not in reader
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.documents.suggest_words("priva", 10)


@pytest.mark.contract(TEMPLATE)
async def test_the_template_of_a_storage_path_is_the_stored_one(backend: Backend) -> None:
    ports = backend.admin
    item = await number(ports, "storage_path", "e2e-archive")

    found = await ports.taxonomy.get_storage_path_template(item)

    assert found == "e2e/{{ created_year }}/{{ title }}"
    with pytest.raises(NotFound):
        _ = await ports.taxonomy.get_storage_path_template(MISSING)
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.taxonomy.get_storage_path_template(item)


@pytest.mark.parametrize(
    ("template", "path"),
    [
        ("x/{{ title }}", "x/e2e-public-invoice.pdf"),
        ("{{ correspondent }} / a", "e2e-acme/a.pdf"),
        ("{title}", "{title}.pdf"),  # the old style is not converted here
        ("{{ nope }}", None),
        ("{{ title", None),
        ("../x", None),
        ("{{ '' }}", ""),
    ],
)
@pytest.mark.contract(RENDER)
async def test_a_template_gives_the_path_of_the_file_or_nothing(
    backend: Backend, template: str, path: str | None
) -> None:
    document = await seeded(backend.admin, "e2e-public-invoice")

    assert await backend.admin.taxonomy.render_storage_path(document, template) == path


@pytest.mark.contract(RENDER)
async def test_trying_a_template_needs_the_view_of_the_document_only(backend: Backend) -> None:
    [document] = backend.new_documents(1)
    viewer = backend.restricted(frozenset({"view_document"}), view=[document])
    private = await seeded(backend.admin, "e2e-private-note")

    assert (await viewer.taxonomy.render_storage_path(document, "x/y") or "").startswith("x/y.")
    for hidden in (private, MISSING):
        with pytest.raises(Validation):
            _ = await viewer.taxonomy.render_storage_path(hidden, "x")
    with pytest.raises(PermissionDenied):
        _ = await backend.blind.taxonomy.render_storage_path(private, "x")


@pytest.mark.decision("decision-100")
@pytest.mark.contract(RENDER)
async def test_paperless_renders_hidden_names_so_the_server_does_not_try(backend: Backend) -> None:
    ports = backend.admin
    [document] = backend.new_documents(1)
    name = f"contract-tag-{uuid.uuid4().hex}"
    tag = (await ports.taxonomy_writes.create_tag(name)).id
    viewer = backend.restricted(frozenset({"view_document", "view_tag"}), view=[document])
    _ = await ports.document_writes.update_document(
        document, DocumentPatch(tags=(tag,), owner=None)
    )
    resolver = TaxonomyResolver(viewer.taxonomy, StaticTokenProvider("t"), ttl=60.0)
    service = Lookups(viewer.documents, viewer.taxonomy, resolver)

    shown = await viewer.taxonomy.render_storage_path(document, "{{ tag_list }}")
    with pytest.raises(PermissionDenied, match="may not see \\(tag\\)"):
        _ = await service.test_storage_path(document, None, "{{ tag_list }}")
    _ = await ports.document_writes.update_document(document, DocumentPatch(tags=()))
    tried = await service.test_storage_path(document, None, "x/y")

    assert name in (shown or "")
    assert (tried.path or "").startswith("x/y.")
