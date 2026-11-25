"""Helpers behind the e2e CI check (PLMCP-39, decision-30): here, not in shell, to be tested.

Three workflows run the live suite. On a pull request the `e2e` job (ci.yml, not a required check:
decision-24) runs a fast tier chosen from the changed paths; after a merge and nightly,
`e2e-full.yml` runs everything in parallel shards; weekly, `e2e-latest.yml` runs the same shards
against the latest paperless-ngx 3.x, resolved once by its `resolve` job (PLMCP-45).

* ``scope``: reads the NUL-separated changed paths on stdin and prints ``mode=skip|fast`` and
  ``targets=<pytest paths>``. Skip only when every path is documentation or project management.
  Otherwise the tier is the stdio smoke test plus the live contract and e2e modules of the
  toolsets the change touches; a change outside the toolset map adds a small smoke set instead of
  the whole suite, which the full run covers after the merge.
* The shards: `just e2e-shard K/N` runs the part of the live suite that ``assign_shards`` gives
  shard K; the pytest option ``--e2e-shard K/N`` (tests/conftest.py) applies it.
* ``workflow_problems`` guards the wiring and the hang guards (timeouts) of the three workflows.
"""

import argparse
import math
import re
import sys
from collections.abc import Collection, Mapping, Sequence  # noqa: TC003 -- the CI jobs run this file on the runner's python 3.12, which evaluates annotations
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast

# The number of parallel jobs of the full run. Its matrix lists the same shards (a test checks it).
SHARDS = 6

# Seconds per module on a hosted runner (ubuntu-latest, 4 vCPUs): the median of four e2e-full
# runs of 2026-11-24 (PLMCP-115, decision-76): 45460920436, 45467934032, 45467975266, 45475116464.
# A module's time is the gap of the Actions timestamps of the ends of its pytest progress lines,
# 30 s of stack seeding taken off the first module of a shard. The Celery-bound modules (bulk,
# writer, pdf, sharing, versions, duplicates) vary 1.5 to 2x between runs (the median ignores one
# fast or slow runner).
# test_*_steps, test_bulk_limits and test_left_out_operations: one run of 2026-11-25, 45656414763.
# The weight the split balances; a module not listed counts as the average of the listed ones, so a
# new module lands in some shard without anyone editing this table; refresh the numbers when the
# balance drifts (a test checks the split of the current table against the measured seconds). Keys
# are paths relative to the repository. test_trash_order and test_rejected_duplicates are skipped in
# the shards (they run in the jobs e2e-postgres and e2e-duplicates).
MODULE_SECONDS: dict[str, float] = {
    "tests/contract/test_pdf.py": 440,
    "tests/contract/test_bulk.py": 340,
    "tests/e2e/test_pdf.py": 259,
    "tests/e2e/test_pages_files.py": 216,
    "tests/contract/test_document_versions.py": 199,
    "tests/contract/test_sharing.py": 192,
    "tests/contract/test_duplicates.py": 185,
    "tests/e2e/test_duplicates.py": 158,
    "tests/e2e/test_bulk_items.py": 144,
    "tests/contract/test_document_writer.py": 129,
    "tests/e2e/test_trash.py": 119,
    "tests/e2e/test_bulk.py": 113,
    "tests/e2e/test_confirmation.py": 113,
    "tests/e2e/test_pdf_insert.py": 113,
    "tests/e2e/test_pages.py": 109,
    "tests/contract/test_pdf_steps.py": 106,
    "tests/e2e/test_versions.py": 102,
    "tests/contract/test_bulk_limits.py": 90,
    "tests/contract/test_bulk_items.py": 83,
    "tests/contract/test_document_reader.py": 72,
    "tests/contract/test_trash.py": 67,
    "tests/contract/test_admin.py": 64,
    "tests/e2e/test_pdf_steps.py": 62,
    "tests/e2e/test_text_edits.py": 61,
    "tests/contract/test_document_upload.py": 60,
    "tests/e2e/test_upload_duplicates.py": 60,
    "tests/contract/test_mail_steps.py": 58,
    "tests/e2e/test_update_document.py": 58,
    "tests/e2e/test_toolset_guide.py": 54,
    "tests/e2e/test_bulk_limits.py": 52,
    "tests/contract/test_notes_and_trash.py": 50,
    "tests/contract/test_text_edits.py": 50,
    "tests/e2e/test_read_documents.py": 49,
    "tests/contract/test_pdf_insert.py": 47,
    "tests/e2e/test_permissions.py": 43,
    "tests/e2e/test_upload_batches.py": 43,
    "tests/contract/test_taxonomy_edits.py": 42,
    "tests/e2e/test_admin.py": 42,
    "tests/e2e/test_search.py": 40,
    "tests/e2e/test_downloads.py": 39,
    "tests/contract/test_permissions.py": 36,
    "tests/contract/test_instance_reader.py": 29,
    "tests/contract/test_batch_uploads.py": 27,
    "tests/e2e/test_upload_document.py": 25,
    "tests/e2e/test_raw.py": 21,
    "tests/e2e/test_taxonomy_edits.py": 17,
    "tests/e2e/test_files.py": 16,
    "tests/e2e/test_mail.py": 16,
    "tests/contract/test_selection.py": 15,
    "tests/e2e/test_notes_and_trash.py": 15,
    "tests/e2e/test_http.py": 14,
    "tests/e2e/test_stdio.py": 14,
    "tests/e2e/test_minimum_release.py": 13,
    "tests/contract/test_task_reader.py": 12,
    "tests/e2e/test_sharing.py": 11,
    "tests/contract/test_ai_reader.py": 10,
    "tests/e2e/test_documents.py": 8,
    "tests/contract/test_views.py": 7,
    "tests/contract/test_workflows.py": 7,
    "tests/contract/test_mail.py": 6,
    "tests/e2e/test_views.py": 6,
    "tests/e2e/test_workflows.py": 6,
    "tests/e2e/test_left_out_operations.py": 4,
    "tests/e2e/test_tasks.py": 4,
    "tests/contract/test_taxonomy_reader.py": 2,
    "tests/contract/test_view_filters.py": 2,
    "tests/e2e/test_ai.py": 2,
    "tests/e2e/test_stack.py": 2,
    "tests/contract/test_raw.py": 1,
    "tests/contract/test_taxonomy_writer.py": 1,
    "tests/e2e/test_client.py": 0,
    "tests/e2e/test_mail_import.py": 0,
    "tests/e2e/test_rejected_duplicates.py": 0,
    "tests/e2e/test_trash_order.py": 0,
}

