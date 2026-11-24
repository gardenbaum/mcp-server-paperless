"""The Paperless trash: what the port returns and what the trash tools answer.

Deleting a document moves it to the trash, where it stays until the trash is emptied (Paperless
does so by itself after 30 days by default). Deleting a version of a document puts that version's
file there too, as an entry of its own. Restoring puts the document back, or re-attaches the
version to its document; emptying removes the entries for good.
"""

from datetime import UTC, date, datetime
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from mcp_server_paperless.domain.documents import (
    DocumentFacts,
    DocumentOverview,
    DocumentRecord,
    DocumentVersion,
    NameOrId,
    Note,
    Ref,
)
from mcp_server_paperless.domain.duplicates import DuplicateRecord  # noqa: TC001 -- a pydantic field

# Most entries one call names: bounded, so the confirmation text and the lookups stay small.
MAX_IDS = 100

type TrashKind = Literal["document", "version"]
type PositiveId = Annotated[int, Field(ge=1)]


# The fields of a filter: the parameters of list_trash and those of TrashFilter.
type TitlePart = Annotated[
    Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None,
    Field(description="Part of the title, ignoring case."),
]
type OfType = Annotated[
    NameOrId | None,
    Field(description="Document type by name or id (of a version: its document's)."),
]
type OfKind = Annotated[
    TrashKind | None, Field(description="document, or version: a deleted version.")
]
type DeletedFrom = Annotated[date | None, Field(description="Deleted on this day (UTC) or later.")]
type DeletedTo = Annotated[date | None, Field(description="Deleted on this day (UTC) or earlier.")]
type OfIds = Annotated[
    tuple[PositiveId, ...] | None,
    Field(min_length=1, max_length=MAX_IDS, description="Only these entry ids."),
]


class TrashFilter(BaseModel):
    """Which entries of the trash; everything given has to match."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    title: TitlePart = None
    document_type: OfType = None
    kind: OfKind = None
    deleted_from: DeletedFrom = None
    deleted_to: DeletedTo = None
    ids: OfIds = None

    def __bool__(self) -> bool:
        """Whether anything is asked for."""
        return bool(self.model_dump(exclude_none=True))


class TrashItem(BaseModel):
    """What names an entry of the trash: a deleted document, or a version deleted from one."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: Annotated[
        int, Field(description="Pass it to restore_from_trash or empty_trash (a version's id).")
    ]
    title: str
    version_of: Annotated[
        int | None,
        Field(
            description=(
                "Set when the entry is a version (a file) deleted from the document with this id; "
                "restoring it re-attaches it to that document. Null for a deleted document."
            )
        ),
    ]


class TrashedDocument(TrashItem):
    """An entry of the trash as the port lists it, with ids.

    ``checksum`` is the SHA-256 of a deleted document's original upload, None for a version.
    """

    deleted_at: datetime  # in whatever zone Paperless answers; see deleted_on
    created: date
    mime_type: str
    document_type: int | None
    owner: int | None
    has_archive: bool
    checksum: str | None = None

    @property
    def deleted_on(self) -> date:
        """The UTC day of the deletion."""
        return self.deleted_at.astimezone(UTC).date()


class TrashEntry(TrashItem):
    """An entry of the trash in a list."""

    deleted_at: datetime
    created: date
    mime_type: str
    document_type: Ref | None
    owner: Annotated[
        Ref | None, Field(description="Null: no owner. Of a version: the owner at its upload.")
    ]
    checksum: Annotated[
        str | None,
        Field(description="SHA-256 of a deleted document's original upload; null for a version."),
    ] = None


class TrashedRecord(DocumentFacts):
    """A document in the trash as the port reads it.

    Paperless does not tell its notes, custom field values and later versions: they are in the
    trash with it. ``checksum`` is the SHA-256 of the original upload.
    """

    checksum: str | None


class TrashedNote(Note):
    """A note on a document in the trash."""

    format: str | None


class TrashedDetail(DocumentRecord):
    """Everything the API tells of an entry, with ids; ``content`` is the row's own text."""

    deleted_at: datetime
    version_of: int | None
    created_date: date
    archived_file_name: str | None
    is_shared_by_requester: bool
    notes: tuple[TrashedNote, ...]
    duplicate_documents: tuple[DuplicateRecord, ...]
    content: str = Field(repr=False)


