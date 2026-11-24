"""Tests of the helpers behind the tiered e2e CI check and the sharded full run (PLMCP-39)."""

import io
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import e2e_ci
import pytest

ROOT = Path(__file__).resolve().parents[2]


E2E = [
    "ai",
    "bulk",
    "client",
    "documents",
    "files",
    "http",
    "notes_and_trash",
    "pdf",
    "search",
    "sharing",
    "stack",
    "stdio",
    "tasks",
    "taxonomy_edits",
    "toolset_guide",
    "trash",
    "update_document",
    "upload_document",
    "versions",
    "views",
]
CONTRACT = [
    "admin",
    "ai_reader",
    "bulk",
    "document_reader",
    "document_upload",
    "document_versions",
    "document_writer",
    "instance_reader",
    "notes_and_trash",
    "pdf",
    "sharing",
    "task_reader",
    "taxonomy_edits",
    "taxonomy_reader",
    "taxonomy_writer",
    "trash",
    "view_filters",
    "views",
]
LIVE = frozenset(
    [f"tests/e2e/test_{name}.py" for name in E2E]
    + [f"tests/contract/test_{name}.py" for name in CONTRACT]
)
STDIO = "tests/e2e/test_stdio.py"
SMOKE = (STDIO, *sorted(set(e2e_ci.SMOKE_TARGETS) - {STDIO}))


def pick(*paths: str) -> e2e_ci.Selection:
    return e2e_ci.select(paths, live_modules=LIVE)


def e2e(name: str) -> str:
    return f"tests/e2e/{name}.py"


def contract(name: str) -> str:
    return f"tests/contract/{name}.py"


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


def test_no_changed_files_runs_the_smoke_tier_rather_than_skipping_on_an_empty_diff() -> None:
    assert pick() == e2e_ci.Selection(skip=False, targets=SMOKE)


