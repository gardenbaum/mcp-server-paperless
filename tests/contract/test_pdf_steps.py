"""insert_pages, extract_pages and split_document as operations, on the fake and a live Paperless.

Their preview, their refusals and what is left when a step fails. The steps that fail do so because
somebody else acts at a known place of the call (tests/interleaving.py); what Paperless then says is
its own answer. Every test works on documents of its own.
"""

import asyncio
import uuid
from typing import TYPE_CHECKING, override

import pytest
from e2e.helpers import pdf_pages
from fakes.paperless import BROKEN_PDF
from interleaving import before

from contract.test_document_upload import ended, ids
from contract.test_pdf import add_version, made_files
from mcp_server_paperless.domain.bulk import ObjectPermissions
from mcp_server_paperless.domain.documents import DocumentPatch
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.pdf import PartMetadata
from mcp_server_paperless.domain.permissions import AccessChange
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.rendering.runner import ChildRenderer
from mcp_server_paperless.services.document_edits import DocumentEditor
from mcp_server_paperless.services.documents import DocumentService
from mcp_server_paperless.services.pdf import Options, PdfService
from mcp_server_paperless.services.pdf_insert import NOTHING, PageInserter
from mcp_server_paperless.services.pdf_parts import Parts
from mcp_server_paperless.services.pdf_replacing import Replacing
from mcp_server_paperless.services.removal import DocumentRemoval
from mcp_server_paperless.services.tasks import TaskService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.trash_scan import TrashScan

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from contract.conftest import Backend, Ports
    from mcp_server_paperless.domain.pdf import FileOutput, PdfResult
    from mcp_server_paperless.domain.ports import DocumentWriter, PdfWriter
    from mcp_server_paperless.domain.rendering import Assembled, AssembleRequest

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-67"),
    pytest.mark.decision("decision-68"),
]

WEB = "http://paperless/"
YES = Options(confirm=True)
# What a user needs to split a document nobody owns, to follow it and to trash the original.
SPLITTING = frozenset(
    {
        *("view_document", "change_document", "add_document", "delete_document"),
        *("view_paperlesstask", "view_uisettings", "view_tag", "view_customfield"),
        *("view_correspondent", "view_documenttype", "view_storagepath"),
    }
)
LOOKING = SPLITTING - {"view_paperlesstask"}
FIRST = PartMetadata(title="plmcp119 first", tags=("e2e-finance",))
SECOND = PartMetadata(title="plmcp119 second", document_type="e2e-invoice")


def new(backend: Backend, pages: int, name: str = "target") -> int:
    title = f"plmcp119-{name}-{uuid.uuid4().hex}"
    texts = [f"{title} page {number}" for number in range(1, pages + 1)]
    return backend.upload(title, f"{title}.pdf", pdf_pages(texts), "application/pdf")


async def free(backend: Backend, *documents: int) -> None:
    """Let everybody see and edit the documents, as far as their global permissions go."""
    for number in documents:
        _ = await backend.admin.document_writes.update_document(number, DocumentPatch(owner=None))


async def take(backend: Backend, document: int, user: Ports) -> None:
    """The admin makes the document their own and lets the user only look at it."""
    number = await user.management.get_current_user_id()
    owner = await backend.admin.management.get_current_user_id()
    _ = await backend.admin.permission_writes.set_permissions(
        "document",
        document,
        AccessChange(owner=owner, permissions=ObjectPermissions(view_users=(number,))),
    )


def operations(
    ports: Ports,
    *,
    pdf_writes: PdfWriter | None = None,
    document_writes: DocumentWriter | None = None,
) -> PdfService:
    """`PdfService` as the server builds it, for one user."""
    reader, writes = ports.documents, document_writes or ports.document_writes
    pdf = pdf_writes or ports.pdf_writes
    taxonomy = TaxonomyResolver(ports.taxonomy, StaticTokenProvider("t"), ttl=3600.0)
    documents = DocumentService(reader, ports.trash, taxonomy, web_url=WEB)
    editor = DocumentEditor(reader, writes, ports.taxonomy_writes, taxonomy, documents=documents)
    scan = TrashScan(ports.trash, reader, taxonomy)
    replacing = Replacing(reader, writes, DocumentRemoval(reader, writes, taxonomy), scan)
    return PdfService(
        reader,
        pdf,
        TaskService(ports.tasks),
        replacing,
        taxonomy,
        parts=Parts(reader, pdf, editor, taxonomy, replacing, web_url=WEB),
        web_url=WEB,
    )


