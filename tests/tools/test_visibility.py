"""Toolset selection, read-only mode and capabilities on the assembled server.

The synthetic catalogue adds an ai tool of its own and a scratch toolset the server does not ship,
so that every rule is proven for toolsets of any size. The shipped ai, bulk and pdf toolsets are
covered in test_ai.py, test_bulk.py and test_pdf.py.
"""

from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from fakes.paperless import FakePaperless, FakeUser, seeded
from fastmcp import Client
from fastmcp.server.providers import LocalProvider
from harness import admin, paperless_settings, refused_url, serve, server_settings
from mcp.types import ToolAnnotations

from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.server import CATALOGUE, create_server
from mcp_server_paperless.tools._shared import (
    ANNOTATIONS,
    LOCAL_TOOLSETS,
    TOOL_TOOLSETS,
    TOOLSETS,
    boundary,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from fastmcp import FastMCP

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-4")]

READ = ToolAnnotations(title="R", read_only_hint=True)
WRITE = ToolAnnotations(title="W", destructive_hint=False)

extra = LocalProvider()


@extra.tool(tags={"ai"}, annotations=READ)
@boundary
async def ask_something() -> str:
    """Ask the AI."""
    return "answer"


@extra.tool(tags={"scratch"}, annotations=WRITE)
@boundary
async def scratch_change() -> str:
    """Change a PDF."""
    return "changed"


@extra.tool(tags={"scratch"}, annotations=READ)
@boundary
async def scratch_preview() -> str:
    """Preview a PDF change."""
    return "preview"


SYNTHETIC = replace(
    CATALOGUE,
    providers=(*CATALOGUE.providers, extra),
    annotations={
        **ANNOTATIONS,
        "ask_something": READ,
        "scratch_change": WRITE,
        "scratch_preview": READ,
    },
    tool_toolsets={
        **TOOL_TOOLSETS,
        "ask_something": "ai",
        "scratch_change": "scratch",
        "scratch_preview": "scratch",
    },
    toolsets=CATALOGUE.toolsets | {"ai", "scratch"},
    requires={"ai": "ai"},
)
CORE = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "core"}
SHIPPED_AI = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "ai"}
SHIPPED_VERSIONS = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "versions"}
SHIPPED_BULK = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "bulk"}
VIEWS = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "views"}
SHIPPED_PDF = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "pdf"}
READ_VIEWS = {"list_saved_views", "get_saved_view"}
SHIPPED_TRASH = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "trash"}
SHARING = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "sharing"}
READ_SHARING = {"list_share_links", "list_share_bundles"}
SHIPPED_PERMISSIONS = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "permissions"}
MAIL = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "mail"}
ADMIN = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "admin"}
READ_ADMIN = {
    *("get_user", "get_group", "get_app_config", "list_logs", "get_log"),
    "bulk_download",
}
TASKS = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "tasks"}
READ_TASKS = {"list_tasks", "get_task_overview", "get_system_report"}
RAW = {"paperless_api_get"}
READ_PDF = {"inspect_pdf"}
READ_BULK = {"read_documents", "suggest_words", "test_storage_path"}
READ_MAIL = {
    *("list_mail_accounts", "get_mail_account", "list_mail_rules", "get_mail_rule"),
    "list_processed_mail",
    "get_processed_mail",
    "explain_mail_import",
}
WORKFLOWS = {name for name, toolset in TOOL_TOOLSETS.items() if toolset == "workflows"}
READ_WORKFLOWS = {"list_workflows", "get_workflow", "explain_workflow"}


async def names(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in await client.list_tools()}


def with_ai(*, enabled: bool) -> FakePaperless:
    return admin(ai_enabled=enabled)


async def test_the_default_profile_offers_only_the_core_toolset() -> None:
    assert await names(serve(admin(), catalogue=SYNTHETIC)) == CORE


async def test_named_toolsets_are_offered_and_others_are_not() -> None:
    server = serve(admin(), catalogue=SYNTHETIC, toolsets=["scratch"])

    assert await names(server) == {"scratch_change", "scratch_preview"}


async def test_several_toolsets_combine() -> None:
    server = serve(admin(), catalogue=SYNTHETIC, toolsets=["core", "scratch"])

    assert await names(server) == CORE | {"scratch_change", "scratch_preview"}


