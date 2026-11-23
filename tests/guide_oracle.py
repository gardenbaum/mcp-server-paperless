"""What the guide to the toolsets must be, worked out from what the server really offers.

The oracle does not read the server's tables. It starts the server twice with the same settings,
once as configured and once with every toolset selected, and takes the tools the second offers
and the first does not as what switching the toolsets on adds. The guide in the instructions and
in the overview must name exactly those, with nothing the settings or the capability exclude.
Shared by the tool tests on the fake and the live tests on Paperless 3.4.2.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastmcp import Client
from fastmcp.client.transports import ClientTransport

from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.tools._shared import TOOL_TOOLSETS, TOOLSETS

if TYPE_CHECKING:
    from fastmcp import FastMCP

OVERVIEW = "get_instance_overview"
CANONICAL = list(dict.fromkeys(TOOL_TOOLSETS.values()))


@dataclass(frozen=True)
class Seen:
    """What a client sees of one started server."""

    tools: frozenset[str]
    instructions: str
    guide: dict[str, object] | None  # the overview's guide; None while the overview is not callable


async def look[T: ClientTransport](client: Client[T], *, overview: bool = True) -> Seen:
    """What a connected client sees: the overview is called when the server offers it."""
    tools = frozenset(tool.name for tool in await client.list_tools())
    guide = None
    if OVERVIEW in tools and overview:
        result = await client.call_tool(OVERVIEW)
        guide = cast("dict[str, object]", (result.structured_content or {})["toolsets"])
    return Seen(tools, client.instructions or "", guide)


async def observe(server: FastMCP, *, overview: bool = True) -> Seen | None:
    """The tools, the instructions and the overview's guide; None if nothing is left to offer."""
    try:
        async with Client(server) as client:
            return await look(client, overview=overview)
    except InvalidConfiguration:
        return None  # the settings alone leave no tool
    except RuntimeError as error:
        if not isinstance(error.__cause__, InvalidConfiguration):
            raise
        return None  # the capability of this Paperless leaves no tool


def verify(current: Seen, ceiling: Seen, *, capability: str) -> None:  # noqa: C901 -- one check per statement of the guide
    """Check the guide of ``current`` against ``ceiling``, the same server with every toolset.

    ``capability`` is what Paperless reported for AI: present, missing, unknown (the user may not
    see it) or silent (Paperless did not answer at startup).
    """
    text = current.instructions
    gain = ceiling.tools - current.tools
    # What is on, in the catalogue's order.
    on = [n for n in CANONICAL if any(TOOL_TOOLSETS[t] == n for t in current.tools)]
    off = [n for n in CANONICAL if any(TOOL_TOOLSETS[t] == n for t in gain)]
    if gain:
        assert f"Toolsets on: {', '.join(on)}." in text
        assert "PAPERLESS_MCP_TOOLSETS=" in text
        assert "restart" in text
    else:  # nothing to switch on: the guide says nothing about switching
        assert "PAPERLESS_MCP_TOOLSETS" not in text
    # Nothing is named that the server cannot offer after the change.
    for tool in TOOL_TOOLSETS:
        if tool in text:
            assert tool in current.tools | gain, tool
    for name in sorted(TOOLSETS):
        assert (f"{name} (" in text) is (name in off), name
    if current.guide is not None:
        assert current.guide["on"] == on
        offered = cast("list[dict[str, object]]", current.guide["off"])
        assert [entry["toolset"] for entry in offered] == off
        for entry in offered:
            assert set(cast("list[str]", entry["tools"])) == {
                t for t in gain if TOOL_TOOLSETS[t] == entry["toolset"]
            }
        assert (current.guide["enable"] is not None) is bool(gain)
        if gain:
            assert cast("str", current.guide["enable"]) in text
    # A capability that could not be checked is shown as a gap; a missing one is not shown.
    if capability in {"unknown", "silent"} and "ai" in off:
        assert "needs Paperless AI, not checked" in text
    if capability == "missing":
        assert "ai" not in off
    if capability == "present":
        assert "not checked" not in text
