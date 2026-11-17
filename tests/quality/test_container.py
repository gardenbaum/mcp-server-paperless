"""Guards for the container image and its workflow (decision-28).

The files are read as text: nothing here builds an image (``just image-smoke`` and the ``image``
workflow do), these tests only keep the hardening rules from eroding unnoticed.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"

FROM = re.compile(r"^FROM\s+(?P<ref>\S+)(?:\s+AS\s+(?P<name>\S+))?\s*$", re.MULTILINE)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
USES = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>\S+)(?P<rest>.*)$", re.MULTILINE)
SHA_PIN = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")
JOB = re.compile(r"^  (?P<name>[\w-]+):\s*$", re.MULTILINE)


def jobs(workflow: str) -> dict[str, str]:
    """Map job name to its text (the workflow below the ``jobs:`` key)."""
    body = workflow.split("\njobs:\n", 1)[1]
    starts = list(JOB.finditer(body))
    return {
        found["name"]: body[found.end() : starts[i + 1].start() if i + 1 < len(starts) else None]
        for i, found in enumerate(starts)
    }


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

    assert re.search(r'^ENTRYPOINT \["paperless-mcp-http"\]$', text, re.MULTILINE)
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

    for entry in (".git", ".env", ".env.*", ".venv", "tests", "docs", "backlog"):
        assert entry in lines


@pytest.mark.decision("decision-28")
def test_every_action_in_the_image_workflow_is_pinned_to_a_commit() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    found = list(USES.finditer(text))

    assert found
    for use in found:
        assert SHA_PIN.fullmatch(use["ref"]), f"{use['ref']} is not pinned to a commit SHA"
        comment = re.fullmatch(r"\s+# v\d+\.\d+\.\d+\s*", use["rest"])
        assert comment, f"{use['ref']}: needs a version comment"


@pytest.mark.decision("decision-28")
def test_the_workflow_never_runs_untrusted_code_with_privileges() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    assert re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE)
    assert "persist-credentials: false" in text


@pytest.mark.decision("decision-28")
def test_only_the_tag_gated_publish_job_may_write_packages_or_push() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    by_name = jobs(text)

    assert set(by_name) == {"build", "publish"}
    assert "packages: write" not in by_name["build"]
    assert "push: true" not in by_name["build"]
    assert "login-action" not in by_name["build"]
    publish = by_name["publish"]
    assert "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in publish
    assert "needs: build" in publish
    assert "packages: write" in publish
    assert text.count("packages: write") == 1
    assert "provenance: mode=max" in publish
    assert "sbom: true" in publish


@pytest.mark.decision("decision-28")
def test_tags_trigger_only_v_tags_and_pull_requests_do_not_publish() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    triggers = text.split("\npermissions:", 1)[0]

    assert 'tags: ["v*"]' in triggers
    assert "pull_request:" in triggers
    assert "branches: [main]" in triggers


@pytest.mark.decision("decision-28")
def test_the_scan_fails_on_high_and_critical_with_a_reviewed_ignore_file() -> None:
    build = jobs(WORKFLOW.read_text(encoding="utf-8"))["build"]

    assert 'severity: "HIGH,CRITICAL"' in build
    assert 'exit-code: "1"' in build
    assert "trivyignores: .trivyignore" in build
    assert (ROOT / ".trivyignore").is_file()


@pytest.mark.decision("decision-28")
def test_the_image_workflow_is_separate_from_ci_so_it_never_gates_a_merge() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "docker/build-push-action" not in ci
    assert "name: Image" in WORKFLOW.read_text(encoding="utf-8")


@pytest.mark.decision("decision-28")
def test_the_runtime_stage_removes_pip_and_upgrades_os_packages() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime = text[text.rindex("AS runtime") :]

    assert "apt-get upgrade -y --no-install-recommends" in runtime
    assert "rm -rf /var/lib/apt/lists/*" in runtime
    for leftover in ("site-packages/pip*", "/ensurepip", "/usr/local/bin/pip"):
        assert leftover in runtime, f"{leftover} is not removed from the runtime image"
    assert "pip install" not in runtime
