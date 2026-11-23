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
from mcp_server_paperless.rendering.wire import Job, answer_of, encoded

if TYPE_CHECKING:
    from asyncio import StreamReader, StreamWriter
    from collections.abc import Mapping, Sequence

    from mcp_server_paperless.domain.rendering import Reason, Rendered, RenderRequest

COMMAND: Final = (sys.executable, "-I", "-m", "mcp_server_paperless.rendering.worker")
# The worst ordinary file measured took 2.4 s.
SECONDS: Final = 30.0
# The worst ordinary file measured took 500 MB.
MEMORY_BYTES: Final = 1024 * 1024 * 1024
# The image bytes of a call and the head before them.
OUTPUT_BYTES: Final = 1024 * 1024
_CHUNK: Final = 256 * 1024
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
        head = Job(
            request=request,
            content_bytes=len(content),
            memory_bytes=self._memory_bytes,
            seconds=self._seconds,
        )
        try:
            process = await asyncio.create_subprocess_exec(
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
            if process.returncode is None:
                process.kill()
                # What it still had in the pipe is read, or the pipe would stay open. Killed is
                # enough for a call that is cancelled again here: the loop does the rest.
                while await stdout.read(_CHUNK):
                    pass
                _ = await process.wait()
        if code:
            raise RenderFailed(reason=_EXITS.get(code, "crashed"))
        return answer_of(output, request)
