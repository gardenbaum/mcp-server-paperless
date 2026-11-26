"""The server starts: on Windows too, where the hosted job runs this (PLMCP-122)."""

import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

import mcp_server_paperless
from mcp_server_paperless.domain.errors import Validation
from mcp_server_paperless.services.allowed_paths import SECURE
from mcp_server_paperless.services.file_sink import FileSink
from mcp_server_paperless.services.upload_listing import list_allowed
from mcp_server_paperless.services.upload_source import read_allowed

pytestmark = pytest.mark.anyio

RUN = ["-m", "mcp_server_paperless"]
UNREACHABLE = {
    "PAPERLESS_URL": "http://127.0.0.1:9",
    "PAPERLESS_TOKEN": "a-token-of-no-instance",
    "PAPERLESS_MCP_STARTUP_TIMEOUT": "2",
}


def test_every_module_can_be_imported() -> None:
    names = [
        module.name
        for module in pkgutil.walk_packages(
            mcp_server_paperless.__path__, f"{mcp_server_paperless.__name__}."
        )
        if not module.name.endswith("__main__")
    ]

    done = subprocess.run(  # noqa: S603 -- this interpreter and names of this package
        [sys.executable, "-c", "\n".join(f"import {name}" for name in names)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert len(names) > 50
    assert (done.returncode, done.stderr) == (0, "")


@pytest.mark.parametrize("argument", ["--help", "-h"])
def test_help_says_how_the_server_is_run_without_any_setting(argument: str) -> None:
    env = {name: value for name, value in os.environ.items() if not name.startswith("PAPERLESS")}

    done = subprocess.run(  # noqa: S603 -- this interpreter and its own arguments
        [sys.executable, *RUN, argument],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=env,
    )

    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.startswith("usage: mcp-server-paperless\n")
    for name in ("PAPERLESS_URL", "PAPERLESS_TOKEN", "mcp-server-paperless-http"):
        assert name in done.stdout


async def test_a_stdio_server_starts_and_lists_its_tools(tmp_path: Path) -> None:
    """Paperless is not there: the server says so when a tool is called. Paths are enabled."""
    directories = {
        "PAPERLESS_MCP_UPLOAD_DIRS": str(tmp_path),
        "PAPERLESS_MCP_DOWNLOAD_DIRS": str(tmp_path),
    }
    transport = StdioTransport(
        sys.executable, RUN, env={**os.environ, **UNREACHABLE, **directories}, keep_alive=False
    )

    async with Client(transport) as client:
        tools = {tool.name for tool in await client.list_tools()}
        called = await client.call_tool("get_document", {"document_id": 1}, raise_on_error=False)

    assert {"search_documents", "upload_document", "download_document"} <= tools
    assert called.is_error
    assert "Paperless is unreachable." in str(called.content)


def test_paths_are_opened_safely_on_every_platform_but_windows() -> None:
    assert SECURE is (sys.platform != "win32")


def names(directory: Path) -> list[str]:
    return [path.name for path in directory.iterdir()]


@pytest.mark.skipif(SECURE, reason="this platform opens them safely")
async def test_without_safe_opening_every_use_of_a_path_is_refused(tmp_path: Path) -> None:
    """Not only with the seam of the tests: these are the defaults of the server."""
    file = tmp_path / "a.pdf"
    _ = file.write_bytes(b"%PDF-1.4")
    roots = [str(tmp_path)]

    with pytest.raises(Validation, match="not supported on this platform"):
        _ = read_allowed(str(file), roots, max_bytes=100)
    with pytest.raises(Validation, match="not supported on this platform"):
        _ = list_allowed(str(tmp_path), roots)
    with pytest.raises(Validation, match="not supported on this platform"):
        async with FileSink(roots).open(str(tmp_path)):
            pass
    assert names(tmp_path) == [file.name]


def test_the_start_is_tested_on_windows() -> None:
    workflow = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    _, job = workflow.read_text(encoding="utf-8").split("\n  rendering-windows:\n")

    steps = job.split("\n  audit:\n")[0]
    assert "uv run pytest tests/test_start.py tests/test_cli.py --no-cov\n" in steps
