"""What each bulk operation does to a document: names become ids, and every check without a write.

Tags follow the hierarchy rules of ``tag_hierarchy`` as ``update_document`` does: parents come
along with an added child, and removing a tag whose child tags stay is refused. Custom fields are
merged: only the named fields are set or removed, the others stay.

Document links are mirrored by Paperless on the linked documents (and dropped from old targets),
without checking change permission there; ``Operation.mirrored`` names those documents.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.bulk import (
    MAX_DOCUMENTS,
    AddTags,
    DeleteDocuments,
    FieldsChange,
    ModifyCustomFields,
    ModifyTags,
    ReferenceChange,
    RemoveTags,
    ReprocessChange,
    SetCorrespondent,
    SetDocumentType,
    SetStoragePath,
    TagsChange,
    TrashDocuments,
)
from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.domain.errors import (
    NotFound,
    PermissionDenied,
    Validation,
    listed,
    quoted,
)
from mcp_server_paperless.services.custom_values import normalise
from mcp_server_paperless.services.document_edits import COUNTED, merged_tags, same_value
from mcp_server_paperless.services.tag_hierarchy import (
    ancestors,
    blocked_removals,
    close_over_ancestors,
)
from mcp_server_paperless.services.taxonomy_edits import documents as count_documents

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.bulk import BulkOperation, DocumentChange, Reference
    from mcp_server_paperless.domain.documents import DocumentHit, NameOrId
    from mcp_server_paperless.domain.ports import DocumentReader
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

TRASH: Final = "(they can be restored from there)"
REPROCESS: Final = "(OCR and archiving run again in the background)"


@dataclass(frozen=True, slots=True)
class Operation:
    """An operation with its names resolved: the request and what it does to each document.

    ``lead`` and ``tail`` surround the documents in the operation's sentence, so the same
    sentence names "the selected documents", or a count with titles.
    """

    name: str
    lead: str
    tail: str
    change: DocumentChange
    effects: Callable[[list[DocumentHit]], dict[int, str]]
    dirty: tuple[TaxonomyKind, ...]
    # The current values of what the change touches, the same for the same values.
    touched: Callable[[DocumentHit], object] = lambda _hit: None
    # The ids of other documents the change reaches (mirrored links), given the changing ones.
    mirrored: Callable[[list[DocumentHit]], set[int]] = lambda _hits: set()
    # Whether removing tags also takes child tags the user cannot see off the documents.
    hidden_children: bool = False
    # Whether it works on the documents' files, so that a file added meanwhile matters.
    on_files: bool = False

    def phrase(self, docs: str) -> str:
        """The operation's sentence about ``docs``."""
        return f"{self.lead}{docs}{self.tail}"

    @property
    def summary(self) -> str:
        """What the operation does, in words."""
        return self.phrase("the selected documents")


def _names(items: list[TaxonomyItem]) -> dict[int, str]:
    return {item.id: item.name for item in items}


def _unique(values: Sequence[int]) -> list[int]:
    return list(dict.fromkeys(values))


def named(documents: Sequence[tuple[int, str]], what: str = "") -> str:
    """How many documents (``what``) and which, all: 1 document (5 'A'); #7 is a hidden one."""
    each = [
        title if title == f"#{number}" else f"{number} {quoted(title)}"
        for number, title in documents[:MAX_DOCUMENTS]
    ]
    rest = len(documents) - MAX_DOCUMENTS
    more = f" and {rest} more" if rest > 0 else ""
    return f"{count_documents(len(documents))}{what} ({', '.join(each)}{more})"


def titled(hits: Sequence[DocumentHit]) -> list[tuple[int, str]]:
    """The documents as ``named`` takes them."""
    return [(hit.id, hit.title) for hit in hits]