def test_the_smoke_tier_is_small_and_made_of_live_modules_that_exist() -> None:
    real = {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").rglob("test_*.py")}
    assert STDIO in e2e_ci.SMOKE_TARGETS
    assert set(e2e_ci.SMOKE_TARGETS) <= real
    assert len(e2e_ci.SMOKE_TARGETS) <= 6  # a pull request signal of minutes, not of the suite


def test_a_toolset_change_runs_the_stdio_test_and_that_toolsets_live_modules() -> None:
    selection = pick("src/mcp_server_paperless/tools/sharing.py", "docs/x.md")
    assert not selection.skip
    assert selection.targets == (STDIO, contract("test_sharing"), e2e("test_sharing"))


def test_several_toolsets_run_their_modules_sorted_and_without_duplicates() -> None:
    selection = pick(
        "src/mcp_server_paperless/tools/views.py",
        "src/mcp_server_paperless/services/views.py",
        "src/mcp_server_paperless/paperless/trash.py",
        "tests/adapter/test_trash.py",
    )
    assert selection.targets == (
        STDIO,
        contract("test_trash"),
        contract("test_view_filters"),
        contract("test_views"),
        e2e("test_search"),
        e2e("test_trash"),
        e2e("test_views"),
    )


def test_a_stem_uses_the_alias_table_besides_the_modules_of_its_own_name() -> None:
    assert pick("src/mcp_server_paperless/services/uploads.py").targets == (
        STDIO,
        contract("test_document_upload"),
        e2e("test_upload_document"),
    )
    assert pick("src/mcp_server_paperless/tools/versions.py").targets == (
        STDIO,
        contract("test_document_versions"),
        e2e("test_versions"),
    )


def test_every_alias_names_existing_live_modules_of_the_real_suite() -> None:
    real = {
        path.relative_to(ROOT / "tests").stem
        for path in (ROOT / "tests").rglob("test_*.py")
        if path.parent.name in {"e2e", "contract"}
    }
    for stem, modules in e2e_ci.ALIASES.items():
        assert modules, stem
        assert set(modules) <= real, stem
    assert e2e_ci.SMOKE_TARGETS


def test_a_changed_live_module_or_a_unit_test_of_a_toolset_runs_that_toolset() -> None:
    assert pick("tests/e2e/test_bulk.py").targets == (STDIO, e2e("test_bulk"))
    assert pick("tests/contract/test_bulk.py").targets == (STDIO, contract("test_bulk"))
    assert pick("tests/tools/test_pdf.py").targets == (
        STDIO,
        contract("test_pdf"),
        e2e("test_pdf"),
    )
    assert pick("tests/services/test_uploads.py").targets == (
        STDIO,
        contract("test_document_upload"),
        e2e("test_upload_document"),
    )


CORE_LIVE = (
    "tests/contract/test_document_reader.py",
    "tests/contract/test_document_writer.py",
    "tests/contract/test_instance_reader.py",
    "tests/contract/test_notes_and_trash.py",
    "tests/contract/test_task_reader.py",
    "tests/contract/test_taxonomy_edits.py",
    "tests/contract/test_taxonomy_reader.py",
    "tests/contract/test_taxonomy_writer.py",
    "tests/e2e/test_documents.py",
    "tests/e2e/test_files.py",
    "tests/e2e/test_notes_and_trash.py",
    "tests/e2e/test_search.py",
    "tests/e2e/test_tasks.py",
    "tests/e2e/test_taxonomy_edits.py",
    "tests/e2e/test_update_document.py",
)


def test_the_core_toolset_runs_the_live_modules_of_documents_and_taxonomy() -> None:
    selection = pick("src/mcp_server_paperless/tools/core.py")
    assert set(CORE_LIVE) <= set(selection.targets)
    assert selection.targets[0] == STDIO


def test_a_domain_module_runs_the_toolset_of_its_name() -> None:
    assert pick("src/mcp_server_paperless/domain/pdf.py").targets == (
        STDIO,
        contract("test_pdf"),
        e2e("test_pdf"),
    )
    assert set(pick("src/mcp_server_paperless/domain/documents.py").targets) >= {
        contract("test_document_writer"),
        e2e("test_update_document"),
    }


def test_shared_plumbing_runs_the_smoke_set_and_the_modules_of_its_stem() -> None:
    client = pick("src/mcp_server_paperless/paperless/client.py")
    assert client.targets == (STDIO, *sorted((set(SMOKE) | {e2e("test_client")}) - {STDIO}))


@pytest.mark.parametrize("name", ["app", "cli"])
def test_the_entry_points_run_the_http_transport_test_besides_the_smoke_set(name: str) -> None:
    selection = pick(f"src/mcp_server_paperless/{name}.py")
    assert set(selection.targets) == set(SMOKE) | {e2e("test_http")}


def test_the_composition_root_also_runs_the_guide_to_the_toolsets() -> None:
    selection = pick("src/mcp_server_paperless/server.py")
    assert set(selection.targets) == set(SMOKE) | {e2e("test_http"), e2e("test_toolset_guide")}
    for path in ("domain/instance.py", "tools/selection.py", "tools/core.py"):
        assert e2e("test_toolset_guide") in pick(f"src/mcp_server_paperless/{path}").targets


def test_every_live_module_is_reachable_from_a_source_path_or_the_smoke_set() -> None:
    """A module nothing selects runs only after the merge: a choice, not an accident."""
    live = {
        path.relative_to(ROOT).as_posix()
        for folder in ("e2e", "contract")
        for path in (ROOT / "tests" / folder).glob("test_*.py")
    } - {"tests/contract/test_coverage.py"}  # guards the contract suite, has no live test
    sources = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src" / "mcp_server_paperless").rglob("*.py")
    ]
    reached = set(SMOKE)
    for source in sources:
        reached |= set(e2e_ci.select([source], live_modules=live).targets)
    assert live - reached == set()


