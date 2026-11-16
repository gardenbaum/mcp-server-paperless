"""Tests of the helpers behind the tiered e2e CI check and the sharded full run (PLMCP-39)."""

import io
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import e2e_ci
import pytest

ROOT = Path(__file__).resolve().parents[2]


E2E = frozenset(
    f"test_{name}"
    for name in [
        "ai",
        "bulk",
        "client",
        "documents",
        "files",
        "notes_and_trash",
        "pdf",
        "search",
        "sharing",
        "stack",
        "stdio",
        "tasks",
        "taxonomy_edits",
        "trash",
        "update_document",
        "upload_document",
        "versions",
        "views",
    ]
)
BASE = ("tests/contract", "tests/e2e/test_stdio.py")


def pick(*paths: str) -> e2e_ci.Selection:
    return e2e_ci.select(paths, e2e_modules=E2E)


def module(name: str) -> str:
    return f"tests/e2e/{name}.py"


def test_a_change_to_backlog_docs_or_markdown_skips_the_live_tests() -> None:
    selection = pick(
        "backlog/tasks/plmcp-1 - x.md",
        "backlog/decisions/decision-18 - y.md",
        "docs/fastmcp-reading-guide.md",
        "README.md",
        "CLAUDE.md",
        ".github/ISSUE_TEMPLATE/bug.yml",
    )
    assert selection.skip
    assert selection.targets == ()


def test_no_changed_files_runs_everything_rather_than_skipping_on_an_empty_diff() -> None:
    assert pick() == e2e_ci.Selection(skip=False, targets=("tests/contract", "tests/e2e"))


def test_a_toolset_change_runs_the_base_tier_and_that_toolsets_modules() -> None:
    selection = pick("src/paperless_mcp/tools/sharing.py", "docs/x.md")
    assert not selection.skip
    assert selection.targets == (*BASE, module("test_sharing"))


def test_several_toolsets_run_their_modules_sorted_and_without_duplicates() -> None:
    selection = pick(
        "src/paperless_mcp/tools/versions.py",
        "src/paperless_mcp/services/versions.py",
        "src/paperless_mcp/paperless/trash.py",
        "tests/adapter/test_trash.py",
    )
    assert selection.targets == (*BASE, module("test_trash"), module("test_versions"))


def test_a_stem_without_an_e2e_module_of_its_name_uses_the_alias_table() -> None:
    selection = pick("src/paperless_mcp/services/uploads.py")
    assert selection.targets == (*BASE, module("test_upload_document"))


def test_every_alias_names_existing_e2e_modules_of_the_real_suite() -> None:
    real = {path.stem for path in (ROOT / "tests" / "e2e").glob("test_*.py")}
    for stem, modules in e2e_ci.ALIASES.items():
        assert modules, stem
        assert set(modules) <= real, stem
        assert stem not in real, f"{stem} maps by name already"
    assert real >= e2e_ci.BASE_E2E


def test_a_changed_e2e_module_or_a_unit_test_of_a_toolset_runs_that_toolset() -> None:
    assert pick("tests/e2e/test_bulk.py").targets == (*BASE, module("test_bulk"))
    assert pick("tests/tools/test_pdf.py").targets == (*BASE, module("test_pdf"))
    assert pick("tests/services/test_uploads.py").targets == (*BASE, module("test_upload_document"))


def test_a_contract_or_base_module_change_runs_only_the_base_tier() -> None:
    assert pick("tests/contract/test_bulk.py", "tests/contract/conftest.py").targets == BASE
    assert pick("tests/e2e/test_stdio.py").targets == BASE


@pytest.mark.parametrize(
    "path",
    [
        "src/paperless_mcp/server.py",
        "src/paperless_mcp/app.py",
        "src/paperless_mcp/settings.py",
        "src/paperless_mcp/tools/_shared.py",
        "src/paperless_mcp/tools/core.py",
        "src/paperless_mcp/tools/__init__.py",
        "src/paperless_mcp/domain/documents.py",
        "src/paperless_mcp/domain/ports.py",
        "src/paperless_mcp/paperless/client.py",
        "src/paperless_mcp/paperless/taxonomy.py",
        "src/paperless_mcp/services/unheard_of.py",
        "src/paperless_mcp/tools/new_toolset.py",  # no e2e module and no alias yet
        "src/paperless_mcp/prompts/guide.md",  # markdown below src is runtime data
        "tests/e2e/helpers.py",
        "tests/e2e/conftest.py",
        "tests/e2e/compose.yaml",
        "tests/e2e/compose.latest.yaml",
        "tests/e2e/test_deleted.py",  # a module that is gone: its toolset is unknown
        "tests/conftest.py",
        "tests/harness.py",
        "tests/fakes/paperless.py",
        "tests/services/test_unheard_of.py",
        "tests/notes.md",
        "scripts/e2e_ci.py",
        "pyproject.toml",
        "uv.lock",
        "justfile",
        ".github/workflows/ci.yml",
        ".github/dependabot.yml",
        "docs/vendor/paperless/openapi.json",
        "docs/vendor/fastmcp/index.md",
        "mutation-equivalents.toml",
        "renovate.json",
        ".python-version",
        "LICENSE",
        "docs/../src/paperless_mcp/server.py",
        "backlog/../justfile",
        "/etc/passwd",
        "",
    ],
)
def test_any_change_outside_the_toolset_map_runs_the_whole_suite(path: str) -> None:
    whole = e2e_ci.Selection(skip=False, targets=("tests/contract", "tests/e2e"))
    assert pick("README.md", "src/paperless_mcp/tools/pdf.py", path) == whole


