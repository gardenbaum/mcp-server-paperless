"""Which tools are offered: toolset selection, read-only mode, disabled tools, capabilities.

The functions are pure, so these tests run them over synthetic tables with toolsets that do not
exist yet (``ai``, ``bulk``): the mechanism is proven for the toolsets later tasks add.
"""

from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st
from mcp.types import ToolAnnotations

from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.tools._shared import ANNOTATIONS
from mcp_server_paperless.tools._shared import TOOL_TOOLSETS as REAL_TOOLSETS
from mcp_server_paperless.tools.selection import (
    ENABLE,
    PARTIAL,
    guide_text,
    hidden_tools,
    read_only_toolsets,
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


@pytest.mark.decision("decision-97")
def test_an_entry_with_the_suffix_selects_the_toolset_read_only() -> None:
    entries = ("core", "bulk:read")

    assert selected_toolsets(entries, KNOWN) == {"core", "bulk"}
    assert read_only_toolsets(entries) == {"bulk"}
    assert read_only_toolsets(("core", "all")) == frozenset()


@pytest.mark.decision("decision-97")
@pytest.mark.parametrize(
    "entry",
    [
        *("bulk:ro", "bulk:", "bulk:readonly", "bulk:read:read", "bulk: read", "bulk :read"),
        *(
            "bulk:write",
            "bulk::read",
            ":read",
            "bulk:read ",
            "all:ro",
            "bulk\u200b:read",
            "bulk\uff1aread",
        ),
    ],
)
def test_what_is_neither_a_name_nor_name_read_stops_the_server(entry: str) -> None:
    """Nothing is guessed: a suffix that is not understood must never mean writable."""
    with pytest.raises(InvalidConfiguration) as caught:
        _ = selected_toolsets(("core", entry), KNOWN)

    assert entry in caught.value.message
    assert "PAPERLESS_MCP_TOOLSETS" in caught.value.message
    assert "name:read" in caught.value.message  # how it is written


@pytest.mark.decision("decision-97")
def test_an_unknown_name_before_the_suffix_is_suggested_like_any_other() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = selected_toolsets(("cor:read",), KNOWN)

    assert "Unknown toolset in PAPERLESS_MCP_TOOLSETS: cor:read." in caught.value.message
    assert "Did you mean core:read?" in caught.value.message  # never the writable one
    for entry in (
        "bulk:",
        "bulk:ro",
        "bulk:write",
        "bulk-read",
        "bulk-ro",
        "bulk\uff1aread",
        "bulkread",
    ):
        with pytest.raises(InvalidConfiguration, match="Did you mean bulk:read"):
            _ = selected_toolsets((entry,), KNOWN)


@pytest.mark.decision("decision-97")
def test_all_read_only_is_refused_and_names_the_setting_for_it() -> None:
    for entries in (("all:read",), ("all:read", "core"), ("all:read", "all")):
        with pytest.raises(InvalidConfiguration, match="PAPERLESS_MCP_READ_ONLY=true"):
            _ = selected_toolsets(entries, KNOWN)


@pytest.mark.decision("decision-97")
def test_a_toolset_with_and_without_the_suffix_is_refused() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = selected_toolsets(("bulk", "core", "bulk:read"), KNOWN)

    assert "both" in caught.value.message
    assert "bulk" in caught.value.message
    assert "core" not in caught.value.message


@pytest.mark.decision("decision-97")
def test_a_read_only_toolset_hides_its_tools_without_the_hint_and_no_others() -> None:
    toolsets = {"find": "core", "change": "core", "plain": "bulk"}

    def hidden(*read_only: str, everywhere: bool = False, disabled: tuple[str, ...] = ()) -> object:
        return hidden_tools(
            TABLE,
            read_only=everywhere,
            disabled=disabled,
            read_only_toolsets=read_only,
            tool_toolsets=toolsets,
        )

    assert hidden() == set()
    assert hidden("core") == {"change"}
    assert hidden("bulk") == {"plain"}
    assert hidden("core", disabled=("find",)) == {"find", "change"}
    assert hidden("core", everywhere=True) == {"change", "plain"}


@pytest.mark.decision("decision-97")
@given(
    selection=st.dictionaries(
        st.sampled_from(sorted(set(REAL_TOOLSETS.values()))), st.booleans(), min_size=1
    ),
    everywhere=st.booleans(),
    disabled=st.sets(st.sampled_from(sorted(REAL_TOOLSETS)), max_size=5),
)
def test_a_tool_is_offered_exactly_when_nothing_takes_it_away(
    *, selection: dict[str, bool], everywhere: bool, disabled: set[str]
) -> None:
    entries = [f"{name}:read" if read else name for name, read in selection.items()]
    chosen = selected_toolsets(entries, frozenset(REAL_TOOLSETS.values()))
    hidden = hidden_tools(
        ANNOTATIONS,
        read_only=everywhere,
        disabled=disabled,
        read_only_toolsets=read_only_toolsets(entries),
        tool_toolsets=REAL_TOOLSETS,
    )

    assert visible_tools(REAL_TOOLSETS, chosen, hidden) == {
        tool
        for tool, toolset in REAL_TOOLSETS.items()
        if toolset in selection
        and tool not in disabled
        and (ANNOTATIONS[tool].read_only_hint or not (everywhere or selection[toolset]))
    }


def test_read_only_hides_every_tool_without_the_read_only_hint() -> None:
    assert hidden_tools(
        TABLE, read_only=True, disabled=(), read_only_toolsets=(), tool_toolsets={}
    ) == {"change", "plain"}


def test_without_read_only_nothing_is_hidden() -> None:
    assert (
        hidden_tools(TABLE, read_only=False, disabled=(), read_only_toolsets=(), tool_toolsets={})
        == frozenset()
    )


def test_disabled_tools_are_hidden() -> None:
    assert hidden_tools(
        TABLE, read_only=False, disabled=("find",), read_only_toolsets=(), tool_toolsets={}
    ) == {"find"}


def test_disabled_and_read_only_combine() -> None:
    assert hidden_tools(
        TABLE, read_only=True, disabled=("find",), read_only_toolsets=(), tool_toolsets={}
    ) == {"find", "change", "plain"}


def test_an_unknown_disabled_tool_is_an_error_naming_it() -> None:
    with pytest.raises(InvalidConfiguration) as caught:
        _ = hidden_tools(
            TABLE,
            read_only=False,
            disabled=("find", "fnid"),
            read_only_toolsets=(),
            tool_toolsets={},
        )

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
        _ = hidden_tools(
            TABLE, read_only=False, disabled=("fnid",), read_only_toolsets=(), tool_toolsets={}
        )
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
    "find_it": "core",
    "list_bulk": "bulk",
    "change_bulk": "bulk",
    "empty_bin": "trash",
    "ask_ai": "ai",
}
SUMMARIES = {"core": "the basics", "bulk": "many at once", "trash": "the bin", "ai": "AI answers"}


