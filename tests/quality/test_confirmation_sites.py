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


def unexplained(source: str) -> list[int]:
    """The lines of `require` calls without a scope, or with None and no comment that says why."""
    lines = source.splitlines()
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call) and ast.unparse(node.func) == "ConfirmationRequired.require"
        ):
            continue
        scopes = [k.value for k in node.keywords if k.arg == "scope"]
        if not scopes:
            found.append(node.lineno)
        elif isinstance(scopes[0], ast.Constant):
            around = lines[node.lineno - 2 : node.end_lineno]
            if not any("# " in line for line in around):
                found.append(node.lineno)
    return found


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


def test_every_confirmation_says_what_it_is_about_or_why_its_text_is_enough() -> None:
    modules = [path for path in SOURCES.rglob("*.py") if path != HOME]

    found = {p.name: unexplained(p.read_text(encoding="utf-8")) for p in modules}

    assert {name: lines for name, lines in found.items() if lines} == {}


def test_self_test_a_missing_or_unexplained_scope_is_found() -> None:
    source = """
ConfirmationRequired.require("x")

ConfirmationRequired.require("x", scope=None)

# The text names everything.
ConfirmationRequired.require("x", scope=None)

ConfirmationRequired.require("x", scope=[1])
"""
    assert unexplained(source) == [2, 4]
