"""Tests of the Backlog.md freshness helpers: block refresh and drift check (PLMCP-50)."""

import io
import sys
import time
from typing import TYPE_CHECKING

import backlog_instructions
import pytest
import scheduled_jobs

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

START = "<!-- BACKLOG.MD GUIDELINES START -->"
END = "<!-- BACKLOG.MD GUIDELINES END -->"


def document(version: str, *, before: str = "intro\n", after: str = "\n## Mine\nkeep\n") -> str:
    header = f"<!-- backlog.md-instructions-version: {version} -->"
    return f"{before}{START}\n{header}\nbody for {version}\n{END}\n{after}"


def test_the_instructions_version_is_read_from_the_header_of_the_managed_block() -> None:
    assert backlog_instructions.instructions_version(document("1.57.4")) == "1.57.4"


def test_a_file_without_a_header_is_a_clear_error() -> None:
    with pytest.raises(scheduled_jobs.JobError, match=r"no backlog\.md-instructions-version"):
        _ = backlog_instructions.instructions_version("# nothing\n")


def test_outside_block_is_everything_but_the_managed_block() -> None:
    text = document("1.55.3", before="a\n", after="z\n")

    assert backlog_instructions.outside_block(text) == "a\n\nz\n"
    assert backlog_instructions.outside_block(document("9.9.9", before="a\n", after="z\n")) == (
        "a\n\nz\n"
    )


def test_a_file_without_the_block_markers_is_a_clear_error() -> None:
    with pytest.raises(scheduled_jobs.JobError, match=r"managed block"):
        _ = backlog_instructions.outside_block("no markers\n")


def test_behind_lists_the_files_older_than_the_latest_release() -> None:
    files = {"CLAUDE.md": document("1.57.4"), "AGENTS.md": document("1.55.3")}

    assert backlog_instructions.behind("1.57.4", files) == {"AGENTS.md": "1.55.3"}


def test_behind_compares_numerically_and_ignores_files_that_are_ahead() -> None:
    files = {"A.md": document("1.9.0"), "B.md": document("1.10.0"), "C.md": document("2.0.0")}

    assert backlog_instructions.behind("1.10.0", files) == {"A.md": "1.9.0"}


def test_behind_is_empty_when_every_file_is_current() -> None:
    assert backlog_instructions.behind("1.57.4", {"CLAUDE.md": document("1.57.4")}) == {}


def test_the_report_names_the_release_the_files_and_the_fix() -> None:
    report = backlog_instructions.render_report("1.57.4", {"AGENTS.md": "1.55.3"})

    assert report.splitlines()[0] == (
        "The Backlog.md instructions in the repository are behind the latest release 1.57.4:"
    )
    assert "- `AGENTS.md`: 1.55.3" in report
    assert "just backlog-update" in report


def write_files(root: Path, version: str) -> dict[str, str]:
    texts = {"CLAUDE.md": document(version, before="c\n"), "AGENTS.md": document(version)}
    for name, text in texts.items():
        _ = (root / name).write_text(text, encoding="utf-8")
    return texts


class FakeDriver:
    """Stands in for the CLI menu: rewrites the version header of the file the menu would pick."""

    def __init__(self, version: str, *, damage: bool = False, stale: bool = False) -> None:
        self.version: str = version
        self.damage: bool = damage
        self.stale: bool = stale
        self.calls: list[tuple[Path, int]] = []

    def __call__(self, root: Path, index: int) -> None:
        self.calls.append((root, index))
        path = root / backlog_instructions.MENU[index]
        text = path.read_text("utf-8")
        new = text
        if not self.stale:
            old_header = text.split("\n")[2]
            new = text.replace(
                old_header, f"<!-- backlog.md-instructions-version: {self.version} -->"
            )
        if self.damage:
            new = new.replace("keep", "lost")
        _ = path.write_text(new, encoding="utf-8")


def test_refresh_drives_the_menu_once_per_file_and_changes_only_the_block(tmp_path: Path) -> None:
    before = write_files(tmp_path, "1.55.3")
    drive = FakeDriver("1.57.4")

    backlog_instructions.refresh(tmp_path, ["CLAUDE.md", "AGENTS.md"], "1.57.4", drive)

    assert drive.calls == [(tmp_path, 0), (tmp_path, 1)]
    for name, old in before.items():
        new = (tmp_path / name).read_text("utf-8")
        assert backlog_instructions.instructions_version(new) == "1.57.4"
        assert backlog_instructions.outside_block(new) == backlog_instructions.outside_block(old)