async def test_all_offers_every_toolset_the_server_may_offer() -> None:
    server = serve(with_ai(enabled=True), catalogue=SYNTHETIC, toolsets=["all"])

    shipped = (
        CORE
        | SHIPPED_AI
        | SHIPPED_BULK
        | SHIPPED_VERSIONS
        | VIEWS
        | SHIPPED_TRASH
        | SHARING
        | MAIL
        | RAW
        | WORKFLOWS
        | ADMIN
        | TASKS
    )
    assert await names(server) == shipped | SHIPPED_PDF | SHIPPED_PERMISSIONS | {
        "ask_something",
        "scratch_change",
        "scratch_preview",
    }


async def test_a_toolset_not_selected_takes_its_resources_with_it() -> None:
    async with Client(serve(admin(), catalogue=SYNTHETIC, toolsets=["scratch"])) as client:
        templates = await client.list_resource_templates()

    assert templates == []


async def test_read_only_mode_offers_only_tools_with_the_read_only_hint() -> None:
    server = serve(with_ai(enabled=True), catalogue=SYNTHETIC, toolsets=["all"], read_only=True)

    # The tools of core that change data are hidden in read-only mode.
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
    expected = (
        (CORE - writing)
        | SHIPPED_AI
        | READ_VIEWS
        | {
            "list_document_versions",
            "list_trash",
            "get_trash_entry",
            "get_trashed_file",
            "list_users",
            "list_groups",
            "get_permissions",
        }
        | READ_SHARING
        | READ_MAIL
        | READ_PDF
        | READ_BULK
        | RAW
        | READ_WORKFLOWS
        | READ_ADMIN
        | READ_TASKS
        | {"ask_something", "scratch_preview"}
    )
    assert await names(server) == expected


async def test_disabled_tools_are_not_offered() -> None:
    server = serve(admin(), catalogue=SYNTHETIC, disabled_tools=["get_task", "list_taxonomy"])

    assert await names(server) == CORE - {"get_task", "list_taxonomy"}


async def test_an_unknown_toolset_stops_the_server_from_being_built() -> None:
    with pytest.raises(InvalidConfiguration, match="scratchh"):
        _ = serve(admin(), toolsets=["core", "scratchh"])


async def test_an_unknown_disabled_tool_stops_the_server_from_being_built() -> None:
    with pytest.raises(InvalidConfiguration, match="get_taks"):
        _ = serve(admin(), disabled_tools=["get_taks"])


async def test_disabling_every_tool_of_the_toolsets_stops_the_server_from_being_built() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = serve(
            admin(),
            catalogue=SYNTHETIC,
            toolsets=["scratch"],
            disabled_tools=["scratch_change", "scratch_preview"],
        )

    assert "No tools are left" in caught.value.message
    assert "PAPERLESS_MCP_TOOLSETS" in caught.value.message


async def test_read_only_mode_over_writing_tools_only_stops_the_server_from_being_built() -> None:
    writing_only = replace(
        SYNTHETIC, annotations={**SYNTHETIC.annotations, "scratch_preview": WRITE}
    )

    with pytest.raises(InvalidConfiguration, match="No tools are left"):
        _ = serve(admin(), catalogue=writing_only, toolsets=["scratch"], read_only=True)


async def test_the_zero_tool_check_does_not_wait_for_paperless() -> None:
    with pytest.raises(InvalidConfiguration, match="No tools are left"):
        _ = create_server(
            paperless_settings(refused_url()),
            server_settings(disabled_tools=list(ANNOTATIONS)),
        )


async def test_a_toolset_whose_capability_is_missing_is_hidden() -> None:
    server = serve(with_ai(enabled=False), catalogue=SYNTHETIC, toolsets=["core", "ai"])

    assert await names(server) == CORE


async def test_a_toolset_whose_capability_is_present_is_offered() -> None:
    server = serve(with_ai(enabled=True), catalogue=SYNTHETIC, toolsets=["core", "ai"])

    assert await names(server) == CORE | SHIPPED_AI | {"ask_something"}


