"""The assembled server on a contract backend, for what is no port method of its own."""

from dataclasses import fields
from typing import TYPE_CHECKING, Literal

from fastmcp import Client
from harness import paperless_settings, server_settings

from mcp_server_paperless.server import Ports as ServerPorts
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Mapping

    from confirmation import Handler
    from fastmcp.client.client import CallToolResult

    from contract.conftest import Ports

type Json = dict[str, object]
type Mode = Literal["auto", "legacy"]


async def call(
    ports: Ports,
    tool: str,
    given: Mapping[str, object] | None = None,
    /,
    *,
    handler: Handler | None = None,
    mode: Mode = "auto",
    **arguments: object,
) -> CallToolResult:
    """Call a tool as the user of ``ports``; ``handler`` answers its dialogs."""
    same = {field.name: getattr(ports, field.name) for field in fields(ServerPorts)}
    server = create_server(
        paperless_settings(),
        server_settings(10.0, toolsets=["all"]),
        ports=lambda _client: ServerPorts(**same),  # pyright: ignore[reportAny] -- by name
    )
    async with Client(server, mode=mode, elicitation_handler=handler) as client:
        return await client.call_tool(tool, {**(given or {}), **arguments}, raise_on_error=False)


def text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


async def output(
    ports: Ports,
    tool: str,
    given: Mapping[str, object] | None = None,
    /,
    *,
    handler: Handler | None = None,
    mode: Mode = "auto",
    **arguments: object,
) -> Json:
    """What a tool answers."""
    result = await call(ports, tool, given, handler=handler, mode=mode, **arguments)
    assert not result.is_error, text(result)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(
    ports: Ports,
    tool: str,
    given: Mapping[str, object] | None = None,
    /,
    *,
    handler: Handler | None = None,
    mode: Mode = "auto",
    **arguments: object,
) -> str:
    """How a tool refuses."""
    result = await call(ports, tool, given, handler=handler, mode=mode, **arguments)
    assert result.is_error, result.structured_content
    return text(result)