def test_refresh_restores_a_file_whose_text_outside_the_block_changed(tmp_path: Path) -> None:
    before = write_files(tmp_path, "1.55.3")

    with pytest.raises(scheduled_jobs.JobError, match="outside the managed block"):
        backlog_instructions.refresh(
            tmp_path, ["AGENTS.md"], "1.57.4", FakeDriver("1.57.4", damage=True)
        )

    assert (tmp_path / "AGENTS.md").read_text("utf-8") == before["AGENTS.md"]


def test_refresh_fails_when_the_header_does_not_reach_the_installed_release(tmp_path: Path) -> None:
    _ = write_files(tmp_path, "1.55.3")

    with pytest.raises(scheduled_jobs.JobError, match=r"still 1\.55\.3, expected 1\.57\.4"):
        backlog_instructions.refresh(
            tmp_path, ["CLAUDE.md"], "1.57.4", FakeDriver("1.57.4", stale=True)
        )


def test_refresh_leaves_a_current_file_alone(tmp_path: Path) -> None:
    _ = write_files(tmp_path, "1.57.4")
    drive = FakeDriver("1.57.4")

    backlog_instructions.refresh(tmp_path, ["CLAUDE.md"], "1.57.4", drive)

    assert drive.calls == []


def test_refresh_refuses_a_file_the_menu_does_not_offer(tmp_path: Path) -> None:
    with pytest.raises(scheduled_jobs.JobError, match=r"README\.md"):
        backlog_instructions.refresh(tmp_path, ["README.md"], "1.57.4", FakeDriver("1.57.4"))


def test_the_menu_order_is_the_clis() -> None:
    assert backlog_instructions.MENU[:2] == ("CLAUDE.md", "AGENTS.md")


