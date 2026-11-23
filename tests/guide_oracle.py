"""What the guide to the toolsets must be, worked out from what the server really offers.

The oracle does not read the server's selection logic. It starts the server twice with the same
settings, once as configured and once with every toolset selected, and takes the tools the second
offers and the first does not as what switching the toolsets on adds. The tool-to-toolset table
(``TOOL_TOOLSETS``, which the registry tests hold equal to the tags) only groups those tools. The
guide in the instructions and in the overview must name exactly those toolsets and tools, say
`what` only where a toolset is complete, give the setting that keeps the selection, and name
nothing the settings or the capability exclude. Shared by the tool tests on the fake and the live
tests on Paperless 3.4.2.
"""

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastmcp import Client
from fastmcp.client.transports import ClientTransport
from harness import serve

from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.tools._shared import TOOL_TOOLSETS, TOOLSETS
from mcp_server_paperless.tools.selection import PARTIAL

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastmcp import FastMCP

    from mcp_server_paperless.domain.ports import InstanceReader

OVERVIEW = "get_instance_overview"
CANONICAL = list(dict.fromkeys(TOOL_TOOLSETS.values()))
# What the server selects when PAPERLESS_MCP_TOOLSETS is not set.
DEFAULT = ["core", "ai"]


def resolved(selection: Sequence[str] | None) -> list[str]:
    """The toolsets a setting selects, in the catalogue's order."""
    chosen = DEFAULT if selection is None else list(selection)
    return CANONICAL if "all" in chosen else [name for name in CANONICAL if name in chosen]


# The settings every combination is made of: what PAPERLESS_MCP_TOOLSETS selects (None: not set),
# the tools PAPERLESS_MCP_DISABLED_TOOLS names, and read-only mode.
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
COMBINATIONS = list(itertools.product(SELECTIONS, DISABLED, (False, True)))


def refused(selection: str, disabled: str) -> bool:
    """Whether the settings leave no tool at all: only the trash toolset, every tool disabled."""
    return (selection, disabled) == ("trash", "all-of-trash")


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


def verify(  # noqa: C901 -- one check per statement of the guide
    current: Seen, ceiling: Seen, *, capability: str, selected: Sequence[str]
) -> None:
    """Check the guide of ``current`` against ``ceiling``, the same server with every toolset.

    ``capability`` is what Paperless reported for AI: present, missing, unknown (the user may not
    see it) or silent (Paperless did not answer at startup). ``selected`` is what the setting
    selects (see ``resolved``).
    """
    text = current.instructions
    gain = ceiling.tools - current.tools
    # What is on, in the catalogue's order.
    on = [n for n in CANONICAL if any(TOOL_TOOLSETS[t] == n for t in current.tools)]
    off = [n for n in CANONICAL if any(TOOL_TOOLSETS[t] == n for t in gain)]
    gap = "needs Paperless AI, not checked"
    unchecked = capability in {"unknown", "silent"} and bool({"ai"} & {*on, *off})
    shown = [f"{n} ({gap})" if unchecked and n == "ai" else n for n in on]
    if gain or unchecked:
        assert f"Toolsets on: {', '.join(shown)}." in text
    if gain:
        assert f"PAPERLESS_MCP_TOOLSETS={','.join(resolved(selected))},<name>" in text
        assert text.index("PAPERLESS_MCP_TOOLSETS=") < text.index("Off: ")
        assert "restart" in text
    else:  # nothing to switch on: the guide says nothing about switching
        assert "PAPERLESS_MCP_TOOLSETS" not in text
    # No tool is named that the server does not offer now, no toolset that is neither on nor off.
    for tool in TOOL_TOOLSETS:
        if tool in text:
            assert tool in current.tools, tool
    listed = text.split("Off: ")[1] if "Off: " in text else ""
    for name in sorted(TOOLSETS):
        assert (f"{name} (" in listed) is (name in off), name
    assert (gap in listed) is (unchecked and "ai" in off)
    if current.guide is not None:
        assert current.guide["on"] == on
        assert (
            current.guide["offered_after_restart"] == []
        )  # the start and the answer now are the same here
        offered = cast("list[dict[str, object]]", current.guide["off"])
        assert [entry["toolset"] for entry in offered] == off
        for entry in offered:
            exact = {t for t in gain if TOOL_TOOLSETS[t] == entry["toolset"]}
            whole = {t for t in TOOL_TOOLSETS if TOOL_TOOLSETS[t] == entry["toolset"]}
            assert set(cast("list[str]", entry["tools"])) == exact
            assert (entry["what"] == PARTIAL) is (exact != whole), entry
            assert entry["what"]
        assert (current.guide["switch_on"] is not None) is bool(gain)
        if gain:
            assert cast("str", current.guide["switch_on"]) in text
        assert current.guide["unchecked"] == ({"ai": "Paperless AI"} if unchecked else {})
    if capability == "missing":
        assert "ai" not in off
        assert "ai" not in on
    if capability == "present":
        assert "not checked" not in text


async def started(
    reader: InstanceReader,
    toolsets: Sequence[str] | None,
    *,
    read_only: bool = False,
    disabled_tools: Sequence[str] = (),
    overview: bool = True,
) -> Seen | None:
    """What a client sees of the server on ``reader``, or None if nothing is left to offer."""
    try:
        server = serve(
            reader, toolsets=toolsets, read_only=read_only, disabled_tools=disabled_tools
        )
    except InvalidConfiguration:
        return None
    return await observe(server, overview=overview)


async def check(
    reader: Callable[[], InstanceReader],
    *,
    capability: str,
    combination: tuple[str, str, bool],
    ceilings: dict[tuple[str, bool], Seen | None] | None = None,
) -> None:
    """Start the server on a fresh reader for one combination of settings and verify its guide.

    ``capability`` is as in ``verify``; the overview is not called while Paperless is silent.
    The server with every toolset depends only on the disabled tools and read-only mode: pass one
    ``ceilings`` dict for the combinations on the same answer of Paperless to start it once.
    """
    selection, disabled, read_only = combination
    kept = DISABLED[disabled]
    overview = capability != "silent"
    current = await started(
        reader(), SELECTIONS[selection], read_only=read_only, disabled_tools=kept, overview=overview
    )
    cache = {} if ceilings is None else ceilings
    if (disabled, read_only) not in cache:
        cache[disabled, read_only] = await started(
            reader(), ["all"], read_only=read_only, disabled_tools=kept, overview=overview
        )
    ceiling = cache[disabled, read_only]
    assert (current is None) is refused(selection, disabled), combination
    if current is not None:
        assert ceiling is not None
        verify(current, ceiling, capability=capability, selected=resolved(SELECTIONS[selection]))
