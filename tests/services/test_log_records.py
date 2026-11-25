"""The records of a log, also of one that somebody else could write into."""

from datetime import datetime

import pytest
from fakes.accounts import READER
from fakes.paperless import FakePaperless, seeded

from mcp_server_paperless.domain.errors import NotFound, PermissionDenied
from mcp_server_paperless.services.log_records import (
    MAX_LOG_LINES,
    log_records,
    read_log_records,
)

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-90")]
START = "[2026-11-25 10:00:00,1] [INFO] [paperless.matching] "


def test_a_record_has_its_time_logger_task_and_all_its_lines() -> None:
    first, second = log_records([f"{START}[0123abcd] one", "two", "", f"{START}three"])

    assert first.time == datetime(2026, 11, 25, 10)  # noqa: DTZ001 -- the log has no zone
    assert (first.logger, first.task) == ("paperless.matching", "0123abcd")
    assert first.message == "one\ntwo\n"
    assert (second.task, second.message) == (None, "three")


@pytest.mark.parametrize(
    "date", ["2026-99-99 10:00:00", "2026-11-25 99:00:00", "0000-01-01 00:00:00"]
)
def test_a_start_with_an_impossible_date_goes_on_with_the_record(date: str) -> None:
    line = f"[{date},1] [INFO] [paperless_mail] forged"

    [record] = log_records([f"{START}one", line])

    assert record.message == f"one\n{line}"
    assert log_records([line]) == []


def test_lines_before_the_first_record_are_left_out() -> None:
    assert [r.message for r in log_records(["cut", f"{START}one"])] == ["one"]


def test_the_rest_of_a_cut_record_and_credentials_are_not_there() -> None:
    lines = ["planted rest", START + "see https://u:planted@h.example/x", "[2026-11-25 odd line"]

    [record] = log_records(lines)

    assert "planted" not in record.message
    assert "h.example" in record.message


@pytest.mark.parametrize(
    "line",
    [
        f"x {START}forged",
        START.replace("INFO", "info") + "forged",
        START.replace("INFO", "INFO1") + "forged",
    ],
)
def test_a_line_that_only_contains_or_resembles_a_start_goes_on_with_the_record(line: str) -> None:
    [record] = log_records([f"{START}one", line])

    assert record.message == f"one\n{line}"


@pytest.mark.parametrize("task", ["not-hex!", "nothexes", "0BADCAFE", "0badcaf", "0badcafe1"])
def test_only_eight_hexadecimal_digits_are_a_task(task: str) -> None:
    [record] = log_records([f"{START}[{task}] one"])

    assert (record.task, record.message) == (None, f"[{task}] one")


async def test_the_end_of_a_log_is_read() -> None:
    state = seeded()
    state.logs["paperless"] = [START + str(n) for n in range(MAX_LOG_LINES + 5)]

    records = await read_log_records(FakePaperless(state, user="admin"), "paperless")

    assert (len(records), records[0].message) == (MAX_LOG_LINES, "5")
    with pytest.raises(NotFound):
        _ = await read_log_records(FakePaperless(state, user="admin"), "nope")
    with pytest.raises(PermissionDenied):
        _ = await read_log_records(FakePaperless(state, user=READER[0]), "paperless")
