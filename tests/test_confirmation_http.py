"""The confirmation dialog on the wire: a real HTTP server, JSON-RPC written by hand (decision-44).

What a client library would never send or check by itself: capabilities, malformed answers, other
callers' answers, dropped connections. Declining cases use a fake that refuses writes.
"""

import asyncio
import contextlib
import json
import threading
import time
from typing import TYPE_CHECKING, Final, cast, final, override

import httpx2
import pytest
import uvicorn
from confirmation import NOT_AGAIN, NOTE, YES, source, world
from fakes.paperless import FakePaperless, FakeState
from harness import serve

from mcp_server_paperless.app import authenticator, build_app
from mcp_server_paperless.domain.errors import CONFIRM_HINT
from mcp_server_paperless.settings import HttpSettings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from confirmation import Json
    from starlette.applications import Starlette

    from mcp_server_paperless.domain.documents import DocumentRecord

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-44")]

ANNA, BERT = "a" * 40, "b" * 40
HANDSHAKE: Final = "2025-11-25"
SESSIONLESS: Final = "2026-07-28"
ACCEPT: Final[Json] = {"action": "accept", "content": YES}
FORMS: Final[Json] = {"elicitation": {"form": {}}}
META: Final = "io.modelcontextprotocol/"


def app(fake: FakePaperless, *, require_dialog: bool = False) -> Starlette:
    http = HttpSettings.model_validate({"auth_tokens": f"{ANNA},{BERT}"})
    server = serve(fake, auth=authenticator(http), require_dialog=require_dialog)
    return build_app(server, http, max_upload_bytes=1024 * 1024)


@contextlib.contextmanager
def running(served: Starlette) -> Generator[str]:
    """The MCP URL of a uvicorn server with `served` on a free loopback port."""
    config = uvicorn.Config(served, host="127.0.0.1", port=0, log_config=None, ws="none")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        port = int(server.servers[0].sockets[0].getsockname()[1])  # pyright: ignore[reportAny] -- (host, port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(30)


def note_gone(state: FakeState) -> bool:
    return not state.documents[0].notes


def said(message: Json) -> str:
    """The text of a tool result, or of a JSON-RPC error."""
    result = cast("dict[str, list[dict[str, str]]]", message.get("result", {}))
    return "".join(b["text"] for b in result.get("content", [])) or json.dumps(message)


async def messages(response: httpx2.Response) -> AsyncGenerator[Json]:
    """The JSON-RPC messages of a response, one JSON body or a stream of events."""
    if response.headers["content-type"].startswith("application/json"):
        yield cast("Json", json.loads(await response.aread()))
        return
    async for line in response.aiter_lines():
        if line.startswith("data:") and line[5:].strip():
            yield cast("Json", json.loads(line[5:]))


@final
class Handshake:
    """One session of a client up to 2025-11-25."""

    def __init__(self, http: httpx2.AsyncClient, url: str, session: str, token: str) -> None:
        self.http: httpx2.AsyncClient = http
        self.url: str = url
        self.session: str = session
        self.token: str = token
        self.number: int = 1

    @staticmethod
    def headers(token: str, session: str | None = None) -> dict[str, str]:
        given = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": HANDSHAKE,
        }
        return given | ({"Mcp-Session-Id": session} if session else {})

    @classmethod
    async def open(
        cls, http: httpx2.AsyncClient, url: str, capabilities: Json = FORMS, token: str = ANNA
    ) -> Handshake:
        params = {
            "protocolVersion": HANDSHAKE,
            "capabilities": capabilities,
            "clientInfo": {"name": "t", "version": "1"},
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}
        response = await http.post(url, headers=cls.headers(token), json=body)
        session = cls(http, url, response.headers["mcp-session-id"], token)
        _ = await session.send({"method": "notifications/initialized"})
        return session

    async def send(self, message: Json, token: str | None = None) -> httpx2.Response:
        headers = self.headers(token or self.token, self.session)
        return await self.http.post(self.url, headers=headers, json={"jsonrpc": "2.0", **message})

    def call(
        self, arguments: Json = NOTE, **more: object
    ) -> contextlib.AbstractAsyncContextManager[httpx2.Response]:
        """A tools/call whose response stream the caller reads."""
        self.number += 1
        params = {"name": "delete_note", "arguments": arguments, **more}
        body = {"jsonrpc": "2.0", "id": self.number, "method": "tools/call", "params": params}
        headers = self.headers(self.token, self.session)
        return self.http.stream("POST", self.url, headers=headers, json=body)

    async def finish(self, reply: Json | None, **more: object) -> tuple[bool, str]:
        """(whether a dialog was shown, the result's text); dialogs are answered with `reply`."""
        asked = False
        async with self.call(**more) as response:  # pyright: ignore[reportArgumentType] -- passthrough
            async for message in messages(response):
                if message.get("method") == "elicitation/create":
                    asked = True
                    assert reply is not None
                    _ = await self.send({"id": message["id"], **reply})
                elif "method" not in message:
                    return asked, said(message)
        raise AssertionError


