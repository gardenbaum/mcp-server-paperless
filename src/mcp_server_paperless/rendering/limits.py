"""What bounds the child process, and what protects the server from it.

This is not a sandbox: the child runs as the user of the server. It contains crashes, hangs and
memory, and makes code execution in the child worth less (see SECURITY.md).
"""

import ctypes
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# Exit codes of the child.
MEMORY_EXIT: Final = 3
TIME_EXIT: Final = 4

# Seconds between two looks at the memory of the child.
INTERVAL: Final = 0.02
# prctl(2).
_PR_SET_DUMPABLE: Final = 4
_PR_SET_NO_NEW_PRIVS: Final = 38
# Where PDFium looks for fonts a PDF names without embedding them (Linux, then macOS). The image
# has none of these.
SYSTEM_FONTS: Final = (
    "/usr/share/fonts",
    "/usr/share/X11/fonts",
    "/usr/local/share/fonts",
    "/System/Library/Fonts",
    "/Library/Fonts",
)
_STATM: Final = Path("/proc/self/statm")

logger = logging.getLogger(__name__)


class Kernel32(Protocol):
    """The one function of kernel32.dll that is called."""

    def K32GetProcessMemoryInfo(  # noqa: N802 -- the name of the Windows function
        self,
        process: ctypes.c_void_p,
        counters: ctypes.Array[ctypes.c_size_t],
        size: int,
        /,
    ) -> int:
        """Fill ``counters``; 0 on failure."""
        ...


def linux_memory(statm: Path = _STATM) -> Callable[[], int]:
    """Reads the resident memory. Not ``ru_maxrss``: Linux hands the parent's peak down."""
    # Kept open: the child may open no file later.
    descriptor = os.open(statm, os.O_RDONLY)
    page = os.sysconf("SC_PAGE_SIZE")
    return lambda: int(os.pread(descriptor, 64, 0).split()[1]) * page


def windows_memory(kernel32: Kernel32 | None = None) -> Callable[[], int]:
    """Reads the memory the process committed."""
    library = kernel32 or ctypes.CDLL("kernel32")
    # PROCESS_MEMORY_COUNTERS: two 32 bit numbers, then eight sizes; PagefileUsage is the seventh.
    head = 8 // ctypes.sizeof(ctypes.c_size_t)
    counters = (ctypes.c_size_t * (head + 8))()
    size = ctypes.sizeof(counters)

    def read() -> int:
        # -1 is the handle of the current process.
        if not library.K32GetProcessMemoryInfo(ctypes.c_void_p(-1), counters, size):
            message = "GetProcessMemoryInfo failed"
            raise OSError(message)
        return cast("int", counters[head + 6])

    return read


def peak_memory() -> Callable[[], int]:
    """Reads the peak of the resident memory, in bytes as macOS reports it."""
    import resource  # noqa: PLC0415 -- does not exist on Windows

    return lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def memory_reader(platform: str = sys.platform) -> Callable[[], int]:
    """What reads the memory of this process on this platform."""
    return {"linux": linux_memory, "win32": windows_memory}.get(platform, peak_memory)()


def watch(  # noqa: PLR0913 -- the limits, and one keyword per seam
    used: Callable[[], int],
    *,
    memory_bytes: int,
    seconds: float,
    end: Callable[[int], NoReturn] = os._exit,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """End this process once it uses more memory or lives longer than it may."""
    deadline = clock() + seconds
    while clock() < deadline:
        if used() > memory_bytes:
            end(MEMORY_EXIT)
        sleep(INTERVAL)
    end(TIME_EXIT)


def _set_limit(name: str, value: int) -> None:
    import resource  # noqa: PLC0415 -- does not exist on Windows

    kind = cast("int", vars(resource)[name])
    hard = resource.getrlimit(kind)[1]
    lowest = value if hard == resource.RLIM_INFINITY else min(value, hard)
    resource.setrlimit(kind, (lowest, lowest))


def _control(option: int, value: int) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    zero = ctypes.c_ulong(0)
    if library.prctl(option, ctypes.c_ulong(value), zero, zero, zero):
        raise OSError(ctypes.get_errno(), "prctl failed")


def lock_down(
    memory_bytes: int,
    *,
    platform: str = sys.platform,
    fonts: Sequence[str] = SYSTEM_FONTS,
    set_limit: Callable[[str, int], None] = _set_limit,
    control: Callable[[int, int], None] = _control,
) -> None:
    """Take from this process what rendering does not need; it cannot get it back.

    It can write no file and start no process or thread. Where there are no system fonts to read
    (as in the container image), it can open nothing at all: no file, no socket. With system fonts
    it still can, because PDFium reads them for text whose font is not embedded.
    """
    if platform == "win32":
        return
    for name in ("RLIMIT_CORE", "RLIMIT_FSIZE", "RLIMIT_NPROC"):
        set_limit(name, 0)
    if not any(Path(folder).is_dir() for folder in fonts):
        set_limit("RLIMIT_NOFILE", 0)
    if platform == "linux":
        # Above the watchdog, which looks every 20 ms: a single larger allocation fails at once.
        set_limit("RLIMIT_AS", 2 * memory_bytes)
        control(_PR_SET_NO_NEW_PRIVS, 1)


def _start(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


def guard(
    memory_bytes: int,
    seconds: float,
    *,
    start: Callable[[Callable[[], None]], None] = _start,
    lock: Callable[[int], None] = lock_down,
) -> None:
    """Bound this process: the watchdog first, because the limits forbid new threads."""
    used = memory_reader()
    start(lambda: watch(used, memory_bytes=memory_bytes, seconds=seconds))
    lock(memory_bytes)


def protect_server(
    *, platform: str = sys.platform, control: Callable[[int, int], None] = _control
) -> None:
    """Keep other processes of this user, the child included, out of this one (Linux).

    They cannot read its environment (the tokens), its memory or its descriptors in /proc, nor
    attach to it. It also writes no core dump.
    """
    if platform != "linux":
        return
    try:
        control(_PR_SET_DUMPABLE, 0)
    except OSError:
        logger.warning("prctl failed: other processes of this user can read its environment")
