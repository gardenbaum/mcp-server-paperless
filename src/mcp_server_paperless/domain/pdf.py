"""PDF operations: what the port takes and what the pdf tools return.

Paperless 3.x edits PDFs on the server. Every operation answers "OK" at once and runs as
background tasks that produce files: new documents, or new versions of the document. Where the
result goes is the ``Output`` of the operation.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.documents import NameOrId, UploadState
from mcp_server_paperless.domain.tasks import Wait

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Where the edited file goes: a new document (the original stays as it is), a new version of the
# same document (the older versions stay), or a new document while the original moves to the
# Paperless trash.
type Output = Literal["new_document", "new_version", "replace_original"]
# What Paperless itself is asked for: trashing the original is this server's last step.
type FileOutput = Literal["new_document", "new_version"]


class PageSlot(BaseModel):
    """One page of an edited file: which page of the source, turned how far, into which file.

    Attributes:
        page: The page of the source file, counted from 1.
        rotate: Degrees clockwise added to the page (a multiple of 90, negative turns back).
        document: The number of the output file the page goes to, from 0; pages keep the order
            they are listed in within their file.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    page: Annotated[int, Field(description="Page of the source file, counted from 1.")]
    rotate: Annotated[
        int,
        Field(
            description="Degrees to turn the page clockwise: 0, 90, 180 or 270 (-90 turns back)."
        ),
    ] = 0
    document: Annotated[
        int,
        Field(
            description=(
                "Number of the output file this page goes to, from 0 without gaps; pages keep "
                "the order they are listed in within their file."
            )
        ),
    ] = 0


class PartMetadata(BaseModel):
    """What a new document of a page operation gets instead of what Paperless copies.

    Attributes:
        tags: The whole list; None keeps the copied ones.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    title: Annotated[str | None, Field(description="At most 128 characters.")] = None
    document_type: Annotated[NameOrId | None, Field(description="Name or id.")] = None
    correspondent: Annotated[NameOrId | None, Field(description="Name or id.")] = None
    created: Annotated[date | None, Field(description="YYYY-MM-DD.")] = None
    tags: Annotated[
        tuple[NameOrId, ...] | None,
        Field(description="All its tags (names or ids), also without the inbox tags if not named."),
    ] = None


# Why the tasks of a request are not all known: the user may not view tasks, or reading them failed.
type LookupProblem = Literal["forbidden", "failed", "unconfirmed"]


class CopiedFiles(BaseModel):
    """How to find the files of a request whose tasks Paperless does not list.

    Attributes:
        request: The queued task of the request.
        names: The name of each output file, in their order.
        included: The documents the name of a merged file stands for, None where it was cut.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    request: str
    names: tuple[str, ...]
    included: tuple[int, ...] | None = None


class FoundCopies(BaseModel):
    """The documents, or versions, that are the files of a request.

    Attributes:
        ids: The document or version of each, None where there is none yet, or in doubt.
        doubt: Why it cannot be told that they are the request's own: then nothing is done to them.
        passing: Whether the doubt may go away when other files are done.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    ids: tuple[int | None, ...]
    doubt: str | None = None
    passing: bool = False


@dataclass(slots=True)
class RotationSearch:
    """What is looked for after a rotation, and what does not change between two looks.

    Attributes:
        request: The queued task of the rotation.
        before: The version each document showed before it, of those without a listed task.
        documents: Every document of the request.
        user: The current user, once known.
        names: The file name of each version that was read ("" if it is gone).
        actors: Who added each version, by the history.
    """

    request: str
    before: Mapping[int, int]
    documents: Sequence[int]
    user: int | None = None
    names: dict[int, str] = field(default_factory=dict[int, str])
    actors: dict[int, int | None] = field(default_factory=dict[int, int | None])


class StartedTasks(BaseModel):
    """The tasks Paperless started for a request, as far as they could be found.

    Attributes:
        ids: Their ids, oldest first, at most ``expected`` of them.
        expected: How many tasks the request starts.
        sources: The document each task belongs to, parallel to ``ids``; None where Paperless
            does not say (reprocessing). Empty when the caller does not know.
        outputs: The output file of an edit into new documents each task makes (from 0), parallel
            to ``ids``. Empty for every other operation.
        included: The documents the file of a merge names, None where that is not known (a long
            name is cut, so it does not say).
        problem: Why the lookup is incomplete beyond tasks that did not show up: the user may not
            view tasks (forbidden), reading them failed after the request was accepted (failed),
            or the queued task of Paperless could not be followed to its end (unconfirmed):
            then ``ids`` holds that task alone.
        doubt: Why it cannot be told that these tasks are the request's own, if so: good enough to
            report, not to trash anything for.
        detail: For ``unconfirmed``: why the queued task could not be followed.
        files: For new documents and rotated versions: how to find those Paperless lists no task
            for.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    ids: tuple[str, ...]
    expected: int
    sources: tuple[int | None, ...] = ()
    outputs: tuple[int, ...] = ()
    included: tuple[int, ...] | None = None
    problem: LookupProblem | None = None
    doubt: str | None = None
    detail: str | None = None
    files: CopiedFiles | None = None


# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class PdfPlan(BaseModel):
    """What an operation makes, what stays and what is lost; known before it runs."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    produces: str
    keeps: tuple[str, ...]
    loses: Annotated[
        tuple[str, ...], Field(description="What the result does not have, or no longer shows.")
    ]
    needs_confirm: bool


class PdfTask(BaseModel):
    """One Paperless task an operation started and how far it is."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    task_id: Annotated[
        str | None,
        Field(description="null: Paperless lists none; the document is known by its file name."),
    ]
    state: Annotated[
        UploadState,
        Field(
            description=(
                "pending, started, success, failure, revoked, or unknown when the task could "
                "not be read."
            )
        ),
    ]
    message: Annotated[str | None, Field(description="Why the task failed; null otherwise.")]
    document_ids: Annotated[
        tuple[int, ...],
        Field(description="The document or version the task produced, once it succeeded."),
    ]


class PartOutcome(BaseModel):
    """One new document of split_document or extract_pages and how far it is."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    part: Annotated[int, Field(description="From 1, in the order of the call.")]
    pages: str
    task_id: str | None
    state: UploadState
    document_id: Annotated[int | None, Field(description="null while it is not known.")]
    web_url: str | None
    metadata: Annotated[
        Literal["set", "not_set", "not_requested"],
        Field(description="not_set: not known to be set; see message and next."),
    ]
    message: str | None
    next: Annotated[str | None, Field(description="The calls that finish it, as a suggestion.")]


class PdfResult(BaseModel):
    """A PDF operation Paperless accepted, the tasks it started and, if waited for, the outcome."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    operation: Annotated[str, Field(description="What was asked, e.g. rotate or merge.")]
    summary: Annotated[str, Field(description="What Paperless was asked to do, in words.")]
    dry_run: Annotated[bool, Field(description="true: a preview, nothing was started.")]
    plan: PdfPlan
    source_document_ids: Annotated[
        tuple[int, ...], Field(description="The documents the operation worked on.")
    ]
    state: Annotated[
        UploadState | Literal["not_started"],
        Field(
            description=(
                "not_started: a preview. The tasks together: failure or revoked if one did, else "
                "started or pending if one still runs, else success. unknown when not all "
                "expected tasks could be found or read: Paperless accepted the request all the "
                "same (the user may not view tasks, or the work is not yet visible); see message."
            )
        ),
    ]
    message: Annotated[
        str | None,
        Field(description="Why a task failed, the state is unknown or an original stays."),
    ]
    source_links: Annotated[
        tuple[str, ...], Field(description="Links to the source documents in the Paperless web UI.")
    ]
    tasks: tuple[PdfTask, ...]
    produced_ids: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "The documents the finished tasks produced: new documents, or for a new version "
                "the id of that version (the document keeps its id). Empty while tasks run; "
                "reprocessing produces none."
            )
        ),
    ]
    produced_links: Annotated[
        tuple[str, ...],
        Field(
            description=(
                "Links to the produced documents; for a new version the link of the document "
                "that got it."
            )
        ),
    ]
    trashed_ids: Annotated[
        tuple[int, ...],
        Field(
            description=(
                "The originals this call moved to the Paperless trash, after it checked that "
                "everything was made; empty if it kept them."
            )
        ),
    ]
    original: Annotated[
        Literal["kept", "trashed", "shortened", "unknown"] | None,
        Field(
            description=(
                "The one original of split_document, extract_pages or a replacement: shortened "
                "if the pages were removed, unknown if that is not known. null elsewhere."
            )
        ),
    ] = None
    parts: Annotated[
        tuple[PartOutcome, ...],
        Field(description="The new documents of split_document and extract_pages."),
    ] = ()
    next: Annotated[
        str | None, Field(description="The calls that finish what was left, as a suggestion.")
    ]
    wait: Wait
    waited_seconds: float
