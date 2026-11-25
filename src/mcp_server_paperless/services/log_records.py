"""Records of a Paperless log, for tools that look for a few of them (decision-88).

A record starts with a line ``[2026-11-25 07:43:15,123] [INFO] [paperless.matching] message``.
Inside a consume task the message begins with the first eight characters of the task id in
brackets, the only sure sign of the document a record is about. Whoever shows something of a record
says it in its own words: credentials are gone here (``lines_without_credentials``), names and text
of other documents are not.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.urls import lines_without_credentials

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.ports import AdminReader

# The most lines of a log that are read: Paperless gives the end of the current file.
MAX_LOG_LINES: Final = 1000
_START: Final = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\] \[[A-Z]+\] \[([\w.]+)\] (?:\[([0-9a-f]{8})\] )?"
)


@dataclass(frozen=True, slots=True)
class LogRecord:
    """A record of a log.

    Attributes:
        time: When, in the time zone of Paperless, which the log does not name.
        logger: Which part of Paperless wrote it.
        task: The first eight characters of the id of the consume task it was written in, if any.
        message: Its text, over all its lines.
    """

    time: datetime
    logger: str
    task: str | None
    message: str


def log_records(lines: Sequence[str]) -> list[LogRecord]:
    """The records of a log, oldest first, without credentials; a cut first one is left out."""
    records: list[LogRecord] = []
    for line in lines_without_credentials(lines):
        start = _START.match(line)
        if start is not None:
            time = datetime.fromisoformat(start[1])
            records.append(LogRecord(time, start[2], start[3], line[start.end() :]))
        elif records:
            last = records[-1]
            text = f"{last.message}\n{line}"
            records[-1] = LogRecord(last.time, last.logger, last.task, text)
    return records


async def read_log_records(reader: AdminReader, name: str) -> list[LogRecord]:
    """The records at the end of a log.

    Raises:
        PermissionDenied: The user is not a staff user.
        NotFound: No such log.
    """
    return log_records(await reader.get_log(name, limit=MAX_LOG_LINES))
