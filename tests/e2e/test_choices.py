"""The pick dialog through the installed console scripts against the E2E Paperless (decision-109).

Both entrypoints and protocol eras. Paperless holds one name once per owner and once without one,
so an administrator sees doubles that only their owners tell apart.
"""

import contextlib
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from choices import NOT_PICKED, picks
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

from e2e.test_confirmation import every_connection
from e2e.test_http import MCP_TOKEN, running
from e2e.test_stdio import console_script

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from confirmation import Handler, Json
    from fastmcp.client.client import CallToolResult
    from fastmcp.client.transports import ClientTransport

    from e2e.helpers import Stack
    from e2e.test_confirmation import Entry, Mode

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-109")]


@pytest.fixture(scope="module")
def url(stack: Stack) -> Generator[str]:
    with running(stack) as endpoint:
        yield endpoint


def transport(stack: Stack, url: str, entry: Entry) -> ClientTransport:
    if entry == "http":
        return StreamableHttpTransport(url, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
    env = {"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token}
    return StdioTransport(console_script(), [], env=env, keep_alive=False)


@contextlib.asynccontextmanager
async def twins(stack: Stack) -> AsyncGenerator[tuple[str, int, int]]:
    """The name and ids of two correspondents: without an owner, and the reader's."""
    name = f"e2e-twin-{uuid.uuid4().hex[:8]}"
    users = stack.admin.get("/api/users/", params={"username__iexact": "e2e-reader"})
    reader = cast("dict[str, list[dict[str, int]]]", users.json())["results"][0]["id"]
    numbers: list[int] = []
    try:
        for owner in (None, reader):
            made = stack.admin.post("/api/correspondents/", json={"name": name, "owner": owner})
            _ = made.raise_for_status()
            numbers.append(cast("dict[str, int]", made.json())["id"])
        yield name, numbers[0], numbers[1]
    finally:
        for number in numbers:
            _ = stack.admin.delete(f"/api/correspondents/{number}/")


def name_of(stack: Stack, number: int) -> str | None:
    answer = stack.admin.get(f"/api/correspondents/{number}/")
    return cast("dict[str, str]", answer.json())["name"] if answer.status_code == 200 else None


async def call(
    via: ClientTransport, mode: Mode, handler: Handler | None, tool: str, arguments: Json
) -> CallToolResult:
    async with Client(via, mode=mode, elicitation_handler=handler) as client:
        return await client.call_tool(
            tool, {"kind": "correspondent", **arguments}, raise_on_error=False
        )


@every_connection
async def test_the_picked_one_of_two_is_renamed(
    stack: Stack, url: str, entry: Entry, mode: Mode
) -> None:
    async with twins(stack) as (name, unowned, readers):
        shown = picks(readers)
        result = await call(
            transport(stack, url, entry),
            mode,
            shown.handler,
            "update_taxonomy_item",
            {"item": name, "name": f"{name}-new"},
        )

        assert not result.is_error, result.content
        [message], [schema] = shown.questions, shown.schemas
        assert f"2 correspondents match '{name}'" in message
        assert f"'{name}' (id {unowned}), 0 documents" in str(schema)
        assert f"'{name}' (id {readers}), 0 documents, owner 'e2e-reader'" in str(schema)
        assert name_of(stack, readers) == f"{name}-new"
        assert name_of(stack, unowned) == name


@every_connection
async def test_a_deletion_asks_for_the_pick_and_for_the_confirmation(
    stack: Stack, url: str, entry: Entry, mode: Mode
) -> None:
    async with twins(stack) as (name, unowned, readers):
        for confirm in (False, True):
            shown = picks(readers, confirm=confirm)
            result = await call(
                transport(stack, url, entry),
                mode,
                shown.handler,
                "delete_taxonomy_item",
                {"item": name},
            )

            assert result.is_error is not confirm, result.content
            assert len(shown.questions) == len(shown.confirmations) == 1
            assert name_of(stack, unowned) == name
            assert name_of(stack, readers) == (None if confirm else name)


@every_connection
async def test_without_a_pick_nothing_changes(
    stack: Stack, url: str, entry: Entry, mode: Mode
) -> None:
    async with twins(stack) as (name, unowned, readers):
        for shown in (None, picks(None)):
            result = await call(
                transport(stack, url, entry),
                mode,
                shown.handler if shown else None,
                "update_taxonomy_item",
                {"item": name, "name": f"{name}-new"},
            )

            assert result.is_error
            text = str(result.content)
            assert f"2 correspondents match '{name}'" in text
            assert "Pass the id of the one you mean." in text
            assert (NOT_PICKED in text) is (shown is not None)
            assert name_of(stack, unowned) == name_of(stack, readers) == name
