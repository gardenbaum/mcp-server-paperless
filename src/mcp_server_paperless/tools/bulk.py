"""The bulk toolset: one operation on many documents, or on many taxonomy objects, at once.

Opt-in (``PAPERLESS_MCP_TOOLSETS=core,bulk``). One wrong call can damage hundreds of documents,
so every operation can be previewed with ``dry_run`` first and executes only with ``confirm``
(design 5.4, decisions 6 and 15).
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from pydantic import Field

from mcp_server_paperless.domain.bulk import (
    MAX_DOCUMENTS,
    BulkKind,
    BulkOperation,
    DocumentBulkReport,
    TaxonomyBulkReport,
    TaxonomyOperation,
)
from mcp_server_paperless.domain.documents import NameOrId
from mcp_server_paperless.domain.duplicate_resolution import (
    MAX_DUPLICATES,
    DuplicateResolution,
    ResolveAction,
)
from mcp_server_paperless.tools._shared import ANNOTATIONS, Confirm, boundary, services

TOOLSET = "bulk"

provider = LocalProvider()

type DryRun = Annotated[
    bool,
    Field(
        description=(
            "true returns a preview (how many change, a sample, what changes) and writes "
            "nothing; it needs no confirm. Do it first."
        )
    ),
]


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["bulk_edit_documents"])
@boundary
async def bulk_edit_documents(
    ctx: Context,
    document_ids: Annotated[
        list[int],
        Field(
            min_length=1,
            max_length=MAX_DOCUMENTS,
            description=(
                f"Ids of the documents to change, at most {MAX_DOCUMENTS}, from search_documents "
                "(page_size 100 fits); repeated ids count once."
            ),
        ),
    ],
    operation: BulkOperation,
    *,
    dry_run: DryRun = False,
    confirm: Confirm = False,
) -> DocumentBulkReport:
    """Change many documents in one step: tags, correspondent, type, storage path, custom fields,
    trash or reprocess.

    Call with dry_run=true to see how many documents change and a sample, then without dry_run:
    without confirm a dialog, or an error for you to show the user, first describes the effect (see
    confirm). Documents that already are as asked are counted and left alone. One operation per
    call, on documents you name by id. Tags are hierarchical: adding a child adds its parent, and
    removing a parent whose child tags stay is refused (list the children too). Custom fields are
    merged: only the fields you name change. Document links are mirrored: linking A to B also
    changes B (you need change permission there), so also_changed lists those documents; a shorter
    list does not unlink the old target, removing the field does. delete moves documents to the
    Paperless trash (restore_from_trash of the trash toolset, or the web UI, restores them);
    reprocess runs OCR again in the background (the pdf toolset's reprocess_documents does the same,
    also with remote OCR). Paperless checks every document first: if you may not change one of them
    (blocked lists them), none changes and nothing is sent. Needs change permission on the
    documents, and ownership and the delete permission for delete.
    """  # noqa: D205 -- the summary is one sentence; FastMCP sends the docstring as is
    return await services(ctx).bulk_documents.edit(
        document_ids, operation, dry_run=dry_run, confirm=confirm
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["bulk_edit_taxonomy"])
@boundary
async def bulk_edit_taxonomy(  # noqa: PLR0913 -- one parameter per concept, described for the LLM
    ctx: Context,
    kind: Annotated[
        BulkKind,
        Field(description="What the objects are: tag, correspondent, document_type, storage_path."),
    ],
    items: Annotated[
        list[NameOrId],
        Field(
            min_length=1,
            max_length=MAX_DOCUMENTS,
            description=f"Names or ids of the objects, at most {MAX_DOCUMENTS}; see list_taxonomy.",
        ),
    ],
    operation: TaxonomyOperation,
    *,
    dry_run: DryRun = False,
    confirm: Confirm = False,
) -> TaxonomyBulkReport:
    """Delete many tags, correspondents, document types or storage paths, or set who owns them
    and who may view or change them.

    dry_run=true previews; without confirm a dialog, or an error for you to show the user, first
    describes the effect (see confirm). A delete is final (taxonomy has no trash): documents lose
    the reference, and deleting a tag deletes its child tags too (Paperless refuses the deletion if
    it would take child tags of other users). set_permissions without merge replaces all grants
    with the lists you give (a list left out becomes empty); with merge it adds to them, and merge
    cannot set an owner. A change grant includes view. Users and groups by name or id. Custom
    fields are not supported. Paperless checks every object first: if you may not change one, none
    changes (you need the permission and must own it, or it must have no owner). The permissions
    toolset's set_permissions also sets permissions and covers documents too; this tool's
    set_permissions operation empties the lists you leave out, that tool keeps them.
    """  # noqa: D205 -- the summary is one sentence; FastMCP sends the docstring as is
    return await services(ctx).bulk_taxonomy.edit(
        kind, items, operation, dry_run=dry_run, confirm=confirm
    )


@provider.tool(tags={TOOLSET}, annotations=ANNOTATIONS["resolve_duplicates"])
@boundary
async def resolve_duplicates(  # noqa: PLR0913 -- one parameter per concept, described for the LLM
    ctx: Context,
    keep_document_id: Annotated[int, Field(description="The document that stays.")],
    duplicate_ids: Annotated[
        list[int],
        Field(
            min_length=1,
            max_length=MAX_DUPLICATES,
            description=(
                f"Ids of its duplicates to resolve, at most {MAX_DUPLICATES}, from get_document "
                "with include duplicates; not those in the trash. After a merge the document "
                "shows the file of the last one."
            ),
        ),
    ],
    action: Annotated[
        ResolveAction,
        Field(
            description=(
                "trash: they can be restored; merge_as_versions: they become versions of the kept "
                "document for good and lose their metadata and notes."
            )
        ),
    ],
    *,
    dry_run: DryRun = False,
    confirm: Confirm = False,
) -> DuplicateResolution:
    """Keep one document and move its duplicates to the trash, or merge them into it as versions.

    Find documents with duplicates with search_documents(has_duplicates=true) and compare the
    copies with get_document(include=["duplicates"]) first: notes, tags and dates differ. With trash
    the duplicates can be restored (restore_from_trash of the trash toolset, or the web UI). Merging
    is final: they vanish for good, not even into the trash, and lose their metadata and notes; only
    their files stay. dry_run=true previews; without confirm a dialog, or an error for you to show
    the user, first describes the effect (see confirm). Every document has to be a duplicate of the
    kept one now, outside the trash: otherwise nothing changes and each one that is not is named.
    Documents you cannot see are never compared. Keeping a document whose file Paperless cannot find
    is refused while a duplicate still has it; otherwise a missing file is named. Needs the
    permissions to change and delete documents and ownership of the duplicates, for a merge of the
    kept document too (or no owner). resolved, not_resolved and kept_seen say what was seen
    afterwards.
    """
    return await services(ctx).duplicate_resolver.resolve(
        keep_document_id, duplicate_ids, action, dry_run=dry_run, confirm=confirm
    )
