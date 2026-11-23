"""Derive what the tools send to Paperless, for the coverage table (PLMCP-91).

1. The adapter tests run once more, only to keep an answer per operation (the schema guard saves
   it): the replay below needs something the adapters can read.
2. The scenarios of tests/tools/test_arguments.py run with their ports recorded: every call a tool
   makes to a port, with its arguments and the tool's name. (The other tool tests add no field
   or value the scenarios lack; they add only tools to the reads that look names up: tags, users,
   correspondents, document types, groups, documents and mail rules.)
3. Each recorded call is handed to the real adapter on a transport that answers with the saved
   answers; the adapter records the request it builds (before it reads the answer).

Both test runs happen inside this process, so the coverage test works on its own, also in CI.
"""

import asyncio
import contextlib
import json
import os
from typing import TYPE_CHECKING, cast

import httpx2
import observation
import pytest
from adapter.conftest import streaming
from adapter.schema_guard import operation_of

from mcp_server_paperless.server import paperless_ports

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

REPLAY_SECONDS = 0.1  # a call that waits for a task Paperless never finishes is given up


def _run(root: Path, folder: str) -> None:
    """Run the tests of `folder` in this process; the ambient options of the outer run stay out."""
    ambient = os.environ.pop("PYTEST_ADDOPTS", None)
    try:
        code = pytest.main(
            [str(root / folder), "-q", "-p", "no:cacheprovider", "--no-cov", "--rootdir", str(root)]
        )
    finally:
        if ambient is not None:
            os.environ["PYTEST_ADDOPTS"] = ambient
    assert code == 0, f"the tests of {folder} fail, so the coverage cannot be derived"


def _canned(request: httpx2.Request) -> httpx2.Response:
    """The answer the adapter tests saw for this operation, or an empty object.

    A page of results never points to a next one: the adapter would read pages for ever.
    """
    status, headers, content = observation.RESPONSES.get(
        operation_of(request) or ("", ""), (200, {"Content-Type": "application/json"}, b"{}")
    )
    if "json" in headers.get("content-type", headers.get("Content-Type", "")):
        body = cast("object", json.loads(content))
        if isinstance(body, dict) and "next" in body:
            content = json.dumps({**cast("dict[str, object]", body), "next": None}).encode()
    return httpx2.Response(status, content=content, headers=headers)


async def _replay(calls: list[observation.PortCall]) -> list[str]:
    """Hand each distinct call to the adapter; the calls that sent no request are named."""
    client, _ = streaming(_canned)
    ports = paperless_ports(client)
    done: set[str] = set()
    silent: list[str] = []
    for call in calls:
        key = repr((call.tool, call.field, call.method, call.args, call.kwargs))
        if call.tool is None or call.field == "raw" or key in done:
            continue
        done.add(key)
        token = observation.CURRENT_TOOL.set(call.tool)
        before = len(observation.OBSERVED)
        try:
            with contextlib.suppress(
                Exception
            ):  # the request is recorded before the answer is read
                port = cast("object", getattr(ports, call.field))
                method = cast("Callable[..., object]", getattr(port, call.method))
                async with asyncio.timeout(REPLAY_SECONDS):
                    answer = method(*call.args, **call.kwargs)
                    if isinstance(answer, contextlib.AbstractAsyncContextManager):
                        async with answer:
                            pass  # an opened file: its request is enough
                    else:
                        _ = await cast("Awaitable[object]", answer)
        finally:
            observation.CURRENT_TOOL.reset(token)
        if len(observation.OBSERVED) == before:
            silent.append(f"{call.tool}: {call.field}.{call.method}")
    return silent


def derive(root: Path) -> list[observation.Observation]:
    """The requests the tools cause, one observation each, named by tool."""
    observation.RESPONSES.clear()
    _run(root, "tests/adapter")
    observation.PORT_CALLS.clear()
    observation.GIVEN.clear()
    observation.TOOL_ARGUMENTS.clear()
    observation.RECORDING = True
    try:
        _run(root, "tests/tools/test_arguments.py")
    finally:
        observation.RECORDING = False
    calls = list(observation.PORT_CALLS)
    observation.OBSERVED.clear()
    silent = asyncio.run(_replay(calls))
    assert not silent, f"these port calls made the adapter send no request: {silent}"
    return list(observation.OBSERVED)
