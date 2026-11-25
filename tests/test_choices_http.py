"""The pick dialog on the wire: a real HTTP server, JSON-RPC written by hand (decision-109).

Declining cases use a fake that refuses writes.
"""

import asyncio
import functools
import json
from typing import TYPE_CHECKING, Final, cast

import httpx2
import pytest
from choices import FIRST, NOT_PICKED, SECOND, TWIN, doubled
from confirmation import YES, source
from test_confirmation_http import (
    BERT,
    FORMS,
    Handshake,
    app,
    messages,
    question,
    running,
    said,
    sessionless,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from confirmation import Json
    from fakes.paperless import FakePaperless

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-109")]

TOOL: Final = "update_document"
CALL: Final[Json] = {"document_id": 10, "correspondent": TWIN}
ERROR: Final = (
    "2 correspondents match 'TWIN': 'Twin' (id 50), 'twin' (id 51). "
    "Hint: Pass the id of the one you mean."
)
PICK: Final[Json] = {"action": "accept", "content": {"choice": "51"}}
OPTIONS: Final = {"50": "'Twin' (id 50, 1 document)", "51": "'twin' (id 51, 1 document)"}

rounds = functools.partial(sessionless, tool=TOOL, arguments=CALL, key="choice")


@pytest.fixture
async def http() -> AsyncGenerator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(timeout=30) as client:
        yield client


def reading() -> FakePaperless:
    return source(doubled(), writes=False)


def correspondent(fake: FakePaperless) -> int | None:
    return next(d for d in fake.state.documents if d.id == 10).correspondent


async def handshake(
    http: httpx2.AsyncClient, url: str, reply: Json | None, capabilities: Json = FORMS
) -> tuple[bool, str]:
    session = await Handshake.open(http, url, capabilities)
    return await session.finish(reply, arguments=CALL, tool=TOOL)


# --- which clients are asked, and with which form -------------------------------------------------

CAPABILITIES = pytest.mark.parametrize(
    ("capabilities", "asked"),
    [
        ({}, False),
        ({"elicitation": {}}, True),
        ({"elicitation": {"form": {}}}, True),
        ({"elicitation": {"form": {}, "url": {}}}, True),
        ({"elicitation": {"url": {}}}, False),
    ],
)
DECLINE: Final[Json] = {"result": {"action": "decline"}}


@CAPABILITIES
async def test_only_a_client_that_declares_forms_is_asked(
    http: httpx2.AsyncClient, capabilities: Json, *, asked: bool
) -> None:
    with running(app(reading())) as url:
        shown, text = await handshake(http, url, DECLINE, capabilities)
        message = await rounds(http, url, capabilities=capabilities)

    assert shown is asked
    assert text == (f"{ERROR} {NOT_PICKED}" if asked else ERROR)
    assert (cast("Json", message["result"])["resultType"] == "input_required") is asked
    if not asked:
        assert said(message) == ERROR


@pytest.mark.parametrize(
    ("version", "titled"),
    [
        ("2025-11-25", True),
        ("2025-06-18", False),
        ("2025-03-26", False),
        ("2024-11-05", False),
    ],
)
async def test_an_older_client_gets_the_older_form_and_its_pick_counts(
    http: httpx2.AsyncClient, version: str, *, titled: bool
) -> None:
    fake, schemas = source(doubled()), list[object]()
    with running(app(fake)) as url:
        session = await Handshake.open(http, url, {"elicitation": {}}, version=version)
        async with session.call(CALL, tool=TOOL) as response:
            async for message in messages(response):
                if message.get("method") == "elicitation/create":
                    schemas.append(cast("Json", message["params"])["requestedSchema"])
                    _ = await session.send({"id": message["id"], "result": PICK})
                elif "method" not in message:
                    assert "error" not in message
                    break

    options = (
        {"oneOf": [{"const": value, "title": title} for value, title in OPTIONS.items()]}
        if titled
        else {"enum": list(OPTIONS), "enumNames": list(OPTIONS.values())}
    )
    choice = {"type": "string", "title": "Which one?", **options}
    assert schemas == [{"type": "object", "properties": {"choice": choice}, "required": ["choice"]}]
    assert correspondent(fake) == SECOND
    assert len(fake.writes) == 1


async def test_a_2026_07_28_client_gets_titled_options(http: httpx2.AsyncClient) -> None:
    with running(app(reading())) as url:
        asked = question(await rounds(http, url))

    requests = cast("dict[str, Json]", asked["inputRequests"])
    assert requests.keys() == {"choice"}
    shown = json.dumps(requests["choice"])
    assert all(json.dumps({"const": v, "title": t}) in shown for v, t in OPTIONS.items())


# --- answers --------------------------------------------------------------------------------------

REPLIES: Final[list[Json]] = [
    {"action": "accept", "content": {"choice": "52"}},
    {"action": "accept", "content": {"choice": 51}},
    {"action": "accept", "content": {"choice": None}},
    {"action": "accept", "content": {"choice": ["51"]}},
    {"action": "accept", "content": {"choice": "51", "x": "y"}},
    {"action": "accept", "content": YES},
    {"action": "accept", "content": {}},
    {"action": "accept", "content": ["51"]},
    {"action": "accept"},
    {"action": "yes", "content": {"choice": "51"}},
    {"content": {"choice": "51"}},
    {},
]


@pytest.mark.parametrize("reply", REPLIES)
async def test_a_malformed_answer_is_the_old_error(http: httpx2.AsyncClient, reply: Json) -> None:
    with running(app(reading())) as url:
        shown, text = await handshake(http, url, {"result": reply})
        state = question(await rounds(http, url))["requestState"]
        message = await rounds(http, url, state=state, answer=reply)

    assert shown
    assert text == ERROR
    assert said(message) == ERROR or "error" in message


@pytest.mark.parametrize("code", [-32601, -32603])
async def test_a_client_that_fails_to_ask_gets_the_old_error(
    http: httpx2.AsyncClient, code: int
) -> None:
    for required in (False, True):
        with running(app(reading(), require_dialog=required)) as url:
            _, text = await handshake(http, url, {"error": {"code": code, "message": "zebra"}})

        assert text == ERROR


@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_no_pick_says_so(http: httpx2.AsyncClient, action: str) -> None:
    reply: Json = {"action": action, "content": {"choice": "51"}}
    with running(app(reading())) as url:
        _, text = await handshake(http, url, {"result": reply})
        state = question(await rounds(http, url))["requestState"]
        message = await rounds(http, url, state=state, answer=reply)

    assert text == said(message) == f"{ERROR} {NOT_PICKED}"


# --- connections and callers ----------------------------------------------------------------------


async def test_every_round_may_come_over_a_connection_of_its_own() -> None:
    """Pick, confirmation and the run over three connections."""
    fake = source(doubled())
    delete = functools.partial(
        sessionless, tool="delete_taxonomy_item", arguments={"kind": "tag", "item": TWIN}
    )
    with running(app(fake)) as url:

        async def once(**more: object) -> Json:
            async with httpx2.AsyncClient(timeout=30) as alone:
                return await delete(alone, url, **more)  # pyright: ignore[reportArgumentType] -- passthrough

        which = question(await once())["requestState"]
        confirm = question(await once(state=which, answer=PICK, key="choice"))
        assert "confirm" in cast("Json", confirm["inputRequests"])
        assert fake.writes == []
        accept: Json = {"action": "accept", "content": YES}
        done = await once(state=confirm["requestState"], answer=accept)

    assert not cast("Json", done["result"]).get("isError")
    assert [t.id for t in fake.state.tags if t.id in {FIRST, SECOND}] == [FIRST]
    assert len(fake.writes) == 1


async def test_a_state_is_worthless_to_another_caller(http: httpx2.AsyncClient) -> None:
    fake = source(doubled())
    with running(app(fake)) as url:
        token = question(await rounds(http, url))["requestState"]
        bert = await rounds(http, url, token=BERT, state=token, answer=PICK)
        assert fake.writes == []
        anna = await rounds(http, url, state=token, answer=PICK)

    assert "Invalid or expired requestState" in said(bert)
    assert "error" not in anna
    assert correspondent(fake) == SECOND


async def test_an_answer_without_declared_forms_is_not_read(http: httpx2.AsyncClient) -> None:
    with running(app(reading())) as url:
        state = question(await rounds(http, url))["requestState"]
        message = await rounds(http, url, capabilities={}, state=state, answer=PICK)

    assert said(message) == ERROR


async def test_answers_in_a_handshake_call_are_not_read(http: httpx2.AsyncClient) -> None:
    with running(app(reading())) as url:
        session = await Handshake.open(http, url)
        shown, text = await session.finish(
            DECLINE, arguments=CALL, tool=TOOL, inputResponses={"choice": PICK}
        )

    assert shown
    assert text == f"{ERROR} {NOT_PICKED}"


async def test_a_pick_after_the_connection_dropped_changes_nothing(
    http: httpx2.AsyncClient,
) -> None:
    fake = source(doubled())
    with running(app(fake)) as url:
        session = await Handshake.open(http, url)
        number: object = None
        async with session.call(CALL, tool=TOOL) as response:
            async for message in messages(response):
                if message.get("method") == "elicitation/create":
                    number = message["id"]
                    break
        # The call still waits: dropping its stream does not cancel it.
        await asyncio.sleep(0.3)
        _ = await session.send({"id": number, "result": PICK})
        await asyncio.sleep(0.5)
        assert fake.writes == []
        # The session goes on, and the next dialog works.
        shown, _ = await session.finish({"result": PICK}, arguments=CALL, tool=TOOL)
        assert shown

    assert correspondent(fake) == SECOND
