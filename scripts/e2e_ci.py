"""Helpers behind the e2e CI job (PLMCP-39, decision-22): here, not in shell, to be tested.

The `e2e` check is required, so it must always report. The workflow splits it in three:

* ``scope`` (job `e2e-scope`): reads the NUL-separated paths a pull request changes and prints
  ``run=true`` or ``run=false``: false only when every path is documentation or project
  management.
* The shards (job `e2e-shard`): `just e2e-shard K/N` runs the part of the live suite that
  ``assign_shards`` gives shard K; the pytest option ``--e2e-shard K/N`` (tests/conftest.py)
  applies it.
* ``verdict`` (job `e2e`): fails unless every shard succeeded, or the scope said skip and the
  shards were skipped. A skipped required check counts as passing on GitHub, so this job must
  never be skipped itself and never treat a skipped, cancelled or failed shard as green.
"""

import argparse
import re
import sys
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The number of parallel e2e jobs. The workflow's matrix lists the same shards (a test checks it).
SHARDS = 3

# Measured seconds per module of the live suite (`pytest -m e2e --durations`, PLMCP-39), the weight
# the split balances. A module not listed counts as the average of the listed ones, so a new module
# lands in some shard without anyone editing this table; refresh the numbers when the balance
# drifts. Keys are paths relative to the repository root.
MODULE_SECONDS: dict[str, float] = {}

# Non-code: a change touching only these cannot alter what the live suite tests. Everything else,
# including the workflow, the justfile, compose files, lock files, scripts, tests and the
# vendored references (the adapter tests validate against docs/vendor), is code.
_NON_CODE_PREFIXES = ("backlog/", "docs/", ".github/ISSUE_TEMPLATE/")
_CODE_PREFIXES = ("docs/vendor/",)


def parse_paths(raw: str) -> list[str]:
    """The paths of ``git diff --name-only -z`` output (NUL-separated, so no quoting)."""
    return [path for path in raw.split("\0") if path]


def _is_non_code(path: str) -> bool:
    if ".." in path.split("/") or path.startswith("/"):
        return False
    if path.startswith(_CODE_PREFIXES):
        return False
    if path.startswith(_NON_CODE_PREFIXES):
        return True
    # Top-level Markdown (README, CLAUDE.md, AGENTS.md); Markdown below src/ is runtime data.
    return "/" not in path and path.endswith(".md")


def needs_e2e(paths: Sequence[str]) -> bool:
    """Whether the live suite must run: any path that is not plainly non-code, or no path at all."""
    return not paths or not all(_is_non_code(path) for path in paths)


def verdict(*, scope: str, run: str, shards: str) -> str | None:
    """None when the `e2e` check may pass, else why not (the results are GitHub's `needs`)."""
    if scope != "success":
        return f"the scope job ended {scope or 'with no result'}, not success"
    if run == "true":
        if shards == "success":
            return None
        return f"the shards ended {shards or 'with no result'}, not success"
    if run == "false":
        if shards == "skipped":
            return None
        return f"the scope said skip but the shards ended {shards or 'with no result'}"
    return f"the scope job gave no usable decision (run={run!r})"


def parse_shard(spec: str) -> tuple[int, int]:
    """``K/N`` as (K, N) with 1 <= K <= N."""
    index, _, count = spec.partition("/")
    if not (index.isdigit() and count.isdigit() and 1 <= int(index) <= int(count)):
        message = f"shard must be K/N with 1 <= K <= N, got {spec!r}"
        raise ValueError(message)
    return int(index), int(count)


def assign_shards(
    modules: Sequence[str], *, count: int, weights: Mapping[str, float] = MODULE_SECONDS
) -> dict[str, int]:
    """Give every module a shard 1..count: heaviest first onto the lightest shard.

    Deterministic and independent of the order of `modules`. Unweighted modules count as the
    average weight of the weighted ones (1.0 when none is).
    """
    known = [weights[module] for module in modules if module in weights]
    default = sum(known) / len(known) if known else 1.0
    ordered = sorted(set(modules), key=lambda module: (-weights.get(module, default), module))
    loads = [0.0] * count
    plan: dict[str, int] = {}
    for module in ordered:
        lightest = loads.index(min(loads))
        loads[lightest] += weights.get(module, default)
        plan[module] = lightest + 1
    return plan


