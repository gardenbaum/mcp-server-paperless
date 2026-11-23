"""Alert when the locked pypdfium2 is not the newest release (decision-42).

pypdfium2 bundles PDFium, whose vulnerabilities neither OSV nor pip-audit list for it: the only
protection is to follow its releases. Usage: ``renderer_drift.py LOCK REPORT``; the report is
written only on drift (exit code ``DRIFT_EXIT_CODE``). Nothing is updated.
"""

import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from pydantic import BaseModel, ValidationError
from scheduled_jobs import DRIFT_EXIT_CODE, JobError, Runner, parse_version, run

if TYPE_CHECKING:
    from collections.abc import Sequence

PACKAGE = "pypdfium2"
COMMAND = ("curl", "--fail", "--silent", "--show-error", f"https://pypi.org/pypi/{PACKAGE}/json")


class _Info(BaseModel):
    version: str


class _Project(BaseModel):
    info: _Info


class _Package(BaseModel):
    name: str
    version: str = ""


class _Lock(BaseModel):
    package: list[_Package]


def locked_version(text: str) -> str:
    """The version of the package in ``uv.lock``."""
    for package in _Lock.model_validate(tomllib.loads(text)).package:
        if package.name == PACKAGE:
            return package.version
    message = f"{PACKAGE} is not in the lock file"
    raise JobError(message)


def newest_version(runner: Runner) -> str:
    """The newest stable release on PyPI."""
    try:
        return _Project.model_validate_json(runner(COMMAND)).info.version
    except ValidationError as error:
        message = f"PyPI answered no version of {PACKAGE}"
        raise JobError(message) from error


def render_report(locked: str, newest: str) -> str:
    """The issue body."""
    lines = [
        f"{PACKAGE} `{locked}` is locked, `{newest}` is the newest release.",
        "",
        "It bundles PDFium, which reads untrusted PDFs. No scanner reports its vulnerabilities",
        f"for {PACKAGE}, so the lock has to follow its releases (decision-42).",
        "",
        f"Read the release notes, run `uv lock --upgrade-package {PACKAGE}` and `just check`, and",
        "open a pull request. A new release of this server carries it to users.",
        "",
        "Opened by the weekly `renderer-drift` workflow (`just renderer-drift-check`).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str], *, out: TextIO, err: TextIO, runner: Runner = run) -> int:
    """Compare; returns the exit code (``DRIFT_EXIT_CODE`` on drift)."""
    lock, report = map(Path, argv)
    try:
        locked, newest = locked_version(lock.read_text(encoding="utf-8")), newest_version(runner)
        drift = parse_version(newest) > parse_version(locked)
    except JobError as error:
        _ = err.write(f"renderer_drift: {error}\n")
        return 1
    if not drift:
        _ = out.write(f"no drift: {PACKAGE} {locked} is locked, {newest} is the newest\n")
        return 0
    _ = report.write_text(render_report(locked, newest), encoding="utf-8")
    _ = out.write(f"drift: {PACKAGE} {locked} is locked, {newest} is the newest\n")
    return DRIFT_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], out=sys.stdout, err=sys.stderr))
