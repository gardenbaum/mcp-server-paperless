"""The stdio entrypoint: configuration and startup failures end with a clear message on stderr."""

import contextlib
import os
import runpy
import socket
import subprocess
import sys
import time
from typing import TYPE_CHECKING, override

import httpx2
import pytest
import uvicorn
from harness import TOKEN, paperless_answering, refused_url

from paperless_mcp.app import run_server
from paperless_mcp.cli import main, main_http
from paperless_mcp.tools._shared import ANNOTATIONS

if TYPE_CHECKING:
    from collections.abc import Callable, Generator


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the variables a test sets; the developer's PAPERLESS_* stay out."""
    for name in os.environ:
        if name.upper().startswith("PAPERLESS_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("PAPERLESS_MCP_STARTUP_TIMEOUT", "2")


def exit_message(capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    return err


@pytest.mark.usefixtures("isolated_logging")
def test_missing_configuration_names_the_variables(capsys: pytest.CaptureFixture[str]) -> None:
    err = exit_message(capsys)

    assert "paperless-mcp: invalid configuration" in err
    assert "PAPERLESS_URL" in err
    assert "PAPERLESS_TOKEN" in err


@pytest.mark.usefixtures("isolated_logging")
def test_invalid_values_are_named_but_not_echoed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "paperless.internal")
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)
    monkeypatch.setenv("PAPERLESS_MCP_LOG_LEVEL", "LOUD")

    err = exit_message(capsys)

    assert "PAPERLESS_URL" in err
    assert "PAPERLESS_MCP_LOG_LEVEL" in err
    assert "paperless.internal" not in err


@pytest.mark.decision("decision-2")
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "Paperless rejected the credentials"),
        (406, "Paperless does not accept API version 10."),
    ],
)
@pytest.mark.usefixtures("isolated_logging")
def test_a_rejected_token_or_api_version_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: int, message: str
) -> None:
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)
    with paperless_answering(status) as url:
        monkeypatch.setenv("PAPERLESS_URL", url)
        err = exit_message(capsys)

    assert f"paperless-mcp: {message}" in err
    assert "Hint: " in err
    assert TOKEN not in err


@pytest.mark.decision("decision-4")
@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PAPERLESS_MCP_TOOLSETS", "core,pdff"),
        ("PAPERLESS_MCP_DISABLED_TOOLS", "get_taks"),
    ],
)
@pytest.mark.usefixtures("isolated_logging")
def test_a_misspelt_toolset_or_tool_stops_the_start_naming_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], variable: str, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless.test")
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)
    monkeypatch.setenv(variable, value)

    err = exit_message(capsys)

    assert f"Unknown {'toolset' if 'TOOLSETS' in variable else 'tool'} in {variable}" in err
    assert value.rsplit(",", maxsplit=1)[-1] in err


@pytest.mark.decision("decision-4")
@pytest.mark.usefixtures("isolated_logging")
def test_filters_that_leave_no_tool_stop_the_start_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", refused_url())
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)
    monkeypatch.setenv("PAPERLESS_MCP_DISABLED_TOOLS", ",".join(ANNOTATIONS))

    err = exit_message(capsys)

    assert "paperless-mcp: No tools are left to offer" in err


