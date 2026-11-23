"""The confirmation dialog, in memory on both protocol eras (decision-44).

`auto` is 2026-07-28, `legacy` a handshake connection. Declining cases use a fake that refuses
writes, so a write fails the test.
"""

import asyncio
import copy
import json
import logging
from typing import TYPE_CHECKING, Literal, cast, override

import anyio
import pytest
from confirmation import CALLS, NOT_AGAIN, NOTE, PASSWORD, YES, answering, source, world
from fakes.paperless import FakeDocument, FakePaperless
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from harness import serve
from mcp import MCPError
from mcp.types import ElicitResult, InputRequiredResult, ListRootsResult

from mcp_server_paperless.domain.errors import CONFIRM_HINT, ConfirmationRequired, NotConfirmed
from mcp_server_paperless.tools._shared import boundary
from mcp_server_paperless.tools.confirmation import MAX_DIALOGS, Asked, Dialogs

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from confirmation import Handler
    from fastmcp.client.client import CallToolResult
    from fastmcp.client.transports import FastMCPTransport
    from mcp.types import CallToolResult as RawResult

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-44")]

type Mode = Literal["auto", "legacy"]
MODES: tuple[Mode, ...] = ("auto", "legacy")
both_eras = pytest.mark.parametrize("mode", MODES)
HINT = f" Hint: {CONFIRM_HINT}"


def server(fake: FakePaperless, dialogs: Dialogs | None = None, **settings: object) -> FastMCP:
    return serve(fake, toolsets=["all"], dialogs=dialogs, **settings)  # pyright: ignore[reportArgumentType] -- keyword passthrough


async def call(
    served: FastMCP,
    mode: Mode,
    handler: Handler | None,
    tool: str = "delete_note",
    arguments: Mapping[str, object] = NOTE,
) -> CallToolResult:
    async with Client(served, mode=mode, elicitation_handler=handler) as client:
        return await client.call_tool(tool, dict(arguments), raise_on_error=False)