class BulkOperations:
    """Resolves bulk operations for the current user."""

    def __init__(self, reader: DocumentReader, taxonomy: TaxonomyResolver) -> None:
        """Look names up in ``taxonomy`` and linked documents in ``reader``."""
        self._reader: DocumentReader = reader
        self._taxonomy: TaxonomyResolver = taxonomy

    async def resolve(self, operation: BulkOperation) -> Operation:
        """Names to ids and every check that needs no document.

        Raises:
            Validation: The operation is empty or contradicts itself, or a value does not fit.
            NotFound: A tag, correspondent, document type, storage path, custom field or linked
                document is unknown or hidden from the user.
            Ambiguous: A name matches more than one object.
            PermissionDenied: The user may not change a linked document.
        """
        if isinstance(operation, AddTags | RemoveTags | ModifyTags):
            return await self._tags(operation)
        if isinstance(operation, SetCorrespondent | SetDocumentType | SetStoragePath):
            return await self._reference(operation)
        if isinstance(operation, ModifyCustomFields):
            return await self._fields(operation)
        if isinstance(operation, DeleteDocuments):
            return Operation(
                name=operation.op,
                lead="move ",
                tail=f" to the Paperless trash {TRASH}",
                change=TrashDocuments(),
                effects=lambda hits: dict.fromkeys((h.id for h in hits), "move to the trash"),
                dirty=COUNTED,
                on_files=True,
            )
        return Operation(
            name=operation.op,
            lead="reprocess ",
            tail=f" {REPROCESS}",
            change=ReprocessChange(),
            effects=lambda hits: dict.fromkeys((h.id for h in hits), "reprocess"),
            dirty=(),
            on_files=True,
        )

    async def _tag_ids(self, values: Sequence[NameOrId]) -> list[int]:
        found: list[int] = []
        for value in values:
            try:
                found.append((await self._taxonomy.resolve("tag", value)).id)
            except NotFound as error:
                hint = f"{error.hint} Create it first with create_taxonomy_item(kind='tag')."
                raise NotFound(error.message, hint=hint) from None
        return _unique(found)

    async def _tags(self, operation: AddTags | RemoveTags | ModifyTags) -> Operation:
        add = operation.add if isinstance(operation, ModifyTags) else []
        remove = operation.remove if isinstance(operation, ModifyTags) else []
        if isinstance(operation, AddTags):
            add = operation.tags
        if isinstance(operation, RemoveTags):
            remove = operation.tags
        if not (add or remove):
            message = "Nothing to change: no tag was given."
            raise Validation(message, hint="Pass tags to add or to remove.")
        added, removed = await self._tag_ids(add), await self._tag_ids(remove)
        if set(added) & set(removed):
            message = "A tag is both added and removed."
            raise Validation(message, hint="Name each tag once, to add or to remove.")
        items = await self._taxonomy.items("tag")
        names, parents = _names(items), {t.id: t.parent for t in items}
        for tag in added:
            for parent in set(ancestors(tag, parents)) & set(removed):
                message = (
                    f"Adding {quoted(names[tag])} needs its parent {quoted(names[parent])}, "
                    "which is also removed."
                )
                raise Validation(message, hint="Keep the parent, or do not add the child.")
        lead, tail = _tag_sentence([names[t] for t in added], [names[t] for t in removed])
        return Operation(
            name=operation.op,
            lead=lead,
            tail=tail,
            change=TagsChange(add=tuple(added), remove=tuple(removed)),
            effects=lambda hits: _tag_effects(hits, added, removed, names, parents),
            dirty=("tag",),
            touched=lambda hit: sorted(hit.tags),
            hidden_children=bool(removed),
        )

    async def _reference(
        self, operation: SetCorrespondent | SetDocumentType | SetStoragePath
    ) -> Operation:
        kind: Reference
        value: NameOrId | None
        if isinstance(operation, SetCorrespondent):
            kind, value = "correspondent", operation.correspondent
        elif isinstance(operation, SetDocumentType):
            kind, value = "document_type", operation.document_type
        else:
            kind, value = "storage_path", operation.storage_path
        label = kind.replace("_", " ")
        target = None if value is None else await self._taxonomy.resolve(kind, value)
        try:
            known = _names(await self._taxonomy.items(kind))
        except PermissionDenied:
            known = {}  # clearing needs no view permission; the old name is then just its id
        wanted = None if target is None else target.id

        def column(hit: DocumentHit) -> int | None:
            return getattr(hit, kind)  # pyright: ignore[reportAny] -- the id field named by kind

        def describe(number: int | None) -> str:
            if number is None:
                return "none"
            return quoted(known[number]) if number in known else f"#{number}"

        def effects(hits: list[DocumentHit]) -> dict[int, str]:
            return {
                hit.id: f"{label} {describe(column(hit))} -> {describe(wanted)}"
                for hit in hits
                if column(hit) != wanted
            }

        if target is None:
            lead, tail = f"remove the {label} from ", ""
        else:
            lead, tail = f"set the {label} of ", f" to {quoted(target.name)}"
        return Operation(
            name=operation.op,
            lead=lead,
            tail=tail,
            change=ReferenceChange(field=kind, value=wanted),
            effects=effects,
            dirty=(kind,),
            touched=column,
        )

    async def _fields(self, operation: ModifyCustomFields) -> Operation:
        if not (operation.values or operation.remove):
            message = "Nothing to change: no custom field was given."
            raise Validation(message, hint="Pass values to set or custom fields to remove.")
        sets: dict[int, JsonValue] = {}
        links: set[int] = set()
        drop_links: set[int] = set()
        names: dict[int, str] = {}
        for key, value in operation.values.items():
            item = await self._taxonomy.resolve("custom_field", key)
            if item.id in sets:
                message = f"Custom field {quoted(item.name)} is given twice."
                raise Validation(message, hint="Name each field once, by name or by id.")
            sets[item.id] = normalise(item, value)
            names[item.id] = item.name
            if item.data_type == "documentlink":
                links.add(item.id)
        removed: list[int] = []
        for key in operation.remove:
            item = await self._taxonomy.resolve("custom_field", key)
            names[item.id] = item.name
            removed.append(item.id)
            if item.data_type == "documentlink":
                drop_links.add(item.id)
        removed = _unique(removed)
        if sets.keys() & set(removed):
            message = "A custom field is both set and removed."
            raise Validation(message, hint="Set it or remove it, not both.")
        await self._linkable({n for number in links for n in _document_ids(sets[number])})
        lead = _field_sentence([names[n] for n in sets], [names[n] for n in removed])
        dropped = [n for n in removed if n in drop_links]
        return Operation(
            name=operation.op,
            lead=lead,
            tail="",
            change=FieldsChange(values=sets, remove=tuple(removed)),
            effects=lambda hits: _field_effects(hits, sets, links, removed, names),
            dirty=("custom_field",),
            touched=lambda hit: sorted(
                (v.field, repr(v.value)) for v in hit.custom_fields if v.field in names
            ),
            mirrored=lambda hits: _mirrored(hits, sets, links, dropped),
        )

    async def _linkable(self, documents: set[int]) -> None:
        """Every linked document must be visible and changeable: Paperless mirrors the link there.

        Raises:
            NotFound: A linked document does not exist or is hidden from the user.
            PermissionDenied: The user may not change a linked document.
        """
        if not documents:
            return
        hits = await self._visible(sorted(documents))
        blocked = [hit for hit in hits if hit.user_can_change is False]
        if blocked:
            message = (
                "A link also changes the linked document, and you may not change "
                f"{named(titled(blocked))}."
            )
            raise PermissionDenied(message, hint="Link only to documents you may change.")

    async def _visible(self, numbers: list[int]) -> list[DocumentHit]:
        """The given documents, read page by page; every one must exist and be visible."""
        found: dict[int, DocumentHit] = {}
        for start in range(0, len(numbers), MAX_DOCUMENTS):
            query = DocumentQuery(ids=tuple(numbers[start : start + MAX_DOCUMENTS]))
            page = await self._reader.search_documents(query, page=1, page_size=MAX_DOCUMENTS)
            found.update((hit.id, hit) for hit in page.hits)
        missing = [number for number in numbers if number not in found]
        require_all(missing)
        return [found[number] for number in numbers]


