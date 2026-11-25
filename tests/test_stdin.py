"""What the stdio server reads: standard input through a filter of lines (runs on Windows, too)."""

import io
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest

from mcp_server_paperless.cli import Messages

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

pytestmark = pytest.mark.decision("decision-110")

REPLACED = b"\\" + b"ufffd"


@pytest.mark.parametrize("size", [1, 2, 7, 8192])
def test_messages_are_handed_on_in_pieces_of_any_size(size: int) -> None:
    lines = [b'{"a": "x"}\n', b"\n", b"\r\n", b'{"b": 1}\r\n', b'{"a": "\\ud800"}\r\n', b"last"]
    reader = io.BufferedReader(Messages(io.BytesIO(b"".join(lines))), size)

    found = reader.readlines()

    assert found == [*lines[:4], b'{"a": "' + REPLACED + b'"}\r\n', b"last"]
    assert reader.read() == b""
    assert reader.read() == b""


def test_a_call_whose_line_ends_like_on_windows_is_still_one_line() -> None:
    line = b'{"method": "tools/call", "params": {"arguments": {"a": "\\ud800"}}}\r\n'

    found = io.BufferedReader(Messages(io.BytesIO(line * 2))).readlines()

    assert len(found) == 2
    assert all(b"refused-text" in one and b"\r" not in one for one in found)


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
    source = Pieces(b'{"a": "\\ud', b'800"}', b"\n", b"second\n", b"cut \\ud8", b"00")
    reader = io.BufferedReader(Messages(io.BufferedReader(source)))

    assert reader.readline() == b'{"a": "' + REPLACED + b'"}\n'
    assert len(source.pieces) == 3
    assert reader.readline() == b"second\n"
    assert reader.readline() == b"cut " + REPLACED  # the input ended in the middle of a line
    assert reader.readline() == b""


def test_a_very_long_line_is_kept_once_and_handed_on_whole() -> None:
    line = b'{"a": "' + b"x" * 20_000_000 + b'"}\n'
    reader = io.BufferedReader(Messages(io.BytesIO(line + b"next\n")))

    assert reader.readline() == line
    assert reader.readline() == b"next\n"


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

    lines = [line.decode() for line in [*whole, b"last " + REPLACED]]
    assert done.stdout.decode().splitlines() == ["''", ascii(lines), "True True"]


def test_the_filter_and_a_stdio_server_are_tested_on_windows() -> None:
    workflow = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    _, job = workflow.read_text(encoding="utf-8").split("\n  rendering-windows:\n")

    steps = job.split("\n  audit:\n")[0]
    assert "uv run pytest tests/test_stdin.py tests/domain/test_texts.py --no-cov\n" in steps
    assert "uv run pytest tests/tools/test_text_refusal.py --no-cov -k stdio\n" in steps
