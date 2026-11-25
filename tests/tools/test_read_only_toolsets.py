"""A read-only toolset over the real transports, on both protocol eras (PLMCP-90, decision-97)."""

import contextlib
import os
import sys
from typing import TYPE_CHECKING, cast

import pytest
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from guide_oracle import look
from harness import serve
from hidden_names import READING, named
from test_http_uploads import DEFAULT, http_settings, listening

from mcp_server_paperless.app import build_app
from mcp_server_paperless.tools._shared import (
    ANNOTATIONS,
    LOCAL_TOOLSETS,
    SAVING_ANNOTATIONS,
    TOOL_TOOLSETS,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from fastmcp.client.transports import ClientTransport

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-97")]

SELECTION = ["core", "bulk:read", "mail:read", "admin:read", "tasks:read", "workflows"]
READ_ONLY = {"bulk", "mail", "admin", "tasks"}
VALUE = "PAPERLESS_MCP_TOOLSETS=core,bulk:read,mail:read,workflows,admin:read,tasks:read,<name>"
ON = (
    "Toolsets on: core, bulk (read-only), mail (read-only), workflows, admin (read-only), "
    "tasks (read-only)."
)
# The server over stdio with a fake Paperless behind it.
STDIO = """
import sys
sys.path[:0] = [{tests!r}]
from fakes.paperless import FakePaperless, seeded
from harness import serve
serve(
    FakePaperless(seeded(), user="admin", allow_writes=True),
    toolsets={selection!r},
    stdio=True,
    upload_dirs=[{root!r}],
    download_dirs=[{root!r}],
).run(transport="stdio", show_banner=False)
"""


@contextlib.contextmanager
def transport(kind: str, root: Path) -> Generator[ClientTransport]:
    """A connection to the server over HTTP, or over stdio with directories."""
    if kind == "stdio":
        tests = os.path.dirname(os.path.dirname(__file__))  # noqa: PTH120 -- for sys.path
        code = STDIO.format(tests=tests, selection=SELECTION, root=str(root))
        yield StdioTransport(sys.executable, ["-c", code], env={}, keep_alive=False)
        return
    server = serve(
        FakePaperless(seeded(), user="admin", allow_writes=True),
        toolsets=SELECTION,
        upload_dirs=[str(root)],
        download_dirs=[str(root)],
    )
    app = build_app(server, http_settings(with_token=False), max_upload_bytes=DEFAULT)
    with listening(app) as url:
        yield StreamableHttpTransport(url)


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("kind", ["stdio", "http"])
async def test_a_mixed_selection_offers_tells_and_refuses_the_same(
    tmp_path: Path, kind: str, mode: str
) -> None:
    table = {**TOOL_TOOLSETS, **(LOCAL_TOOLSETS if kind == "stdio" else {})}
    with transport(kind, tmp_path) as connection:
        async with Client(connection, mode=mode) as client:
            seen = await look(client)
            tools = await client.list_tools()
            hints = {tool.name: tool.annotations for tool in tools}
            prompts = {prompt.name for prompt in await client.list_prompts()}
            read = await client.call_tool("list_mail_rules", raise_on_error=False)
            written = await client.call_tool(
                "create_taxonomy_item", {"kind": "tag", "name": "new"}, raise_on_error=False
            )
            hidden = [
                await client.call_tool(name, arguments, raise_on_error=False)
                for name, arguments in (
                    ("delete_mail_rule", {"rule_id": 1, "confirm": True}),
                    ("upload_documents", {}),
                    ("acknowledge_tasks", {"task_ids": [1]}),
                    ("run_task", {"task_type": "sanity_check"}),
                )
            ]
            unsaved = await client.call_tool(
                "bulk_download",
                {"document_ids": [1], "save_dir": str(tmp_path)},
                raise_on_error=False,
            )

    assert seen.tools == {
        tool
        for tool, toolset in table.items()
        if toolset in {"core", "workflows"}
        or (toolset in READ_ONLY and tool in ANNOTATIONS and ANNOTATIONS[tool].read_only_hint)
    }
    gone = {t for t in table if table[t] in READ_ONLY} - seen.tools
    for tool in tools:  # what is hidden is not named either (decision-111)
        if tool.name in READING:
            assert named(tool, gone) == set(), tool.name
    # Only core still saves, and only over stdio.
    saving = SAVING_ANNOTATIONS if kind == "stdio" else ANNOTATIONS
    assert hints["download_document"] == saving["download_document"]
    assert hints["bulk_download"] == ANNOTATIONS["bulk_download"]
    assert ON in seen.instructions
    assert VALUE in seen.instructions
    assert seen.guide is not None
    assert VALUE in cast("str", seen.guide["switch_on"])
    assert "triage_inbox" in prompts
    assert not read.is_error
    assert not written.is_error
    assert all(result.is_error and "Unknown tool" in str(result.content) for result in hidden)
    assert unsaved.is_error  # the tool is there, the parameter is not
    assert "save_dir" in str(unsaved.content)
    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240 -- a test looking at its directory