def test_paths_are_read_from_nul_separated_git_output() -> None:
    raw = "docs/a b.md\0src/x.py\0"
    assert e2e_ci.parse_paths(raw) == ["docs/a b.md", "src/x.py"]
    assert e2e_ci.parse_paths("") == []


def run_main(argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = e2e_ci.main(argv, stdin=io.StringIO(stdin), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_the_scope_command_prints_the_job_outputs_for_a_toolset_change() -> None:
    stdin = "src/paperless_mcp/tools/pdf.py\0docs/a.md\0"
    code, out, _ = run_main(["scope"], stdin=stdin)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "mode=fast"
    assert lines[1] == "targets=tests/contract tests/e2e/test_stdio.py tests/e2e/test_pdf.py"


def test_the_scope_command_skips_documentation_only_changes() -> None:
    code, out, _ = run_main(["scope"], stdin="docs/a.md\0backlog/tasks/x.md\0")
    assert (code, out) == (0, "mode=skip\ntargets=\n")


def test_the_scope_command_reads_the_e2e_modules_from_the_given_directory(tmp_path: Path) -> None:
    (tmp_path / "test_only.py").write_text("", "utf-8")
    argv = ["scope", "--e2e-dir", str(tmp_path)]
    _, out, _ = run_main(argv, stdin="src/paperless_mcp/tools/only.py\0")
    assert f"targets=tests/contract tests/e2e/test_stdio.py {module('test_only')}" in out
    _, out, _ = run_main(argv, stdin="src/paperless_mcp/tools/pdf.py\0")
    assert "targets=tests/contract tests/e2e\n" in out


# Shard assignment ----------------------------------------------------------------------------


def test_modules_are_split_into_balanced_shards_by_weight() -> None:
    weights = {"a": 10.0, "b": 9.0, "c": 5.0, "d": 4.0, "e": 1.0}
    plan = e2e_ci.assign_shards(["a", "b", "c", "d", "e"], count=2, weights=weights)
    loads = [sum(weights[m] for m in plan if plan[m] == shard) for shard in (1, 2)]
    assert sorted(loads) == [14.0, 15.0]


def test_the_assignment_is_independent_of_the_order_modules_are_given_in() -> None:
    modules = ["m3", "m1", "m2", "m5", "m4"]
    weights = {"m1": 3.0, "m2": 3.0, "m3": 2.0, "m4": 2.0}
    first = e2e_ci.assign_shards(modules, count=3, weights=weights)
    assert e2e_ci.assign_shards(modules[::-1], count=3, weights=weights) == first


def test_every_module_lands_in_exactly_one_shard_even_without_a_weight() -> None:
    modules = [f"tests/e2e/test_{n}.py" for n in range(7)]
    plan = e2e_ci.assign_shards(modules, count=3, weights={})
    assert sorted(plan) == sorted(modules)
    assert set(plan.values()) <= {1, 2, 3}
    assert {shard: list(plan.values()).count(shard) for shard in (1, 2, 3)} == {1: 3, 2: 2, 3: 2}


def test_an_unweighted_module_counts_as_the_average_of_the_weighted_ones() -> None:
    weights = {"big": 9.0, "small": 1.0}
    plan = e2e_ci.assign_shards(["big", "small", "x", "y"], count=2, weights=weights)
    assert plan["x"] == plan["y"] != plan["big"]


@pytest.mark.parametrize("spec", ["0/3", "4/3", "1/0", "x", "1-3", "1/", "/3", "-1/3", "1/3/5"])
def test_a_malformed_shard_spec_is_refused(spec: str) -> None:
    with pytest.raises(ValueError, match="shard"):
        _ = e2e_ci.parse_shard(spec)


def test_a_shard_spec_is_index_over_count() -> None:
    assert e2e_ci.parse_shard("2/3") == (2, 3)


def test_the_union_of_the_shards_is_the_whole_live_suite_with_nothing_twice() -> None:
    """Collects the real suite whole and per shard: no test lost, none run by two shards."""

    def collect(*extra: str) -> list[str]:
        command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "e2e", *extra]
        done = subprocess.run(  # noqa: S603 -- fixed command line, no input from outside
            [*command, "--no-cov", "-p", "no:cacheprovider"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return [line for line in done.stdout.splitlines() if "::" in line]

    specs = [f"{n}/{e2e_ci.SHARDS}" for n in range(1, e2e_ci.SHARDS + 1)]
    with ThreadPoolExecutor() as pool:  # four collections at once: each one imports the suite
        whole_run = pool.submit(collect)
        shard_runs = [pool.submit(collect, "--e2e-shard", spec) for spec in specs]
        shards = [run.result() for run in shard_runs]
        whole = whole_run.result()
    assert whole, "the e2e suite collects no tests"
    flat = [test for shard in shards for test in shard]
    assert len(flat) == len(set(flat)), "a test is selected by two shards"
    assert sorted(flat) == sorted(whole)
    assert all(shards), "a shard selects no tests"


# The workflow wiring ---------------------------------------------------------------------------

CI = ROOT / ".github" / "workflows" / "ci.yml"
FULL = ROOT / ".github" / "workflows" / "e2e-full.yml"

GOOD_CI = """\
on:
  pull_request:
jobs:
  e2e:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - id: scope
        run: python3 scripts/e2e_ci.py scope
      - if: steps.scope.outputs.mode != 'skip'
        run: just e2e-paths $TARGETS
      - if: failure() && steps.scope.outputs.mode != 'skip'
        run: docker compose logs
"""

GOOD_FULL = """\
on:
  push:
    branches: [main]
  schedule:
    - cron: "17 3 * * *"
jobs:
  e2e-shard:
    strategy:
      fail-fast: false
      matrix:
        shard: ["1/3", "2/3", "3/3"]
    steps:
      - run: just e2e-shard "$SHARD"
"""


def test_the_real_workflows_are_wired_safely() -> None:
    problems = e2e_ci.workflow_problems(
        CI.read_text("utf-8"), FULL.read_text("utf-8"), shards=e2e_ci.SHARDS
    )
    assert problems == []


def test_the_reference_workflows_have_no_problems() -> None:
    assert e2e_ci.workflow_problems(GOOD_CI, GOOD_FULL, shards=3) == []


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        ("== 'pull_request'", "== 'push'", "no `if`"),
        ("    if: github.event_name == 'pull_request'\n", "    if: always()\n", "no `if`"),
        ("  e2e:\n", "  e2e:\n    name: other\n", "name"),
        ("  e2e:\n", "  e2e-x:\n", "no job `e2e`"),
        ("    runs-on: ubuntu-latest\n", "    continue-on-error: true\n", "continue-on-error"),
        ("      - id: scope\n", "      - id: other\n", "scope step"),
        ("      - if: steps.scope.outputs.mode != 'skip'\n", "      - if: success()\n", "mode"),
        ("      - if: steps.scope.outputs.mode != 'skip'\n", "      - if: false\n", "mode"),
        ("just e2e-paths $TARGETS", "just e2e", "e2e-paths"),
        ("      - if: failure() && steps", "      - if: failure() || steps", "mode"),
        (
            "        run: docker compose logs",
            "        if: success()\n        run: docker compose logs",
            "mode",
        ),
    ],
)
def test_a_pull_request_workflow_that_could_skip_the_live_tests_wrongly_is_reported(
    old: str, new: str, problem: str
) -> None:
    broken = GOOD_CI.replace(old, new)
    assert broken != GOOD_CI
    found = e2e_ci.workflow_problems(broken, GOOD_FULL, shards=3)
    assert any(problem in line for line in found), found


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        ('["1/3", "2/3", "3/3"]', '["1/3", "2/3"]', "matrix"),
        ('["1/3", "2/3", "3/3"]', '["1/3", "2/3", "3/3", "1/3"]', "matrix"),
        ('["1/3", "2/3", "3/3"]', '["1/2", "2/2"]', "matrix"),
        (
            "      fail-fast: false\n",
            "      fail-fast: false\n      continue-on-error: true\n",
            "continue-on-error",
        ),
        ("  e2e-shard:\n", "  other:\n", "no job `e2e-shard`"),
        ("  push:\n    branches: [main]\n", "", "push to main"),
        ("  schedule:\n", "  workflow_dispatch:\n", "schedule"),
        ('just e2e-shard "$SHARD"', "just e2e", "e2e-shard"),
    ],
)
def test_a_full_run_workflow_that_could_miss_shards_or_runs_is_reported(
    old: str, new: str, problem: str
) -> None:
    broken = GOOD_FULL.replace(old, new)
    assert broken != GOOD_FULL
    found = e2e_ci.workflow_problems(GOOD_CI, broken, shards=3)
    assert any(problem in line for line in found), found