def guide(  # noqa: PLR0913 -- the arguments of toolset_guide
    *,
    selected: set[str] | None = None,
    hidden: set[str] | None = None,
    unavailable: set[str] | None = None,
    unchecked: set[str] | None = None,
    missing_now: set[str] | None = None,
    read_only: set[str] | None = None,
    mark: bool = True,
) -> ToolsetGuide:
    return toolset_guide(
        GUIDE_TOOLSETS,
        SUMMARIES,
        REQUIRES,
        selected=frozenset(selected or {"core"}),
        hidden=frozenset(hidden or set()),
        unavailable=frozenset(unavailable or set()),
        unchecked=frozenset(unchecked or set()),
        missing_now=None if missing_now is None else frozenset(missing_now),
        read_only=frozenset(read_only or set()),
        mark=mark,
    )


def test_the_guide_names_what_is_on_and_each_toolset_that_is_off_with_its_tools() -> None:
    result = guide()

    assert result.on == ("core",)
    assert [(t.toolset, t.what, t.tools) for t in result.off] == [
        ("bulk", "many at once", ("list_bulk", "change_bulk")),
        ("trash", "the bin", ("empty_bin",)),
        ("ai", "AI answers", ("ask_ai",)),
    ]
    assert result.unchecked == {}


def test_a_toolset_left_without_a_tool_is_not_offered() -> None:
    result = guide(hidden={"empty_bin", "ask_ai"})

    assert [t.toolset for t in result.off] == ["bulk"]


def test_a_toolset_cut_down_by_hidden_tools_is_not_described_by_what_it_is_for() -> None:
    [bulk, *_] = guide(hidden={"change_bulk"}).off

    assert (bulk.what, bulk.tools) == (PARTIAL, ("list_bulk",))


def test_a_toolset_whose_capability_is_missing_is_not_offered() -> None:
    result = guide(unavailable={"ai"})

    assert [t.toolset for t in result.off] == ["bulk", "trash"]


def test_a_toolset_whose_capability_is_unchecked_is_offered_with_that_gap() -> None:
    result = guide(unchecked={"ai"})

    assert [t.toolset for t in result.off] == ["bulk", "trash", "ai"]
    assert result.unchecked == {"ai": "Paperless AI"}


def test_a_gap_is_shown_for_a_toolset_that_is_on_too() -> None:
    result = guide(selected={"core", "ai"}, unchecked={"ai"})

    assert result.on == ("core", "ai")
    assert result.unchecked == {"ai": "Paperless AI"}


