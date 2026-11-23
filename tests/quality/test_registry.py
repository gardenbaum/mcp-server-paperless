"""Guards for the MCP Registry listing and its workflow (decision-35).

The files are read as text: nothing here publishes (the ``registry`` workflow does), these tests
only keep the listing in step with the package and the publishing rules from eroding unnoticed.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import yaml

from mcp_server_paperless.settings import PaperlessSettings, ServerSettings

if TYPE_CHECKING:
    from pydantic_settings import BaseSettings

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "registry.yml"
SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
SHA_PIN = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")
MARKER = re.compile(r"<!-- mcp-name: (\S+) -->")

pytestmark = pytest.mark.decision("decision-35")


def mapping(value: object) -> dict[str, object]:
    """The value as a string-keyed mapping; fails the test when it is not one."""
    assert isinstance(value, dict)
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def sequence(value: object) -> list[object]:
    """The value as a list; fails the test when it is not one."""
    assert isinstance(value, list)
    return list(cast("list[object]", value))


def load(name: str) -> dict[str, object]:
    return mapping(cast("object", json.loads((ROOT / name).read_text(encoding="utf-8"))))


def package() -> dict[str, object]:
    packages = sequence(load("server.json")["packages"])
    assert len(packages) == 1
    return mapping(packages[0])


def variables() -> dict[str, dict[str, object]]:
    found = [mapping(item) for item in sequence(package()["environmentVariables"])]
    return {str(item["name"]): item for item in found}


def settings_variables(settings: type[BaseSettings]) -> dict[str, bool]:
    """Each variable the settings read, with whether it is required."""
    prefix = settings.model_config.get("env_prefix") or ""
    return {
        f"{prefix}{name}".upper(): field.is_required()
        for name, field in settings.model_fields.items()
    }


def workflow() -> dict[str, object]:
    """The parsed workflow; YAML 1.1 reads the key ``on`` as the boolean True."""
    loaded = mapping(cast("object", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))))
    loaded["on"] = loaded.pop("True", loaded.get("on"))
    return loaded


def job() -> dict[str, object]:
    jobs = mapping(workflow()["jobs"])
    assert set(jobs) == {"publish"}
    return mapping(jobs["publish"])


def steps() -> list[dict[str, object]]:
    return [mapping(step) for step in sequence(job()["steps"])]


def label(step: dict[str, object]) -> str:
    return str(step.get("uses") or step.get("name"))


def named(text: str) -> dict[str, object]:
    found = [step for step in steps() if text in str(step.get("name", ""))]
    assert len(found) == 1, f"one step named like {text!r}"
    return found[0]


def position(needle: str) -> int:
    return next(i for i, step in enumerate(steps()) if needle in label(step))


def test_the_listing_is_named_like_the_readme_marker_pypi_checks() -> None:
    """The registry accepts a PyPI package only when its README carries the server name."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    listing = load("server.json")

    assert MARKER.findall(readme) == [listing["name"]]
    assert listing["name"] == "io.github.gardenbaum/mcp-server-paperless"
    assert listing["$schema"] == SCHEMA
    assert 0 < len(str(listing["description"])) <= 100  # the schema's limit
    assert mapping(listing["repository"]) == {
        "url": "https://github.com/gardenbaum/mcp-server-paperless",
        "source": "github",
    }


def test_the_listing_points_at_the_pypi_package_run_by_uvx_over_stdio() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        name = mapping(cast("object", tomllib.load(file)["project"]))["name"]
    entry = package()

    assert entry["registryType"] == "pypi"
    assert entry["identifier"] == name
    assert entry["runtimeHint"] == "uvx"
    assert mapping(entry["transport"]) == {"type": "stdio"}
    # A placeholder: the workflow writes the tag's version into both (decision-33).
    assert entry["version"] == load("server.json")["version"]


def test_the_listing_declares_exactly_the_variables_the_stdio_server_reads() -> None:
    known = settings_variables(PaperlessSettings) | settings_variables(ServerSettings)
    declared = variables()

    assert set(declared) == set(known)
    for name, item in declared.items():
        assert item.get("isRequired", False) is known[name], name
        assert item.get("description"), name
    assert [name for name, item in declared.items() if item.get("isSecret")] == ["PAPERLESS_TOKEN"]


