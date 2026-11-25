"""update_documents and add_notes through the assembled server (decision-73).

`auto` is 2026-07-28, `legacy` a handshake connection. Where nothing may be changed the fake refuses
writes, so a write fails the test.
"""

from typing import TYPE_CHECKING, Literal, cast

import pytest
from confirmation import YES, answering, source
from fakes.accounts import READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import admin, serve

from mcp_server_paperless.tools.confirmation import MAX_DIALOGS

if TYPE_CHECKING:
    from confirmation import Handler, Json
    from fastmcp.client.client import CallToolResult

type Mode = Literal["auto", "legacy"]
pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-73")]
both_eras = pytest.mark.parametrize("mode", ["auto", "legacy"])
TOOLS = {"update_documents", "add_notes"}
UPDATE: Json = {
    "documents": [
        {"document_id": 10, "title": "Invoice 10", "tags_add": ["e2e-inbox"]},
        {"document_id": 11, "title": "Invoice 11", "owner": "e2e-reader"},
    ]
}
NOTE: Json = {"document_ids": [10, 11], "note": "Paid"}
BY_FILTER: Json = {"filters": {"title_contains": "Scan"}, "note": "Paid"}
CALLS: list[tuple[str, Json]] = [
    ("update_documents", UPDATE),
    ("add_notes", NOTE),
    ("add_notes", BY_FILTER),
]
every_call = pytest.mark.parametrize(("tool", "arguments"), CALLS)


def world() -> FakeState:
    state = seeded()
    state.documents += [FakeDocument(id=n, title=f"Scan {n}", owner=2) for n in (10, 11)]
    return state


def changed(state: FakeState) -> list[int]:
    """The documents that a call of CALLS changed."""
    return [d.id for d in state.documents if d.id >= 10 and (d.notes or d.title != f"Scan {d.id}")]


async def call(  # noqa: PLR0913 -- one keyword per thing a test varies
    state: FakeState,
    tool: str,
    arguments: Json,
    *,
    mode: Mode = "auto",
    handler: Handler | None = None,
    writes: bool = True,
    required: bool = False,
) -> CallToolResult:
    served = serve(source(state, writes=writes), toolsets=["bulk"], require_dialog=required)
    async with Client(served, mode=mode, elicitation_handler=handler) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


def text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def output(result: CallToolResult) -> Json:
    assert not result.is_error, text(result)
    assert result.structured_content is not None
    return result.structured_content


async def test_both_are_opt_in_and_not_in_read_only_mode() -> None:
    async def names(**settings: object) -> set[str]:
        async with Client(serve(admin(), **settings)) as client:  # pyright: ignore[reportArgumentType] -- keyword passthrough
            return {tool.name for tool in await client.list_tools()}

    assert TOOLS.isdisjoint(await names())
    assert await names(toolsets=["bulk"]) >= TOOLS
    assert TOOLS.isdisjoint(await names(toolsets=["bulk"], read_only=True))