async def versions(backend: Backend, document: int) -> int:
    return len((await backend.admin.documents.get_document(document)).versions)


def track(backend: Backend, result: PdfResult) -> None:
    for number in result.produced_ids:
        backend.track_document(number)


# --- insert_pages


class Acting(ChildRenderer):
    """The renderer, during whose work something happens; `instead` replaces what it makes."""

    def __init__(
        self,
        act: Callable[[], Awaitable[object]] | None = None,
        *,
        instead: bytes | None = None,
    ) -> None:
        super().__init__()
        self.act: Callable[[], Awaitable[object]] | None = act
        self.instead: bytes | None = instead

    @override
    async def assemble(self, target: bytes, source: bytes, request: AssembleRequest) -> Assembled:
        made = await super().assemble(target, source, request)
        if self.act is not None:
            _ = await self.act()
        if self.instead is not None:
            return made.model_copy(update={"content": self.instead})
        return made


def inserter(ports: Ports, renderer: ChildRenderer | None = None) -> PageInserter:
    return PageInserter(
        ports.documents,
        ports.document_writes,
        renderer or ChildRenderer(),
        TaskService(ports.tasks),
        asyncio.Semaphore(1),
        web_url=WEB,
    )


async def previewed_and_refused(backend: Backend, target: int, source: int) -> None:
    service = inserter(backend.admin)

    produced: dict[FileOutput, str] = {
        "new_version": "a new version of the document with 4 pages",
        "new_document": "1 new document with 4 pages",
    }
    for output, produces in produced.items():
        preview = await service.insert(target, source, "2-3", 1, output=output, dry_run=True)
        assert (preview.dry_run, preview.state, preview.tasks) == (True, "not_started", ())
        assert preview.plan is not None
        assert produces in preview.plan.produces
        assert preview.plan.needs_confirm is False
        assert preview.plan.loses
    with pytest.raises(Validation, match=rf"Page 3 does not exist: document {target} has 2 pages"):
        _ = await service.insert(target, source, "1", 3)
    with pytest.raises(Validation, match="Page 4 does not exist: the document has 3 pages"):
        _ = await service.insert(target, source, "4", 1)
    with pytest.raises(Validation, match="cannot be its own source"):
        _ = await service.insert(target, target, "1", 1)
    with pytest.raises(NotFound):
        _ = await service.insert(target, 999_999, "1", 1)

    assert (await versions(backend, target), await versions(backend, source)) == (1, 1)


async def changed_meanwhile(backend: Backend, target: int, source: int, other: int) -> None:
    before_it = await versions(backend, target) + await versions(backend, source)
    kept = source if other == target else target
    kept_versions = await versions(backend, kept)
    renderer = Acting(lambda: add_version(backend, other, 2))

    with pytest.raises(PaperlessMcpError, match="got another file meanwhile") as stopped:
        _ = await inserter(backend.admin, renderer).insert(target, source, "1", 1)

    assert NOTHING in stopped.value.message
    assert await versions(backend, target) + await versions(backend, source) == before_it + 1
    assert await versions(backend, kept) == kept_versions


async def right_taken(backend: Backend, target: int, source: int) -> None:
    user = backend.restricted(LOOKING)
    renderer = Acting(lambda: take(backend, target, user))

    with pytest.raises(PermissionDenied):
        _ = await inserter(user, renderer).insert(target, source, "1", 1)
    assert await versions(backend, target) == 1