async def test_an_unknown_capability_leaves_the_toolset_visible() -> None:
    state = seeded()
    state.ai_enabled = False
    state.users.append(FakeUser(id=9, username="no-settings", permissions=frozenset()))
    blind = FakePaperless(state, user="no-settings")  # ai_enabled reads as null

    server = serve(blind, catalogue=SYNTHETIC, toolsets=["core", "ai"])

    assert "ask_something" in await names(server)


async def test_an_unreachable_paperless_at_startup_leaves_the_toolset_visible() -> None:
    server = create_server(
        paperless_settings(refused_url()),
        server_settings(toolsets=["core", "ai"]),
        catalogue=SYNTHETIC,
    )

    assert "ask_something" in await names(server)


async def test_a_missing_capability_can_leave_nothing_and_stops_the_start() -> None:
    server = serve(with_ai(enabled=False), catalogue=SYNTHETIC, toolsets=["ai"])

    with pytest.raises(RuntimeError) as caught:
        async with Client(server):
            pass

    assert isinstance(caught.value.__cause__, InvalidConfiguration)


async def test_one_server_follows_the_capability_of_each_start() -> None:
    state = seeded()
    state.ai_enabled = False
    fake = FakePaperless(state, user="admin")
    server = serve(fake, catalogue=SYNTHETIC, toolsets=["core", "ai"])

    first = await names(server)
    state.ai_enabled = True
    second = await names(server)
    state.ai_enabled = False
    third = await names(server)

    assert ("ask_something" in first, "ask_something" in second, "ask_something" in third) == (
        False,
        True,
        False,
    )


# --- A toolset read-only (PLMCP-90) --------------------------------------------------------------

READING = {name for name, hints in ANNOTATIONS.items() if hints.read_only_hint}


# Written out, so that a new toolset or tool, or a changed hint, is noticed here.
READS = {
    "core": {
        *("get_instance_overview", "list_taxonomy", "search_documents", "get_task"),
        *("get_document", "get_document_content", "get_document_thumbnail", "get_document_pages"),
        *("run_saved_view", "download_document"),
    },
    "ai": {"ask_paperless_ai", "get_ai_suggestions"},
    "bulk": READ_BULK,
    "versions": {"list_document_versions"},
    "views": READ_VIEWS,
    "trash": {"list_trash", "get_trash_entry", "get_trashed_file"},
    "pdf": READ_PDF,
    "sharing": READ_SHARING,
    "permissions": {"list_users", "list_groups"},
    "mail": READ_MAIL,
    "raw": RAW,
    "workflows": READ_WORKFLOWS,
    "admin": READ_ADMIN,
    "tasks": READ_TASKS,
}


@pytest.mark.decision("decision-97")
async def test_each_toolset_read_only_alone_offers_exactly_its_reading_tools() -> None:
    assert set(READS) == TOOLSETS
    for toolset, reads in READS.items():
        server = serve(with_ai(enabled=True), toolsets=[f"{toolset}:read"])
        assert await names(server) == reads, toolset


def all_but(toolset: str) -> list[str]:
    """Every toolset, this one read-only."""
    return [f"{name}:read" if name == toolset else name for name in sorted(TOOLSETS)]


@pytest.mark.decision("decision-97")
@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("toolset", sorted(TOOLSETS))
async def test_a_read_only_toolset_keeps_its_reading_tools_and_the_others_keep_all(
    tmp_path: Path, toolset: str, *, local: bool
) -> None:
    """Over stdio with directories the local tool and the saving downloads are there, too."""
    directories = [str(tmp_path)] if local else []
    table = {**TOOL_TOOLSETS, **(LOCAL_TOOLSETS if local else {})}
    server = serve(
        with_ai(enabled=True),
        toolsets=all_but(toolset),
        stdio=local,
        upload_dirs=directories,
        download_dirs=directories,
    )

    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    assert set(tools) == {n for n, name in table.items() if name != toolset or n in READING}
    for name, tool in tools.items():
        if table[name] == toolset:  # what the client is told, not what the table says
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint, name


@pytest.mark.decision("decision-97")
async def test_a_read_only_toolset_is_selected_alone_and_beside_others() -> None:
    alone = serve(admin(), catalogue=SYNTHETIC, toolsets=["scratch:read"])
    mixed = serve(admin(), catalogue=SYNTHETIC, toolsets=["core", "scratch:read"])

    assert await names(alone) == {"scratch_preview"}
    assert await names(mixed) == CORE | {"scratch_preview"}