# The hang guards of the two workflows in minutes (a test reads the real files): the fast tier is
# expected in a few minutes, a shard of the full run in about 15 on a hosted runner. They bound a
# stuck run; they are no quality gate and must not creep up again to hide a slow suite.
PR_TIMEOUT_MINUTES = 20
SHARD_TIMEOUT_MINUTES = 25

# A tier heavier than this (recorded seconds) is split over up to TIER_PARTS parallel jobs of the
# `e2e` matrix (a cross-cutting change would pass the 20-minute guard on a hosted runner). Weights
# and budget are hosted-runner seconds (decision-30); the number of shards and parts is
# decision-76's. The full run covers everything after the merge.
TIER_BUDGET_SECONDS = 450.0
TIER_PARTS = 6

# What every pull request that changes code runs: the stdio smoke test.
STDIO = "tests/e2e/test_stdio.py"
# What a change outside the toolset map (shared code, helpers, build files) adds to the tier: the
# stdio test, the stack's own seeding test and a read-only sample across the adapter, about 65 s.
# The full suite follows after the merge.
SMOKE_TARGETS = frozenset(
    {
        STDIO,
        "tests/e2e/test_documents.py",
        "tests/e2e/test_stack.py",
        "tests/contract/test_instance_reader.py",
        "tests/contract/test_task_reader.py",
        "tests/contract/test_taxonomy_reader.py",
    }
)

