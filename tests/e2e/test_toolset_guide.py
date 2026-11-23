"""The guide to the toolsets on Paperless 3.4.2: in process, and as a real stdio and HTTP server.

The stack of `just e2e` has Paperless AI off, so the capability is missing there (the guide must
not offer the ai toolset); the tests marked `ai_e2e` need an AI-enabled Paperless (`just e2e-ai`).
The setting the guide names is applied to a second real process, which then offers the tools the
guide promised.
"""

import itertools
import re
import shutil
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from guide_oracle import OVERVIEW, Seen, look, observe, verify
from harness import paperless_settings, server_settings

from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Sequence

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-41")]

SELECTIONS: dict[str, list[str] | None] = {
    "default": None,
    "core": ["core"],
    "core-ai-trash": ["core", "ai", "trash"],
    "all": ["all"],
    "trash": ["trash"],
    "raw": ["raw"],
}
DISABLED = {
    "none": (),
    "part-of-trash": ("restore_from_trash", "empty_trash"),
    "all-of-trash": ("list_trash", "restore_from_trash", "empty_trash"),
    "the-overview": (OVERVIEW,),
}


def token_of(stack: Stack, user: str) -> str:
    return {
        "admin": stack.admin_token,
        "reader": stack.reader_token,
        "blind": stack.blind_token,
    }[user]


async def live(
    stack: Stack,
    user: str,
    toolsets: Sequence[str] | None,
    *,
    read_only: bool,
    disabled_tools: Sequence[str],
) -> Seen | None:
    settings = server_settings(
        toolsets=toolsets, read_only=read_only, disabled_tools=disabled_tools
    )
    try:
        server = create_server(paperless_settings(stack.base_url, token_of(stack, user)), settings)
    except InvalidConfiguration:
        return None  # the settings alone leave no tool
    return await observe(server)


# Every combination for the superuser; the other two users (they see less, and all of them have
# the UI settings, so the AI flag reads false) with the selections and settings that differ most.
COMBINATIONS = [
    *itertools.product(("admin",), SELECTIONS, DISABLED, (False, True)),
    *itertools.product(
        ("reader", "blind"), ("default", "trash"), ("none", "the-overview"), (False, True)
    ),
]


@pytest.mark.parametrize(("user", "selection", "disabled", "read_only"), COMBINATIONS)
async def test_the_guide_matches_what_switching_the_toolsets_on_would_add_on_paperless(
    stack: Stack, user: str, selection: str, disabled: str, *, read_only: bool
) -> None:
    kept = DISABLED[disabled]
    current = await live(
        stack, user, SELECTIONS[selection], read_only=read_only, disabled_tools=kept
    )
    ceiling = await live(stack, user, ["all"], read_only=read_only, disabled_tools=kept)
    if current is None:
        return  # nothing is offered, so there is nothing to tell
    assert ceiling is not None
    verify(current, ceiling, capability="missing")


@pytest.mark.ai_e2e
@pytest.mark.parametrize("selection", ["default", "core", "all"])
async def test_the_guide_offers_the_ai_toolset_where_paperless_ai_is_on(
    stack: Stack, selection: str
) -> None:
    if stack.admin.get("/api/ui_settings/").json()["settings"]["ai_enabled"] is not True:
        pytest.skip("AI is disabled on this Paperless; `just e2e-ai` runs this against an AI one.")
    for read_only in (False, True):
        current = await live(
            stack, "admin", SELECTIONS[selection], read_only=read_only, disabled_tools=()
        )
        ceiling = await live(stack, "admin", ["all"], read_only=read_only, disabled_tools=())
        assert current is not None
        assert ceiling is not None
        verify(current, ceiling, capability="present")


def console_script() -> str:
    path = shutil.which("mcp-server-paperless")
    assert path, "the mcp-server-paperless console script is not installed (uv sync)"
    return path


async def over_stdio(stack: Stack, **env: str) -> Seen:
    transport = StdioTransport(
        console_script(),
        [],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token, **env},
        keep_alive=False,
    )
    async with Client(transport) as client:
        return await look(client)


async def test_the_setting_the_guide_names_switches_the_toolsets_on_after_a_restart_over_stdio(
    stack: Stack,
) -> None:
    seen = await over_stdio(stack)

    assert seen.guide is not None
    first = cast("list[dict[str, object]]", seen.guide["off"])[0]
    setting = re.search(r"PAPERLESS_MCP_TOOLSETS=(\S+)", seen.instructions)
    assert setting is not None, seen.instructions
    assert setting[1] == f"core,ai,{first['toolset']}"
    assert "stdio: env of the server entry in the MCP client's config, restart the client" in (
        seen.instructions
    )

    restarted = await over_stdio(stack, PAPERLESS_MCP_TOOLSETS=setting[1])

    assert restarted.tools == seen.tools | set(cast("list[str]", first["tools"]))
