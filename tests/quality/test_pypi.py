"""Guards for the PyPI distribution and its workflow (decision-32).

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

import paperless_mcp

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "pypi.yml"
DISTRIBUTION = "mcp-server-paperless"
SHA_PIN = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")
PUBLISH_ACTION = "pypa/gh-action-pypi-publish@"

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


def job(name: str) -> dict[str, object]:
    return mapping(mapping(workflow()["jobs"])[name])


def steps(name: str) -> list[dict[str, object]]:
    return [mapping(step) for step in sequence(job(name)["steps"])]


def runs(name: str) -> str:
    return "\n".join(str(step.get("run", "")) for step in steps(name))


def test_the_distribution_has_a_console_script_of_its_own_name_beside_the_old_ones() -> None:
    meta = project()
    scripts = mapping(meta["scripts"])
    assert meta["name"] == DISTRIBUTION
    # `uvx mcp-server-paperless@latest` runs the script named like the distribution.
    assert scripts[DISTRIBUTION] == scripts["paperless-mcp"] == "paperless_mcp.cli:main"
    assert scripts["paperless-mcp-http"] == "paperless_mcp.cli:main_http"


def test_the_version_is_read_from_the_renamed_distribution() -> None:
    assert paperless_mcp.__version__ == importlib.metadata.version(DISTRIBUTION)


def test_every_action_in_the_pypi_workflow_is_pinned_to_a_commit() -> None:
    uses = [
        str(step["uses"]) for name in ("build", "publish") for step in steps(name) if "uses" in step
    ]
    assert uses
    assert [ref for ref in uses if not SHA_PIN.match(ref)] == []
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(re.search(rf"{re.escape(ref)} # v\d", text) for ref in uses), "version comments"


def test_only_v_tags_publish_and_pull_requests_only_build() -> None:
    triggers = mapping(workflow()["on"])
    assert set(triggers) == {"push", "pull_request"}
    assert mapping(triggers["push"]) == {"tags": ["v*"]}
    condition = str(job("publish")["if"])
    assert "github.event_name == 'push'" in condition
    assert "startsWith(github.ref, 'refs/tags/v')" in condition
    assert job("publish")["needs"] == "build"


def test_only_the_publish_job_gets_an_oidc_token_and_only_in_the_pypi_environment() -> None:
    assert mapping(workflow()["permissions"]) == {"contents": "read"}
    assert "permissions" not in job("build")
    assert mapping(job("publish")["permissions"]) == {"contents": "read", "id-token": "write"}
    assert mapping(job("publish")["environment"])["name"] == "pypi"


def test_a_tag_publishes_only_a_commit_on_main_whose_version_is_the_tag() -> None:
    assert 'git merge-base --is-ancestor "$TAGGED_SHA" origin/main' in runs("publish")
    assert 'test "${TAG#v}" = "$(uv version --short)"' in runs("build")


def test_publish_uploads_the_built_files_by_trusted_publishing_with_attestations() -> None:
    publish = [
        step for step in steps("publish") if str(step.get("uses", "")).startswith(PUBLISH_ACTION)
    ]
    assert len(publish) == 1
    inputs = mapping(publish[0].get("with", {}))
    assert "password" not in inputs, "no API token: Trusted Publishing only"
    assert inputs.get("attestations", True) is True
    assert any("download-artifact" in str(step.get("uses", "")) for step in steps("publish"))
    assert "uv build --no-sources" in runs("build")


def test_the_workflow_never_hands_credentials_or_a_shared_cache_to_the_build() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    for name in ("build", "publish"):
        for step in steps(name):
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert mapping(step["with"])["persist-credentials"] is False
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                # A cache written by a pull request must never feed a release build.
                assert mapping(step["with"])["enable-cache"] is False


def test_the_build_smoke_tests_the_console_script_of_the_built_wheel() -> None:
    assert ".smoke/bin/mcp-server-paperless" in runs("build")