_JOBS = re.compile(r"^jobs:[ \t]*$", re.MULTILINE)
_JOB = re.compile(r"^  ([\w-]+):[ \t]*$", re.MULTILINE)
_KEY = re.compile(r"^    ([\w-]+):[ \t]*(.*?)[ \t]*$", re.MULTILINE)
_SHARD_LIST = re.compile(r"^\s+shard:\s*\[(.*)\]\s*$", re.MULTILINE)


def _jobs(workflow: str) -> dict[str, str]:
    """The text of each job of a workflow, by id (jobs are the two-space-indented keys)."""
    section = _JOBS.search(workflow)
    body = workflow[section.end() :] if section else ""
    marks = list(_JOB.finditer(body))
    ends = [mark.start() for mark in marks[1:]] + [len(body)]
    return {mark[1]: body[mark.end() : end] for mark, end in zip(marks, ends, strict=False)}


def _needs(value: str) -> set[str]:
    return {part.strip() for part in value.strip("[]").split(",") if part.strip()}


def _keys(job: str) -> dict[str, str]:
    return {key[1]: key[2] for key in _KEY.finditer(job)}


def _aggregate_problems(job: str) -> list[str]:
    keys = _keys(job)
    problems: list[str] = []
    if keys.get("if") != "always()":
        problems.append("`e2e` must run `if: always()`, or a failed shard would skip it")
    if _needs(keys.get("needs", "")) != {"e2e-scope", "e2e-shard"}:
        problems.append("`e2e` must have needs: [e2e-scope, e2e-shard]")
    if "name" in keys:
        problems.append("`e2e` must not set a name: the required check is called `e2e`")
    return problems


def _shard_problems(job: str, shards: int) -> list[str]:
    keys = _keys(job)
    problems: list[str] = []
    if "e2e-scope" not in _needs(keys.get("needs", "")):
        problems.append("`e2e-shard` needs e2e-scope, whose output decides whether it runs")
    if "outputs.run == 'true'" not in keys.get("if", ""):
        problems.append("`e2e-shard` must run only `if` the scope's run == 'true'")
    listed = _SHARD_LIST.search(job)
    found = [item.strip().strip("\"'") for item in listed[1].split(",")] if listed else []
    if found != [f"{n}/{shards}" for n in range(1, shards + 1)]:
        problems.append(f"the matrix of `e2e-shard` must list 1/{shards} to {shards}/{shards}")
    return problems


def workflow_problems(workflow: str, *, shards: int) -> list[str]:
    """Ways the e2e jobs of `workflow` could report green with a shard failed or never run."""
    jobs = _jobs(workflow)
    problems = [f"no job `{name}`" for name in ("e2e", "e2e-shard") if name not in jobs]
    problems += [
        f"`{name}` sets continue-on-error: a failure would not fail the check"
        for name in ("e2e", "e2e-shard")
        if "continue-on-error" in jobs.get(name, "")
    ]
    if "e2e" in jobs:
        problems += _aggregate_problems(jobs["e2e"])
    if "e2e-shard" in jobs:
        problems += _shard_problems(jobs["e2e-shard"], shards)
    return problems


class _Args(argparse.Namespace):
    """The parsed command line, typed."""

    command: str = ""
    scope: str = ""
    run: str = ""
    shards: str = ""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    _ = commands.add_parser("scope", help="run=true|false from the changed paths on stdin")
    check = commands.add_parser("verdict", help="fail unless the shards succeeded or were skipped")
    _ = check.add_argument("--scope", required=True, help="result of the scope job")
    _ = check.add_argument("--run", required=True, help="output `run` of the scope job")
    _ = check.add_argument("--shards", required=True, help="result of the shard jobs")
    return parser


def main(argv: Sequence[str], *, stdin: TextIO, out: TextIO, err: TextIO) -> int:
    """Run one subcommand; returns the process exit code."""
    args = _parser().parse_args(argv, namespace=_Args())
    if args.command == "scope":
        _ = out.write(f"run={str(needs_e2e(parse_paths(stdin.read()))).lower()}\n")
        return 0
    reason = verdict(scope=args.scope, run=args.run, shards=args.shards)
    if reason:
        _ = err.write(f"e2e_ci: {reason}\n")
        return 1
    done = (
        "every shard succeeded"
        if args.run == "true"
        else "skipped, the change touches no runtime file"
    )
    _ = out.write(f"e2e: {done}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], stdin=sys.stdin, out=sys.stdout, err=sys.stderr))
