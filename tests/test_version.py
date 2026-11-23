import tomllib
from pathlib import Path

import pytest

import mcp_server_paperless

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def test_version_is_the_declared_project_version() -> None:
    match tomllib.loads(PYPROJECT.read_text(encoding="utf-8")):
        case {"project": {"version": str() as declared}}:
            assert mcp_server_paperless.__version__ == declared
        case _:
            pytest.fail("pyproject.toml declares no [project] version")
