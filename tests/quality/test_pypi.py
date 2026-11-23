"""Guards for the PyPI distribution and its workflow (decision-32, decision-33).

The files are read as text: nothing here builds or uploads a package (the ``pypi`` workflow
does), these tests only keep the name and the publishing rules from eroding unnoticed.
"""

import importlib.metadata
import re
import tomllib
from pathlib import Path
from typing import cast

import pytest
import yaml

import mcp_server_paperless

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "pypi.yml"
DISTRIBUTION = "mcp-server-paperless"
REPOSITORY = "https://github.com/gardenbaum/mcp-server-paperless"
SHA_PIN = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")
TAG_GATE = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"

pytestmark = pytest.mark.decision("decision-32")


def mapping(value: object) -> dict[str, object]:
    """The value as a string-keyed mapping; fails the test when it is not one."""
    assert isinstance(value, dict)
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def sequence(value: object) -> list[object]:
    """The value as a list; fails the test when it is not one."""
    assert isinstance(value, list)
    return list(cast("list[object]", value))


def project() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return mapping(cast("object", tomllib.load(file)["project"]))


def workflow() -> dict[str, object]:
    """The parsed workflow; YAML 1.1 reads the key ``on`` as the boolean True."""
    loaded = mapping(cast("object", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))))
    loaded["on"] = loaded.pop("True", loaded.get("on"))
    return loaded


def jobs() -> dict[str, dict[str, object]]:
    return {name: mapping(item) for name, item in mapping(workflow()["jobs"]).items()}


def job(name: str) -> dict[str, object]:
    return jobs()[name]


def steps(name: str) -> list[dict[str, object]]:
    return [mapping(step) for step in sequence(job(name)["steps"])]


def all_steps() -> list[dict[str, object]]:
    return [step for name in jobs() for step in steps(name)]


def label(step: dict[str, object]) -> str:
    return str(step.get("uses") or step.get("name"))


def named(job_name: str, text: str) -> dict[str, object]:
    found = [step for step in steps(job_name) if text in str(step.get("name", ""))]
    assert len(found) == 1, f"{job_name}: one step named like {text!r}"
    return found[0]


def position(job_name: str, needle: str) -> int:
    return next(i for i, step in enumerate(steps(job_name)) if needle in label(step))


@pytest.mark.decision("decision-37")
def test_the_distribution_has_a_console_script_of_its_own_name_beside_the_old_ones() -> None:
    meta = project()
    scripts = mapping(meta["scripts"])

    assert meta["name"] == DISTRIBUTION
    # `uvx mcp-server-paperless@latest` runs the script named like the distribution.
    assert scripts[DISTRIBUTION] == scripts["paperless-mcp"] == "mcp_server_paperless.cli:main"
    assert scripts["paperless-mcp-http"] == "mcp_server_paperless.cli:main_http"
    assert scripts[f"{DISTRIBUTION}-http"] == "mcp_server_paperless.cli:main_http"
    # PyPI marks this URL verified because it is the Trusted Publisher's repository.
    assert mapping(meta["urls"])["Repository"] == REPOSITORY


def test_the_version_is_read_from_the_renamed_distribution() -> None:
    assert mcp_server_paperless.__version__ == importlib.metadata.version(DISTRIBUTION)


def test_pypi_search_finds_the_distribution_by_its_topics() -> None:
    meta = project()
    keywords = cast("list[str]", meta["keywords"])
    classifiers = cast("list[str]", meta["classifiers"])

    assert {"mcp", "paperless-ngx", "model-context-protocol"} <= set(keywords)
    # The classifier must name the Python the package requires, so PyPI filters list it.
    assert "Programming Language :: Python :: 3.14" in classifiers
    assert not [c for c in classifiers if c.startswith("License ::")]  # PEP 639: license field


def test_the_import_package_is_named_after_the_distribution_without_an_override() -> None:
    """PyPI's unrelated `paperless-mcp` installs `paperless_mcp`, so ours must not (PLMCP-54)."""
    with (ROOT / "pyproject.toml").open("rb") as file:
        uv = mapping(mapping(cast("object", tomllib.load(file)["tool"])).get("uv", {}))

    assert "build-backend" not in uv  # uv_build derives mcp_server_paperless from the name
    assert (ROOT / "src" / "mcp_server_paperless" / "__init__.py").is_file()
    assert not (ROOT / "src" / "paperless_mcp").exists()


@pytest.mark.parametrize("page", ["README.md", "docs/http.md"])
def test_the_docs_start_the_released_server_and_warn_about_the_other_project(page: str) -> None:
    text = (ROOT / page).read_text(encoding="utf-8")

    assert "mcp-server-paperless@latest" in text
    assert "git+https://" not in text
    assert "https://pypi.org/project/paperless-mcp/" in text, "warn about the unrelated project"


@pytest.mark.decision("decision-37")
def test_the_http_page_says_the_old_command_names_must_not_be_run_through_uvx() -> None:
    text = " ".join((ROOT / "docs" / "http.md").read_text(encoding="utf-8").split())

    assert "Do not run `uvx paperless-mcp`" in text