@pytest.fixture
async def http() -> AsyncGenerator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(timeout=30) as client:
        yield client


async def sessionless(  # noqa: PLR0913 -- one keyword per part of the wire format
    http: httpx2.AsyncClient,
    url: str,
    *,
    capabilities: Json = FORMS,
    token: str = ANNA,
    state: object = None,
    answer: Json | None = None,
    arguments: Json = NOTE,
) -> Json:
    """The response message of one 2026-07-28 round."""
    params: Json = {
        "name": "delete_note",
        "arguments": arguments,
        "_meta": {
            f"{META}protocolVersion": SESSIONLESS,
            f"{META}clientInfo": {"name": "t", "version": "1"},
            f"{META}clientCapabilities": capabilities,
        },
    }
    if state is not None:
        params["requestState"] = state
    if answer is not None:
        params["inputResponses"] = {"confirm": answer}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": SESSIONLESS,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "delete_note",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
    async with http.stream("POST", url, headers=headers, json=body) as response:
        async for message in messages(response):
            if "method" not in message:
                return message
    raise AssertionError


def question(message: Json) -> Json:
    result = cast("Json", message["result"])
    assert result["resultType"] == "input_required", message
    return result


# --- which clients are asked ----------------------------------------------------------------------

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
async def test_only_a_handshake_client_that_declares_forms_is_asked(
    http: httpx2.AsyncClient, capabilities: Json, *, asked: bool
) -> None:
    with running(app(source(world(), writes=False))) as url:
        session = await Handshake.open(http, url, capabilities)
        shown, text = await session.finish(DECLINE)

    assert shown is asked
    assert text.endswith(NOT_AGAIN if asked else CONFIRM_HINT)


@CAPABILITIES
async def test_only_a_2026_07_28_client_that_declares_forms_is_asked(
    http: httpx2.AsyncClient, capabilities: Json, *, asked: bool
) -> None:
    with running(app(source(world(), writes=False))) as url:
        message = await sessionless(http, url, capabilities=capabilities)

    assert (cast("Json", message["result"])["resultType"] == "input_required") is asked
    if asked:
        assert "e2e-public-invoice" in json.dumps(question(message)["inputRequests"])
    else:
        assert said(message).endswith(CONFIRM_HINT)


async def test_an_answer_from_a_client_that_no_longer_declares_forms_is_not_read(
    http: httpx2.AsyncClient,
) -> None:
    with running(app(source(world(), writes=False))) as url:
        state = question(await sessionless(http, url))["requestState"]
        message = await sessionless(http, url, capabilities={}, state=state, answer=ACCEPT)

    assert said(message).endswith(CONFIRM_HINT)


