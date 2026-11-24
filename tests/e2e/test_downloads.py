"""download_document and bulk_download of the installed scripts: parts over both transports, saving.

The real processes against the E2E Paperless: every file kind, a named version and a ZIP archive, in
parts and whole; over stdio also saved, with a replacement refused and then confirmed.
"""

import base64
import contextlib
import hashlib
import io
import json
import time
import uuid
import zipfile
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from mcp.types import BlobResourceContents, EmbeddedResource, TextContent

from e2e.helpers import consume, lose_file, parse, pdf, results
from e2e.test_http import MCP_TOKEN, running
from e2e.test_stdio import console_script

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from pathlib import Path

    from fastmcp.client.client import CallToolResult
    from fastmcp.client.transports import ClientTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-45")]

type Json = dict[str, object]


class Uploaded:
    """A document with two versions: a PDF with an archive version, and a newer text."""

    def __init__(self, stack: Stack) -> None:
        title = f"plmcp76 {uuid.uuid4().hex[:12]}"
        self.original: bytes = pdf(title)
        self.text: bytes = (f"{title} " * 60).encode()
        _, document = consume(stack.admin, title, self.original)
        assert document is not None
        self.id: int = document
        reply = stack.admin.post(
            f"/api/documents/{document}/update_version/",
            files={"document": ("newer.txt", self.text, "text/plain")},
        ).raise_for_status()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            tasks = results(stack.admin.get("/api/tasks/", params={"task_id": str(parse(reply))}))
            if tasks and tasks[0]["status"] in {"success", "failure"}:
                break
            time.sleep(1)
        self.archive: bytes = stack.admin.get(
            f"/api/documents/{document}/download/",
            params={"original": "false", "version": document},
        ).content
        assert self.archive != self.original


@pytest.fixture(scope="module")
def uploaded(stack: Stack) -> Iterator[Uploaded]:
    found = Uploaded(stack)
    try:
        yield found
    finally:
        _ = stack.admin.delete(f"/api/documents/{found.id}/")


def script(stack: Stack, settings: dict[str, str]) -> ClientTransport:
    """The stdio server as a process."""
    env = {
        "PAPERLESS_URL": stack.base_url,
        "PAPERLESS_TOKEN": stack.admin_token,
        "PAPERLESS_MCP_TOOLSETS": "core,admin",
        **settings,
    }
    return StdioTransport(console_script(), [], env=env, keep_alive=False)


@contextlib.contextmanager
def serving(transport: str, stack: Stack, root: Path) -> Generator[ClientTransport]:
    """The installed script over `transport`; the directory is configured in both cases."""
    settings = {"PAPERLESS_MCP_DOWNLOAD_DIRS": str(root)}
    if transport == "stdio":
        yield script(stack, settings)
        return
    with running(stack, PAPERLESS_MCP_TOOLSETS="core,admin", **settings) as url:
        yield StreamableHttpTransport(url, headers={"Authorization": f"Bearer {MCP_TOKEN}"})


def opened(result: CallToolResult) -> tuple[Json, bytes]:
    first, *rest = result.content
    assert isinstance(first, TextContent)
    data = b""
    for block in rest:
        assert isinstance(block, EmbeddedResource)
        assert isinstance(block.resource, BlobResourceContents)
        data += base64.b64decode(block.resource.blob)
    return cast("Json", json.loads(first.text)), data


async def all_parts(
    client: Client[ClientTransport], name: str, arguments: Json, size: int = 400
) -> bytes:
    """The file from its parts, with the arguments that `next` names."""
    collected, offset, again, count = b"", 0, dict[str, object](), 0
    while True:
        report, data = opened(
            await client.call_tool(
                name, {**arguments, **again, "offset": offset, "max_bytes": size}
            )
        )
        assert report["part_sha256"] == hashlib.sha256(data).hexdigest()
        collected, count = collected + data, count + 1
        again = {"checksum": report["checksum"]}
        if name == "download_document":
            again["version_id"] = report["version_id"]
        if report["next_offset"] is None:
            assert (report["checksum"], report["total_bytes"]) == (
                hashlib.sha256(collected).hexdigest(),
                len(collected),
            )
            assert count > 1
            return collected
        offset = cast("int", report["next_offset"])


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_every_kind_and_version_and_a_zip_are_read_whole_and_in_parts(
    stack: Stack, uploaded: Uploaded, transport: str, tmp_path: Path
) -> None:
    number = uploaded.id
    kinds: list[tuple[Json, bytes]] = [
        ({"document_id": number, "kind": "original", "version_id": number}, uploaded.original),
        ({"document_id": number, "kind": "archive", "version_id": number}, uploaded.archive),
        ({"document_id": number}, uploaded.text),
    ]
    zipped: Json = {"document_ids": [number, stack.seeded.public_document], "content": "both"}
    with serving(transport, stack, tmp_path) as server:
        async with Client(server) as client:
            for arguments, content in kinds:
                report, whole = opened(await client.call_tool("download_document", arguments))
                assert (report["delivered"], whole) == (True, content)
                assert await all_parts(client, "download_document", arguments) == content
            _, archive = opened(await client.call_tool("bulk_download", zipped))
            assert await all_parts(client, "bulk_download", zipped, 4000) == archive
            stale = await client.call_tool(
                "download_document",
                {"document_id": number, "version_id": number, "offset": 5, "checksum": "0" * 64},
                raise_on_error=False,
            )
            tools = {t.name: t for t in await client.list_tools()}

    with zipfile.ZipFile(io.BytesIO(archive)) as opened_zip:
        assert uploaded.text in [opened_zip.read(n) for n in opened_zip.namelist()]
    assert stale.is_error
    assert "start again with offset=0" in str(stale.content)
    for name in ("download_document", "bulk_download"):
        properties = cast("Json", tools[name].input_schema["properties"])
        assert ("save_dir" in properties) is (transport == "stdio")
    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240 -- a test looking at its directory


