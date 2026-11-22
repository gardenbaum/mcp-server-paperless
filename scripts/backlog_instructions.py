"""Keep the Backlog.md CLI and the agent instructions it wrote current (PLMCP-50).

Subcommands (see the justfile recipes `backlog-update` and `backlog-drift-check`):

* ``refresh``: rewrite the managed Backlog.md block of the given files with the installed CLI.
  ``backlog agents --update-instructions`` only has an interactive multi-select menu, so it is
  driven through a pseudo-terminal; a file whose text outside the block changed is restored.
* ``check``: compare the latest release on npm with the ``backlog.md-instructions-version`` header
  of the given files and write a report when one is behind (exit code ``DRIFT_EXIT_CODE``).
"""

import argparse
import os
import pty
import re
import select
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from scheduled_jobs import DRIFT_EXIT_CODE, JobError, Runner, parse_version, run

# The files the CLI's menu offers, in its order (the first is under the cursor, none selected).
MENU = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md")
COMMAND = ("backlog", "agents", "--update-instructions")
Driver = Callable[[Path, int], None]

_START = "<!-- BACKLOG.MD GUIDELINES START -->"
_END = "<!-- BACKLOG.MD GUIDELINES END -->"
_HEADER = re.compile(r"<!-- backlog\.md-instructions-version: (\S+) -->")
_DOWN = b"\x1b[B"
# The menu is ready when the CLI has written something and then been quiet this long (seconds).
_QUIET = 0.7
_PATIENCE = 20.0


def instructions_version(text: str) -> str:
    """The release named by the header of the managed block."""
    match = _HEADER.search(text)
    if not match:
        message = "no backlog.md-instructions-version header in the file"
        raise JobError(message)
    return match[1]


def outside_block(text: str) -> str:
    """Everything except the managed block (its marker lines and the lines between them)."""
    start, end = text.find(_START), text.find(_END)
    if start < 0 or end < start:
        message = "no managed block (BACKLOG.MD GUIDELINES START/END) in the file"
        raise JobError(message)
    return text[:start] + text[end + len(_END) :]


def behind(latest: str, files: Mapping[str, str]) -> dict[str, str]:
    """The files whose instructions are older than ``latest``, with their version."""
    newest = parse_version(latest)
    older: dict[str, str] = {}
    for name, text in files.items():
        version = instructions_version(text)
        if parse_version(version) < newest:
            older[name] = version
    return older


def render_report(latest: str, older: Mapping[str, str]) -> str:
    """The issue body: the release, the files behind it and the command that fixes them."""
    lines = [
        f"The Backlog.md instructions in the repository are behind the latest release {latest}:",
        "",
        *(f"- `{name}`: {version}" for name, version in older.items()),
        "",
        (
            "Run `just backlog-update` (installs the latest CLI and refreshes the managed block "
            "of CLAUDE.md and AGENTS.md), review the diff and commit it."
        ),
    ]
    return "\n".join(lines) + "\n"


def _read_until_quiet(fd: int, *, wait: float) -> None:
    """Discard the CLI's output until it has been silent for `_QUIET`, at most `wait` seconds."""
    deadline = time.monotonic() + wait
    last = time.monotonic()
    seen = False
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                _ = os.read(fd, 65536)
            except OSError:  # the child closed its side of the terminal
                return
            seen, last = True, time.monotonic()
        elif seen and time.monotonic() - last >= _QUIET:
            return


def _stop(child: subprocess.Popen[bytes], grace: float) -> None:
    """Terminate the child, kill it when it ignores that, and reap it without waiting forever."""
    child.terminate()
    try:
        _ = child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        child.kill()
        _ = child.wait(timeout=grace)


