"""download_document and bulk_download in parts and saved, through the in-memory client."""

import base64
import hashlib
import json
from typing import TYPE_CHECKING, Literal, cast

import pytest
from fakes.paperless import FakeDocument, FakePaperless, seeded
from fastmcp import Client
from fastmcp.server.context import (
    _current_transport,  # pyright: ignore[reportPrivateUsage] -- the seam FastMCP itself sets
)
from harness import serve
from mcp.types import BlobResourceContents, EmbeddedResource, TextContent

from mcp_server_paperless.tools._shared import ANNOTATIONS, SAVING_ANNOTATIONS

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-45")]

type Json = dict[str, object]

LIMIT = 64
ORIGINAL = b"%PDF-1.4 " + bytes(range(150))
DOCUMENT: Json = {"document_id": 7}
ARCHIVE: Json = {"document_ids": [1, 7], "content": "originals"}


def fake() -> FakePaperless:
    state = seeded()
    state.documents.append(
        FakeDocument(id=7, title="Müller €", original=ORIGINAL, archive=b"%PDF archive")
    )
    return FakePaperless(state, user="admin")


def plain(paperless: FakePaperless | None = None) -> FastMCP:
    return serve(paperless or fake(), toolsets=["core", "admin"], max_download_bytes=LIMIT)


def saving(root: Path, paperless: FakePaperless | None = None) -> FastMCP:
    return serve(
        paperless or fake(),
        toolsets=["core", "admin"],
        max_download_bytes=LIMIT,
        stdio=True,
        download_dirs=[str(root)],
    )


async def call(server: FastMCP, name: str, arguments: Json) -> tuple[Json, EmbeddedResource | None]:
    async with Client(server) as client:
        result = await client.call_tool(name, arguments)
    assert result.structured_content is None  # decision-11: content blocks only
    first, *rest = result.content
    assert isinstance(first, TextContent)
    embedded = rest[0] if rest else None
    assert embedded is None or isinstance(embedded, EmbeddedResource)
    return cast("Json", json.loads(first.text)), embedded


async def refused(server: FastMCP, name: str, arguments: Json) -> str:
    async with Client(server) as client:
        result = await client.call_tool(name, arguments, raise_on_error=False)
    assert result.is_error
    [text] = result.content
    assert isinstance(text, TextContent)
    return text.text


def bytes_of(embedded: EmbeddedResource | None) -> bytes:
    assert embedded is not None
    assert isinstance(embedded.resource, BlobResourceContents)
    assert embedded.resource.mime_type == "application/octet-stream"
    return base64.b64decode(embedded.resource.blob)


@pytest.mark.parametrize("variant", ["plain", "saving"])
async def test_a_file_over_the_limit_is_read_in_parts(variant: str, tmp_path: Path) -> None:
    server = plain() if variant == "plain" else saving(tmp_path)
    collected, offset, again = b"", 0, dict[str, object]()
    while True:
        report, embedded = await call(
            server, "download_document", {**DOCUMENT, "offset": offset, **again}
        )
        part = bytes_of(embedded)
        assert len(part) <= LIMIT
        assert embedded is not None
        assert str(embedded.resource.uri) == (
            f"paperless://documents/7/versions/7/original?offset={offset}&length={len(part)}"
        )
        assert (embedded.resource.meta or {})["filename"] == report["filename"]
        collected += part
        again = {"version_id": report["version_id"], "checksum": report["checksum"]}
        if report["next_offset"] is None:
            break
        offset = cast("int", report["next_offset"])

    assert collected == ORIGINAL
    assert report["checksum"] == hashlib.sha256(ORIGINAL).hexdigest()
    assert "part_sha256" in report


@pytest.mark.parametrize("variant", ["plain", "saving"])
async def test_an_archive_over_the_limit_is_read_in_parts(variant: str, tmp_path: Path) -> None:
    server = plain() if variant == "plain" else saving(tmp_path)
    collected, offset, again = b"", 0, dict[str, object]()
    uris: set[str] = set()
    while True:
        report, embedded = await call(
            server, "bulk_download", {**ARCHIVE, "offset": offset, "max_bytes": 50, **again}
        )
        part = bytes_of(embedded)
        assert len(part) <= 50
        assert embedded is not None
        uris.add(str(embedded.resource.uri))
        assert str(embedded.resource.uri).endswith(
            f"/documents.zip?offset={offset}&length={len(part)}"
        )
        collected += part
        again = {"checksum": report["checksum"]}
        if report["next_offset"] is None:
            break
        offset = cast("int", report["next_offset"])

    assert report["checksum"] == hashlib.sha256(collected).hexdigest()
    assert len(uris) > 2


