"""Files as they are handed over: chunk by chunk, in parts, or saved into a directory."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True, slots=True)
class OpenedFile:
    """A file Paperless began to send; its bytes are read only as they are asked for.

    Attributes:
        mime_type: The ``Content-Type`` Paperless sent, parameters included.
        filename: The name Paperless proposes, without a path, control or bidi characters; None if
            it proposes none.
        chunks: The bytes. A connection that breaks raises; it never ends the file early.
    """

    mime_type: str
    filename: str | None
    chunks: AsyncIterator[bytes]
