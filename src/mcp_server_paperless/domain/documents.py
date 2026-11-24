"""Documents: the search query, the rows the port returns and the summaries tools return.

The port speaks ids (``DocumentQuery``, ``DocumentHit``); services resolve names into the query
and enrich the hits with names into ``DocumentSummary``. No row ever holds the OCR content.
"""

from datetime import date, datetime
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from mcp_server_paperless.domain.duplicates import DuplicateCheck
from mcp_server_paperless.domain.tasks import TaskState, Wait

# The most ``PAPERLESS_MCP_MAX_UPLOAD_BYTES`` may be set to (100 MiB); settings and the refusal
# hint both read it.
MAX_UPLOAD_LIMIT: Final = 100 * 1024 * 1024

type Include = Literal["notes", "history", "metadata", "versions", "suggestions", "duplicates"]
# same_file: the original uploads are identical; archive: only an archive file is.
type DuplicateKind = Literal["same_file", "archive"]
type Ordering = Literal[
    "created",
    "-created",
    "added",
    "-added",
    "modified",
    "-modified",
    "title",
    "-title",
    "archive_serial_number",
    "-archive_serial_number",
]


class DocumentQuery(BaseModel):
    """Filters of a document search, every name already resolved to an id; all are ANDed.

    The filters from ``title_contains`` to ``owner`` and ``inbox`` are the ones the search tool
    offers. The rest exist for saved views (``run_saved_view`` translates the filter rules of a
    view into them) and are named after the Paperless parameter they become.

    Attributes:
        query: Full-text query in Paperless's Tantivy syntax, passed through unchanged.
        more_like: Id of a document to find similar ones to; never together with ``query``.
        text: Simple full-text search over title and content; alone or with ``query``.
        title_search: Simple full-text search over the title; alone or with ``query``.
        custom_field_query: Paperless's ``custom_field_query`` expression, passed through.
        ids: Only these documents (those of them the user may see); empty means no restriction.
        shared_by: Documents this user owns and has shared with someone.
        checksum: The SHA-256 of the original upload (not of later versions).
        ordering: A Paperless ordering field, ``-`` for descending.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    query: str | None = None
    checksum: str | None = None
    more_like: int | None = None
    text: str | None = None
    title_search: str | None = None
    title_contains: str | None = None
    content_contains: str | None = None
    correspondent: int | None = None
    correspondent_isnull: bool | None = None
    correspondent_in: tuple[int, ...] = ()
    correspondent_none: tuple[int, ...] = ()
    document_type: int | None = None
    document_type_isnull: bool | None = None
    document_type_in: tuple[int, ...] = ()
    document_type_none: tuple[int, ...] = ()
    storage_path: int | None = None
    storage_path_isnull: bool | None = None
    storage_path_in: tuple[int, ...] = ()
    storage_path_none: tuple[int, ...] = ()
    tags_all: tuple[int, ...] = ()
    tags_any: tuple[int, ...] = ()
    tags_none: tuple[int, ...] = ()
    tagged: bool | None = None
    archive_serial_number: int | None = None
    asn_gt: int | None = None
    asn_lt: int | None = None
    asn_isnull: bool | None = None
    created_from: date | None = None
    created_to: date | None = None
    created_gt: date | None = None
    created_lt: date | None = None
    created_year: int | None = None
    created_month: int | None = None
    created_day: int | None = None
    added_from: date | None = None
    added_to: date | None = None
    added_gt: date | None = None
    added_lt: date | None = None
    modified_gt: date | None = None
    modified_lt: date | None = None
    owner: int | None = None
    owner_in: tuple[int, ...] = ()
    owner_none: tuple[int, ...] = ()
    owner_isnull: bool | None = None
    shared_by: int | None = None
    inbox: bool | None = None
    mime_type: str | None = None
    has_duplicates: bool | None = None
    custom_fields_icontains: str | None = None
    custom_fields_all: tuple[int, ...] = ()
    custom_fields_in: tuple[int, ...] = ()
    custom_fields_none: tuple[int, ...] = ()
    has_custom_fields: bool | None = None
    custom_field_query: JsonValue = None
    ordering: str | None = None
    ids: tuple[int, ...] = ()


type NameOrId = str | int
type FileVersion = Literal["original", "archive"]
type FileKind = Literal["thumbnail", "original", "archive"]


class NameFilters(BaseModel):
    """The filters of a search that callers may give by name; services resolve them to ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    correspondent: NameOrId | None = None
    document_type: NameOrId | None = None
    storage_path: NameOrId | None = None
    tags_all: tuple[NameOrId, ...] = ()
    tags_any: tuple[NameOrId, ...] = ()
    tags_none: tuple[NameOrId, ...] = ()
    owner: NameOrId | None = None


