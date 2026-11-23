"""What bounds the child process, and what protects the server from it."""

import ctypes
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, override

import pytest

from mcp_server_paperless.rendering import limits
from mcp_server_paperless.rendering.limits import MEMORY_EXIT, TIME_EXIT, WATCH_EXIT

if TYPE_CHECKING:
    from collections.abc import Callable

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


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OSError("gone"), WATCH_EXIT),
        (MemoryError(), MEMORY_EXIT),
        (ValueError("not a number"), WATCH_EXIT),
    ],
)
@pytest.mark.parametrize("broken", ["memory", "clock", "sleep"])
def test_a_watchdog_that_cannot_watch_ends_the_process(
    broken: str, error: Exception, code: int
) -> None:
    class Broken(Process):
        """Fails in one of its parts once the watch has begun."""

        def check(self, part: str) -> None:
            if part == broken and (part != "clock" or self.used > 0):
                raise error

        @override
        def memory(self) -> int:
            self.check("memory")
            return super().memory()

        @override
        def clock(self) -> float:
            self.check("clock")
            return super().clock()

        @override
        def sleep(self, seconds: float) -> None:
            self.check("sleep")
            super().sleep(seconds)

    process = Broken(step=1)
    process.watch()

    assert process.codes == [code]


def test_the_real_thread_ends_its_process_when_memory_cannot_be_read() -> None:
    code = (
        "import threading, time\n"
        "from mcp_server_paperless.rendering.limits import watch\n"
        "def used(): raise OSError\n"
        "threading.Thread(target=lambda: watch(used, memory_bytes=1, seconds=9)).start()\n"
        "time.sleep(5)\n"
    )

    run = subprocess.run([sys.executable, "-I", "-c", code], check=False, timeout=60)  # noqa: S603 -- this interpreter and code of this test

    assert run.returncode == WATCH_EXIT


def test_the_memory_of_this_process_is_read() -> None:
    read = limits.memory_reader()

    assert 5_000_000 < read() < 8_000_000_000


def test_more_memory_is_noticed() -> None:
    grown = child(GROWING)

    assert int(grown) > 100_000_000


@pytest.mark.skipif(WINDOWS, reason="there is no resource module on Windows")
def test_another_platform_is_measured_by_its_peak() -> None:
    import resource  # noqa: PLC0415 -- does not exist on Windows

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert 0 < before <= limits.memory_reader("darwin")() < 8_000_000_000


@pytest.mark.skipif(WINDOWS, reason="os.pread and os.sysconf are POSIX")
def test_linux_memory_is_the_resident_part_of_statm(tmp_path: Path) -> None:
    statm = tmp_path / "statm"
    _ = statm.write_text("9000 25 12 1 0 4 0\n")
    read = limits.linux_memory(statm)

    assert read() == 25 * os.sysconf("SC_PAGE_SIZE")


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


def test_the_child_may_not_write_data_into_files_or_fork() -> None:
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


@pytest.mark.skipif(WINDOWS, reason="there are no resource limits on Windows")
def test_a_limit_is_lowered_but_never_raised() -> None:
    import resource  # noqa: PLC0415 -- does not exist on Windows

    limits.set_limit("RLIMIT_CORE", 4096)
    lowered = resource.getrlimit(resource.RLIMIT_CORE)
    limits.set_limit("RLIMIT_CORE", 8192)

    assert resource.getrlimit(resource.RLIMIT_CORE) == lowered
    assert lowered[0] == lowered[1] <= 4096


class Libc:
    """Answers prctl."""

    def __init__(self, answer: int) -> None:
        self._answer: int = answer
        self.asked: list[tuple[int, list[int]]] = []

    def prctl(self, option: int, *values: ctypes.c_ulong) -> int:
        self.asked.append((option, [value.value for value in values]))
        return self._answer


def test_a_process_control_that_fails_is_an_error() -> None:
    works, fails = Libc(0), Libc(-1)

    limits.control(38, 1, works)
    with pytest.raises(OSError, match="prctl failed"):
        limits.control(38, 1, fails)

    assert works.asked == fails.asked == [(38, [1, 0, 0, 0])]


def test_the_watchdog_runs_beside_the_renderer() -> None:
    done = threading.Event()

    limits.start_thread(done.set)

    assert done.wait(10)


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
GROWING = """
from mcp_server_paperless.rendering.limits import memory_reader
read = memory_reader()
before = read()
block = bytearray(b"x") * 200_000_000
print(read() - before)
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
def test_a_locked_down_process_cannot_open_connect_or_fork() -> None:
    found = child(LOCKED)

    assert found.split()[:3] == ["24", "24", "24"]  # EMFILE
    if not LINUX or os.geteuid() != 0:
        assert found.split()[3:] == [str(35 if sys.platform == "darwin" else 11)]  # EAGAIN


@pytest.mark.skipif(WINDOWS, reason="there are no resource limits on Windows")
def test_a_process_that_may_read_fonts_cannot_write_data_into_a_file() -> None:
    assert child(WRITING).split() == ["27", "None"]  # EFBIG, and reading works


@pytest.mark.skipif(not LINUX, reason="/proc and prctl are Linux")
@pytest.mark.skipif(LINUX and os.geteuid() == 0, reason="root may read every process")
def test_a_child_of_a_protected_server_cannot_read_its_environment_or_memory() -> None:
    assert child(PARENT).split() == [*unprotected(ptrace_scope()), *REFUSED]


REFUSED = ["PermissionError", "PermissionError"]


def ptrace_scope() -> str:
    """What Yama lets a process attach to; empty where the kernel has no Yama."""
    setting = Path("/proc/sys/kernel/yama/ptrace_scope")
    return setting.read_text().strip() if setting.exists() else ""


def unprotected(scope: str) -> list[str]:
    """What a child can read of its unprotected parent: its environment, and its memory.

    From scope 1 on Yama refuses the memory to a child by itself (Ubuntu's default), but never the
    environment.
    """
    return ["read", "read" if scope in {"", "0"} else "PermissionError"]


@pytest.mark.parametrize(
    ("scope", "memory"),
    [("", "read"), ("0", "read"), ("1", "PermissionError"), ("2", "PermissionError")],
)
def test_the_control_of_the_protection_knows_what_yama_refuses(scope: str, memory: str) -> None:
    assert unprotected(scope) == ["read", memory]


@pytest.mark.skipif(not LINUX, reason="RLIMIT_AS is enforced on Linux")
def test_the_address_space_is_bounded_on_linux() -> None:
    assert child(ALLOCATING).split() == ["refused"]
