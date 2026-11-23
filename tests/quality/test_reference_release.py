"""The reference release (decision-43).

One release in the justfile, the E2E stack, the E2E helpers and the vendored docs, and the docs
name it as the minimum.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def pinned() -> str:
    found = re.search(r'^PAPERLESS_VERSION := "(\d+\.\d+\.\d+)"', read("justfile"), re.MULTILINE)
    assert found
    return found[1]


@pytest.mark.decision("decision-43")
def test_the_stack_the_helpers_and_the_vendored_docs_are_the_release_of_the_justfile() -> None:
    version = pinned()
    compose = re.search(
        r"paperless-ngx:(\d+\.\d+\.\d+)@(sha256:[0-9a-f]{64})", read("tests/e2e/compose.yaml")
    )
    upstream = re.fullmatch(
        r"[0-9a-f]{40} v(\d+\.\d+\.\d+) image-digest (sha256:[0-9a-f]{64})\n",
        read("docs/vendor/paperless/UPSTREAM"),
    )
    helper = re.search(r'"PAPERLESS_E2E_VERSION", "([\d.]+)"', read("tests/e2e/helpers.py"))

    assert compose
    assert upstream
    assert helper
    assert (compose[1], upstream[1], helper[1]) == (version, version, version)
    assert compose[2] == upstream[2]


@pytest.mark.decision("decision-43")
@pytest.mark.parametrize("page", ["README.md", "docs/configuration.md", "plugin/README.md"])
def test_the_docs_name_the_reference_release_as_the_minimum(page: str) -> None:
    assert re.search(rf"{re.escape(pinned())} or later|{re.escape(pinned())} and later", read(page))