class CustomFieldValue(BaseModel):
    """A custom field's value on a document, as Paperless stores it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    field: int
    value: JsonValue


class DocumentPatch(BaseModel):
    """What a document update changes, ids only; the port sends exactly the fields that are set.

    A field that was never given is left alone, one given as ``None`` is cleared (correspondent,
    document type, storage path, archive serial number, owner). ``tags`` and ``custom_fields``
    are the whole new lists: Paperless replaces them as a whole, so whoever builds the patch
    merges them with the current state first.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    title: str | None = None
    created: date | None = None
    archive_serial_number: int | None = None
    owner: int | None = None
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    tags: tuple[int, ...] | None = None
    custom_fields: tuple[CustomFieldValue, ...] | None = None

    @model_validator(mode="after")
    def _required_values(self) -> DocumentPatch:
        """Title, created date and the two lists cannot be cleared, only changed."""
        for name in ("title", "created", "tags", "custom_fields"):
            if name in self.model_fields_set and getattr(self, name) is None:
                message = f"{name} cannot be cleared"
                raise ValueError(message)
        return self


class DocumentUpload(BaseModel):
    """A file with the metadata Paperless should give it, ids only; the port sends it as is.

    Paperless applies the metadata when it consumes the file, so a field left out is decided by
    its own rules (matching, workflows, the file name as the title).

    Attributes:
        filename: The name the file is uploaded under; Paperless keeps it as the original name.
        mime_type: What the content is; the services have sniffed it, Paperless does so again.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    filename: str
    mime_type: str
    content: bytes = Field(repr=False)
    title: str | None = None
    created: date | None = None
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    tags: tuple[int, ...] = ()
    archive_serial_number: int | None = None
    custom_fields: tuple[CustomFieldValue, ...] = ()


class UploadMetadata(BaseModel):
    """What a caller wants a new document to have, with names where Paperless wants ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    title: str | None = None
    created: date | None = None
    archive_serial_number: int | None = None
    correspondent: NameOrId | None = None
    document_type: NameOrId | None = None
    storage_path: NameOrId | None = None
    tags: tuple[NameOrId, ...] = ()
    custom_fields: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


type Clearable = Literal[
    "correspondent", "document_type", "storage_path", "archive_serial_number", "owner"
]


class DocumentEdit(BaseModel):
    """What a caller wants changed on a document, with names where Paperless wants ids.

    A field left out stays as it is. ``clear`` empties the fields that can be empty. Tags are
    added, removed or replaced as a whole (``tags_set``, not together with the others); custom
    fields are set by name or id and taken off with ``remove_custom_fields``; whatever is not
    named stays. Services resolve the names and merge, see ``DocumentEditor``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    title: str | None = None
    created: date | None = None
    archive_serial_number: int | None = None
    owner: NameOrId | None = None
    correspondent: NameOrId | None = None
    document_type: NameOrId | None = None
    storage_path: NameOrId | None = None
    clear: tuple[Clearable, ...] = ()
    tags_add: tuple[NameOrId, ...] = ()
    tags_remove: tuple[NameOrId, ...] = ()
    tags_set: tuple[NameOrId, ...] | None = None
    create_missing_tags: bool = False
    confirm: bool = False
    custom_fields: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    remove_custom_fields: tuple[NameOrId, ...] = ()


class DocumentHit(BaseModel):
    """One document of a search result, with ids where Paperless has them."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    created: date
    added: datetime
    correspondent: int | None
    document_type: int | None
    storage_path: int | None
    tags: tuple[int, ...]
    custom_fields: tuple[CustomFieldValue, ...]
    highlights: str | None = None
    user_can_change: bool | None = None