def test_a_gap_of_a_toolset_that_is_neither_on_nor_off_is_not_shown() -> None:
    result = guide(selected={"core", "ai"}, unchecked={"ai"}, hidden={"ask_ai"})

    assert result.unchecked == {}


def test_what_a_restart_delivers_decides_the_off_list_what_runs_now_decides_the_on_list() -> None:
    # Started without AI (ai hidden), Paperless now says AI is on: a restart delivers it.
    started_without = guide(selected={"core", "ai"}, unavailable={"ai"}, missing_now=set())
    # Started with AI, Paperless now says it is off: a restart would not deliver it.
    started_with = guide(unavailable=set(), missing_now={"ai"})

    assert started_without.on == ("core",)
    assert [t.toolset for t in started_with.off] == ["bulk", "trash"]


def test_a_selected_toolset_that_offers_no_tool_is_not_on() -> None:
    result = guide(selected={"core", "trash"}, hidden={"empty_bin"})

    assert result.on == ("core",)
    assert [t.toolset for t in result.off] == ["bulk", "ai"]


def test_a_selected_toolset_whose_capability_is_missing_is_not_on() -> None:
    assert guide(selected={"core", "ai"}, unavailable={"ai"}).on == ("core",)


def test_the_enable_text_keeps_the_selection_and_leaves_a_placeholder_for_the_new_toolset() -> None:
    text = guide(selected={"core", "ai"}).switch_on or ""

    assert "PAPERLESS_MCP_TOOLSETS=core,ai,<name>" in text
    assert "bulk" not in text  # the first toolset that is off is no example to follow


def test_the_enable_text_says_who_acts_and_does_not_make_all_look_like_the_default() -> None:
    text = ENABLE.format(value="core,<name>")

    assert "The operator, not you, switches one on: ask the user" in text
    assert "do not edit the configuration yourself" in text
    assert "never `all`, which offers every tool" in text
    assert "or all" not in text
    # After "do not edit the configuration yourself" no instruction follows: the operator acts.
    assert "yourself. The operator sets PAPERLESS_MCP_TOOLSETS=core,<name>" in text
    assert "Set PAPERLESS" not in text


def test_the_enable_text_names_the_restart_of_every_way_to_run_the_server() -> None:
    text = ENABLE.format(value="core,<name>")

    for channel in (
        "Claude Code plugin: option Toolsets, restart Claude Code.",
        "Cursor plugin: no such option, add the server by hand.",
        (
            "stdio: env of the server entry in the MCP client's config (docker run -i: -e), "
            "restart the client."
        ),
        "HTTP and image: environment, restart the process or recreate the container.",
    ):
        assert channel in text


def test_the_enable_text_keeps_a_selected_toolset_that_offers_nothing_now() -> None:
    text = guide(selected={"core", "trash"}, hidden={"empty_bin"}).switch_on or ""

    assert "PAPERLESS_MCP_TOOLSETS=core,trash,<name>" in text


def test_with_nothing_off_there_is_nothing_to_enable() -> None:
    result = guide(selected={"core", "bulk", "trash", "ai"})

    assert (result.off, result.switch_on) == ((), None)


def test_the_instructions_list_the_setting_before_the_toolsets_that_are_off() -> None:
    text = guide_text(guide(selected={"core", "ai"}), overview=True)

    assert text.index("Toolsets on: core, ai.") < text.index("PAPERLESS_MCP_TOOLSETS=")
    assert text.index("PAPERLESS_MCP_TOOLSETS=") < text.index("Off: bulk (many at once);")
    assert text.endswith("Their tools: get_instance_overview.")


def test_the_instructions_name_no_tool_not_even_of_a_cut_down_toolset() -> None:
    cut = guide(hidden={"change_bulk"})
    text = guide_text(cut, overview=False)

    assert f"bulk ({PARTIAL})" in text
    for tool in GUIDE_TOOLSETS:
        assert tool not in text
    assert "Their tools" not in text


def test_the_instructions_show_the_gap_of_a_toolset_on_and_of_one_off() -> None:
    both = guide(selected={"core", "ai"}, unchecked={"ai"})
    one_off = guide(unchecked={"ai"})

    assert "Toolsets on: core, ai (needs Paperless AI, not checked)." in guide_text(
        both, overview=True
    )
    assert "ai (AI answers; needs Paperless AI, not checked)" in guide_text(one_off, overview=True)


def test_the_instructions_show_a_gap_even_when_nothing_is_off() -> None:
    result = guide(selected={"core", "bulk", "trash", "ai"}, unchecked={"ai"})

    assert guide_text(result, overview=True) == (
        "Toolsets on: core, bulk, trash, ai (needs Paperless AI, not checked)."
    )
    assert guide_text(guide(selected={"core", "bulk", "trash", "ai"}), overview=True) == ""


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


