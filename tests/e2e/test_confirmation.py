"""The confirmation dialog through the installed console scripts against the E2E Paperless.

Both entrypoints, both protocol eras, every answer, and a dialog that stays open longer than the
usual request timeouts of 30 and 60 seconds (decision-44).
"""

import contextlib
import uuid
from typing import TYPE_CHECKING, Literal, cast

import anyio
import pytest
from confirmation import NOT_AGAIN, YES, answering
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

from e2e.test_http import MCP_TOKEN, running
from e2e.test_stdio import console_script
from mcp_server_paperless.domain.errors import CONFIRM_HINT

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from confirmation import Handler
    from fastmcp.client.client import CallToolResult
    from fastmcp.client.transports import ClientTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-44")]

type Entry = Literal["stdio", "http"]
type Mode = Literal["auto", "legacy"]
CASES: tuple[tuple[Entry, Mode], ...] = (
    ("stdio", "auto"),
    ("stdio", "legacy"),
    ("http", "auto"),
    ("http", "legacy"),
)
every_connection = pytest.mark.parametrize(("entry", "mode"), CASES)
LONG = 65.0
REQUIRE_DIALOG = {"PAPERLESS_MCP_REQUIRE_DIALOG": "true"}


@pytest.fixture(scope="module")
def url(stack: Stack) -> Generator[str]:
    with running(stack) as endpoint:
        yield endpoint


@pytest.fixture(scope="module")
def strict_url(stack: Stack) -> Generator[str]:
    """Like `url`, of a server that requires dialogs."""
    with running(stack, **REQUIRE_DIALOG) as endpoint:
        yield endpoint


def transport(
    stack: Stack, url: str, entry: Entry, settings: dict[str, str] | None = None
) -> ClientTransport:
    if entry == "http":
        return StreamableHttpTransport(url, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
    env = {"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token}
    env |= settings or {}
    return StdioTransport(console_script(), [], env=env, keep_alive=False)


@contextlib.asynccontextmanager
async def tag(stack: Stack) -> AsyncGenerator[tuple[int, str]]:
    """The id and name of a tag of this test's own."""
    name = f"e2e-confirm-{uuid.uuid4().hex[:8]}"
    response = stack.admin.post("/api/tags/", json={"name": name})
    _ = response.raise_for_status()
    number = cast("dict[str, int]", response.json())["id"]
    try:
        yield number, name
    finally:
        _ = stack.admin.delete(f"/api/tags/{number}/")


def exists(stack: Stack, number: int) -> bool:
    return stack.admin.get(f"/api/tags/{number}/").status_code == 200


async def delete(
    via: ClientTransport, mode: Mode, handler: Handler | None, number: int, **more: object
) -> CallToolResult:
    async with Client(via, mode=mode, elicitation_handler=handler) as client:
        return await client.call_tool(
            "delete_taxonomy_item", {"kind": "tag", "item": number, **more}, raise_on_error=False
        )


@every_connection
async def test_an_accepted_dialog_deletes(stack: Stack, url: str, entry: Entry, mode: Mode) -> None:
    seen: list[str] = []
    async with tag(stack) as (number, name):
        result = await delete(
            transport(stack, url, entry), mode, answering("accept", YES, seen=seen), number
        )

        assert not result.is_error, result.content
        assert len(seen) == 1
        assert f"'{name}'" in seen[0]
        assert not exists(stack, number)


@every_connection
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_a_refused_dialog_deletes_nothing(
    stack: Stack, url: str, entry: Entry, mode: Mode, action: str
) -> None:
    async with tag(stack) as (number, _):
        result = await delete(transport(stack, url, entry), mode, answering(action), number)

        assert result.is_error
        assert NOT_AGAIN in str(result.content)
        assert exists(stack, number)


@every_connection
async def test_a_client_without_dialogs_gets_the_refusal(
    stack: Stack, url: str, entry: Entry, mode: Mode
) -> None:
    async with tag(stack) as (number, _):
        result = await delete(transport(stack, url, entry), mode, None, number)

        assert result.is_error
        assert CONFIRM_HINT in str(result.content)
        assert exists(stack, number)


@every_connection
async def test_with_dialogs_required_only_an_accepted_one_deletes(
    stack: Stack, strict_url: str, entry: Entry, mode: Mode
) -> None:
    def via() -> ClientTransport:
        return transport(stack, strict_url, entry, REQUIRE_DIALOG)

    async with tag(stack) as (number, _):
        for handler in (None, answering("decline")):
            refused = await delete(via(), mode, handler, number, confirm=True)

            assert refused.is_error
            assert "PAPERLESS_MCP_REQUIRE_DIALOG=true" in str(refused.content)
            assert exists(stack, number)

        seen: list[str] = []
        done = await delete(via(), mode, answering("accept", YES, seen=seen), number, confirm=True)

        assert not done.is_error, done.content
        assert len(seen) == 1
        assert not exists(stack, number)


async def test_a_dialog_may_stay_open_longer_than_the_usual_timeouts(
    stack: Stack, url: str
) -> None:
    async def wait() -> None:
        await anyio.sleep(LONG)

    async def slow(entry: Entry, mode: Mode) -> None:
        async with tag(stack) as (number, _):
            handler = answering("accept", YES, before=wait)
            result = await delete(transport(stack, url, entry), mode, handler, number)

            assert not result.is_error, result.content
            assert not exists(stack, number)

    async with anyio.create_task_group() as group:
        for entry, mode in CASES:
            _ = group.start_soon(slow, entry, mode)
