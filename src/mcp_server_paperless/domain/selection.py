"""Many documents at once: how a caller chooses them, and what reading them returns.

A selection is named by ids, by the filters of ``search_documents`` or by a saved view; services
resolve it to documents (``DocumentSelector``, decision-69).
"""

import re
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue

from mcp_server_paperless.domain.documents import (
    CustomFieldValue,
    DocumentQuery,
    FieldValue,
    NameFilters,
    NameOrId,
    Note,
    Ref,
)

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import Ordering

# Most documents that may match a selection, and most characters of text and of the whole answer
# of one read (owner, 2026-11-24).
MAX_MATCHES: Final = 1000
MAX_TEXT: Final = 20_000
MAX_ANSWER: Final = 100_000

DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")

type ReadField = Literal[
    "title",
    "created",
    "added",
    "modified",
    "correspondent",
    "document_type",
    "storage_path",
    "tags",
    "custom_fields",
    "archive_serial_number",
    "owner",
    "original_file_name",
    "mime_type",
    "page_count",
    "notes",
    "version_count",
    "user_can_change",
    "content",
]


def _absent(has: bool | None) -> bool | None:  # noqa: FBT001 -- a tri-state filter, not a switch
    """The Paperless ``isnull`` flag for a ``has_*`` filter: has=false means isnull=true."""
    return None if has is None else not has


def no_flag(value: object) -> object:
    """Refuse true and false, which would become the ids 1 and 0."""
    if isinstance(value, bool):
        message = "true and false are no ids"
        raise ValueError(message)  # noqa: TRY004 -- pydantic reports a ValueError as invalid input
    return value


def _calendar(value: object) -> object:
    """Refuse what pydantic would turn into a date: numbers, times and other spellings."""
    if type(value) is date or (isinstance(value, str) and DATE.fullmatch(value)):
        return value
    message = "a date is YYYY-MM-DD"
    raise ValueError(message)


NO_FLAG: Final = BeforeValidator(no_flag)
CALENDAR: Final = BeforeValidator(_calendar)


class SelectionFilters(BaseModel):
    """The filters of search_documents, with the same names and meaning; all are ANDed."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    query: str | None = None
    more_like_id: Annotated[int, NO_FLAG] | None = None
    title_contains: str | None = None
    correspondent: Annotated[NameOrId, NO_FLAG] | None = None
    document_type: Annotated[NameOrId, NO_FLAG] | None = None
    storage_path: Annotated[NameOrId, NO_FLAG] | None = None
    has_correspondent: bool | None = None
    has_document_type: bool | None = None
    has_storage_path: bool | None = None
    has_tags: bool | None = None
    tags_all: list[Annotated[NameOrId, NO_FLAG]] | None = None
    tags_any: list[Annotated[NameOrId, NO_FLAG]] | None = None
    tags_none: list[Annotated[NameOrId, NO_FLAG]] | None = None
    created_from: Annotated[date, CALENDAR] | None = None
    created_to: Annotated[date, CALENDAR] | None = None
    added_from: Annotated[date, CALENDAR] | None = None
    added_to: Annotated[date, CALENDAR] | None = None
    owner: Annotated[NameOrId, NO_FLAG] | None = None
    inbox: bool | None = None
    custom_field_query: list[JsonValue] | None = None
    has_duplicates: bool | None = None

    def parts(self, ordering: Ordering | None = None) -> tuple[NameFilters, DocumentQuery]:
        """The filters given by name, and the rest as a query."""
        names = NameFilters(
            correspondent=self.correspondent,
            document_type=self.document_type,
            storage_path=self.storage_path,
            tags_all=tuple(self.tags_all or ()),
            tags_any=tuple(self.tags_any or ()),
            tags_none=tuple(self.tags_none or ()),
            owner=self.owner,
        )
        query = DocumentQuery(
            query=self.query,
            more_like=self.more_like_id,
            title_contains=self.title_contains,
            created_from=self.created_from,
            created_to=self.created_to,
            added_from=self.added_from,
            added_to=self.added_to,
            inbox=self.inbox,
            correspondent_isnull=_absent(self.has_correspondent),
            document_type_isnull=_absent(self.has_document_type),
            storage_path_isnull=_absent(self.has_storage_path),
            tagged=self.has_tags,
            has_duplicates=self.has_duplicates,
            custom_field_query=self.custom_field_query,
            ordering=ordering,
        )
        return names, query


class Selection(BaseModel):
    """Which documents a call is about.

    Ids alone are these documents. Filters or a saved view (never both) are what they match now,
    without ``exclude_ids``; with ids too, exactly these documents and only while they all match.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_ids: tuple[int, ...] = ()
    filters: SelectionFilters | None = None
    saved_view: NameOrId | None = None
    exclude_ids: tuple[int, ...] = ()


