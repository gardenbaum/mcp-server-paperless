"""Deleting documents: to the Paperless trash, behind the confirmation flow (decision-6)."""

from typing import TYPE_CHECKING

from mcp_server_paperless.domain.documents import DocumentDeleted
from mcp_server_paperless.domain.errors import ConfirmationRequired, quoted
from mcp_server_paperless.services.document_edits import COUNTED

if TYPE_CHECKING:
    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# Where a deleted document can be got back. restore_from_trash belongs to the trash toolset
# (PLMCP-27); 30 days is Paperless's default PAPERLESS_EMPTY_TRASH_DELAY.
RESTORE = (
    "It stays there until the trash is emptied (by default after 30 days) and can be restored "
    "in the Paperless web UI, or with restore_from_trash if the trash toolset is enabled."
)


class DocumentRemoval:
    """Moves documents to the trash for the current user."""

    def __init__(
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        taxonomy: TaxonomyResolver,
    ) -> None:
        """Read through ``reader``, delete through ``writer``; ``taxonomy`` forgets the counts."""
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy

    async def delete(self, document_id: int, *, confirm: bool = False) -> DocumentDeleted:
        """Move a document to the trash; without ``confirm`` nothing changes and the error says why.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not delete this document.
        """
        document = await self._reader.get_document(document_id)
        if not confirm:
            effect = (
                f"move document {document_id} {quoted(document.title)} to the Paperless trash "
                f"(it keeps its notes and can be restored from there)"
            )
            ConfirmationRequired.require(effect)
        await self._writer.delete_document(document_id)
        for kind in COUNTED:
            self._taxonomy.invalidate(kind)
        return DocumentDeleted(
            document_id=document_id,
            title=document.title,
            in_trash=True,
            message=f"Document {document_id} {quoted(document.title)} is in the trash. {RESTORE}",
        )