class DocumentPage(BaseModel):
    """One page of search hits."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    next_page: int | None
    hits: tuple[DocumentHit, ...]


class Ref(BaseModel):
    """An object a document refers to; name is null if the user may not see it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str | None


class FieldValue(BaseModel):
    """A custom field's value, readable: select labels, "EUR 12.50", document ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str | None
    value: JsonValue


class DocumentSummary(BaseModel):
    """A document as a compact search row, without its content."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    created: date
    added: datetime
    correspondent: Ref | None
    document_type: Ref | None
    storage_path: Ref | None
    tags: tuple[Ref, ...]
    custom_fields: tuple[FieldValue, ...]
    highlights: Annotated[
        str | None,
        Field(description="Full-text search only: matching excerpt, terms marked with <b>."),
    ] = None
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class DocumentSearchResult(BaseModel):
    """One page of a document search."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: Annotated[int, Field(description="How many documents match in total.")]
    page: int
    next_page: Annotated[int | None, Field(description="The next page, null on the last one.")]
    documents: tuple[DocumentSummary, ...]
    notices: Annotated[
        tuple[str, ...],
        Field(
            description=(
                "Query syntax the search index reads as plain text, with the working alternative; "
                "empty for a clean query. Paperless itself reports no error for these."
            )
        ),
    ] = ()


class Note(BaseModel):
    """A note on a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    note: str
    created: datetime | None
    user: Annotated[str | None, Field(description="Username of the author, if known.")]


class DocumentVersion(BaseModel):
    """One file version of a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    added: datetime
    version_label: str | None
    is_root: Annotated[bool, Field(description="True for the original upload.")]
    checksum: Annotated[
        str | None,
        Field(description="SHA-256 of the file; two versions with the same checksum are copies."),
    ] = None


class HistoryEntry(BaseModel):
    """One change of a document in its audit log; the OCR text is never part of it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    timestamp: datetime
    action: str
    actor: Annotated[str | None, Field(description="Username; null for system changes.")]
    changes: Annotated[
        dict[str, JsonValue],
        Field(description="Changed attribute to its old and new value, or to the m2m change."),
    ]


class FileMetadata(BaseModel):
    """What Paperless knows about the files of a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    original_filename: str | None
    original_mime_type: str
    original_size: Annotated[
        int | None, Field(description="Bytes; null: Paperless cannot find the file.")
    ]
    original_checksum: str
    media_filename: str | None
    has_archive_version: bool
    archive_size: Annotated[
        int | None,
        Field(description="Bytes; null without archive version or if its file is missing."),
    ]
    archive_checksum: str | None
    archive_media_filename: str | None

    def sent_for(self, version: FileVersion) -> FileVersion:
        """The file Paperless sends for ``version``: the original when there is no archive."""
        return version if self.has_archive_version else "original"

    def lacks(self, version: FileVersion) -> bool:
        """Whether Paperless cannot find the file it would send for ``version``."""
        archive = self.sent_for(version) == "archive"
        return (self.archive_size if archive else self.original_size) is None


class Suggestions(BaseModel):
    """The classifier's suggestions for a document, as ids (dates as Paperless sends them)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    correspondents: tuple[int, ...]
    tags: tuple[int, ...]
    document_types: tuple[int, ...]
    storage_paths: tuple[int, ...]
    dates: tuple[str, ...]


class DocumentFacts(BaseModel):
    """What Paperless tells of a document, live or in the trash, with ids where it has them."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    created: date
    added: datetime
    modified: datetime
    archive_serial_number: int | None
    owner: int | None
    correspondent: int | None
    document_type: int | None
    storage_path: int | None
    tags: tuple[int, ...]
    original_file_name: str | None
    mime_type: str


class DocumentRecord(DocumentFacts):
    """One document with ids where Paperless has them; no content, notes only counted."""

    custom_fields: tuple[CustomFieldValue, ...]
    page_count: int | None
    notes_count: int
    versions: tuple[DocumentVersion, ...]
    user_can_change: Annotated[
        bool | None,
        Field(
            description=(
                "Whether object permissions let the user change the document; null where "
                "Paperless does not say (the answer to an update)."
            )
        ),
    ]

    @property
    def newest_file(self) -> tuple[int, int | None]:
        """The document and the version it shows: what a confirmation of a file operation binds."""
        return self.id, self.versions[0].id if self.versions else None


class DocumentText(BaseModel):
    """The whole OCR text of one version of a document, and what a change of it needs to know."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    version_id: int
    versions: tuple[DocumentVersion, ...]
    user_can_change: bool
    content: str = Field(repr=False)


