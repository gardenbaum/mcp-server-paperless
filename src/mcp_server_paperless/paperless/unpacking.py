"""Reading a compressed body in bounded pieces.

The decoders of httpx2 bound what one piece unpacks to, but not what unpacks to nothing: data after
the end of the stream is kept (and copied again with every piece), empty blocks cost time only. So
gzip and deflate are unpacked here, where the end of the stream is the end of the body, and a JSON
read counts the bytes that arrive, too.
"""

import zlib
from typing import TYPE_CHECKING, Final, cast, final, override

from httpx2 import AsyncByteStream, DecodingError, Response

from mcp_server_paperless.domain.errors import AnswerTooLarge

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterable, Iterator

# The encodings that are unpacked here, as they are asked for.
ENCODINGS: Final = ("gzip", "deflate")
PIECE: Final = 1024 * 1024
# The first bytes that tell a zlib stream from a raw one (both are sent as "deflate").
HEADER: Final = 2
# The encodings left to httpx2 (brotli if it is installed): data after their end is not kept.
OTHERS: Final = ("br", "zstd")
MAX_LAYERS: Final = 5  # as in httpx2


class BrokenBodyError(Exception):
    """A compressed body is broken, cut off or goes on after its end."""


@final
class Cut(AsyncByteStream):
    """The first ``limit`` bytes of a stream, as they arrive."""

    def __init__(self, stream: object, limit: int) -> None:
        """Cut ``stream``, the one of an async response."""
        self._stream: AsyncByteStream = cast("AsyncByteStream", stream)
        self._left: int = limit

    @override
    async def __aiter__(self) -> AsyncGenerator[bytes]:
        async for chunk in self._stream:
            yield chunk[: self._left]
            self._left -= len(chunk)
            if self._left <= 0:
                return

    @override
    async def aclose(self) -> None:
        await self._stream.aclose()


@final
class _Unpacker:
    def __init__(self, encoding: str) -> None:
        self._gzip: bool = encoding == "gzip"
        self._start: bytes = b""
        self._stream = None

    def unpack(self, data: bytes) -> Iterator[bytes]:
        """What ``data`` unpacks to, at most ``PIECE`` bytes at a time; none after the end.

        Raises:
            BrokenBodyError: It is broken.
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
            data = stream.unconsumed_tail
            going = len(piece) == PIECE  # more can wait inside, also when all data has gone in
            if piece:
                yield piece

    @property
    def ended(self) -> bool:
        """The stream came to its end."""
        return self._stream is not None and self._stream.eof

    @property
    def exceeded(self) -> bool:
        """Data came after the end of the stream."""
        return self._stream is not None and bool(self._stream.unused_data)


async def decoded(response: Response) -> AsyncGenerator[bytes]:
    """The streamed body like ``aiter_bytes()``, but it ends where a gzip or deflate stream ends.

    At most one piece is read on from there, to see the end of the answer if it is there. A stream
    without an end gives what arrived, as with httpx2.

    Raises:
        DecodingError: The body is broken, or packed more than ``MAX_LAYERS`` times.
    """
    # Not aiter_raw(): see unpacked().
    body = cast("AsyncIterable[bytes]", response.stream)
    names = response.headers.get_list("Content-Encoding", split_commas=True)
    layers = [name for n in names if (name := n.lower()) in {*ENCODINGS, *OTHERS}]
    if len(layers) > MAX_LAYERS:
        message = f"Cannot apply more than {MAX_LAYERS} content encodings."
        raise DecodingError(message)
    for layer in reversed(layers):
        if layer in ENCODINGS:
            body = _to_its_end(body, layer)
        else:
            body = Response(200, headers={"Content-Encoding": layer}, content=body).aiter_bytes()
    async for chunk in body:
        yield chunk


async def _to_its_end(body: AsyncIterable[bytes], encoding: str) -> AsyncGenerator[bytes]:
    unpacker = _Unpacker(encoding)
    async for chunk in body:
        if unpacker.ended:
            return
        try:
            for piece in unpacker.unpack(chunk):
                yield piece
        except BrokenBodyError as error:
            message = "The body cannot be unpacked."
            raise DecodingError(message) from error


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
            if unpacker.exceeded:
                raise BrokenBodyError
    if unpacker is not None and not unpacker.ended:
        raise BrokenBodyError