async def not_consumed(backend: Backend, target: int, source: int) -> None:
    service = inserter(backend.admin, Acting(instead=BROKEN_PDF))

    result = await service.insert(target, source, "1", 1, wait_seconds=30)
    alone = await service.insert(target, source, "1", 1, output="new_document", wait_seconds=30)

    assert (result.state, result.produced_ids) == ("failure", ())
    assert f"Document {target} shows the file it showed before." in (result.message or "")
    assert (alone.state, alone.produced_ids) == ("failure", ())
    assert (await versions(backend, target), await versions(backend, source)) == (1, 1)


async def without_tasks(backend: Backend, target: int, source: int) -> None:
    user = backend.restricted(LOOKING)

    result = await inserter(user).insert(target, source, "1", 1, wait_seconds=1)

    assert (result.state, result.produced_ids) == ("unknown", ())
    assert result.next == f'get_document(document_id={target}, include=["versions"])'
    assert "Look before repeating the call." in (result.message or "")
    [task_id] = [task.task_id for task in result.tasks]
    assert task_id is not None
    assert (await ended(backend, task_id)).state == "success"
    assert await versions(backend, target) == 2


async def test_every_way_an_insertion_ends_early_leaves_both_documents_until_one_succeeds(
    backend: Backend,
) -> None:
    """One pair of documents for all of them: each has to leave it as it was for the next."""
    target, source = new(backend, 2), new(backend, 3, "source")

    await previewed_and_refused(backend, target, source)
    await not_consumed(backend, target, source)
    await free(backend, target, source)
    await right_taken(backend, target, source)
    await free(backend, target)
    await without_tasks(backend, target, source)
    # Both documents may have more versions now: what counts is what is added.
    await changed_meanwhile(backend, target, source, source)
    await changed_meanwhile(backend, target, source, target)


# --- split_document with metadata


async def split_previewed_and_refused(backend: Backend, source: int) -> None:
    service, metadata = operations(backend.admin), (FIRST, SECOND)

    preview = await service.split(
        source,
        ["1", "2-3"],
        output="replace_original",
        part_metadata=metadata,
        options=Options(dry_run=True),
    )
    assert (preview.dry_run, preview.state, preview.tasks) == (True, "not_started", ())
    assert preview.plan is not None
    assert preview.plan.needs_confirm is True
    plan = " ".join(preview.plan.keeps)
    assert "part 1 (page 1) gets title 'plmcp119 first' and tags 'e2e-finance'" in plan
    assert "part 2 (pages 2-3) gets title 'plmcp119 second' and document type 'e2e-inv" in plan
    with pytest.raises(ConfirmationRequired, match="plmcp119 first"):
        _ = await service.split(
            source, ["1", "2-3"], output="replace_original", part_metadata=metadata
        )
    with pytest.raises(NotFound, match="plmcp119-no-such-tag"):
        _ = await service.split(
            source,
            ["1", "2-3"],
            part_metadata=(FIRST, PartMetadata(tags=("plmcp119-no-such-tag",))),
        )
    with pytest.raises(Validation, match="2 parts, but metadata for 1"):
        _ = await service.split(source, ["1", "2-3"], part_metadata=(FIRST,))
    viewer = backend.restricted(SPLITTING, view=[source])
    with pytest.raises(PermissionDenied):
        _ = await operations(viewer).split(source, ["1", "2-3"], part_metadata=metadata)

    assert not backend.in_trash(source)
    assert await versions(backend, source) == 1


async def part_metadata_refused(backend: Backend, source: int) -> None:
    title = (await backend.admin.documents.get_document(source)).title
    user = backend.restricted(SPLITTING)

    async def taken(document: int, _: object) -> None:
        await take(backend, document, user)

    writes = before(user.document_writes, "update_document", taken)
    result = await operations(user, document_writes=writes).split(
        source,
        ["1", "2-3"],
        output="replace_original",
        part_metadata=(FIRST, SECOND),
        options=YES,
    )

    track(backend, result)
    one, two = result.parts
    assert (one.state, one.metadata, two.state, two.metadata) == (
        *("success", "not_set"),
        *("success", "set"),
    )
    assert "its metadata is not known to be set" in (one.message or "")
    assert (one.next or "").startswith(f"update_document(document_id={one.document_id}, title=")
    assert (result.original, result.trashed_ids) == ("kept", ())
    assert "the metadata of part 1 is not set" in (result.message or "")
    assert f"delete_document(document_id={source})" in (result.next or "")
    assert not backend.in_trash(source)
    assert one.document_id is not None
    assert two.document_id is not None
    assert (await backend.admin.documents.get_document(one.document_id)).title == title
    assert (await backend.admin.documents.get_document(two.document_id)).title == SECOND.title


