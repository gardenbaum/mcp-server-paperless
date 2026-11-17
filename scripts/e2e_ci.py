"""Helpers behind the e2e CI check (PLMCP-39, decision-23): here, not in shell, to be tested.

Two workflows run the live suite. On a pull request the `e2e` job (ci.yml, not a required check:
decision-24) runs a fast tier chosen from the changed paths; after a merge and nightly,
`e2e-full.yml` runs everything in parallel shards.

* ``scope``: reads the NUL-separated changed paths on stdin and prints ``mode=skip|fast`` and
  ``targets=<pytest paths>``. Skip only when every path is documentation or project management.
  Otherwise the tier is the stdio smoke test plus the live contract and e2e modules of the
  toolsets the change touches; a change outside the toolset map adds a small smoke set instead of
  the whole suite, which the full run covers after the merge.
* The shards: `just e2e-shard K/N` runs the part of the live suite that ``assign_shards`` gives
  shard K; the pytest option ``--e2e-shard K/N`` (tests/conftest.py) applies it.
* ``workflow_problems`` guards the wiring and the hang guards (timeouts) of both workflows.
"""

import argparse
import re
import sys
from collections.abc import Collection, Mapping, Sequence  # noqa: TC003 -- the CI jobs run this file on the runner's python 3.12, which evaluates annotations
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast

# The number of parallel jobs of the full run. Its matrix lists the same shards (a test checks it).
SHARDS = 3

# Measured seconds per module of the live suite on a 4-core sandbox (`pytest -m e2e --junitxml`,
# 629 tests in 1163 s, PLMCP-39), the weight the split balances; a hosted runner needs about 1.75
# times as long. A module not listed counts as the average of the listed ones, so a new module
# lands in some shard without anyone editing this table; refresh the numbers when the balance
# drifts (a test checks the split of the current table). Keys are paths relative to the repository.
MODULE_SECONDS: dict[str, float] = {
    "tests/contract/test_bulk.py": 136,
    "tests/contract/test_pdf.py": 114,
    "tests/e2e/test_pdf.py": 77,
    "tests/contract/test_sharing.py": 70,
    "tests/contract/test_document_upload.py": 61,
    "tests/contract/test_document_writer.py": 58,
    "tests/contract/test_document_versions.py": 50,
    "tests/contract/test_admin.py": 44,
    "tests/contract/test_taxonomy_edits.py": 42,
    "tests/e2e/test_admin.py": 40,
    "tests/e2e/test_bulk.py": 39,
    "tests/e2e/test_update_document.py": 38,
    "tests/e2e/test_versions.py": 34,
    "tests/e2e/test_permissions.py": 33,
    "tests/contract/test_document_reader.py": 28,
    "tests/e2e/test_upload_document.py": 25,
    "tests/contract/test_notes_and_trash.py": 24,
    "tests/contract/test_permissions.py": 21,
    "tests/e2e/test_raw.py": 21,
    "tests/e2e/test_files.py": 18,
    "tests/e2e/test_mail.py": 16,
    "tests/e2e/test_taxonomy_edits.py": 16,
    "tests/e2e/test_notes_and_trash.py": 16,
    "tests/contract/test_trash.py": 14,
    "tests/e2e/test_stdio.py": 14,
    "tests/e2e/test_sharing.py": 13,
    "tests/contract/test_ai_reader.py": 9,
    "tests/contract/test_workflows.py": 9,
    "tests/e2e/test_documents.py": 8,
    "tests/contract/test_mail.py": 8,
    "tests/contract/test_views.py": 8,
    "tests/e2e/test_trash.py": 7,
    "tests/e2e/test_http.py": 7,
    "tests/contract/test_task_reader.py": 7,
    "tests/e2e/test_search.py": 6,
    "tests/e2e/test_views.py": 5,
    "tests/e2e/test_workflows.py": 5,
    "tests/contract/test_view_filters.py": 4,
    "tests/e2e/test_tasks.py": 4,
    "tests/contract/test_instance_reader.py": 3,
    "tests/contract/test_taxonomy_reader.py": 2,
    "tests/e2e/test_ai.py": 2,
    "tests/e2e/test_stack.py": 2,
    "tests/contract/test_raw.py": 2,
    "tests/contract/test_taxonomy_writer.py": 2,
    "tests/e2e/test_client.py": 0,
}