@pytest.mark.decision("decision-97")
async def test_server_wide_read_only_mode_wins_over_the_suffixes() -> None:
    selections: list[list[str]] = [
        ["core", "mail", "bulk"],
        ["core", "mail:read", "bulk"],
        ["core:read", "mail:read", "bulk:read"],
    ]
    offered = [
        await names(serve(admin(), toolsets=toolsets, read_only=True)) for toolsets in selections
    ]

    assert offered[0] == offered[1] == offered[2]
    assert offered[0] <= READING


@pytest.mark.decision("decision-97")
async def test_disabled_tools_work_on_top_of_a_read_only_toolset() -> None:
    server = serve(
        admin(), toolsets=["core:read", "mail:read"], disabled_tools=["get_mail_rule", "get_task"]
    )

    assert await names(server) == ((CORE | MAIL) & READING) - {"get_mail_rule", "get_task"}


@pytest.mark.decision("decision-97")
async def test_a_read_only_toolset_without_a_reading_tool_counts_as_none() -> None:
    writing_only = replace(
        SYNTHETIC, annotations={**SYNTHETIC.annotations, "scratch_preview": WRITE}
    )

    with pytest.raises(InvalidConfiguration, match="No tools are left"):
        _ = serve(admin(), catalogue=writing_only, toolsets=["scratch:read"])
    mixed = serve(admin(), catalogue=writing_only, toolsets=["core", "scratch:read"])

    async with Client(mixed) as client:
        assert {tool.name for tool in await client.list_tools()} == CORE
        assert "Toolsets on: core." in (client.instructions or "")


@pytest.mark.decision("decision-97")
async def test_a_tool_missing_from_the_table_of_toolsets_stops_a_read_only_selection() -> None:
    """A writing tool whose toolset is not known can not be told to be outside the selection."""
    unplaced = replace(SYNTHETIC, annotations={**SYNTHETIC.annotations, "unplaced": WRITE})

    with pytest.raises(KeyError, match="unplaced"):
        _ = serve(admin(), catalogue=unplaced, toolsets=["scratch:read"])


@pytest.mark.decision("decision-97")
@pytest.mark.parametrize(
    "toolsets", [["core:ro"], ["core", "mail:"], ["all:read"], ["core", "core:read"]]
)
async def test_a_misspelt_read_only_selection_stops_the_server_from_being_built(
    toolsets: Sequence[str],
) -> None:
    with pytest.raises(InvalidConfiguration, match="PAPERLESS_MCP_TOOLSETS"):
        _ = serve(admin(), toolsets=toolsets)


@pytest.mark.decision("decision-97")
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_calling_a_hidden_tool_is_refused_and_nothing_reaches_paperless(mode: str) -> None:
    """Also for a client that still has the list of a server that offered it."""
    fake = FakePaperless(seeded(), user="admin", allow_writes=True)
    before = deepcopy(fake.state)
    calls: list[tuple[str, dict[str, object]]] = [
        ("create_taxonomy_item", {"kind": "tag", "name": "x"}),
        ("delete_document", {"document_id": 1, "confirm": True}),
    ]

    async with Client(serve(fake, toolsets=["core:read", "bulk"]), mode=mode) as client:
        for name, arguments in calls:
            refused = await client.call_tool(name, arguments, raise_on_error=False)
            assert refused.is_error
            assert "Unknown tool" in str(refused.content)

    assert fake.state == before
    async with Client(serve(fake, toolsets=["core"]), mode=mode) as client:
        for name, arguments in calls:  # the arguments are right: it is the selection that refuses
            _ = await client.call_tool(name, arguments)
    assert fake.state != before


@pytest.mark.decision("decision-97")
async def test_resources_of_a_read_only_toolset_stay() -> None:
    async with Client(serve(admin(), toolsets=["core:read"])) as client:
        reading = await client.list_resource_templates()
    async with Client(serve(admin(), toolsets=["core"])) as client:
        everything = await client.list_resource_templates()

    assert reading != []
    assert [t.name for t in reading] == [t.name for t in everything]