class TrashLookup(BaseModel):
    """What one page of the trash says about one entry: the entry, and the next page if any."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    entry: TrashedDetail | None
    next_page: int | None


class TrashPage(BaseModel):
    """One page of the trash as the port reads it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: int | None
    entries: tuple[TrashedDocument, ...]


class TrashList(BaseModel):
    """One page of the trash."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    web_url: Annotated[
        str,
        Field(description="Link to the trash page of the Paperless web UI (entries have none)."),
    ]

    count: Annotated[
        int,
        Field(description="How many entries the trash holds, or with a filter: how many match."),
    ]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    entries: tuple[TrashEntry, ...]
    scanned: Annotated[
        int | None,
        Field(description="With a filter other than ids: how many entries were looked at."),
    ] = None
    truncated: Annotated[
        bool, Field(description="With a filter: not every entry was looked at; more may match.")
    ] = False
    notice: Annotated[
        str | None, Field(description="What the filter left out and how to reach it.")
    ] = None


class TrashText(BaseModel):
    """A slice of the text of an entry."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    offset: Annotated[int, Field(description="Where the slice starts, in characters.")]
    content: str
    total_chars: Annotated[int, Field(description="Length of the whole text.")]
    next_offset: Annotated[
        int | None, Field(description="Offset of the next slice, null at the end of the text.")
    ]


class TrashGrants(BaseModel):
    """Who may view and who may change an entry, besides its owner."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    view_users: tuple[Ref, ...]
    view_groups: tuple[Ref, ...]
    change_users: tuple[Ref, ...]
    change_groups: tuple[Ref, ...]


class TrashEntryDetail(DocumentOverview):
    """Everything Paperless tells of an entry of the trash."""

    web_url: Annotated[str, Field(description="Link to the trash page of the web UI.")]
    deleted_at: datetime
    version_of: Annotated[
        int | None, Field(description="The document a deleted version belongs to; else null.")
    ]
    created_date: Annotated[date, Field(description="Deprecated copy of created.")]
    archived_file_name: str | None
    is_shared_by_requester: Annotated[bool | None, Field(description="Null for a version.")]
    user_can_change: Annotated[
        bool | None, Field(description="Of a version: its document's. Null: see limits.")
    ]
    permissions: Annotated[
        TrashGrants | None,
        Field(description="Of a version: its document's. Null if not readable: see limits."),
    ]
    notes: tuple[TrashedNote, ...]
    versions: Annotated[
        tuple[DocumentVersion, ...],
        Field(description="Versions outside the trash, the original upload included."),
    ]
    duplicate_documents: Annotated[
        tuple[DuplicateRecord, ...],
        Field(description="Paperless does not fill this in the trash, so empty proves nothing."),
    ]
    text: Annotated[TrashText | None, Field(description="Only with text=true.")] = None
    limits: Annotated[str, Field(description="What Paperless does not show of it in the trash.")]


type Outcome = Literal["restored", "deleted", "still_in_trash", "unverified"]


class EntryOutcome(TrashItem):
    """An entry that was acted on, and what the trash showed afterwards."""

    outcome: Annotated[
        Outcome,
        Field(description="unverified: what happened to it could not be read afterwards."),
    ]


class RestoredEntry(EntryOutcome):
    """An entry that was to be restored, with the link to its document."""

    web_url: Annotated[
        str,
        Field(
            description="Link to the document in the Paperless web UI (a version's: its document)."
        ),
    ]


class TrashRestored(BaseModel):
    """What restoring did to each entry."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    entries: tuple[RestoredEntry, ...]
    message: Annotated[str, Field(description="What happened to documents and versions.")]


class TrashEmptied(BaseModel):
    """What deleting entries for good did to each of them."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_ids: Annotated[tuple[int, ...], Field(description="The entries that are gone.")]
    count: Annotated[int, Field(description="How many entries are gone.")]
    entries: tuple[EntryOutcome, ...]
    message: str