@pytest.mark.parametrize("name", ["download_document", "bulk_download"])
async def test_a_part_past_the_end_has_a_report_and_no_content(name: str) -> None:
    server = plain()
    arguments = DOCUMENT if name == "download_document" else ARCHIVE
    first, _ = await call(server, name, {**arguments, "offset": 0})
    more = {"version_id": 7} if name == "download_document" else {}

    report, embedded = await call(
        server, name, {**arguments, **more, "offset": 10_000, "checksum": first["checksum"]}
    )

    assert (report["length"], report["next_offset"], embedded) == (0, None, None)


async def test_a_whole_file_of_a_named_version_has_a_uri_of_its_own() -> None:
    paperless = fake()
    paperless.state.documents[-1].original = b"small"

    newest, plain_one = await call(plain(paperless), "download_document", DOCUMENT)
    named, versioned = await call(
        plain(paperless), "download_document", {**DOCUMENT, "version_id": 7, "kind": "archive"}
    )

    assert plain_one is not None
    assert versioned is not None
    assert str(plain_one.resource.uri) == "paperless://documents/7/original"
    assert str(versioned.resource.uri) == "paperless://documents/7/versions/7/archive"
    assert (newest["version_id"], named["version_id"]) == (None, 7)


@pytest.mark.parametrize("variant", ["plain", "saving"])
@pytest.mark.parametrize("name", ["download_document", "bulk_download"])
@pytest.mark.parametrize("arguments", [{"max_bytes": 5}, {"checksum": "x"}])
async def test_the_arguments_of_a_part_without_offset_are_refused(
    variant: str, name: str, arguments: Json, tmp_path: Path
) -> None:
    server = plain() if variant == "plain" else saving(tmp_path)
    of = DOCUMENT if name == "download_document" else ARCHIVE

    text = await refused(server, name, {**of, **arguments})

    assert text.startswith("max_bytes and checksum are for reading a file in parts.")


# --- saving


async def test_a_document_is_saved_and_replaced_only_with_confirm(tmp_path: Path) -> None:
    server = saving(tmp_path)
    arguments: Json = {
        **DOCUMENT,
        "kind": "archive",
        "version_id": 7,
        "save_dir": str(tmp_path),
        "save_as": "x.pdf",
    }

    report, embedded = await call(server, "download_document", arguments)
    text = await refused(server, "download_document", arguments)
    again, _ = await call(server, "download_document", {**arguments, "confirm": True})

    assert embedded is None
    assert report == {
        "document_id": 7,
        "kind": "archive",
        "version_id": 7,
        "directory": str(tmp_path),
        "filename": "x.pdf",
        "size_bytes": 12,
        "sha256": hashlib.sha256(b"%PDF archive").hexdigest(),
        "replaced": False,
        "web_url": "http://paperless.test/documents/7/details",
    }
    assert "Confirming would replace the file 'x.pdf' (12 bytes" in text
    assert again["replaced"] is True
    assert (tmp_path / "x.pdf").read_bytes() == b"%PDF archive"


async def test_an_archive_is_saved_and_replaced_only_with_confirm(tmp_path: Path) -> None:
    server = saving(tmp_path)
    arguments: Json = {**ARCHIVE, "compression": "deflated", "save_dir": str(tmp_path)}

    report, embedded = await call(server, "bulk_download", arguments)
    text = await refused(server, "bulk_download", arguments)
    again, _ = await call(server, "bulk_download", {**arguments, "confirm": True})

    assert embedded is None
    assert (report["filename"], report["replaced"]) == ("documents.zip", False)
    assert (report["document_ids"], report["content"]) == ([1, 7], "originals")
    assert "Confirming would replace the file 'documents.zip'" in text
    assert again["replaced"] is True
    assert hashlib.sha256((tmp_path / "documents.zip").read_bytes()).hexdigest() == again["sha256"]


@pytest.mark.parametrize("name", ["download_document", "bulk_download"])
async def test_saving_and_parts_do_not_go_together(name: str, tmp_path: Path) -> None:
    of = DOCUMENT if name == "download_document" else ARCHIVE
    for given in ({"offset": 0}, {"max_bytes": 1}, {"checksum": "x"}):
        text = await refused(saving(tmp_path), name, {**of, "save_dir": str(tmp_path), **given})
        assert text == (
            "save_dir does not go with offset, max_bytes or checksum. Hint: A file is saved whole; "
            "leave them out, or leave out save_dir to read it in parts."
        )

    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240 -- a test looking at its directory