def test_a_cross_cutting_change_over_the_budget_is_split_not_cut() -> None:
    weights = {f"tests/e2e/test_m{n}.py": 300.0 for n in range(5)}
    targets = (STDIO, *weights)
    groups = e2e_ci.split_tier(targets, weights={**weights, STDIO: 14.0}, budget=450.0, parts=3)
    assert len(groups) == 3
    assert sorted(t for group in groups for t in group) == sorted(targets)  # nothing dropped
    assert STDIO in groups[0]


def test_a_tier_within_the_budget_stays_one_group() -> None:
    targets = (STDIO, e2e("test_pdf"))
    assert e2e_ci.split_tier(targets, weights={STDIO: 14.0, e2e("test_pdf"): 77.0}) == [targets]


def test_the_split_never_makes_more_groups_than_the_matrix_allows() -> None:
    weights = {f"tests/e2e/test_m{n}.py": 900.0 for n in range(9)}
    groups = e2e_ci.split_tier(tuple(weights), weights=weights, budget=450.0, parts=3)
    assert len(groups) == 3


def test_the_real_cross_cutting_example_fits_the_budget_per_group() -> None:
    stems = ["documents", "bulk_documents", "pdf", "uploads", "versions", "sharing"]
    live = {
        path.relative_to(ROOT).as_posix()
        for folder in ("e2e", "contract")
        for path in (ROOT / "tests" / folder).glob("test_*.py")
    }
    paths = [f"src/mcp_server_paperless/services/{stem}.py" for stem in stems]
    targets = e2e_ci.select(paths, live_modules=live).targets
    assert sum(e2e_ci.MODULE_SECONDS.get(t, 0.0) for t in targets) > e2e_ci.TIER_BUDGET_SECONDS
    groups = e2e_ci.split_tier(targets)
    assert len(groups) > 1
    assert all(
        sum(e2e_ci.MODULE_SECONDS.get(t, 0.0) for t in group) <= e2e_ci.TIER_BUDGET_SECONDS
        for group in groups
    )


def test_a_changed_stdio_test_runs_only_the_stdio_test() -> None:
    assert pick(STDIO).targets == (STDIO,)


@pytest.mark.parametrize(
    "path",
    [
        "src/mcp_server_paperless/settings.py",
        "src/mcp_server_paperless/tools/_shared.py",
        "src/mcp_server_paperless/tools/__init__.py",
        "src/mcp_server_paperless/domain/ports.py",
        "src/mcp_server_paperless/domain/credentials.py",
        "src/mcp_server_paperless/paperless/errors.py",  # a shared stem, no module of its name
        "src/mcp_server_paperless/services/unheard_of.py",
        "src/mcp_server_paperless/tools/new_toolset.py",  # no live module and no alias yet
        "src/mcp_server_paperless/prompts/guide.md",  # markdown below src is runtime data
        "tests/e2e/helpers.py",
        "tests/e2e/conftest.py",
        "tests/e2e/compose.yaml",
        "tests/e2e/compose.latest.yaml",
        "tests/e2e/test_deleted.py",  # a module that is gone: its toolset is unknown
        "tests/contract/conftest.py",
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
        "docs/../src/mcp_server_paperless/server.py",
        "backlog/../justfile",
        "/etc/passwd",
        "",
    ],
)
def test_a_change_outside_the_toolset_map_runs_the_smoke_tier_not_the_suite(path: str) -> None:
    """Such a change is checked after the merge by the full run (decision-23)."""
    assert pick("README.md", path) == e2e_ci.Selection(skip=False, targets=SMOKE)
    mixed = pick("README.md", "src/mcp_server_paperless/tools/pdf.py", path)
    extra = {contract("test_pdf"), e2e("test_pdf")} - set(SMOKE)
    assert mixed.targets == (STDIO, *sorted((set(SMOKE) | extra) - {STDIO}))