# The hang guards of the two workflows in minutes (a test reads the real files): the fast tier is
# expected in a few minutes, a shard of the full run in about 15 on a hosted runner. They bound a
# stuck run; they are no quality gate and must not creep up again to hide a slow suite.
PR_TIMEOUT_MINUTES = 20
SHARD_TIMEOUT_MINUTES = 25

# What every pull request that changes code runs: the stdio smoke test.
STDIO = "tests/e2e/test_stdio.py"
# What a change outside the toolset map (shared code, helpers, build files) adds to the tier: the
# stdio test and a read-only sample across the adapter, about 30 s together. The full suite
# follows after the merge.
SMOKE_TARGETS = frozenset(
    {
        STDIO,
        "tests/e2e/test_documents.py",
        "tests/contract/test_instance_reader.py",
        "tests/contract/test_task_reader.py",
        "tests/contract/test_taxonomy_reader.py",
    }
)

# The live modules (test names without folder) of a source or test stem, besides the `test_<stem>`
# modules of its own name in tests/e2e and tests/contract. A stem with none of either is outside
# the map and adds the smoke set, so a new toolset is never skipped by accident.
ALIASES: dict[str, tuple[str, ...]] = {
    "documents": (
        "test_document_reader",
        "test_document_writer",
        "test_files",
        "test_notes_and_trash",
        "test_search",
        "test_update_document",
    ),
    "document_edits": ("test_document_writer", "test_update_document"),
    "custom_values": ("test_document_writer", "test_update_document"),
    "files": ("test_files",),
    "notes": ("test_notes_and_trash",),
    "removal": ("test_notes_and_trash",),
    "uploads": ("test_document_upload", "test_upload_document"),
    "upload": ("test_document_upload", "test_upload_document"),
    "upload_source": ("test_document_upload", "test_upload_document"),
    "upload_types": ("test_document_upload", "test_upload_document"),
    "upload_stdio": ("test_document_upload", "test_upload_document"),
    "version_uploads": ("test_document_versions", "test_versions"),
    "versions": ("test_document_versions",),
    "versions_stdio": ("test_document_versions", "test_versions"),
    "views": ("test_search", "test_view_filters"),
    "filter_rules": ("test_search", "test_view_filters", "test_views"),
    "bulk_documents": ("test_bulk",),
    "bulk_taxonomy": ("test_bulk",),
    "selection": ("test_bulk",),
    "page_ranges": ("test_pdf",),
    "task_outcome": ("test_tasks", "test_task_reader"),
    "tasks": ("test_task_reader",),
    "tag_hierarchy": ("test_taxonomy_edits",),
    "taxonomy": ("test_taxonomy_edits", "test_taxonomy_reader", "test_taxonomy_writer"),
    "taxonomy_edits": ("test_taxonomy_edits",),
    "instance": ("test_instance_reader",),
    "ai": ("test_ai_reader",),
    "principals": ("test_admin", "test_permissions"),
    "admin_accounts": ("test_admin", "test_permissions"),
    "admin_system": ("test_admin",),
}

# Shared plumbing every toolset goes through: a change runs the whole suite even where a stem
# happens to name an e2e module (paperless/client.py and tests/e2e/test_client.py).
_SHARED_STEMS = frozenset({"client", "auth", "errors", "pagination", "disposition"})

