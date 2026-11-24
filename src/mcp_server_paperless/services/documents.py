"""Document search: names to ids (decision-5), contradictions caught early, readable rows.

Rows name what they refer to and render custom field values readably, without the content. A
kind the user may not list leaves its names null instead of failing the search.
"""

import asyncio
import contextlib
import hashlib
import json
import re
from typing import TYPE_CHECKING, cast

from mcp_server_paperless.domain.documents import (
    ContentSlice,
    DocumentHistory,
    DocumentRead,
    DocumentRecord,
    DocumentSearchResult,
    DocumentSummary,
    DuplicateDocument,
    DuplicateList,
    FieldValue,
    Ref,
    SuggestionNames,
)
from mcp_server_paperless.domain.errors import (
    FeatureUnavailable,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.services.search_syntax import query_notices

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import date

    from pydantic import JsonValue

    from mcp_server_paperless.domain.documents import (
        CustomFieldValue,
        DocumentFacts,
        DocumentHit,
        DocumentQuery,
        DuplicateKind,
        Include,
        NameFilters,
        NameOrId,
    )
    from mcp_server_paperless.domain.duplicates import DuplicateRecord
    from mcp_server_paperless.domain.ports import DocumentReader, TrashReader
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from mcp_server_paperless.domain.trash import TrashedRecord
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# The kinds a search row refers to by id.
REFERRED: tuple[TaxonomyKind, ...] = (
    "tag",
    "correspondent",
    "document_type",
    "storage_path",
    "custom_field",
)
# Characters of the OCR text per slice unless the caller asks otherwise (design 7).
DEFAULT_SLICE = 20_000
# Newest audit log entries returned with include history; the rest is counted.
HISTORY_LIMIT = 50
# Most distinct linked documents whose titles a document read looks up.
LINK_LIMIT = 100
# The order sections are fetched and reported in.
SECTIONS: tuple[Include, ...] = (
    "notes",
    "history",
    "metadata",
    "versions",
    "suggestions",
    "duplicates",
)
# Most duplicates listed (each is a read of its own); the rest is counted.
DUPLICATE_LIMIT = 25
# What one read of the trash may cost, as in the check of uploads (decision-47).
TRASH_ENTRIES = 500
TRASH_BYTES = 64 * 1024 * 1024
NOT_COMPARED = (
    "Paperless compares original uploads and archive files, not later versions; documents this "
    "user cannot see are never compared."
)
TRASH_DETAILS = "In the trash Paperless tells no notes, custom fields, page count or versions."
NOT_LISTED = "Only the first {shown} of {total} duplicates are listed."
UNREAD_DETAILS = (
    "Only the title could be read of: {ids} (in the trash and not owned, beyond the first 500 "
    "entries, a trash over 64 MiB or that could not be read, or changed meanwhile)."
)
MONETARY = re.compile(r"([A-Z]{3})(-?\d+(?:\.\d+)?)")


def listed_version(document: DocumentRecord, version_id: int | None) -> int:
    """The version ``version_id`` of a document (None: the newest), which must be listed.

    Paperless would answer the file of a deleted version, too (decision-42).

    Raises:
        NotFound: The document lists no such version.
    """
    # The root version has the id of the document.
    listed = [version.id for version in document.versions] or [document.id]
    if version_id is not None and version_id not in listed:
        message = f"Document {document.id} has no version {version_id}."
        raise NotFound(message, hint="Its versions are in get_document with include versions.")
    return listed[0] if version_id is None else version_id


def text_state(document_id: int, version_id: int, text: str) -> str:
    """A fingerprint of the whole text of a version; versions often have the same text."""
    about = json.dumps([document_id, version_id, text])
    return hashlib.sha256(about.encode()).hexdigest()[:32]


def root_checksum(document: DocumentRecord) -> str | None:
    """The checksum of a document's original upload, in lower case."""
    root = next((v.checksum for v in document.versions if v.is_root), None)
    return root.lower() if root else None


def match_of(document: DocumentRecord, checksum: str | None) -> DuplicateKind | None:
    """How a duplicate whose original has ``checksum`` matches; None if that cannot be told."""
    own = root_checksum(document)
    if not own or not checksum:
        return None
    return "same_file" if checksum.lower() == own else "archive"


def document_link(web_url: str, document_id: int) -> str:
    """The link to a document in the Paperless web UI under ``web_url``."""
    return f"{web_url.rstrip('/')}/documents/{document_id}/details"


def _check_range(name: str, start: date | None, end: date | None) -> None:
    if start and end and start > end:
        message = f"{name}_from is after {name}_to."
        raise Validation(message, hint=f"Swap them, or leave one out for an open {name} range.")


def _check_absent(names: NameFilters, query: DocumentQuery) -> None:
    """has_x=false (no value) cannot be combined with a name that asks for a value."""
    asked = (
        (
            "has_correspondent",
            query.correspondent_isnull is True,
            {"correspondent": names.correspondent},
        ),
        (
            "has_document_type",
            query.document_type_isnull is True,
            {"document_type": names.document_type},
        ),
        (
            "has_storage_path",
            query.storage_path_isnull is True,
            {"storage_path": names.storage_path},
        ),
        (
            "has_tags",
            query.tagged is False,
            {"tags_all": names.tags_all, "tags_any": names.tags_any},
        ),
    )
    for flag, without, named in asked:
        for field, value in named.items():
            if without and value not in {None, ()}:
                message = f"{flag}=false contradicts {field}: no document can match both."
                raise Validation(message, hint=f"Leave out {flag} or {field}.")


def _readable(value: JsonValue, field: TaxonomyItem | None) -> JsonValue:
    """Select option ids as labels and "EUR12.50" as "EUR 12.50"; anything else unchanged."""
    if field is None or not isinstance(value, str):
        return value
    if field.data_type == "select":
        labels = {option.id: option.label for option in field.select_options or ()}
        return labels.get(value, value)
    money = MONETARY.fullmatch(value) if field.data_type == "monetary" else None
    return f"{money[1]} {money[2]}" if money else value


async def owner_id(taxonomy: TaxonomyResolver, value: NameOrId | None) -> int | None:
    """The user id ``value`` names. Paperless takes any owner id; only a username needs the list."""
    if isinstance(value, int) or (value and value.strip().isascii() and value.strip().isdigit()):
        return int(value)
    try:
        return None if value is None else (await taxonomy.resolve("user", value)).id
    except PermissionDenied as error:
        hint = "Pass the owner's user id instead of the username."
        raise PermissionDenied(error.message, hint=hint) from None


async def known_items(taxonomy: TaxonomyResolver, kind: TaxonomyKind) -> dict[int, TaxonomyItem]:
    """The objects of ``kind`` by id, for names; none if the user may not list them."""
    try:
        return {item.id: item for item in await taxonomy.items(kind)}
    except PermissionDenied:
        return {}


class DocumentService:
    """Finds documents for the current user."""

    def __init__(
        self,
        reader: DocumentReader,
        trash: TrashReader,
        taxonomy: TaxonomyResolver,
        *,
        web_url: str,
    ) -> None:
        """Search through ``reader``; ``web_url`` is the base of links to the Paperless UI."""
        self._reader: DocumentReader = reader
        self._trash: TrashReader = trash
        self._taxonomy: TaxonomyResolver = taxonomy
        self._web_url: str = web_url

    async def search(
        self, names: NameFilters, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentSearchResult:
        """One page of the documents matching ``names`` and ``query``.

        Raises:
            Validation: The filters contradict each other, or Paperless rejects them.
            NotFound: A name matches nothing, or ``page`` is beyond the last page.
            Ambiguous: A name matches more than one object.
        """
        if query.query is not None and query.more_like is not None:
            message = "Pass either query or more_like_id, not both."
            hint = "Search similar documents first, then filter by title or tags."
            raise Validation(message, hint=hint)
        _check_range("created", query.created_from, query.created_to)
        _check_range("added", query.added_from, query.added_to)
        _check_absent(names, query)
        # Names are optional: the ids a query already holds (a saved view's) stay unless named.
        given = {k: v for k, v in (await self._ids(names)).items() if v not in {None, ()}}
        resolved = query.model_copy(update=given)
        found = await self._reader.search_documents(resolved, page=page, page_size=page_size)
        known: dict[TaxonomyKind, dict[int, TaxonomyItem]] = {
            kind: await self._known(kind) for kind in REFERRED
        }
        return DocumentSearchResult(
            count=found.count,
            page=page,
            next_page=found.next_page,
            documents=tuple(self._summary(hit, known) for hit in found.hits),
            notices=query_notices(query.query or ""),
        )

    async def _ids(self, names: NameFilters) -> dict[str, object]:
        async def one(kind: TaxonomyKind, value: NameOrId | None) -> int | None:
            return None if value is None else (await self._taxonomy.resolve(kind, value)).id

        async def tags(values: tuple[NameOrId, ...]) -> tuple[int, ...]:
            return tuple([(await self._taxonomy.resolve("tag", value)).id for value in values])

        tags_all, tags_none = await tags(names.tags_all), await tags(names.tags_none)
        required = dict(zip(tags_all, names.tags_all, strict=True))
        both = [required[tag] for tag in tags_none if tag in required]
        if both:
            message = f"Tags both required and excluded: {', '.join(map(repr, both))}."
            raise Validation(message, hint="Remove them from tags_all or from tags_none.")
        return {
            "correspondent": await one("correspondent", names.correspondent),
            "document_type": await one("document_type", names.document_type),
            "storage_path": await one("storage_path", names.storage_path),
            "owner": await self._owner(names.owner),
            "tags_all": tags_all,
            "tags_any": await tags(names.tags_any),
            "tags_none": tags_none,
        }

    async def _owner(self, value: NameOrId | None) -> int | None:
        return await owner_id(self._taxonomy, value)

    async def _known(self, kind: TaxonomyKind) -> dict[int, TaxonomyItem]:
        return await known_items(self._taxonomy, kind)

    def _summary(
        self, hit: DocumentHit, known: dict[TaxonomyKind, dict[int, TaxonomyItem]]
    ) -> DocumentSummary:
        return DocumentSummary(
            id=hit.id,
            title=hit.title,
            created=hit.created,
            added=hit.added,
            correspondent=maybe_ref(known, "correspondent", hit.correspondent),
            document_type=maybe_ref(known, "document_type", hit.document_type),
            storage_path=maybe_ref(known, "storage_path", hit.storage_path),
            tags=tuple(_ref(known, "tag", tag) for tag in hit.tags),
            custom_fields=_fields(known, hit.custom_fields),
            highlights=hit.highlights,
            web_url=self._link(hit.id),
        )

    def _link(self, document_id: int) -> str:
        return document_link(self._web_url, document_id)

    async def get(self, document_id: int, include: Collection[Include] = ()) -> DocumentRead:
        """One document with names, readable custom fields and the sections in ``include``.

        A section the user may not read in Paperless (notes and the audit log need their own
        permissions, and the audit log can be switched off) is named in ``unavailable``
        instead of failing the call.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not view documents at all.
        """
        return await self.detail(await self._reader.get_document(document_id), include)

    async def detail(
        self, record: DocumentRecord, include: Collection[Include] = ()
    ) -> DocumentRead:
        """The detail of ``record`` as ``get`` returns it: names, readable fields, sections."""
        kinds: tuple[TaxonomyKind, ...] = (*REFERRED, "user")
        loaded = await asyncio.gather(*(self._known(kind) for kind in kinds))
        known = dict(zip(kinds, loaded, strict=True))
        titles = await self._linked_titles(record, known)
        wanted: list[Include] = [section for section in SECTIONS if section in include]
        sections: dict[str, object] = {}
        unavailable: list[Include] = []
        for section in wanted:
            try:
                sections.update(await self._section(section, record, known))
            except PermissionDenied, FeatureUnavailable:
                unavailable.append(section)
        return DocumentRead(
            id=record.id,
            title=record.title,
            created=record.created,
            added=record.added,
            modified=record.modified,
            archive_serial_number=record.archive_serial_number,
            owner=maybe_ref(known, "user", record.owner),
            correspondent=maybe_ref(known, "correspondent", record.correspondent),
            document_type=maybe_ref(known, "document_type", record.document_type),
            storage_path=maybe_ref(known, "storage_path", record.storage_path),
            tags=tuple(_ref(known, "tag", tag) for tag in record.tags),
            custom_fields=_fields(known, record.custom_fields, titles),
            original_file_name=record.original_file_name,
            mime_type=record.mime_type,
            page_count=record.page_count,
            notes_count=record.notes_count,
            web_url=self._link(record.id),
        ).model_copy(update={**sections, "unavailable": tuple(unavailable)})

    async def _linked_titles(
        self, record: DocumentRecord, known: dict[TaxonomyKind, dict[int, TaxonomyItem]]
    ) -> dict[int, str]:
        """Titles of the documents the document-link fields of ``record`` point to."""
        linked = [
            number
            for value in record.custom_fields
            if _is_link(known, value)
            for number in cast("list[int]", value.value)
        ]
        return await self._reader.get_document_titles(list(dict.fromkeys(linked))[:LINK_LIMIT])

    async def _section(
        self,
        section: Include,
        record: DocumentRecord,
        known: dict[TaxonomyKind, dict[int, TaxonomyItem]],
    ) -> dict[str, object]:
        """The fields of the detail one ``include`` section fills."""
        reader, number = self._reader, record.id
        match section:
            case "notes":
                return {"notes": await reader.list_document_notes(number)}
            case "history":
                entries = await reader.list_document_history(number)
                shown = entries[:HISTORY_LIMIT]
                return {
                    "history": DocumentHistory(
                        total=len(entries), truncated=len(shown) < len(entries), entries=shown
                    )
                }
            case "metadata":
                return {"file_metadata": await reader.get_document_metadata(number)}
            case "versions":
                return {"versions": record.versions}
            case "suggestions":
                found = await reader.get_document_suggestions(number)
                return {
                    "suggestions": SuggestionNames(
                        correspondents=tuple(
                            _ref(known, "correspondent", n) for n in found.correspondents
                        ),
                        tags=tuple(_ref(known, "tag", n) for n in found.tags),
                        document_types=tuple(
                            _ref(known, "document_type", n) for n in found.document_types
                        ),
                        storage_paths=tuple(
                            _ref(known, "storage_path", n) for n in found.storage_paths
                        ),
                        dates=found.dates,
                    )
                }
            case "duplicates":
                return {"duplicates": await self._duplicates(record, known)}

    async def _duplicates(
        self, record: DocumentRecord, known: dict[TaxonomyKind, dict[int, TaxonomyItem]]
    ) -> DuplicateList:
        found = sorted(
            await self._reader.get_duplicates(record.id),
            key=lambda d: (d.deleted_at is not None, d.id),
        )
        shown = found[:DUPLICATE_LIMIT]
        trashed = [d.id for d in shown if d.deleted_at is not None]
        live = [d.id for d in shown if d.deleted_at is None]
        records: dict[int, DocumentRecord | TrashedRecord] = {**await self._live(live)}
        records.update(await self._trashed(trashed))
        gaps = [NOT_COMPARED, *([TRASH_DETAILS] if trashed else [])]
        if len(shown) < len(found):
            gaps.append(NOT_LISTED.format(shown=len(shown), total=len(found)))
        unread = [str(d.id) for d in shown if d.id not in records]
        if unread:
            gaps.append(UNREAD_DETAILS.format(ids=", ".join(unread)))
        return DuplicateList(
            total=len(found),
            truncated=len(shown) < len(found),
            documents=tuple(self._duplicate(record, d, records.get(d.id), known) for d in shown),
            not_compared=tuple(gaps),
        )

    async def _live(self, ids: list[int]) -> dict[int, DocumentRecord]:
        found: dict[int, DocumentRecord] = {}
        for number in ids:
            with contextlib.suppress(NotFound):  # deleted meanwhile
                found[number] = await self._reader.get_document(number)
        return found

    async def _trashed(self, ids: list[int]) -> dict[int, TrashedRecord]:
        if not ids:
            return {}
        try:
            return await self._trash.find_in_trash(
                ids, entries=TRASH_ENTRIES, max_bytes=TRASH_BYTES
            )
        except PaperlessMcpError:  # named as a gap; the live ones stay
            return {}

    def _duplicate(
        self,
        record: DocumentRecord,
        duplicate: DuplicateRecord,
        read: DocumentRecord | TrashedRecord | None,
        known: dict[TaxonomyKind, dict[int, TaxonomyItem]],
    ) -> DuplicateDocument:
        found = DuplicateDocument(
            id=duplicate.id,
            title=duplicate.title,
            in_trash=duplicate.deleted_at is not None,
            deleted_at=duplicate.deleted_at,
            web_url=None if duplicate.deleted_at else self._link(duplicate.id),
        )
        if read is None:
            return found
        values: dict[str, object] = {
            "created": read.created,
            "added": read.added,
            "owner": maybe_ref(known, "user", read.owner),
            "correspondent": maybe_ref(known, "correspondent", read.correspondent),
            "document_type": maybe_ref(known, "document_type", read.document_type),
            "storage_path": maybe_ref(known, "storage_path", read.storage_path),
            "tags": tuple(_ref(known, "tag", tag) for tag in read.tags),
            "archive_serial_number": read.archive_serial_number,
            "original_file_name": read.original_file_name,
            "mime_type": read.mime_type,
        }
        if isinstance(read, DocumentRecord):
            values |= {
                "match": match_of(record, root_checksum(read)),
                "page_count": read.page_count,
                "notes_count": read.notes_count,
                "custom_fields": _fields(known, read.custom_fields),
                "version_count": len(read.versions),
                "user_can_change": read.user_can_change,
            }
        else:
            values["match"] = match_of(record, read.checksum)
        theirs, ours = _comparable(read), _comparable(record)
        differs = tuple(name for name in theirs if theirs[name] != ours[name])
        return found.model_copy(update={**values, "differs": differs})

    async def content(
        self, document_id: int, *, offset: int, max_chars: int, version_id: int | None = None
    ) -> ContentSlice:
        """``max_chars`` characters of the OCR text of a version (default: newest) from ``offset``.

        An offset at or past the end gives an empty slice, so callers can always read
        ``total_chars``. ``next_offset`` is null once the slice reaches the end.

        Raises:
            NotFound: No such document, the user may not see it, or it has no such version.
        """
        found = await self._reader.get_document_content(document_id, version_id=version_id)
        text = found.content
        end = offset + max_chars
        return ContentSlice(
            document_id=document_id,
            offset=offset,
            content=text[offset:end],
            total_chars=len(text),
            next_offset=end if end < len(text) else None,
            version_id=found.version_id,
            text_state=text_state(document_id, found.version_id, text),
            web_url=self._link(document_id),
        )


def _comparable(document: DocumentFacts) -> dict[str, object]:
    """What can tell two copies of a file apart, by field; less of a document in the trash."""
    values: dict[str, object] = {
        "title": document.title,
        "created": document.created,
        "owner": document.owner,
        "correspondent": document.correspondent,
        "document_type": document.document_type,
        "storage_path": document.storage_path,
        "tags": set(document.tags),
        "archive_serial_number": document.archive_serial_number,
        "original_file_name": document.original_file_name,
        "mime_type": document.mime_type,
    }
    if isinstance(document, DocumentRecord):
        values |= {
            "page_count": document.page_count,
            "notes_count": document.notes_count,
            "custom_fields": {f.field: f.value for f in document.custom_fields},
            "version_count": len(document.versions),
        }
    return values


def _ref(
    known: dict[TaxonomyKind, dict[int, TaxonomyItem]], kind: TaxonomyKind, number: int
) -> Ref:
    item = known[kind].get(number)
    return Ref(id=number, name=item.name if item else None)


def maybe_ref(
    known: dict[TaxonomyKind, dict[int, TaxonomyItem]], kind: TaxonomyKind, number: int | None
) -> Ref | None:
    """The object of ``kind`` with its name if known; None for no id."""
    return None if number is None else _ref(known, kind, number)


def _is_link(known: dict[TaxonomyKind, dict[int, TaxonomyItem]], value: CustomFieldValue) -> bool:
    """Whether ``value`` is a document-link field holding a list of document ids."""
    item = known["custom_field"].get(value.field)
    return bool(item and item.data_type == "documentlink" and isinstance(value.value, list))


def _fields(
    known: dict[TaxonomyKind, dict[int, TaxonomyItem]],
    values: tuple[CustomFieldValue, ...],
    titles: dict[int, str] | None = None,
) -> tuple[FieldValue, ...]:
    """Custom field values, readable; with ``titles`` document links name their documents."""
    fields = known["custom_field"]

    def render(value: CustomFieldValue) -> JsonValue:
        if titles is not None and _is_link(known, value):
            return [{"id": n, "name": titles.get(n)} for n in cast("list[int]", value.value)]
        return _readable(value.value, fields.get(value.field))

    return tuple(
        FieldValue(
            id=value.field,
            name=item.name if (item := fields.get(value.field)) else None,
            value=render(value),
        )
        for value in values
    )