def test_paths_are_read_from_nul_separated_git_output() -> None:
    raw = "docs/a b.md\0src/x.py\0"
    assert e2e_ci.parse_paths(raw) == ["docs/a b.md", "src/x.py"]
    assert e2e_ci.parse_paths("") == []


def run_main(argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = e2e_ci.main(argv, stdin=io.StringIO(stdin), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_the_scope_command_prints_the_job_outputs_for_a_toolset_change() -> None:
    stdin = "src/mcp_server_paperless/tools/pdf.py\0docs/a.md\0"
    code, out, _ = run_main(["scope"], stdin=stdin)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "mode=fast"
    names = [contract("test_pdf"), contract("test_pdf_insert")]
    names += [e2e("test_pages"), e2e("test_pdf"), e2e("test_pdf_insert")]
    assert lines[1] == f"targets={STDIO} {' '.join(names)}"


def test_the_scope_command_gives_each_part_its_group_and_skips_the_parts_without_work() -> None:
    stems = ["documents", "bulk_documents", "pdf", "uploads", "versions", "sharing"]
    stdin = "".join(f"src/mcp_server_paperless/services/{stem}.py\0" for stem in stems)
    seen: list[str] = []
    modes: list[str] = []
    for part in ("1/4", "2/4", "3/4", "4/4"):
        code, out, err = run_main(["scope", "--part", part], stdin=stdin)
        assert code == 0
        modes.append(out.splitlines()[0])
        seen += out.splitlines()[1].removeprefix("targets=").split()
        assert part in err  # the log line names the part and what it covers
    all_targets = run_main(["scope"], stdin=stdin)[1].splitlines()[1].removeprefix("targets=")
    assert sorted(seen) == sorted(all_targets.split())  # nothing dropped, nothing run twice
    assert modes[0] == "mode=fast"
    _, out, err = run_main(
        ["scope", "--part", "4/4"], stdin="src/mcp_server_paperless/tools/pdf.py\0"
    )
    assert out == "mode=skip\ntargets=\n"
    assert "no work" in err


def test_the_scope_command_skips_documentation_only_changes() -> None:
    code, out, _ = run_main(["scope"], stdin="docs/a.md\0backlog/tasks/x.md\0")
    assert (code, out) == (0, "mode=skip\ntargets=\n")


def test_the_scope_command_reads_the_live_modules_below_the_given_root(tmp_path: Path) -> None:
    for name in ("e2e/test_only.py", "contract/test_only.py", "contract/conftest.py"):
        (tmp_path / "tests" / name).parent.mkdir(parents=True, exist_ok=True)
        _ = (tmp_path / "tests" / name).write_text("", "utf-8")
    argv = ["scope", "--root", str(tmp_path)]
    _, out, _ = run_main(argv, stdin="src/mcp_server_paperless/tools/only.py\0")
    assert f"targets={STDIO} {contract('test_only')} {e2e('test_only')}\n" in out
    _, out, _ = run_main(argv, stdin="src/mcp_server_paperless/tools/pdf.py\0")
    assert f"targets={' '.join(SMOKE)}\n" in out


# Shard assignment ----------------------------------------------------------------------------


def test_the_weights_name_the_live_modules_of_the_suite_and_split_it_evenly() -> None:
    real = {
        path.relative_to(ROOT).as_posix()
        for folder in ("e2e", "contract")
        for path in (ROOT / "tests" / folder).glob("test_*.py")
    }
    assert set(e2e_ci.MODULE_SECONDS) <= real, "a weight names a module that is gone"
    plan = e2e_ci.assign_shards(sorted(real), count=e2e_ci.SHARDS)
    known = [e2e_ci.MODULE_SECONDS[m] for m in real if m in e2e_ci.MODULE_SECONDS]
    average = sum(known) / len(known)  # an unweighted module counts so in assign_shards too
    loads = [0.0] * e2e_ci.SHARDS
    for module_path in real:
        loads[plan[module_path] - 1] += e2e_ci.MODULE_SECONDS.get(module_path, average)
    assert max(loads) <= 1.15 * sum(loads) / len(loads), loads


# Seconds per module on a hosted runner: the median of four green e2e-full runs of 2026-11-24,
# 45349506723, 45349964929, 45353668848 and 45355735636, derived from the Actions timestamp of each
# module's progress line; the first module of a shard has the 30 s of the stack's seeding taken off.
# This is the measured reality the weights in scripts/e2e_ci.py must split evenly; refresh it
# together with the weights.
HOSTED_SECONDS = {
    "tests/contract/test_pdf.py": 253,
    "tests/contract/test_bulk.py": 248,
    "tests/contract/test_duplicates.py": 207,
    "tests/e2e/test_pages_files.py": 206,
    "tests/contract/test_document_versions.py": 205,
    "tests/contract/test_document_writer.py": 160,
    "tests/e2e/test_duplicates.py": 111,
    "tests/e2e/test_pdf.py": 111,
    "tests/contract/test_sharing.py": 106,
    "tests/e2e/test_confirmation.py": 96,
    "tests/e2e/test_bulk.py": 69,
    "tests/e2e/test_upload_duplicates.py": 68,
    "tests/contract/test_document_upload.py": 62,
    "tests/contract/test_notes_and_trash.py": 58,
    "tests/contract/test_admin.py": 57,
    "tests/contract/test_taxonomy_edits.py": 56,
    "tests/e2e/test_text_edits.py": 56,
    "tests/e2e/test_toolset_guide.py": 54,
    "tests/e2e/test_versions.py": 52,
    "tests/e2e/test_upload_batches.py": 50,
    "tests/e2e/test_pages.py": 49,
    "tests/e2e/test_update_document.py": 48,
    "tests/contract/test_trash.py": 47,
    "tests/e2e/test_permissions.py": 44,
    "tests/e2e/test_search.py": 41,
    "tests/e2e/test_admin.py": 40,
    "tests/contract/test_permissions.py": 38,
    "tests/contract/test_batch_uploads.py": 37,
    "tests/contract/test_text_edits.py": 34,
    "tests/e2e/test_downloads.py": 28,
    "tests/e2e/test_upload_document.py": 25,
    "tests/contract/test_document_reader.py": 23,
    "tests/contract/test_instance_reader.py": 22,
    "tests/e2e/test_raw.py": 20,
    "tests/contract/test_task_reader.py": 16,
    "tests/e2e/test_mail.py": 16,
    "tests/e2e/test_notes_and_trash.py": 16,
    "tests/e2e/test_taxonomy_edits.py": 16,
    "tests/e2e/test_files.py": 15,
    "tests/e2e/test_http.py": 15,
    "tests/e2e/test_stdio.py": 15,
    "tests/e2e/test_sharing.py": 11,
    "tests/e2e/test_trash.py": 10,
    "tests/contract/test_ai_reader.py": 8,
    "tests/e2e/test_documents.py": 8,
    "tests/contract/test_workflows.py": 7,
    "tests/contract/test_mail.py": 6,
    "tests/contract/test_views.py": 6,
    "tests/e2e/test_views.py": 6,
    "tests/e2e/test_workflows.py": 6,
    "tests/e2e/test_tasks.py": 4,
    "tests/contract/test_taxonomy_reader.py": 2,
    "tests/contract/test_view_filters.py": 2,
    "tests/e2e/test_ai.py": 2,
    "tests/e2e/test_stack.py": 2,
    "tests/contract/test_raw.py": 1,
    "tests/contract/test_taxonomy_writer.py": 1,
    "tests/e2e/test_client.py": 0,
    "tests/e2e/test_rejected_duplicates.py": 0,
    "tests/e2e/test_trash_order.py": 0,
}


def test_the_split_by_the_weights_is_even_in_hosted_runner_seconds() -> None:
    """PLMCP-44: the first main run split 745/652/458 s where 370/396/396 s were predicted."""
    real = {
        path.relative_to(ROOT).as_posix()
        for folder in ("e2e", "contract")
        for path in (ROOT / "tests" / folder).glob("test_*.py")
    }
    assert set(HOSTED_SECONDS) <= real, "a measured module is gone: refresh HOSTED_SECONDS"
    plan = e2e_ci.assign_shards(sorted(HOSTED_SECONDS), count=e2e_ci.SHARDS)
    loads = [0.0] * e2e_ci.SHARDS
    for module_path, seconds in HOSTED_SECONDS.items():
        loads[plan[module_path] - 1] += seconds
    assert max(loads) <= 1.15 * sum(loads) / len(loads), loads


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
    with ThreadPoolExecutor() as pool:  # five collections at once: each one imports the suite
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
LATEST = ROOT / ".github" / "workflows" / "e2e-latest.yml"

GOOD_CI = """\
on:
  pull_request:
jobs:
  e2e:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    strategy:
      matrix:
        part: ["1/4", "2/4", "3/4", "4/4"]
    steps:
      - id: scope
        run: python3 scripts/e2e_ci.py scope --part "$PART"
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
    timeout-minutes: 25
    strategy:
      fail-fast: false
      matrix:
        shard: ["1/3", "2/3", "3/3"]
    steps:
      - run: just e2e-shard "$SHARD"
"""

GOOD_LATEST = """\
on:
  schedule:
    - cron: "41 4 * * 1"
  workflow_dispatch:
jobs:
  resolve:
    timeout-minutes: 10
    outputs:
      version: ${{ steps.version.outputs.version }}
    steps:
      - id: version
        run: just e2e-latest-version
  e2e-latest:
    needs: resolve
    timeout-minutes: 25
    strategy:
      fail-fast: false
      matrix:
        shard: ["1/3", "2/3", "3/3"]
    steps:
      - run: just e2e-latest "$SHARD"
        env:
          PAPERLESS_E2E_VERSION: ${{ needs.resolve.outputs.version }}
          SHARD: ${{ matrix.shard }}
"""


def problems_of(
    *, ci: str = GOOD_CI, full: str = GOOD_FULL, latest: str = GOOD_LATEST
) -> list[str]:
    return e2e_ci.workflow_problems(ci, full, latest, shards=3)


def test_the_real_workflows_are_wired_safely() -> None:
    problems = e2e_ci.workflow_problems(
        CI.read_text("utf-8"),
        FULL.read_text("utf-8"),
        LATEST.read_text("utf-8"),
        shards=e2e_ci.SHARDS,
    )
    assert problems == []


def test_the_reference_workflows_have_no_problems() -> None:
    assert problems_of() == []


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
        ('["1/4", "2/4", "3/4", "4/4"]', '["1/4", "2/4", "3/4"]', "matrix"),
        ('scope --part "$PART"', "scope", "--part"),
        ("      - if: failure() && steps", "      - if: failure() || steps", "mode"),
        (
            "        run: docker compose logs",
            "        if: success()\n        run: docker compose logs",
            "mode",
        ),
        ("    timeout-minutes: 20\n", "", "timeout-minutes"),
        ("timeout-minutes: 20", "timeout-minutes: 35", "timeout-minutes"),
        ("timeout-minutes: 20", "timeout-minutes: 60", "timeout-minutes"),
    ],
)
def test_a_pull_request_workflow_that_could_skip_the_live_tests_wrongly_is_reported(
    old: str, new: str, problem: str
) -> None:
    broken = GOOD_CI.replace(old, new)
    assert broken != GOOD_CI
    found = problems_of(ci=broken)
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
        ("    timeout-minutes: 25\n", "", "timeout-minutes"),
        ("timeout-minutes: 25", "timeout-minutes: 60", "timeout-minutes"),
    ],
)
def test_a_full_run_workflow_that_could_miss_shards_or_runs_is_reported(
    old: str, new: str, problem: str
) -> None:
    broken = GOOD_FULL.replace(old, new)
    assert broken != GOOD_FULL
    found = problems_of(full=broken)
    assert any(problem in line for line in found), found


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        ('["1/3", "2/3", "3/3"]', '["1/3", "2/3"]', "matrix"),
        ('["1/3", "2/3", "3/3"]', '["1/3", "2/3", "3/3", "3/3"]', "matrix"),
        ('["1/3", "2/3", "3/3"]', '["1/2", "2/2"]', "matrix"),
        ("      fail-fast: false\n", "", "fail-fast"),
        (
            "      fail-fast: false\n",
            "      fail-fast: false\n      continue-on-error: true\n",
            "continue-on-error",
        ),
        (
            "    needs: resolve\n",
            "    needs: resolve\n    continue-on-error: ${{ true }}\n",
            "continue-on-error",
        ),
        ("  e2e-latest:\n", "  other:\n", "no job `e2e-latest`"),
        ("  resolve:\n", "  other:\n", "no job `resolve`"),
        ("    needs: resolve\n", "", "needs"),
        ("    needs: resolve\n", "    needs: other\n", "needs"),
        ("  schedule:\n    - cron:", "  workflow_dispatch:\n    - cron:", "schedule"),
        ("  schedule:\n", "  pull_request:\n", "schedule"),
        ("  workflow_dispatch:\n", "", "workflow_dispatch"),
        ("    timeout-minutes: 25\n", "", "timeout-minutes"),
        ("timeout-minutes: 25", "timeout-minutes: 30", "timeout-minutes"),
        ("    timeout-minutes: 10\n", "", "timeout-minutes"),
        ("      version: ${{ steps.version.outputs.version }}\n", "      other: x\n", "output"),
        ('just e2e-latest "$SHARD"', "just e2e", "just e2e-latest"),
        ('just e2e-latest "$SHARD"', "just e2e-latest", "shard"),
        ("          PAPERLESS_E2E_VERSION: ${{ needs.resolve.outputs.version }}\n", "", "version"),
        (
            "${{ needs.resolve.outputs.version }}",
            "${{ inputs.version }}",
            "version",
        ),
    ],
)
def test_a_latest_workflow_that_could_miss_shards_or_the_resolved_version_is_reported(
    old: str, new: str, problem: str
) -> None:
    broken = GOOD_LATEST.replace(old, new)
    assert broken != GOOD_LATEST
    found = problems_of(latest=broken)
    assert any(problem in line for line in found), found


