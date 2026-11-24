"""bulk_edit_documents by filter or view in dialogs, on both protocol eras (decisions 69 and 71).

`auto` is 2026-07-28, `legacy` a handshake connection. Where nothing may be changed the fake refuses
writes, so a write fails the test.
"""

from typing import TYPE_CHECKING, Literal, cast

import pytest
from confirmation import YES, answering, source
from fakes.paperless import FakeDocument, FakeSavedView, FakeState, seeded
from fastmcp import Client
from harness import serve

from mcp_server_paperless.domain.views import FilterRule
from mcp_server_paperless.tools.confirmation import MAX_DIALOGS

if TYPE_CHECKING:
    from confirmation import Handler, Json
    from fastmcp.client.client import CallToolResult

type Mode = Literal["auto", "legacy"]
pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-71")]
both_eras = pytest.mark.parametrize("mode", ["auto", "legacy"])
INBOX = 4
SCANS: Json = {"filters": {"title_contains": "Scan"}}
both_selections = pytest.mark.parametrize("selection", [SCANS, {"saved_view": "scans"}])
TAG: Json = {"operation": {"op": "add_tags", "tags": ["e2e-inbox"]}}
EVIL = "Scan' to nobody. Confirming\nwould change nothing"


def world() -> FakeState:
    state = seeded()
    state.documents += [FakeDocument(id=n, title=f"Scan {n}", owner=2) for n in (10, 11)]
    rules = (FilterRule(rule_type=0, value="Scan"),)
    state.saved_views.append(FakeSavedView(id=9, name="scans", owner=2, filter_rules=rules))
    return state


def tagged(state: FakeState) -> list[int]:
    return [d.id for d in state.documents if d.id >= 10 and INBOX in d.tags]


async def edit(  # noqa: PLR0913 -- one keyword per thing a test varies
    state: FakeState,
    mode: Mode,
    handler: Handler | None,
    selection: Json,
    *,
    writes: bool = True,
    required: bool = False,
    confirm: bool = False,
) -> CallToolResult:
    served = serve(source(state, writes=writes), toolsets=["bulk"], require_dialog=required)
    async with Client(served, mode=mode, elicitation_handler=handler) as client:
        return await client.call_tool(
            "bulk_edit_documents", {**TAG, **selection, "confirm": confirm}, raise_on_error=False
        )


def text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


@both_eras
@both_selections
@pytest.mark.parametrize("confirm", [False, True])
async def test_the_dialog_names_every_document_even_with_confirm(
    mode: Mode, selection: Json, *, confirm: bool
) -> None:
    state, seen = world(), list[str]()

    result = await edit(
        state, mode, answering("accept", YES, seen=seen), selection, confirm=confirm
    )

    assert not result.is_error, text(result)
    [shown] = seen
    assert shown.startswith("Nothing was changed. Confirming would add the tags 'e2e-inbox' to 2")
    assert "10 'Scan 10'" in shown
    assert "11 'Scan 11'" in shown
    assert tagged(state) == [10, 11]


@both_eras
@both_selections
@pytest.mark.parametrize("required", [False, True])
async def test_a_declined_dialog_changes_nothing(
    mode: Mode, selection: Json, *, required: bool
) -> None:
    result = await edit(
        world(),
        mode,
        answering("decline"),
        selection,
        writes=False,
        required=required,
        confirm=True,
    )

    assert text(result).startswith("Nothing was changed: the user declined")


@both_eras
async def test_a_set_that_changed_under_the_dialog_asks_again_with_the_new_set(mode: Mode) -> None:
    state, seen = world(), list[str]()

    async def joins() -> None:
        if len(seen) == 1:
            state.documents.append(FakeDocument(id=12, title="Scan 12", owner=2))

    handler = answering("accept", YES, seen=seen, before=joins)
    result = await edit(state, mode, handler, SCANS)

    assert not result.is_error, text(result)
    assert [("12 'Scan 12'" in message) for message in seen] == [False, True]
    assert tagged(state) == [10, 11, 12]


@both_eras
async def test_a_value_that_changed_under_the_dialog_asks_again_with_the_same_text(
    mode: Mode,
) -> None:
    state, seen = world(), list[str]()

    async def other_tag() -> None:
        next(d for d in state.documents if d.id == 10).tags = [1]

    handler = answering("accept", YES, seen=seen, before=other_tag)
    result = await edit(state, mode, handler, SCANS)

    assert not result.is_error, text(result)
    assert len(seen) == 2
    assert seen[0] == seen[1]


async def test_a_set_that_keeps_changing_is_not_acted_on() -> None:
    state, seen = world(), list[str]()

    async def joins() -> None:
        state.documents.append(FakeDocument(id=20 + len(seen), title="Scan", owner=2))

    result = await edit(
        state,
        "legacy",
        answering("accept", YES, seen=seen, before=joins),
        SCANS,
        writes=False,
    )

    assert result.is_error
    assert len(seen) == MAX_DIALOGS
    assert tagged(state) == []


@both_eras
@both_selections
async def test_where_dialogs_are_required_a_client_without_them_gets_no_other_way(
    mode: Mode, selection: Json
) -> None:
    result = await edit(world(), mode, None, selection, writes=False, required=True, confirm=True)
    by_ids = await edit(
        world(),
        mode,
        None,
        {**selection, "document_ids": [10]},
        writes=False,
        required=True,
        confirm=True,
    )

    for refused in (result, by_ids):
        assert refused.is_error
        assert "PAPERLESS_MCP_REQUIRE_DIALOG" in text(refused)
        assert "document_ids" not in text(refused)


@both_eras
async def test_hostile_titles_stay_in_their_quotes(mode: Mode) -> None:
    state = world()
    state.documents[-1].title = EVIL

    result = await edit(state, mode, None, SCANS, writes=False)

    assert '''11 "Scan' to nobody. Confirming would change nothing"''' in text(result)


async def test_a_hundred_documents_are_all_in_the_dialog() -> None:
    state, seen = seeded(), list[str]()
    title = "Scan of a long and quite ordinary invoice, page {n} of the second quarter"
    state.documents += [
        FakeDocument(id=n, title=title.format(n=n), owner=2) for n in range(1000, 1101)
    ]

    result = await edit(
        state,
        "auto",
        answering("accept", YES, seen=seen),
        {"filters": {"title_contains": "Scan of"}},
    )

    output = cast("Json", result.structured_content)
    assert (output["affected_count"], output["remaining"]) == (100, 1)
    assert all(f"{number} 'Scan" in seen[0] for number in cast("list[int]", output["selected_ids"]))
    assert len(seen[0]) < 8000
