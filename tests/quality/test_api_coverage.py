"""The coverage of the Paperless API, endpoint by endpoint, is derived and guarded (PLMCP-91).

`docs/api-coverage.md` is generated (`just docs-coverage`, this module with `--write-docs`):
for every operation of the vendored schema the tools that cover it, and for the rest the open
task or the owner-approved exclusion; for the covered ones the query parameters, body fields
(nested ones too) and enum values no tool sends. Coverage is derived: the tool tests record which
port method each tool calls with which arguments, and the real adapters answer what request that
is (`tests/quality/derive.py`). `paperless_api_get` (the raw reader) never counts. The first half of
this file proves the checks on synthetic violations, so they cannot pass by looking at nothing.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

import observation
import pytest
from adapter import schema_guard
from observation import Observation, Operation

from quality.api_coverage import (
    Approval,
    Entry,
    Fields,
    Plumbing,
    Usage,
    backlog_statuses,
    covering,
    load_entries,
    missing_scenarios,
    problems,
    render,
    schema_fields,
    undeclared_problems,
)
from quality.derive import derive

if TYPE_CHECKING:
    from collections.abc import Mapping

NONE = frozenset[str]()
GET_ONE: Operation = ("GET", "/api/things/{id}/")
PATCH_ONE: Operation = ("PATCH", "/api/things/{id}/")
SCHEMA: dict[Operation, Fields] = {
    GET_ONE: Fields(query=frozenset({"fields", "page"}), body=NONE),
    PATCH_ONE: Fields(query=NONE, body=frozenset({"name", "color"})),
}


def names(*given: str) -> frozenset[str]:
    return frozenset(given)


def entry(
    operation: Operation,
    *,
    fields: frozenset[str] | None = None,
    task: str | None = None,
    approval: Approval | None = None,
    reason: str | None = None,
) -> Entry:
    return Entry(operation=operation, fields=fields, task=task, approval=approval, reason=reason)


def used(*keys: str, tool: str = "get_thing") -> Usage:
    """A tool that sends the fields `keys` (`query:page`, `body:name`, `body:color=red`)."""
    return Usage(tools=names(tool), used={key: names(tool) for key in keys})


def check(  # noqa: PLR0913, PLR0917 -- one keyword per input a case varies
    covered: Mapping[Operation, Usage],
    entries: list[Entry],
    statuses: Mapping[str, str] | None = None,
    root: Path | None = None,
    internal: Mapping[Operation, frozenset[str]] | None = None,
    schema: Mapping[Operation, Fields] | None = None,
    plumbing: Mapping[Operation, Plumbing] | None = None,
) -> list[str]:
    known = {"PLMCP-83": "To Do"} if statuses is None else statuses
    return problems(
        schema or SCHEMA,
        covered,
        entries,
        known,
        root=root or Path(),
        internal=internal or {},
        plumbing=plumbing or {},
    )


FULL: dict[Operation, Usage] = {
    GET_ONE: used("query:fields", "query:page"),
    PATCH_ONE: used("body:name", "body:color", tool="update_thing"),
}
ONLY_GET: dict[Operation, Usage] = {GET_ONE: FULL[GET_ONE]}


def test_a_fully_covered_schema_has_no_problems() -> None:
    assert check(FULL, []) == []


def test_an_endpoint_that_is_neither_covered_nor_listed_is_a_problem() -> None:
    found = check(ONLY_GET, [])

    assert len(found) == 1
    assert "PATCH /api/things/{id}/" in found[0]


def test_a_new_endpoint_after_a_schema_refresh_is_a_problem() -> None:
    refreshed = {**SCHEMA, ("GET", "/api/new/"): Fields(NONE, NONE)}

    found = check(FULL, [], schema=refreshed)

    assert any("GET /api/new/" in line for line in found)


def test_an_open_entry_with_a_task_that_is_to_do_lists_the_endpoint() -> None:
    assert check(ONLY_GET, [entry(PATCH_ONE, task="PLMCP-83")]) == []


@pytest.mark.parametrize(
    ("statuses", "word"),
    [
        ({}, "does not exist"),
        ({"PLMCP-83": "Done"}, "Done"),
        ({"PLMCP-83": "Archived"}, "Archived"),
    ],
    ids=["missing", "done", "archived"],
)
def test_an_open_entry_fails_when_its_task_is_missing_or_done(
    statuses: dict[str, str], word: str
) -> None:
    found = check(ONLY_GET, [entry(PATCH_ONE, task="PLMCP-83")], statuses)

    assert len(found) == 1
    assert word in found[0]


def test_an_entry_for_an_endpoint_that_is_covered_is_stale() -> None:
    found = check(FULL, [entry(PATCH_ONE, task="PLMCP-83")])

    assert len(found) == 1
    assert "already covered" in found[0]


def test_an_entry_for_an_operation_the_schema_lacks_is_stale() -> None:
    found = check(FULL, [entry(("GET", "/api/gone/"), task="PLMCP-83")])

    assert len(found) == 1
    assert "/api/gone/" in found[0]


def test_an_entry_with_neither_task_nor_approval_is_a_problem() -> None:
    assert len(check(ONLY_GET, [entry(PATCH_ONE)])) == 1


def test_an_endpoint_listed_twice_is_a_problem() -> None:
    twice = [entry(PATCH_ONE, task="PLMCP-83"), entry(PATCH_ONE, task="PLMCP-83")]

    found = check(ONLY_GET, twice)

    assert len(found) == 1
    assert "twice" in found[0]


def test_an_endpoint_both_open_and_excluded_is_a_problem(tmp_path: Path) -> None:
    approval = write_source(tmp_path, APPROVED)
    both = [entry(PATCH_ONE, task="PLMCP-83"), entry(PATCH_ONE, approval=approval)]

    found = check(ONLY_GET, both, root=tmp_path)

    assert len(found) == 1
    assert "twice" in found[0]


def test_unused_fields_of_a_covered_endpoint_need_an_entry() -> None:
    partly = {**FULL, GET_ONE: used("query:fields")}

    found = check(partly, [])

    assert len(found) == 1
    assert "query:page" in found[0]


def test_unused_body_fields_need_an_entry_too() -> None:
    partly = {**FULL, PATCH_ONE: used("body:name", tool="update_thing")}

    found = check(partly, [])

    assert len(found) == 1
    assert "body:color" in found[0]


def test_unused_fields_are_listed_by_name_and_all_of_them_must_be() -> None:
    partly = {**FULL, GET_ONE: used()}
    both = names("query:fields", "query:page")

    assert check(partly, [entry(GET_ONE, fields=both, task="PLMCP-83")]) == []
    assert len(check(partly, [entry(GET_ONE, fields=names("query:page"), task="PLMCP-83")])) == 1


def test_a_field_entry_for_a_field_a_tool_uses_is_stale() -> None:
    found = check(FULL, [entry(GET_ONE, fields=names("query:page"), task="PLMCP-83")])

    assert len(found) == 1
    assert "query:page" in found[0]


def test_a_field_entry_that_names_no_field_of_the_endpoint_is_stale() -> None:
    partly = {**FULL, GET_ONE: used("query:page")}

    found = check(
        partly, [entry(GET_ONE, fields=names("query:nope", "query:fields"), task="PLMCP-83")]
    )

    assert len(found) == 1
    assert "query:nope" in found[0]


def test_a_field_entry_needs_its_task_too() -> None:
    partly = {**FULL, GET_ONE: used()}
    both = names("query:fields", "query:page")

    assert len(check(partly, [entry(GET_ONE, fields=both, task="PLMCP-1")], {})) == 1


def test_a_field_listed_in_two_entries_is_a_problem() -> None:
    partly = {**FULL, GET_ONE: used()}
    two = [
        entry(GET_ONE, fields=names("query:fields", "query:page"), task="PLMCP-83"),
        entry(GET_ONE, fields=names("query:page"), task="PLMCP-83"),
    ]

    found = check(partly, two)

    assert len(found) == 1
    assert "twice" in found[0]


def test_field_entries_of_an_endpoint_no_tool_covers_are_a_problem() -> None:
    found = check(
        ONLY_GET,
        [
            entry(PATCH_ONE, task="PLMCP-83"),
            entry(PATCH_ONE, fields=names("body:name"), task="PLMCP-83"),
        ],
    )

    assert len(found) == 1
    assert "not covered" in found[0]


# Nested fields and enum values (PLMCP-79, 80, 81, 87, 88 and 89 need them).
NESTED: Operation = ("POST", "/api/nested/")
NESTED_SCHEMA: dict[Operation, Fields] = {
    NESTED: Fields(
        query=NONE,
        body=names("method", "triggers", "triggers[].type"),
        enums={"body:method": names("rotate", "merge"), "body:triggers[].type": names("1", "2")},
    )
}


def test_a_nested_field_no_tool_sends_needs_an_entry() -> None:
    sent = {
        NESTED: used("body:method", "body:method=rotate", "body:triggers", "body:triggers[].type=1")
    }

    found = check(sent, [], schema=NESTED_SCHEMA)

    assert sorted(found) == [
        "POST /api/nested/: body:method=merge is used by no tool and not listed",
        "POST /api/nested/: body:triggers[].type is used by no tool and not listed",
    ]


def test_every_enum_value_a_tool_sends_is_credited() -> None:
    everything = used(
        "body:method",
        "body:method=rotate",
        "body:method=merge",
        "body:triggers",
        "body:triggers[].type",
        "body:triggers[].type=1",
        "body:triggers[].type=2",
    )

    assert check({NESTED: everything}, [], schema=NESTED_SCHEMA) == []


def test_unused_enum_values_are_listed_like_any_other_field() -> None:
    sent = {
        NESTED: used(
            "body:method",
            "body:method=rotate",
            "body:triggers",
            "body:triggers[].type",
            "body:triggers[].type=1",
            "body:triggers[].type=2",
        )
    }
    listed = entry(NESTED, fields=names("body:method=merge"), task="PLMCP-83", reason="no tool")

    assert check(sent, [listed], schema=NESTED_SCHEMA) == []


def test_when_a_parent_is_open_only_the_parent_is_listed() -> None:
    sent = {NESTED: used("body:method", "body:method=rotate", "body:method=merge")}

    assert (
        check(
            sent,
            [entry(NESTED, fields=names("body:triggers"), task="PLMCP-83")],
            schema=NESTED_SCHEMA,
        )
        == []
    )
    found = check(
        sent,
        [entry(NESTED, fields=names("body:triggers", "body:triggers[].type"), task="PLMCP-83")],
        schema=NESTED_SCHEMA,
    )
    assert len(found) == 1
    assert "body:triggers[].type is listed" in found[0]


def test_the_children_and_values_of_a_parent_reappear_once_it_is_sent() -> None:
    sent = {NESTED: used("body:method", "body:method=rotate", "body:method=merge", "body:triggers")}

    found = check(
        sent, [entry(NESTED, fields=names("body:triggers"), task="PLMCP-83")], schema=NESTED_SCHEMA
    )

    assert sorted(found) == [
        "POST /api/nested/: body:triggers is listed, but a tool uses it or the schema lacks it",
        "POST /api/nested/: body:triggers[].type is used by no tool and not listed",
    ]


def test_the_values_of_a_field_that_is_sent_are_listed_with_a_reason() -> None:
    sent = {
        NESTED: used(
            "body:method",
            "body:method=rotate",
            "body:triggers",
            "body:triggers[].type",
            "body:triggers[].type=1",
            "body:triggers[].type=2",
        )
    }

    found = check(
        sent,
        [entry(NESTED, fields=names("body:method=merge"), task="PLMCP-83")],
        schema=NESTED_SCHEMA,
    )

    assert found == ["POST /api/nested/: body:method=merge needs a reason: why no tool sends it"]


# Fields the adapter sends by itself stay open although a tool is credited with them.
def test_the_value_of_an_array_hangs_under_the_array() -> None:
    schema = {NESTED: Fields(query=NONE, body=names("xs"), enums={"body:xs[]": names("1", "2")})}

    found = check({NESTED: used()}, [], schema=schema)

    assert found == ["POST /api/nested/: body:xs is used by no tool and not listed"]


# Plumbing: what the adapter chooses for itself, no feature and no open work.
def test_plumbing_that_is_sent_is_neither_open_work_nor_a_gap() -> None:
    plumbing = {GET_ONE: Plumbing(names("query:page"), "a constant of the list helper")}

    assert check(FULL, [], plumbing=plumbing) == []


# What hangs under a plumbing field (its values, its children) is not plumbing by itself: when no
# tool sends it, the row must name it, or it could hide work (PLMCP-97).
FLAGGED: dict[Operation, Fields] = {
    GET_ONE: Fields(
        query=frozenset({"flag"}), body=NONE, enums={"query:flag": frozenset({"true", "false"})}
    )
}
FLAG_SENT: dict[Operation, Usage] = {GET_ONE: used("query:flag", "query:flag=true")}


def test_an_unsent_value_below_a_plumbing_field_is_a_problem_unless_the_row_names_it() -> None:
    bare = {GET_ONE: Plumbing(names("query:flag"), "reads the grants")}
    named = {GET_ONE: Plumbing(names("query:flag", "query:flag=false"), "reads the grants")}

    found = check(FLAG_SENT, [], schema=FLAGGED, plumbing=bare)

    assert found == [
        (
            "GET /api/things/{id}/: query:flag=false hangs under a plumbing field, is sent by no "
            "tool and is not named in the plumbing row"
        )
    ]
    assert check(FLAG_SENT, [], schema=FLAGGED, plumbing=named) == []


def test_a_value_below_a_plumbing_field_that_a_tool_sends_needs_no_naming() -> None:
    both = {GET_ONE: used("query:flag", "query:flag=true", "query:flag=false")}
    bare = {GET_ONE: Plumbing(names("query:flag"), "reads the grants")}

    assert check(both, [], schema=FLAGGED, plumbing=bare) == []


def test_a_named_value_that_no_plumbing_field_holds_is_still_a_problem() -> None:
    stray = {GET_ONE: Plumbing(names("query:flag=false"), "x")}

    found = check(FLAG_SENT, [], schema=FLAGGED, plumbing=stray)

    assert any("query:flag=false is listed as plumbing, but no tool sends it" in f for f in found)


def test_plumbing_that_no_tool_sends_is_a_problem() -> None:
    partly = {**FULL, GET_ONE: used("query:fields")}
    plumbing = {GET_ONE: Plumbing(names("query:page"), "x")}

    found = check(partly, [], plumbing=plumbing)

    assert found == [
        "GET /api/things/{id}/: query:page is listed as plumbing, but no tool sends it"
    ]


def test_plumbing_of_an_operation_no_tool_covers_or_the_schema_lacks_is_a_problem() -> None:
    plumbing = {
        PATCH_ONE: Plumbing(names("body:name"), "x"),
        ("GET", "/api/gone/"): Plumbing(names("query:a"), "x"),
    }

    found = check(ONLY_GET, [entry(PATCH_ONE, task="PLMCP-83")], plumbing=plumbing)

    assert sorted(found) == [
        "GET /api/gone/: plumbing for an operation the schema lacks",
        "PATCH /api/things/{id}/: plumbing for an operation no tool covers",
    ]


def test_a_field_cannot_be_plumbing_and_internal() -> None:
    plumbing = {GET_ONE: Plumbing(names("query:page"), "x")}
    listed = entry(GET_ONE, fields=names("query:page"), task="PLMCP-83")

    found = check(FULL, [listed], internal={GET_ONE: names("query:page")}, plumbing=plumbing)

    assert any("both plumbing and internal" in line for line in found)


def test_internal_fields_of_an_operation_no_tool_covers_or_the_schema_lacks_are_problems() -> None:
    internal = {PATCH_ONE: names("body:name"), ("GET", "/api/gone/"): names("query:a")}

    found = check(ONLY_GET, [entry(PATCH_ONE, task="PLMCP-83")], internal=internal)

    assert sorted(found) == [
        "GET /api/gone/: internal fields for an operation the schema lacks",
        "PATCH /api/things/{id}/: internal fields for an operation no tool covers",
    ]


def test_plumbing_the_schema_lacks_is_stale() -> None:
    plumbing = {GET_ONE: Plumbing(names("query:nope"), "x")}

    found = check(FULL, [], plumbing=plumbing)

    assert len(found) == 1
    assert "query:nope" in found[0]
    assert "plumbing" in found[0]


def test_a_field_cannot_be_plumbing_and_listed_as_open() -> None:
    partly = FULL
    plumbing = {GET_ONE: Plumbing(names("query:page"), "x")}
    listed = entry(GET_ONE, fields=names("query:page"), task="PLMCP-83")

    found = check(partly, [listed], plumbing=plumbing)

    assert len(found) == 1
    assert "query:page is listed" in found[0]


def test_plumbing_needs_a_reason() -> None:
    plumbing = {GET_ONE: Plumbing(names("query:page"), " ")}

    found = check(FULL, [], plumbing=plumbing)

    assert len(found) == 1
    assert "needs a reason" in found[0]


def test_every_tool_and_every_argument_needs_a_scenario() -> None:
    tools = {"get_thing": names("a", "b"), "other": names("c")}

    found = missing_scenarios(tools, {"get_thing": {"a"}})

    assert found == ["get_thing.b", "other.c"]
    assert missing_scenarios(tools, {"get_thing": {"a", "b"}, "other": {"c", "extra"}}) == []


def test_an_internal_field_is_open_even_though_a_tool_sends_it() -> None:
    internal = {GET_ONE: names("query:page")}

    found = check(FULL, [], internal=internal)

    assert len(found) == 1
    assert "query:page" in found[0]
    listed = entry(GET_ONE, fields=names("query:page"), task="PLMCP-83")
    assert check(FULL, [listed], internal=internal) == []


def test_an_internal_field_the_schema_lacks_is_stale() -> None:
    internal = {GET_ONE: names("query:nope")}

    found = check(FULL, [], internal=internal)

    assert len(found) == 1
    assert "query:nope" in found[0]
    assert "internal" in found[0]


def test_an_internal_field_that_nothing_sends_stays_open_as_any_other_unsent_field() -> None:
    partly = {**FULL, GET_ONE: used("query:fields")}
    internal = {GET_ONE: names("query:page")}

    assert len(check(partly, [], internal=internal)) == 1
    listed = entry(GET_ONE, fields=names("query:page"), task="PLMCP-83")
    assert check(partly, [listed], internal=internal) == []


APPROVED = "Notes.\n\nThe owner approved leaving it out on 2026-11-23, in the review.\n\nMore."


def write_source(tmp_path: Path, text: str, name: str = "backlog/decisions/d.md") -> Approval:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")
    return Approval(quote="approved leaving it out", date="2026-11-23", source=name)


def test_an_exclusion_with_its_quoted_approval_and_date_counts(tmp_path: Path) -> None:
    approval = write_source(tmp_path, APPROVED)

    assert check(ONLY_GET, [entry(PATCH_ONE, approval=approval)], root=tmp_path) == []


@pytest.mark.parametrize(
    "text",
    [
        "Nobody did anything on 2026-11-23.",
        "The owner approved leaving it out on a Monday.",
        "The owner approved leaving it out.\n\nIt was on 2026-11-23.",
        "Leaving it out was approved on 2026-11-23.",
        "We approved leaving it out on 2026-11-23, said the owner of the cat.",
        "The owner approved something else on 2026-11-23.",
    ],
    ids=[
        "quote not in the source",
        "date not in the source",
        "quote and date in different paragraphs",
        "no word that shows the owner approved",
        "owner named without the approval",
        "another thing than the quoted one was approved",
    ],
)
def test_an_exclusion_whose_approval_is_not_in_the_repository_is_a_problem(
    tmp_path: Path, text: str
) -> None:
    approval = write_source(tmp_path, text)

    assert len(check(ONLY_GET, [entry(PATCH_ONE, approval=approval)], root=tmp_path)) == 1


@pytest.mark.parametrize(
    "approval",
    [
        Approval("", "2026-11-23", "backlog/decisions/d.md"),
        Approval("   ", "2026-11-23", "backlog/decisions/d.md"),
        Approval("approved leaving it out", "", "backlog/decisions/d.md"),
        Approval("approved leaving it out", "23.11.2026", "backlog/decisions/d.md"),
        Approval("approved leaving it out", "2026-13-45", "backlog/decisions/d.md"),
        Approval("approved leaving it out", "2026-11-23", "docs/api-coverage.toml"),
        Approval("approved leaving it out", "2026-11-23", "docs/api-coverage.md"),
        Approval("approved leaving it out", "2026-11-23", "backlog/../docs/d.md"),
        Approval("approved leaving it out", "2026-11-23", "backlog/config.toml"),
        Approval("approved leaving it out", "2026-11-23", "nowhere.md"),
        Approval("approved leaving it out", "2026-11-23", "backlog/decisions/gone.md"),
    ],
    ids=[
        "empty quote",
        "blank quote",
        "no date",
        "date in another format",
        "date that is none",
        "the entries file as source",
        "the generated page as source",
        "a path out of backlog",
        "a file under backlog that is no Markdown file",
        "a file that is not there",
        "a file in backlog that is not there",
    ],
)
def test_an_approval_that_is_hollow_is_a_problem(tmp_path: Path, approval: Approval) -> None:
    _ = write_source(tmp_path, APPROVED)
    _ = write_source(tmp_path, APPROVED, "docs/api-coverage.toml")
    _ = write_source(tmp_path, APPROVED, "docs/api-coverage.md")
    _ = write_source(tmp_path, APPROVED, "docs/d.md")
    _ = write_source(tmp_path, APPROVED, "backlog/config.toml")

    assert len(check(ONLY_GET, [entry(PATCH_ONE, approval=approval)], root=tmp_path)) == 1


def test_the_date_in_the_file_must_be_the_date_given(tmp_path: Path) -> None:
    approval = write_source(tmp_path, APPROVED.replace("2026-11-23", "2026-11-24"))

    assert len(check(ONLY_GET, [entry(PATCH_ONE, approval=approval)], root=tmp_path)) == 1
    assert date.fromisoformat(approval.date).year == 2026


def observed(  # noqa: PLR0913, PLR0917 -- one keyword per field of an observation
    tool: str | None,
    port: str,
    method: str,
    operation: Operation,
    query: str = "",
    values: str = "",
) -> Observation:
    return Observation(
        tool=tool,
        call=(port, method),
        operation=operation,
        query=names(*query.split()),
        body=NONE,
        values=names(*values.split()),
    )


def test_a_request_a_tool_caused_covers_its_endpoint_and_names_the_tool() -> None:
    seen = [observed("get_thing", "ThingReader", "get", GET_ONE, "fields", "query:fields=x")]

    found = covering(seen)

    assert found == {
        GET_ONE: Usage(
            names("get_thing"),
            {"query:fields": names("get_thing"), "query:fields=x": names("get_thing")},
        )
    }


def test_fields_remember_every_tool_that_sends_them() -> None:
    seen = [
        observed("one", "ThingReader", "get", GET_ONE, "page"),
        observed("two", "ThingReader", "get", GET_ONE, "page fields"),
    ]

    found = covering(seen)[GET_ONE]

    assert found.tools == names("one", "two")
    assert found.used == {"query:page": names("one", "two"), "query:fields": names("two")}


def test_a_request_no_tool_caused_covers_nothing() -> None:
    assert covering([observed(None, "ThingReader", "get", GET_ONE)]) == {}


def test_the_raw_api_tool_never_covers_an_endpoint() -> None:
    assert covering([observed("paperless_api_get", "RawReader", "get", GET_ONE)]) == {}


def test_schema_fields_read_query_parameters_body_properties_nested_and_enums() -> None:
    schema: dict[str, object] = {
        "paths": {
            "/api/things/{id}/": {
                "parameters": [{"name": "id", "in": "path"}],
                "patch": {
                    "parameters": [
                        {"name": "fields", "in": "query"},
                        {
                            "name": "kind",
                            "in": "query",
                            "schema": {"type": "string", "enum": ["a", "b"]},
                        },
                        {
                            "name": "kinds",
                            "in": "query",
                            "schema": {"type": "array", "items": {"enum": ["c"]}},
                        },
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/P"}}
                        }
                    },
                },
                "get": {"parameters": [{"name": "page", "in": "query"}]},
            }
        },
        "components": {
            "schemas": {
                "P": {
                    "properties": {
                        "name": {},
                        "flag": {"type": "boolean"},
                        "color": {"allOf": [{"$ref": "#/components/schemas/Color"}]},
                        "triggers": {"type": "array", "items": {"$ref": "#/components/schemas/T"}},
                    }
                },
                "Color": {"enum": ["red", "blue", "", None]},
                "T": {
                    "properties": {
                        "type": {"enum": [1, 2]},
                        "parent": {"$ref": "#/components/schemas/T"},
                    }
                },
            }
        },
    }

    found = schema_fields(schema, {PATCH_ONE: names("version")})

    patch = found[PATCH_ONE]
    assert patch.query == names("fields", "kind", "kinds", "version")
    assert patch.body == names(
        "name", "flag", "color", "triggers", "triggers[].type", "triggers[].parent"
    )
    assert patch.enums["body:flag"] == names("true", "false")
    assert patch.enums["query:kind"] == names("a", "b")
    assert patch.enums["query:kinds"] == names("c")
    assert patch.enums["body:color"] == names("red", "blue")
    assert patch.enums["body:triggers[].type"] == names("1", "2")
    assert found[GET_ONE] == Fields(names("page"), NONE)


def test_a_schema_that_refers_to_itself_does_not_loop() -> None:
    schema: dict[str, object] = {
        "paths": {
            "/api/t/": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/T"}}
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {"T": {"properties": {"child": {"$ref": "#/components/schemas/T"}}}}
        },
    }

    found = schema_fields(schema, {})[("POST", "/api/t/")]

    assert "child" in found.body


UNDECLARED_SCHEMA: dict[str, object] = {
    "paths": {"/api/things/{id}/": {"get": {"parameters": [{"name": "page", "in": "query"}]}}}
}


def test_an_undeclared_parameter_the_schema_has_or_an_operation_it_lacks_is_a_problem() -> None:
    found = undeclared_problems(
        UNDECLARED_SCHEMA,
        {GET_ONE: names("page", "version"), ("GET", "/api/gone/"): names("x")},
    )

    assert sorted(found) == [
        "GET /api/gone/: no such operation in the schema",
        "GET /api/things/{id}/: query:page is declared by the schema",
    ]


def test_an_undeclared_parameter_that_is_really_missing_is_fine() -> None:
    assert undeclared_problems(UNDECLARED_SCHEMA, {GET_ONE: names("version")}) == []


ENTRIES_TOML = """
[[undeclared]]
operation = "GET /api/things/{id}/"
params = ["version"]

