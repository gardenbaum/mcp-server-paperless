"""The guide to the toolsets on the E2E Paperless: every combination, and real processes.

What Paperless answers about AI is taken once from the live stack for each kind of user (the
overview the adapter returns), and every combination of selection, disabled tools and read-only
mode is then started in process on that answer: one live request a user instead of a Paperless
start per combination. The adapter's start (the probe) is exercised by the stdio and HTTP tests,
which start real processes, apply the setting the guide names and check that they then offer the
promised tools, and by one start per user in process with the real adapter.

The stack of `just e2e` has Paperless AI off; the tests turn it on for a moment through the
application configuration (`ai_enabled`, which needs no LLM backend to be reported) and restore it.
"""

import re
import shutil
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import live_backend
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from guide_oracle import COMBINATIONS, Seen, check, look, observe, resolved, verify
from harness import Recorded, paperless_settings, server_settings

from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from e2e.helpers import Stack
    from mcp_server_paperless.domain.instance import InstanceOverview

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-41")]

USERS = ("admin", "reader", "blind", "no-settings")


async def answers(stack: Stack) -> dict[str, InstanceOverview]:
    """What the live Paperless says to each kind of user; ``no-settings`` may not view the UI."""
    async with live_backend(stack) as live:
        return {
            "admin": await live.admin.instance.get_overview(),
            "reader": await live.reader.instance.get_overview(),
            "blind": await live.blind.instance.get_overview(),
            "no-settings": await live.restricted(frozenset()).instance.get_overview(),
        }


async def run_every_combination(overviews: dict[str, InstanceOverview], *, ai: bool) -> None:
    for user, overview in overviews.items():
        capability = "unknown" if user == "no-settings" else "present" if ai else "missing"
        assert (overview.ai_enabled is None) is (user == "no-settings"), user
        ceilings: dict[tuple[object, ...], Seen | None] = {}
        for combination in COMBINATIONS:
            await check(
                lambda overview=overview: Recorded(overview),
                capability=capability,
                combination=combination,
                ceilings=ceilings,
            )


async def test_every_combination_where_paperless_ai_is_off(stack: Stack) -> None:
    overviews = await answers(stack)

    assert overviews["admin"].ai_enabled is False
    await run_every_combination(overviews, ai=False)


async def test_every_combination_where_paperless_ai_is_on(stack: Stack) -> None:
    config = cast("list[dict[str, object]]", stack.admin.get("/api/config/").json())[0]
    path = f"/api/config/{config['id']}/"
    was = config["ai_enabled"]
    before = cast("dict[str, dict[str, object]]", stack.admin.get("/api/ui_settings/").json())[
        "settings"
    ]["ai_enabled"]
    _ = stack.admin.patch(path, json={"ai_enabled": True}).raise_for_status()
    try:
        overviews = await answers(stack)
    finally:
        _ = stack.admin.patch(path, json={"ai_enabled": was}).raise_for_status()

    assert overviews["admin"].ai_enabled is True
    await run_every_combination(overviews, ai=True)
    after = cast("dict[str, dict[str, object]]", stack.admin.get("/api/ui_settings/").json())
    assert after["settings"]["ai_enabled"] == before


@pytest.mark.parametrize("user", ["admin", "reader", "blind"])
async def test_the_real_adapter_starts_with_the_guide_for_each_user(
    stack: Stack, user: str
) -> None:
    token = {"admin": stack.admin_token, "reader": stack.reader_token, "blind": stack.blind_token}
    settings = paperless_settings(stack.base_url, token[user])
    current = await observe(create_server(settings, server_settings()))
    ceiling = await observe(create_server(settings, server_settings(toolsets=["all"])))

    assert current is not None
    assert ceiling is not None
    verify(current, ceiling, capability="missing", selected=resolved(None))


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


@pytest.mark.parametrize("wanted", ["trash", "raw"])
async def test_the_setting_the_guide_names_switches_the_toolset_wanted_on_over_stdio(
    stack: Stack, wanted: str
) -> None:
    seen = await over_stdio(stack)

    assert seen.guide is not None
    setting = re.search(r"PAPERLESS_MCP_TOOLSETS=(\S+)", seen.instructions)
    assert setting is not None, seen.instructions
    assert setting[1] == "core,ai,<name>"
    [entry] = [
        e for e in cast("list[dict[str, object]]", seen.guide["off"]) if e["toolset"] == wanted
    ]

    restarted = await over_stdio(stack, PAPERLESS_MCP_TOOLSETS=setting[1].replace("<name>", wanted))

    assert restarted.tools == seen.tools | set(cast("list[str]", entry["tools"]))
