"""The pick dialog for an ambiguous name, in memory on both protocol eras (decision-109).

Declining cases use a fake that refuses writes, so a write fails the test.
"""

import contextlib
import copy
import json
import logging
import re
from typing import TYPE_CHECKING, Literal, cast, override

import anyio
import pytest
from choices import (
    CASES,
    FIRST,
    NO_NAMES,
    NOT_PICKED,
    READS,
    SECOND,
    TWIN,
    by_id,
    doubled,
    picks,
)
from confirmation import NOT_AGAIN, YES, answering, source, world
from fakes.paperless import FakePaperless, FakeTag, FakeTaxonomy
from fastmcp import Client, FastMCP
from harness import serve
from mcp import MCPError
from mcp.types import ElicitResult, InputRequiredResult, ListRootsResult

from mcp_server_paperless.domain.choices import Choice, Pick, Question, note_write, picked
from mcp_server_paperless.domain.errors import Ambiguous, PermissionDenied, UpstreamUnavailable
from mcp_server_paperless.tools import _shared
from mcp_server_paperless.tools._shared import ANNOTATIONS, boundary
from mcp_server_paperless.tools.confirmation import DIALOGS, MAX_CHOICES, MAX_PICKS, Asked, Dialogs

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator, Mapping

    from confirmation import Handler, Json
    from fakes.paperless import FakeState
    from fastmcp.client.client import CallToolResult
    from fastmcp.client.transports import FastMCPTransport
    from mcp.types import CallToolResult as RawResult

    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-109")]

type Mode = Literal["auto", "legacy"]
MODES: tuple[Mode, ...] = ("auto", "legacy")
both_eras = pytest.mark.parametrize("mode", MODES)

CALL: Json = {"document_id": 10, "correspondent": TWIN}
ERROR = (
    "2 correspondents match 'TWIN': 'Twin' (id 50), 'twin' (id 51). "
    "Hint: Pass the id of the one you mean."
)
QUESTION = (
    "Update document: 2 correspondents match 'TWIN'. Which one is meant? Nothing was changed."
)


def server(fake: FakePaperless, dialogs: Dialogs | None = None, **settings: object) -> FastMCP:
    return serve(fake, toolsets=["all"], dialogs=dialogs, **settings)  # pyright: ignore[reportArgumentType] -- keyword passthrough


async def call(
    served: FastMCP,
    mode: Mode,
    handler: Handler | None,
    tool: str = "update_document",
    arguments: Mapping[str, object] = CALL,
) -> CallToolResult:
    async with Client(served, mode=mode, elicitation_handler=handler) as client:
        return await client.call_tool(tool, dict(arguments), raise_on_error=False)


