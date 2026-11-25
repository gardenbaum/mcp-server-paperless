"""Small questions to Paperless: words of the search index, the path a template gives a file."""

from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import Ref
from mcp_server_paperless.domain.errors import NotFound, Validation
from mcp_server_paperless.domain.lookups import StoragePathTest, Words

if TYPE_CHECKING:
    from mcp_server_paperless.domain.ports import DocumentReader, TaxonomyReader
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# Characters of a word and of a path that are shown.
MAX_WORD: Final = 100
MAX_PATH_SHOWN: Final = 1000
NO_PATH: Final = (
    "The template cannot be rendered for this document, or gives nothing or an unsafe path: "
    "Paperless would name the file by the id of the document."
)


class Lookups:
    """Answers small questions for the current user; nothing is changed."""

    def __init__(
        self, documents: DocumentReader, taxonomy: TaxonomyReader, resolver: TaxonomyResolver
    ) -> None:
        """Read through ``documents`` and ``taxonomy``; ``resolver`` resolves names."""
        self._documents: DocumentReader = documents
        self._taxonomy: TaxonomyReader = taxonomy
        self._resolver: TaxonomyResolver = resolver

    async def suggest_words(self, term: str, limit: int) -> Words:
        """At most ``limit`` words of the search index that begin with ``term``.

        Raises:
            Validation: ``term`` is not the beginning of one word.
            PermissionDenied: The user may not view documents.
        """
        if len(term.split()) != 1:
            message = "Give the beginning of one word."
            raise Validation(message, hint="For several words use search_documents.")
        found = await self._documents.suggest_words(term.strip(), limit)
        words = tuple(word for word in found if len(word) <= MAX_WORD)
        return Words(words=words, too_long=len(found) - len(words))

    async def test_storage_path(
        self, document_id: int, storage_path: str | int | None, template: str | None
    ) -> StoragePathTest:
        """The path a stored storage path, or a new template, would give the document's file.

        Raises:
            Validation: Not exactly one of the two is given, or the template is blank.
            NotFound: No such document or storage path, or the user may not see it.
            Ambiguous: The name matches several storage paths.
            PermissionDenied: The user may not view documents, or storage paths.
        """
        if (storage_path is None) == (template is None):
            message = "Give exactly one of storage_path and template."
            raise Validation(message)
        if template is not None and not template.strip():
            message = "The template is blank."
            raise Validation(message)
        item = None
        if storage_path is not None:
            item = await self._resolver.resolve("storage_path", storage_path)
            template = await self._taxonomy.get_storage_path_template(item.id)
        if document_id not in await self._documents.get_document_titles([document_id]):
            message = f"No document {document_id} exists, or the user may not see it."
            raise NotFound(message)
        path = await self._taxonomy.render_storage_path(document_id, template or "")
        note = None if path else NO_PATH
        if path and len(path) > MAX_PATH_SHOWN:
            note = f"The path has {len(path)} characters; the first {MAX_PATH_SHOWN} are shown."
            path = path[:MAX_PATH_SHOWN]
        return StoragePathTest(
            document_id=document_id,
            storage_path=Ref(id=item.id, name=item.name) if item else None,
            path=path or None,
            note=note,
        )
