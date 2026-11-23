"""Guards for the container image and its workflow (decision-28).

The files are read as text: nothing here builds an image (``just image-smoke`` and the ``image``
workflow do), these tests only keep the hardening rules from eroding unnoticed.
"""

import re
from pathlib import Path
from typing import cast

import pytest
import yaml

ROOT = Path(__file__).parents[2]
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"

FROM = re.compile(r"^FROM\s+(?P<ref>\S+)(?:\s+AS\s+(?P<name>\S+))?\s*$", re.MULTILINE)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
SHA_PIN = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")
COPY_SOURCES = {
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "src",
    "/app/.venv",  # --from=builder
}


def mapping(value: object) -> dict[str, object]:
    """The value as a string-keyed mapping; fails the test when it is not one."""
    assert isinstance(value, dict)
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def sequence(value: object) -> list[object]:
    """The value as a list; fails the test when it is not one."""
    assert isinstance(value, list)
    return list(cast("list[object]", value))


def workflow() -> dict[str, object]:
    """The parsed image workflow; YAML 1.1 reads the key ``on`` as the boolean True."""
    loaded = mapping(cast("object", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))))
    loaded["on"] = loaded.pop("True", loaded.get("on"))
    return loaded


def job(name: str) -> dict[str, object]:
    return mapping(mapping(workflow()["jobs"])[name])


def steps(name: str) -> list[dict[str, object]]:
    return [mapping(step) for step in sequence(job(name)["steps"])]


@pytest.mark.decision("decision-28")
def test_every_base_image_is_pinned_by_digest_with_a_renovate_comment() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    stages = {found["name"] for found in FROM.finditer(text) if found["name"]}
    external = [found for found in FROM.finditer(text) if found["ref"] not in stages]

    assert len(external) >= 2  # the uv stage and the python base
    for found in external:
        assert DIGEST.search(found["ref"]), f"{found['ref']} is not pinned by digest"
        assert ":latest" not in found["ref"]
        before = text[: found.start()].rstrip().splitlines()[-1]
        assert before.startswith("# renovate: datasource=docker depName="), found["ref"]


@pytest.mark.decision("decision-28")
def test_the_image_runs_as_a_numeric_non_root_user() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    users = [found[0].split()[1] for found in re.finditer(r"^USER\s+\S+\s*$", text, re.MULTILINE)]

    assert users, "the runtime stage must set USER"
    uid = users[-1].split(":")[0]
    assert uid.isdigit(), "USER is numeric so Kubernetes runAsNonRoot can verify it"
    assert int(uid) >= 1000
    # The last stage is the runtime: nothing after USER may switch back to root.
    assert text.rfind("USER") > text.rfind("FROM")


@pytest.mark.decision("decision-28")
def test_the_image_runs_the_http_entrypoint_and_probes_the_port_without_a_route() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert re.search(r'^ENTRYPOINT \["mcp-server-paperless-http"\]$', text, re.MULTILINE)
    assert "PAPERLESS_MCP_HTTP_HOST=0.0.0.0" in text
    health = re.search(r"^HEALTHCHECK (?:.*\\\n)*.*$", text, re.MULTILINE)
    assert health, "the image needs a HEALTHCHECK"
    # decision-27: no health route. The probe is a TCP connect, not an HTTP request.
    assert "socket.create_connection" in health[0]
    assert "http" not in health[0].lower().replace("paperless_mcp_http_port", "")


@pytest.mark.decision("decision-28")
def test_the_image_bakes_in_no_credentials_and_no_unauthenticated_mode() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    env_lines = " ".join(
        found[0] for found in re.finditer(r"^(?:ENV|ARG)\s.*$", text, re.MULTILINE)
    )

    assert "AUTH_TOKENS" not in env_lines
    assert "PAPERLESS_TOKEN" not in env_lines
    assert "ALLOW_UNAUTHENTICATED_LOOPBACK" not in text
    assert "COPY . " not in text, "copy named paths so .env and .git cannot end up in a layer"


@pytest.mark.decision("decision-28")
def test_dockerignore_keeps_secrets_and_history_out_of_the_build_context() -> None:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    wanted = (".git", ".env", ".env.*", ".venv", "tests", "docs", "backlog")
    wanted += ("**/.env", "**/.env.*", "**/__pycache__", "**/*.py[cod]")
    for entry in wanted:
        assert entry in lines


@pytest.mark.decision("decision-28")
def test_every_action_in_the_image_workflow_is_pinned_to_a_commit() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = [
        str(step["uses"]) for name in ("build", "publish") for step in steps(name) if "uses" in step
    ]

    assert uses
    for ref in uses:
        assert SHA_PIN.fullmatch(ref), f"{ref} is not pinned to a commit SHA"
        assert re.search(
            rf"^\s*-?\s*uses: {re.escape(ref)} # v\d+\.\d+\.\d+$", text, re.MULTILINE
        ), f"{ref}: needs a version comment"


@pytest.mark.decision("decision-28")
def test_the_workflow_never_runs_untrusted_code_with_privileges() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    flow = workflow()

    assert set(mapping(flow["on"])) == {"push", "pull_request"}
    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    assert flow["permissions"] == {"contents": "read"}
    for name in ("build", "publish"):
        checkout = steps(name)[0]
        assert str(checkout["uses"]).startswith("actions/checkout@")
        assert mapping(checkout["with"])["persist-credentials"] is False


