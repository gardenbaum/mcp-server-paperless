"""Many documents changed with values of their own, or given notes, in one call (decision-73).

Output models are tool output schemas: a one-line class docstring each, and field descriptions
only where the name does not say enough.
"""

from datetime import date
from typing import Annotated, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from mcp_server_paperless.domain.documents import Clearable, DocumentEdit, NameOrId, Ref
from mcp_server_paperless.domain.selection import CALENDAR, NO_FLAG

type Named = Annotated[NameOrId, NO_FLAG]


class DocumentValues(BaseModel):
    """What changes on one document, as in update_document; what is left out stays."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    document_id: Annotated[int, NO_FLAG]
    title: str | None = None
    correspondent: Named | None = None
    document_type: Named | None = None
    storage_path: Named | None = None
    created: Annotated[date, CALENDAR] | None = None
    archive_serial_number: Annotated[int, NO_FLAG, Field(ge=0)] | None = None
    owner: Named | None = None
    clear: Annotated[
        tuple[Clearable, ...] | None,
        Field(description="Fields to empty; no owner makes the document visible to every user."),
    ] = None
    tags_add: tuple[Named, ...] | None = None
    tags_remove: tuple[Named, ...] | None = None
    tags_set: Annotated[
        tuple[Named, ...] | None, Field(description="Replace all tags with exactly these.")
    ] = None
    custom_fields: Annotated[
        dict[str, JsonValue] | None,
        Field(
            description=(
                "Values to set by field name or id, others stay: text; url with scheme; date "
                "'YYYY-MM-DD'; true/false; numbers; 'EUR12.50'; a list of document ids for links; "
                "a select option's label."
            )
        ),
    ] = None
    remove_custom_fields: tuple[Named, ...] | None = None

    def edit(self, *, create_missing_tags: bool) -> DocumentEdit:
        """The same as a single update is asked for."""
        named = self.model_fields_set - {"document_id"}
        given = {name: cast("object", getattr(self, name)) for name in named}
        given = {name: value for name, value in given.items() if value is not None}
        return DocumentEdit.model_validate(given | {"create_missing_tags": create_missing_tags})


type UpdateStatus = Literal[
    "would_change",
    "changed",
    "unchanged",
    "changed_meanwhile",
    "failed",
    "unknown",
    "not_started",
]


class ValueChange(BaseModel):
    """A field with its value before and after; names where Paperless has ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    field: str
    old: JsonValue
    new: JsonValue


class UpdatedDocument(BaseModel):
    """What became of one document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: Annotated[str, Field(description="As it was before.")]
    status: Annotated[
        UpdateStatus,
        Field(
            description=(
                "changed_meanwhile: someone else changed a value this call touches, so nothing was "
                "written; unknown: no usable answer, and the values were not seen afterwards."
            )
        ),
    ]
    why: str | None = None
    changes: tuple[ValueChange, ...] = ()
    implicit_tags: Annotated[
        tuple[Ref, ...], Field(description="Parent tags that came along with a child tag.")
    ] = ()
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class UpdateReport(BaseModel):
    """Many documents with values of their own: the preview of a dry run, or what became of each."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    dry_run: Annotated[bool, Field(description="true: a preview, nothing was changed.")]
    counts: Annotated[dict[UpdateStatus, int], Field(description="Documents per status.")]
    documents: Annotated[
        tuple[UpdatedDocument, ...], Field(description="Every document, in the order given.")
    ]
    also_changed: Annotated[
        tuple[Ref, ...],
        Field(
            description=(
                "Other documents a mirrored document link changes (a preview) or was sent to."
            )
        ),
    ] = ()
    created_tags: Annotated[
        tuple[str, ...], Field(description="Tags created, or that would be.")
    ] = ()
    left: Annotated[
        tuple[int, ...],
        Field(description="Not as asked: pass these items again, they are checked anew."),
    ] = ()
    truncated: Annotated[
        bool,
        Field(
            description="Values, changes or other documents are left out: the answer was too large."
        ),
    ] = False
    message: str


class DocumentNote(BaseModel):
    """A note for one document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    document_id: Annotated[int, NO_FLAG]
    note: str


type NoteStatus = Literal[
    "would_add", "blocked", "added", "already_there", "failed", "unknown", "not_started"
]


class NotedDocument(BaseModel):
    """What became of one note."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    title: str
    status: Annotated[
        NoteStatus,
        Field(
            description=(
                "already_there: a note with this text is on the document, so none was added; "
                "unknown: no usable answer, and the note was not seen afterwards."
            )
        ),
    ]
    note_id: Annotated[int | None, Field(description="The note that was added or is there.")] = None
    why: str | None = None
    web_url: Annotated[str, Field(description="Link to the document in the Paperless web UI.")]


class NotesReport(BaseModel):
    """Notes on many documents: the preview of a dry run, or what became of each."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    dry_run: Annotated[bool, Field(description="true: a preview, nothing was added.")]
    matched: Annotated[
        int,
        Field(description="How many documents the ids, filters or view match, or notes name."),
    ]
    selected_ids: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "The documents that get a note (a preview) or were chosen to in this call; with "
                "the same filters or saved_view they are the document_ids of the next call."
            )
        ),
    ]
    counts: Annotated[dict[NoteStatus, int], Field(description="Notes per status.")]
    documents: Annotated[
        tuple[NotedDocument, ...],
        Field(
            description=(
                "Every note of this call; what a filter or view alone matches and has it already "
                "is only counted."
            )
        ),
    ]
    remaining: Annotated[int, Field(description="Matching documents that still lack the note.")] = 0
    also_matching: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "With document_ids and filters or saved_view: matching documents that were not "
                "named and are not touched."
            )
        ),
    ] = ()
    left: Annotated[
        tuple[int, ...], Field(description="Without the note, or not known to have it.")
    ] = ()
    message: str
