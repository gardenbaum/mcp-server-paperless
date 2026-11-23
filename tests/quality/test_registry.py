"""Guards for the MCP Registry listing and its workflow (decision-35).

The files are read as text: nothing here publishes (the ``registry`` workflow does), these tests
only keep the listing in step with the package and the publishing rules from eroding unnoticed.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast, get_args

import pytest
import yaml

from mcp_server_paperless.settings import LogLevel, PaperlessSettings, ServerSettings

if TYPE_CHECKING:
    from pydantic_settings import BaseSettings

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "registry.yml"
SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
PINNED = re.compile(r"[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+")
MARKER = re.compile(r"<!-- mcp-name: (\S+) -->")

# The scripts the workflow must run, word for word: one `|| true` would disarm a check.
ON_MAIN = """\
git fetch --no-tags origin '+refs/heads/main:refs/remotes/origin/main'
git merge-base --is-ancestor "$TAGGED_SHA" origin/main
"""
TAKE_VERSION = """\
uv version --frozen -- "${TAG#v}"
version="$(uv version --short)"
echo "version=$version" >> "$GITHUB_OUTPUT"
"""
WAIT = """\
while [ "$SECONDS" -lt 900 ]; do
  if curl --fail --silent --max-time 10 --output /dev/null \
"https://pypi.org/pypi/mcp-server-paperless/$VERSION/json"; then
    exit 0
  fi
  sleep 10
done
echo "PyPI does not serve mcp-server-paperless $VERSION after 15 minutes" >&2
exit 1
"""
SET_VERSION = """\
jq --arg v "$VERSION" '.version = $v | .packages[].version = $v' server.json > server.tmp
mv server.tmp server.json
"""
VERIFY = """\
set -euo pipefail
os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
archive="mcp-publisher_${os}_${arch}.tar.gz"
base_url="https://github.com/modelcontextprotocol/registry/releases/download/${MCP_PUBLISHER_VERSION}"

curl --fail --location --silent --show-error --output "$archive" "$base_url/$archive"
curl --fail --location --silent --show-error --output "$archive.sigstore.json" \
"$base_url/$archive.sigstore.json"
cosign verify-blob \\
  --bundle "$archive.sigstore.json" \\
  --certificate-identity "https://github.com/modelcontextprotocol/registry/\
.github/workflows/release.yml@refs/tags/${MCP_PUBLISHER_VERSION}" \\
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \\
  "$archive"
tar xzf "$archive" mcp-publisher
rm -f "$archive" "$archive.sigstore.json"
"""

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


def job(name: str) -> dict[str, object]:
    return mapping(mapping(workflow()["jobs"])[name])


def steps(name: str) -> list[dict[str, object]]:
    """The job's steps, with the pin cut off `uses` (the pins have a test of their own)."""
    found = [mapping(step) for step in sequence(job(name)["steps"])]
    return [
        step | {"uses": str(step["uses"]).split("@")[0]} if "uses" in step else step
        for step in found
    ]


def as_text(default: object) -> str | None:
    """A setting's default the way server.json writes it; None when there is none to write."""
    if isinstance(default, tuple):
        return ",".join(cast("tuple[str, ...]", default)) or None
    if isinstance(default, bool):
        return str(default).lower()
    return None if default is None else str(default).removesuffix(".0")


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


def test_the_listing_names_the_defaults_and_choices_of_the_settings() -> None:
    declared = variables()
    for settings in (PaperlessSettings, ServerSettings):
        prefix = settings.model_config.get("env_prefix") or ""
        for field, info in settings.model_fields.items():
            item = declared[f"{prefix}{field}".upper()]
            default = None if info.is_required() else cast("object", info.default)
            # Compared as it stands: a JSON number or boolean is not the schema's string.
            assert item.get("default") == as_text(default), item["name"]
    choices = {name: item["choices"] for name, item in declared.items() if "choices" in item}

    assert choices == {
        "PAPERLESS_MCP_LOG_LEVEL": list(get_args(cast("object", LogLevel.__value__)))
    }


