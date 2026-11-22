"""Guard tests of the weekly Backlog.md freshness workflow and its justfile recipes (PLMCP-50)."""

import re
from pathlib import Path
from typing import cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "backlog-drift.yml"
SHA_PINNED = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")


def mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def load() -> dict[str, object]:
    return mapping(cast("object", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))))


def job() -> dict[str, object]:
    return mapping(mapping(load()["jobs"])["backlog-drift"])


def steps() -> list[dict[str, object]]:
    return [mapping(step) for step in cast("list[object]", job()["steps"])]


def test_it_runs_weekly_and_on_demand_with_an_issue_switch() -> None:
    triggers = mapping(cast("dict[object, object]", load())[True])  # YAML 1.1: `on` is True

    schedule = cast("list[dict[str, str]]", triggers["schedule"])
    assert len(schedule) == 1
    assert re.fullmatch(r"\d+ \d+ \* \* \d", schedule[0]["cron"])
    dispatch = mapping(triggers["workflow_dispatch"])
    inputs = mapping(mapping(dispatch["inputs"])["open_issue"])
    assert (inputs["type"], inputs["default"]) == ("boolean", True)


def test_the_token_is_read_only_except_for_issues_on_the_one_job() -> None:
    assert load()["permissions"] == {"contents": "read"}
    assert job()["permissions"] == {"contents": "read", "issues": "write"}


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    uses = [str(step["uses"]) for step in steps() if "uses" in step]

    assert uses
    assert all(SHA_PINNED.fullmatch(entry.split(" ")[0]) for entry in uses)


def test_a_failing_step_fails_the_run() -> None:
    assert "continue-on-error" not in WORKFLOW.read_text(encoding="utf-8")
    assert isinstance(job()["timeout-minutes"], int)
    assert cast("int", job()["timeout-minutes"]) <= 10


def test_runs_do_not_overlap_and_are_not_cancelled() -> None:
    assert load()["concurrency"] == {"group": "backlog-drift", "cancel-in-progress": False}


def test_only_the_issue_step_gets_a_token_and_only_on_schedule_or_by_choice() -> None:
    with_token = [step for step in steps() if "GH_TOKEN" in mapping(step.get("env", {}))]

    assert len(with_token) == 1
    issue = with_token[0]
    assert issue["run"] == "just backlog-drift-issue"
    assert issue["if"] == "${{ github.event_name == 'schedule' || inputs.open_issue }}"


def test_the_check_runs_the_recipe_that_writes_the_report_the_issue_step_reads() -> None:
    check = next(step for step in steps() if step.get("run") == "just backlog-drift-check")

    assert mapping(check["env"]) == {"DRIFT_REPORT": "backlog-drift-report.md"}
    assert "persist-credentials: false" in WORKFLOW.read_text(encoding="utf-8")


JUSTFILE = (ROOT / "justfile").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "needle",
    [
        "backlog-update: backlog-install",
        "scripts/backlog_instructions.py refresh CLAUDE.md AGENTS.md",
        "backlog-drift-check:",
        "scripts/backlog_instructions.py check CLAUDE.md AGENTS.md",
        "backlog-drift-issue report=",
        "--label backlog-drift",
    ],
)
def test_the_justfile_wires_the_routine(needle: str) -> None:
    assert needle in JUSTFILE


def test_the_check_recipe_exit_code_is_the_scripts_drift_code() -> None:
    import scheduled_jobs  # noqa: PLC0415 -- scripts/ is on the path only for the quality tests

    assert (
        f"status == {scheduled_jobs.DRIFT_EXIT_CODE}" in JUSTFILE.split("backlog-drift-check:")[1]
    )
