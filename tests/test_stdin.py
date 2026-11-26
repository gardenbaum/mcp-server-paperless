"""What the stdio server reads: standard input through a filter of lines."""

import io
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast, override

import pytest

from mcp_server_paperless.cli import Messages, above_standard

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

pytestmark = pytest.mark.decision("decision-110")

CALL = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"arguments":{"a":"\\ud800"}}}'
REFUSED = (
    b'{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": '
    b'{"arguments": {"mcp-server-paperless/refused-text": "a"}}}\n'
)


@pytest.mark.parametrize("size", [1, 2, 7, 8192])
def test_messages_are_handed_on_in_pieces_of_any_size(size: int) -> None:
    lines = [b'{"a": "x"}\n', b"\n", b"\r\n", b'{"b": 1}\r\n', CALL + b"\r\n", b"last"]
    reader = io.BufferedReader(Messages(io.BytesIO(b"".join(lines))), size)

    found = reader.readlines()

    assert found == [*lines[:4], REFUSED, b"last"]
    assert reader.read() == b""
    assert reader.read() == b""


def test_a_call_whose_line_ends_like_on_windows_is_still_one_line() -> None:
    found = io.BufferedReader(Messages(io.BytesIO((CALL + b"\r\n") * 2))).readlines()

    assert found == [REFUSED] * 2


class Lines:
    """A source whose only line is known."""

    def __init__(self, line: bytes) -> None:
        self.line: bytes = line

    def readline(self) -> bytes:
        return self.line


class Pieces(io.RawIOBase):
    """A source that gives what it has in pieces, as a pipe does."""

    def __init__(self, *pieces: bytes) -> None:
        super().__init__()
        self.pieces: list[bytes] = list(pieces)

    @override
    def readable(self) -> bool:
        return True

    @override
    def readinto(self, buffer: WriteableBuffer, /) -> int:
        piece = self.pieces.pop(0) if self.pieces else b""
        memoryview(buffer)[: len(piece)] = piece
        return len(piece)


def test_an_escape_that_comes_in_two_reads_is_found_and_no_line_waits_for_the_next() -> None:
    source = Pieces(CALL[:-14], CALL[-14:], b"\n", b"second\n", b"cut \\ud8", b"00")
    reader = io.BufferedReader(Messages(io.BufferedReader(source)))

    assert reader.readline() == REFUSED
    assert len(source.pieces) == 3
    assert reader.readline() == b"second\n"
    assert reader.readline() == b"cut \\ud800"  # the input ended in the middle of a line
    assert reader.readline() == b""


def test_a_very_long_line_is_kept_once_and_handed_on_whole() -> None:
    line = b'{"a": "' + b"x" * 20_000_000 + b'"}\n'
    reader = io.BufferedReader(Messages(io.BytesIO(line + b"next\n")))

    assert reader.readline() == line
    assert reader.readline() == b"next\n"


def test_no_line_is_kept_after_it_was_handed_on() -> None:
    """It can hold a whole file."""
    source = Lines(b"x" * 1_000_000 + b"\n")
    messages = Messages(cast("BinaryIO", cast("object", source)))
    before = sys.getrefcount(source.line)

    assert messages.readinto(bytearray(500_000)) == 500_000
    assert sys.getrefcount(source.line) > before
    assert messages.readinto(bytearray(2_000_000)) == 500_001
    assert sys.getrefcount(source.line) == before


STDIN = """
import os, subprocess, sys
from mcp_server_paperless.cli import parseable_stdin
given, before = sys.stdin, os.fstat(0)
with parseable_stdin():
    child = [sys.executable, "-c", "import sys; print(ascii(sys.stdin.read()))"]
    print(subprocess.run(child, capture_output=True, text=True).stdout.strip())
    print(ascii([line.decode() for line in sys.stdin.buffer]))
print(sys.stdin is given, os.name == "nt" or os.path.samestat(os.fstat(0), before))
"""