def text(result: CallToolResult | RawResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def reading() -> FakePaperless:
    """A fake that fails the test on any write."""
    return source(doubled(), writes=False)


# --- pick, decline, cancel, malformed ------------------------------------------------------------


@both_eras
@pytest.mark.parametrize("chosen", [FIRST, SECOND])
async def test_the_call_goes_on_with_the_picked_match_and_writes_once(
    mode: Mode, chosen: int
) -> None:
    fake, shown = source(doubled()), picks(chosen)

    result = await call(server(fake), mode, shown.handler)

    assert not result.is_error, text(result)
    assert shown.questions == [QUESTION]
    assert shown.confirmations == []
    assert len(fake.writes) == 1
    assert next(d for d in fake.state.documents if d.id == 10).correspondent == chosen


@both_eras
async def test_the_form_offers_the_titled_matches_and_preselects_none(mode: Mode) -> None:
    shown = picks(None)
    state = doubled()
    state.correspondents[-1].owner = 3

    _ = await call(server(source(state, writes=False)), mode, shown.handler)

    [schema] = shown.schemas
    assert schema == {
        "type": "object",
        "properties": {
            "choice": {
                "type": "string",
                "title": "Which one?",
                "oneOf": [
                    {"const": "50", "title": "'Twin' (id 50, 1 document)"},
                    {"const": "51", "title": "'twin' (id 51, 1 document), owner 'e2e-reader'"},
                ],
            }
        },
        "required": ["choice"],
    }


@both_eras
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_without_a_pick_the_error_is_the_old_one_and_one_sentence(
    mode: Mode, action: str
) -> None:
    plain = await call(server(reading()), mode, None)
    result = await call(server(reading()), mode, answering(action, {"choice": "50"}))

    assert plain.is_error
    assert text(plain) == ERROR
    assert result.is_error
    assert text(result) == f"{ERROR} {NOT_PICKED}"


WRONG: list[object] = [
    {"choice": "52"},
    {"choice": ""},
    {"choice": "Twin"},
    {"choice": "50 "},
    {"choice": 50},
    {"choice": True},
    {"choice": None},
    {"choice": ["50"]},
    {"choice": "50", "extra": "x"},
    {"confirm": True},
    {"choice": "50", "confirm": True},
    {},
    None,
]


@pytest.mark.parametrize("content", WRONG)
async def test_a_malformed_2026_07_28_answer_is_the_old_error(content: object) -> None:
    async with Client(server(reading()), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        result = refused(await raw(client, answer("accept", content), state))

    assert result == ERROR


async def test_an_answer_of_another_kind_is_the_old_error() -> None:
    async with Client(server(reading()), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        assert refused(await raw(client, ListRootsResult(roots=[]), state)) == ERROR


# --- who is asked --------------------------------------------------------------------------------


@both_eras
async def test_a_dry_run_is_never_asked(mode: Mode) -> None:
    shown = picks(FIRST)
    arguments: Json = {
        "document_ids": [10],
        "operation": {"op": "add_tags", "tags": [TWIN]},
        "dry_run": True,
    }

    result = await call(server(reading()), mode, shown.handler, "bulk_edit_documents", arguments)

    assert result.is_error
    assert "2 tags match" in text(result)
    assert shown.questions == []


def test_the_default_of_dry_run_counts() -> None:
    """A tool that previews by default must not ask in a preview; none does today."""

    async def update_document(*, dry_run: bool = True) -> bool:
        return dry_run

    async def delete_document(*, dry_run: bool = False) -> bool:
        return dry_run

    assert _shared.asking_title(update_document, {}) is None
    assert _shared.asking_title(update_document, {"dry_run": False}) == "Update document"
    assert _shared.asking_title(delete_document, {}) == "Delete document"
    assert _shared.asking_title(delete_document, {"dry_run": True}) is None


def test_a_tool_that_reads_or_is_unknown_does_not_ask() -> None:
    async def get_document() -> None: ...

    async def unknown() -> None: ...

    async def upload_documents() -> None: ...

    assert _shared.asking_title(get_document, {}) is None
    assert _shared.asking_title(unknown, {}) is None
    assert _shared.asking_title(upload_documents, {}) == "Upload several documents"


@both_eras
async def test_read_only_mode_asks_nothing(mode: Mode) -> None:
    shown = picks(FIRST)

    result = await call(server(reading(), read_only=True), mode, shown.handler)

    assert result.is_error
    assert shown.questions == []


@both_eras
async def test_dialogs_required_change_nothing_about_a_pick(mode: Mode) -> None:
    fake, shown = source(doubled()), picks(SECOND)
    done = await call(server(fake, require_dialog=True), mode, shown.handler)
    none = await call(server(reading(), require_dialog=True), mode, None)
    no = await call(server(reading(), require_dialog=True), mode, picks(None).handler)

    assert not done.is_error, text(done)
    assert len(fake.writes) == 1
    assert text(none) == ERROR
    assert text(no) == f"{ERROR} {NOT_PICKED}"


async def test_a_tool_called_on_the_server_without_a_client_gets_the_old_error() -> None:
    served = server(reading())
    async with Client(served):
        with pytest.raises(Exception, match="2 correspondents match") as raised:
            _ = await served.call_tool("update_document", CALL)

    assert str(raised.value) == ERROR


# --- how many ------------------------------------------------------------------------------------


def with_tags(count: int) -> FakeState:
    state = world()
    for number in range(count):
        state.tags.append(FakeTag(id=100 + number, name="Same", owner=number))
    return state


@both_eras
@pytest.mark.parametrize(("count", "asked"), [(2, 1), (MAX_CHOICES, 1), (MAX_CHOICES + 1, 0)])
async def test_twenty_matches_make_a_dialog_and_one_more_does_not(
    mode: Mode, count: int, asked: int
) -> None:
    shown, state = picks(100 + count - 1), with_tags(count)
    arguments: Json = {"document_id": 10, "tags_add": ["Same"]}

    result = await call(
        server(source(state, writes=bool(asked))), mode, shown.handler, arguments=arguments
    )

    assert MAX_CHOICES == 20
    assert len(shown.questions) == asked
    assert result.is_error is not bool(asked)
    if not asked:
        assert text(result).startswith("21 tags match 'Same': 'Same' (id 100)")
        assert text(result).endswith("Hint: Pass the id of the one you mean.")


def many_names(count: int) -> tuple[FakeState, Json]:
    """A call with `count` different ambiguous names, each a tag."""
    state = world()
    names = [f"Word{n}" for n in range(count)]
    for n, name in enumerate(names):
        state.tags.append(FakeTag(id=100 + 2 * n, name=name))
        state.tags.append(FakeTag(id=101 + 2 * n, name=name))
    return state, {"document_id": 10, "tags_add": names}


@both_eras
async def test_two_ambiguous_names_are_two_dialogs_and_one_write(mode: Mode) -> None:
    fake, shown = source(doubled()), picks(SECOND, FIRST)
    arguments: Json = {**CALL, "tags_add": [TWIN]}

    result = await call(server(fake), mode, shown.handler, arguments=arguments)

    assert not result.is_error, text(result)
    assert len(shown.questions) == len(set(shown.questions)) == 2
    assert len(fake.writes) == 1
    changed = next(d for d in fake.state.documents if d.id == 10)
    assert changed.correspondent in {FIRST, SECOND}
    asked_first = "correspondents" in shown.questions[0]
    assert (changed.correspondent, changed.tags) == (
        (SECOND, [FIRST]) if asked_first else (FIRST, [SECOND])
    )


@both_eras
async def test_a_second_name_that_is_declined_leaves_the_first_unwritten(mode: Mode) -> None:
    shown = picks(SECOND, None)
    arguments: Json = {**CALL, "tags_add": [TWIN]}

    result = await call(server(reading()), mode, shown.handler, arguments=arguments)

    assert result.is_error
    assert len(shown.questions) == 2
    kind = "tags" if "tags" in shown.questions[1] else "correspondents"
    assert text(result) == f"{ERROR.replace('correspondents', kind)} {NOT_PICKED}"


@both_eras
async def test_the_same_word_twice_in_one_role_is_one_question(mode: Mode) -> None:
    fake, shown = source(doubled()), picks(SECOND)

    result = await call(
        server(fake), mode, shown.handler, "set_permissions",
        {"kind": "document", "items": [10], "view_users": [TWIN], "change_users": [TWIN]},
    )  # fmt: skip

    assert not result.is_error, text(result)
    assert len(shown.questions) == 1


@both_eras
@pytest.mark.parametrize(("count", "works"), [(MAX_PICKS, True), (MAX_PICKS + 1, False)])
async def test_five_names_are_asked_and_the_sixth_is_the_old_error(
    mode: Mode, count: int, *, works: bool
) -> None:
    state, arguments = many_names(count)
    fake = source(state, writes=works)
    shown = picks(*(101 + 2 * n for n in range(count)))

    result = await call(server(fake), mode, shown.handler, arguments=arguments)

    assert MAX_PICKS == 5
    assert len(shown.questions) == MAX_PICKS
    if works:
        assert not result.is_error, text(result)
        assert sorted(next(d for d in state.documents if d.id == 10).tags) == [
            101 + 2 * n for n in range(count)
        ]
        assert len(fake.writes) == 1
    else:
        assert text(result).startswith("2 tags match 'Word5'")
        assert NOT_PICKED not in text(result)


# --- what changes while the user is asked --------------------------------------------------------


RENAME: Json = {"workflow": TWIN, "name": "New"}


@both_eras
async def test_matches_that_changed_are_asked_again(mode: Mode) -> None:
    state, shown = doubled(), picks(SECOND)
    third = state.workflows[-1].model_copy(update={"id": 60})

    async def one_more() -> None:
        if len(shown.questions) == 1:
            state.workflows.append(third)

    fake = source(state)
    result = await call(server(fake), mode, shown.before(one_more), "update_workflow", RENAME)

    assert not result.is_error, text(result)
    assert ["2 workflows" in shown.questions[0], "3 workflows" in shown.questions[1]] == [True] * 2
    assert [w.id for w in state.workflows if w.name == "New"] == [SECOND]


@both_eras
async def test_matches_that_keep_changing_end_with_the_old_error(mode: Mode) -> None:
    state, shown = doubled(), picks(SECOND)

    async def one_more() -> None:
        number = 60 + len(shown.questions)
        state.workflows.append(state.workflows[-1].model_copy(update={"id": number}))

    result = await call(
        server(source(state, writes=False)), mode, shown.before(one_more), "update_workflow", RENAME
    )

    assert len(shown.questions) == MAX_PICKS
    assert text(result).startswith("7 workflows match 'TWIN'")


@both_eras
@pytest.mark.parametrize("gone", [FIRST, SECOND])
async def test_of_two_matches_the_other_one_is_never_taken_for_the_picked(
    mode: Mode, gone: int
) -> None:
    """One of two is deleted meanwhile: the name is unique now."""
    state, shown = doubled(), picks(SECOND)

    async def delete() -> None:
        state.workflows[:] = [w for w in state.workflows if w.id != gone]

    result = await call(
        server(source(state, writes=gone == FIRST)),
        mode,
        shown.before(delete),
        "update_workflow",
        RENAME,
    )

    assert len(shown.questions) == 1
    if gone == FIRST:
        assert [w.id for w in state.workflows if w.name == "New"] == [SECOND]
    else:
        assert text(result).startswith(
            "The workflow the user picked (id 51) does not match 'TWIN' any more."
        )


@both_eras
async def test_after_a_no_a_name_that_became_unique_is_not_written(mode: Mode) -> None:
    state, shown = doubled(), picks(None)

    async def delete() -> None:
        state.workflows[:] = [w for w in state.workflows if w.id != SECOND]

    result = await call(
        server(source(state, writes=False)), mode, shown.before(delete), "update_workflow", RENAME
    )

    assert text(result).startswith("2 workflows match 'TWIN'")
    assert text(result).endswith(NOT_PICKED)


# --- a pick is no confirmation -------------------------------------------------------------------

DELETE: Json = {"kind": "tag", "item": TWIN}


@both_eras
async def test_a_pick_and_a_confirmation_are_asked_both(mode: Mode) -> None:
    fake, shown = source(doubled()), picks(SECOND)

    result = await call(server(fake), mode, shown.handler, "delete_taxonomy_item", DELETE)

    assert not result.is_error, text(result)
    assert len(shown.questions) == len(shown.confirmations) == 1
    assert "'twin'" in shown.confirmations[0]
    assert [t.id for t in fake.state.tags if t.id in {FIRST, SECOND}] == [FIRST]


@both_eras
async def test_a_pick_without_the_confirmation_changes_nothing(mode: Mode) -> None:
    shown = picks(SECOND, confirm=False)

    result = await call(server(reading()), mode, shown.handler, "delete_taxonomy_item", DELETE)

    assert len(shown.questions) == len(shown.confirmations) == 1
    assert text(result) == f"Nothing was changed: the user declined. Hint: {NOT_AGAIN}"


@both_eras
async def test_confirm_true_does_not_pick(mode: Mode) -> None:
    fake, shown = source(doubled()), picks(SECOND)
    done = await call(
        server(fake), mode, shown.handler, "delete_taxonomy_item", {**DELETE, "confirm": True}
    )
    no = await call(
        server(reading()), mode, picks(None).handler, "delete_taxonomy_item",
        {**DELETE, "confirm": True},
    )  # fmt: skip

    assert not done.is_error, text(done)
    assert (len(shown.questions), shown.confirmations) == (1, [])
    assert [t.id for t in fake.state.tags if t.id in {FIRST, SECOND}] == [FIRST]
    assert text(no).endswith(NOT_PICKED)


@both_eras
async def test_with_dialogs_required_confirm_true_is_asked_after_the_pick(mode: Mode) -> None:
    fake, shown = source(doubled()), picks(SECOND)

    result = await call(
        server(fake, require_dialog=True), mode, shown.handler, "delete_taxonomy_item",
        {**DELETE, "confirm": True},
    )  # fmt: skip

    assert not result.is_error, text(result)
    assert len(shown.questions) == len(shown.confirmations) == 1


@both_eras
async def test_a_pick_after_a_confirmation_takes_the_yes_back(mode: Mode) -> None:
    """A word that becomes ambiguous while the confirmation is open: pick, then confirm again."""
    state, shown = world(), picks(FIRST)
    state.tags.append(FakeTag(id=FIRST, name="Twin"))

    async def double() -> None:
        if not shown.questions and len(shown.confirmations) == 1:
            state.tags.append(FakeTag(id=SECOND, name="twin"))

    result = await call(
        server(source(state)), mode, shown.before(double), "delete_taxonomy_item", DELETE
    )

    assert not result.is_error, text(result)
    assert (len(shown.questions), len(shown.confirmations)) == (1, 2)
    assert [t.id for t in state.tags if t.id in {FIRST, SECOND}] == [SECOND]


# --- never after a write -------------------------------------------------------------------------

ANY = Question("things", "x", (Choice("1", "one"), Choice("2", "two")))


def bare() -> FastMCP:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncGenerator[dict[str, object]]:
        yield {DIALOGS: Dialogs()}

    return FastMCP("synthetic", lifespan=lifespan)


def synthetic(*, writes: bool) -> tuple[FastMCP, list[str]]:
    """A server with a tool that (perhaps) writes and then finds a name ambiguous."""
    served, runs = bare(), list[str]()

    async def add_note() -> str:
        runs.append("run")
        if writes:
            note_write()
        answer = picked(ANY)
        if answer is None:
            message = "Two match."
            raise Ambiguous(message, hint="Pass the id.", question=ANY)
        return answer

    _ = served.tool(boundary(add_note), name="synthetic")
    return served, runs


@both_eras
@pytest.mark.parametrize("writes", [False, True])
async def test_a_call_that_has_written_asks_nothing(mode: Mode, *, writes: bool) -> None:
    served, runs = synthetic(writes=writes)
    shown = picks(2)

    result = await call(served, mode, shown.handler, "synthetic", {})

    if writes:
        assert text(result) == "Two match. Hint: Pass the id."
        assert (shown.questions, runs) == ([], ["run"])
    else:
        assert not result.is_error
        assert len(shown.questions) == 1


@both_eras
@pytest.mark.parametrize(
    "question",
    [
        None,
        Question("things", "x", ()),
        Question("things", "x", (Choice("1", "one"),)),
    ],
)
async def test_without_two_choices_there_is_nothing_to_ask(
    mode: Mode, question: Question | None
) -> None:
    served, shown = bare(), picks(1)

    async def add_note() -> None:
        message = "Two match."
        raise Ambiguous(message, question=question)

    _ = served.tool(boundary(add_note), name="synthetic")
    result = await call(served, mode, shown.handler, "synthetic", {})

    assert text(result) == "Two match."
    assert shown.questions == []


@both_eras
async def test_a_service_that_ignores_the_pick_is_asked_once(mode: Mode) -> None:
    served, shown = bare(), picks(1)

    async def add_note() -> None:
        message = "Two match."
        raise Ambiguous(message, question=ANY)

    _ = served.tool(boundary(add_note), name="synthetic")
    result = await call(served, mode, shown.handler, "synthetic", {})

    assert text(result) == "Two match."
    assert len(shown.questions) == 1


# --- 2026-07-28: the server's own state ----------------------------------------------------------

NEVER = answering("decline")  # these clients are driven by hand; the handler only declares forms


async def raw(  # noqa: PLR0913 -- the parts of a round
    client: Client[FastMCPTransport],
    reply: object = None,
    state: str | None = None,
    arguments: Mapping[str, object] = CALL,
    *,
    key: str = "choice",
    tool: str = "update_document",
) -> RawResult | InputRequiredResult:
    """One round of a 2026-07-28 call, with exactly this answer and state."""
    return await client.session.call_tool(
        tool,
        dict(arguments),
        input_responses=None if reply is None else {key: cast("ElicitResult", reply)},
        request_state=state,
        allow_input_required=True,
    )


def answer(action: str, content: object = None) -> ElicitResult:
    """An answer without the checks a client makes before it sends one."""
    return ElicitResult.model_construct(action=action, content=content)


PICK = answer("accept", {"choice": "51"})


def state_of(result: RawResult | InputRequiredResult) -> str:
    assert isinstance(result, InputRequiredResult)
    assert result.request_state
    return result.request_state


def offered(result: RawResult | InputRequiredResult) -> list[str]:
    """The values of a question's options."""
    assert isinstance(result, InputRequiredResult)
    return re.findall(r'"const":"([^"]+)"', result.model_dump_json())


def refused(result: RawResult | InputRequiredResult) -> str:
    assert not isinstance(result, InputRequiredResult)
    assert result.is_error
    return text(result)


async def test_an_answer_without_the_servers_state_is_not_read() -> None:
    async with Client(server(reading()), elicitation_handler=NEVER) as client:
        first = await raw(client, PICK)

    assert isinstance(first, InputRequiredResult)


async def test_a_state_without_its_answer_is_the_old_error() -> None:
    async with Client(server(reading()), elicitation_handler=NEVER) as client:
        for reply, key in ((None, "choice"), (PICK, "other"), (answer("accept", YES), "confirm")):
            state = state_of(await raw(client))
            assert refused(await raw(client, reply, state, key=key)) == ERROR


async def test_a_forged_or_foreign_state_is_rejected() -> None:
    async with Client(server(reading()), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        for wrong, arguments, tool in (
            ("v1.forged", CALL, "update_document"),
            (state[:-2] + ("AA" if state[-2:] != "AA" else "BB"), CALL, "update_document"),
            (state, {**CALL, "document_id": 11}, "update_document"),
            (state, {**CALL, "confirm": True}, "update_document"),
            (state, {"document_id": 10}, "delete_document"),
        ):
            with pytest.raises(MCPError, match="Invalid or expired requestState"):
                _ = await raw(client, PICK, wrong, arguments, tool=tool)
    async with Client(server(reading()), elicitation_handler=NEVER) as client:
        with pytest.raises(MCPError, match="Invalid or expired requestState"):
            _ = await raw(client, PICK, state)


@pytest.mark.parametrize("first", [answer("decline"), answer("accept", {}), None])
async def test_a_state_is_used_up_by_any_answer(first: ElicitResult | None) -> None:
    async with Client(server(reading()), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        _ = refused(await raw(client, first, state))
        assert refused(await raw(client, PICK, state)) == ERROR


async def test_a_pick_does_not_work_twice() -> None:
    fake = source(doubled())
    async with Client(server(fake), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        done = await raw(client, PICK, state)
        assert not isinstance(done, InputRequiredResult)
        assert not done.is_error
        assert refused(await raw(client, PICK, state)) == ERROR

    assert len(fake.writes) == 1


async def test_a_used_confirmation_takes_its_picks_with_it() -> None:
    """A replayed state of a confirmation is no pick either: the user is asked from the start."""
    fake = source(doubled(), writes=False)
    async with Client(server(fake), elicitation_handler=NEVER) as client:

        async def step(
            reply: ElicitResult | None, state: str | None, key: str
        ) -> RawResult | InputRequiredResult:
            return await raw(client, reply, state, DELETE, key=key, tool="delete_taxonomy_item")

        which = state_of(await step(None, None, "choice"))
        confirm = state_of(await step(PICK, which, "choice"))
        for reply, key in ((None, "confirm"), (PICK, "choice")):
            again = await step(reply, confirm, key)
            assert isinstance(again, InputRequiredResult)
            assert "Which one is meant" in again.model_dump_json()
        _ = refused(await step(answer("decline"), confirm, "confirm"))
        replayed = await step(answer("accept", YES), confirm, "confirm")

    assert isinstance(replayed, InputRequiredResult)
    assert "Which one is meant" in replayed.model_dump_json()


async def test_an_expired_dialog_is_the_old_error() -> None:
    now = [1000.0]
    dialogs = Dialogs(clock=lambda: now[0])
    async with Client(server(reading(), dialogs), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        now[0] += dialogs.timeout
        result = refused(await raw(client, PICK, state))

    assert result == ERROR


async def test_a_dialog_counts_until_it_expires() -> None:
    now = [1000.0]
    dialogs = Dialogs(clock=lambda: now[0])
    async with Client(server(source(doubled()), dialogs), elicitation_handler=NEVER) as client:
        state = state_of(await raw(client))
        now[0] += dialogs.timeout - 1
        result = await raw(client, PICK, state)

    assert not isinstance(result, InputRequiredResult)
    assert not result.is_error


def test_a_state_of_a_dialog_holds_what_its_answer_needs() -> None:
    asked = Asked(
        nonce="n",
        fingerprint="f",
        expires=1.5,
        picks=(Pick("t", "q", "1"),),
        error="e",
        choices=("1", "2"),
    )

    assert Asked.parse(asked.model_dump_json()) == asked
    good = cast("Json", json.loads(asked.model_dump_json()))
    for wrong in (
        {"picks": [{"topic": "t", "question": "q", "value": 1}]},
        {"choices": ["1", 2]},
        {"error": 1},
        {"picks": None},
    ):
        assert Asked.parse(json.dumps({**good, **wrong})) is None


async def test_a_state_stays_small() -> None:
    """Twenty matches with the longest names, after four picks."""
    state, arguments = many_names(4)
    for number in range(MAX_CHOICES):
        state.correspondents.append(FakeTaxonomy(id=1000 + number, name="N" * 128, owner=number))
    arguments["correspondent"] = "N" * 128
    sizes: list[int] = []
    async with Client(server(source(state, writes=False)), elicitation_handler=NEVER) as client:
        result = await raw(client, None, None, arguments)
        while len(sizes) < MAX_PICKS:
            token = state_of(result)
            sizes.append(len(token))
            reply = answer("accept", {"choice": offered(result)[0]})
            if len(sizes) < MAX_PICKS:
                result = await raw(client, reply, token, arguments)

    assert max(sizes) < 8 * 1024


# --- waiting (handshake) -------------------------------------------------------------------------


async def test_a_dialog_nobody_answers_is_the_old_error() -> None:
    with anyio.fail_after(10):
        result = await call(
            server(reading(), Dialogs(timeout=0.1)),
            "legacy",
            answering("accept", {"choice": "51"}, before=anyio.sleep_forever),
        )

    assert text(result) == ERROR


async def test_progress_says_that_the_user_is_to_choose() -> None:
    reports: list[str | None] = []
    once = anyio.Event()

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        assert total
        assert progress < total
        reports.append(message)
        once.set()

    async def wait() -> None:
        with anyio.fail_after(5):
            await once.wait()

    async with Client(
        server(source(doubled()), Dialogs(progress_every=0.02)),
        mode="legacy",
        elicitation_handler=answering("accept", {"choice": "51"}, before=wait),
    ) as client:
        result = await client.call_tool("update_document", CALL, progress_handler=on_progress)

    assert not result.is_error
    assert set(reports) == {"Waiting for the user to choose."}


# --- the log -------------------------------------------------------------------------------------


@pytest.fixture
def every_record(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog, also for FastMCP's own logger (it does not propagate to the root logger)."""
    fastmcp = logging.getLogger("fastmcp")
    fastmcp.addHandler(caplog.handler)
    yield caplog
    fastmcp.removeHandler(caplog.handler)


@pytest.mark.parametrize(
    ("mode", "pick", "expected"),
    [
        ("auto", SECOND, [("asked", None), ("ok", None)]),
        ("legacy", SECOND, [("ok", None)]),
        ("auto", None, [("asked", None), ("error", "Ambiguous")]),
        ("legacy", None, [("error", "Ambiguous")]),
    ],
)
async def test_a_pick_is_logged_by_outcome_without_dialog_or_answer(
    every_record: pytest.LogCaptureFixture,
    mode: Mode,
    pick: int | None,
    expected: list[tuple[object, object]],
) -> None:
    every_record.set_level(logging.INFO)
    state = doubled()
    for found in state.correspondents[-2:]:
        found.name = found.name.replace("win", "zebra")

    shown = picks(pick)
    _ = await call(
        server(source(state)),
        mode,
        shown.handler,
        arguments={"document_id": 10, "correspondent": "TZEBRA"},
    )

    assert "TZEBRA" in shown.questions[0]
    records = every_record.records
    assert [
        (vars(r)["outcome"], vars(r).get("error")) for r in records if r.getMessage() == "tool call"
    ] == expected
    logged = every_record.text + str([vars(r) for r in records])
    for hidden in ("zebra", "ZEBRA", "Which one", "'choice'"):
        assert hidden not in logged


# --- hostile names -------------------------------------------------------------------------------


@both_eras
async def test_other_peoples_text_stands_in_the_dialog_only_quoted_and_cut(mode: Mode) -> None:
    hostile = f"x' (id 1){chr(0x202E)}\n" + "Confirming would " * 20 + "!"
    state = doubled()
    state.users[1] = copy.replace(state.users[1], username=hostile)
    for found in state.correspondents[-2:]:
        found.name, found.owner = hostile, 3
    shown = picks(None)

    _ = await call(
        server(source(state, writes=False)),
        mode,
        shown.handler,
        arguments={"document_id": 10, "correspondent": hostile},
    )

    [message], [schema] = shown.questions, shown.schemas
    for line in (message, json.dumps(schema, ensure_ascii=False)):
        assert chr(0x202E) not in line
        assert "\\n" not in line
        assert "Confirming would " * 4 not in line
    options = cast("dict[str, Json]", schema["properties"])["choice"]["oneOf"]
    assert all(len(o["title"]) <= 200 for o in cast("list[dict[str, str]]", options))


@both_eras
async def test_owners_that_cannot_be_read_are_shown_by_id(mode: Mode) -> None:
    class Hidden(FakePaperless):
        broken: bool = False

        @override
        async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
            if kind == "user":
                message = "no"
                raise (UpstreamUnavailable if self.broken else PermissionDenied)(message)
            return await super().list_taxonomy(kind)

    for broken in (False, True):
        state, shown = doubled(), picks(None)
        state.correspondents[-1].owner = 3
        fake = Hidden(state, user="admin")
        fake.broken = broken
        result = await call(server(fake), mode, shown.handler)

        assert text(result) == f"{ERROR} {NOT_PICKED}"
        assert "'twin' (id 51, 1 document), owner #3" in json.dumps(shown.schemas)


# --- every tool ----------------------------------------------------------------------------------


def _schema_names(node: Json, defs: Json, place: str, seen: tuple[str, ...] = ()) -> Iterator[str]:
    """The places of a schema that take an integer or a string: a name or an id."""
    ref = str(node.get("$ref", "")).split("/")[-1]
    if ref and ref not in seen:
        yield from _schema_names(cast("Json", defs[ref]), defs, place, (*seen, ref))
    parts = [
        cast("Json", part)
        for key in ("anyOf", "oneOf", "allOf", "items")
        for part in _listed(node.get(key))
    ]
    if {"integer", "string"} <= {part.get("type") for part in parts}:
        yield place
    for part in parts:
        yield from _schema_names(part, defs, place, seen)
    for name, part in cast("Json", node.get("properties", {})).items():
        yield from _schema_names(cast("Json", part), defs, f"{place}.{name}", seen)


def _listed(value: object) -> list[object]:
    if isinstance(value, list):
        return cast("list[object]", value)
    return [value] if isinstance(value, dict) else []


async def places(*, read_only: bool) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for tool in await server(source(world())).list_tools():
        if ANNOTATIONS[tool.name].read_only_hint is read_only:
            defs = cast("Json", tool.parameters.get("$defs", {}))
            found |= {(tool.name, place) for place in _schema_names(tool.parameters, defs, "")}
    return {(tool, place) for tool, place in found if place not in NO_NAMES}


# Their items are collected into one error (PLMCP-100.2).
LATER = {"update_documents"}


async def test_every_place_of_a_write_tool_that_takes_a_name_has_a_case() -> None:
    covered = {(tool, place) for tool, place, _ in CASES}
    wanted = {(tool, place) for tool, place in await places(read_only=False) if tool not in LATER}

    assert wanted - covered == set()
    # Beside them only keys of objects, which a schema does not show.
    assert {place.split(".")[-1] for _, place in covered - wanted} == {
        "custom_fields",
        "values",
        "assign_custom_fields_values",
    }


async def test_every_place_of_a_read_tool_that_takes_a_name_has_a_case() -> None:
    assert {(tool, place) for tool, place, _ in READS} == await places(read_only=True)


def test_the_search_for_names_finds_them() -> None:
    named: Json = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    schema: Json = {
        "properties": {
            "a": named,
            "b": {"anyOf": [{"items": named, "type": "array"}, {"type": "null"}]},
            "c": {"$ref": "#/$defs/C"},
            "d": {"type": "string"},
        }
    }
    defs: Json = {"C": {"properties": {"e": named, "again": {"$ref": "#/$defs/C"}}}}

    assert set(_schema_names(schema, defs, "")) == {".a", ".b", ".c.e"}


def _titles(cases: tuple[tuple[str, str, Json], ...]) -> list[str]:
    return [f"{tool}{place}-{n}" for n, (tool, place, _) in enumerate(cases)]


@pytest.fixture(scope="module")
def unwritten() -> FastMCP:
    """One server for every call that writes nothing."""
    return server(reading())


@pytest.mark.parametrize(("tool", "place", "arguments"), CASES, ids=_titles(CASES))
async def test_a_write_tool_asks_and_writes_what_the_call_with_the_id_writes(
    unwritten: FastMCP, tool: str, place: str, arguments: Json
) -> None:
    """On both eras: what is written is compared with the call that names the id."""
    del place
    oracle, by_number = source(doubled()), picks(SECOND)
    expected = await call(server(oracle), "auto", by_number.handler, tool, by_id(arguments))
    assert by_number.questions == []

    for mode in MODES:
        fake, shown = source(doubled()), picks(SECOND)
        result = await call(server(fake), mode, shown.handler, tool, arguments)

        assert len(shown.questions) == 1, text(result)
        assert shown.confirmations == by_number.confirmations
        if tool == "restore_from_trash":  # a filter restores nothing: it names what it matches
            assert text(result) == text(expected)
        else:
            assert not result.is_error, text(result)
            assert fake.writes
        assert fake.writes == oracle.writes

        plain = await call(unwritten, mode, None, tool, arguments)
        declined = await call(unwritten, mode, picks(None).handler, tool, arguments)

        assert text(plain).startswith("2 ")
        assert text(declined) == f"{text(plain)} {NOT_PICKED}"


@both_eras
@pytest.mark.parametrize(("tool", "place", "arguments"), READS, ids=_titles(READS))
async def test_a_read_tool_never_asks(
    unwritten: FastMCP, mode: Mode, tool: str, place: str, arguments: Json
) -> None:
    del place
    shown = picks(FIRST)

    result = await call(unwritten, mode, shown.handler, tool, arguments)

    assert shown.questions == []
    assert result.is_error
    assert "match 'TWIN'" in text(result)