def require_all(missing: Sequence[int]) -> None:
    """Refuse named documents that do not exist or are hidden from the user.

    Raises:
        NotFound: There are some; all of them are named.
    """
    if missing:
        noun = "document" if len(missing) == 1 else "documents"
        verb = "exists" if len(missing) == 1 else "exist"
        message = f"No {noun} {', '.join(map(str, missing))} {verb} that this user may see."
        raise NotFound(message, hint="Take the ids from search_documents rows.")


def _tag_sentence(added: list[str], removed: list[str]) -> tuple[str, str]:
    """The words before and after the documents in a tag operation's sentence."""
    adding = f"add the tags {listed(added)}"
    removing = f"remove the tags {listed(removed)}"
    if added and removed:
        return f"{adding} and {removing} on ", ""
    return (f"{adding} to ", "") if added else (f"{removing} from ", "")


def _label(names: dict[int, str], tag: int) -> str:
    """The name of a tag, or what is known of one the user cannot see."""
    return names.get(tag) or f"#{tag} (a tag you cannot see)"


def _tag_effects(
    hits: list[DocumentHit],
    added: list[int],
    removed: list[int],
    names: dict[int, str],
    parents: dict[int, int | None],
) -> dict[int, str]:
    """Per document the tag change, or an error if a removal would take child tags along."""
    blocked: dict[int, tuple[list[int], int]] = {}
    found: dict[int, str] = {}
    for hit in hits:
        remaining = merged_tags(hit.tags, added, removed)
        refused = blocked_removals([t for t in removed if t in hit.tags], remaining, parents)
        for tag, children in refused.items():
            known, count = blocked.get(tag, ([], 0))
            blocked[tag] = (_unique([*known, *children]), count + 1)
        wanted = close_over_ancestors(remaining, parents)
        if set(wanted) != set(hit.tags):
            gained = [f"+{_label(names, t)}" for t in wanted if t not in hit.tags]
            lost = [f"-{_label(names, t)}" for t in hit.tags if t not in wanted]
            found[hit.id] = f"tags {', '.join([*gained, *lost])}"
    for tag, (children, count) in blocked.items():
        message = (
            f"Removing {quoted(names[tag])} also removes its child tags "
            f"{listed([names[child] for child in children])} (on {count_documents(count)})."
        )
        hint = f"List them in the tags to remove too, or keep {quoted(names[tag])}."
        raise Validation(message, hint=hint)
    return found


