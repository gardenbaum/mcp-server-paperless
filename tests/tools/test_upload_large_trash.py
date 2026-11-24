"""A trash over 64 MiB: the upload tools send and say that it was not checked (decision-52)."""

import base64
from typing import TYPE_CHECKING, Final, cast, override

import pytest
from confirmation import KNOWN, YES, answering, world
from fakes.paperless import FakePaperless
from fastmcp import Client
from harness import serve

from mcp_server_paperless.domain.errors import (
    AnswerCompressed,
    AnswerTooLarge,
    PaperlessMcpError,
    UpstreamTimeout,
    UpstreamUnavailable,
)

if TYPE_CHECKING:
    from pathlib import Path

    from confirmation import Json
    from fastmcp import FastMCP

    from mcp_server_paperless.domain.trash import TrashPage

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-52")]

TOOLS: Final = ("upload_document", "upload_document_version", "upload_documents")
NEW: Final = b"%PDF-1.4 a new file"
GAP: Final = (
    "Trash: neither its documents nor its deleted versions were checked, because its entries "
    "(the first 500) with the text of their documents are larger than 64 MiB."
)


class Trash(FakePaperless):
    """The admin's fake whose trash fails with `error` when its entries are read."""

    def __init__(self, error: PaperlessMcpError) -> None:
        super().__init__(world(), user="admin", allow_writes=True)
        self.error: PaperlessMcpError = error

    @override
    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        if entries > 1:
            raise self.error
        return await super().read_trash(entries=entries, max_bytes=max_bytes)


def served(fake: Trash, folder: Path) -> FastMCP:
    return serve(fake, toolsets=["all"], stdio=True, upload_dirs=[str(folder)])


def arguments(tool: str, folder: Path, content: bytes) -> Json:
    if tool == "upload_documents":
        _ = (folder / "x.pdf").write_bytes(content)
        return {"paths": [str(folder / "x.pdf")]}
    given: Json = {"document_id": 14} if tool.endswith("version") else {}
    return {**given, "content_base64": base64.b64encode(content).decode(), "filename": "x.pdf"}


@pytest.mark.parametrize("tool", TOOLS)
async def test_a_new_file_is_uploaded_and_the_gap_is_named(tool: str, tmp_path: Path) -> None:
    fake = Trash(AnswerTooLarge("too large"))
    async with Client(served(fake, tmp_path)) as client:
        done = await client.call_tool(tool, arguments(tool, tmp_path, NEW), raise_on_error=False)

    assert not done.is_error, str(done.content)
    output = done.structured_content or {}
    named = output if tool == "upload_documents" else cast("Json", output["duplicates"])
    assert GAP in cast("list[str]", named["not_checked"])
    assert len(fake.state.tasks) == 1


@pytest.mark.parametrize("tool", TOOLS[:2])
async def test_the_dialog_for_a_known_file_names_the_gap(tool: str, tmp_path: Path) -> None:
    fake = Trash(AnswerTooLarge("too large"))
    seen: list[str] = []
    handler = answering("accept", YES, seen=seen)
    async with Client(served(fake, tmp_path), elicitation_handler=handler) as client:
        done = await client.call_tool(tool, arguments(tool, tmp_path, KNOWN), raise_on_error=False)

    [message] = seen
    assert GAP in message
    assert not done.is_error, str(done.content)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize(
    "error",
    [
        UpstreamTimeout("slow"),
        UpstreamUnavailable("HTTP 500", status=500),
        AnswerCompressed("compressed", hint="proxy"),
    ],
)
async def test_every_other_failure_still_refuses(
    tool: str, error: PaperlessMcpError, tmp_path: Path
) -> None:
    fake = Trash(error)
    given = arguments(tool, tmp_path, NEW)
    if tool != "upload_documents":
        given["confirm"] = True
    async with Client(served(fake, tmp_path)) as client:
        done = await client.call_tool(tool, given, raise_on_error=False)

    assert done.is_error
    assert fake.state.tasks == []
