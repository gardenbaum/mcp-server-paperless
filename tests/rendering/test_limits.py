"""What bounds the child process, and what protects the server from it."""

import ctypes
import os
import subprocess
import sys
from typing import TYPE_CHECKING, NoReturn

import pytest

from mcp_server_paperless.rendering import limits
from mcp_server_paperless.rendering.limits import MEMORY_EXIT, TIME_EXIT

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

LINUX = sys.platform == "linux"
WINDOWS = sys.platform == "win32"


class Exit(Exception):  # noqa: N818 -- stands for the end of the process
    pass


class Process:
    """A child whose memory grows by `step` per look and whose clock by 20 ms."""

    def __init__(self, step: int) -> None:
        self.used: int = 0
        self.now: float = 100.0
        self.codes: list[int] = []
        self._step: int = step

    def memory(self) -> int:
        self.used += self._step
        return self.used

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def exit(self, code: int) -> NoReturn:
        self.codes.append(code)
        raise Exit

    def watch(self) -> None:
        with pytest.raises(Exit):
            limits.watch(
                self.memory,
                memory_bytes=100,
                seconds=1.0,
                end=self.exit,
                clock=self.clock,
                sleep=self.sleep,
            )


def test_the_watchdog_ends_a_process_over_its_memory() -> None:
    process = Process(step=40)
    process.watch()

    assert (process.codes, process.used) == ([MEMORY_EXIT], 120)


def test_the_watchdog_leaves_a_process_at_its_memory_alone_and_ends_it_at_its_time() -> None:
    process = Process(step=0)
    process.used = 100
    process.watch()

    assert process.codes == [TIME_EXIT]
    assert 101.0 <= process.now < 101.1


def test_the_memory_of_this_process_is_read() -> None:
    read = limits.memory_reader()

    assert 5_000_000 < read() < 8_000_000_000


def test_more_memory_is_noticed() -> None:
    read = limits.memory_reader()
    before = read()
    block = bytearray(b"x") * 200_000_000

    assert read() - before > 100_000_000
    del block


def test_linux_memory_is_the_resident_part_of_statm(tmp_path: Path) -> None:
    statm = tmp_path / "statm"
    _ = statm.write_text("9000 25 12 1 0 4 0\n")
    read = limits.linux_memory(statm)

    assert read() == 25 * os.sysconf("SC_PAGE_SIZE") if not WINDOWS else read


class Kernel32:
    """Windows' answer to GetProcessMemoryInfo."""

    def __init__(self, answer: int) -> None:
        self._answer: int = answer
        self.asked: list[tuple[int | None, int]] = []

    def K32GetProcessMemoryInfo(  # noqa: N802 -- the name of the Windows function
        self,
        process: ctypes.c_void_p,
        counters: ctypes.Array[ctypes.c_size_t],
        size: int,
    ) -> int:
        self.asked.append((process.value, size))
        counters[7] = 4711  # on 64 bit Windows
        return self._answer


def test_windows_memory_is_what_the_process_committed() -> None:
    kernel32 = Kernel32(1)

    assert limits.windows_memory(kernel32)() == 4711
    assert kernel32.asked == [(ctypes.c_void_p(-1).value, 72)]


def test_a_failing_windows_call_is_an_error_not_zero() -> None:
    with pytest.raises(OSError, match="GetProcessMemoryInfo"):
        _ = limits.windows_memory(Kernel32(0))()


class Posix:
    """Records the limits and the process controls that are set."""

    def __init__(self) -> None:
        self.limits: dict[str, int] = {}
        self.controls: list[tuple[int, int]] = []

    def limit(self, name: str, value: int) -> None:
        self.limits[name] = value

    def control(self, option: int, value: int) -> None:
        self.controls.append((option, value))


def locked(platform: str, *fonts: str) -> Posix:
    posix = Posix()
    limits.lock_down(
        1000, platform=platform, fonts=fonts, set_limit=posix.limit, control=posix.control
    )
    return posix


def test_the_child_writes_no_file_and_starts_no_process() -> None:
    for platform in ("linux", "darwin"):
        found = locked(platform, "/").limits

        assert (found["RLIMIT_FSIZE"], found["RLIMIT_NPROC"], found["RLIMIT_CORE"]) == (0, 0, 0)


def test_without_system_fonts_the_child_opens_no_file_and_no_socket(tmp_path: Path) -> None:
    missing = str(tmp_path / "fonts")

    assert locked("linux", missing).limits["RLIMIT_NOFILE"] == 0
    assert "RLIMIT_NOFILE" not in locked("linux", missing, str(tmp_path)).limits


