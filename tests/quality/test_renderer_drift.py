"""The weekly alert when the locked pypdfium2 is not the newest release (PLMCP-74)."""

import io
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import renderer_drift
import yaml
from scheduled_jobs import DRIFT_EXIT_CODE, JobError

if TYPE_CHECKING:
    from collections.abc import Sequence

pytestmark = pytest.mark.decision("decision-42")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "renderer-drift.yml"
JUSTFILE = (ROOT / "justfile").read_text(encoding="utf-8")


def lock(version: str) -> str:
    return (
        'version = 1\n[[package]]\nname = "pillow"\nversion = "13.0.0"\n'
        f'[[package]]\nname = "pypdfium2"\nversion = "{version}"\n'
    )


class PyPi:
    """Answers the project page of PyPI and records the commands."""

    def __init__(self, version: str) -> None:
        self.answer: str = json.dumps({"info": {"version": version}})
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> str:
        self.commands.append(list(command))
        return self.answer


def check(tmp_path: Path, locked: str, pypi: PyPi) -> tuple[int, str, str]:
    """The exit code, what was said (or the error), and the report."""
    file, report = tmp_path / "uv.lock", tmp_path / "report.md"
    _ = file.write_text(lock(locked), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    code = renderer_drift.main([str(file), str(report)], out=out, err=err, runner=pypi)
    said = out.getvalue() + err.getvalue()
    return code, said, report.read_text(encoding="utf-8") if report.exists() else ""


@pytest.mark.parametrize("newest", ["5.15.2", "5.9.0", "5.15.1"])
def test_the_newest_release_is_no_drift(tmp_path: Path, newest: str) -> None:
    pypi = PyPi(newest)

    code, said, report = check(tmp_path, "5.15.2", pypi)

    assert (code, report) == (0, "")
    assert said == f"no drift: pypdfium2 5.15.2 is locked, {newest} is the newest\n"
    assert pypi.commands == [
        ["curl", "--fail", "--silent", "--show-error", "https://pypi.org/pypi/pypdfium2/json"]
    ]


@pytest.mark.parametrize("newest", ["5.15.3", "5.16.0", "6.0.0"])
def test_a_newer_release_is_reported_with_what_to_do(tmp_path: Path, newest: str) -> None:
    code, _, report = check(tmp_path, "5.15.2", PyPi(newest))

    assert code == DRIFT_EXIT_CODE
    assert report.startswith(f"pypdfium2 `5.15.2` is locked, `{newest}` is the newest release.")
    assert "uv lock --upgrade-package pypdfium2" in report


@pytest.mark.parametrize("answer", ["", "[]", "{}", '{"info": {"version": "6.0.0rc1"}}'])
def test_an_answer_without_a_release_fails_instead_of_passing(tmp_path: Path, answer: str) -> None:
    pypi = PyPi("x")
    pypi.answer = answer

    code, said, report = check(tmp_path, "5.15.2", pypi)

    assert (code, report) == (1, "")
    assert said.startswith("renderer_drift: ")


def test_a_lock_without_the_package_fails() -> None:
    with pytest.raises(JobError, match="not in the lock file"):
        _ = renderer_drift.locked_version('version = 1\n[[package]]\nname = "x"\nversion = "1"\n')


def test_the_real_lock_names_a_version() -> None:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")

    assert re.fullmatch(r"\d+\.\d+\.\d+", renderer_drift.locked_version(text))


def mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def test_the_workflow_is_the_one_of_backlog_md_with_other_names() -> None:
    """The same schedule kind, pins, permissions and steps, which test_backlog_workflow checks."""
    other = (WORKFLOW.parent / "backlog-drift.yml").read_text(encoding="utf-8")
    mine, theirs = (
        mapping(cast("object", yaml.safe_load(text)))
        for text in (WORKFLOW.read_text(encoding="utf-8"), other)
    )
    text = json.dumps(mapping(theirs["jobs"])["backlog-drift"])
    renamed = cast("object", json.loads(text.replace("backlog-drift", "renderer-drift")))
    job = mapping(mapping(mine["jobs"])["renderer-drift"])

    def shape(value: object) -> list[dict[str, object]]:
        steps = cast("list[object]", mapping(value)["steps"])
        return [
            {key: part for key, part in mapping(step).items() if key != "name"}
            for step in steps
            if "GITHUB_STEP_SUMMARY" not in str(step)
        ]

    assert shape(job) == shape(renamed)
    assert (job["permissions"], job["timeout-minutes"]) == (
        {"contents": "read", "issues": "write"},
        10,
    )
    assert mine["permissions"] == {"contents": "read"}
    assert mine["concurrency"] == {"group": "renderer-drift", "cancel-in-progress": False}
    triggers = mapping(cast("dict[object, object]", mine)[True])  # YAML 1.1: `on` is True
    assert set(triggers) == {"schedule", "workflow_dispatch"}


@pytest.mark.parametrize(
    "needle",
    [
        "renderer-drift-check:",
        "scripts/renderer_drift.py uv.lock",
        "renderer-drift-issue report=",
        "--label renderer-drift",
    ],
)
def test_the_justfile_wires_the_check(needle: str) -> None:
    assert needle in JUSTFILE


def test_the_check_recipe_exit_code_is_the_scripts_drift_code() -> None:
    assert f"status == {DRIFT_EXIT_CODE}" in JUSTFILE.split("renderer-drift-check:")[1]
