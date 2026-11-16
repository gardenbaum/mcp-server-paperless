"""Helpers behind the e2e CI check (PLMCP-39, decision-23): here, not in shell, to be tested.

Two workflows run the live suite. On a pull request the required `e2e` job (ci.yml) runs a fast
tier chosen from the changed paths; after a merge and nightly, `e2e-full.yml` runs everything in
parallel shards.

* ``scope``: reads the NUL-separated changed paths on stdin and prints ``mode=skip|fast`` and
  ``targets=<pytest paths>``. Skip only when every path is documentation or project management.
  Otherwise the tier is the live contract suite, the stdio smoke test and the e2e modules of the
  toolsets the change touches; a change outside the toolset map runs the whole suite.
* The shards: `just e2e-shard K/N` runs the part of the live suite that ``assign_shards`` gives
  shard K; the pytest option ``--e2e-shard K/N`` (tests/conftest.py) applies it.
* ``workflow_problems`` guards the wiring of both workflows.
"""

import argparse
import re
import sys
from collections.abc import Collection, Mapping, Sequence  # noqa: TC003 -- the CI jobs run this file on the runner's python 3.12, which evaluates annotations
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

# The number of parallel jobs of the full run. Its matrix lists the same shards (a test checks it).
SHARDS = 3

# Measured seconds per module of the live suite (`pytest -m e2e --durations`, PLMCP-39), the weight
# the split balances. A module not listed counts as the average of the listed ones, so a new module
# lands in some shard without anyone editing this table; refresh the numbers when the balance
# drifts. Keys are paths relative to the repository root.
MODULE_SECONDS: dict[str, float] = {}

# What every pull request runs besides the modules of the toolsets it touches: the live contract
# suite (it guards the adapter and the fake against real Paperless) and these e2e modules.
BASE_TARGETS = ("tests/contract",)
BASE_E2E = frozenset({"test_stdio"})
ALL_TARGETS = ("tests/contract", "tests/e2e")

# The e2e modules of a source or test stem that has no `test_<stem>` module of its own. A stem with
# neither runs the whole suite, so a new toolset is never skipped by accident.
ALIASES: dict[str, tuple[str, ...]] = {
    "documents": (
        "test_documents",
        "test_files",
        "test_notes_and_trash",
        "test_search",
        "test_update_document",
    ),
    "document_edits": ("test_update_document",),
    "custom_values": ("test_update_document",),
    "files": ("test_files",),
    "notes": ("test_notes_and_trash",),
    "removal": ("test_notes_and_trash",),
    "uploads": ("test_upload_document",),
    "upload": ("test_upload_document",),
    "upload_source": ("test_upload_document",),
    "upload_types": ("test_upload_document",),
    "upload_stdio": ("test_upload_document",),
    "version_uploads": ("test_versions",),
    "versions_stdio": ("test_versions",),
    "views": ("test_views", "test_search"),
    "filter_rules": ("test_search", "test_views"),
    "bulk_documents": ("test_bulk",),
    "bulk_taxonomy": ("test_bulk",),
    "selection": ("test_bulk",),
    "page_ranges": ("test_pdf",),
    "task_outcome": ("test_tasks",),
    "tag_hierarchy": ("test_taxonomy_edits",),
    "taxonomy_edits": ("test_taxonomy_edits",),
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
_E2E_TEST = re.compile(r"tests/e2e/(test_[a-z][a-z0-9_]*)\.py")
_CONTRACT_TEST = re.compile(r"tests/contract/[\w/]+\.py")


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


def _modules_of(stem: str, e2e_modules: Collection[str]) -> tuple[str, ...]:
    """The e2e modules a toolset stem stands for; empty when the stem maps to none."""
    if f"test_{stem}" in e2e_modules:
        return (f"test_{stem}",)
    return tuple(module for module in ALIASES.get(stem, ()) if module in e2e_modules)


def _modules_for(path: str, e2e_modules: Collection[str]) -> tuple[str, ...] | None:
    """The e2e modules `path` can affect, () when only the base tier is, None when unknown."""
    for pattern in (_SOURCE, _UNIT_TEST):
        found = pattern.fullmatch(path)
        if found:
            if found[1] in _SHARED_STEMS:
                return None
            return _modules_of(found[1], e2e_modules) or None
    found = _E2E_TEST.fullmatch(path)
    if found:
        # A module that is gone (not in e2e_modules) cannot be run: its toolset is unknown.
        module = found[1]
        if module in BASE_E2E:
            return ()
        return (module,) if module in e2e_modules else None
    if _CONTRACT_TEST.fullmatch(path):
        return ()
    return None


def select(paths: Sequence[str], *, e2e_modules: Collection[str]) -> Selection:
    """The live tests a pull request changing `paths` must run.

    Skips only when every path is plainly non-code. A path outside the toolset map, or no path at
    all, selects the whole suite; no change is ever matched by a guess.
    """
    if not paths:
        return Selection(skip=False, targets=ALL_TARGETS)
    code = [path for path in paths if not _is_non_code(path)]
    if not code:
        return Selection(skip=True, targets=())
    modules: set[str] = set()
    for path in code:
        found = _modules_for(path, e2e_modules)
        if found is None:
            return Selection(skip=False, targets=ALL_TARGETS)
        modules.update(found)
    extra = sorted(modules - BASE_E2E)
    return Selection(
        skip=False,
        targets=(
            *BASE_TARGETS,
            *(f"tests/e2e/{module}.py" for module in sorted(BASE_E2E)),
            *(f"tests/e2e/{module}.py" for module in extra),
        ),
    )


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


def _pull_request_problems(workflow: str) -> list[str]:
    job = _jobs(workflow).get("e2e")
    if job is None:
        return ["no job `e2e` in ci.yml: the required check would never report"]
    keys = _keys(job)
    problems: list[str] = []
    if keys.get("if", _ON_PULL_REQUEST) != _ON_PULL_REQUEST:
        problems.append(f"`e2e` may have no `if` but `{_ON_PULL_REQUEST}`: a skipped job passes")
    if "name" in keys:
        problems.append("`e2e` must not set a name: the required check is called `e2e`")
    if "continue-on-error" in job:
        problems.append("`e2e` sets continue-on-error: a failure would not fail the check")
    if "id: scope" not in job or "e2e_ci.py scope" not in job:
        problems.append("`e2e` needs the scope step (`id: scope`, scripts/e2e_ci.py scope)")
    if "just e2e-paths" not in job:
        problems.append("`e2e` must run the tier with `just e2e-paths`")
    problems += [
        f"a step of `e2e` has `if: {condition}`; only `{_NOT_SKIPPED}` may skip a step on mode"
        for condition in _STEP_IF.findall(job)
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
    e2e_dir: Path = Path("tests/e2e")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scope = commands.add_parser("scope", help="mode and pytest targets from the paths on stdin")
    _ = scope.add_argument("--e2e-dir", type=Path, default=Path("tests/e2e"))
    return parser


def main(argv: Sequence[str], *, stdin: TextIO, out: TextIO, err: TextIO) -> int:  # noqa: ARG001 -- the signature of every script main
    """Print the job outputs of the scope; returns the process exit code."""
    args = _parser().parse_args(argv, namespace=_Args())
    modules = {path.stem for path in args.e2e_dir.glob("test_*.py")}
    selection = select(parse_paths(stdin.read()), e2e_modules=modules)
    _ = out.write(f"mode={'skip' if selection.skip else 'fast'}\n")
    _ = out.write(f"targets={' '.join(selection.targets)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], stdin=sys.stdin, out=sys.stdout, err=sys.stderr))