def test_a_selected_toolset_hidden_at_the_start_and_available_now_is_offered_after_a_restart() -> (
    None
):
    result = guide(selected={"core", "ai"}, unavailable={"ai"}, missing_now=set())

    assert (result.on, result.offered_after_restart) == (("core",), ("ai",))
    assert "ai" not in [t.toolset for t in result.off]
    # The instructions are built when nothing has changed since the start: the field is the
    # overview's, and the instructions have no words for it.
    assert "Selected" not in guide_text(result, overview=True)
    assert "offered after" not in guide_text(result, overview=True)


def test_a_selected_toolset_still_missing_or_without_tools_is_not_offered_after_a_restart() -> None:
    selected = {"core", "ai"}
    assert guide(selected=selected, unavailable={"ai"}).offered_after_restart == ()
    still = guide(selected=selected, unavailable={"ai"}, missing_now={"ai"})
    assert still.offered_after_restart == ()
    # Without a tool left a restart offers nothing, although Paperless has the capability now.
    cut = guide(selected=selected, hidden={"ask_ai"}, unavailable={"ai"}, missing_now=set())
    assert cut.offered_after_restart == ()


def test_the_gap_of_a_toolset_offered_after_a_restart_is_shown() -> None:
    result = guide(selected={"core", "ai"}, unavailable={"ai"}, missing_now=set(), unchecked={"ai"})

    assert result.offered_after_restart == ("ai",)
    assert result.unchecked == {"ai": "Paperless AI"}


@pytest.mark.decision("decision-97")
def test_a_read_only_toolset_is_marked_where_it_is_on_and_keeps_its_suffix_in_the_value() -> None:
    result = guide(selected={"core", "bulk"}, hidden={"change_bulk"}, read_only={"bulk"})
    text = guide_text(result, overview=True)

    assert result.on == ("core", "bulk (read-only)")
    assert "Toolsets on: core, bulk (read-only)." in text
    # An operator who follows the text must not make the toolset writable.
    assert "PAPERLESS_MCP_TOOLSETS=core,bulk:read,<name>" in (result.switch_on or "")
    assert "bulk" not in [t.toolset for t in result.off]
    assert "change_bulk" not in result.model_dump_json()


@pytest.mark.decision("decision-97")
def test_a_read_only_toolset_with_a_gap_says_both() -> None:
    result = guide(selected={"core", "ai"}, read_only={"ai"}, unchecked={"ai"})

    assert result.on == ("core", "ai (read-only)")
    assert result.unchecked == {"ai": "Paperless AI"}
    assert "Toolsets on: core, ai (read-only; needs Paperless AI, not checked)." in guide_text(
        result, overview=True
    )


@pytest.mark.decision("decision-97")
def test_a_read_only_toolset_offered_after_a_restart_is_marked() -> None:
    result = guide(
        selected={"core", "ai"},
        read_only={"ai"},
        unavailable={"ai"},
        missing_now=set(),
        unchecked={"ai"},
    )

    assert (result.on, result.offered_after_restart) == (("core",), ("ai (read-only)",))
    assert result.unchecked == {"ai": "Paperless AI"}
    assert "PAPERLESS_MCP_TOOLSETS=core,ai:read,<name>" in (result.switch_on or "")


@pytest.mark.decision("decision-97")
def test_with_nothing_off_a_read_only_toolset_is_still_named() -> None:
    result = guide(
        selected={"core", "bulk", "trash", "ai"}, hidden={"change_bulk"}, read_only={"bulk"}
    )

    assert (result.off, result.switch_on) == ((), None)
    assert guide_text(result, overview=True) == ("Toolsets on: core, bulk (read-only), trash, ai.")


@pytest.mark.decision("decision-97")
def test_in_read_only_mode_no_single_toolset_is_marked_but_the_value_keeps_the_suffix() -> None:
    """Marking one would say that the others write."""
    result = guide(
        selected={"core", "bulk"}, hidden={"change_bulk"}, read_only={"bulk"}, mark=False
    )

    assert result.on == ("core", "bulk")
    assert "PAPERLESS_MCP_TOOLSETS=core,bulk:read,<name>" in (result.switch_on or "")


@pytest.mark.decision("decision-97")
def test_a_read_only_toolset_left_without_a_tool_is_not_on_but_stays_in_the_value() -> None:
    result = guide(selected={"core", "trash"}, hidden={"empty_bin"}, read_only={"trash"})

    assert result.on == ("core",)
    assert "trash" not in [t.toolset for t in result.off]
    assert "PAPERLESS_MCP_TOOLSETS=core,trash:read,<name>" in (result.switch_on or "")