async def trashing_refused(backend: Backend, source: int) -> None:
    user = backend.restricted(SPLITTING)

    async def taken(document: int) -> None:
        await take(backend, document, user)

    writes = before(user.document_writes, "delete_document", taken)
    result = await operations(user, document_writes=writes).split(
        source,
        ["1", "2"],
        output="replace_original",
        part_metadata=(FIRST, SECOND),
        options=YES,
    )

    track(backend, result)
    assert [part.metadata for part in result.parts] == ["set", "set"]
    assert len(result.produced_ids) == 2
    assert (result.original, result.trashed_ids) == ("kept", ())
    assert f"Not moved to the trash: document {source}" in (result.message or "")
    assert f"delete_document(document_id={source})" in (result.next or "")
    assert not backend.in_trash(source)


async def replaced_while(
    backend: Backend, source: int, happens: Callable[[int], Awaitable[None]]
) -> PdfResult:
    """Split the document, with something else just before its deletion."""
    user = backend.restricted(SPLITTING)
    writes = before(user.document_writes, "delete_document", happens)
    result = await operations(user, document_writes=writes).split(
        source, ["1", "2"], output="replace_original", options=YES
    )
    track(backend, result)
    return result


async def hidden_before_its_deletion(backend: Backend, source: int) -> None:
    """The deletion and the read after it answer 404 for what is hidden, as for what is gone."""

    async def hide(document: int) -> None:
        owner = await backend.admin.management.get_current_user_id()
        _ = await backend.admin.permission_writes.set_permissions(
            "document",
            document,
            AccessChange(owner=owner, permissions=ObjectPermissions()),
        )

    result = await replaced_while(backend, source, hide)

    assert (result.original, result.trashed_ids) == ("unknown", ())
    assert f"It is not known whether document {source} went to the trash" in (result.message or "")
    assert "it is not in the trash this user may see" in (result.message or "")
    assert not backend.in_trash(source)


async def trashed_by_somebody_before_its_deletion(backend: Backend, source: int) -> None:
    happens = backend.admin.document_writes.delete_document
    result = await replaced_while(backend, source, happens)

    assert (result.original, result.trashed_ids) == ("trashed", (source,))
    assert backend.in_trash(source)


async def test_a_split_with_metadata_is_previewed_and_the_original_stays_for_a_part_without_its_own(
    backend: Backend,
) -> None:
    source = new(backend, 3)

    await split_previewed_and_refused(backend, source)
    await free(backend, source)
    await part_metadata_refused(backend, source)


async def test_the_original_of_a_split_is_only_said_to_be_trashed_if_it_is(
    backend: Backend,
) -> None:
    source = new(backend, 2)

    await free(backend, source)
    await trashing_refused(backend, source)
    await free(backend, source)
    await hidden_before_its_deletion(backend, source)
    await free(backend, source)
    await trashed_by_somebody_before_its_deletion(backend, source)


# --- extract_pages


async def extraction_previewed_and_refused(backend: Backend, source: int) -> None:
    service = operations(backend.admin)

    preview = await service.extract(
        source, "2", remove=True, metadata=FIRST, options=Options(dry_run=True)
    )
    assert (preview.dry_run, preview.state, preview.tasks) == (True, "not_started", ())
    assert preview.plan is not None
    assert preview.plan.needs_confirm is True
    assert "without these pages" in preview.plan.produces
    assert "the new document gets title 'plmcp119 first'" in " ".join(preview.plan.keeps)
    with pytest.raises(ConfirmationRequired, match=f"extract page 2 of document {source}"):
        _ = await service.extract(source, "2", remove=True)
    with pytest.raises(Validation, match="Removing every page leaves nothing"):
        _ = await service.extract(source, "1-", remove=True)
    with pytest.raises(Validation, match="Page 4 does not exist: the document has 3 pages"):
        _ = await service.extract(source, "4")
    viewer = backend.restricted(SPLITTING, view=[source])
    with pytest.raises(PermissionDenied):
        _ = await operations(viewer).extract(source, "2", remove=True, options=YES)

    assert (await backend.admin.documents.get_document(source)).page_count == 3
    assert await versions(backend, source) == 1


