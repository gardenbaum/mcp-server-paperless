"""Tests of the helpers behind the sharded, conditionally skipped e2e CI job (PLMCP-39)."""

import io
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import e2e_ci
import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_a_change_to_backlog_docs_or_markdown_needs_no_e2e() -> None:
    paths = [
        "backlog/tasks/plmcp-1 - x.md",
        "backlog/decisions/decision-18 - y.md",
        "docs/fastmcp-reading-guide.md",
        "README.md",
        "CLAUDE.md",
        ".github/ISSUE_TEMPLATE/bug.yml",
    ]
    assert not e2e_ci.needs_e2e(paths)


@pytest.mark.parametrize(
    "path",
    [
        "src/paperless_mcp/server.py",
        "src/paperless_mcp/prompts/guide.md",  # markdown below src is runtime data
        "tests/e2e/helpers.py",
        "tests/e2e/compose.yaml",
        "tests/notes.md",
        "scripts/e2e_ci.py",
        "pyproject.toml",
        "uv.lock",
        "justfile",
        ".github/workflows/ci.yml",
        ".github/dependabot.yml",
        "docs/vendor/paperless/openapi.json",  # the adapter tests validate against it
        "docs/vendor/fastmcp/index.md",
        "mutation-equivalents.toml",
        "renovate.json",
        ".python-version",
        "LICENSE",
        "docs/../src/paperless_mcp/server.py",  # a path that climbs out of docs/
        "backlog/../justfile",
        "",
    ],
)
def test_any_other_change_needs_e2e(path: str) -> None:
    assert e2e_ci.needs_e2e(["README.md", path])


def test_no_changed_files_needs_e2e_rather_than_skipping_on_an_empty_diff() -> None:
    assert e2e_ci.needs_e2e([])


def test_paths_are_read_from_nul_separated_git_output() -> None:
    raw = "docs/a b.md\0src/x.py\0"
    assert e2e_ci.parse_paths(raw) == ["docs/a b.md", "src/x.py"]
    assert e2e_ci.parse_paths("") == []


@pytest.mark.parametrize(
    ("scope", "run", "shards", "passes"),
    [
        ("success", "true", "success", True),
        ("success", "false", "skipped", True),
        ("success", "true", "failure", False),
        ("success", "true", "cancelled", False),
        ("success", "true", "skipped", False),  # a skipped shard is not a green shard
        ("success", "false", "success", False),  # shards ran although the scope said skip
        ("success", "false", "failure", False),
        ("success", "", "skipped", False),
        ("success", "maybe", "success", False),
        ("failure", "false", "skipped", False),
        ("skipped", "false", "skipped", False),
        ("cancelled", "true", "success", False),
        ("", "true", "success", False),
    ],
)
def test_the_verdict_passes_only_when_every_shard_succeeded_or_the_skip_applies(
    scope: str, run: str, shards: str, *, passes: bool
) -> None:
    assert (e2e_ci.verdict(scope=scope, run=run, shards=shards) is None) is passes


def test_the_verdict_names_the_reason() -> None:
    reason = e2e_ci.verdict(scope="success", run="true", shards="failure")
    assert reason is not None
    assert "failure" in reason


def run_main(argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = e2e_ci.main(argv, stdin=io.StringIO(stdin), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_the_scope_command_prints_the_job_output_for_a_code_change() -> None:
    code, out, _ = run_main(["scope"], stdin="docs/a.md\0src/x.py\0")
    assert (code, out) == (0, "run=true\n")


def test_the_scope_command_skips_documentation_only_changes() -> None:
    code, out, _ = run_main(["scope"], stdin="docs/a.md\0backlog/tasks/x.md\0")
    assert (code, out) == (0, "run=false\n")


def test_the_verdict_command_exits_zero_when_the_skip_applies() -> None:
    argv = ["verdict", "--scope", "success", "--run", "false", "--shards", "skipped"]
    code, out, _ = run_main(argv)
    assert (code, out) == (0, "e2e: skipped, the change touches no runtime file\n")


def test_the_verdict_command_exits_zero_when_every_shard_succeeded() -> None:
    argv = ["verdict", "--scope", "success", "--run", "true", "--shards", "success"]
    code, out, _ = run_main(argv)
    assert (code, out) == (0, "e2e: every shard succeeded\n")


def test_the_verdict_command_fails_when_a_shard_failed() -> None:
    argv = ["verdict", "--scope", "success", "--run", "true", "--shards", "failure"]
    code, out, err = run_main(argv)
    assert code == 1
    assert out == ""
    assert "failure" in err


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

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

GOOD_WORKFLOW = """\
jobs:
  e2e-scope:
    runs-on: ubuntu-latest
  e2e-shard:
    name: e2e-shard (${{ matrix.shard }})
    needs: e2e-scope
    if: needs.e2e-scope.outputs.run == 'true'
    strategy:
      fail-fast: false
      matrix:
        shard: ["1/3", "2/3", "3/3"]
  e2e:
    needs: [e2e-scope, e2e-shard]
    if: always()
    runs-on: ubuntu-latest
"""


def test_the_workflow_wires_the_scope_the_shards_and_the_aggregate_check_safely() -> None:
    assert e2e_ci.workflow_problems(WORKFLOW.read_text("utf-8"), shards=e2e_ci.SHARDS) == []


def test_the_reference_workflow_has_no_problems() -> None:
    assert e2e_ci.workflow_problems(GOOD_WORKFLOW, shards=3) == []


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        ("    if: always()\n", "", "always()"),
        ("    if: always()\n", "    if: success()\n", "always()"),
        (
            "    if: always()\n",
            "    if: always()\n    continue-on-error: true\n",
            "continue-on-error",
        ),
        ("    needs: [e2e-scope, e2e-shard]\n", "    needs: [e2e-shard]\n", "needs"),
        ("    needs: [e2e-scope, e2e-shard]\n", "    needs: [e2e-scope]\n", "needs"),
        ("  e2e:\n", "  e2e:\n    name: something else\n", "name"),
        ("  e2e:\n", "  e2e-all:\n", "no job `e2e`"),
        ("    if: needs.e2e-scope.outputs.run == 'true'\n", "", "run == 'true'"),
        ('["1/3", "2/3", "3/3"]', '["1/3", "2/3"]', "matrix"),
        ('["1/3", "2/3", "3/3"]', '["1/3", "2/3", "3/3", "1/3"]', "matrix"),
        ('["1/3", "2/3", "3/3"]', '["1/2", "2/2"]', "matrix"),
        (
            "      fail-fast: false\n",
            "      fail-fast: false\n      continue-on-error: true\n",
            "continue-on-error",
        ),
        ("  e2e-shard:\n", "  e2e-shard-x:\n", "no job `e2e-shard`"),
        ("    needs: e2e-scope\n", "", "needs"),
    ],
)
def test_a_workflow_that_could_pass_without_every_shard_is_reported(
    old: str, new: str, problem: str
) -> None:
    broken = GOOD_WORKFLOW.replace(old, new)
    assert broken != GOOD_WORKFLOW
    found = e2e_ci.workflow_problems(broken, shards=3)
    assert any(problem in line for line in found), found