def run_main(argv: list[str], *, runner_output: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = backlog_instructions.main(
        argv,
        out=out,
        err=err,
        runner=lambda _command: runner_output,
        driver=FakeDriver("1.57.4"),
    )
    return code, out.getvalue(), err.getvalue()


def test_main_check_writes_the_report_and_exits_10_on_drift(tmp_path: Path) -> None:
    _ = write_files(tmp_path, "1.55.3")
    report = tmp_path / "report.md"

    code, out, _ = run_main(
        [
            "check",
            str(tmp_path / "CLAUDE.md"),
            str(tmp_path / "AGENTS.md"),
            "--report",
            str(report),
        ],
        runner_output="1.57.4\n",
    )

    assert code == scheduled_jobs.DRIFT_EXIT_CODE
    assert out == "drift: latest 1.57.4, behind: CLAUDE.md, AGENTS.md\n"
    assert "just backlog-update" in report.read_text("utf-8")


def test_main_check_exits_0_and_writes_nothing_when_current(tmp_path: Path) -> None:
    _ = write_files(tmp_path, "1.57.4")
    report = tmp_path / "report.md"

    code, out, _ = run_main(
        ["check", str(tmp_path / "CLAUDE.md"), "--report", str(report)], runner_output="1.57.4\n"
    )

    assert (code, out) == (0, "no drift: latest 1.57.4\n")
    assert not report.exists()


def test_main_check_asks_npm_for_the_latest_version(tmp_path: Path) -> None:
    commands: list[list[str]] = []
    _ = write_files(tmp_path, "1.57.4")

    def runner(command: Sequence[str]) -> str:
        commands.append(list(command))
        return "1.57.4\n"

    code = backlog_instructions.main(
        ["check", str(tmp_path / "CLAUDE.md"), "--report", str(tmp_path / "r.md")],
        out=io.StringIO(),
        err=io.StringIO(),
        runner=runner,
        driver=FakeDriver("1.57.4"),
    )

    assert code == 0
    assert commands == [["npm", "view", "backlog.md", "version"]]


def test_main_check_rejects_a_version_that_is_not_a_release(tmp_path: Path) -> None:
    _ = write_files(tmp_path, "1.57.4")

    code, _, err = run_main(
        ["check", str(tmp_path / "CLAUDE.md"), "--report", str(tmp_path / "r.md")],
        runner_output="not-a-version\n",
    )

    assert code == 1
    assert "not a release version" in err


def test_main_refresh_uses_the_installed_cli_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = write_files(tmp_path, "1.55.3")
    monkeypatch.chdir(tmp_path)

    code, out, _ = run_main(["refresh", "CLAUDE.md", "AGENTS.md"], runner_output="1.57.4\n")

    assert code == 0
    assert out == "refreshed to 1.57.4: CLAUDE.md, AGENTS.md\n"
    assert (
        backlog_instructions.instructions_version((tmp_path / "AGENTS.md").read_text("utf-8"))
        == "1.57.4"
    )


FAKE_MENU = """\
import os, sys, termios, tty
print("Select files", flush=True)
tty.setraw(sys.stdin.fileno())
keys = b""
while not keys.endswith(b"\\r"):
    keys += os.read(sys.stdin.fileno(), 1)
open("keys.txt", "wb").write(keys)
"""


@pytest.mark.parametrize(
    ("index", "keys"),
    [(0, b" \r"), (1, b"\x1b[B \r")],
)
def test_the_menu_driver_moves_down_toggles_and_confirms_through_a_terminal(
    tmp_path: Path, index: int, keys: bytes
) -> None:
    fake = tmp_path / "fake_menu.py"
    _ = fake.write_text(FAKE_MENU, encoding="utf-8")

    backlog_instructions.drive_menu(tmp_path, index, [sys.executable, str(fake)])

    assert (tmp_path / "keys.txt").read_bytes() == keys


def test_the_menu_driver_reports_a_failing_cli(tmp_path: Path) -> None:
    with pytest.raises(scheduled_jobs.JobError, match="exited with 3"):
        backlog_instructions.drive_menu(tmp_path, 0, [sys.executable, "-c", "raise SystemExit(3)"])


def test_refresh_restores_the_original_when_the_cli_removes_the_markers(tmp_path: Path) -> None:
    before = write_files(tmp_path, "1.55.3")

    def wipe(root: Path, index: int) -> None:
        _ = (root / backlog_instructions.MENU[index]).write_text("no markers\n", encoding="utf-8")

    with pytest.raises(scheduled_jobs.JobError, match=r"managed block"):
        backlog_instructions.refresh(tmp_path, ["CLAUDE.md"], "1.57.4", wipe)

    assert (tmp_path / "CLAUDE.md").read_text("utf-8") == before["CLAUDE.md"]


def test_refresh_restores_the_original_when_the_header_is_missing_afterwards(
    tmp_path: Path,
) -> None:
    before = write_files(tmp_path, "1.55.3")

    def drop_header(root: Path, index: int) -> None:
        path = root / backlog_instructions.MENU[index]
        text = path.read_text("utf-8")
        _ = path.write_text(text.replace(text.split("\n")[2] + "\n", ""), encoding="utf-8")

    with pytest.raises(scheduled_jobs.JobError, match=r"header"):
        backlog_instructions.refresh(tmp_path, ["AGENTS.md"], "1.57.4", drop_header)

    assert (tmp_path / "AGENTS.md").read_text("utf-8") == before["AGENTS.md"]


def test_refresh_restores_the_original_when_the_driver_fails(tmp_path: Path) -> None:
    before = write_files(tmp_path, "1.55.3")

    def fail(root: Path, index: int) -> None:
        _ = (root / backlog_instructions.MENU[index]).write_text("half\n", encoding="utf-8")
        message = "boom"
        raise scheduled_jobs.JobError(message)

    with pytest.raises(scheduled_jobs.JobError, match="boom"):
        backlog_instructions.refresh(tmp_path, ["CLAUDE.md"], "1.57.4", fail)

    assert (tmp_path / "CLAUDE.md").read_text("utf-8") == before["CLAUDE.md"]


HANG = "import time; time.sleep(60)"
STUBBORN = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"


@pytest.mark.parametrize("script", [HANG, STUBBORN])
def test_the_menu_driver_stops_a_child_that_never_exits(tmp_path: Path, script: str) -> None:
    started = time.monotonic()

    with pytest.raises(scheduled_jobs.JobError, match="did not finish"):
        backlog_instructions.drive_menu(tmp_path, 0, [sys.executable, "-c", script], patience=0.3)

    assert time.monotonic() - started < 10
