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
from mcp_server_paperless.domain.toolsets import READ_SUFFIX

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from mcp.types import ToolAnnotations


# What the operator is told to change, for every way of running the server (PLMCP-82). The new
# toolset is a placeholder, so that the text is no example to follow, and `all` is not offered.
ENABLE = (
    "The operator, not you, switches one on: ask the user; do not edit the configuration "
    "yourself. The operator sets PAPERLESS_MCP_TOOLSETS={value} (the current ones plus the one "
    "needed, never `all`, which offers every tool) in the server's environment and "
    "restarts it. Claude Code plugin: option Toolsets, restart Claude Code. Cursor plugin: no "
    "such option, add the server by hand. stdio: env of the server entry in the MCP client's "
    "config (docker run -i: -e), restart the client. HTTP and image: environment, restart the "
    "process or recreate the container."
)
# What a toolset is said to be when read-only mode or disabled tools cut it down: its summary
# would promise tools that are not there, and the instructions name no tool (the overview does).
PARTIAL = "some of its tools"
# What follows the name of a toolset that is selected read-only, so that the assistant does not
# call its writing missing.
READ_ONLY_MARK = " (read-only)"
CAPABILITY_LABELS = {"ai": "Paperless AI"}


def _closest(name: str, known: Collection[str]) -> str | None:
    """The close known name of an unknown one."""
    return next(iter(difflib.get_close_matches(name, known, n=1)), None)


def _closest_entry(entry: str, known: Collection[str]) -> str | None:
    """The entry an unknown one may mean; never writable where a suffix may have been meant.

    What starts with a toolset's name means its reading, and so does what is not only letters.
    """
    started = [name for name in known if entry.startswith(name)]
    if started:
        return f"{max(started, key=len)}{READ_SUFFIX}"
    reading = [f"{name}{READ_SUFFIX}" for name in known]
    return _closest(entry, [*known, *reading] if entry.isalpha() else reading)


def _did_you_mean(matches: Iterable[str | None]) -> str:
    """' Did you mean x?' for the names found."""
    found = ", ".join(dict.fromkeys(filter(None, matches)))
    return f" Did you mean {found}?" if found else ""


def selected_toolsets(names: Collection[str], known: Collection[str]) -> frozenset[str]:
    """The toolsets named by ``PAPERLESS_MCP_TOOLSETS``; ``all`` selects every known one.

    An entry is a name or ``name:read`` (see ``read_only_toolsets``). Anything else stops the
    server: a suffix that is not understood must never mean writable (decision-97).
    """
    if f"{ALL}{READ_SUFFIX}" in names:
        message = (
            f"{ALL}{READ_SUFFIX} is not a selection of PAPERLESS_MCP_TOOLSETS. To offer every "
            f"toolset only for reading, set PAPERLESS_MCP_TOOLSETS={ALL} and "
            "PAPERLESS_MCP_READ_ONLY=true."
        )
        raise InvalidConfiguration(message)
    if ALL in names:
        return frozenset(known)
    reading = {f"{name}{READ_SUFFIX}" for name in known}
    unknown = sorted(set(names) - set(known) - reading)
    if unknown:
        message = (
            f"Unknown toolset in PAPERLESS_MCP_TOOLSETS: {', '.join(unknown)}."
            f"{_did_you_mean(_closest_entry(entry, known) for entry in unknown)}"
            f" Known toolsets: {', '.join(sorted(known))}, or {ALL}; "
            f"name{READ_SUFFIX} offers only the tools of a toolset that change nothing."
        )
        raise InvalidConfiguration(message)
    read_only = read_only_toolsets(names)
    both = sorted(read_only & set(names))
    if both:
        message = (
            f"PAPERLESS_MCP_TOOLSETS names a toolset both with and without {READ_SUFFIX}: "
            f"{', '.join(both)}. Write it once."
        )
        raise InvalidConfiguration(message)
    return frozenset(names) - reading | read_only


def read_only_toolsets(names: Collection[str]) -> frozenset[str]:
    """The toolsets ``PAPERLESS_MCP_TOOLSETS`` selects read-only (``name:read``)."""
    return frozenset(name.removesuffix(READ_SUFFIX) for name in names if name.endswith(READ_SUFFIX))


def hidden_tools(
    annotations: Mapping[str, ToolAnnotations],
    *,
    read_only: bool,
    disabled: Collection[str],
    read_only_toolsets: Collection[str],
    tool_toolsets: Mapping[str, str],
) -> frozenset[str]:
    """Tools never offered: the disabled ones, and those that may change data where that is off.

    It is off for every toolset in read-only mode, else for ``read_only_toolsets``.
    """
    unknown = sorted(set(disabled) - set(annotations))
    if unknown:
        message = (
            f"Unknown tool in PAPERLESS_MCP_DISABLED_TOOLS: {', '.join(unknown)}."
            f"{_did_you_mean(_closest(name, annotations) for name in unknown)}"
            f" Known tools: {', '.join(sorted(annotations))}."
        )
        raise InvalidConfiguration(message)
    changing = {
        name
        for name, hints in annotations.items()
        if not hints.read_only_hint
        and (read_only or (read_only_toolsets and tool_toolsets[name] in read_only_toolsets))
    }
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
    read_only: Collection[str] = (),
    mark: bool = True,
) -> ToolsetGuide:
    """The toolsets on and the ones the operator could switch on, as far as that adds a tool.

    A toolset that read-only mode, disabled tools or a missing capability leave without a tool is
    in neither list. A selected one that was hidden at the start and that a restart would offer
    is ``offered_after_restart``. One that is cut down is described as such (``PARTIAL``), never
    by what it is for. A toolset whose capability could not be checked, on, offered or off, is
    listed in ``unchecked``. ``unavailable`` is what the start found missing and decides what is
    on; ``missing_now`` (default: the same) is what Paperless says now and decides what a restart
    would deliver, so what is off. A selected toolset in ``read_only`` is marked so (unless not
    ``mark``: in read-only mode, where marking one would say that the others write), and the value
    to set keeps its suffix, so that following it makes nothing writable (decision-97).
    """
    now = unavailable if missing_now is None else missing_now
    order = list(dict.fromkeys(tool_toolsets.values()))
    every = {name: [t for t, ts in tool_toolsets.items() if ts == name] for name in order}
    left = {name: tuple(t for t in tools if t not in hidden) for name, tools in every.items()}
    chosen = [name for name in order if name in selected]

    def shown_as(name: str) -> str:
        return f"{name}{READ_ONLY_MARK}" if mark and name in read_only else name

    entries = [f"{name}{READ_SUFFIX}" if name in read_only else name for name in chosen]
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
    offered = tuple(
        name for name in chosen if left[name] and name in unavailable and name not in now
    )
    shown = {*on, *offered, *(t.toolset for t in off)}
    return ToolsetGuide(
        on=tuple(map(shown_as, on)),
        offered_after_restart=tuple(map(shown_as, offered)),
        off=off,
        switch_on=ENABLE.format(value=",".join((*entries, "<name>"))) if off else None,
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
    reading = {name for name in guide.on if name.endswith(READ_ONLY_MARK)}
    if not guide.off and not guide.unchecked and not reading:
        return ""

    def gap(name: str) -> str:
        return f"needs {guide.unchecked[name]}, not checked" if name in guide.unchecked else ""

    def state(shown: str) -> str:
        name = shown.removesuffix(READ_ONLY_MARK)
        notes = "; ".join(filter(None, ("read-only" if shown in reading else "", gap(name))))
        return f"{name} ({notes})" if notes else name

    on = ", ".join(map(state, guide.on))
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