async def test_the_documents_take_the_fields_of_update_document_and_the_lists_are_bounded() -> None:
    async with Client(serve(admin(), toolsets=["core", "bulk"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    schema = tools["update_documents"].input_schema
    properties = cast("dict[str, Json]", schema["properties"])
    single = cast("dict[str, Json]", tools["update_document"].input_schema["properties"])
    item = cast("dict[str, Json]", schema.get("$defs", {}) or {})
    listed = cast("Json", properties["documents"]["items"])
    body = listed if "properties" in listed else item["DocumentValues"]
    assert set(properties) == {"documents", "create_missing_tags", "dry_run", "confirm"}
    assert schema["required"] == ["documents"]
    assert (properties["documents"]["minItems"], properties["documents"]["maxItems"]) == (1, 100)
    assert set(cast("Json", body["properties"])) == set(single) - {"create_missing_tags", "confirm"}
    assert body["additionalProperties"] is False
    assert properties["create_missing_tags"] == single["create_missing_tags"]

    notes = cast("dict[str, Json]", tools["add_notes"].input_schema["properties"])
    reading = cast("dict[str, Json]", tools["read_documents"].input_schema["properties"])
    selection = {"document_ids", "filters", "saved_view", "exclude_ids"}
    assert set(notes) == {*selection, "note", "notes", "format", "dry_run", "confirm"}
    assert {n: notes[n] for n in selection} == {n: reading[n] for n in selection}


async def test_update_documents_previews_and_changes() -> None:
    state = world()

    preview = output(
        await call(world(), "update_documents", {**UPDATE, "dry_run": True}, writes=False)
    )
    done = output(await call(state, "update_documents", {**UPDATE, "confirm": True}))

    assert (preview["dry_run"], preview["counts"]) == (True, {"would_change": 2})
    assert (done["dry_run"], done["counts"], done["left"]) == (False, {"changed": 2}, [])
    [first, second] = cast("list[Json]", done["documents"])
    assert first["changes"] == [
        {"field": "title", "old": "Scan 10", "new": "Invoice 10"},
        {"field": "tags", "old": [], "new": ["e2e-inbox"]},
    ]
    assert (second["id"], second["title"], second["status"]) == (11, "Scan 11", "changed")
    assert changed(state) == [10, 11]


async def test_add_notes_previews_adds_and_skips() -> None:
    state = world()

    preview = output(await call(world(), "add_notes", {**NOTE, "dry_run": True}, writes=False))
    done = output(await call(state, "add_notes", {**NOTE, "confirm": True}))
    again = output(await call(state, "add_notes", NOTE, writes=False))
    each: Json = {"notes": [{"document_id": 11, "note": "Other"}], "confirm": True}
    other = output(await call(state, "add_notes", each))

    assert preview["counts"] == {"would_add": 2}
    assert done["counts"] == {"added": 2}
    assert again["counts"] == {"already_there": 2}
    assert other["counts"] == {"added": 1}
    assert [[n.note for n in d.notes] for d in state.documents if d.id >= 10] == [
        ["Paid"],
        ["Paid", "Other"],
    ]


async def test_the_call_a_refusal_names_adds_the_notes() -> None:
    state = world()

    refusal = text(await call(state, "add_notes", {**BY_FILTER, "confirm": True}, writes=False))
    assert "document_ids=[11, 10] and confirm=true" in refusal
    done = output(
        await call(state, "add_notes", {**BY_FILTER, "document_ids": [11, 10], "confirm": True})
    )

    assert done["counts"] == {"added": 2}


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("update_documents", {"documents": []}),
        ("update_documents", {"documents": [{"document_id": 10, "titel": "x"}]}),
        ("update_documents", {"documents": [{"document_id": True, "title": "x"}]}),
        ("update_documents", {"documents": [{"document_id": 10, "title": "x"}] * 101}),
        ("add_notes", {"notes": []}),
        ("add_notes", {"notes": [{"document_id": 10, "note": "x"}] * 101}),
        ("add_notes", {"note": "x", "document_ids": list(range(1, 102))}),
        ("add_notes", {"note": "x", "document_ids": [True]}),
        ("add_notes", {"note": "x", "filters": {"titel": "Scan"}}),
        ("add_notes", {"note": "x"}),
        ("add_notes", {"document_ids": [10]}),
        ("add_notes", {**NOTE, "notes": [{"document_id": 10, "note": "x"}]}),
    ],
)
async def test_calls_that_could_be_meant_otherwise_are_refused(tool: str, arguments: Json) -> None:
    result = await call(world(), tool, {**arguments, "confirm": True}, writes=False)

    assert result.is_error


async def test_a_restricted_user_changes_nothing() -> None:
    served = serve(FakePaperless(seeded(), user=READER[0]), toolsets=["bulk"])
    item: Json = {"document_id": 2, "title": "x"}
    async with Client(served) as client:
        hidden = await client.call_tool(
            "update_documents", {"documents": [item], "confirm": True}, raise_on_error=False
        )
        notes = await client.call_tool(
            "add_notes", {"document_ids": [1], "note": "x"}, raise_on_error=False
        )

    assert "document 2: It does not exist or is hidden from you." in text(hidden)
    assert "may not view notes" in text(notes)


# --- dialogs


@both_eras
@every_call
@pytest.mark.parametrize("required", [False, True])
async def test_the_dialog_names_every_document_and_an_accepted_one_runs(
    mode: Mode, tool: str, arguments: Json, *, required: bool
) -> None:
    state, seen = world(), list[str]()

    result = await call(
        state,
        tool,
        arguments,
        mode=mode,
        handler=answering("accept", YES, seen=seen),
        required=required,
    )

    assert not result.is_error, text(result)
    [shown] = seen
    assert "10 'Scan 10'" in shown
    assert "11 'Scan 11'" in shown
    assert changed(state) == [10, 11]


@both_eras
@every_call
async def test_a_declined_dialog_changes_nothing(mode: Mode, tool: str, arguments: Json) -> None:
    result = await call(
        world(), tool, arguments, mode=mode, handler=answering("decline"), writes=False
    )

    assert text(result).startswith("Nothing was changed: the user declined")


@every_call
async def test_where_a_dialog_is_required_nothing_else_is_offered(
    tool: str, arguments: Json
) -> None:
    result = await call(world(), tool, {**arguments, "confirm": True}, writes=False, required=True)

    assert result.is_error
    assert "confirm=true does not replace it" in text(result)
    assert "document_ids=[" not in text(result)


@both_eras
@every_call
async def test_what_changed_under_the_dialog_asks_again(
    mode: Mode, tool: str, arguments: Json
) -> None:
    state, seen = world(), list[str]()
    [first] = [d for d in state.documents if d.id == 10]

    async def others() -> None:
        if len(seen) == 1:
            first.title = "Scan ten"

    result = await call(
        state,
        tool,
        arguments,
        mode=mode,
        handler=answering("accept", YES, seen=seen, before=others),
    )

    assert not result.is_error, text(result)
    assert len(seen) == 2
    assert "10 'Scan 10'" in seen[0]
    assert "10 'Scan ten'" in seen[1]


async def test_a_document_that_keeps_changing_is_given_up_after_three_dialogs() -> None:
    state, seen = world(), list[str]()
    [first] = [d for d in state.documents if d.id == 10]

    async def others() -> None:
        first.title = f"Scan {len(seen)}"

    result = await call(
        state,
        "update_documents",
        UPDATE,
        mode="legacy",
        handler=answering("accept", YES, seen=seen, before=others),
        writes=False,
    )

    assert result.is_error
    assert len(seen) == MAX_DIALOGS


async def test_add_notes_takes_the_format_of_its_notes() -> None:
    state = world()

    _ = output(await call(state, "add_notes", {**NOTE, "format": "markdown", "confirm": True}))
    again = output(await call(state, "add_notes", {**NOTE, "confirm": True}))

    assert cast("Json", again["counts"]) == {"added": 2}
    assert {n.format for d in state.documents if d.id in {10, 11} for n in d.notes} == {
        "markdown",
        "plain",
    }
