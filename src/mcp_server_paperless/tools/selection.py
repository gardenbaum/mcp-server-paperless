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
    from collections.abc import Collection, Mapping

    from mcp.types import ToolAnnotations


# What the operator is told to change, for every way of running the server (PLMCP-82). The new
# toolset is a placeholder, so that the text is no example to follow, and `all` is not offered.
ENABLE = (
    "The operator, not you, switches one on: ask the user, and do not edit the configuration "
    "yourself. Set PAPERLESS_MCP_TOOLSETS={value} (the current ones plus the one needed; not all, "
    "which offers every tool) in the server's environment, then restart. Claude Code plugin: "
    "option Toolsets, restart Claude Code. Cursor plugin: no such option, add the server by hand. "
    "stdio: env of the server entry in the MCP client's config (docker run -i: -e), restart the "
    "client. HTTP and image: environment of the process or container, restart the process or "
    "recreate the container."
)
# What a toolset is said to be when read-only mode or disabled tools cut it down: its summary
# would promise tools that are not there, and the instructions name no tool (the overview does).
PARTIAL = "some of its tools"
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


def toolset_guide(  # noqa: PLR0913 -- the tables and the states a toolset can be in
    tool_toolsets: Mapping[str, str],
    summaries: Mapping[str, str],
    requires: Mapping[str, str],
    *,
    selected: Collection[str],
    hidden: Collection[str],
    unavailable: Collection[str],
    unchecked: Collection[str],
    missing_now: Collection[str] | None = None,
) -> ToolsetGuide:
    """The toolsets on and the ones the operator could switch on, as far as that adds a tool.

    A toolset that read-only mode, disabled tools or a missing capability leave without a tool
    is in neither list. One that is cut down is described as such (``PARTIAL``), never by what it
    is for. A toolset whose capability could not be checked, on or off, is listed in
    ``unchecked``. ``unavailable`` is what the start found missing and decides what is on;
    ``missing_now`` (default: the same) is what Paperless says now and decides what a restart
    would deliver, so what is off.
    """
    now = unavailable if missing_now is None else missing_now
    order = list(dict.fromkeys(tool_toolsets.values()))
    every = {name: [t for t, ts in tool_toolsets.items() if ts == name] for name in order}
    left = {name: tuple(t for t in tools if t not in hidden) for name, tools in every.items()}
    chosen = [name for name in order if name in selected]
    on = tuple(name for name in chosen if left[name] and name not in unavailable)
    off = tuple(
        OffToolset(
            toolset=name,
            what=summaries.get(name, name) if len(left[name]) == len(every[name]) else PARTIAL,
            tools=left[name],
        )
        for name in order
        if name not in selected and left[name] and name not in now
    )
    shown = {*on, *(t.toolset for t in off)}
    return ToolsetGuide(
        on=on,
        off=off,
        switch_on=ENABLE.format(value=",".join((*chosen, "<name>"))) if off else None,
        unchecked={
            name: CAPABILITY_LABELS.get(requires[name], requires[name])
            for name in order
            if name in unchecked and name in shown
        },
    )


def guide_text(guide: ToolsetGuide, *, overview: bool) -> str:
    """The guide as a paragraph of the server instructions; empty if there is nothing to say.

    The setting comes before the list of toolsets, so that a cut text loses the list. No tool is
    named, so nothing that is excluded can be: the overview lists the tools.
    """
    if not guide.off and not guide.unchecked:
        return ""

    def gap(name: str) -> str:
        return f"needs {guide.unchecked[name]}, not checked" if name in guide.unchecked else ""

    on = ", ".join(f"{name} ({gap(name)})" if gap(name) else name for name in guide.on)
    parts = [f"Toolsets on: {on}."]
    if guide.switch_on:
        parts.append(guide.switch_on)
        parts.append(
            "Off: "
            + "; ".join(
                f"{t.toolset} ({'; '.join(filter(None, (t.what, gap(t.toolset))))})"
                for t in guide.off
            )
            + "."
        )
        if overview:
            parts.append("Their tools: get_instance_overview.")
    return " ".join(parts)


def visible_tools(
    tool_toolsets: Mapping[str, str], toolsets: Collection[str], hidden: Collection[str]
) -> frozenset[str]:
    """The tools left once the selected toolsets and the hidden names are applied."""
    return frozenset(
        name
        for name, toolset in tool_toolsets.items()
        if toolset in toolsets and name not in hidden
    )