# The live modules (test names without folder) of a source or test stem, besides the `test_<stem>`
# modules of its own name in tests/e2e and tests/contract. A stem with none of either is outside
# the map and adds the smoke set, so a new toolset is never skipped by accident.
ALIASES: dict[str, tuple[str, ...]] = {
    # tools/core.py registers the everyday tools: documents, notes, files, search, tasks, taxonomy
    # and the instance overview (the live modules named after those subjects).
    "core": (
        "test_document_reader",
        "test_document_writer",
        "test_documents",
        "test_files",
        "test_instance_reader",
        "test_notes_and_trash",
        "test_pages",
        "test_pages_files",
        "test_search",
        "test_task_reader",
        "test_tasks",
        "test_taxonomy_edits",
        "test_taxonomy_reader",
        "test_taxonomy_writer",
        "test_toolset_guide",
        "test_update_document",
    ),
    # The entry points start the server the HTTP transport test talks to (decision-27).
    "app": ("test_http",),
    "cli": ("test_http",),
    # server.py composes the instructions and the guide to the toolsets (decision-41).
    "server": ("test_http", "test_toolset_guide"),
    "documents": (
        "test_document_reader",
        "test_document_writer",
        "test_files",
        "test_notes_and_trash",
        "test_pages",
        "test_search",
        "test_update_document",
    ),
    "document_edits": ("test_bulk_items", "test_document_writer", "test_update_document"),
    "custom_values": ("test_document_writer", "test_update_document"),
    "files": ("test_files",),
    "notes": ("test_bulk_items", "test_notes_and_trash"),
    "removal": ("test_notes_and_trash",),
    "uploads": ("test_document_upload", "test_upload_document"),
    "duplicate_resolution": ("test_duplicates",),
    "duplicates": (
        "test_batch_uploads",
        "test_rejected_duplicates",
        "test_trash_order",
        "test_upload_duplicates",
    ),
    "upload_hint": ("test_upload_duplicates",),
    "upload": ("test_document_upload", "test_upload_document"),
    "upload_source": ("test_document_upload", "test_upload_document"),
    "upload_types": ("test_document_upload", "test_upload_document"),
    "upload_listing": ("test_batch_uploads", "test_upload_batches"),
    "batch_uploads": ("test_batch_uploads", "test_upload_batches"),
    "bulk_stdio": ("test_batch_uploads", "test_upload_batches"),
    "upload_stdio": ("test_document_upload", "test_upload_document"),
    "version_uploads": ("test_document_versions", "test_versions"),
    "trash": ("test_duplicates",),
    "trash_scan": ("test_trash",),
    "trashed_documents": ("test_trash",),
    "versions": ("test_document_versions",),
    "versions_stdio": ("test_document_versions", "test_versions"),
    "views": ("test_search", "test_view_filters", "test_view_pins"),
    "ui_settings": ("test_view_pins",),
    "view_pins": ("test_ui_settings",),
    "filter_rules": ("test_search", "test_view_filters", "test_views"),
    "bulk_documents": ("test_bulk",),
    "bulk_operations": ("test_bulk",),
    "bulk_updates": ("test_bulk_items", "test_bulk_limits"),
    "bulk_notes": ("test_bulk_items", "test_bulk_limits"),
    "document_runner": ("test_bulk_items", "test_bulk_limits"),
    "bulk_taxonomy": ("test_bulk",),
    "workflows": (
        "test_run_workflow",
        "test_workflow_parts",
        "test_workflow_runs",
        "test_workflow_steps",
    ),
    "workflow_runs": ("test_run_workflow",),
    "workflow_parts": ("test_workflow_steps", "test_workflows"),
    "workflow_usage": ("test_workflow_steps", "test_workflows"),
    "workflow_explanation": ("test_workflow_steps", "test_workflows"),
    "run_workflow": ("test_workflow_runs",),
    # tools/selection.py selects toolsets; domain/ and services/selection.py choose documents.
    "selection": ("test_bulk", "test_read_documents", "test_toolset_guide"),
    "read_documents": ("test_bulk_items", "test_selection"),
    # tools/bulk.py also reads many documents, and changes them one by one.
    "bulk": ("test_bulk_items", "test_read_documents", "test_selection"),
    "page_ranges": ("test_pages", "test_pdf"),
    "pages": ("test_pages_files",),
    # inspect_pdf, insert_pages, and the steps of the composed operations.
    "pdf": ("test_pages", "test_pdf_insert", "test_pdf_steps"),
    "pdf_insert": ("test_pdf_steps",),
    "pdf_parts": ("test_pdf", "test_pdf_steps"),
    # The renderer (rendering/ and domain/rendering.py).
    "rendering": ("test_pages", "test_pages_files", "test_pdf_insert"),
    "fields": ("test_pages", "test_pages_files", "test_pdf_insert"),
    "worker": ("test_pages", "test_pages_files", "test_pdf_insert"),
    "runner": ("test_pages", "test_pages_files", "test_pdf_insert"),
    "wire": ("test_pages", "test_pages_files", "test_pdf_insert"),
    "limits": ("test_pages", "test_pages_files", "test_pdf_insert"),
    "task_outcome": ("test_tasks", "test_task_reader"),
    "tasks": ("test_task_lists", "test_task_reader"),
    "tag_hierarchy": ("test_taxonomy_edits",),
    "sharing": ("test_left_out_operations",),
    "taxonomy": (
        "test_left_out_operations",
        "test_taxonomy_edits",
        "test_taxonomy_reader",
        "test_taxonomy_writer",
    ),
    "taxonomy_edits": ("test_taxonomy_edits",),
    "instance": ("test_instance_reader", "test_minimum_release", "test_toolset_guide"),
    "ai": ("test_ai_reader",),
    "mail": ("test_mail_import", "test_mail_steps"),
    "mail_explanation": ("test_mail", "test_mail_import", "test_mail_steps"),
    "log_records": ("test_mail_import", "test_workflows"),
    "processed_mail": ("test_mail", "test_mail_import", "test_mail_steps"),
    "principals": ("test_admin", "test_permissions"),
    "admin_accounts": ("test_admin", "test_permissions"),
    "admin_system": ("test_admin",),
    "task_lists": ("test_rejected_duplicates",),
}