def text(result: CallToolResult | RawResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


# --- every tool -----------------------------------------------------------------------------------


async def test_every_tool_with_a_confirm_parameter_has_a_call_here() -> None:
    tools = await server(source(world())).list_tools()

    confirming = {t.name for t in tools if "confirm" in t.parameters["properties"]}
    assert confirming == CALLS.keys()


@both_eras
@pytest.mark.parametrize("tool", CALLS)
async def test_a_tool_shows_the_refusal_and_runs_only_after_the_user_accepts(
    mode: Mode, tool: str
) -> None:
    refused = await call(server(source(world(), writes=False)), mode, None, tool, CALLS[tool])
    declined: list[str] = []
    no = await call(
        server(source(world(), writes=False)),
        mode,
        answering("decline", seen=declined),
        tool,
        CALLS[tool],
    )
    state = world()
    before = copy.deepcopy(state)
    accepted: list[str] = []
    yes = await call(
        server(source(state)), mode, answering("accept", YES, seen=accepted), tool, CALLS[tool]
    )

    assert refused.is_error
    assert text(refused).startswith("Nothing was changed. Confirming would ")
    assert text(refused).endswith(HINT)
    assert declined == accepted == [text(refused).removesuffix(HINT)]
    assert no.is_error
    assert text(no) == f"Nothing was changed: the user declined. Hint: {NOT_AGAIN}"
    assert not yes.is_error, text(yes)
    assert yes.structured_content is not None
    assert state != before


@both_eras
async def test_confirm_true_on_the_first_call_runs_without_a_dialog(mode: Mode) -> None:
    state, seen = world(), list[str]()

    result = await call(
        server(source(state)),
        mode,
        answering("decline", seen=seen),
        arguments={**NOTE, "confirm": True},
    )

    assert not result.is_error
    assert seen == []
    assert state.documents[0].notes == []


# --- answers --------------------------------------------------------------------------------------


@both_eras
@pytest.mark.parametrize(
    ("action", "content", "reason"),
    [
        ("decline", None, "the user declined"),
        ("cancel", None, "the user closed the dialog without answering"),
        ("accept", {"confirm": False}, "the user declined"),
        ("decline", YES, "the user declined"),
        ("cancel", YES, "the user closed the dialog without answering"),
    ],
)
async def test_an_answer_that_is_no_acceptance_ends_the_call(
    mode: Mode, action: str, content: object, reason: str
) -> None:
    seen: list[str] = []

    result = await call(
        server(source(world(), writes=False)), mode, answering(action, content, seen=seen)
    )

    assert result.is_error
    assert text(result) == f"Nothing was changed: {reason}. Hint: {NOT_AGAIN}"
    assert len(seen) == 1


# --- 2026-07-28: the server's own state ----------------------------------------------------------


NEVER = answering("decline")  # these clients are driven by hand; the handler only declares forms


async def raw(
    client: Client[FastMCPTransport],
    reply: object = None,
    state: str | None = None,
    arguments: Mapping[str, object] = NOTE,
    *,
    key: str = "confirm",
) -> RawResult | InputRequiredResult:
    """One round of a 2026-07-28 call, with exactly this answer and state."""
    return await client.session.call_tool(
        "delete_note",
        dict(arguments),
        input_responses=None if reply is None else {key: cast("ElicitResult", reply)},
        request_state=state,
        allow_input_required=True,
    )


def answer(action: str, content: object = None) -> ElicitResult:
    """An answer without the checks a client makes before it sends one."""
    return ElicitResult.model_construct(action=action, content=content)


ACCEPT = answer("accept", YES)


def state_of(result: RawResult | InputRequiredResult) -> str:
    assert isinstance(result, InputRequiredResult)
    assert result.request_state
    return result.request_state


def refused_text(result: RawResult | InputRequiredResult) -> str:
    assert not isinstance(result, InputRequiredResult)
    assert result.is_error
    return text(result)


@pytest.mark.parametrize(
    "content",
    [
        {"confirm": "true"},
        {"confirm": 1},
        {"confirm": None},
        {},
        {"confirm": True, "extra": "x"},
        {"other": True},
        None,
    ],
)
async def test_a_malformed_answer_changes_nothing(content: object) -> None:
    async with Client(server(source(world(), writes=False)), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        result = refused_text(await raw(client, answer("accept", content), state))

    assert result == (
        f"Nothing was changed: the answer to the dialog was not understood. Hint: {NOT_AGAIN}"
    )


async def test_an_answer_of_another_kind_changes_nothing() -> None:
    async with Client(server(source(world(), writes=False)), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        result = refused_text(await raw(client, ListRootsResult(roots=[]), state))

    assert "was not understood" in result


async def test_an_answer_without_the_servers_state_is_not_read() -> None:
    async with Client(server(source(world(), writes=False)), elicitation_handler=NEVER) as client:
        first = await raw(client, ACCEPT)

    assert isinstance(first, InputRequiredResult)


async def test_a_state_without_an_answer_to_the_dialog_asks_again() -> None:
    async with Client(server(source(world(), writes=False)), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        none = await raw(client, None, state)
        other = await raw(client, ACCEPT, state, key="other")

    assert isinstance(none, InputRequiredResult)
    assert isinstance(other, InputRequiredResult)


async def test_a_forged_state_and_a_state_for_other_arguments_are_rejected() -> None:
    async with Client(server(source(world(), writes=False)), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        for wrong, arguments in (
            ("v1.forged", NOTE),
            (state[:-2] + ("AA" if state[-2:] != "AA" else "BB"), NOTE),
            (state, {"document_id": 1, "note_id": 2}),
            (state, {**NOTE, "confirm": True}),
        ):
            with pytest.raises(MCPError, match="Invalid or expired requestState"):
                _ = await raw(client, ACCEPT, wrong, arguments)


async def test_a_state_of_another_tool_is_rejected() -> None:
    async with Client(server(source(world(), writes=False)), elicitation_handler=NEVER) as client:
        asked = await client.session.call_tool(
            "delete_document", {"document_id": 1}, allow_input_required=True
        )
        with pytest.raises(MCPError, match="Invalid or expired requestState"):
            _ = await raw(client, ACCEPT, state_of(asked), {"document_id": 1})


async def test_a_state_of_another_process_is_rejected() -> None:
    async with Client(server(source(world(), writes=False)), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
    async with Client(server(source(world(), writes=False)), elicitation_handler=NEVER) as client:
        with pytest.raises(MCPError, match="Invalid or expired requestState"):
            _ = await raw(client, ACCEPT, state)


@pytest.mark.parametrize("first", [answer("decline"), answer("cancel"), answer("accept", {})])
async def test_a_state_is_used_up_by_any_answer(first: ElicitResult) -> None:
    async with Client(server(source(world(), writes=False)), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        _ = refused_text(await raw(client, first, state))
        again = await raw(client, ACCEPT, state)

    assert isinstance(again, InputRequiredResult)


async def test_an_accepted_state_does_not_work_twice_even_over_a_new_connection() -> None:
    state = world()
    served = server(source(state))
    async with Client(served, elicitation_handler=NEVER) as client:
        token = state_of(await raw(client))
        done = await raw(client, ACCEPT, token)
        assert not isinstance(done, InputRequiredResult)
        assert not done.is_error
    # The same target again: the server's lifespan ended and started in between.
    state.documents[0].notes = list(world().documents[0].notes)
    async with Client(served, elicitation_handler=NEVER) as client:
        replayed = await raw(client, ACCEPT, token)

    assert isinstance(replayed, InputRequiredResult)
    assert state.documents[0].notes


async def test_an_expired_dialog_is_told_and_changes_nothing() -> None:
    now = [1000.0]
    dialogs = Dialogs(clock=lambda: now[0])
    async with Client(
        server(source(world(), writes=False), dialogs), elicitation_handler=NEVER
    ) as client:
        state = state_of(await raw(client))
        now[0] += dialogs.timeout
        result = refused_text(await raw(client, ACCEPT, state))

    assert result == f"Nothing was changed: the dialog has expired. Hint: {NOT_AGAIN}"


async def test_the_sdk_keeps_a_state_longer_than_its_dialog() -> None:
    """Otherwise an expired dialog would be a rejected request instead of a readable answer."""
    async with Client(
        server(source(world(), writes=False), Dialogs(timeout=1)), elicitation_handler=NEVER
    ) as client:
        state = state_of(await raw(client))
        await anyio.sleep(1.1)
        result = refused_text(await raw(client, ACCEPT, state))

    assert "the dialog has expired" in result


async def test_a_dialog_counts_until_it_expires() -> None:
    now = [1000.0]
    dialogs = Dialogs(clock=lambda: now[0])
    async with Client(server(source(world()), dialogs), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        now[0] += dialogs.timeout - 1
        result = await raw(client, ACCEPT, state)

    assert not isinstance(result, InputRequiredResult)
    assert not result.is_error


def test_the_default_wait_is_ten_minutes_with_progress_every_thirty_seconds() -> None:
    assert (Dialogs().timeout, Dialogs().progress_every) == (600, 30)


def test_a_used_state_is_remembered_until_it_expires_and_not_longer() -> None:
    now = [0.0]
    dialogs = Dialogs(clock=lambda: now[0])

    assert dialogs.use("a", 10)
    assert not dialogs.use("a", 10)
    now[0] = 10
    assert dialogs.use("b", 20)
    assert dialogs.use("a", 10)


async def test_a_state_this_server_did_not_make_is_no_state() -> None:
    """Sealed by the SDK, but not a dialog's: another feature's state proves no question."""
    good = {"nonce": "n", "fingerprint": "f", "expires": 1.5}
    for wrong in ({}, {**good, "nonce": 1}, {**good, "expires": "1.5"}, {**good, "extra": "x"}):
        assert Asked.parse(json.dumps(wrong)) is None
    for sealed in (None, "", "x", "[]", "null"):
        assert Asked.parse(sealed) is None
    asked = Asked(nonce="n", fingerprint="f", expires=1.5)
    assert Asked.parse(asked.model_dump_json()) == asked


# --- what changed while the dialog was open -----------------------------------------------------


async def test_a_target_whose_effect_changed_asks_again_and_changes_nothing() -> None:
    state = world()
    async with Client(server(source(state, writes=False)), elicitation_handler=NEVER) as client:
        first = await raw(client)
        state.documents[0].title = "renamed"
        second = await raw(client, ACCEPT, state_of(first))

    assert isinstance(second, InputRequiredResult)
    assert "renamed" in second.model_dump_json()
    assert "renamed" not in first.model_dump_json()


async def test_a_handshake_call_asks_at_most_three_times_and_then_refuses() -> None:
    state, seen = world(), list[str]()

    async def rename() -> None:
        state.documents[0].title = f"changed {len(seen)}"

    result = await call(
        server(source(state, writes=False)),
        "legacy",
        answering("accept", YES, seen=seen, before=rename),
    )

    assert len(seen) == MAX_DIALOGS == 3
    assert text(result).endswith(HINT)
    assert "changed 3" in text(result)


async def test_the_third_and_last_dialog_of_a_handshake_call_still_counts() -> None:
    state, seen = world(), list[str]()

    async def rename_twice() -> None:
        state.documents[0].title = f"changed {min(len(seen), 2)}"

    result = await call(
        server(source(state)), "legacy", answering("accept", YES, seen=seen, before=rename_twice)
    )

    assert len(seen) == MAX_DIALOGS
    assert not result.is_error
    assert state.documents[0].notes == []


@both_eras
async def test_a_target_that_changed_once_runs_after_the_second_acceptance(mode: Mode) -> None:
    state, seen = world(), list[str]()

    async def rename() -> None:
        state.documents[0].title = "renamed"

    result = await call(
        server(source(state)), mode, answering("accept", YES, seen=seen, before=rename)
    )

    assert not result.is_error
    assert ["renamed" in message for message in seen] == [False, True]
    assert state.documents[0].notes == []


@both_eras
async def test_other_targets_behind_the_same_text_ask_again(mode: Mode) -> None:
    """The trash shows ten entries and counts the rest: one of the rest is swapped meanwhile."""
    state, seen = world(), list[str]()
    state.trash = [FakeDocument(id=number, title="Same") for number in range(100, 112)]

    async def swap() -> None:
        state.trash[0].id = 50

    result = await call(
        server(source(state)),
        mode,
        answering("accept", YES, seen=seen, before=swap),
        "empty_trash",
        {"all": True},
    )

    assert not result.is_error
    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert result.structured_content is not None
    assert 50 in cast("list[int]", result.structured_content["document_ids"])


@both_eras
async def test_a_target_that_is_gone_is_the_error_it_always_was(mode: Mode) -> None:
    state = world()

    async def gone() -> None:
        state.documents[0].notes = []

    result = await call(
        server(source(state, writes=False)), mode, answering("accept", YES, before=gone)
    )

    assert result.is_error
    assert "has no note 1" in text(result)


# --- waiting (handshake) --------------------------------------------------------------------------


def never_answering(cancelled: anyio.Event) -> Handler:
    async def hang() -> None:
        try:
            await anyio.sleep_forever()
        except BaseException:
            cancelled.set()
            raise

    return answering("accept", YES, before=hang)


async def test_a_dialog_nobody_answers_ends_and_closes_at_the_client() -> None:
    cancelled = anyio.Event()
    async with Client(
        server(source(world(), writes=False), Dialogs(timeout=0.1)),
        mode="legacy",
        elicitation_handler=never_answering(cancelled),
    ) as client:
        with anyio.fail_after(10):
            result = await client.call_tool("delete_note", NOTE, raise_on_error=False)
            await cancelled.wait()

    assert text(result) == (
        f"Nothing was changed: nobody answered the dialog in time. Hint: {NOT_AGAIN}"
    )


async def test_progress_is_reported_while_the_dialog_is_open_and_not_after() -> None:
    reports: list[tuple[float, str | None]] = []
    twice = anyio.Event()

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        assert total == 600
        reports.append((progress, message))
        if len(reports) > 1:
            twice.set()

    async def wait() -> None:
        with anyio.fail_after(5):
            await twice.wait()

    async with Client(
        server(source(world()), Dialogs(progress_every=0.02)),
        mode="legacy",
        elicitation_handler=answering("accept", YES, before=wait),
    ) as client:
        result = await client.call_tool("delete_note", NOTE, progress_handler=on_progress)
        count = len(reports)
        await anyio.sleep(0.1)

        reporting = [t for t in asyncio.all_tasks() if "_report_progress" in repr(t)]

    assert not result.is_error
    assert reporting == []
    assert len(reports) == count
    assert [progress for progress, _ in reports] == sorted({progress for progress, _ in reports})
    assert {message for _, message in reports} == {"Waiting for the user to confirm."}


async def test_a_call_the_client_gives_up_changes_nothing() -> None:
    state = world()
    opened = anyio.Event()

    async def slow() -> None:
        opened.set()
        await anyio.sleep(0.2)

    async with Client(
        server(source(state, writes=False)),
        mode="legacy",
        elicitation_handler=answering("accept", YES, before=slow),
    ) as client:
        async with anyio.create_task_group() as group:

            async def given_up() -> None:
                _ = await client.call_tool("delete_note", NOTE)

            _ = group.start_soon(given_up)
            await opened.wait()
            group.cancel_scope.cancel()
        await anyio.sleep(0.4)
        assert state.documents[0].notes
        # The connection goes on.
        assert await client.list_tools()


async def test_an_open_dialog_holds_nothing_back() -> None:
    """Meanwhile other calls read and write, the same object included."""
    state, meanwhile = world(), list[bool]()
    served = server(source(state))

    async def elsewhere() -> None:
        async with Client(served, mode="legacy") as other:
            read = await other.call_tool("get_document", {"document_id": 1}, raise_on_error=False)
            wrote = await other.call_tool("delete_note", {**NOTE, "confirm": True})
            meanwhile.extend([read.is_error, wrote.is_error])

    with anyio.fail_after(10):
        result = await call(served, "legacy", answering("accept", YES, before=elsewhere))

    assert meanwhile == [False, False]
    assert "has no note 1" in text(result)


# --- the rest of the server ----------------------------------------------------------------------


@pytest.fixture
def every_record(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog, also for FastMCP's own logger (it does not propagate to the root logger)."""
    fastmcp = logging.getLogger("fastmcp")
    fastmcp.addHandler(caplog.handler)
    yield caplog
    fastmcp.removeHandler(caplog.handler)


def logged(records: pytest.LogCaptureFixture) -> str:
    return records.text + str([vars(r) for r in records.records])


def outcomes(records: pytest.LogCaptureFixture) -> list[tuple[object, object]]:
    return [
        (vars(r)["outcome"], vars(r).get("error"))
        for r in records.records
        if r.getMessage() == "tool call"
    ]


@pytest.mark.parametrize(
    ("mode", "action", "expected"),
    [
        ("auto", "accept", [("asked", None), ("ok", None)]),
        ("legacy", "accept", [("ok", None)]),
        ("auto", "decline", [("asked", None), ("error", "NotConfirmed")]),
        ("legacy", "decline", [("error", "NotConfirmed")]),
    ],
)
async def test_rounds_are_logged_by_outcome_without_dialog_or_answer(
    every_record: pytest.LogCaptureFixture,
    mode: Mode,
    action: str,
    expected: list[tuple[object, object]],
) -> None:
    every_record.set_level(logging.INFO)
    seen: list[str] = []

    _ = await call(
        server(source(world())),
        mode,
        answering(action, YES if action == "accept" else None, seen=seen),
    )

    assert outcomes(every_record) == expected
    assert len(seen) == 1
    assert "e2e-public-invoice" in seen[0]
    for shown in ("e2e-public-invoice", "zebra", "Confirming would", "'confirm': True"):
        assert shown not in logged(every_record)


async def test_a_refusal_for_want_of_a_dialog_is_logged_as_before(
    every_record: pytest.LogCaptureFixture,
) -> None:
    every_record.set_level(logging.INFO)

    _ = await call(server(source(world(), writes=False)), "auto", None)

    assert outcomes(every_record) == [("error", "ConfirmationRequired")]
    assert "zebra" not in logged(every_record)


@both_eras
async def test_the_dialog_holds_no_secret_of_the_call(mode: Mode) -> None:
    seen: list[str] = []
    for tool, arguments in CALLS.items():
        if PASSWORD in json.dumps(arguments):
            result = await call(
                server(source(world(), writes=False)),
                mode,
                answering("decline", seen=seen),
                tool,
                arguments,
            )
            assert PASSWORD not in text(result)

    assert len(seen) == 3
    assert PASSWORD not in "".join(seen)


@both_eras
async def test_a_bug_in_the_accepted_run_is_masked_like_any_other(
    every_record: pytest.LogCaptureFixture, mode: Mode
) -> None:
    every_record.set_level(logging.INFO)

    class Broken(FakePaperless):
        @override
        async def delete_note(self, document_id: int, note_id: int) -> None:
            message = "zebra content"
            raise KeyError(message)

    result = await call(
        server(Broken(world(), user="admin", allow_writes=True)), mode, answering("accept", YES)
    )

    assert text(result) == "Internal error in delete_note; the server log has details."
    assert "zebra" not in logged(every_record)


@both_eras
async def test_read_only_mode_hides_the_tools_before_anything_is_asked(mode: Mode) -> None:
    seen: list[str] = []

    result = await call(
        server(source(world(), writes=False), read_only=True),
        mode,
        answering("accept", YES, seen=seen),
    )

    assert result.is_error
    assert seen == []


async def test_a_tool_called_on_the_server_without_a_client_refuses() -> None:
    served = FastMCP("in-process")

    @served.tool
    @boundary
    async def destroy() -> None:
        ConfirmationRequired.require("destroy")

    with pytest.raises(ToolError) as raised:
        _ = await served.call_tool("destroy")

    assert str(raised.value).endswith(HINT)


async def test_a_refused_answer_is_logged_at_info_not_error() -> None:
    async def tool() -> None:
        reason = "the user declined"
        raise NotConfirmed(reason)

    with pytest.raises(ToolError) as raised:
        await boundary(tool)()

    assert raised.value.log_level == logging.INFO


async def test_the_accepted_run_gets_the_same_arguments() -> None:
    """Outside a server the boundary still refuses, and keeps positional and keyword arguments."""
    seen: list[tuple[int, int]] = []

    async def tool(a: int, *, b: int) -> None:
        seen.append((a, b))
        ConfirmationRequired.require("x")

    with pytest.raises(ToolError):
        await boundary(tool)(1, b=2)

    assert seen == [(1, 2)]