# Non-code: a change touching only these cannot alter what the live suite tests. Everything else,
# including the workflow, the justfile, compose files, lock files, scripts, tests and the
# vendored references (the adapter tests validate against docs/vendor), is code.
_NON_CODE_PREFIXES = ("backlog/", "docs/", ".github/ISSUE_TEMPLATE/")
_CODE_PREFIXES = ("docs/vendor/",)
_SOURCE = re.compile(r"src/paperless_mcp/(?:tools|services|paperless)/([a-z][a-z0-9_]*)\.py")
_UNIT_TEST = re.compile(r"tests/(?:adapter|services|tools)/test_([a-z][a-z0-9_]*)\.py")
_LIVE_TEST = re.compile(r"tests/(?:e2e|contract)/test_[a-z][a-z0-9_]*\.py")


@dataclass(frozen=True)
class Selection:
    """What the live tests of a change are: nothing (skip), or the pytest paths to run."""

    skip: bool
    targets: tuple[str, ...]


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


def _modules_of(stem: str, live: Collection[str]) -> frozenset[str]:
    """The live modules a toolset stem stands for; empty when the stem maps to none."""
    names = {f"test_{stem}", *ALIASES.get(stem, ())}
    return frozenset(module for module in live if Path(module).stem in names)


def _modules_for(path: str, live: Collection[str]) -> frozenset[str] | None:
    """The live modules `path` can affect, None when the path is outside the toolset map."""
    for pattern in (_SOURCE, _UNIT_TEST):
        found = pattern.fullmatch(path)
        if found:
            return None if found[1] in _SHARED_STEMS else _modules_of(found[1], live) or None
    if _LIVE_TEST.fullmatch(path):
        # A module that is gone (not in `live`) cannot be run: its toolset is unknown.
        return frozenset({path}) if path in live else None
    return None


def select(paths: Sequence[str], *, live_modules: Collection[str]) -> Selection:
    """The live tests a pull request changing `paths` must run.

    `live_modules` are the paths of the live test modules (tests/e2e and tests/contract). Skips
    only when every path is plainly non-code. A path outside the toolset map, or no path at all,
    adds the smoke set; no change is ever matched by a guess, and none runs the whole suite.
    """
    if not paths:
        return Selection(skip=False, targets=_ordered(SMOKE_TARGETS))
    code = [path for path in paths if not _is_non_code(path)]
    if not code:
        return Selection(skip=True, targets=())
    modules: set[str] = {STDIO}
    for path in code:
        found = _modules_for(path, live_modules)
        modules.update(SMOKE_TARGETS if found is None else found)
    return Selection(skip=False, targets=_ordered(modules))


