"""Which tools are offered: toolset selection, read-only mode, disabled tools, capabilities.

The functions are pure, so these tests run them over synthetic tables with toolsets that do not
exist yet (``ai``, ``bulk``): the mechanism is proven for the toolsets later tasks add.
"""

from typing import TYPE_CHECKING

import pytest
from mcp.types import ToolAnnotations

from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.tools.selection import (
    enable_text,
    hidden_tools,
    selected_toolsets,
    toolset_guide,
    unavailable_toolsets,
    unchecked_toolsets,
    visible_tools,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mcp_server_paperless.domain.instance import ToolsetGuide

pytestmark = pytest.mark.decision("decision-4")

KNOWN = frozenset({"core", "ai", "bulk"})
READ = ToolAnnotations(title="R", read_only_hint=True)
WRITE = ToolAnnotations(title="W", read_only_hint=False)
TABLE: Mapping[str, ToolAnnotations] = {"find": READ, "change": WRITE, "plain": ToolAnnotations()}


def test_named_toolsets_are_selected() -> None:
    assert selected_toolsets(("core", "bulk"), KNOWN) == {"core", "bulk"}


def test_all_selects_every_toolset() -> None:
    assert selected_toolsets(("all",), KNOWN) == KNOWN


def test_an_unknown_toolset_is_an_error_naming_it_and_the_known_ones() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = selected_toolsets(("core", "pdff"), KNOWN)

    assert "pdff" in caught.value.message
    assert "ai, bulk, core" in caught.value.message
    assert "PAPERLESS_MCP_TOOLSETS" in caught.value.message


def test_read_only_hides_every_tool_without_the_read_only_hint() -> None:
    assert hidden_tools(TABLE, read_only=True, disabled=()) == {"change", "plain"}


def test_without_read_only_nothing_is_hidden() -> None:
    assert hidden_tools(TABLE, read_only=False, disabled=()) == frozenset()


def test_disabled_tools_are_hidden() -> None:
    assert hidden_tools(TABLE, read_only=False, disabled=("find",)) == {"find"}


def test_disabled_and_read_only_combine() -> None:
    assert hidden_tools(TABLE, read_only=True, disabled=("find",)) == {"find", "change", "plain"}


def test_an_unknown_disabled_tool_is_an_error_naming_it() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = hidden_tools(TABLE, read_only=False, disabled=("find", "fnid"))

    assert "fnid" in caught.value.message
    assert "PAPERLESS_MCP_DISABLED_TOOLS" in caught.value.message


REQUIRES = {"ai": "ai"}


@pytest.mark.parametrize(
    ("ai_enabled", "hidden"),
    [(False, {"ai"}), (True, set[str]()), (None, set[str]())],
)
def test_a_toolset_is_unavailable_only_when_its_capability_is_known_missing(
    *, ai_enabled: bool | None, hidden: set[str]
) -> None:
    assert unavailable_toolsets(ai_enabled=ai_enabled, requires=REQUIRES) == hidden


def test_toolsets_without_a_requirement_are_always_available() -> None:
    assert unavailable_toolsets(ai_enabled=False, requires={}) == frozenset()


def test_an_unreachable_paperless_leaves_every_toolset_visible() -> None:
    assert unavailable_toolsets(ai_enabled=None, requires=REQUIRES) == frozenset()


def test_an_unknown_name_suggests_the_close_match_and_lists_the_known_ones() -> None:
    with pytest.raises(InvalidConfiguration) as tool:
        _ = hidden_tools(TABLE, read_only=False, disabled=("fnid",))
    with pytest.raises(InvalidConfiguration) as toolset:
        _ = selected_toolsets(("cor",), KNOWN)

    assert "Did you mean find?" in tool.value.message
    assert "change, find, plain" in tool.value.message
    assert "Did you mean core?" in toolset.value.message


TOOL_TOOLSETS = {"find": "core", "change": "core", "other": "bulk"}


def test_visible_tools_follow_toolsets_and_hidden_names() -> None:
    assert visible_tools(TOOL_TOOLSETS, {"core"}, {"change"}) == {"find"}
    assert visible_tools(TOOL_TOOLSETS, {"core", "bulk"}, set[str]()) == set(TOOL_TOOLSETS)
    assert visible_tools(TOOL_TOOLSETS, {"bulk"}, {"other"}) == set()


@pytest.mark.parametrize(
    ("ai_enabled", "unchecked"),
    [(False, set[str]()), (True, set[str]()), (None, {"ai"})],
)
def test_a_toolset_is_unchecked_when_its_capability_is_unknown(
    *, ai_enabled: bool | None, unchecked: set[str]
) -> None:
    assert unchecked_toolsets(ai_enabled=ai_enabled, requires=REQUIRES) == unchecked


# A catalogue of its own, so that the guide is proven for toolsets of any size: `bulk` has a read
# and a write tool, `trash` only writes, `ai` needs a capability, `core` is the one selected.
GUIDE_TOOLSETS = {
    "find": "core",
    "list_bulk": "bulk",
    "change_bulk": "bulk",
    "empty": "trash",
    "ask": "ai",
}
SUMMARIES = {"core": "the basics", "bulk": "many at once", "trash": "the bin", "ai": "AI answers"}


def guide(
    *,
    selected: set[str] | None = None,
    hidden: set[str] | None = None,
    unavailable: set[str] | None = None,
    unchecked: set[str] | None = None,
) -> ToolsetGuide:
    return toolset_guide(
        GUIDE_TOOLSETS,
        SUMMARIES,
        REQUIRES,
        selected=frozenset(selected or {"core"}),
        hidden=frozenset(hidden or set()),
        unavailable=frozenset(unavailable or set()),
        unchecked=frozenset(unchecked or set()),
    )


def test_the_guide_names_what_is_on_and_each_toolset_that_is_off_with_its_tools() -> None:
    result = guide()

    assert result.on == ("core",)
    assert [(t.toolset, t.what, t.tools, t.unchecked) for t in result.off] == [
        ("bulk", "many at once", ("list_bulk", "change_bulk"), None),
        ("trash", "the bin", ("empty",), None),
        ("ai", "AI answers", ("ask",), None),
    ]


def test_a_toolset_left_without_a_tool_is_not_offered() -> None:
    result = guide(hidden={"empty", "ask"})

    assert [t.toolset for t in result.off] == ["bulk"]


def test_a_toolset_cut_down_by_hidden_tools_says_which_tools_remain_not_what_it_is_for() -> None:
    [bulk, *_] = guide(hidden={"change_bulk"}).off

    assert (bulk.what, bulk.tools) == ("only list_bulk", ("list_bulk",))


def test_a_toolset_whose_capability_is_missing_is_not_offered() -> None:
    result = guide(unavailable={"ai"})

    assert [t.toolset for t in result.off] == ["bulk", "trash"]


def test_a_toolset_whose_capability_is_unchecked_is_offered_with_that_gap() -> None:
    result = guide(unchecked={"ai"})

    assert [(t.toolset, t.unchecked) for t in result.off] == [
        ("bulk", None),
        ("trash", None),
        ("ai", "Paperless AI"),
    ]


def test_a_selected_toolset_that_offers_no_tool_is_not_on() -> None:
    result = guide(selected={"core", "trash"}, hidden={"empty"})

    assert result.on == ("core",)
    assert [t.toolset for t in result.off] == ["bulk", "ai"]


def test_a_selected_toolset_whose_capability_is_missing_is_not_on() -> None:
    assert guide(selected={"core", "ai"}, unavailable={"ai"}).on == ("core",)


def test_the_enable_text_gives_the_new_setting_and_the_restart_for_every_channel() -> None:
    text = guide(selected={"core", "ai"}).enable or ""

    assert "PAPERLESS_MCP_TOOLSETS=core,ai,bulk" in text
    for channel in ("plugin", "stdio", "HTTP", "image"):
        assert channel in text
    assert text.count("restart") >= 1


def test_the_enable_text_keeps_a_selected_toolset_that_offers_nothing_now() -> None:
    text = guide(selected={"core", "trash"}, hidden={"empty"}).enable or ""

    assert "PAPERLESS_MCP_TOOLSETS=core,trash,bulk" in text


def test_with_nothing_off_there_is_nothing_to_enable() -> None:
    result = guide(selected={"core", "bulk", "trash", "ai"})

    assert (result.off, result.enable) == ((), None)


def test_the_enable_text_is_the_same_function_for_the_overview_and_the_instructions() -> None:
    assert enable_text(("core", "ai"), "bulk") == guide(selected={"core", "ai"}).enable


def test_a_toolset_without_a_summary_is_described_by_its_name() -> None:
    result = toolset_guide(
        GUIDE_TOOLSETS,
        {},
        REQUIRES,
        selected=frozenset({"core"}),
        hidden=frozenset(),
        unavailable=frozenset(),
        unchecked=frozenset(),
    )

    assert [(t.toolset, t.what) for t in result.off] == [
        ("bulk", "bulk"),
        ("trash", "trash"),
        ("ai", "ai"),
    ]
