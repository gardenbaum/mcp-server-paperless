"""The records of a log, also of one that somebody else could write into."""

from datetime import datetime

import pytest

from mcp_server_paperless.services.log_records import log_records

pytestmark = pytest.mark.decision("decision-90")
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