def test_glama_can_claim_the_listing() -> None:
    assert load("glama.json") == {
        "$schema": "https://glama.ai/mcp/schemas/server.json",
        "maintainers": ["gardenbaum"],
    }


def test_only_v_tags_run_the_workflow_and_only_its_job_gets_an_oidc_token() -> None:
    flow = workflow()
    text = WORKFLOW.read_text(encoding="utf-8")

    assert flow["name"] == "MCP Registry"
    assert mapping(flow["on"]) == {"push": {"tags": ["v*"]}}
    assert flow["permissions"] == {"contents": "read"}
    assert job()["permissions"] == {"contents": "read", "id-token": "write"}
    assert yaml.safe_dump(flow).count("id-token") == 1
    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    assert "registry" not in (ROOT / ".github" / "workflows" / "ci.yml").read_text().lower()


def test_every_action_is_pinned_and_nothing_is_soft_failed_or_interpolated() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = [str(step["uses"]) for step in steps() if "uses" in step]

    assert uses
    for ref in uses:
        assert SHA_PIN.fullmatch(ref), f"{ref} is not pinned to a commit SHA"
        assert re.search(
            rf"^\s*-?\s*uses: {re.escape(ref)} # v\d+\.\d+\.\d+$", text, re.MULTILINE
        ), f"{ref}: needs a version comment"
    for item in [job(), *steps()]:
        assert "continue-on-error" not in item
    for step in steps():
        assert "${{" not in str(step.get("run", "")), f"{label(step)}: pass values through env"
    for step in steps():
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert mapping(step["with"])["persist-credentials"] is False
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
            assert mapping(step["with"])["enable-cache"] is False


def test_a_tag_publishes_only_a_commit_on_main() -> None:
    check = named("on main")

    assert "if" not in check
    assert 'git merge-base --is-ancestor "$TAGGED_SHA" origin/main' in str(check["run"])
    assert mapping(check["env"]) == {"TAGGED_SHA": "${{ github.sha }}"}
    assert mapping(steps()[0]["with"])["fetch-depth"] == 0
    assert position("on main") < position("Publish the listing")


@pytest.mark.decision("decision-33")
def test_the_listing_takes_the_pep_440_version_from_the_tag_like_pypi() -> None:
    step = named("version from the tag")
    script = str(step["run"])

    assert mapping(step["env"]) == {"TAG": "${{ github.ref_name }}"}
    assert 'uv version --frozen -- "${TAG#v}"' in script
    assert ".version = $v | .packages[].version = $v" in script
    assert position("version from the tag") < position("Wait until PyPI")


def test_the_listing_is_published_only_once_pypi_serves_the_version() -> None:
    script = str(named("Wait until PyPI")["run"])

    assert "https://pypi.org/pypi/mcp-server-paperless/$VERSION/json" in script
    assert "exit 1" in script  # gives up instead of publishing a version PyPI does not have
    assert position("Wait until PyPI") < position("Log in to the MCP Registry")


def test_a_pinned_mcp_publisher_verified_by_cosign_publishes_by_github_oidc() -> None:
    version = str(mapping(job()["env"])["MCP_PUBLISHER_VERSION"])
    verify = str(named("Download and verify mcp-publisher")["run"])

    assert re.fullmatch(r"v\d+\.\d+\.\d+", version)
    assert "releases/latest" not in WORKFLOW.read_text(encoding="utf-8")
    assert "cosign verify-blob" in verify
    assert (
        '--certificate-identity "https://github.com/modelcontextprotocol/registry/.github/'
        'workflows/release.yml@refs/tags/${MCP_PUBLISHER_VERSION}"'
    ) in verify
    assert '--certificate-oidc-issuer "https://token.actions.githubusercontent.com"' in verify
    assert named("Log in to the MCP Registry")["run"] == "./mcp-publisher login github-oidc"
    assert named("Publish the listing")["run"] == "./mcp-publisher publish"
    order = [
        position(name)
        for name in (
            "sigstore/cosign-installer@",
            "Download and verify",
            "Log in to the MCP Registry",
            "Publish the listing",
        )
    ]
    assert order == sorted(order)