[[open]]
operations = ["PATCH /api/things/{id}/", "GET /api/other/"]
task = "PLMCP-83"

[[open_fields]]
operation = "GET /api/things/{id}/"
fields = ["query:page", "query:version"]
task = "PLMCP-84"

[[internal]]
operation = "GET /api/things/{id}/"
fields = ["query:fields"]
reason = "the adapter always sends it"

[[plumbing]]
operation = "GET /api/things/{id}/"
fields = ["query:page"]
reason = "a constant of the list helper"

[[exclusion]]
operation = "DELETE /api/things/{id}/"
quote = "approved leaving it out"
date = "2026-11-23"
source = "backlog/decisions/d.md"
"""


def test_entries_are_read_from_their_file(tmp_path: Path) -> None:
    path = tmp_path / "entries.toml"
    _ = path.write_text(ENTRIES_TOML, encoding="utf-8")
    approval = Approval("approved leaving it out", "2026-11-23", "backlog/decisions/d.md")

    found = load_entries(path)

    assert found.entries == [
        entry(PATCH_ONE, task="PLMCP-83"),
        entry(("GET", "/api/other/"), task="PLMCP-83"),
        entry(GET_ONE, fields=names("query:page", "query:version"), task="PLMCP-84"),
        entry(("DELETE", "/api/things/{id}/"), approval=approval),
    ]
    assert found.undeclared == {GET_ONE: names("version")}
    assert found.internal == {GET_ONE: names("query:fields")}
    assert found.plumbing == {
        GET_ONE: Plumbing(names("query:page"), "a constant of the list helper")
    }
    assert found.duplicate_rows == []


def test_the_reason_of_an_excluded_value_is_read(tmp_path: Path) -> None:
    path = tmp_path / "entries.toml"
    _ = path.write_text(
        """
