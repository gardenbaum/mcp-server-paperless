"""Small questions to Paperless: words of the search index, the path a template gives a file."""

from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.documents import Ref
from mcp_server_paperless.domain.errors import PermissionDenied, Validation
from mcp_server_paperless.domain.lookups import StoragePathTest, Words
from mcp_server_paperless.services.documents import known_items

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import DocumentRecord
    from mcp_server_paperless.domain.ports import DocumentReader, TaxonomyReader
    from mcp_server_paperless.domain.taxonomy import TaxonomyKind
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
            PermissionDenied: The user may not view documents, or storage paths, or the document
                refers to an object the user may not see: Paperless renders every name.
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
        await self._require_visible(await self._documents.get_document(document_id))
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

    async def _require_visible(self, document: DocumentRecord) -> None:
        """Refuse a document whose template could show the name of an object hidden from the user.

        Paperless checks only the document, and a template names every object it refers to. What
        the user may see is read anew.
        """
        used: dict[TaxonomyKind, set[int]] = {
            "correspondent": _ids(document.correspondent),
            "document_type": _ids(document.document_type),
            "storage_path": _ids(document.storage_path),
            "tag": set(document.tags),
            "custom_field": {value.field for value in document.custom_fields},
            "user": _ids(document.owner),
        }
        if used["user"]:
            me = await self._documents.get_signed_in()
            used["user"] -= _ids(me.id if me else None)
        hidden: list[str] = []
        for kind, ids in used.items():
            if not ids:
                continue
            self._resolver.invalidate(kind)
            if ids - set(await known_items(self._resolver, kind)):
                hidden.append("owner" if kind == "user" else kind.replace("_", " "))
        if hidden:
            message = (
                f"Document {document.id} refers to objects you may not see ({', '.join(hidden)}); "
                "a template could show their names, so it is not tried."
            )
            raise PermissionDenied(message, hint="Try it on a document whose objects you see.")


def _ids(number: int | None) -> set[int]:
    return set() if number is None else {number}
