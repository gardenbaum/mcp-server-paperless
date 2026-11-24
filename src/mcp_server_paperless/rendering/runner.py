"""Renders in a child process: one per call, bounded in time, memory and output.

The child is the same interpreter with the worker module, isolated (``-I``). It gets the file on
its stdin and answers on its stdout: both are pipes of its own, never the ones of the server, which
over stdio carry the protocol. It inherits no environment variable (the tokens are there)
and no descriptor.
"""

import asyncio
import contextlib
import os
import sys
from typing import TYPE_CHECKING, Final, cast

from mcp_server_paperless.domain.rendering import RenderFailed
from mcp_server_paperless.rendering.limits import MEMORY_EXIT, TIME_EXIT
from mcp_server_paperless.rendering.wire import Job, answer_of, encoded, overview_of

if TYPE_CHECKING:
    from asyncio import StreamReader, StreamWriter
    from collections.abc import Mapping, Sequence

    from mcp_server_paperless.domain.rendering import (
        Overview,
        OverviewRequest,
        Reason,
        Rendered,
        RenderRequest,
    )

COMMAND: Final = (sys.executable, "-I", "-m", "mcp_server_paperless.rendering.worker")
# The worst ordinary file measured took 2.4 s.
SECONDS: Final = 30.0
# The worst ordinary file measured took 500 MB.
MEMORY_BYTES: Final = 1024 * 1024 * 1024
# The image bytes of a call and the head before them.
OUTPUT_BYTES: Final = 1024 * 1024
_CHUNK: Final = 256 * 1024
# How long a killed child is waited for.
_REAP_SECONDS: Final = 1.0
# CREATE_NO_WINDOW: no console pops up when the server has none.
_NO_WINDOW: Final = 0x08000000
_EXITS: Final[Mapping[int, Reason]] = {MEMORY_EXIT: "memory", TIME_EXIT: "timeout"}


def child_environment(platform: str, environment: Mapping[str, str]) -> dict[str, str]:
    """The environment of the child: empty, but for what Windows needs to load its own libraries."""
    if platform == "win32" and "SystemRoot" in environment:
        return {"SystemRoot": environment["SystemRoot"]}
    return {}


async def _feed(stdin: StreamWriter, head: bytes, content: bytes) -> None:
    """Send the job; a child that ends early is noticed by its exit code."""
    with contextlib.suppress(ConnectionError):
        stdin.write(head)
        view = memoryview(content)
        for start in range(0, len(view), _CHUNK):
            stdin.write(view[start : start + _CHUNK])
            await stdin.drain()
        stdin.close()


async def _output(stdout: StreamReader, limit: int) -> bytes:
    """Everything the child writes, at most ``limit`` bytes."""
    try:
        _ = await stdout.readexactly(limit + 1)
    except asyncio.IncompleteReadError as end:
        return end.partial
    raise RenderFailed(reason="crashed")


class ChildRenderer:
    """``PageRenderer`` in a child process per call."""

    def __init__(
        self,
        *,
        command: Sequence[str] = COMMAND,
        seconds: float = SECONDS,
        memory_bytes: int = MEMORY_BYTES,
        output_bytes: int = OUTPUT_BYTES,
    ) -> None:
        """Start ``command`` for each call; the limits are the same for every file."""
        self._command: Sequence[str] = command
        self._seconds: float = seconds
        self._memory_bytes: int = memory_bytes
        self._output_bytes: int = output_bytes

    async def render(self, content: bytes, request: RenderRequest) -> Rendered:
        """See ``PageRenderer.render``."""
        return answer_of(await self._answered(content, request), request)

    async def inspect(self, content: bytes, request: OverviewRequest) -> Overview:
        """See ``PageRenderer.inspect``."""
        return overview_of(await self._answered(content, request), request)

    async def _answered(self, content: bytes, request: RenderRequest | OverviewRequest) -> bytes:
        """What the child writes for a job."""
        head = Job(
            request=request,
            content_bytes=len(content),
            memory_bytes=self._memory_bytes,
            seconds=self._seconds,
        )
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.subprocess_exec(
                lambda: asyncio.subprocess.SubprocessStreamProtocol(limit=_CHUNK, loop=loop),
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=child_environment(sys.platform, os.environ),
                creationflags=_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as error:
            # No process or descriptor left, or no such program.
            raise RenderFailed(reason="crashed") from error
        # What create_subprocess_exec does, but with the transport, which closes the pipes.
        process = asyncio.subprocess.Process(transport, protocol, loop)
        # Both are pipes, as asked for.
        stdin, stdout = cast("StreamWriter", process.stdin), cast("StreamReader", process.stdout)
        feed = asyncio.create_task(_feed(stdin, encoded(head), content))
        try:
            async with asyncio.timeout(self._seconds):
                output = await _output(stdout, self._output_bytes)
                code = await process.wait()
        except TimeoutError as error:
            raise RenderFailed(reason="timeout") from error
        finally:
            _ = feed.cancel()
            try:
                if process.returncode is None:
                    process.kill()
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(_REAP_SECONDS):
                            _ = await process.wait()
            finally:
                # Not awaited, so also for a call that is cancelled again here, and whoever else
                # holds the other end: a full or an inherited pipe would stay open.
                transport.close()
        if code:
            raise RenderFailed(reason=_EXITS.get(code, "crashed"))
        return output