def test_the_listing_offers_nothing_but_the_pypi_package() -> None:
    """No other package index and no remote server can slip in."""
    assert set(load("server.json")) == {
        "$schema",
        "name",
        "title",
        "description",
        "repository",
        "version",
        "packages",
    }
    assert set(package()) == {
        "registryType",
        "identifier",
        "version",
        "runtimeHint",
        "transport",
        "environmentVariables",
    }


def test_only_v_tags_run_the_workflow() -> None:
    flow = workflow()

    # Exact keys: no `env` or `defaults` can change what the scripts below do.
    assert set(flow) == {"name", "on", "permissions", "concurrency", "jobs"}
    assert flow["name"] == "MCP Registry"
    assert mapping(flow["on"]) == {"push": {"tags": ["v*"]}}
    assert flow["permissions"] == {"contents": "read"}
    assert set(mapping(flow["jobs"])) == {"prepare", "publish"}
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "mcp-publisher" not in ci


def test_every_action_is_pinned_to_a_commit_with_a_version_comment() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = cast("list[str]", re.findall(r"uses: (.*)$", text, re.MULTILINE))

    assert len(uses) == 4
    for ref in uses:
        assert PINNED.fullmatch(ref), ref


def test_the_prepare_job_gates_and_computes_the_version_without_an_oidc_token() -> None:
    rest = {key: value for key, value in job("prepare").items() if key != "steps"}

    # Exact: no `if`, no `continue-on-error`, and no `permissions` (it inherits contents: read).
    assert rest == {
        "runs-on": "ubuntu-latest",
        "timeout-minutes": 25,
        "outputs": {"version": "${{ steps.version.outputs.version }}"},
    }


@pytest.mark.decision("decision-33")
def test_the_prepare_job_runs_exactly_these_steps() -> None:
    assert steps("prepare") == [
        {
            "uses": "actions/checkout",
            "with": {"fetch-depth": 0, "persist-credentials": False},
        },
        {
            "name": "Require the tagged commit to be on main",
            "env": {"TAGGED_SHA": "${{ github.sha }}"},
            "run": ON_MAIN,
        },
        {
            "uses": "astral-sh/setup-uv",
            "with": {"python-version": "3.14", "enable-cache": False},
        },
        {
            "name": "Take the version from the tag",
            "id": "version",
            "env": {"TAG": "${{ github.ref_name }}"},
            "run": TAKE_VERSION,
        },
        {
            "name": "Wait until PyPI serves the version",
            "env": {"VERSION": "${{ steps.version.outputs.version }}"},
            "run": WAIT,
        },
    ]


def test_only_the_publish_job_gets_an_oidc_token_and_only_after_prepare() -> None:
    rest = {key: value for key, value in job("publish").items() if key != "steps"}
    version = str(mapping(rest.pop("env"))["MCP_PUBLISHER_VERSION"])

    assert re.fullmatch(r"v\d+\.\d+\.\d+", version)
    assert set(mapping(job("publish")["env"])) == {"MCP_PUBLISHER_VERSION"}
    # Exact: `needs` without an `if` skips the job when a check of `prepare` fails.
    assert rest == {
        "needs": "prepare",
        "runs-on": "ubuntu-latest",
        "timeout-minutes": 10,
        "permissions": {"contents": "read", "id-token": "write"},
    }


def test_the_publish_job_runs_exactly_these_steps() -> None:
    assert steps("publish") == [
        {"uses": "actions/checkout", "with": {"persist-credentials": False}},
        {
            "name": "Set the listing version",
            "env": {"VERSION": "${{ needs.prepare.outputs.version }}"},
            "run": SET_VERSION,
        },
        {"uses": "sigstore/cosign-installer"},
        {"name": "Download and verify mcp-publisher", "run": VERIFY},
        {"name": "Log in to the MCP Registry", "run": "./mcp-publisher login github-oidc"},
        {"name": "Publish the listing", "run": "./mcp-publisher publish"},
    ]


def test_no_expression_reaches_a_script() -> None:
    for script in (ON_MAIN, TAKE_VERSION, WAIT, SET_VERSION, VERIFY):
        assert "${{" not in script