def test_standard_input_is_read_to_its_end_and_kept_from_child_processes() -> None:
    """Long lines, several lines in one read, and a last one without an end."""
    long = b'{"note": "' + b"x" * 300_000 + b'\\\\ud800"}\n'
    whole = [long, b"\n", b"{}\r\n", b'{"a": "\\ud83e\\uddfe"}\n']

    done = subprocess.run(  # noqa: S603 -- this interpreter and a script of this file
        [sys.executable, "-c", STDIN],
        input=b"".join([*whole, b"last \\udc00"]),
        capture_output=True,
        check=True,
        timeout=60,
    )

    lines = [line.decode() for line in [*whole, b"last \\udc00"]]
    assert done.stdout.decode().splitlines() == ["''", ascii(lines), "True True"]


CLOSED = """
import os, sys
from mcp_server_paperless.cli import parseable_stdin
os.close(2)
with parseable_stdin():
    try:
        os.fstat(2)
    except OSError:
        print("free")
"""


@pytest.mark.skipif(sys.platform == "win32", reason="os.dup has no lowest number there")
def test_the_messages_are_never_read_from_a_standard_descriptor() -> None:
    """With stderr closed the lowest free one is 2, which a child process would inherit."""
    done = subprocess.run(  # noqa: S603 -- this interpreter and a script of this file
        [sys.executable, "-c", CLOSED], input=b"", capture_output=True, check=True, timeout=60
    )

    assert done.stdout == b"free\n"


def test_a_free_standard_descriptor_is_taken_only_while_a_higher_one_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same as above in this process, where os.dup gives what the test wants."""
    given = iter([1, 2, 7])
    closed: list[int] = []
    def dup(_descriptor: int) -> int:
        return next(given)

    monkeypatch.setattr("os.dup", dup)
    monkeypatch.setattr("os.close", closed.append)

    assert above_standard(0) == 7
    assert closed == [1, 2]


SERVER = """
import sys
sys.path[:0] = [{tests!r}]
from fakes.paperless import FakePaperless, seeded
from harness import serve
from mcp_server_paperless.cli import run_stdio
server = serve(FakePaperless(seeded(), user="admin"), stdio=True)
{run}
"""
HELLO = (
    b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18",'
    b'"capabilities":{},"clientInfo":{"name":"x","version":"1"}}}\n'
)


def ends(run: str, interrupts: int) -> tuple[bool, int]:
    """Whether a server ends by itself after Ctrl-C while its input is open, and its exit code."""
    code = SERVER.format(tests=str(Path(__file__).parent), run=run)
    with subprocess.Popen(  # noqa: S603 -- this interpreter and a script of this file
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        assert process.stdin
        assert process.stdout
        try:
            _ = process.stdin.write(HELLO)
            process.stdin.flush()
            assert process.stdout.readline()
            for _ in range(interrupts):
                time.sleep(0.7)
                process.send_signal(signal.SIGINT)
            try:
                return True, process.wait(5)
            except subprocess.TimeoutExpired:
                process.stdin.close()
                return False, process.wait(5)
        finally:
            process.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT cannot be sent to one process there")
@pytest.mark.parametrize("interrupts", [1, 2, 3])
def test_ctrl_c_ends_the_server_as_it_does_without_the_filter(interrupts: int) -> None:
    """The last Ctrl-C ends it while a thread still waits for input and holds the reader's lock."""
    plain = ends('server.run(transport="stdio", show_banner=False)', interrupts)

    assert ends("run_stdio(server)", interrupts) == plain
    assert interrupts < 3 or plain == (True, -signal.SIGINT)


def test_the_lines_are_tested_on_windows() -> None:
    workflow = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    _, job = workflow.read_text(encoding="utf-8").split("\n  rendering-windows:\n")

    assert "uv run pytest tests/domain/test_texts.py --no-cov\n" in job.split("\n  audit:\n")[0]
