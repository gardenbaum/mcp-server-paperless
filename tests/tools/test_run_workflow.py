"""run_workflow through the assembled server, dialogs on both protocol eras included (decision-75).

`auto` is 2026-07-28, `legacy` a handshake connection. Where nothing may run the fake refuses
writes, so a write fails the test.
"""

from typing import TYPE_CHECKING, Literal, cast

import pytest
from confirmation import YES, answering, source
from fakes.paperless import (
    FakeCustomField,
    FakeDocument,
    FakePaperless,
    FakeSavedView,
    FakeState,
    FakeTag,
    seeded,
)
from fastmcp import Client
from harness import serve

from mcp_server_paperless.domain.views import FilterRule
from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger
from mcp_server_paperless.tools._shared import ANNOTATIONS
from mcp_server_paperless.tools.confirmation import MAX_DIALOGS

if TYPE_CHECKING:
    from confirmation import Handler, Json
    from fastmcp.client.client import CallToolResult

type Mode = Literal["auto", "legacy"]
pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-75")]
both_eras = pytest.mark.parametrize("mode", ["auto", "legacy"])
INBOX = 4
SCANS: Json = {"filters": {"title_contains": "Scan"}}
both_selections = pytest.mark.parametrize("selection", [SCANS, {"saved_view": "scans"}])
EVIL = "Scan' to nobody. Confirming\nwould change nothing"


MANUAL = WorkflowTrigger(id=1, type=5)
TAGGING = WorkflowAction(id=2, type=1, settings={"assign_tags": [INBOX]})


def flow(
    action: WorkflowAction = TAGGING,
    *,
    name: str = "Paid",
    enabled: bool = True,
    triggers: tuple[WorkflowTrigger, ...] = (MANUAL,),
) -> Workflow:
    return Workflow(id=3, name=name, order=0, enabled=enabled, triggers=triggers, actions=(action,))


def world() -> FakeState:
    state = seeded()
    state.documents += [FakeDocument(id=n, title=f"Scan {n}", owner=2) for n in (10, 11)]
    rules = (FilterRule(rule_type=0, value="Scan"),)
    state.saved_views.append(FakeSavedView(id=9, name="scans", owner=2, filter_rules=rules))
    state.workflows = [flow()]
    return state


async def run(  # noqa: PLR0913 -- one keyword per thing a test varies
    state: FakeState,
    arguments: Json,
    mode: Mode = "auto",
    handler: Handler | None = None,
    *,
    writes: bool = True,
    required: bool = False,
) -> CallToolResult:
    served = serve(source(state, writes=writes), toolsets=["workflows"], require_dialog=required)
    async with Client(served, mode=mode, elicitation_handler=handler) as client:
        return await client.call_tool(
            "run_workflow", {"workflow": "Paid", **arguments}, raise_on_error=False
        )


def text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def output(result: CallToolResult) -> Json:
    assert not result.is_error, text(result)
    return cast("Json", result.structured_content)


def test_the_hints_say_that_a_run_is_destructive_reaches_out_and_cannot_be_repeated() -> None:
    hints = ANNOTATIONS["run_workflow"]

    assert hints.title == "Run workflow"
    assert hints.read_only_hint is False
    assert hints.destructive_hint is True
    assert hints.idempotent_hint is False
    assert hints.open_world_hint is True


async def test_named_documents_run_after_the_second_call() -> None:
    state = world()

    refused = await run(state, {"document_ids": [10, 11]}, writes=False)
    done = output(await run(state, {"document_ids": [10, 11], "confirm": True}))

    assert refused.is_error
    assert "assign tags 'e2e-inbox'. It runs on 2 documents" in text(refused)
    assert state.workflow_runs == [(3, (10, 11), True)]
    assert (done["started"], done["selected_ids"], done["failed"]) == (True, [10, 11], [])
    assert done["actions"] == ["step 1 of 1: assign tags 'e2e-inbox'"]


async def test_the_call_a_refusal_by_filter_names_goes_through() -> None:
    state = world()

    refused = await run(state, {**SCANS, "confirm": True}, writes=False)
    done = output(
        await run(
            state,
            {**SCANS, "confirm": True, "apply_filters": False, "document_ids": [10, 11]},
        )
    )

    assert "document_ids=[" in text(refused)
    assert state.workflow_runs == [(3, (10, 11), False)]
    assert done["apply_filters"] is False


