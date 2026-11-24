"""Reading a compressed body in bounded pieces.

The decoders of httpx2 bound what one piece unpacks to, but not what unpacks to nothing: data after
the end of the stream is kept (and copied again with every piece), empty blocks cost time only. So
the bytes that arrive are counted here, and the end of the stream is the end of the body.
"""

import zlib
from typing import TYPE_CHECKING, Final, cast, final

from mcp_server_paperless.domain.errors import AnswerTooLarge

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator

    from httpx2 import AsyncByteStream, Response

# The encodings that are unpacked here, as they are asked for.
ENCODINGS: Final = ("gzip", "deflate")
PIECE: Final = 1024 * 1024
# The first bytes that tell a zlib stream from a raw one (both are sent as "deflate").
HEADER: Final = 2


class BrokenBodyError(Exception):
    """A compressed body is broken, cut off or goes on after its end."""


@final
class _Unpacker:
    def __init__(self, encoding: str) -> None:
        self._gzip: bool = encoding == "gzip"
        self._start: bytes = b""
        self._stream = None

    def unpack(self, data: bytes) -> Iterator[bytes]:
        """What ``data`` unpacks to, at most ``PIECE`` bytes at a time.

        Raises:
            BrokenBodyError: It is broken, or there is data after the end of the stream.
        """
        if self._stream is None:
            data = self._start + data
            if len(data) < HEADER:
                self._start = data
                return
            zlibbed = data[0] & 0x0F == zlib.DEFLATED and int.from_bytes(data[:HEADER]) % 31 == 0
            bits = zlib.MAX_WBITS if zlibbed else -zlib.MAX_WBITS
            self._stream = zlib.decompressobj(zlib.MAX_WBITS | 16 if self._gzip else bits)
        stream, going = self._stream, bool(data)
        while going:
            try:
                piece = stream.decompress(data, PIECE)
            except zlib.error as error:
                raise BrokenBodyError from error
            if stream.unused_data:
                raise BrokenBodyError
            data = stream.unconsumed_tail
            going = len(piece) == PIECE  # more can wait inside, also when all data has gone in
            if piece:
                yield piece

    def finish(self) -> None:
        """Raises ``BrokenBodyError`` if the stream did not end."""
        if self._stream is None or not self._stream.eof:
            raise BrokenBodyError


async def unpacked(response: Response, encoding: str, max_bytes: int) -> AsyncGenerator[bytes]:
    """The streamed body, unpacked if ``encoding`` is one of ``ENCODINGS``, as it arrives.

    Raises:
        AnswerTooLarge: More than ``max_bytes`` bytes arrived.
        BrokenBodyError: See there.
    """
    unpacker = _Unpacker(encoding) if encoding in ENCODINGS else None
    arrived = 0
    # Not aiter_raw(): the schema guard of the tests has read the body already.
    body = cast("AsyncByteStream", response.stream)
    async for chunk in body:
        arrived += len(chunk)
        if arrived > max_bytes:
            path = response.request.url.path
            message = f"Paperless sent more than {max_bytes} bytes for GET {path}."
            raise AnswerTooLarge(message)
        if unpacker is None:
            yield chunk
        else:
            for piece in unpacker.unpack(chunk):
                yield piece
    if unpacker is not None:
        unpacker.finish()