[[exclusion]]
operation = "GET /api/things/{id}/"
fields = ["query:flag=false"]
quote = "approved leaving it out"
date = "2026-11-23"
source = "backlog/decisions/d.md"
reason = "false is the default"
""",
        encoding="utf-8",
    )

    assert [e.reason for e in load_entries(path).entries] == ["false is the default"]


def test_two_rows_of_plumbing_or_internal_for_one_operation_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "entries.toml"
    twice = (
        '[[plumbing]]\noperation = "GET /api/a/"\nfields = ["query:x"]\nreason = "r"\n'
        '[[plumbing]]\noperation = "GET /api/a/"\nfields = ["query:y"]\nreason = "r"\n'
        '[[internal]]\noperation = "GET /api/b/"\nfields = ["query:x"]\n'
        '[[internal]]\noperation = "GET /api/b/"\nfields = ["query:y"]\n'
    )
    _ = path.write_text(twice, encoding="utf-8")

    assert load_entries(path).duplicate_rows == [
        "GET /api/a/: two plumbing rows",
        "GET /api/b/: two internal rows",
    ]


def test_the_status_of_a_task_comes_from_its_file_and_an_archived_task_is_closed(
    tmp_path: Path,
) -> None:
    board = tmp_path / "backlog" / "tasks"
    archive = tmp_path / "backlog" / "archive" / "tasks"
    board.mkdir(parents=True)
    archive.mkdir(parents=True)
    _ = (board / "plmcp-1 - A task.md").write_text("---\nid: PLMCP-1\nstatus: In Progress\n---\n")
    _ = (board / "plmcp-2 - Another.md").write_text("---\nid: PLMCP-2\nstatus: Done\n---\n")
    _ = (archive / "plmcp-3 - Old.md").write_text("---\nid: PLMCP-3\nstatus: To Do\n---\n")

    assert backlog_statuses(tmp_path) == {
        "PLMCP-1": "In Progress",
        "PLMCP-2": "Done",
        "PLMCP-3": "Archived",
    }


def test_the_table_names_the_covering_tools_the_open_tasks_and_the_unused_fields() -> None:
    covered = {GET_ONE: used("query:fields")}
    entries = [
        entry(PATCH_ONE, task="PLMCP-83"),
        entry(GET_ONE, fields=names("query:page"), task="PLMCP-84"),
    ]

    page = render(SCHEMA, covered, entries, {})

    assert "| `GET /api/things/{id}/` | `get_thing` |  |" in page
    assert "| `PATCH /api/things/{id}/` |  | PLMCP-83 |" in page
    assert "| `GET /api/things/{id}/` | `query:page` | PLMCP-84 |" in page


def test_the_table_shows_plumbing_apart_from_the_open_work() -> None:
    partly = {**FULL, GET_ONE: used("query:fields")}
    plumbing = {GET_ONE: Plumbing(names("query:page"), "a constant of the list helper")}

    page = render(SCHEMA, partly, [], {}, plumbing)

    open_work, own_section = page.split("## Fields the adapter chooses for itself")
    assert "query:page" not in open_work.split("## Query parameters")[1]
    assert (
        "| `GET /api/things/{id}/` | `query:page` | a constant of the list helper |" in own_section
    )
    assert "no feature" in " ".join(own_section.split())
    assert "where it was noticed" in " ".join(own_section.split())


def test_the_table_lists_an_internal_field_as_open() -> None:
    entries = [entry(GET_ONE, fields=names("query:page"), task="PLMCP-84")]

    page = render(SCHEMA, FULL, entries, {GET_ONE: names("query:page")}, {})

    assert "| `GET /api/things/{id}/` | `query:page` | PLMCP-84 |" in page


def test_the_table_names_the_fields_only_a_saved_view_reaches() -> None:
    covered = {
        GET_ONE: Usage(
            names("run_saved_view", "x"),
            {
                "query:page": names("run_saved_view"),
                "query:page=2": names("run_saved_view"),
                "query:fields": names("run_saved_view", "x"),
            },
        )
    }

    page = render(SCHEMA, covered, [entry(PATCH_ONE, task="PLMCP-83")], {})

    section = page.split("## Fields only a saved view reaches")[1]
    assert "| `GET /api/things/{id}/` | `query:page` |" in section
    assert "query:fields" not in section
    assert "query:page=2" not in section  # names, not sample values


def test_a_long_list_of_covering_tools_is_cut_and_counted() -> None:
    crowd = Usage(names(*(f"tool_{n}" for n in range(8))), {})

    page = render(SCHEMA, {**FULL, GET_ONE: crowd}, [], {})

    assert "`tool_0`, `tool_1`, `tool_2`, `tool_3`, `tool_4` and 3 more" in page


def test_an_exclusion_is_shown_with_its_quote_and_date() -> None:
    approval = Approval("approved leaving it out", "2026-11-23", "backlog/decisions/d.md")

    page = render(SCHEMA, ONLY_GET, [entry(PATCH_ONE, approval=approval)], {})

    assert 'excluded, "approved leaving it out" (2026-11-23, backlog/decisions/d.md)' in page


def test_the_page_says_what_it_does_not_check() -> None:
    page = " ".join(render(SCHEMA, FULL, [], {}).split())

    assert "The scenarios of tests/tools/test_arguments.py" in page
    assert "hides the children a schema refresh adds below it" in page
    assert "not checked against the schema" in page
    assert "does not mean that the whole answer is shown" in page
    assert "copies from the stored object" in page
    assert "[[internal]]" in page
    assert "[[plumbing]]" in page


# The real thing: what the tools ask of the ports, and what the adapters send for it.
ROOT = Path(__file__).parents[2]
PAGE = ROOT / "docs" / "api-coverage.md"
ENTRIES = ROOT / "docs" / "api-coverage.toml"
SCHEMA_FILE = ROOT / "docs" / "vendor" / "paperless" / "openapi.json"


@pytest.fixture(scope="module")
def covered() -> dict[Operation, Usage]:
    return covering(derive(ROOT))


def schema() -> dict[str, object]:
    return cast("dict[str, object]", json.loads(SCHEMA_FILE.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Real:
    """The real schema, the hand-written file and what is wrong with them."""

    found: list[str]
    fields: dict[Operation, Fields]
    entries: list[Entry]
    internal: dict[Operation, frozenset[str]]
    plumbing: dict[Operation, Plumbing]


def real_problems(covered: dict[Operation, Usage]) -> Real:
    listed = load_entries(ENTRIES)
    guarded = {
        operation: frozenset(params) | listed.undeclared.get(operation, frozenset())
        for operation, params in schema_guard.UNDECLARED_QUERY_PARAMETERS.items()
    }
    fields = schema_fields(schema(), listed.undeclared | guarded)
    found = undeclared_problems(schema(), listed.undeclared) + problems(
        fields,
        covered,
        listed.entries,
        backlog_statuses(ROOT),
        root=ROOT,
        internal=listed.internal,
        plumbing=listed.plumbing,
    )
    return Real(
        found + listed.duplicate_rows, fields, listed.entries, listed.internal, listed.plumbing
    )


def test_the_derivation_sees_the_tools_nested_fields_enum_values_and_saved_views(
    covered: dict[Operation, Usage],
) -> None:
    workflow = covered[("POST", "/api/workflows/")]
    sent = covered[("POST", "/api/documents/bulk_edit/")].used
    searched = covered[("GET", "/api/documents/")].used

    assert covered[("GET", "/api/trash/")].tools >= names("list_trash")
    assert workflow.used["body:triggers[].type"] == names("create_workflow")
    assert "body:actions[].assign_tags" in workflow.used
    assert "body:method=modify_tags" in sent
    assert searched["query:has_duplicates"] == names("run_saved_view")  # a saved view only
    assert searched["query:title__icontains"] >= names("search_documents")
    assert all("paperless_api_get" not in usage.tools for usage in covered.values())


def test_no_endpoint_or_field_is_neither_covered_nor_listed_and_no_entry_is_stale(
    covered: dict[Operation, Usage],
) -> None:
    assert real_problems(covered).found == []


def test_every_tool_and_every_argument_of_it_is_in_a_scenario(
    covered: dict[Operation, Usage],
) -> None:
    """After the derivation, which ran the scenarios of tests/tools/test_arguments.py."""
    assert covered
    assert observation.TOOL_ARGUMENTS
    assert missing_scenarios(observation.TOOL_ARGUMENTS, observation.GIVEN) == []


def test_the_coverage_table_is_current(
    request: pytest.FixtureRequest, covered: dict[Operation, Usage]
) -> None:
    real = real_problems(covered)
    assert real.found == [], "fix the gaps before the table is written"
    page = render(real.fields, covered, real.entries, real.internal, real.plumbing)

    if request.config.getoption("--write-docs") and PAGE.read_text(encoding="utf-8") != page:
        _ = PAGE.write_text(page, encoding="utf-8")
    assert PAGE.read_text(encoding="utf-8") == page, "run `just docs-coverage`"