async def test_excluded_documents_are_left_alone() -> None:
    state = world()

    done = output(await run(state, {**SCANS, "dry_run": True, "exclude_ids": [10]}, writes=False))

    assert (done["dry_run"], done["started"]) == (True, False)
    assert (done["matched"], done["selected_ids"]) == (2, [11])


async def test_a_partial_failure_is_a_result_that_names_the_failed_documents() -> None:
    state = world()
    state.workflow_fails_on = {11}

    done = output(await run(state, {"document_ids": [10, 11], "confirm": True}))
    state.workflow_fails_on = {10, 11}
    failed = await run(state, {"document_ids": [10, 11], "confirm": True})

    assert done["failed"] == [11]
    assert cast("str", done["message"]).startswith(
        "The workflow 'Paid' (id 3) failed on 1 of 2 documents: 11."
    )
    assert failed.is_error
    assert "do not run it again" in text(failed)


async def test_an_unusable_workflow_is_an_error_even_with_confirm() -> None:
    state = world()
    for broken, reason in (
        (flow(enabled=False), "is not enabled"),
        (flow(triggers=()), "has no manual trigger"),
    ):
        state.workflows = [broken]

        result = await run(state, {"document_ids": [10], "confirm": True}, writes=False)

        assert result.is_error
        assert reason in text(result)


async def test_a_restricted_user_is_told_what_is_missing() -> None:
    state = world()
    served = serve(FakePaperless(state, user="e2e-reader"), toolsets=["workflows"])
    async with Client(served) as client:
        result = await client.call_tool(
            "run_workflow",
            {"workflow": "Paid", "document_ids": [1], "confirm": True},
            raise_on_error=False,
        )

    assert result.is_error
    assert "may not" in text(result)
    assert state.workflow_runs == []


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"document_ids": []},
        {"document_ids": [True]},
        {"document_ids": list(range(1, 102))},
        {"filters": {}},
        {"filters": {"title": "x"}},
        {"filters": {"title_contains": "Scan"}, "saved_view": "scans"},
        {"document_ids": [10], "exclude_ids": [11]},
    ],
)
async def test_a_selection_that_is_none_is_refused(arguments: Json) -> None:
    result = await run(world(), {**arguments, "confirm": True}, writes=False)

    assert result.is_error


async def test_read_only_mode_does_not_offer_it() -> None:
    served = serve(source(world(), writes=False), toolsets=["workflows"], read_only=True)
    async with Client(served) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert "run_workflow" not in names


# --- dialogs


@both_eras
@both_selections
@pytest.mark.parametrize("confirm", [False, True])
async def test_the_dialog_names_every_document_and_action_even_with_confirm(
    mode: Mode, selection: Json, *, confirm: bool
) -> None:
    state, seen = world(), list[str]()

    result = await run(
        state, {**selection, "confirm": confirm}, mode, answering("accept", YES, seen=seen)
    )

    assert output(result)["started"] is True
    [shown] = seen
    assert "run the workflow 'Paid' (id 3), which will" in shown
    assert "assign tags 'e2e-inbox'. It runs on 2 documents" in shown
    assert "10 'Scan 10'" in shown
    assert "11 'Scan 11'" in shown
    assert len(state.workflow_runs) == 1


@both_eras
@both_selections
@pytest.mark.parametrize("required", [False, True])
async def test_a_declined_dialog_runs_nothing(
    mode: Mode, selection: Json, *, required: bool
) -> None:
    result = await run(
        world(),
        {**selection, "confirm": True},
        mode,
        answering("decline"),
        writes=False,
        required=required,
    )

    assert text(result).startswith("Nothing was changed: the user declined")


@both_eras
async def test_what_changed_under_the_dialog_is_asked_again(mode: Mode) -> None:
    state, seen = world(), list[str]()
    mail = WorkflowAction(
        id=9, type=3, settings={"email": {"subject": "s", "body": "b", "to": "a@example.org"}}
    )

    async def edited() -> None:
        state.workflows = [flow(mail)]

    result = await run(state, SCANS, mode, answering("accept", YES, seen=seen, before=edited))

    assert output(result)["started"] is True
    assert ["send an e-mail to 'a@example.org'" in message for message in seen] == [False, True]


async def test_a_set_that_keeps_changing_is_not_acted_on() -> None:
    state, seen = world(), list[str]()

    async def joins() -> None:
        state.documents.append(FakeDocument(id=20 + len(seen), title="Scan", owner=2))

    handler = answering("accept", YES, seen=seen, before=joins)
    result = await run(state, SCANS, "legacy", handler, writes=False)

    assert result.is_error
    assert len(seen) == MAX_DIALOGS


