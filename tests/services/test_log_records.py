"""Records of a log: parsed, without credentials, with the consume task they belong to."""

from datetime import datetime

import pytest
from fakes.accounts import READER
from fakes.paperless import FakePaperless, seeded

from mcp_server_paperless.domain.errors import NotFound, PermissionDenied
from mcp_server_paperless.services.log_records import (
    MAX_LOG_LINES,
    LogRecord,
    log_records,
    read_log_records,
)

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-88")]

START = "[2026-11-25 07:43:15,123] [INFO] [paperless.matching] "
TIME = datetime(2026, 11, 25, 7, 43, 15)  # noqa: DTZ001 -- a log has no time zone


def test_a_record_has_its_lines_and_its_task() -> None:
    lines = [START + "one", "two", START + "[0badcafe] three", START + "[not-hex!] four"]

    assert log_records(lines) == [
        LogRecord(TIME, "paperless.matching", None, "one\ntwo"),
        LogRecord(TIME, "paperless.matching", "0badcafe", "three"),
        LogRecord(TIME, "paperless.matching", None, "[not-hex!] four"),
    ]


def test_the_rest_of_a_cut_record_and_credentials_are_not_there() -> None:
    lines = ["planted rest", START + "see https://u:planted@h.example/x", "[2026-11-25 odd line"]

    [record] = log_records(lines)

    assert "planted" not in record.message
    assert "h.example" in record.message


async def test_the_end_of_a_log_is_read() -> None:
    state = seeded()
    state.logs["paperless"] = [START + str(n) for n in range(MAX_LOG_LINES + 5)]

    records = await read_log_records(FakePaperless(state, user="admin"), "paperless")

    assert (len(records), records[0].message) == (MAX_LOG_LINES, "5")
    with pytest.raises(NotFound):
        _ = await read_log_records(FakePaperless(state, user="admin"), "nope")
    with pytest.raises(PermissionDenied):
        _ = await read_log_records(FakePaperless(state, user=READER[0]), "paperless")
