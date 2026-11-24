"""What content and arguments reach stderr: the real entrypoints, in a process (PLMCP-84).

A stub Paperless and the calls carry canaries; none may come out of the process's stderr at INFO
or above, whichever logger (ours, FastMCP's, the SDK's, uvicorn's) or hook would print it.
"""

import contextlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, Final, override

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from harness import TOKEN
from mcp import MCPError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Generator
    from pathlib import Path

    from fastmcp.client.client import ConnectMode

pytestmark = pytest.mark.anyio

# What Paperless says, what the model says, and a value in the answer to the startup check.
ANSWER: Final = "canary-answer-of-paperless"
ARGUMENT: Final = "canary-argument"
STARTUP: Final = "canary-startup-answer"
HTTP_TOKEN: Final = "bearer-of-the-log-tests"
ENTRYPOINTS: Final = {"stdio": "main", "http": "main_http"}
MODES: Final[list[ConnectMode]] = ["legacy", "2026-07-28"]


def unavailable_but_for_documents(path: str) -> tuple[int, object]:
    """Not up at startup (the server starts anyway); every document request is refused with text."""
    if path.startswith("/api/documents/"):
        return 400, {"detail": ANSWER}
    return 503, {"detail": ANSWER}


@contextlib.contextmanager
def paperless(answer: Callable[[str], tuple[int, object]]) -> Generator[str]:
    """The URL of a Paperless that answers a GET of a path with a status and a JSON body."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, body = answer(self.path)
            content = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            _ = self.wfile.write(content)

        @override
        def log_message(self, format: str, *args: object) -> None:
            """Nothing on this process's stderr."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def environment(url: str, **given: str) -> dict[str, str]:
    """The server's environment; nothing of the developer's settings."""
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(("PAPERLESS", "FASTMCP"))}
    fixed = {
        "PAPERLESS_URL": url,
        "PAPERLESS_TOKEN": TOKEN,
        "PAPERLESS_MCP_STARTUP_TIMEOUT": "1",
        "PAPERLESS_MCP_HTTP_AUTH_TOKENS": HTTP_TOKEN,
        "PAPERLESS_MCP_HTTP_PORT": str(free_port()),
    }
    return inherited | fixed | given


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])  # pyright: ignore[reportAny] -- (host, port) tuple


def command(transport: str) -> list[str]:
    entrypoint = ENTRYPOINTS[transport]
    return [sys.executable, "-c", f"from mcp_server_paperless.cli import {entrypoint} as m; m()"]


@contextlib.asynccontextmanager
async def connected(
    transport: str, mode: ConnectMode, env: dict[str, str], log: Path
) -> AsyncGenerator[Client[StdioTransport] | Client[StreamableHttpTransport]]:
    """A client of the server in a process whose stderr goes to ``log``."""
    python, *arguments = command(transport)
    if transport == "stdio":
        pipe = StdioTransport(python, arguments, env=env, keep_alive=False, log_file=log)
        async with Client(pipe, mode=mode) as client:
            yield client
        return
    port = int(env["PAPERLESS_MCP_HTTP_PORT"])
    with log.open("w") as stderr:
        process = subprocess.Popen([python, *arguments], env=env, stderr=stderr)  # noqa: ASYNC220, S603 -- a fixed command line; a short start
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 1):
                    break
                assert process.poll() is None
                time.sleep(0.1)  # noqa: ASYNC251 -- nothing else runs here
            url = f"http://127.0.0.1:{port}/mcp"
            remote = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {HTTP_TOKEN}"})
            async with Client(remote, mode=mode) as client:
                yield client
        finally:
            process.terminate()
            _ = process.wait(30)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("transport", ENTRYPOINTS)