@both_eras
@both_selections
async def test_where_dialogs_are_required_a_client_without_them_gets_no_other_way(
    mode: Mode, selection: Json
) -> None:
    with_ids: Json = {"document_ids": [10]}
    for more in ({}, with_ids):
        result = await run(
            world(), {**selection, **more, "confirm": True}, mode, writes=False, required=True
        )

        assert result.is_error
        assert "PAPERLESS_MCP_REQUIRE_DIALOG" in text(result)
        assert "document_ids" not in text(result)


@both_eras
async def test_hostile_titles_names_and_values_stay_in_their_quotes(mode: Mode) -> None:
    state = world()
    state.documents[-1].title = EVIL
    state.workflows = [
        flow(WorkflowAction(id=2, type=1, settings={"assign_title": EVIL}), name=EVIL)
    ]

    result = await run(state, {"workflow": 3, "document_ids": [11]}, mode, writes=False)

    shown = '''"Scan' to nobody. Confirming would change nothing"'''
    assert text(result).count(shown) == 3


ADDRESSES = [f"{n}{'a' * 50}@{'b' * 60}.{'c' * 60}.example.org" for n in range(19)]


async def sized(title: str, name: str) -> tuple[str, str]:
    """The refusal and the preview of a run on 100 documents, with several actions."""
    state = world()
    state.documents += [FakeDocument(id=n, title=title, owner=2) for n in range(1000, 1100)]
    state.tags += [FakeTag(id=n, name=f"{name}{n}", parent=n - 1) for n in range(61, 81)]
    state.custom_fields += [
        FakeCustomField(id=n, name=f"{name}{n}", data_type="string") for n in range(200, 210)
    ]
    actions = (
        WorkflowAction(
            id=2,
            type=1,
            settings={
                "assign_title": name,
                "assign_tags": [80],
                "assign_owner": 3,
                "assign_custom_fields": list(range(200, 210)),
                "assign_custom_fields_values": {str(n): name for n in range(200, 210)},
            },
        ),
        WorkflowAction(id=3, type=2, settings={"remove_tags": [61]}),
        WorkflowAction(
            id=4,
            type=3,
            settings={"email": {"subject": "s", "body": "b", "to": ",".join(ADDRESSES)}},
        ),
        WorkflowAction(id=5, type=4, settings={"webhook": {"url": "https://example.org/x"}}),
        WorkflowAction(
            id=6, type=8, settings={"ai_suggestion_fields": ["title", "tags", "correspondent"]}
        ),
        WorkflowAction(id=7, type=6),
    )
    state.workflows = [flow().model_copy(update={"actions": actions})]
    chosen: Json = {"document_ids": list(range(1000, 1100))}
    refused = await run(state, chosen, writes=False)
    preview = await run(state, {**chosen, "dry_run": True}, writes=False)
    shown = text(refused).partition(" Hint: ")[0]
    assert all(f"{number} 'Scan" in shown for number in range(1000, 1100))
    assert all(address in shown for address in ADDRESSES)
    assert shown.index(ADDRESSES[0]) < shown.index("1000 'Scan")
    return shown, str(output(preview)["message"])


# As measured, far below the limit (decision-75).
@pytest.mark.parametrize(
    ("title", "name", "most"),
    [
        (
            "Scan of a long and quite ordinary invoice, page 1 of the second quarter",
            "Value ",
            (12_020, 7_164),
        ),
        ("Scan " + chr(0x201D) * 123, chr(0x1F676) * 60, (42_924, 24_564)),
    ],
    ids=["ordinary", "hostile"],
)
async def test_the_size_of_a_run_on_a_hundred_documents_with_several_actions(
    title: str, name: str, most: tuple[int, int]
) -> None:
    shown, preview = await sized(title, name)

    assert (len(shown), len(preview)) == most


@both_eras
async def test_a_run_too_long_to_confirm_is_refused_without_a_dialog(mode: Mode) -> None:
    state, seen = world(), list[str]()
    actions = tuple(
        WorkflowAction(id=n, type=1, settings={"assign_title": f"{n:04}" + "y" * 50})
        for n in range(1200)
    )
    state.workflows = [flow().model_copy(update={"actions": actions})]

    result = await run(
        state,
        {"document_ids": [10], "confirm": True},
        mode,
        answering("accept", YES, seen=seen),
        writes=False,
    )

    assert result.is_error
    assert "at most 100000 can be shown" in text(result)
    assert seen == []
