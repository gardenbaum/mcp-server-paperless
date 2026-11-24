"""Changes that need one request per document: one after the other, until something ends them.

Nothing is started after ``RUN_SECONDS``, after a rate limit or a rejected credential, or after two
answers in a row were lost; what was not started is the caller's to name (decision-73).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import AuthenticationFailed, PaperlessMcpError
from mcp_server_paperless.services.bulk_documents import REFUSALS
from mcp_server_paperless.services.trash_scan import limited

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

# Nothing is started this many seconds after the call began (decision-50's number).
RUN_SECONDS: Final = 40.0
# Reasons the message of an answer repeats, and characters of one.
MAX_WHY: Final = 10
WHY_CHARS: Final = 300
OUT_OF_TIME: Final = f"{RUN_SECONDS:g} seconds have passed."
SILENT_TWICE: Final = "Paperless did not answer twice in a row."


@dataclass(frozen=True, slots=True)
class Ended[R]:
    """How one document ended.

    Attributes:
        row: What became of it.
        stop: Why nothing more may be asked of Paperless; empty if it may.
        silent: Whether an answer was lost and it is not known what became of the document.
    """

    row: R
    stop: str = ""
    silent: bool = False


def refused(error: PaperlessMcpError) -> bool:
    """Whether Paperless refused a request before it did anything."""
    return isinstance(error, REFUSALS) or limited(error)


def stopped_by(error: PaperlessMcpError) -> str:
    """Why ``error`` ends the whole call; empty if it concerns one request."""
    return error.message if limited(error) or isinstance(error, AuthenticationFailed) else ""


async def one_by_one[T, R](
    items: Sequence[T],
    act: Callable[[T], Awaitable[Ended[R]]],
    *,
    until: float,
    clock: Callable[[], float],
) -> tuple[list[R], str]:
    """What became of the items that were started, and why the rest was not (empty: all were)."""
    rows: list[R] = []
    silent = False
    for item in items:
        if clock() >= until:
            return rows, OUT_OF_TIME
        ended = await act(item)
        rows.append(ended.row)
        if ended.stop:
            return rows, ended.stop
        if ended.silent and silent:
            return rows, SILENT_TWICE
        silent = ended.silent
    return rows, ""


def cut(text: str, limit: int = WHY_CHARS) -> str:
    """``text`` with at most ``limit`` characters."""
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def reasons(rows: Sequence[tuple[int, str, str]], *, limit: int) -> str:
    """Documents with their status and why, at most ``limit`` of them."""
    told = " ".join(f"{number} ({status}): {cut(why)}" for number, status, why in rows[:limit])
    more = len(rows) - limit
    return f"{told} And {more} more." if more > 0 else told