def test_every_action_in_the_pypi_workflow_is_pinned_to_a_commit() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = [str(step["uses"]) for step in all_steps() if "uses" in step]

    assert uses
    for ref in uses:
        assert SHA_PIN.fullmatch(ref), f"{ref} is not pinned to a commit SHA"
        assert re.search(
            rf"^\s*-?\s*uses: {re.escape(ref)} # v\d+\.\d+\.\d+$", text, re.MULTILINE
        ), f"{ref}: needs a version comment"


def test_only_v_tags_publish_and_pull_requests_only_build() -> None:
    trigger = mapping(workflow()["on"])
    pull_request = mapping(trigger["pull_request"])

    assert set(trigger) == {"push", "pull_request"}
    assert mapping(trigger["push"]) == {"tags": ["v*"]}
    assert set(pull_request) == {"branches", "paths"}
    assert pull_request["branches"] == ["main"]
    paths = sequence(pull_request["paths"])
    for needed in ("pyproject.toml", "uv.lock", "src/**", ".github/workflows/pypi.yml"):
        assert needed in paths


def test_only_the_tag_gated_publish_job_gets_an_oidc_token_in_the_pypi_environment() -> None:
    flow = workflow()

    assert set(jobs()) == {"build", "publish"}
    assert flow["permissions"] == {"contents": "read"}
    assert "permissions" not in job("build")  # inherits contents: read
    assert job("publish")["permissions"] == {"contents": "read", "id-token": "write"}
    assert job("publish")["needs"] == "build"
    assert job("publish")["if"] == TAG_GATE
    assert mapping(job("publish")["environment"])["name"] == "pypi"
    assert yaml.safe_dump(flow).count("id-token") == 1


def test_no_check_can_be_skipped_or_soft_failed_and_no_expression_reaches_a_script() -> None:
    for item in [*jobs().values(), *all_steps()]:
        assert "continue-on-error" not in item
    for step in all_steps():
        assert "${{" not in str(step.get("run", "")), f"{label(step)}: pass values through env"


def test_a_tag_publishes_only_a_commit_on_main() -> None:
    check = named("publish", "on main")

    assert "if" not in check
    assert 'git merge-base --is-ancestor "$TAGGED_SHA" origin/main' in str(check["run"])
    assert mapping(check["env"]) == {"TAGGED_SHA": "${{ github.sha }}"}
    assert mapping(steps("publish")[0]["with"])["fetch-depth"] == 0
    assert position("publish", "on main") < position("publish", "gh-action-pypi-publish@")


@pytest.mark.decision("decision-33")
def test_a_tag_build_takes_its_version_from_the_tag() -> None:
    step = named("build", "version from the tag")

    assert step["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert mapping(step["env"]) == {"TAG": "${{ github.ref_name }}"}
    # uv rejects a tag that is no valid version and writes PEP 440 (1.2.3-rc.1 -> 1.2.3rc1);
    # `--` keeps a tag like `v--bump=patch` from being read as an option.
    assert step["run"] == 'uv version --frozen -- "${TAG#v}"'
    assert position("build", "version from the tag") < position("build", "Build the sdist")
    assert not any("name the version" in str(s.get("name", "")) for s in steps("build"))


def test_publish_uploads_the_built_files_by_trusted_publishing_with_attestations() -> None:
    build = named("build", "Build the sdist")
    upload = mapping(steps("build")[-1]["with"])
    download = mapping(steps("publish")[2]["with"])
    publish = steps("publish")[-1]

    assert build["run"] == "uv build --no-sources"
    assert str(steps("build")[-1]["uses"]).startswith("actions/upload-artifact@")
    assert [label(step).split("@")[0] for step in steps("publish")] == [
        "actions/checkout",
        "Require the tagged commit to be on main",
        "actions/download-artifact",
        "pypa/gh-action-pypi-publish",
    ]
    assert download["name"] == upload["name"]
    # No password: Trusted Publishing only. The action attaches attestations by default.
    assert mapping(publish.get("with", {})).keys() <= {"packages-dir", "verbose"}


def test_the_workflow_never_hands_credentials_or_a_shared_cache_to_the_build() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    checkouts = [s for s in all_steps() if str(s.get("uses", "")).startswith("actions/checkout@")]
    uv = [s for s in all_steps() if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    assert len(checkouts) == 2
    assert uv
    for step in checkouts:
        assert mapping(step["with"])["persist-credentials"] is False
    for step in uv:
        # A cache written by a pull request must never feed a release build.
        assert mapping(step["with"])["enable-cache"] is False


def test_the_build_smoke_tests_the_console_script_of_the_built_wheel() -> None:
    smoke = str(named("build", "Smoke test the wheel")["run"])

    assert "uv pip install --quiet --python .smoke dist/*.whl" in smoke
    assert ".smoke/bin/mcp-server-paperless" in smoke
    assert 'grep -q "invalid configuration"' in smoke
    # On a tag this is the version just taken from it (decision-33).
    assert 'print(mcp_server_paperless.__version__)\')" = "$(uv version --short)"' in smoke
    order = [position("build", name) for name in ("Build the sdist", "Smoke", "upload-artifact")]
    assert order == sorted(order)


def test_the_pypi_workflow_is_separate_from_ci_so_it_never_gates_a_merge() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "gh-action-pypi-publish" not in ci
    assert workflow()["name"] == "PyPI"