@pytest.mark.decision("decision-28")
def test_only_the_tag_gated_publish_job_may_write_packages_or_push() -> None:
    flow = workflow()
    jobs = mapping(flow["jobs"])

    assert set(jobs) == {"build", "publish"}
    assert "permissions" not in job("build")  # inherits contents: read
    assert job("publish")["permissions"] == {"contents": "read", "packages": "write"}
    assert job("publish")["needs"] == "build"
    assert job("publish")["if"] == (
        "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
    )
    build_text = yaml.safe_dump(job("build"))
    assert "push: true" not in build_text
    assert "login-action" not in build_text
    for step in steps("publish"):
        if str(step.get("uses", "")).startswith("docker/build-push-action@"):
            options = mapping(step["with"])
            assert "cache-to" not in options, "a tag build must not write the shared cache"
            assert options["provenance"] == "mode=max"
            assert options["sbom"] is True
    assert yaml.safe_dump(flow).count("packages") == 1


@pytest.mark.decision("decision-28")
def test_publish_scans_and_smoke_tests_the_digest_it_pushed_before_tagging() -> None:
    publish = steps("publish")
    order = [str(step.get("uses") or step.get("name")) for step in publish]
    text = yaml.safe_dump(publish)

    def index(needle: str) -> int:
        return next(i for i, item in enumerate(order) if needle in item)

    assert index("build-push-action") < index("trivy-action") < index("Smoke") < index("Tag")
    assert "push-by-digest=true" in text
    assert "imagetools create" in text
    assert "@${{ steps.push.outputs.digest }}" in text


@pytest.mark.decision("decision-28")
def test_a_tag_publishes_only_a_commit_that_is_on_main() -> None:
    check = next(step for step in steps("publish") if "main" in str(step.get("name", "")))

    assert "merge-base --is-ancestor" in str(check["run"])
    assert "origin/main" in str(check["run"])
    assert mapping(check["env"]) == {"TAGGED_SHA": "${{ github.sha }}"}
    assert "${{" not in str(check["run"]), "no expression inside the script"
    checkout = mapping(steps("publish")[0]["with"])
    assert checkout["fetch-depth"] == 0


@pytest.mark.decision("decision-28")
def test_tags_trigger_only_v_tags_and_pull_requests_only_build() -> None:
    trigger = mapping(workflow()["on"])
    push = mapping(trigger["push"])
    pull_request = mapping(trigger["pull_request"])

    assert push["tags"] == ["v*"]
    assert push["branches"] == ["main"]
    assert set(pull_request) == {"branches", "paths"}
    assert pull_request["branches"] == ["main"]
    paths = sequence(pull_request["paths"])
    for needed in ("Dockerfile", ".dockerignore", ".trivyignore", "uv.lock", "src/**"):
        assert needed in paths


@pytest.mark.decision("decision-28")
def test_the_scan_fails_on_high_and_critical_with_a_reviewed_ignore_file() -> None:
    for name in ("build", "publish"):
        scans = [
            mapping(step["with"])
            for step in steps(name)
            if str(step.get("uses", "")).startswith("aquasecurity/trivy-action@")
        ]
        assert len(scans) == 1, name
        assert scans[0]["severity"] == "HIGH,CRITICAL"
        assert scans[0]["exit-code"] == "1"
        assert scans[0]["trivyignores"] == ".trivyignore"
    assert (ROOT / ".trivyignore").is_file()


@pytest.mark.decision("decision-28")
def test_the_image_workflow_is_separate_from_ci_so_it_never_gates_a_merge() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "docker/build-push-action" not in ci
    assert workflow()["name"] == "Image"


@pytest.mark.decision("decision-28")
def test_the_runtime_stage_removes_pip_and_upgrades_os_packages() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime = text[text.rindex("AS runtime") :]

    assert "apt-get upgrade -y --no-install-recommends" in runtime
    assert "rm -rf /var/lib/apt/lists/*" in runtime
    for leftover in ("site-packages/pip*", "/ensurepip", "/usr/local/bin/pip"):
        assert leftover in runtime, f"{leftover} is not removed from the runtime image"
    assert "pip install" not in runtime


@pytest.mark.decision("decision-28")
def test_build_arguments_come_last_so_a_tag_build_reuses_the_scanned_layers() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime = text[text.rindex("AS runtime") :]
    first_arg = runtime.index("\nARG ")

    assert "RUN" not in runtime[first_arg:], "an ARG before a RUN changes that layer's cache key"
    assert "COPY" not in runtime[first_arg:]


@pytest.mark.decision("decision-28")
def test_the_dockerfile_has_no_floating_frontend_and_copies_only_allowed_sources() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    for found in re.finditer(r"^# syntax=(\S+)", text, re.MULTILINE):
        assert DIGEST.search(found[1]), "a syntax directive must be pinned by digest"
    sources: set[str] = set()
    for line in re.finditer(r"^(?:COPY|ADD)\s+(?P<args>.*?)(?<!\\)$", text, re.MULTILINE):
        words = [w for w in line["args"].split() if not w.startswith("--")]
        assert not re.match(r"^ADD\b", line[0]), "ADD can fetch URLs; use COPY"
        sources.update(words[:-1])
    assert sources
    assert sources <= COPY_SOURCES, sources - COPY_SOURCES