@pytest.mark.usefixtures("isolated_logging")
def test_python_dash_m_runs_the_same_entrypoint(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        _ = runpy.run_module("paperless_mcp", run_name="__main__")

    assert caught.value.code == 1
    assert "invalid configuration" in capsys.readouterr().err


def test_the_transport_is_stdio_whatever_the_environment_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FASTMCP_TRANSPORT must not turn the stdio entrypoint, and its path uploads, into HTTP."""
    env = {**os.environ, "PAPERLESS_URL": refused_url(), "PAPERLESS_TOKEN": TOKEN}
    env |= {"FASTMCP_TRANSPORT": "streamable-http", "FASTMCP_PORT": "0"}
    env |= {"FASTMCP_HOST": "127.0.0.1", "PAPERLESS_MCP_STARTUP_TIMEOUT": "2"}
    monkeypatch.setenv("PYTHONUNBUFFERED", "1")

    finished = subprocess.run(
        [sys.executable, "-m", "paperless_mcp"],
        env=env,
        stdin=subprocess.DEVNULL,  # stdio ends at once; a web server would keep running
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert finished.returncode == 0
    assert "uvicorn" not in finished.stderr.lower()
    assert "http://" not in finished.stderr.replace(env["PAPERLESS_URL"], "")


# --- HTTP entrypoint (PLMCP-34) ------------------------------------------------------------------

HTTP_TOKEN = "test-token-not-a-secret"


class RecordingServer(uvicorn.Server):
    """A uvicorn server that does not serve; `ran` records that it was asked to."""

    started: bool = False

    def __init__(self, config: uvicorn.Config, *, starts: bool) -> None:
        super().__init__(config)
        self.ran: bool = False
        self._starts: bool = starts

    @override
    def run(self, sockets: list[socket.socket] | None = None) -> None:
        self.ran = True
        self.started = self._starts


def runner(
    *, starts: bool = True
) -> tuple[list[RecordingServer], Callable[[uvicorn.Server], bool]]:
    seen: list[RecordingServer] = []

    def run(server: uvicorn.Server) -> bool:
        assert isinstance(server, uvicorn.Server)
        recording = RecordingServer(server.config, starts=starts)
        seen.append(recording)
        recording.run()
        return recording.started

    return seen, run


def http_exit_message(capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as caught:
        main_http(runner()[1])
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    return err


@pytest.fixture
def connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless.test")
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)


@pytest.mark.decision("decision-27")
@pytest.mark.usefixtures("isolated_logging", "connection")
def test_http_without_authentication_stops_the_start_with_the_remedy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    err = http_exit_message(capsys)

    assert "paperless-mcp: refusing to start without authentication" in err
    assert "PAPERLESS_MCP_HTTP_AUTH_TOKENS" in err
    assert "PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK" in err


@pytest.mark.decision("decision-27")
@pytest.mark.usefixtures("isolated_logging", "connection")
def test_http_on_a_remote_address_never_starts_unauthenticated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_HOST", "0.0.0.0")  # noqa: S104 -- a value under test
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK", "true")

    err = http_exit_message(capsys)

    assert "not loopback" in err


@pytest.mark.usefixtures("isolated_logging", "connection")
def test_invalid_http_settings_are_named_but_not_echoed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_AUTH_TOKENS", "short-secret")
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_PORT", "0")

    err = http_exit_message(capsys)

    assert "PAPERLESS_MCP_HTTP_AUTH_TOKENS:" in err
    assert "PAPERLESS_MCP_HTTP_PORT" in err
    assert "short-secret" not in err


@pytest.mark.usefixtures("isolated_logging", "connection")
def test_http_serves_with_the_configured_address_and_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_AUTH_TOKENS", HTTP_TOKEN)
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_PORT", "8123")
    seen, run = runner()

    main_http(run)

    assert [(s.ran, s.config.host, s.config.port) for s in seen] == [(True, "127.0.0.1", 8123)]
    config = seen[0].config
    # No access log (it would print query strings), no forwarded-header trust, no server banner.
    assert (config.access_log, config.proxy_headers, config.server_header) == (False, False, False)
    assert config.log_config is None


@pytest.mark.usefixtures("isolated_logging", "connection")
def test_a_server_that_did_not_come_up_ends_the_process_with_exit_code_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_AUTH_TOKENS", HTTP_TOKEN)

    with pytest.raises(SystemExit) as caught:
        main_http(runner(starts=False)[1])

    assert caught.value.code == 1
    assert "did not start" in capsys.readouterr().err


def test_run_server_returns_whether_the_server_came_up() -> None:
    config = uvicorn.Config(lambda *_: None, port=0)

    assert run_server(RecordingServer(config, starts=True)) is True
    assert run_server(RecordingServer(config, starts=False)) is False


@contextlib.contextmanager
def http_process(env: dict[str, str], port: int) -> Generator[subprocess.Popen[str]]:
    """The real HTTP entrypoint in a process, once it answers on `port`."""
    process = subprocess.Popen(
        [sys.executable, "-c", "from paperless_mcp.cli import main_http; main_http()"],
        env={**os.environ, **env},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
                break
            assert process.poll() is None, process.stderr.read() if process.stderr else ""
            time.sleep(0.1)
        yield process
    finally:
        process.terminate()
        _ = process.communicate(timeout=30)


@pytest.mark.decision("decision-27")
def test_the_real_process_serves_http_with_bearer_authentication() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])  # pyright: ignore[reportAny] -- (host, port) tuple
    # An unreachable Paperless does not stop the start.
    env = {
        "PAPERLESS_URL": refused_url(),
        "PAPERLESS_TOKEN": TOKEN,
        "PAPERLESS_MCP_STARTUP_TIMEOUT": "2",
        "PAPERLESS_MCP_HTTP_PORT": str(port),
        "PAPERLESS_MCP_HTTP_AUTH_TOKENS": HTTP_TOKEN,
    }
    with http_process(env, port):
        base = f"http://127.0.0.1:{port}"
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        accept = {"Accept": "application/json, text/event-stream"}
        anonymous = httpx2.post(f"{base}/mcp", json=body, headers=accept)
        wrong = httpx2.post(
            f"{base}/mcp", json=body, headers={**accept, "Authorization": "Bearer nope"}
        )
        bearer = {"Authorization": f"Bearer {HTTP_TOKEN}", **accept}
        served = httpx2.post(f"{base}/mcp", json=body, headers=bearer)
        foreign = httpx2.post(f"{base}/mcp", json=body, headers={**bearer, "Host": "evil.example"})

    assert [r.status_code for r in (anonymous, wrong, served, foreign)] == [401, 401, 200, 421]
    assert all(r.headers["x-request-id"] for r in (anonymous, wrong, served, foreign))
    assert "server" not in served.headers
