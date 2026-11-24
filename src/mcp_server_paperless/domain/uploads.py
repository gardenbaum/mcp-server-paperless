"""Several files uploaded in one call: one report per file and what is left (decision-50)."""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.documents import UploadState
from mcp_server_paperless.domain.duplicates import DuplicateCheck

type BatchStatus = Literal["uploaded", "would_upload", "duplicate", "refused", "failed"]

# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class BatchFile(BaseModel):
    """What became of one file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    path: Annotated[
        str,
        Field(
            description=(
                "With characters nobody sees escaped and a backslash doubled; paths and "
                "directory take it so, upload_document takes the plain path."
            )
        ),
    ]
    status: Annotated[
        BatchStatus,
        Field(
            description=(
                "uploaded: Paperless accepted it and consumes it later (see state); would_upload: "
                "dry_run; duplicate: not sent, or rejected, because Paperless has it or this call "
                "sent it; refused: not a file this server uploads; failed: see reason."
            )
        ),
    ]
    reason: str | None = None
    size_bytes: int | None = None
    mime_type: str | None = None
    task_id: Annotated[str | None, Field(description="Follow it with get_task.")] = None
    state: Annotated[
        UploadState | None, Field(description="Of the task; unknown if it could not be read.")
    ] = None
    document_id: int | None = None
    web_url: str | None = None
    duplicates: Annotated[
        DuplicateCheck | None,
        Field(description="Where Paperless holds the file; not_checked is given for the call."),
    ] = None


class UploadBatch(BaseModel):
    """What a call that uploads several files did."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    dry_run: bool
    files: tuple[BatchFile, ...]
    counts: Annotated[dict[BatchStatus, int], Field(description="How many files per status.")]
    left_out: Annotated[
        tuple[str, ...], Field(description="Files this call did not start; at most 25 named.")
    ]
    left_out_count: int
    directories: Annotated[
        tuple[str, ...], Field(description="Subdirectories not read; at most 25 named.")
    ]
    next_after: Annotated[
        str | None,
        Field(description="Pass unchanged as after to go on with the directory; null: done."),
    ]
    notes: Annotated[tuple[str, ...], Field(description="What was left out and how to go on.")]
    not_checked: Annotated[
        tuple[str, ...], Field(description="Where a duplicate would not have been noticed.")
    ]
