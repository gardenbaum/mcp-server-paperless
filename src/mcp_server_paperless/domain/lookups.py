"""What the small lookups answer: words of the search index, and a tried storage path."""

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.documents import Ref


class Words(BaseModel):
    """Words of the search index, most frequent first."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    words: tuple[str, ...]
    too_long: Annotated[int, Field(description="Words left out: over 100 characters.")]


class StoragePathTest(BaseModel):
    """The path a template gives the file of a document."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    storage_path: Annotated[
        Ref | None, Field(description="The stored path that was tried; null for a template.")
    ]
    path: Annotated[str | None, Field(description="Null: no path, see note.")]
    note: str | None