# --- answers --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"error": {"code": -32601, "message": "no"}}, CONFIRM_HINT),
        ({"error": {"code": -32603, "message": "zebra"}}, "the dialog could not be shown."),
        ({"result": {"action": "accept", "content": {"confirm": "true"}}}, "not understood."),
        ({"result": {"action": "accept", "content": {"confirm": 1}}}, "not understood."),
        ({"result": {"action": "accept", "content": {"confirm": None}}}, "not understood."),
        ({"result": {"action": "accept", "content": {}}}, "not understood."),
        ({"result": {"action": "accept"}}, "not understood."),
        ({"result": {"action": "accept", "content": {**YES, "x": "y"}}}, "not understood."),
        ({"result": {"action": "accept", "content": [True]}}, "not understood."),
        ({"result": {"action": "yes", "content": YES}}, "not understood."),
        ({"result": {"content": YES}}, "not understood."),
        ({"result": {}}, "not understood."),
        ({"result": {"action": "accept", "content": {"confirm": False}}}, "the user declined."),
    ],
)
async def test_what_a_handshake_client_answers(
    http: httpx2.AsyncClient, reply: Json, expected: str
) -> None:
    with running(app(source(world(), writes=False))) as url:
        session = await Handshake.open(http, url)
        shown, text = await session.finish(reply)

    assert shown
    assert text.startswith("Nothing was changed")
    assert expected in text
    if expected == "the dialog could not be shown.":
        assert "zebra" not in text  # the client's own words are not passed on


@pytest.mark.parametrize(
    "answer",
    [
        {"action": "accept", "content": {"confirm": "true"}},
        {"action": "accept", "content": {"confirm": 1}},
        {"action": "accept"},
        {"action": "accept", "content": [True]},
        {"action": "yes", "content": YES},
        {"content": YES},
        {},
        {"roots": []},
    ],
)
async def test_a_malformed_2026_07_28_answer_changes_nothing(
    http: httpx2.AsyncClient, answer: Json
) -> None:
    with running(app(source(world(), writes=False))) as url:
        state = question(await sessionless(http, url))["requestState"]
        message = await sessionless(http, url, state=state, answer=answer)

    refused = "Nothing was changed: the answer to the dialog was not understood" in said(message)
    assert refused or "error" in message


# --- dialogs required (decision-53) ---------------------------------------------------------------

CONFIRMED: Final[Json] = {**NOTE, "confirm": True}
SETTING: Final = "PAPERLESS_MCP_REQUIRE_DIALOG=true"


NO_FORMS: Final = "the user cannot be asked on this connection, or the client does not declare "
NOT_SHOWN: Final = "the dialog could not be shown; "
NOT_UNDERSTOOD: Final = "the answer to the dialog was not understood; "
WAY_OUT: Final = "a client that shows dialogs can confirm, or the operator can switch that off."


def fails_closed(text: str, reason: str) -> bool:
    return (
        text.startswith(f"Nothing was changed: {reason}")
        and SETTING in text
        and (WAY_OUT in text) is (reason in {NO_FORMS, NOT_SHOWN})
        and text.endswith(NOT_AGAIN)
    )


@pytest.mark.decision("decision-53")
@CAPABILITIES
async def test_required_a_handshake_client_is_asked_or_refused(
    http: httpx2.AsyncClient, capabilities: Json, *, asked: bool
) -> None:
    with running(app(source(world(), writes=False), require_dialog=True)) as url:
        session = await Handshake.open(http, url, capabilities)
        shown, text = await session.finish(DECLINE, arguments=CONFIRMED)

    assert shown is asked
    assert fails_closed(text, "the user declined; " if asked else NO_FORMS)


@pytest.mark.decision("decision-53")
@CAPABILITIES
async def test_required_a_2026_07_28_client_is_asked_or_refused(
    http: httpx2.AsyncClient, capabilities: Json, *, asked: bool
) -> None:
    with running(app(source(world(), writes=False), require_dialog=True)) as url:
        message = await sessionless(http, url, capabilities=capabilities, arguments=CONFIRMED)

    assert (cast("Json", message["result"])["resultType"] == "input_required") is asked
    assert asked or fails_closed(said(message), NO_FORMS)


