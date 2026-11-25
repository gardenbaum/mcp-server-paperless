"""The pick dialog over stdio, both protocol eras, with the tools only stdio has (decision-109)."""

import os
import sys
from typing import TYPE_CHECKING, Literal, cast

import pytest
from choices import NOT_PICKED, SECOND, TWIN, doubled, picks
from confirmation import source
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from harness import serve

if TYPE_CHECKING:
    from pathlib import Path

    from choices import Dialogs
    from confirmation import Json
    from fastmcp.client.client import CallToolResult

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-109")]

type Mode = Literal["auto", "legacy"]
both_eras = pytest.mark.parametrize("mode", ["auto", "legacy"])

# The server over stdio with a fake Paperless behind it.
SERVER = """
import sys
sys.path[:0] = [{tests!r}]
from choices import doubled
from confirmation import source
from harness import serve
serve(
    source(doubled()), toolsets=["all"], stdio=True, upload_dirs=[{root!r}]
).run(transport="stdio", show_banner=False)
"""


def connection(root: Path) -> StdioTransport:
    tests = os.path.dirname(os.path.dirname(__file__))  # noqa: PTH120 -- for sys.path
    code = SERVER.format(tests=tests, root=str(root))
    return StdioTransport(sys.executable, ["-c", code], env={}, keep_alive=False)


def said(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


@both_eras
async def test_a_pick_over_stdio_counts_and_a_no_changes_nothing(
    tmp_path: Path, mode: Mode
) -> None:
    async def correspondent(client: Client[StdioTransport]) -> object:
        read = await client.call_tool("get_document", {"document_id": 10})
        return cast("Json", read.structured_content)["correspondent"]

    call: Json = {"document_id": 10, "correspondent": TWIN}
    for shown in (None, picks(None), picks(SECOND)):
        handler = shown.handler if shown else None
        async with Client(connection(tmp_path), mode=mode, elicitation_handler=handler) as client:
            result = await client.call_tool("update_document", call, raise_on_error=False)
            now = await correspondent(client)

        if shown and shown.picks == [str(SECOND)]:
            assert not result.is_error, said(result)
            assert now == {"id": SECOND, "name": "twin"}
        else:
            assert said(result).startswith("2 correspondents match 'TWIN'")
            assert said(result).endswith(NOT_PICKED) is (shown is not None)
            assert now is None


@both_eras
@pytest.mark.parametrize("tool", ["upload_document", "upload_documents"])
async def test_the_uploads_from_disk_ask_once_for_the_whole_call(
    tmp_path: Path, mode: Mode, tool: str
) -> None:
    paths = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for path in paths:
        _ = path.write_bytes(b"%PDF-1.4 " + path.name.encode())
    given: Json = (
        {"paths": [str(p) for p in paths]}
        if tool == "upload_documents"
        else {"path": str(paths[0])}
    )

    async def call(fake_writes: bool, shown: Dialogs) -> tuple[CallToolResult, list[str]]:  # noqa: FBT001 -- one flag
        fake = source(doubled(), writes=fake_writes)
        server = serve(fake, toolsets=["all"], stdio=True, upload_dirs=[str(tmp_path)])
        async with Client(server, mode=mode, elicitation_handler=shown.handler) as client:
            result = await client.call_tool(
                tool, {**given, "correspondent": TWIN}, raise_on_error=False
            )
        return result, [name for name, _, _ in fake.sent_writes]

    shown = picks(SECOND)
    done, sent = await call(fake_writes=True, shown=shown)
    no, none = await call(fake_writes=False, shown=picks(None))

    assert not done.is_error, said(done)
    assert len(shown.questions) == 1
    assert sent == ["upload_document"] * len(given.get("paths", [1]))  # pyright: ignore[reportArgumentType] -- a list
    assert said(no).endswith(NOT_PICKED)
    assert none == []
