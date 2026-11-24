"""What insert_pages relies on: the same assertions on the fake and a live Paperless.

Every test works on PDFs of its own. The file is made by the real renderer, so Paperless consumes
what PDFium writes.
"""

import asyncio
import uuid
from datetime import date
from typing import TYPE_CHECKING

import pytest
from e2e.helpers import pdf_pages

from contract.test_document_upload import ended, ids
from mcp_server_paperless.domain.documents import CustomFieldValue, DocumentPatch
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied
from mcp_server_paperless.rendering.runner import ChildRenderer
from mcp_server_paperless.services.pdf_insert import PageInserter
from mcp_server_paperless.services.tasks import TaskService

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports
    from mcp_server_paperless.domain.pdf import FileOutput

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-68")]

VERSION = "DocumentWriter.upload_document_version"
UPLOAD = "DocumentWriter.upload_document"
CHANGING = frozenset({"view_document", "add_document", "change_document"})


def new(backend: Backend, pages: int, name: str = "target") -> int:
    title = f"plmcp79-{name}-{uuid.uuid4().hex}"
    texts = [f"{title} page {number}" for number in range(1, pages + 1)]
    return backend.upload(title, f"{title}.pdf", pdf_pages(texts), "application/pdf")


async def insert(  # noqa: PLR0913 -- the two documents, the place and the target
    backend: Backend,
    target: int,
    source: int,
    pages: str = "1",
    after_page: int = 1,
    *,
    output: FileOutput = "new_version",
    by: Ports | None = None,
) -> int:
    """Insert (as ``by``, default the admin), wait as the admin, and return what was made."""
    ports = by or backend.admin
    service = PageInserter(
        ports.documents,
        ports.document_writes,
        ChildRenderer(),
        TaskService(ports.tasks),
        asyncio.Semaphore(1),
        web_url="http://paperless/",
    )
    result = await service.insert(target, source, pages, after_page, output=output)
    task_id = result.tasks[0].task_id
    assert task_id is not None
    status = await ended(backend, task_id)
    assert status.state == "success", status.message
    [made] = status.document_ids
    return made


@pytest.mark.contract(VERSION)
async def test_the_file_is_consumed_as_a_version_with_its_pages_and_the_metadata_stays(
    backend: Backend,
) -> None:
    ports, target, source = backend.admin, new(backend, 2), new(backend, 3, "source")
    before = await ports.documents.get_document(target)

    version = await insert(backend, target, source, "2-3")

    after = await ports.documents.get_document(target)
    assert [v.id for v in after.versions] == [version, target]
    assert (before.page_count, after.page_count) == (2, 4)
    assert (after.title, after.tags, after.owner) == (before.title, before.tags, before.owner)
    untouched = await ports.documents.get_document(source)
    assert (len(untouched.versions), untouched.page_count) == (1, 3)


@pytest.mark.contract(VERSION)
async def test_a_change_grant_on_the_document_and_a_view_of_the_source_are_enough(
    backend: Backend,
) -> None:
    target, source = new(backend, 1), new(backend, 1, "source")
    user = backend.restricted(CHANGING, view=[source], change=[target])

    version = await insert(backend, target, source, by=user)

    assert [v.id for v in (await backend.admin.documents.get_document(target)).versions] == [
        *(version, target),
    ]


@pytest.mark.contract(VERSION)
@pytest.mark.contract(UPLOAD)
async def test_without_the_rights_nothing_is_added(backend: Backend) -> None:
    target, source = new(backend, 1), new(backend, 1, "source")
    no_adding = backend.restricted(
        frozenset({"view_document", "change_document"}), view=[source], change=[target]
    )
    for output in ("new_version", "new_document"):
        with pytest.raises(PermissionDenied):
            _ = await insert(backend, target, source, output=output, by=no_adding)
    looking = backend.restricted(CHANGING, view=[source, target])
    with pytest.raises(PermissionDenied):
        _ = await insert(backend, target, source, by=looking)
    _ = backend.restricted(CHANGING, view=[source])  # takes the view from everybody else
    unseeing = backend.restricted(CHANGING, change=[target])
    with pytest.raises(NotFound):
        _ = await insert(backend, target, source, by=unseeing)

    assert len((await backend.admin.documents.get_document(target)).versions) == 1


@pytest.mark.contract(UPLOAD)
async def test_a_new_document_takes_the_metadata_and_belongs_to_the_caller(
    backend: Backend,
) -> None:
    ports, target, source = backend.admin, new(backend, 2), new(backend, 1, "source")
    tags, fields = await ids(ports, "tag"), await ids(ports, "custom_field")
    link = next(
        item
        for item in await ports.taxonomy.list_taxonomy("custom_field")
        if item.data_type == "documentlink"
    )
    values = (
        CustomFieldValue(field=fields["e2e-string"], value=None),
        CustomFieldValue(field=fields["e2e-integer"], value=7),
        CustomFieldValue(field=fields["e2e-date"], value="2026-02-03"),
        CustomFieldValue(field=fields["e2e-monetary"], value="EUR12.50"),
        CustomFieldValue(field=link.id, value=[source]),
    )
    _ = await ports.document_writes.update_document(
        target,
        DocumentPatch(
            created=date(2025, 3, 4),
            correspondent=(await ids(ports, "correspondent"))["e2e-acme"],
            document_type=(await ids(ports, "document_type"))["e2e-invoice"],
            storage_path=(await ids(ports, "storage_path"))["e2e-archive"],
            tags=(tags["e2e-invoices"], tags["e2e-finance"]),
            custom_fields=values,
        ),
    )
    user = backend.restricted(frozenset({"view_document", "add_document"}), view=[source, target])
    before = await ports.documents.get_document(target)

    made = await insert(backend, target, source, "1", 0, output="new_document", by=user)

    copy = await ports.documents.get_document(made)
    assert made not in {target, source}
    assert (copy.title, copy.created, copy.page_count) == (before.title, date(2025, 3, 4), 3)
    assert (copy.correspondent, copy.document_type, copy.storage_path) == (
        *(before.correspondent, before.document_type, before.storage_path),
    )
    assert set(before.tags) <= set(copy.tags)
    assert {v.field: v.value for v in copy.custom_fields} == {v.field: v.value for v in values}
    assert copy.archive_serial_number is None
    assert copy.owner not in {None, before.owner}
    assert len((await ports.documents.get_document(target)).versions) == 1
