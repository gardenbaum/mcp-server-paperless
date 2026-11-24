"""Files as they are handed over: chunk by chunk, in parts, or saved into a directory."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.admin import ArchiveContent
from mcp_server_paperless.domain.documents import FileVersion

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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
