"""Resolving duplicates: what the tool takes and answers."""

from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.documents import DocumentVersion
from mcp_server_paperless.domain.duplicates import DuplicateMatch

# Most duplicates one call resolves: bounded, so the confirmation text and the reads stay small.
MAX_DUPLICATES: Final = 20

type ResolveAction = Literal["trash", "merge_as_versions"]


# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class NamedDocument(BaseModel):
    """A document by its id and the title it has or had."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str


class UnresolvedDuplicate(NamedDocument):
    """A duplicate that is not resolved, or not known to be."""

    why: str


class DuplicateResolution(BaseModel):
    """What was done with the duplicates of a document, or with dry_run what would be."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    action: ResolveAction
    dry_run: Annotated[bool, Field(description="true: a preview, nothing was changed.")]
    applied: Annotated[bool, Field(description="Paperless accepted the change.")]
    kept: NamedDocument
    kept_seen: Annotated[
        bool | None,
        Field(description="false: the kept document was gone afterwards; null: not read."),
    ]
    resolved: Annotated[
        tuple[NamedDocument, ...],
        Field(
            description=(
                "Seen in the trash, or as a version of the kept document; with dry_run those that "
                "would be (none if it would be refused)."
            )
        ),
    ]
    not_resolved: tuple[UnresolvedDuplicate, ...]
    other_duplicates: Annotated[
        tuple[DuplicateMatch, ...],
        Field(description="Duplicates of the kept document that stay; at most 10."),
    ]
    other_duplicates_count: int
    versions: Annotated[
        tuple[DocumentVersion, ...] | None,
        Field(description="Of the kept document after a merge, newest first; null if not read."),
    ] = None
    unconfirmed_task_id: Annotated[
        str | None,
        Field(
            description=(
                "Set when the task Paperless queued could not be followed: it may still run. "
                "Read it with get_task before repeating the call."
            )
        ),
    ] = None
    message: str
    web_url: Annotated[str, Field(description="Link to the kept document.")]