def _ordered(modules: Collection[str]) -> tuple[str, ...]:
    """The stdio test first (it is the cheapest to fail), then the rest sorted."""
    return (STDIO, *sorted(set(modules) - {STDIO}))


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
_STEP_IF = re.compile(r"^(?:      - |        )if:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
_SHARD_LIST = re.compile(r"^\s+shard:\s*\[(.*)\]\s*$", re.MULTILINE)
_ON_PULL_REQUEST = "github.event_name == 'pull_request'"
_NOT_SKIPPED = "steps.scope.outputs.mode != 'skip'"


def _jobs(workflow: str) -> dict[str, str]:
    """The text of each job of a workflow, by id (jobs are the two-space-indented keys)."""
    section = _JOBS.search(workflow)
    body = workflow[section.end() :] if section else ""
    marks = list(_JOB.finditer(body))
    ends = [mark.start() for mark in marks[1:]] + [len(body)]
    return {mark[1]: body[mark.end() : end] for mark, end in zip(marks, ends, strict=False)}


def _keys(job: str) -> dict[str, str]:
    return {key[1]: key[2] for key in _KEY.finditer(job)}


def _timeout_problems(name: str, keys: Mapping[str, str], limit: int) -> list[str]:
    value = keys.get("timeout-minutes", "")
    if not value.isdigit() or not 0 < int(value) <= limit:
        return [f"`{name}` needs `timeout-minutes` of at most {limit}, it has {value or 'none'}"]
    return []


def _pull_request_problems(workflow: str) -> list[str]:
    job = _jobs(workflow).get("e2e")
    if job is None:
        return ["no job `e2e` in ci.yml: the pull request would get no live signal"]
    keys = _keys(job)
    problems: list[str] = []
    if keys.get("if", _ON_PULL_REQUEST) != _ON_PULL_REQUEST:
        problems.append(f"`e2e` may have no `if` but `{_ON_PULL_REQUEST}`: a skipped job passes")
    if "name" in keys:
        problems.append("`e2e` must not set a name: the check is called `e2e` (decision-23)")
    if "continue-on-error" in job:
        problems.append("`e2e` sets continue-on-error: a failure would not fail the check")
    if "id: scope" not in job or "e2e_ci.py scope" not in job:
        problems.append("`e2e` needs the scope step (`id: scope`, scripts/e2e_ci.py scope)")
    if "just e2e-paths" not in job:
        problems.append("`e2e` must run the tier with `just e2e-paths`")
    problems += _timeout_problems("e2e", keys, PR_TIMEOUT_MINUTES)
    problems += [
        f"a step of `e2e` has `if: {condition}`; only `{_NOT_SKIPPED}` may skip a step on mode"
        for condition in cast("list[str]", _STEP_IF.findall(job))
        if condition not in {_NOT_SKIPPED, f"failure() && {_NOT_SKIPPED}"}
    ]
    return problems


def _full_run_problems(workflow: str, shards: int) -> list[str]:
    job = _jobs(workflow).get("e2e-shard")
    if job is None:
        return ["no job `e2e-shard` in e2e-full.yml"]
    problems: list[str] = []
    if "continue-on-error" in job:
        problems.append("`e2e-shard` sets continue-on-error: a red shard would not fail the run")
    problems += _timeout_problems("e2e-shard", _keys(job), SHARD_TIMEOUT_MINUTES)
    if "just e2e-shard" not in job:
        problems.append("`e2e-shard` must run `just e2e-shard`")
    listed = _SHARD_LIST.search(job)
    found = [item.strip().strip("\"'") for item in listed[1].split(",")] if listed else []
    if found != [f"{n}/{shards}" for n in range(1, shards + 1)]:
        problems.append(f"the matrix of `e2e-shard` must list 1/{shards} to {shards}/{shards}")
    if not re.search(r"^  push:\s*\n\s+branches:\s*\[main\]", workflow, re.MULTILINE):
        problems.append("e2e-full.yml must run on every push to main")
    if not re.search(r"^  schedule:", workflow, re.MULTILINE):
        problems.append("e2e-full.yml must run on a schedule")
    return problems


def workflow_problems(ci: str, full: str, *, shards: int) -> list[str]:
    """Ways the e2e wiring of ci.yml and e2e-full.yml could skip live tests unseen."""
    return _pull_request_problems(ci) + _full_run_problems(full, shards)


class _Args(argparse.Namespace):
    """The parsed command line, typed."""

    command: str = ""
    root: Path = Path()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scope = commands.add_parser("scope", help="mode and pytest targets from the paths on stdin")
    _ = scope.add_argument("--root", type=Path, default=Path(), help="the repository root")
    return parser


def main(argv: Sequence[str], *, stdin: TextIO, out: TextIO, err: TextIO) -> int:
    """Print the job outputs of the scope; returns the process exit code."""
    _ = err  # the signature of every script main
    args = _parser().parse_args(argv, namespace=_Args())
    live = {
        path.relative_to(args.root).as_posix()
        for folder in ("e2e", "contract")
        for path in (args.root / "tests" / folder).glob("test_*.py")
    }
    selection = select(parse_paths(stdin.read()), live_modules=live)
    _ = out.write(f"mode={'skip' if selection.skip else 'fast'}\n")
    _ = out.write(f"targets={' '.join(selection.targets)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], stdin=sys.stdin, out=sys.stdout, err=sys.stderr))