class SuggestionNames(BaseModel):
    """Suggestions with names; a name is null if the user may not see that object."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    correspondents: tuple[Ref, ...]
    tags: tuple[Ref, ...]
    document_types: tuple[Ref, ...]
    storage_paths: tuple[Ref, ...]
    dates: tuple[str, ...]


class DocumentHistory(BaseModel):
    """The newest changes of a document, newest first."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    total: Annotated[int, Field(description="How many changes the document has in total.")]
    truncated: Annotated[bool, Field(description="Older changes exist beyond the entries.")]
    entries: tuple[HistoryEntry, ...]


class DuplicateDocument(BaseModel):
    """A document Paperless takes for a duplicate; what could not be read is null."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    in_trash: bool
    deleted_at: datetime | None = None
    match: Annotated[
        DuplicateKind | None,
        Field(
            description=(
                "same_file: identical original uploads; archive: only an archive file (the PDF "
                "Paperless made) is; null: unknown."
            )
        ),
    ] = None
    differs: Annotated[
        tuple[str, ...], Field(description="Known fields that differ from the document's.")
    ] = ()
    created: date | None = None
    added: datetime | None = None
    owner: Ref | None = None
    correspondent: Ref | None = None
    document_type: Ref | None = None
    storage_path: Ref | None = None
    tags: tuple[Ref, ...] | None = None
    archive_serial_number: int | None = None
    original_file_name: str | None = None
    mime_type: str | None = None
    page_count: int | None = None
    notes_count: int | None = None
    custom_fields: tuple[FieldValue, ...] | None = None
    version_count: int | None = None
    user_can_change: bool | None = None
    web_url: Annotated[str | None, Field(description="Null in the trash.")] = None


class DuplicateList(BaseModel):
    """Duplicates, those in the trash last."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    total: int
    truncated: Annotated[bool, Field(description="More duplicates exist than are listed.")]
    documents: tuple[DuplicateDocument, ...]
    not_compared: tuple[str, ...]


class DocumentDetail(BaseModel):
    """A document with names, readable custom fields and what ``include`` asked for."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    created: date
    added: datetime
    modified: datetime
    archive_serial_number: int | None
    owner: Annotated[Ref | None, Field(description="Null: no owner, visible to every user.")]
    correspondent: Ref | None
    document_type: Ref | None
    storage_path: Ref | None
    tags: tuple[Ref, ...]
    custom_fields: tuple[FieldValue, ...]
    original_file_name: str | None
    mime_type: str
    page_count: int | None
    notes_count: int
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]
    notes: Annotated[tuple[Note, ...] | None, Field(description="Only with include notes.")] = None
    history: Annotated[DocumentHistory | None, Field(description="Only with include history.")] = (
        None
    )
    file_metadata: Annotated[
        FileMetadata | None, Field(description="Only with include metadata.")
    ] = None
    versions: Annotated[
        tuple[DocumentVersion, ...] | None, Field(description="Only with include versions.")
    ] = None
    suggestions: Annotated[
        SuggestionNames | None, Field(description="Only with include suggestions.")
    ] = None
    unavailable: Annotated[
        tuple[Include, ...],
        Field(description="Included sections the user may not read in Paperless."),
    ] = ()


class DocumentRead(DocumentDetail):
    """A document as it is read, with its duplicates if ``include`` asked for them."""

    duplicates: Annotated[
        DuplicateList | None, Field(description="Only with include duplicates.")
    ] = None


class DocumentUpdate(DocumentDetail):
    """The document after an update, and what the update added on its own."""

    implicit_tags: Annotated[
        tuple[Ref, ...],
        Field(
            description=(
                "Parent tags added because a child tag was added or kept: Paperless tags are "
                "hierarchical and a child needs its parent."
            )
        ),
    ] = ()


class NoteAdded(BaseModel):
    """The note that was added to a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    note: Note
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class NoteDeleted(BaseModel):
    """A note that was deleted for good."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    note_id: int
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class DocumentDeleted(BaseModel):
    """A document that was moved to the Paperless trash."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    title: Annotated[str, Field(description="The title the document had.")]
    in_trash: Annotated[
        bool, Field(description="True: the document is in the trash and can be restored.")
    ]
    message: Annotated[str, Field(description="What happened and how to get the document back.")]