@pytest.mark.decision("decision-53")
@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ({"error": {"code": -32601, "message": "no"}}, NOT_SHOWN),
        ({"error": {"code": -32603, "message": "no"}}, NOT_SHOWN),
        ({"result": {"action": "accept"}}, NOT_UNDERSTOOD),
        ({"result": {"action": "accept", "content": {"confirm": "true"}}}, NOT_UNDERSTOOD),
    ],
)
async def test_required_a_dialog_that_fails_changes_nothing(
    http: httpx2.AsyncClient, reply: Json, reason: str
) -> None:
    with running(app(source(world(), writes=False), require_dialog=True)) as url:
        session = await Handshake.open(http, url)
        shown, text = await session.finish(reply, arguments=CONFIRMED)

    assert shown
    assert fails_closed(text, reason)


@pytest.mark.decision("decision-53")
async def test_required_an_accepted_dialog_still_works_on_both_eras(
    http: httpx2.AsyncClient,
) -> None:
    first, second = world(), world()
    with running(app(source(first), require_dialog=True)) as url:
        session = await Handshake.open(http, url)
        shown, _ = await session.finish({"result": ACCEPT}, arguments=CONFIRMED)
    with running(app(source(second), require_dialog=True)) as url:
        token = question(await sessionless(http, url, arguments=CONFIRMED))["requestState"]
        assert not note_gone(second)
        _ = await sessionless(http, url, state=token, answer=ACCEPT, arguments=CONFIRMED)

    assert shown
    assert note_gone(first)
    assert note_gone(second)


@pytest.mark.decision("decision-53")
async def test_required_an_answer_without_declared_forms_is_not_read(
    http: httpx2.AsyncClient,
) -> None:
    with running(app(source(world(), writes=False), require_dialog=True)) as url:
        token = question(await sessionless(http, url, arguments=CONFIRMED))["requestState"]
        message = await sessionless(
            http, url, capabilities={}, state=token, answer=ACCEPT, arguments=CONFIRMED
        )

    assert fails_closed(said(message), NO_FORMS)


@pytest.mark.decision("decision-53")
async def test_required_a_stateless_server_that_cannot_ask_refuses(
    http: httpx2.AsyncClient,
) -> None:
    """The app never runs like this; a server that does must not let confirm=true through."""
    server = serve(source(world(), writes=False), require_dialog=True)
    with running(server.http_app(stateless_http=True)) as url:
        params = {"name": "delete_note", "arguments": CONFIRMED}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
        headers = Handshake.headers(ANNA)
        async with http.stream("POST", url, headers=headers, json=body) as response:
            replies = [m async for m in messages(response)]
        message = await sessionless(http, url, arguments=CONFIRMED)

    assert fails_closed(said(replies[-1]), NO_FORMS)
    assert question(message)["requestState"]


# --- other callers --------------------------------------------------------------------------------


async def test_only_the_session_that_was_asked_answers_a_handshake_dialog(
    http: httpx2.AsyncClient,
) -> None:
    state = world()
    answered: list[int] = []
    with running(app(source(state))) as url:
        session = await Handshake.open(http, url)
        other = await Handshake.open(http, url)
        bert = await Handshake.open(http, url, token=BERT)
        async with session.call() as response:
            async for message in messages(response):
                if message.get("method") == "elicitation/create":
                    number, reply = cast("int", message["id"]), {"result": ACCEPT}
                    # Another caller in this session, other sessions, and an id nobody asked with.
                    answered.append((await session.send({"id": number, **reply}, BERT)).status_code)
                    _ = await other.send({"id": number, **reply})
                    _ = await bert.send({"id": number, **reply})
                    _ = await session.send({"id": number + 50, **reply})
                    await asyncio.sleep(0.3)
                    assert not note_gone(state)
                    _ = await session.send({"id": number, "result": {"action": "decline"}})
                elif "method" not in message:
                    assert "the user declined" in said(message)
                    break

    assert answered == [404]
    assert not note_gone(state)


