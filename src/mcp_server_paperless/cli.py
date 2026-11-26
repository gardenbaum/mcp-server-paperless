"""Command-line entrypoints: ``mcp-server-paperless`` (stdio) and ``…-http`` (HTTP).

Configuration errors and a Paperless that rejects the token or API version end the process
with exit code 1 and one line on stderr, without a traceback.
"""

import contextlib
import io
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Final, NoReturn, final, override

from pydantic import ValidationError
from pydantic_settings import BaseSettings

from mcp_server_paperless.app import create_app, run_server, uvicorn_server
from mcp_server_paperless.domain.errors import PaperlessMcpError
from mcp_server_paperless.domain.texts import parseable
from mcp_server_paperless.observability import configure_logging, server_failure
from mcp_server_paperless.server import create_server, protect_process
from mcp_server_paperless.settings import (
    HttpSettings,
    OidcSettings,
    PaperlessSettings,
    ServerSettings,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence

    import uvicorn
    from _typeshed import WriteableBuffer
    from fastmcp import FastMCP


HELP: Final = """\
usage: mcp-server-paperless

MCP server for paperless-ngx over stdio; mcp-server-paperless-http serves it over HTTP.
It takes no arguments. PAPERLESS_URL and PAPERLESS_TOKEN must be set in the environment; the other
settings are in https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/configuration.md
"""


def _fail(message: str) -> NoReturn:
    print(f"mcp-server-paperless: {message}", file=sys.stderr)  # noqa: T201 -- the user-facing exit message
    raise SystemExit(1)


def _load[S: BaseSettings](kind: type[S], problems: list[str]) -> S | None:
    """The settings from the environment, or None with one line per invalid variable."""
    try:
        return kind.model_validate({})
    except ValidationError as error:
        prefix = kind.model_config.get("env_prefix") or ""
        # Only the variable and the reason: the input may be a token.
        problems.extend(
            f"{prefix}{'_'.join(map(str, problem['loc'])).upper()}: {problem['msg']}"
            for problem in error.errors()
        )
        return None


@final
class Messages(io.RawIOBase):
    """The lines of a stream as ``parseable`` has them."""

    def __init__(self, source: BinaryIO) -> None:
        """Read ``source``, which stays open."""
        super().__init__()
        self._source: BinaryIO = source
        self._rest: memoryview = memoryview(b"")

    @override
    def readable(self) -> bool:
        """It is."""
        return True

    @override
    def readinto(self, buffer: WriteableBuffer, /) -> int:
        """The next bytes of the current line, or the first of the next one."""
        view = memoryview(buffer).cast("B")
        self._rest = self._rest or memoryview(parseable(self._source.readline()))
        size = min(len(view), len(self._rest))
        view[:size] = self._rest[:size]
        # An empty part would keep the whole line.
        self._rest = self._rest[size:] if size < len(self._rest) else memoryview(b"")
        return size


@final
class _Input(io.TextIOWrapper):
    """Text over a buffer that is not closed with it, as in the SDK."""

    @override
    def close(self) -> None:
        """Nothing: closing waits for the thread that reads, for ever if no input comes (Ctrl-C)."""


def above_standard(descriptor: int) -> int:
    """A duplicate of ``descriptor`` that is none of 0 to 2, which a child process would get."""
    low: list[int] = []
    copy = os.dup(descriptor)
    while copy <= 2:  # noqa: PLR2004 -- the standard descriptors
        low.append(copy)
        copy = os.dup(descriptor)
    for number in low:
        os.close(number)
    return copy


@contextlib.contextmanager
def parseable_stdin() -> Generator[None]:
    """Standard input with every message the SDK's parser would drop in a form it takes.

    The SDK reads the buffer of ``sys.stdin``. It keeps the descriptor from child processes only if
    that buffer is the descriptor's own, so this does.
    """
    given = sys.stdin
    private = above_standard(0)
    with Path(os.devnull).open("rb") as nothing:
        _ = os.dup2(nothing.fileno(), 0)
    source = os.fdopen(private, "rb", closefd=False)
    sys.stdin = _Input(io.BufferedReader(Messages(source)))
    try:
        yield
    finally:
        _ = os.dup2(private, 0)
        sys.stdin = given


def run_stdio(server: FastMCP) -> None:
    """Serve over stdio until the client is done."""
    # No banner: it is noise on stderr and would check PyPI for updates (decision-7).
    with parseable_stdin():
        server.run(transport="stdio", show_banner=False)


def main(
    protect: Callable[[], None] = protect_process,
    run: Callable[[FastMCP], None] = run_stdio,
    *,
    arguments: Sequence[str] | None = None,
) -> None:
    """Run the server over stdio, or say how that is done.

    Args:
        protect: Keeps other processes out of this one; tests pass a fake.
        run: Serves; tests pass a fake.
        arguments: Those of the command line; tests pass their own.
    """
    if {"-h", "--help"} & set(sys.argv[1:] if arguments is None else arguments):
        _ = sys.stdout.write(HELP)
        return
    problems: list[str] = []
    paperless = _load(PaperlessSettings, problems)
    settings = _load(ServerSettings, problems)
    if paperless is None or settings is None:
        _fail("invalid configuration: " + "; ".join(problems))
    configure_logging(settings.log_level)
    protect()
    try:
        run(create_server(paperless, settings, stdio=True))
    except (PaperlessMcpError, ExceptionGroup) as error:
        _fail(server_failure(error))


def main_http(
    run: Callable[[uvicorn.Server], bool] = run_server,
    protect: Callable[[], None] = protect_process,
) -> None:
    """Run the server over Streamable HTTP; refuses to start without authentication.

    Authentication is static bearer tokens, the optional OIDC layer (``PAPERLESS_MCP_OIDC_*``),
    or both (decision-31).

    Args:
        run: Serves the uvicorn server and says whether it came up; tests pass a fake.
        protect: Keeps other processes out of this one; tests pass a fake.
    """
    problems: list[str] = []
    paperless = _load(PaperlessSettings, problems)
    settings = _load(ServerSettings, problems)
    http = _load(HttpSettings, problems)
    oidc = _load(OidcSettings, problems)
    if paperless is None or settings is None or http is None or oidc is None:
        _fail("invalid configuration: " + "; ".join(problems))
    configure_logging(settings.log_level)
    protect()
    try:
        app = create_app(paperless, settings, http, oidc=oidc)
    except PaperlessMcpError as error:
        _fail(server_failure(error))
    if not run(uvicorn_server(app, http)):
        _fail("the HTTP server did not start; see the log above")
