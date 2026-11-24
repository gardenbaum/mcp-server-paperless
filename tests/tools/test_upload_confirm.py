"""A known file goes through every form of both upload tools with confirm=true (decision-47).

The dialog tests call the tools over the remote server with content only, so each form that takes
the file another way is called here: content and path, over stdio and HTTP.
"""

import base64
from typing import TYPE_CHECKING, Final

import pytest
from confirmation import KNOWN, YES, answering, source, world
from fastmcp import Client
from harness import serve

if TYPE_CHECKING:
    from pathlib import Path

    from confirmation import Json
    from fastmcp import FastMCP

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-47")]

TOOLS: Final = ("upload_document", "upload_document_version")
FORMS: Final = ("http content", "stdio content", "allowlist content", "allowlist path")


def served(form: str, folder: Path, *, writes: bool = True) -> FastMCP:
    fake = source(world(), writes=writes)
    if form.startswith("allowlist"):
        return serve(fake, toolsets=["all"], stdio=True, upload_dirs=[str(folder)])
    return serve(fake, toolsets=["all"], stdio=form.startswith("stdio"))


def arguments(form: str, tool: str, folder: Path) -> Json:
    given: Json = {"document_id": 14} if tool.endswith("version") else {}
    if form.endswith("path"):
        _ = (folder / "x.pdf").write_bytes(KNOWN)
        return {**given, "path": str(folder / "x.pdf")}
    return {**given, "content_base64": base64.b64encode(KNOWN).decode(), "filename": "x.pdf"}


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("form", FORMS)
async def test_confirm_uploads_a_known_file(form: str, tool: str, tmp_path: Path) -> None:
    given = arguments(form, tool, tmp_path)
    async with Client(served(form, tmp_path, writes=False)) as client:
        refused = await client.call_tool(tool, given, raise_on_error=False)
    async with Client(served(form, tmp_path)) as client:
        done = await client.call_tool(tool, {**given, "confirm": True}, raise_on_error=False)

    assert refused.is_error
    assert "although the same file is already in Paperless" in str(refused.content)
    assert not done.is_error, str(done.content)
    assert done.structured_content is not None
    assert done.structured_content["state"] == "success"


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("form", FORMS)
async def test_the_dialog_uploads_a_known_file(form: str, tool: str, tmp_path: Path) -> None:
    given = arguments(form, tool, tmp_path)
    seen: list[str] = []
    handler = answering("accept", YES, seen=seen)
    async with Client(served(form, tmp_path), elicitation_handler=handler) as client:
        done = await client.call_tool(tool, given, raise_on_error=False)

    assert len(seen) == 1
    assert not done.is_error, str(done.content)
