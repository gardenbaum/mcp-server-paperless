"""Duplicates: the same file, by its SHA-256, in another document, a version or the trash."""

from datetime import datetime
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class DuplicateRecord(BaseModel):
    """A document Paperless reports as a duplicate, as the port reads it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    title: str
    deleted_at: datetime | None


# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# only where the name does not say enough.


class DuplicateMatch(BaseModel):
    """A document, or one version of it, that holds the same file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    title: Annotated[str | None, Field(description="Null if it could not be read.")]
    version_id: Annotated[
        int | None, Field(description="The later version with the file; null: the document's own.")
    ] = None
    in_trash: bool = False
    deleted_at: datetime | None = None
    web_url: Annotated[
        str | None, Field(description="Link to the document; null in the trash.")
    ] = None


class DuplicateReport(BaseModel):
    """What is known about duplicates of a consumed file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    after: Annotated[
        tuple[DuplicateMatch, ...] | None,
        Field(
            description=(
                "Found after consumption: what Paperless reports for a document, what this server "
                "compared for a version. Null: not consumed yet, or not readable."
            )
        ),
    ] = None
    rejected_for: Annotated[
        DuplicateMatch | None,
        Field(
            description=(
                "The existing document Paperless rejected the file for, if this user may see it."
            )
        ),
    ] = None
    not_checked: Annotated[
        tuple[str, ...], Field(description="Where a duplicate would not have been noticed.")
    ] = ()


class DuplicateCheck(DuplicateReport):
    """The duplicates of an uploaded file, before sending and after consumption."""

    before: Annotated[
        tuple[DuplicateMatch, ...], Field(description="Found before sending; at most 10.")
    ]
    before_count: int
