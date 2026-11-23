"""Only `domain/errors.py` raises the confirmation refusal: every service asks for it (decision-44).

A service that raised it itself could not be let past it by the user's answer in the dialog.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.decision("decision-44")

SOURCES = Path(__file__).parents[2] / "src" / "mcp_server_paperless"
HOME = SOURCES / "domain" / "errors.py"


def own_refusals(source: str) -> list[int]:
    """The lines that build a ConfirmationRequired instead of calling `require`."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and "ConfirmationRequired" in ast.unparse(node.func)
        and ast.unparse(node.func) != "ConfirmationRequired.require"
    ]


def required(source: str) -> int:
    return source.count("ConfirmationRequired.require(")


def test_no_module_but_the_domain_builds_the_refusal() -> None:
    modules = [path for path in SOURCES.rglob("*.py") if path != HOME]

    offending = {p.name: own_refusals(p.read_text(encoding="utf-8")) for p in modules}

    assert {name: lines for name, lines in offending.items() if lines} == {}
    # The check has something to check.
    assert sum(required(p.read_text(encoding="utf-8")) for p in modules) >= 35


def test_self_test_every_other_way_to_refuse_is_found() -> None:
    source = """
ConfirmationRequired.require("x")
raise ConfirmationRequired.for_effect("x")
raise ConfirmationRequired("x")
raise errors.ConfirmationRequired("x")
"""
    assert own_refusals(source) == [3, 4, 5]