async def test_every_kind_and_version_and_a_zip_are_saved_over_stdio(
    stack: Stack, uploaded: Uploaded, tmp_path: Path
) -> None:
    number, where = uploaded.id, str(tmp_path)
    kinds: list[tuple[Json, bytes, str]] = [
        ({"kind": "original", "version_id": number}, uploaded.original, "original.pdf"),
        ({"kind": "archive", "version_id": number}, uploaded.archive, "archive.pdf"),
        ({}, uploaded.text, "newest.txt"),
    ]
    async with Client(script(stack, {"PAPERLESS_MCP_DOWNLOAD_DIRS": where})) as client:
        for arguments, content, name in kinds:
            given = {"document_id": number, "save_dir": where, "save_as": name, **arguments}
            report, _ = opened(await client.call_tool("download_document", given))
            assert (report["filename"], report["replaced"]) == (name, False)
            assert (tmp_path / name).read_bytes() == content
        proposed, _ = opened(
            await client.call_tool("download_document", {"document_id": number, "save_dir": where})
        )
        zipped: Json = {"document_ids": [number], "content": "originals", "save_dir": where}
        archive, _ = opened(await client.call_tool("bulk_download", zipped))

        before = (tmp_path / "archive.pdf").stat()
        again: Json = {
            "document_id": number,
            "version_id": number,
            "save_dir": where,
            "save_as": "archive.pdf",
        }
        refused = await client.call_tool("download_document", again, raise_on_error=False)
        assert refused.is_error
        assert "Confirming would replace the file 'archive.pdf'" in str(refused.content)
        assert (tmp_path / "archive.pdf").stat() == before
        refused = await client.call_tool("bulk_download", zipped, raise_on_error=False)
        assert "Confirming would replace the file 'documents.zip'" in str(refused.content)
        replaced, _ = opened(
            await client.call_tool("download_document", {**again, "confirm": True})
        )
        zipped_again, _ = opened(
            await client.call_tool("bulk_download", {**zipped, "confirm": True})
        )
        outside = await client.call_tool(
            "download_document", {**again, "save_dir": str(tmp_path.parent)}, raise_on_error=False
        )

    assert replaced["replaced"] is True
    assert zipped_again["replaced"] is True
    assert (tmp_path / "archive.pdf").read_bytes() == uploaded.original
    assert str(proposed["filename"]).endswith(".txt")
    assert archive["filename"] == "documents.zip"
    with zipfile.ZipFile(tmp_path / "documents.zip") as opened_zip:
        assert [opened_zip.read(n) for n in opened_zip.namelist()] == [uploaded.text]
    assert "No usable directory was found" in str(outside.content)
    names = sorted(p.name for p in tmp_path.iterdir())  # noqa: ASYNC240 -- a test looking at its directory
    assert names == sorted(
        ["original.pdf", "archive.pdf", "newest.txt", "documents.zip", str(proposed["filename"])]
    )


async def test_read_only_mode_and_no_directory_mean_nothing_is_saved(
    stack: Stack, tmp_path: Path
) -> None:
    cases: tuple[dict[str, str], ...] = (
        {},
        {"PAPERLESS_MCP_DOWNLOAD_DIRS": str(tmp_path), "PAPERLESS_MCP_READ_ONLY": "true"},
    )
    for settings in cases:
        async with Client(script(stack, settings)) as client:
            tools = {t.name: t for t in await client.list_tools()}
            properties = cast("Json", tools["download_document"].input_schema["properties"])
            assert "save_dir" not in properties
            report, data = opened(
                await client.call_tool(
                    "download_document", {"document_id": stack.seeded.public_document}
                )
            )
            assert report["delivered"] is True
            assert data.startswith(b"%PDF")

    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240 -- a test looking at its directory


async def test_a_document_whose_file_is_lost_is_read_and_the_file_said_to_be_missing(
    stack: Stack,
) -> None:
    title = f"plmcp105 {uuid.uuid4().hex[:12]}"
    _, number = consume(stack.admin, title, pdf(title))
    assert number is not None
    try:
        lose_file(stack.admin, number, "original")
        lose_file(stack.admin, number, "thumbnail")
        asked: Json = {"document_id": number}
        bulk: Json = {
            "document_ids": [stack.seeded.public_document, number],
            "content": "originals",
        }
        settings = {"PAPERLESS_MCP_TOOLSETS": "core,admin,pdf"}
        async with Client(script(stack, settings)) as client:
            found = await client.call_tool("get_document", {**asked, "include": ["metadata"]})
            _, archive = opened(
                await client.call_tool("download_document", {**asked, "kind": "archive"})
            )
            refusals = [
                str((await client.call_tool(name, arguments, raise_on_error=False)).content)
                for name, arguments in (
                    ("download_document", asked),
                    ("download_document", {**asked, "offset": 0}),
                    ("get_document_pages", asked),
                    ("get_document_thumbnail", asked),
                    ("bulk_download", bulk),
                    ("rotate_documents", {"document_ids": [number], "degrees": 90}),
                )
            ]
    finally:
        _ = stack.admin.delete(f"/api/documents/{number}/")

    assert found.structured_content is not None
    facts = cast("Json", found.structured_content["file_metadata"])
    assert facts["original_size"] is None
    assert facts["original_checksum"]
    assert archive.startswith(b"%PDF")
    for refusal in refusals:
        assert "Paperless cannot find" in refusal
        assert f"document {number}" in refusal
