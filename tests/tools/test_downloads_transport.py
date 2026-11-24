"""download_document and bulk_download over the real transports (PLMCP-76, AC 2 and AC 4)."""

import base64
import hashlib
import json
import os
import sys
from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakeDocument, FakePaperless, seeded
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from harness import serve
from mcp.types import BlobResourceContents, EmbeddedResource, TextContent
from test_http_uploads import DEFAULT, http_settings, listening

from mcp_server_paperless.app import build_app

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.client.client import CallToolResult
    from fastmcp.client.transports import ClientTransport

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-45")]

type Json = dict[str, object]

LIMIT = 512 * 1024  # the default download limit


def opened(result: CallToolResult) -> tuple[Json, bytes]:
    first, *rest = result.content
    assert isinstance(first, TextContent)
    data = b""
    for block in rest:
        assert isinstance(block, EmbeddedResource)
        assert isinstance(block.resource, BlobResourceContents)
        data += base64.b64decode(block.resource.blob)
    return cast("Json", json.loads(first.text)), data


async def all_parts[T: ClientTransport](client: Client[T], name: str, arguments: Json) -> bytes:
    collected, offset, again = b"", 0, dict[str, object]()
    while True:
        report, data = opened(
            await client.call_tool(name, {**arguments, **again, "offset": offset})
        )
        collected += data
        again = {"checksum": report["checksum"]}
        if name == "download_document":
            again["version_id"] = report["version_id"]
        if report["next_offset"] is None:
            assert report["checksum"] == hashlib.sha256(collected).hexdigest()
            return collected
        offset = cast("int", report["next_offset"])


def paperless(original: bytes) -> FakePaperless:
    state = seeded()
    state.documents.append(FakeDocument(id=7, title="Scan", original=original))
    return FakePaperless(state, user="admin")


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.decision("decision-46")
async def test_files_at_the_default_limit_and_parts_pass_over_http(mode: str) -> None:
    """One event of the MCP client holds 1 MiB: a part of the default limit fits, in base64."""
    original = os.urandom(LIMIT + LIMIT // 2)
    app = build_app(
        serve(paperless(original), toolsets=["core", "admin"]),
        http_settings(with_token=False),
        max_upload_bytes=DEFAULT,
    )
    small = build_app(
        serve(paperless(original[:LIMIT])),
        http_settings(with_token=False),
        max_upload_bytes=DEFAULT,
    )
    with listening(small) as url:
        async with Client(StreamableHttpTransport(url), mode=mode) as client:
            report, data = opened(await client.call_tool("download_document", {"document_id": 7}))
    assert (report["delivered"], data) == (True, original[:LIMIT])

    with listening(app) as url:
        async with Client(StreamableHttpTransport(url), mode=mode) as client:
            whole, _ = opened(await client.call_tool("download_document", {"document_id": 7}))
            parts = await all_parts(client, "download_document", {"document_id": 7})
            archive = await all_parts(
                client, "bulk_download", {"document_ids": [7], "content": "originals"}
            )
            refused = await client.call_tool(
                "download_document",
                {"document_id": 7, "save_dir": "/tmp"},  # noqa: S108 -- never used
                raise_on_error=False,
            )

    assert whole["delivered"] is False
    assert parts == original
    assert original in archive  # stored, not compressed
    assert refused.is_error  # no such parameter over HTTP


# The server over stdio with a fake Paperless behind it.
STDIO = """
import sys
sys.path[:0] = [{tests!r}]
from fakes.paperless import FakeDocument, FakePaperless, seeded
from harness import serve
state = seeded()
state.documents.append(FakeDocument(id=7, title="Scan", original=bytes(range(256)) * 4096))
serve(
    FakePaperless(state, user="admin"),
    toolsets=["core", "admin"],
    stdio=True,
    download_dirs=[{root!r}],
).run(transport="stdio", show_banner=False)
"""


async def test_parts_and_saving_work_over_stdio_and_the_stream_stays_clean(tmp_path: Path) -> None:
    tests = os.path.dirname(os.path.dirname(__file__))  # noqa: PTH120 -- a string for sys.path
    transport = StdioTransport(
        sys.executable,
        ["-c", STDIO.format(tests=tests, root=str(tmp_path))],
        env={},
        keep_alive=False,
    )
    original = bytes(range(256)) * 4096

    async with Client(transport) as client:
        parts = await all_parts(client, "download_document", {"document_id": 7})
        saved, nothing = opened(
            await client.call_tool(
                "download_document", {"document_id": 7, "save_dir": str(tmp_path)}
            )
        )
        archive, _ = opened(
            await client.call_tool(
                "bulk_download", {"document_ids": [7], "save_dir": str(tmp_path)}
            )
        )

    assert parts == original
    assert nothing == b""
    assert (tmp_path / str(saved["filename"])).read_bytes() == original
    assert (tmp_path / "documents.zip").stat().st_size == archive["size_bytes"]