def test_linux_adds_an_address_space_limit_and_no_new_privileges() -> None:
    linux, mac = locked("linux", "/"), locked("darwin", "/")

    assert (linux.limits["RLIMIT_AS"], linux.controls) == (2000, [(38, 1)])
    assert ("RLIMIT_AS" in mac.limits, mac.controls) == (False, [])


def test_windows_has_none_of_these() -> None:
    windows = locked("win32")

    assert (windows.limits, windows.controls) == ({}, [])


def test_the_guard_locks_down_and_starts_the_watchdog() -> None:
    started: list[Callable[[], None]] = []
    locks: list[int] = []

    limits.guard(1000, 5.0, start=started.append, lock=locks.append)

    assert (len(started), locks) == (1, [1000])


def test_the_server_keeps_its_environment_from_other_processes_on_linux_only() -> None:
    linux, mac = Posix(), Posix()

    limits.protect_server(platform="linux", control=linux.control)
    limits.protect_server(platform="darwin", control=mac.control)

    assert (linux.controls, mac.controls) == ([(4, 0)], [])


def test_a_server_that_cannot_be_protected_says_so_and_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def refuse(_option: int, _value: int) -> None:
        raise PermissionError

    limits.protect_server(platform="linux", control=refuse)

    assert "other processes of this user can read its environment" in caplog.text


LOCKED = """
import os, socket
from mcp_server_paperless.rendering.limits import lock_down
lock_down(2**30, fonts=())
for act in (lambda: open("/etc/hosts"), socket.socket, os.pipe, os.fork):
    try:
        act()
        print("done")
    except OSError as error:
        print(error.errno)
"""
WRITING = """
import sys, tempfile
from mcp_server_paperless.rendering.limits import lock_down
with tempfile.TemporaryFile(buffering=0) as file:
    lock_down(2**30, fonts=("/",))
    try:
        print(file.write(b"x"))
    except OSError as error:
        print(error.errno)
    print(open("/etc/hosts").close())
"""
# A server that asks a child what it can read of it in /proc, before and after.
PARENT = """
import subprocess, sys
from mcp_server_paperless.rendering.limits import protect_server
CHILD = (
    "import os\\n"
    "for name in ('environ', 'mem'):\\n"
    "    try:\\n"
    "        open(f'/proc/{os.getppid()}/{name}', 'rb').close()\\n"
    "        print('read')\\n"
    "    except OSError as error:\\n"
    "        print(type(error).__name__)\\n"
)
subprocess.run([sys.executable, "-I", "-c", CHILD], check=True)
protect_server()
subprocess.run([sys.executable, "-I", "-c", CHILD], check=True)
"""
ALLOCATING = """
from mcp_server_paperless.rendering.limits import lock_down
lock_down(2**28)
try:
    bytearray(2**30)
    print("allocated")
except MemoryError:
    print("refused")
"""


def child(code: str) -> str:
    """What a child process with `code` writes (a pipe: the limits forbid writing files)."""
    run = subprocess.run(  # noqa: S603 -- this interpreter and code of this test
        [sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, check=True, timeout=60
    )
    return run.stdout.decode()


@pytest.mark.skipif(WINDOWS, reason="there are no resource limits on Windows")
def test_a_locked_down_process_cannot_open_connect_or_start_anything() -> None:
    found = child(LOCKED)

    assert found.split()[:3] == ["24", "24", "24"]  # EMFILE
    if not LINUX or os.geteuid() != 0:
        assert found.split()[3:] == [str(35 if sys.platform == "darwin" else 11)]  # EAGAIN


@pytest.mark.skipif(WINDOWS, reason="there are no resource limits on Windows")
def test_a_process_that_may_read_fonts_still_writes_nothing_to_disk() -> None:
    assert child(WRITING).split() == ["27", "None"]  # EFBIG, and reading works


@pytest.mark.skipif(not LINUX, reason="/proc and prctl are Linux")
@pytest.mark.skipif(LINUX and os.geteuid() == 0, reason="root may read every process")
def test_a_child_of_a_protected_server_cannot_read_its_environment_or_memory() -> None:
    before, after = ["read", "read"], ["PermissionError", "PermissionError"]

    assert child(PARENT).split() == before + after


@pytest.mark.skipif(not LINUX, reason="RLIMIT_AS is enforced on Linux")
def test_the_address_space_is_bounded_on_linux() -> None:
    assert child(ALLOCATING).split() == ["refused"]
