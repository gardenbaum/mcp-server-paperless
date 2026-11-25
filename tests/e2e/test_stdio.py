"""Smoke test of the installed `mcp-server-paperless` script over stdio against the E2E Paperless.

This is how Claude Code runs the server (`claude mcp add`): a subprocess speaking MCP on
stdin/stdout, configured by environment variables, logging to stderr.
"""

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from guide_oracle import Seen, look
from hidden_names import EVERY, READING, named

from e2e.helpers import EXPECTED_VERSION
from mcp_server_paperless.tools._shared import ANNOTATIONS, TOOL_TOOLSETS

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]


def console_script() -> str:
    path = shutil.which("mcp-server-paperless")
    assert path, "the mcp-server-paperless console script is not installed (uv sync)"
    return path


async def test_claude_code_can_call_get_instance_overview_over_stdio(stack: Stack) -> None:
    transport = StdioTransport(
        console_script(),
        [],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token},
        keep_alive=False,
    )

    async with Client(transport) as client:
        result = await client.call_tool("get_instance_overview")

    output = result.structured_content
    assert output is not None
    assert (output["paperless_version"], output["api_version"]) == (EXPECTED_VERSION, 10)
    assert output["current_user"]["username"] == "admin"
    assert output["system_status"] is not None


@pytest.mark.decision("decision-2")
def test_a_wrong_token_stops_the_console_script_with_a_clear_message(stack: Stack) -> None:
    finished = subprocess.run(  # noqa: S603 -- our own console script, no shell
        [console_script()],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": "not-a-token"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert finished.returncode == 1
    assert finished.stdout == ""
    assert "mcp-server-paperless: Paperless rejected the credentials" in finished.stderr
    assert "Traceback" not in finished.stderr


async def listed_tools(stack: Stack, **env: str) -> set[str]:
    transport = StdioTransport(
        console_script(),
        [],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token, **env},
        keep_alive=False,
    )
    async with Client(transport) as client:
        return {tool.name for tool in await client.list_tools()}


@pytest.mark.decision("decision-4")
async def test_the_environment_selects_the_tools_over_stdio(stack: Stack) -> None:
    default = await listed_tools(stack)
    read_only = await listed_tools(
        stack, PAPERLESS_MCP_TOOLSETS="all", PAPERLESS_MCP_READ_ONLY="true"
    )
    trimmed = await listed_tools(stack, PAPERLESS_MCP_DISABLED_TOOLS="get_task, list_taxonomy")

    assert "get_instance_overview" in default
    writing = {
        "update_document",
        "add_note",
        "delete_note",
        "delete_document",
        "upload_document",
    } | {
        "create_taxonomy_item",
        "update_taxonomy_item",
        "delete_taxonomy_item",
    }
    # `all` adds the toolsets that are off by default: of versions, trash, sharing, permissions,
    # mail, workflows, bulk, tasks and admin only the listings and reads are read-only, of views the
    # listing and the read (running a view is in core, decision-34).
    views = {"list_saved_views", "get_saved_view"}
    mail = {
        *("list_mail_accounts", "get_mail_account", "list_mail_rules", "get_mail_rule"),
        *("list_processed_mail", "get_processed_mail", "explain_mail_import"),
    }
    listings = {
        *("list_document_versions", "list_share_links", "list_share_bundles"),
        *("list_trash", "get_trash_entry", "get_trashed_file"),
        *(
            "get_permissions",
            "list_users",
            "list_groups",
            "list_workflows",
            "get_workflow",
            "explain_workflow",
        ),
    }
    admin = {
        *("get_user", "get_group", "get_app_config", "list_tasks", "list_logs", "get_log"),
        *("bulk_download", "inspect_pdf", "read_documents"),
        *("suggest_words", "test_storage_path"),
        *("get_task_overview", "get_system_report"),
    }
    listings |= mail | admin | {"paperless_api_get"}
    assert read_only == (default - writing) | listings | views
    assert trimmed == default - {"get_task", "list_taxonomy"}


