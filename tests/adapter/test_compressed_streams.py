"""Streamed reads of compressed bodies: hostile ones take little memory, files are read as ever."""

import gzip
import hashlib
import subprocess
import sys
import zlib
from compression import zstd
from typing import TYPE_CHECKING

import httpx2
import pytest

from adapter.conftest import streaming
from adapter.test_client import Chunks
from mcp_server_paperless.domain.errors import (
    FeatureUnavailable,
    NotFound,
    UnexpectedAnswer,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.paperless.client import ERROR_BODY_LIMIT, PaperlessClient, opened
from mcp_server_paperless.paperless.errors import AI_DISABLED_BODY

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

pytestmark = pytest.mark.anyio

MEMORY = """
import asyncio, gzip, resource, sys, zlib, httpx2, pydantic
from compression import zstd
from mcp_server_paperless.domain.errors import PaperlessMcpError
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy, opened

FILE = b"a small file"
TRAIL = [b"x" * 65536] * 128
SKIPPED = b"\\x50*M\\x18\\xf8\\xff\\0\\0" + bytes(65528)  # a skippable frame
EMPTY_BLOCKS = bytes([0, 0, 0, 255, 255]) * 13000
packer = zlib.compressobj(wbits=31)
start = packer.compress(b"") + packer.flush(zlib.Z_SYNC_FLUSH)
# An outer stream of 16 KiB that unpacks to 16 MiB after the end of the inner one.
inside = gzip.compress(gzip.compress(FILE) + bytes(16 * 1024 * 1024), 9)
BODIES = {
    "gzip": ([gzip.compress(FILE), *TRAIL], "gzip"),
    "deflate": ([zlib.compress(FILE), *TRAIL], "deflate"),
    "members": ([gzip.compress(FILE) * 2000] * 128, "gzip"),
    "blocks": ([start, *[EMPTY_BLOCKS] * 128], "gzip"),
    "inner": ([inside], "gzip, gzip"),
    "outer": ([gzip.compress(gzip.compress(FILE)), *TRAIL], "gzip, gzip"),
    "inner of zstd": ([gzip.compress(zstd.compress(FILE)), *TRAIL], "zstd, gzip"),
    "zstd": ([zstd.compress(FILE), *TRAIL], "zstd"),
    "skipped": ([zstd.compress(FILE), *[SKIPPED] * 128], "zstd"),
    "frames": ([zstd.compress(b"") * 2000] * 64, "zstd"),
}
read, how = sys.argv[1:3]
pieces, encoding = BODIES[how]
read, error = read.removesuffix(" error"), read.endswith(" error")


class Body(httpx2.AsyncByteStream):
    def __init__(self, pieces):
        self.pieces = pieces

    async def __aiter__(self):
        for piece in self.pieces:
            yield piece


def answer(request):
    if request.url.path.endswith("warm"):
        return httpx2.Response(200, stream=Body([b"x"]))
    headers = {"Content-Encoding": encoding}
    return httpx2.Response(500 if error else 200, headers=headers, stream=Body(pieces))


async def get(client, path):
    if read == "download":
        return (await client.download(path, max_bytes=1000)).content
    if read == "post":
        return (await client.stream_post(path, json={}, max_bytes=1000, max_seconds=50)).content
    if read == "post json":
        return await client.post_json(path, pydantic.BaseModel, json={}, max_bytes=1000)
    if read == "json":
        return await client.read_json(path, pydantic.BaseModel, max_bytes=1000)
    async with client.stream("GET", path) as response:
        return b"".join([chunk async for chunk in opened(response).chunks])


async def main():
    transport = httpx2.MockTransport(answer)
    async with httpx2.AsyncClient(base_url="http://x", transport=transport) as http:
        retry = RetryPolicy(attempts=1)
        client = PaperlessClient(http, StaticTokenProvider("t"), retry=retry, upload_timeout=5)
        async with client.stream("GET", "/warm"):
            pass
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        told = "error"
        try:
            told = (await get(client, "/api/x/")).decode()
        except (PaperlessMcpError, httpx2.DecodingError) as raised:
            told = type(raised).__name__
        print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before, told, sep="|")


asyncio.run(main())
"""


@pytest.mark.parametrize(
    "how",
    [
        "gzip",
        "deflate",
        "members",
        "blocks",
        "inner",
        "outer",
        "inner of zstd",
        "zstd",
        "skipped",
        "frames",
    ],
)
@pytest.mark.parametrize(
    "read",
    [
        *("download", "stream", "post", "json", "post json"),
        *("download error", "stream error", "post error", "json error", "post json error"),
    ],
)
def test_a_hostile_compressed_body_takes_little_memory_of_the_process(read: str, how: str) -> None:
    done = subprocess.run(  # noqa: S603 -- this interpreter and a script of this file
        [sys.executable, "-c", MEMORY, read, how],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )

    grown, told = done.stdout.strip().split("|")
    unit = 1 if sys.platform == "darwin" else 1024
    assert int(grown) * unit < 8_000_000, (grown, told)
    if "error" in read:
        assert told == "UpstreamUnavailable"
    elif "json" in read:
        assert told == "AnswerCompressed"
    elif how in {"gzip", "deflate", "members", "inner", "outer", "inner of zstd", "skipped"}:
        assert told == "a small file"  # what follows the end is not read, as before


# --- files are read as before ---------------------------------------------------------------------

# Longer than a piece, and not all of it is small when packed.
NOISE = b"".join(hashlib.sha256(bytes([n // 256, n % 256])).digest() for n in range(6000))
FILE = NOISE + bytes(3 * 1024 * 1024) + b"the end"


def raw(data: bytes) -> bytes:
    packer = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return packer.compress(data) + packer.flush()


def five(data: bytes) -> bytes:
    for _ in range(5):
        data = gzip.compress(data)
    return data


PACKERS: dict[str, tuple[str, Callable[[bytes], bytes]]] = {
    "none": ("", bytes),
    "identity": ("identity", bytes),
    "gzip": ("gzip", gzip.compress),
    "GZIP": (" GZIP ", gzip.compress),
    "zlib": ("deflate", zlib.compress),
    "raw": ("Deflate", raw),
    "zstd": ("zstd", zstd.compress),
    "zstd frames": ("zstd", lambda data: zstd.compress(data[:9]) + zstd.compress(data[9:])),
    "two": ("gzip, zstd", lambda data: zstd.compress(gzip.compress(data))),
    "three": ("deflate, identity,gzip", lambda data: gzip.compress(zlib.compress(data))),
    "five": ("gzip, " * 4 + "gzip", five),
    "unknown": ("x-unknöwn", bytes),
}


def answered(how: str, data: bytes, step: int) -> tuple[httpx2.Response, Chunks]:
    encoding, pack = PACKERS[how]
    packed = pack(data)
    body = Chunks(*[packed[at : at + step] for at in range(0, len(packed), step)])
    headers = [(b"Content-Encoding", encoding.encode())] if encoding else []
    return httpx2.Response(200, headers=headers, stream=body), body


async def download(client: PaperlessClient) -> bytes:
    return (await client.download("/api/documents/1/download/", max_bytes=len(FILE))).content


async def post(client: PaperlessClient) -> bytes:
    found = await client.stream_post(
        "/api/documents/chat/", json={}, max_bytes=len(FILE), max_seconds=30
    )
    assert not found.truncated
    return found.content


async def stream(client: PaperlessClient) -> bytes:
    async with client.stream("GET", "/api/documents/1/download/") as response:
        return b"".join([chunk async for chunk in opened(response).chunks])


type Read = Callable[[PaperlessClient], Awaitable[bytes]]
READS: dict[str, Read] = {"download": download, "post": post, "stream": stream}


@pytest.mark.parametrize("data", [b"", b"x", FILE], ids=["empty", "short", "long"])
@pytest.mark.parametrize("step", [1, 7, 65536, 10**9])
@pytest.mark.parametrize("how", list(PACKERS))
@pytest.mark.parametrize("read", list(READS))
async def test_a_file_is_read_whole_however_it_is_packed_and_cut(
    read: str, how: str, step: int, data: bytes
) -> None:
    if step == 1 and data is FILE:
        data = FILE[:300] + bytes(2 * 1024 * 1024)  # else a million steps
        if PACKERS[how][1](data) == data:
            data = FILE[:300]
    answer, body = answered(how, data, step)
    client, _ = streaming(lambda _: answer)

    assert await READS[read](client) == data

    assert body.pulled == len(body.chunks)
    assert body.closed


@pytest.mark.parametrize("read", list(READS))
@pytest.mark.parametrize("how", ["gzip", "zlib", "raw"])
async def test_what_follows_the_end_is_left_out_and_not_read_on(read: str, how: str) -> None:
    """One piece more is asked for, so that a connection whose answer is over can be kept."""
    answer, body = answered(how, b"text", 10**9)
    body.chunks += (b"more", b"more", b"more")
    client, _ = streaming(lambda _: answer)

    assert await READS[read](client) == b"text"

    assert body.pulled == 2
    assert body.closed


@pytest.mark.parametrize("read", list(READS))
async def test_a_raw_stream_whose_start_looks_like_a_zlib_header_is_read(read: str) -> None:
    """A stored block whose padding makes 78 9c of it: httpx2 reads it, so it is read here."""
    data = bytes.fromhex("789c0063ff") + b"x" * 156 + bytes.fromhex("010000ffff")
    answer = httpx2.Response(200, headers={"Content-Encoding": "deflate"}, stream=Chunks(data))
    client, _ = streaming(lambda _: answer)

    assert await READS[read](client) == b"x" * 156


@pytest.mark.parametrize("read", list(READS))
@pytest.mark.parametrize("how", ["gzip", "zlib", "raw"])
async def test_a_file_whose_end_is_missing_gives_what_arrived_as_before(
    read: str, how: str
) -> None:
    """Readers compare the size and the checksum where Paperless names them."""
    encoding, pack = PACKERS[how]
    body = Chunks(pack(b"text" * 10)[:-1])
    answer = httpx2.Response(200, headers={"Content-Encoding": encoding}, stream=body)
    client, _ = streaming(lambda _: answer)

    assert b"text" * 9 in await READS[read](client)


@pytest.mark.parametrize("read", list(READS))
@pytest.mark.parametrize(
    ("encoding", "data"),
    [
        *((encoding, b"no such thing") for encoding in ("gzip", "deflate", "zstd", "gzip, gzip")),
        ("gzip," * 5 + "gzip", gzip.compress(five(b"six layers"))),
    ],
)
async def test_a_broken_body_is_an_unexpected_answer_not_an_internal_error(
    read: str, encoding: str, data: bytes
) -> None:
    body = Chunks(data)
    answer = httpx2.Response(200, headers={"Content-Encoding": encoding}, stream=body)
    client, _ = streaming(lambda _: answer)

    with pytest.raises(UnexpectedAnswer, match="unexpected response for") as raised:
        _ = await READS[read](client)

    assert raised.value.hint is None  # a POST that only reads: nothing may have been applied
    assert body.closed


# --- error bodies ---------------------------------------------------------------------------------


@pytest.mark.parametrize("read", list(READS))
@pytest.mark.parametrize("how", list(PACKERS))
@pytest.mark.parametrize("large", [False, True])
async def test_the_words_of_an_error_are_told_however_they_are_packed(
    read: str,
    how: str,
    large: bool,  # noqa: FBT001 -- a parametrised flag, not a switch
) -> None:
    text = AI_DISABLED_BODY.encode() + b" " * 300_000 if large else b'{"document_id": ["wrong"]}'
    answer, body = answered(how, text, 1000)
    answer.status_code = 400
    client, _ = streaming(lambda _: answer)

    with pytest.raises(FeatureUnavailable if large else Validation, match=r"AI|wrong"):
        _ = await READS[read](client)

    assert body.closed


@pytest.mark.parametrize("read", list(READS))
@pytest.mark.parametrize("first", [b"", gzip.compress(b"no more")], ids=["blocks", "trail"])
async def test_no_more_of_an_error_arrives_than_its_limit(read: str, first: bytes) -> None:
    """Also when nothing of it unpacks to anything."""
    packer = zlib.compressobj(wbits=31)
    start = first or packer.compress(b"") + packer.flush(zlib.Z_SYNC_FLUSH)
    body = Chunks(start, *[b"\0\0\0\xff\xff" * 2000] * 100)
    answer = httpx2.Response(500, headers={"Content-Encoding": "gzip"}, stream=body)
    client, _ = streaming(lambda _: answer)

    with pytest.raises(UpstreamUnavailable):
        _ = await READS[read](client)

    assert body.pulled <= ERROR_BODY_LIMIT // 10_000 + 2


@pytest.mark.parametrize("read", list(READS))
async def test_an_error_whose_body_cannot_be_unpacked_is_still_that_error(read: str) -> None:
    answer = httpx2.Response(
        404, headers={"Content-Encoding": "gzip"}, stream=Chunks(b"not gzip at all")
    )
    client, _ = streaming(lambda _: answer)

    with pytest.raises(NotFound):
        _ = await READS[read](client)