@pytest.mark.parametrize(
    "path",
    ["rendering/worker.py", "rendering/limits.py", "services/pages.py", "domain/rendering.py"],
)
def test_a_changed_renderer_runs_the_live_tests_of_page_images(path: str) -> None:
    live = {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("e2e/test_*.py")}

    targets = e2e_ci.select([f"src/mcp_server_paperless/{path}"], live_modules=live).targets

    assert {e2e("test_pages"), e2e("test_pages_files")} <= set(targets)


def test_a_changed_pdf_tool_runs_the_live_tests_of_inspect_pdf() -> None:
    live = {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("e2e/test_*.py")}

    targets = e2e_ci.select(["src/mcp_server_paperless/tools/pdf.py"], live_modules=live).targets

    assert {e2e("test_pages"), e2e("test_pdf"), e2e("test_pdf_insert")} <= set(targets)


@pytest.mark.parametrize(
    "path", ["rendering/worker.py", "rendering/fields.py", "services/pdf_insert.py"]
)
def test_what_inserts_pages_runs_the_live_tests_of_insert_pages(path: str) -> None:
    live = {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("e2e/test_*.py")}

    targets = e2e_ci.select([f"src/mcp_server_paperless/{path}"], live_modules=live).targets

    assert e2e("test_pdf_insert") in targets