class ContentSlice(BaseModel):
    """A slice of the OCR text of a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    offset: Annotated[int, Field(description="Where the slice starts, in characters.")]
    content: str
    total_chars: Annotated[int, Field(description="Length of the whole text.")]
    next_offset: Annotated[
        int | None, Field(description="Offset of the next slice, null at the end of the text.")
    ]
    version_id: Annotated[int, Field(description="The version this text belongs to.")]
    text_state: Annotated[str, Field(description="Fingerprint of the whole text of this version.")]
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class DocumentFile(BaseModel):
    """A file of a document as Paperless sends it: the bytes, their MIME type and file name.

    Attributes:
        content: The file itself.
        mime_type: The ``Content-Type`` Paperless sent, parameters included
            (``text/plain; charset=utf-8``).
        filename: The name Paperless proposes (RFC 6266), non-ASCII characters intact and never
            holding a path, control or bidi characters; None if it proposes none.
        truncated: The file is larger than the limit the caller gave; ``content`` is then empty.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    content: bytes
    mime_type: str
    filename: str | None
    truncated: bool = False


class FileReport(BaseModel):
    """What a tool says about a thumbnail or download, whether or not the bytes came with it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: Annotated[int, Field(description="The document the file belongs to.")]
    kind: Annotated[FileKind, Field(description="Which file: the thumbnail, original or archive.")]
    filename: Annotated[
        str | None,
        Field(
            description=(
                "File name, non-ASCII characters intact. An attached file has the name Paperless "
                "proposes for a download (date, correspondent, title); a file that is not attached "
                "has the name of the upload (the archive version with .pdf), or null."
            )
        ),
    ]
    mime_type: Annotated[
        str, Field(description="MIME type as Paperless sends it, charset parameter included.")
    ]
    size_bytes: Annotated[
        int | None, Field(description="Size of the file in bytes; null if it was not measured.")
    ]
    delivered: Annotated[
        bool, Field(description="The file is attached as content; false: it is over the limit.")
    ]
    max_bytes: Annotated[
        int | None, Field(description="The server's download limit; null for thumbnails.")
    ]
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]
    note: Annotated[str | None, Field(description="Why the file is not attached, if it is not.")]
    version_id: Annotated[
        int | None, Field(description="The version that was asked for; null: the newest.")
    ] = None


class FileDelivery(BaseModel):
    """A report and, unless the file is over the size limit, the bytes it describes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    report: FileReport
    data: bytes | None


class Thumbnail(BaseModel):
    """A report and the image it describes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    report: FileReport
    data: bytes


type UploadState = TaskState | Literal["unknown"]


class UploadResult(BaseModel):
    """What an upload did: the file Paperless accepted and, if it was waited for, its outcome."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: Annotated[str, Field(description="Follow it with get_task; Paperless consumes later.")]
    state: Annotated[
        UploadState,
        Field(
            description=(
                "pending, started, success, failure or revoked; unknown when the task could not "
                "be read after the upload (e.g. this user may not view tasks): the upload was "
                "accepted all the same."
            )
        ),
    ]
    message: Annotated[
        str | None, Field(description="Why consumption failed, or why the state is unknown.")
    ]
    document_id: Annotated[
        int | None, Field(description="The new document once consumption succeeded; else null.")
    ]
    web_url: Annotated[str | None, Field(description="Link to the new document, once known.")]
    wait: Annotated[
        Wait,
        Field(
            description=(
                "not_requested: wait_seconds was 0; finished: consumption ended within the wait "
                "(see state); timed_out: still running, ask get_task later."
            )
        ),
    ]
    waited_seconds: float
    filename: Annotated[str, Field(description="The name the file was uploaded under.")]
    mime_type: Annotated[str, Field(description="The type found in the content.")]
    size_bytes: int
    duplicates: Annotated[
        DuplicateCheck, Field(description="Where Paperless already holds the same file.")
    ]
