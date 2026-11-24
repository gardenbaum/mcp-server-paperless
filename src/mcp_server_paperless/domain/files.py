"""Files as they are handed over: chunk by chunk, in parts, or saved into a directory."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.admin import ArchiveContent
from mcp_server_paperless.domain.documents import FileVersion
from mcp_server_paperless.domain.errors import NotFound, PaperlessMcpError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from mcp_server_paperless.domain.documents import FileMetadata


STILL_READ: Final = "Its text and metadata can still be read; restore the file from a backup."
STILL_READ_MANY: Final = (
    "Their text and metadata can still be read; restore the files from a backup."
)
LEAVE_OUT: Final = (
    "Leave it out; its text and metadata can still be read. Restore the file from a backup."
)
LEAVE_OUT_MANY: Final = (
    "Leave them out; their text and metadata can still be read. Restore the files from a backup."
)
NOUNS: Final[dict[FileVersion, str]] = {"original": "original", "archive": "archive version"}


def missing_file(
    document_id: int,
    version: FileVersion,
    facts: FileMetadata,
    version_id: int | None = None,
    *,
    hint: str | None = None,
) -> NotFound:
    """The error for a file ``facts`` says is missing; ``hint`` replaces the one for a download."""
    wanted = facts.sent_for(version)
    other: FileVersion = "original" if wanted == "archive" else "archive"
    # Without an archive version the other file is the missing one.
    there = not facts.lacks(other)
    named = "" if version_id is None else f" (version {version_id})"
    return NotFound(
        f"Paperless cannot find the {NOUNS[wanted]} of document {document_id}{named}.",
        hint=hint or (f"Ask for the {NOUNS[other]}, which is there." if there else STILL_READ),
    )


def not_sent(document_id: int) -> PaperlessMcpError:
    """The error for a file Paperless lists, with its size, and still answers as not found."""
    message = f"Paperless did not send the file of document {document_id} although it lists it."
    hint = "Try again; if it stays so, an administrator can check the media directory of Paperless."
    return PaperlessMcpError(message, hint=hint)


def joined(document_ids: Sequence[int]) -> str:
    """``document 3`` or ``documents 3, 5 and 8``."""
    *rest, last = [str(number) for number in document_ids]
    return f"documents {', '.join(rest)} and {last}" if rest else f"document {last}"


def missing_files(
    facts: Mapping[int, FileMetadata], versions: Sequence[FileVersion], total: int, *, choice: bool
) -> NotFound | None:
    """The error if Paperless cannot find a file of some of ``total`` chosen documents, else None.

    ``facts`` holds the documents that could be checked, ``versions`` the files that are needed of
    each. With ``choice`` the caller can ask for the other file kind, which is named if it works.
    """
    lost = [n for n, found in facts.items() if any(found.lacks(v) for v in versions)]
    if not lost:
        return None
    hints: list[str] = []
    if choice and len(facts) == total:
        kinds: tuple[FileVersion, ...] = ("archive", "original")
        works: list[FileVersion] = [
            k for k in kinds if not any(found.lacks(k) for found in facts.values())
        ]
        if works:
            hints.append(f"Choosing the {NOUNS[works[0]]}s instead works for all of them.")
    if len(lost) == total:
        hints.append(STILL_READ if total == 1 else STILL_READ_MANY)
    else:
        hints.append(LEAVE_OUT if len(lost) == 1 else LEAVE_OUT_MANY)
    if len(facts) < total:
        unchecked = total - len(facts)
        hints.append(f"The list may be incomplete: {unchecked} could not be checked.")
    count = "" if total == 1 else f" ({len(lost)} of the {total} chosen)"
    return NotFound(f"Paperless cannot find a file of {joined(lost)}{count}.", hint=" ".join(hints))


@dataclass(frozen=True, slots=True)
class OpenedFile:
    """A file Paperless began to send; its bytes are read only as they are asked for.

    Attributes:
        mime_type: The ``Content-Type`` Paperless sent, parameters included.
        filename: The name Paperless proposes, without a path, control or bidi characters; None if
            it proposes none.
        chunks: The bytes. A connection that breaks raises, unless the body ends with its connection
            (no length, no chunks): then it can end early unnoticed, so readers compare the size,
            the checksum or the end of the file.
    """

    mime_type: str
    filename: str | None
    chunks: AsyncIterator[bytes]


class PartReport(BaseModel):
    """What a tool says about one part of a file; the part itself is attached unless empty."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    filename: Annotated[str | None, Field(description="Name of the whole file.")]
    mime_type: Annotated[str, Field(description="MIME type of the whole file.")]
    offset: Annotated[int, Field(description="Byte of the file this part starts at.")]
    length: Annotated[int, Field(description="Bytes in this part; 0 past the end.")]
    total_bytes: Annotated[int, Field(description="Size of the whole file.")]
    next_offset: Annotated[int | None, Field(description="Where the next part starts; null: end.")]
    part_sha256: Annotated[str, Field(description="SHA-256 of this part.")]
    checksum: Annotated[str, Field(description="Checksum of the whole file (SHA-256).")]
    next: Annotated[str | None, Field(description="What to call next; null: that was the end.")]


class FilePartReport(PartReport):
    """A part of a document's file."""

    document_id: int
    kind: FileVersion
    version_id: int
    web_url: str


class ArchivePartReport(PartReport):
    """A part of a ZIP archive of documents."""

    document_ids: tuple[int, ...]
    content: ArchiveContent


@dataclass(frozen=True, slots=True)
class FilePart[R: PartReport]:
    """A report and the bytes of the part it describes."""

    report: R
    data: bytes


class SavedReport(BaseModel):
    """What a tool says about a file it saved on the machine it runs on."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    directory: Annotated[str, Field(description="The directory, as it was given.")]
    filename: str
    size_bytes: int
    sha256: str
    replaced: Annotated[bool, Field(description="An existing file was replaced.")]


class SavedFileReport(SavedReport):
    """A saved file of a document."""

    document_id: int
    kind: FileVersion
    version_id: int
    web_url: str


class SavedArchiveReport(SavedReport):
    """A saved ZIP archive of documents."""

    document_ids: tuple[int, ...]
    content: ArchiveContent