# --- A toolset read-only (PLMCP-90) --------------------------------------------------------------

MIXED = "core:read,mail:read,workflows,tasks:read"


async def offered(stack: Stack, mode: str = "auto", **env: str) -> tuple[Seen, set[str]]:
    """What the process offers, and its prompts."""
    transport = StdioTransport(
        console_script(),
        [],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token, **env},
        keep_alive=False,
    )
    entries = env["PAPERLESS_MCP_TOOLSETS"].split(",")
    hidden = {n for n in EVERY - READING if f"{TOOL_TOOLSETS.get(n)}:read" in entries}
    async with Client(transport, mode=mode) as client:
        for tool in await client.list_tools():  # nothing hidden is named (decision-111)
            if tool.name in READING:
                assert named(tool, hidden) == set(), tool.name
        return await look(client), {prompt.name for prompt in await client.list_prompts()}


def mixed_tools() -> set[str]:
    return {
        tool
        for tool, toolset in TOOL_TOOLSETS.items()
        if toolset == "workflows"
        or (toolset in {"core", "mail", "tasks"} and ANNOTATIONS[tool].read_only_hint)
    }


@pytest.mark.decision("decision-97")
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_a_mixed_selection_over_stdio(stack: Stack, mode: str) -> None:
    seen, prompts = await offered(stack, mode, PAPERLESS_MCP_TOOLSETS=MIXED)

    assert seen.tools == mixed_tools()
    assert prompts == {"find_document"}  # the others need a tool that writes (decision-20)
    assert (
        "Toolsets on: core (read-only), mail (read-only), workflows, tasks (read-only)."
        in seen.instructions
    )
    assert f"PAPERLESS_MCP_TOOLSETS={MIXED},<name>" in seen.instructions
    assert seen.guide is not None
    assert seen.guide["on"] == [
        "core (read-only)",
        "mail (read-only)",
        "workflows",
        "tasks (read-only)",
    ]


@pytest.mark.decision("decision-97")
async def test_following_the_guide_keeps_a_read_only_toolset_read_only(stack: Stack) -> None:
    before, _ = await offered(stack, PAPERLESS_MCP_TOOLSETS=MIXED)
    setting = before.instructions.split("PAPERLESS_MCP_TOOLSETS=")[1].split()[0]
    after, _ = await offered(stack, PAPERLESS_MCP_TOOLSETS=setting.replace("<name>", "raw"))

    assert after.tools == before.tools | {"paperless_api_get"}


@pytest.mark.decision("decision-97")
async def test_server_wide_read_only_mode_wins_over_stdio(stack: Stack) -> None:
    suffixed, _ = await offered(stack, PAPERLESS_MCP_TOOLSETS=MIXED, PAPERLESS_MCP_READ_ONLY="true")
    bare, _ = await offered(
        stack, PAPERLESS_MCP_TOOLSETS="core,mail,workflows,tasks", PAPERLESS_MCP_READ_ONLY="true"
    )

    assert suffixed.tools == bare.tools
    assert all(ANNOTATIONS[tool].read_only_hint for tool in suffixed.tools)


@pytest.mark.decision("decision-97")
@pytest.mark.parametrize(
    ("value", "says"),
    [
        ("core,mail:ro", "Did you mean mail:read?"),
        ("all:read", "PAPERLESS_MCP_READ_ONLY=true"),
        ("core,core:read", "both"),
        ("all,mail:read", "list every toolset"),
    ],
)
def test_a_wrong_read_only_selection_stops_the_console_script(
    stack: Stack, value: str, says: str
) -> None:
    finished = subprocess.run(  # noqa: S603 -- our own console script, no shell
        [console_script()],
        env={
            "PAPERLESS_URL": stack.base_url,
            "PAPERLESS_TOKEN": stack.admin_token,
            "PAPERLESS_MCP_TOOLSETS": value,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert finished.returncode == 1
    assert finished.stdout == ""
    assert says in finished.stderr
    assert "Traceback" not in finished.stderr
