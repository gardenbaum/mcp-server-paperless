"""Which documents a call is about: ids, search filters or a saved view become a set (decision-69).

The set is resolved here, in one request, and never by Paperless's own ``filters``: Paperless reads
a filter it does not understand as none and names nobody. A filter or a view is resolved anew in
every call; with ids too, the call is refused unless each of them still matches.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.selection import MAX_MATCHES, SelectionFilters
from mcp_server_paperless.services.search_syntax import query_notices

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import BaseModel

    from mcp_server_paperless.domain.documents import DocumentHit, Ordering
    from mcp_server_paperless.domain.ports import DocumentReader
    from mcp_server_paperless.domain.selection import Selection
    from mcp_server_paperless.domain.taxonomy import TaxonomyKind
    from mcp_server_paperless.services.documents import DocumentService
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver
    from mcp_server_paperless.services.views import ViewService

SELECT_BYTES: Final = 64 * 1024 * 1024
# What a view excludes, and the kind of the objects: one that is gone excludes nothing.
EXCLUDING: Final[Mapping[str, TaxonomyKind]] = {
    "tags_none": "tag",
    "correspondent_none": "correspondent",
    "document_type_none": "document_type",
    "storage_path_none": "storage_path",
    "owner_none": "user",
    "custom_fields_none": "custom_field",
}
NARROW: Final = "Narrow the filters (dates, tags, correspondent) or pass document_ids."
EXCLUDES_NOTHING: Final = (
    "The selection excludes what you cannot see; if it was deleted, more may match: "
)
# Most ids an error names that nobody asked for.
SHOWN: Final = 10
EVERYTHING: Final = "The saved view filters nothing: it is every document you may see."


@dataclass(frozen=True, slots=True)
class Selected:
    """The documents of a selection.

    Attributes:
        hits: The selected documents, in the order of the ids or of the search.
        matched: How many documents match, the excluded ones too.
        missing: Named ids that do not exist or are hidden from the user.
        also_matching: With ids and filters: what matches now and was not named.
        excluded_absent: What is excluded although the user's list does not hold it. An object
            that was deleted excludes nothing, so more matches than was meant; one that is hidden
            from the user does exclude. The two cannot be told apart, so it is noticed, not
            refused; whoever changes documents decides on it (PLMCP-80.2).
        excluded_unchecked: What is excluded of a kind the user may not list: not known.
        notices: What the caller should know of the filters.
    """

    hits: tuple[DocumentHit, ...]
    matched: int
    missing: tuple[int, ...] = ()
    also_matching: tuple[int, ...] = ()
    excluded_absent: tuple[str, ...] = ()
    excluded_unchecked: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()


def _empty(value: object) -> bool:
    """Whether a filter with ``value`` filters nothing; false and 0 are values."""
    return value in (None, (), []) or (isinstance(value, str) and not value.strip())


def _named(ids: Sequence[int]) -> str:
    return ", ".join(map(str, ids))


def _values(model: BaseModel) -> dict[str, object]:
    return model.model_dump(exclude_none=True)


def _check(selection: Selection) -> None:
    chosen = selection.filters is not None or selection.saved_view is not None
    if selection.filters is not None and selection.saved_view is not None:
        message = "filters and saved_view cannot be combined."
        raise Validation(message, hint="Pass one of them; a view can be rewritten as filters.")
    if selection.exclude_ids and not chosen:
        message = "exclude_ids needs filters or saved_view."
        raise Validation(message, hint="Leave the documents out of document_ids instead.")
    if not chosen and not selection.document_ids:
        message = "No document is chosen."
        raise Validation(message, hint="Pass document_ids, filters or saved_view.")
    both = sorted({*selection.document_ids} & {*selection.exclude_ids})
    if both:
        message = f"Documents both named and excluded: {_named(both)}."
        raise Validation(message, hint="Take them out of document_ids or of exclude_ids.")
    if selection.filters is not None:
        _check_filters(selection.filters)


def _check_filters(filters: SelectionFilters) -> None:
    given = _values(filters)
    if not given:
        message = "filters holds no filter: that would be every document."
        raise Validation(message, hint="Name at least one filter, as in search_documents.")
    empty = [name for name, value in given.items() if _empty(value)]
    if empty:
        message = f"Empty filters match everything: {', '.join(empty)}."
        raise Validation(message, hint="Give them a value or leave them out.")


class DocumentSelector:
    """Resolves selections for the current user."""

    def __init__(
        self,
        reader: DocumentReader,
        documents: DocumentService,
        views: ViewService,
        taxonomy: TaxonomyResolver,
    ) -> None:
        """Read through ``reader``; names and views mean what they mean in a search."""
        self._taxonomy: TaxonomyResolver = taxonomy
        self._reader: DocumentReader = reader
        self._documents: DocumentService = documents
        self._views: ViewService = views

    async def select(
        self, selection: Selection, *, ordering: Ordering | None = None, change: bool = False
    ) -> Selected:
        """The documents of ``selection``, in one request.

        ``ordering`` replaces the order of the ids, of the view or of the search. With ``change``
        what matches more than was meant is refused instead of noticed. It does not
        refuse ``missing`` or ``excluded_absent``: the caller has to decide on them.

        Raises:
            Validation: Nothing or too much was chosen, a filter is empty or contradicts another,
                or a named document no longer matches the filters.
            NotFound: A name or the view matches nothing.
            Ambiguous: A name or the view matches several.
            PermissionDenied: The user may not view documents, or saved views.
            AnswerTooLarge: The rows are larger than 64 MiB.
        """
        _check(selection)
        ids = tuple(dict.fromkeys(selection.document_ids))
        if selection.filters is None and selection.saved_view is None:
            hits = await self._read(DocumentQuery(ids=ids, ordering=ordering))
            found = {hit.id for hit in hits}
            missing = tuple(number for number in ids if number not in found)
            return Selected(_arranged(hits, ids, ordering), len(hits), missing)
        query = await self._query(selection, ordering)
        notices = query_notices(query.query or "")
        everything = all(_empty(v) for name, v in _values(query).items() if name != "ordering")
        if change and everything:
            raise Validation(EVERYTHING, hint="Use filters, or a view with a rule.")
        if change and notices:
            message = f"The full-text query may match more than was meant: {' '.join(notices)}"
            hint = "Write the query as the notice says, or use structured filters."
            raise Validation(message, hint=hint)
        absent, unchecked = await self._excluded(query)
        if everything:
            notices = (EVERYTHING, *notices)
        if absent:
            notices = (f"{EXCLUDES_NOTHING}{', '.join(absent)}.", *notices)
        matches = await self._read(query)
        excluded = set(selection.exclude_ids)
        hits = tuple(hit for hit in matches if hit.id not in excluded)
        also: tuple[int, ...] = ()
        if ids:
            _require_all(ids, hits)
            also = tuple(hit.id for hit in hits if hit.id not in ids)
            hits = _arranged([hit for hit in hits if hit.id in ids], ids, ordering)
        return Selected(
            hits,
            len(matches),
            also_matching=also,
            excluded_absent=absent,
            excluded_unchecked=unchecked,
            notices=notices,
        )

    async def _query(self, selection: Selection, ordering: Ordering | None) -> DocumentQuery:
        if selection.saved_view is None:
            filters = selection.filters or SelectionFilters()
            return await self._documents.resolved(*filters.parts(ordering))
        query = await self._views.query(selection.saved_view)
        return query if ordering is None else query.model_copy(update={"ordering": ordering})

    async def _excluded(self, query: DocumentQuery) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """What ``query`` excludes and the list lacks, and what cannot be listed; read anew."""
        absent: list[str] = []
        unchecked: list[str] = []
        for field, kind in EXCLUDING.items():
            ids = cast("tuple[int, ...]", getattr(query, field))
            if not ids:
                continue
            named = {number: f"{kind.replace('_', ' ')} {number}" for number in ids}
            self._taxonomy.invalidate(kind)
            try:
                listed = {item.id for item in await self._taxonomy.items(kind)}
            except PermissionDenied:
                unchecked += named.values()
            else:
                absent += [name for number, name in named.items() if number not in listed]
        return tuple(absent), tuple(unchecked)

    async def _read(self, query: DocumentQuery) -> tuple[DocumentHit, ...]:
        try:
            page = await self._reader.select_documents(
                query, limit=MAX_MATCHES + 1, max_bytes=SELECT_BYTES
            )
        except AnswerTooLarge:
            message = "The rows of the matching documents are larger than 64 MiB."
            raise AnswerTooLarge(message, hint=NARROW) from None
        hits = tuple({hit.id: hit for hit in page.hits}.values())
        count = page.count - (len(page.hits) - len(hits))
        unasked = [hit.id for hit in hits if hit.id not in query.ids] if query.ids else []
        if unasked:
            named = _named(unasked[:SHOWN])
            message = f"Paperless named documents that were not asked for: {named}."
            hint = "Nothing was used of its answer; tell the administrator."
            raise PaperlessMcpError(message, hint=hint)
        if count > MAX_MATCHES:
            message = f"{count} documents match; at most {MAX_MATCHES} may."
            raise Validation(message, hint=NARROW)
        if len(hits) < count:
            message = f"Paperless named only {len(hits)} of the {count} documents that match."
            raise PaperlessMcpError(message, hint="Try again in a moment, or narrow the filters.")
        return hits


def _arranged(
    hits: Sequence[DocumentHit], ids: tuple[int, ...], ordering: Ordering | None
) -> tuple[DocumentHit, ...]:
    """The hits in the order of ``ids``, or as they were ordered on request."""
    return tuple(hits if ordering else sorted(hits, key=lambda hit: ids.index(hit.id)))


def _require_all(ids: tuple[int, ...], hits: Sequence[DocumentHit]) -> None:
    matching = {hit.id for hit in hits}
    gone = [number for number in ids if number not in matching]
    if gone:
        verb = "matches" if len(gone) == 1 else "match"
        named = _named(gone)
        message = f"{len(gone)} of the named documents no longer {verb}: {named}."
        hint = "Choose again with the filters or the view alone, and show the new set."
        raise Validation(message, hint=hint)