# Shared plumbing every toolset goes through: such a change runs the smoke set and the modules its
# stem names (paperless/client.py and tests/e2e/test_client.py), not one toolset alone.
_SHARED_STEMS = frozenset({"client", "auth", "errors", "pagination", "disposition"})
_ENTRY_STEMS = frozenset({"app", "cli", "server"})

# Non-code: a change touching only these cannot alter what the live suite tests. Everything else,
# including the workflow, the justfile, compose files, lock files, scripts, tests and the
# vendored references (the adapter tests validate against docs/vendor), is code.
_NON_CODE_PREFIXES = ("backlog/", "docs/", ".github/ISSUE_TEMPLATE/")
_CODE_PREFIXES = ("docs/vendor/",)
_SOURCE = re.compile(
    r"src/mcp_server_paperless/(?:(?:tools|services|paperless|domain|rendering)/)?([a-z][a-z0-9_]*)\.py"
)
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
            modules = _modules_of(found[1], live)
            if found[1] in _SHARED_STEMS or found[1] in _ENTRY_STEMS:
                return modules | SMOKE_TARGETS
            return modules or None
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


def split_tier(
    targets: Sequence[str],
    *,
    weights: Mapping[str, float] = MODULE_SECONDS,
    budget: float = TIER_BUDGET_SECONDS,
    parts: int = TIER_PARTS,
) -> list[tuple[str, ...]]:
    """The tier as one group, or as up to `parts` balanced groups when its weight passes `budget`.

    A change that touches many toolsets would pass the hang guard in one job. Splitting never drops
    a module: the groups together are exactly `targets`, the stdio test leads the first group.
    """
    known = [weights[target] for target in targets if target in weights]
    default = sum(known) / len(known) if known else 1.0
    total = sum(weights.get(target, default) for target in targets)
    count = min(parts, max(1, math.ceil(total / budget)))
    if count == 1:
        return [tuple(targets)]
    plan = assign_shards(targets, count=count, weights=weights)
    groups = [tuple(t for t in targets if plan[t] == n) for n in range(1, count + 1)]
    first = groups[plan[STDIO] - 1] if STDIO in plan else ()
    groups = [group for group in groups if group]
    if first and first != groups[0]:  # keep the stdio test in the group that runs first
        groups.sort(key=lambda group: group != first)
    return groups


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
_PART_LIST = re.compile(r"^\s+part:\s*\[(.*)\]\s*$", re.MULTILINE)
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
    if "name" in keys and not keys["name"].startswith("e2e"):
        problems.append("`e2e` must keep a name starting with `e2e` (decision-23)")
    if "continue-on-error" in job:
        problems.append("`e2e` sets continue-on-error: a failure would not fail the check")
    if "id: scope" not in job or "e2e_ci.py scope" not in job:
        problems.append("`e2e` needs the scope step (`id: scope`, scripts/e2e_ci.py scope)")
    listed = _PART_LIST.search(job)
    parts = [item.strip().strip("\"'") for item in listed[1].split(",")] if listed else []
    if parts != [f"{n}/{TIER_PARTS}" for n in range(1, TIER_PARTS + 1)]:
        problems.append(
            f"the matrix of `e2e` must list the parts 1/{TIER_PARTS} to {TIER_PARTS}/{TIER_PARTS}"
        )
    if "scope --part" not in job:
        problems.append("`e2e` must give the scope its matrix part (`scope --part`)")
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