async def metadata_refused(backend: Backend, source: int) -> None:
    user = backend.restricted(SPLITTING)

    async def taken(document: int, _: object) -> None:
        await take(backend, document, user)

    writes = before(user.document_writes, "update_document", taken)
    result = await operations(user, document_writes=writes).extract(
        source, "2", remove=True, metadata=FIRST, options=YES
    )

    track(backend, result)
    [part] = result.parts
    assert (part.state, part.metadata, result.original) == ("success", "not_set", "kept")
    assert "the metadata of the new document is not set" in (result.message or "")
    assert f"delete_pages(document_id={source}" in (result.next or "")
    assert len(result.produced_ids) == 1
    assert (await backend.admin.documents.get_document(source)).page_count == 3
    assert await versions(backend, source) == 1


async def changed_during_an_extraction(backend: Backend, source: int) -> None:
    async def changed(*_args: object) -> None:
        _ = await add_version(backend, source, 2)

    writes = before(backend.admin.document_writes, "update_document", changed)
    result = await operations(backend.admin, document_writes=writes).extract(
        source, "2", remove=True, metadata=PartMetadata(title="plmcp119 page"), options=YES
    )

    track(backend, result)
    [product] = result.produced_ids
    assert [part.metadata for part in result.parts] == ["set"]
    assert result.original == "kept"
    assert f"document {source} was changed meanwhile" in (result.message or "")
    assert f"delete_document(document_id={product}) to drop" in (result.next or "")
    assert (await backend.admin.documents.get_document(source)).page_count == 2
    assert await versions(backend, source) == 2


async def removal_refused(backend: Backend, source: int) -> None:
    user = backend.restricted(SPLITTING)

    async def taken(document: int, _: object) -> None:
        await take(backend, document, user)

    writes = before(user.pdf_writes, "edit_pdf", taken, at=2)
    result = await operations(user, pdf_writes=writes).extract(
        source, "2", remove=True, metadata=FIRST, options=YES
    )

    track(backend, result)
    [product] = result.produced_ids
    assert [part.metadata for part in result.parts] == ["set"]
    assert (result.original, result.state) == ("kept", "success")
    assert "may not" in (result.message or "")
    assert f"delete_document(document_id={product}) to drop" in (result.next or "")
    made = await backend.admin.documents.get_document(product)
    assert (made.title, made.page_count) == (FIRST.title, 1)
    tags = await ids(backend.admin, "tag")
    assert {tags["e2e-finance"]} <= set(made.tags)
    assert (await backend.admin.documents.get_document(source)).page_count == 3
    assert await versions(backend, source) == 1


async def extraction_without_tasks(backend: Backend, source: int) -> None:
    user = backend.restricted(LOOKING)

    result = await operations(user).extract(source, "2", remove=True, metadata=FIRST, options=YES)

    [part] = result.parts
    assert (result.state, result.original, result.produced_ids) == ("unknown", "kept", ())
    assert (part.state, part.metadata) == ("unknown", "not_set")
    assert 'title="plmcp119 first"' in (part.next or "")
    await made_files(backend, backend.admin, f"{source}_edit_1.pdf", 1)
    assert await versions(backend, source) == 1


async def test_every_way_an_extraction_ends_early_leaves_the_pages(backend: Backend) -> None:
    """One document for all of them: each has to leave it as it was for the next."""
    source = new(backend, 3)

    await extraction_previewed_and_refused(backend, source)
    await free(backend, source)
    await extraction_without_tasks(backend, source)
    await metadata_refused(backend, source)
    await removal_refused(backend, source)
    await changed_during_an_extraction(backend, source)
