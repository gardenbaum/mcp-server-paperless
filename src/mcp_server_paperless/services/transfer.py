"""Parts of a file: which bytes, and the proof that they belong to the parts before them.

Paperless serves no ranges, so every part streams the file from its start and keeps a window.
Nothing is kept between two calls; what a caller brings along (the checksum, and for a document the
version) is compared instead, and a file that changed in between is refused (decision-45).
"""

import asyncio
import contextlib
import hashlib
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import PaperlessMcpError, Validation

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

# The largest file that is streamed, and how long that may take: for a part as for saving.
MAX_FILE_BYTES: Final = 2 * 1024 * 1024 * 1024
MAX_SECONDS: Final = 30 * 60.0
# How long Paperless may take to build a ZIP archive before its first byte, for a part.
PART_WAIT_SECONDS: Final = 120.0
REPEAT: Final = "Repeat the arguments that next in the report names."
APPEND: Final = "append the parts in order."
RESTART: Final = "Repeat the call with offset=0 and without checksum, and use only the new parts."


def changed() -> Validation:
    """The refusal of a part that does not belong to the parts before it."""
    message = "The file changed since the first part; start again with offset=0."
    return Validation(message, hint=RESTART)


def too_large() -> PaperlessMcpError:
    """The refusal of a file over the limit."""
    message = f"The file is larger than {MAX_FILE_BYTES // 1024**3} GiB, the most that is read."
    return PaperlessMcpError(message, hint="Open the document in the Paperless web UI.")


def incomplete() -> PaperlessMcpError:
    """The error for a file whose bytes are not those Paperless describes."""
    message = "Paperless sent other bytes than it says the file has, so they are not used."
    return PaperlessMcpError(message, hint="Try again; the file may just have changed.")


@contextlib.asynccontextmanager
async def time_limit(seconds: float) -> AsyncGenerator[None]:
    """Give the block ``seconds``; then it ends with an error the caller may see.

    Raises:
        PaperlessMcpError: The time was not enough.
    """
    limit = asyncio.timeout(seconds)
    try:
        async with limit:
            yield
    except TimeoutError:
        if not limit.expired():
            raise
        message = f"Paperless did not send the file within {seconds / 60:.0f} minutes."
        raise PaperlessMcpError(message, hint="Try again later or ask for less.") from None


def check_part(offset: int | None, max_bytes: int | None, checksum: str | None) -> None:
    """Refuse arguments of a part without the part, and a part that does not say where it belongs.

    Raises:
        Validation: See above.
    """
    if offset is None:
        if max_bytes is not None or checksum is not None:
            message = "max_bytes and checksum are for reading a file in parts."
            raise Validation(message, hint="Give offset, too; the first part starts at offset=0.")
        return
    if offset < 0 or (max_bytes is not None and max_bytes < 1):
        message = "offset must be 0 or more and max_bytes 1 or more."
        raise Validation(message, hint="The first part starts at offset=0.")
    if offset and checksum is None:
        message = "A part after the first needs the checksum of the first part's report."
        raise Validation(message, hint=REPEAT)


async def read_window(chunks: AsyncIterator[bytes], offset: int, length: int) -> bytes:
    """The ``length`` bytes from ``offset``, fewer at the end; what follows is not read."""
    window = bytearray()
    position = 0
    async for chunk in chunks:
        window += chunk[max(0, offset - position) :]
        position += len(chunk)
        if len(window) >= length:
            break
    return bytes(window[:length])


async def read_all(
    chunks: AsyncIterator[bytes], offset: int, length: int
) -> tuple[bytes, int, str]:
    """The same window of a file that is read to its end, and the size and SHA-256 of the file.

    Raises:
        PaperlessMcpError: The file is over the limit.
    """
    digest = hashlib.sha256()
    window = bytearray()
    position = 0
    async for chunk in chunks:
        digest.update(chunk)
        if len(window) < length:
            window += chunk[max(0, offset - position) :]
            del window[length:]
        position += len(chunk)
        if position > MAX_FILE_BYTES:
            raise too_large()
    return bytes(window), position, digest.hexdigest()