def drive_menu(
    root: Path, index: int, command: Sequence[str] = COMMAND, *, patience: float = _PATIENCE
) -> None:
    """Run the interactive menu in ``root`` and select the ``index``-th item with the keyboard."""
    master, slave = pty.openpty()
    try:
        # The terminal is the child's stdin and stdout, so the CLI shows its menu.
        with subprocess.Popen(  # noqa: S603 -- fixed argument vector
            command, cwd=root, stdin=slave, stdout=slave, stderr=slave, start_new_session=True
        ) as child:
            os.close(slave)
            _read_until_quiet(master, wait=patience)
            for key in (_DOWN * index, b" ", b"\r"):
                if key:
                    _ = os.write(master, key)
                    _read_until_quiet(master, wait=min(1.0, patience))
            _read_until_quiet(master, wait=patience)
            try:
                code = child.wait(timeout=patience)
            except subprocess.TimeoutExpired as error:
                _stop(child, patience)
                message = f"{command[0]} did not finish within {patience:g} s and was stopped"
                raise JobError(message) from error
    finally:
        os.close(master)
    if code:
        message = f"{command[0]} exited with {code}"
        raise JobError(message)


def _check_refreshed(name: str, before: str, after: str, expected: str) -> None:
    if outside_block(after) != outside_block(before):
        message = f"{name} changed outside the managed block"
        raise JobError(message)
    version = instructions_version(after)
    if version != expected:
        message = f"{name} is still {version}, expected {expected}: the menu did not select it"
        raise JobError(message)


def refresh(root: Path, names: Sequence[str], expected: str, driver: Driver) -> None:
    """Refresh the managed block of each file; only the block may change.

    Whatever goes wrong while a file is being refreshed, its original text is put back.
    """
    for name in names:
        if name not in MENU:
            message = f"{name} is not offered by the CLI's menu ({', '.join(MENU)})"
            raise JobError(message)
    for name in names:
        path = root / name
        before = path.read_text("utf-8")
        if instructions_version(before) == expected:
            continue
        try:
            driver(root, MENU.index(name))
            _check_refreshed(name, before, path.read_text("utf-8"), expected)
        except JobError, OSError:
            _ = path.write_text(before, encoding="utf-8")
            raise


class _Args(argparse.Namespace):
    command: str = ""
    files: list[Path] = []  # noqa: RUF012 -- argparse assigns it
    report: Path = Path()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    refreshing = commands.add_parser("refresh", help="refresh the managed block with the CLI")
    _ = refreshing.add_argument("files", nargs="+", type=Path, help="names from the CLI's menu")
    check = commands.add_parser("check", help="compare the npm release with the headers")
    _ = check.add_argument("files", nargs="+", type=Path)
    _ = check.add_argument("--report", type=Path, required=True, help="written only on drift")
    return parser


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0].strip() if lines else ""


def _refresh(args: _Args, out: TextIO, runner: Runner, driver: Driver) -> int:
    installed = _first_line(runner(["backlog", "--version"]))
    _ = parse_version(installed)
    names = [path.as_posix() for path in args.files]
    refresh(Path.cwd(), names, installed, driver)
    _ = out.write(f"refreshed to {installed}: {', '.join(names)}\n")
    return 0


def _check(args: _Args, out: TextIO, runner: Runner) -> int:
    latest = _first_line(runner(["npm", "view", "backlog.md", "version"]))
    _ = parse_version(latest)
    older = behind(latest, {path.name: path.read_text("utf-8") for path in args.files})
    if not older:
        _ = out.write(f"no drift: latest {latest}\n")
        return 0
    _ = args.report.write_text(render_report(latest, older), encoding="utf-8")
    _ = out.write(f"drift: latest {latest}, behind: {', '.join(older)}\n")
    return DRIFT_EXIT_CODE


def main(
    argv: Sequence[str],
    *,
    out: TextIO,
    err: TextIO,
    runner: Runner = run,
    driver: Driver = drive_menu,
) -> int:
    """Run one subcommand; returns the process exit code (``DRIFT_EXIT_CODE`` on drift)."""
    args = _parser().parse_args(argv, namespace=_Args())
    try:
        if args.command == "refresh":
            return _refresh(args, out, runner, driver)
        return _check(args, out, runner)
    except (JobError, OSError) as error:
        _ = err.write(f"backlog_instructions: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], out=sys.stdout, err=sys.stderr))
