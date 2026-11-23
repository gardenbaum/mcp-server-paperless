"""Which tools are offered: toolset selection, read-only mode, disabled tools, capabilities.

Pure functions over the tables in ``_shared``; the composition root applies the results with
FastMCP's visibility (``enable``/``disable``). Every misspelt name is an ``InvalidConfiguration``
at startup, never a silently empty selection.
"""

import difflib
from typing import TYPE_CHECKING

from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.domain.instance import OffToolset, ToolsetGuide
from mcp_server_paperless.domain.toolsets import ALL_TOOLSETS as ALL

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from mcp.types import ToolAnnotations


# What the operator is told to change, for every way of running the server (PLMCP-82).
ENABLE = (
    "The operator, not you, switches one on: PAPERLESS_MCP_TOOLSETS={value} (or all) in the "
    "server's environment, then restart. Claude Code plugin: option Toolsets, restart Claude Code. "
    "Cursor plugin: no such option, add the server by hand. stdio: env of the server entry in the "
    "MCP client's config, restart the client. HTTP and image: environment of the process or "
    "container, restart the process or recreate the container."
)
CAPABILITY_LABELS = {"ai": "Paperless AI"}


def _suggestions(unknown: Collection[str], known: Collection[str]) -> str:
    """' Did you mean x?' for the unknown names that have a close known name."""
    matches = [
        match[0]
        for name in sorted(unknown)
        if (match := difflib.get_close_matches(name, known, n=1))
    ]
    return f" Did you mean {', '.join(matches)}?" if matches else ""


def selected_toolsets(names: Collection[str], known: Collection[str]) -> frozenset[str]:
    """The toolsets named by ``PAPERLESS_MCP_TOOLSETS``; ``all`` selects every known one."""
    if ALL in names:
        return frozenset(known)
    unknown = sorted(set(names) - set(known))
    if unknown:
        message = (
            f"Unknown toolset in PAPERLESS_MCP_TOOLSETS: {', '.join(unknown)}."
            f"{_suggestions(unknown, known)}"
            f" Known toolsets: {', '.join(sorted(known))}, or {ALL}."
        )
        raise InvalidConfiguration(message)
    return frozenset(names)


def hidden_tools(
    annotations: Mapping[str, ToolAnnotations], *, read_only: bool, disabled: Collection[str]
) -> frozenset[str]:
    """Tools never offered: the disabled ones, and in read-only mode all that may change data."""
    unknown = sorted(set(disabled) - set(annotations))
    if unknown:
        message = (
            f"Unknown tool in PAPERLESS_MCP_DISABLED_TOOLS: {', '.join(unknown)}."
            f"{_suggestions(unknown, annotations)}"
            f" Known tools: {', '.join(sorted(annotations))}."
        )
        raise InvalidConfiguration(message)
    changing = (
        {name for name, hints in annotations.items() if not hints.read_only_hint}
        if read_only
        else set[str]()
    )
    return frozenset(changing | set(disabled))


def unavailable_toolsets(*, ai_enabled: bool | None, requires: Mapping[str, str]) -> frozenset[str]:
    """Toolsets whose Paperless capability is known to be missing.

    ``requires`` maps a toolset to the capability it needs. Only a definite "no" hides a
    toolset: unknown (the user may not view the UI settings, or Paperless did not answer at
    startup) leaves it visible, and its tools report what is wrong when called.
    """
    missing = {"ai"} if ai_enabled is False else set[str]()
    return frozenset(toolset for toolset, capability in requires.items() if capability in missing)


def unchecked_toolsets(*, ai_enabled: bool | None, requires: Mapping[str, str]) -> frozenset[str]:
    """Toolsets whose Paperless capability is unknown: not hidden, but not confirmed either."""
    unknown = {"ai"} if ai_enabled is None else set[str]()
    return frozenset(toolset for toolset, capability in requires.items() if capability in unknown)


def enable_text(selected: Sequence[str], new: str) -> str:
    """What to set and restart to switch ``new`` on beside the ``selected`` toolsets."""
    return ENABLE.format(value=",".join((*selected, new)))


def toolset_guide(  # noqa: PLR0913 -- the tables and the four states a toolset can be in
    tool_toolsets: Mapping[str, str],
    summaries: Mapping[str, str],
    requires: Mapping[str, str],
    *,
    selected: Collection[str],
    hidden: Collection[str],
    unavailable: Collection[str],
    unchecked: Collection[str],
) -> ToolsetGuide:
    """The toolsets on and the ones the operator could switch on, as far as that adds a tool.

    A toolset that read-only mode, disabled tools or a missing capability leave without a tool
    is in neither list. One that is cut down lists the tools left instead of what it is for, so
    nothing excluded is described. A toolset whose capability could not be checked is listed with
    that gap.
    """
    order = list(dict.fromkeys(tool_toolsets.values()))
    every = {name: [t for t, ts in tool_toolsets.items() if ts == name] for name in order}
    left = {name: tuple(t for t in tools if t not in hidden) for name, tools in every.items()}
    chosen = [name for name in order if name in selected]
    on = tuple(name for name in chosen if left[name] and name not in unavailable)
    off = tuple(
        OffToolset(
            toolset=name,
            what=summaries.get(name, name)
            if len(left[name]) == len(every[name])
            else f"only {', '.join(left[name])}",
            tools=left[name],
            unchecked=CAPABILITY_LABELS.get(requires[name], requires[name])
            if name in unchecked
            else None,
        )
        for name in order
        if name not in selected and left[name] and name not in unavailable
    )
    return ToolsetGuide(on=on, off=off, enable=enable_text(chosen, off[0].toolset) if off else None)


def guide_text(guide: ToolsetGuide, *, overview: bool) -> str:
    """The guide as a paragraph of the server instructions; empty if no toolset is off."""
    if guide.enable is None:
        return ""
    off = "; ".join(
        f"{t.toolset} ({t.what}{f'; needs {t.unchecked}, not checked' if t.unchecked else ''})"
        for t in guide.off
    )
    where = " Their tools: get_instance_overview." if overview else ""
    return f"Toolsets on: {', '.join(guide.on)}. Off: {off}. {guide.enable}{where}"


def visible_tools(
    tool_toolsets: Mapping[str, str], toolsets: Collection[str], hidden: Collection[str]
) -> frozenset[str]:
    """The tools left once the selected toolsets and the hidden names are applied."""
    return frozenset(
        name
        for name, toolset in tool_toolsets.items()
        if toolset in toolsets and name not in hidden
    )