_RESOLVED_VERSION = "PAPERLESS_E2E_VERSION: ${{ needs.resolve.outputs.version }}"


def _latest_trigger_problems(workflow: str) -> list[str]:
    problems: list[str] = []
    if "continue-on-error" in workflow:
        problems.append("e2e-latest.yml sets continue-on-error: a red shard would not fail the run")
    if not re.search(r"^  schedule:\s*\n\s+- cron:", workflow, re.MULTILINE):
        problems.append("e2e-latest.yml must run on a schedule")
    if not re.search(r"^  workflow_dispatch:", workflow, re.MULTILINE):
        problems.append("e2e-latest.yml must offer workflow_dispatch")
    return problems


def _latest_run_problems(workflow: str, shards: int) -> list[str]:
    jobs = _jobs(workflow)
    problems = _latest_trigger_problems(workflow)
    resolve = jobs.get("resolve")
    if resolve is None:
        problems.append("no job `resolve` in e2e-latest.yml: the shards would not share a version")
    else:
        problems += _timeout_problems("resolve", _keys(resolve), SHARD_TIMEOUT_MINUTES)
        if not re.search(r"^    outputs:\s*\n\s+version:", resolve, re.MULTILINE):
            problems.append("`resolve` must have the output `version`")
    job = jobs.get("e2e-latest")
    if job is None:
        return [*problems, "no job `e2e-latest` in e2e-latest.yml"]
    keys = _keys(job)
    if keys.get("needs") not in {"resolve", "[resolve]"}:
        problems.append("`e2e-latest` needs `resolve`: every shard must wait for the version")
    problems += _timeout_problems("e2e-latest", keys, SHARD_TIMEOUT_MINUTES)
    if not re.search(r"^      fail-fast:\s*false\s*$", job, re.MULTILINE):
        problems.append("`e2e-latest` needs `fail-fast: false`: every shard must report")
    if not re.search(r"just e2e-latest \"\$SHARD\"", job):
        problems.append('`e2e-latest` must run `just e2e-latest "$SHARD"`: a shard of the suite')
    if _RESOLVED_VERSION not in job:
        problems.append(f"`e2e-latest` must set `{_RESOLVED_VERSION}`: one version for all shards")
    listed = _SHARD_LIST.search(job)
    found = [item.strip().strip("\"'") for item in listed[1].split(",")] if listed else []
    if found != [f"{n}/{shards}" for n in range(1, shards + 1)]:
        problems.append(f"the matrix of `e2e-latest` must list 1/{shards} to {shards}/{shards}")
    return problems


def workflow_problems(ci: str, full: str, latest: str, *, shards: int) -> list[str]:
    """Ways the e2e wiring of the three workflows could skip live tests unseen."""
    return (
        _pull_request_problems(ci)
        + _full_run_problems(full, shards)
        + _latest_run_problems(latest, shards)
    )


class _Args(argparse.Namespace):
    """The parsed command line, typed."""

    command: str = ""
    root: Path = Path()
    part: tuple[int, int] | None = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scope = commands.add_parser("scope", help="mode and pytest targets from the paths on stdin")
    _ = scope.add_argument(
        "--part",
        type=parse_shard,
        default=None,
        metavar="K/N",
        help="print only group K of the N matrix parts (a heavy tier is split, see split_tier)",
    )
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
    targets = selection.targets
    skip = selection.skip
    if args.part is not None and not skip:
        index, _count = args.part
        groups = split_tier(targets)
        skip = index > len(groups)
        targets = () if skip else groups[index - 1]
        note = "no work for this part" if skip else f"{len(targets)} modules"
        _ = err.write(f"e2e part {index}/{_count} of {len(groups)} group(s): {note}\n")
        if len(groups) > 1 and index == 1:
            _ = err.write(f"the tier is over its budget and split in {len(groups)} groups\n")
    _ = out.write(f"mode={'skip' if skip else 'fast'}\n")
    _ = out.write(f"targets={' '.join(targets)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], stdin=sys.stdin, out=sys.stdout, err=sys.stderr))