def _document_ids(value: JsonValue) -> set[int]:
    return {n for n in value if isinstance(n, int)} if isinstance(value, list) else set()


def _field_sentence(sets: list[str], removed: list[str]) -> str:
    """The words before the documents in a custom field operation's sentence."""
    setting = f"set the custom fields {listed(sets)}"
    if sets and removed:
        return f"{setting} and remove {listed(removed)} on "
    return f"{setting} on " if sets else f"remove the custom fields {listed(removed)} from "


def _field_effects(
    hits: list[DocumentHit],
    sets: dict[int, JsonValue],
    links: set[int],
    removed: list[int],
    names: dict[int, str],
) -> dict[int, str]:
    """Per document the custom fields that differ from what is asked."""
    found: dict[int, str] = {}
    for hit in hits:
        have = {v.field: v.value for v in hit.custom_fields}
        # Paperless skips a document-link field that links the document to itself.
        own = {n for n in links if hit.id in _document_ids(sets[n])}
        changing = [
            n for n in sets if n not in own and (n not in have or not same_value(have[n], sets[n]))
        ]
        dropped = [n for n in removed if n in have]
        if changing or dropped:
            texts = [names[n] for n in changing] + [f"-{names[n]}" for n in dropped]
            found[hit.id] = f"custom fields {', '.join(texts)}"
    return found


def _mirrored(
    hits: list[DocumentHit], sets: dict[int, JsonValue], links: set[int], dropped: list[int]
) -> set[int]:
    """The documents whose reverse link Paperless adds or removes for the changing ``hits``."""
    reached: set[int] = set()
    for hit in hits:
        have = {v.field: _document_ids(v.value) for v in hit.custom_fields}
        for number in links:
            wanted = _document_ids(sets[number])
            if hit.id not in wanted:  # a link to itself is skipped altogether
                # Paperless adds the reverse link on the targets; it never takes one from the
                # old targets of a changed list (3.4.2), only when the field is removed.
                reached |= wanted - have.get(number, set())
        for number in dropped:
            reached |= have.get(number, set())
    return reached