async def test_neither_answers_nor_arguments_of_failing_requests_reach_the_log(
    transport: str, mode: ConnectMode, tmp_path: Path
) -> None:
    log = tmp_path / "stderr"
    with paperless(unavailable_but_for_documents) as url:
        async with connected(transport, mode, environment(url), log) as client:
            # Paperless refuses with text: in a resource, and in a tool.
            with pytest.raises(MCPError, match=ANSWER):
                _ = await client.read_resource("paperless://documents/7")
            result = await client.call_tool(
                "get_document", {"document_id": 7}, raise_on_error=False
            )
            assert ANSWER in str(result.content)
            # The caller's own values are wrong: in a prompt, a resource and a tool.
            with pytest.raises(MCPError):
                _ = await client.get_prompt("annual_review", {"year": ARGUMENT})
            with pytest.raises(MCPError):
                _ = await client.get_prompt("annual_review", {ARGUMENT: "1"})
            for address in (
                f"paperless://documents/{ARGUMENT}",
                f"paperless://documents/7/content?offset={ARGUMENT}",
                f"paperless://taxonomy/{ARGUMENT}",
                f"paperless://{ARGUMENT}",
            ):
                with pytest.raises(MCPError):
                    _ = await client.read_resource(address)
            with pytest.raises(MCPError):
                _ = await client.get_prompt(ARGUMENT)
            for arguments in ({"document_id": ARGUMENT}, {ARGUMENT: 1}):
                result = await client.call_tool("get_document", arguments, raise_on_error=False)
                assert result.is_error
            result = await client.call_tool(
                "search_documents", {"query": ARGUMENT}, raise_on_error=False
            )
            assert result.is_error

    logged = log.read_text()
    assert ANSWER not in logged
    assert ARGUMENT not in logged
    assert "Traceback" not in logged
    # Diagnosable: what failed, and the type of the error.
    assert "document_detail failed: Validation" in logged
    assert "annual_review" in logged
    assert "PromptError" in logged or "ValidationError" in logged
    assert '"error": "Validation"' in logged


@pytest.mark.parametrize("transport", ENTRYPOINTS)
@pytest.mark.parametrize(
    ("status", "body", "named"),
    [
        (200, STARTUP, "Paperless sent an unexpected response for GET /api/"),
        (400, {"detail": STARTUP}, "Validation"),
        (409, {"detail": STARTUP}, "Conflict"),
        (401, {"detail": STARTUP}, "Paperless rejected the credentials"),
    ],
)
def test_a_failed_startup_check_is_named_but_quotes_nothing_of_the_answer(
    transport: str, status: int, body: object, named: str
) -> None:
    with paperless(lambda _path: (status, body)) as url:
        finished = subprocess.run(  # noqa: S603 -- a fixed command line
            command(transport),
            env=environment(url),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    assert finished.returncode != 0
    assert STARTUP not in finished.stderr
    assert "Traceback" not in finished.stderr
    assert named in finished.stderr


def content_of_tags(path: str) -> tuple[int, object]:
    """Not up at startup; a tag answers with content."""
    return (200, {"name": ANSWER}) if path.startswith("/api/tags/") else (503, {})


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("transport", ENTRYPOINTS)
async def test_debug_does_not_log_answers(
    transport: str, mode: ConnectMode, tmp_path: Path
) -> None:
    log = tmp_path / "stderr"
    given = {"PAPERLESS_MCP_LOG_LEVEL": "DEBUG", "FASTMCP_LOG_LEVEL": "DEBUG"}
    given["PAPERLESS_MCP_TOOLSETS"] = "core,raw"
    with paperless(content_of_tags) as url:
        async with connected(transport, mode, environment(url, **given), log) as client:
            result = await client.call_tool("paperless_api_get", {"path": "/api/tags/7/"})

    assert ANSWER in str(result.content)
    logged = log.read_text()
    assert '"level": "DEBUG"' in logged
    assert ANSWER not in logged


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("transport", ENTRYPOINTS)
async def test_failed_resources_and_prompts_are_private_above_debug_also_at_debug(
    transport: str, mode: ConnectMode, tmp_path: Path
) -> None:
    log = tmp_path / "stderr"
    with paperless(unavailable_but_for_documents) as url:
        env = environment(url, PAPERLESS_MCP_LOG_LEVEL="DEBUG")
        async with connected(transport, mode, env, log) as client:
            with pytest.raises(MCPError, match=ANSWER):
                _ = await client.read_resource("paperless://documents/7")
            with pytest.raises(MCPError):
                _ = await client.read_resource(f"paperless://documents/{ARGUMENT}")
            with pytest.raises(MCPError):
                _ = await client.get_prompt("annual_review", {"year": ARGUMENT})

    above = [line for line in log.read_text().splitlines() if '"level": "DEBUG"' not in line]
    assert [line for line in above if "document_detail failed" in line]
    assert not [line for line in above if ANSWER in line or ARGUMENT in line]


async def test_the_traceback_of_a_failed_prompt_is_in_the_debug_log(tmp_path: Path) -> None:
    """The proof that the tests above would see an argument, and a way to find a bug."""
    log = tmp_path / "stderr"
    with paperless(unavailable_but_for_documents) as url:
        env = environment(url, PAPERLESS_MCP_LOG_LEVEL="DEBUG")
        async with connected("stdio", "legacy", env, log) as client:
            with pytest.raises(MCPError):
                _ = await client.get_prompt("annual_review", {"year": ARGUMENT})

    [record] = [line for line in log.read_text().splitlines() if ARGUMENT in line]
    assert '"level": "DEBUG"' in record
    assert "Traceback" in record