class DocumentRow(BaseModel):
    """A document with the fields that were asked for, ids where Paperless has them."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str | None = None
    created: date | None = None
    added: datetime | None = None
    modified: datetime | None = None
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    tags: tuple[int, ...] | None = None
    custom_fields: tuple[CustomFieldValue, ...] | None = None
    archive_serial_number: int | None = None
    owner: int | None = None
    original_file_name: str | None = None
    mime_type: str | None = None
    page_count: int | None = None
    notes: tuple[Note, ...] | None = None
    version_count: int | None = None
    user_can_change: bool | None = None


def _nothing(value: object) -> bool:
    return value is None


SHOWN: Final = cast("object", Field(exclude_if=_nothing))


class ReadRow(BaseModel):
    """A document with the fields asked for; a field without a value is left out."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]
    title: Annotated[str | None, SHOWN] = None
    created: Annotated[date | None, SHOWN] = None
    added: Annotated[datetime | None, SHOWN] = None
    modified: Annotated[datetime | None, SHOWN] = None
    correspondent: Annotated[Ref | None, SHOWN] = None
    document_type: Annotated[Ref | None, SHOWN] = None
    storage_path: Annotated[Ref | None, SHOWN] = None
    tags: Annotated[tuple[Ref, ...] | None, SHOWN] = None
    custom_fields: Annotated[tuple[FieldValue, ...] | None, SHOWN] = None
    archive_serial_number: Annotated[int | None, SHOWN] = None
    owner: Annotated[Ref | None, SHOWN] = None
    original_file_name: Annotated[str | None, SHOWN] = None
    mime_type: Annotated[str | None, SHOWN] = None
    page_count: Annotated[int | None, SHOWN] = None
    notes: Annotated[tuple[Note, ...] | None, SHOWN] = None
    version_count: Annotated[int | None, SHOWN] = None
    user_can_change: Annotated[bool | None, SHOWN] = None
    content: Annotated[str | None, SHOWN] = None
    content_total: Annotated[int | None, SHOWN] = None
    content_next: Annotated[
        int | None,
        Field(
            exclude_if=_nothing,
            description="Offset of the rest of the text (get_document_content).",
        ),
    ] = None
    too_large: Annotated[
        tuple[ReadField, ...] | None,
        Field(exclude_if=_nothing, description="Fields left out: the row alone was too large."),
    ] = None


class DocumentsRead(BaseModel):
    """Many documents read at once, and what of the selection is not in the answer."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    matched: Annotated[int, Field(description="How many documents the ids or filters match.")]
    selected_ids: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "Every document of the selection (without exclude_ids), in the order of the pages; "
                "on page 1 only."
            )
        ),
    ]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    documents: tuple[ReadRow, ...]
    missing: Annotated[
        tuple[int, ...], Field(description="Ids that do not exist or are hidden from the user.")
    ] = ()
    also_matching: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "With document_ids and filters: ids that match, too, and are not shown; on page 1 "
                "only."
            )
        ),
    ] = ()
    truncated: Annotated[bool, Field(description="Rows were left out: the answer was full.")] = (
        False
    )
    left: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "Shown without the text asked for, or left out: pass them as document_ids. A text "
                "dropped for its size is in too_large, not here."
            )
        ),
    ] = ()
    failed: Annotated[
        tuple[int, ...],
        Field(description="Shown without text: it could not be read in this call, see notices."),
    ] = ()
    notices: tuple[str, ...] = ()