@pytest.mark.parametrize("name", ["download_document", "bulk_download"])
async def test_a_name_or_a_confirmation_needs_the_directory(name: str, tmp_path: Path) -> None:
    of = DOCUMENT if name == "download_document" else ARCHIVE
    for given in ({"save_as": "x.pdf"}, {"confirm": True}):
        text = await refused(saving(tmp_path), name, {**of, **given})
        assert text == (
            "save_as and confirm are for saving a file. "
            "Hint: Give save_dir, too, or leave them out."
        )


@pytest.mark.parametrize("name", ["download_document", "bulk_download"])
async def test_without_a_directory_the_saving_variant_attaches_as_usual(
    name: str, tmp_path: Path
) -> None:
    paperless = fake()
    paperless.state.documents[-1].original = b"small"
    arguments: Json = DOCUMENT if name == "download_document" else {"document_ids": [7]}

    expected = await call(plain(paperless), name, arguments)
    found = await call(saving(tmp_path, paperless), name, arguments)

    if name == "download_document":
        assert found == expected
    else:  # the ZIP is over the limit: its note names the way that only this variant has
        assert "save_dir" in str(found[0]["note"])
        assert "save_dir" not in str(expected[0]["note"])


@pytest.mark.parametrize("name", ["download_document", "bulk_download"])
@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
async def test_a_directory_is_refused_when_the_request_came_over_http(
    name: str, transport: Literal["sse", "streamable-http"], tmp_path: Path
) -> None:
    """The second lock: the server never registers this variant over HTTP."""
    of = DOCUMENT if name == "download_document" else ARCHIVE
    server = saving(tmp_path)
    token = _current_transport.set(transport)
    try:
        text = await refused(server, name, {**of, "save_dir": str(tmp_path)})
        report, _ = await call(server, name, {**of, "offset": 0})
    finally:
        _current_transport.reset(token)

    assert text == (
        "A path can only be used when the server runs over stdio. Hint: Read the file in parts "
        "with offset."
    )
    assert report["offset"] == 0  # parts still work: only a path needs stdio
    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240 -- a test looking at its directory


# --- which variant is offered


@pytest.mark.parametrize(
    ("stdio", "configured", "read_only", "saves"),
    [
        (False, True, False, False),  # HTTP never, whatever is configured
        (True, False, False, False),
        (True, True, False, True),
        (True, True, True, False),
    ],
)
async def test_the_saving_variants_exist_only_over_stdio_with_directories_and_writing_allowed(
    tmp_path: Path, *, stdio: bool, configured: bool, read_only: bool, saves: bool
) -> None:
    server = serve(
        fake(),
        toolsets=["all"],
        stdio=stdio,
        download_dirs=[str(tmp_path)] if configured else [],
        read_only=read_only,
        max_download_bytes=LIMIT,
    )
    tools = {t.name: t for t in await server.list_tools()}

    for name in ("download_document", "bulk_download"):
        properties = cast("Json", tools[name].parameters["properties"])
        assert ({"save_dir", "save_as", "confirm"} <= set(properties)) is saves
        assert ("save_dir" in properties) is saves
        assert {"offset", "max_bytes", "checksum"} <= set(properties)
        assert tools[name].annotations == (SAVING_ANNOTATIONS if saves else ANNOTATIONS)[name]
        assert ("not saved on the server's machine" in (tools[name].description or "")) is not saves
    report, _ = await call(server, "download_document", DOCUMENT)
    assert ("save_dir" in str(report["note"])) is saves


def test_saving_is_not_read_only_and_may_replace() -> None:
    assert set(SAVING_ANNOTATIONS) == {"download_document", "bulk_download"}
    for name, hints in SAVING_ANNOTATIONS.items():
        assert hints.title == ANNOTATIONS[name].title
        assert (hints.read_only_hint, hints.destructive_hint) == (False, True)
        assert (hints.idempotent_hint, hints.open_world_hint) == (False, False)
        assert ANNOTATIONS[name].read_only_hint is True


async def test_uploads_and_downloads_are_switched_on_independently(tmp_path: Path) -> None:
    where = [str(tmp_path)]
    for uploads, downloads in ((where, []), ([], where)):
        server = serve(
            fake(), toolsets=["all"], stdio=True, upload_dirs=uploads, download_dirs=downloads
        )
        tools = {t.name: t.parameters["properties"] for t in await server.list_tools()}
        assert ("path" in tools["upload_document"]) is bool(uploads)
        assert ("save_dir" in tools["download_document"]) is bool(downloads)