async def test_answers_in_a_handshake_call_are_not_read(http: httpx2.AsyncClient) -> None:
    with running(app(source(world(), writes=False))) as url:
        session = await Handshake.open(http, url)
        shown, text = await session.finish(DECLINE, inputResponses={"confirm": ACCEPT})
        _, with_state = await session.finish(
            DECLINE, inputResponses={"confirm": ACCEPT}, requestState="v1.x"
        )

    assert shown
    assert "the user declined" in text
    assert "Invalid or expired requestState" in with_state


async def test_a_state_is_worthless_to_another_caller(http: httpx2.AsyncClient) -> None:
    state = world()
    with running(app(source(state))) as url:
        token = question(await sessionless(http, url))["requestState"]
        bert = await sessionless(http, url, token=BERT, state=token, answer=ACCEPT)
        assert not note_gone(state)
        anna = await sessionless(http, url, state=token, answer=ACCEPT)

    assert "Invalid or expired requestState" in said(bert)
    assert "error" not in anna
    assert note_gone(state)


# --- dropped connections ------------------------------------------------------------------------


async def test_an_acceptance_after_the_connection_dropped_changes_nothing_on_a_handshake(
    http: httpx2.AsyncClient,
) -> None:
    state = world()
    with running(app(source(state))) as url:
        session = await Handshake.open(http, url)
        number: object = None
        async with session.call() as response:
            async for message in messages(response):
                if message.get("method") == "elicitation/create":
                    number = message["id"]
                    break
        # The call still waits: dropping its stream does not cancel it.
        await asyncio.sleep(0.3)
        _ = await session.send({"id": number, "result": ACCEPT})
        await asyncio.sleep(0.5)
        assert not note_gone(state)
        # The session goes on, and the next dialog works.
        shown, _ = await session.finish({"result": ACCEPT})
        assert shown

    assert note_gone(state)


class Held(FakePaperless):
    """Reads of a document wait until `release`; `reading` says that one does."""

    def __init__(self, state: FakeState) -> None:
        super().__init__(state, user="admin", allow_writes=True)
        self.hold: threading.Event = threading.Event()
        self.reading: threading.Event = threading.Event()
        self.release: threading.Event = threading.Event()

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        if self.hold.is_set():
            self.reading.set()
            while not self.release.is_set():  # noqa: ASYNC110 -- the event is another thread's
                await asyncio.sleep(0.01)
        return await super().get_document(document_id)


@pytest.mark.parametrize("dropped", [True, False])
async def test_an_accepted_call_whose_connection_drops_changes_nothing_on_2026_07_28(
    http: httpx2.AsyncClient, *, dropped: bool
) -> None:
    state = world()
    fake = Held(state)
    with running(app(fake)) as url:
        token = question(await sessionless(http, url))["requestState"]
        fake.hold.set()
        round_two = asyncio.create_task(sessionless(http, url, state=token, answer=ACCEPT))
        while not fake.reading.is_set():  # noqa: ASYNC110 -- the event is another thread's
            await asyncio.sleep(0.01)
        if dropped:
            _ = round_two.cancel()
            await asyncio.sleep(0.3)
        fake.release.set()
        await asyncio.sleep(0.5)
        assert note_gone(state) is not dropped
        # Used up: the same answer over a new connection asks again.
        if dropped:
            again = await sessionless(http, url, state=token, answer=ACCEPT)
            assert question(again)["requestState"] != token
            assert not note_gone(state)


# --- sessions ------------------------------------------------------------------------------------


async def test_a_session_does_not_expire_while_a_dialog_is_open(http: httpx2.AsyncClient) -> None:
    state = world()
    server = serve(source(state))
    with running(server.http_app(session_idle_timeout=0.5)) as url:
        session = await Handshake.open(http, url)
        async with session.call() as response:
            async for message in messages(response):
                if message.get("method") == "elicitation/create":
                    await asyncio.sleep(2)
                    _ = await session.send({"id": message["id"], "result": ACCEPT})
                elif "method" not in message:
                    break
        assert note_gone(state)
        # It does expire once nothing is open.
        await asyncio.sleep(1.5)
        assert (await session.send({"id": 9, "method": "ping"})).status_code == 404
