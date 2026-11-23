"""The coverage of the Paperless API, endpoint by endpoint, is derived and guarded (PLMCP-91).

`docs/api-coverage.md` is generated (`just docs-coverage`, this module with `--write-docs`):
for every operation of the vendored schema the tools that cover it, and for the rest the open
task or the owner-approved exclusion; for the covered ones the query parameters and body fields
no tool uses. Coverage is derived: the requests the adapter sends in its tests (checked against the
schema) say what an adapter method does, and the code says which tool reaches that method.
`paperless_api_get` (the raw reader) never counts. The first half of this file proves the guard
on synthetic violations, so it cannot pass by looking at nothing.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from adapter import schema_guard

from mcp_server_paperless.tools._shared import TOOL_TOOLSETS
from quality.api_coverage import (
    Approval,
    Call,
    Entry,
    Fields,
    Observation,
    Operation,
    Usage,
    backlog_statuses,
    covering,
    load_entries,
    problems,
    render,
    schema_fields,
    tool_calls,
)

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


def calls(*given: Call) -> frozenset[Call]:
    return frozenset(given)


def entry(
    operation: Operation,
    *,
    fields: frozenset[str] | None = None,
    task: str | None = None,
    approval: Approval | None = None,
) -> Entry:
    return Entry(operation=operation, fields=fields, task=task, approval=approval)


def used(operation: Operation, tool: str = "get_thing", **fields: str) -> Usage:
    assert operation in SCHEMA
    return Usage(
        tools=names(tool),
        query=names(*fields.get("query", "").split()),
        body=names(*fields.get("body", "").split()),
    )


def check(
    covered: Mapping[Operation, Usage],
    entries: list[Entry],
    statuses: Mapping[str, str] | None = None,
    root: Path | None = None,
) -> list[str]:
    known = {"PLMCP-83": "To Do"} if statuses is None else statuses
    return problems(SCHEMA, covered, entries, known, root or Path())


FULL: dict[Operation, Usage] = {
    GET_ONE: used(GET_ONE, query="fields page"),
    PATCH_ONE: used(PATCH_ONE, "update_thing", body="name color"),
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

    found = problems(refreshed, FULL, [], {}, Path())

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


def test_unused_fields_of_a_covered_endpoint_need_an_entry() -> None:
    partly = {**FULL, GET_ONE: used(GET_ONE, query="fields")}

    found = check(partly, [])

    assert len(found) == 1
    assert "query:page" in found[0]


def test_unused_body_fields_need_an_entry_too() -> None:
    partly = {**FULL, PATCH_ONE: used(PATCH_ONE, "update_thing", body="name")}

    found = check(partly, [])

    assert len(found) == 1
    assert "body:color" in found[0]


def test_unused_fields_are_listed_by_name_and_all_of_them_must_be() -> None:
    partly = {**FULL, GET_ONE: used(GET_ONE)}
    both = names("query:fields", "query:page")

    assert check(partly, [entry(GET_ONE, fields=both, task="PLMCP-83")]) == []
    assert len(check(partly, [entry(GET_ONE, fields=names("query:page"), task="PLMCP-83")])) == 1


def test_a_field_entry_for_a_field_a_tool_uses_is_stale() -> None:
    found = check(FULL, [entry(GET_ONE, fields=names("query:page"), task="PLMCP-83")])

    assert len(found) == 1
    assert "query:page" in found[0]


def test_a_field_entry_that_names_no_field_of_the_endpoint_is_stale() -> None:
    partly = {**FULL, GET_ONE: used(GET_ONE, query="page")}

    found = check(
        partly, [entry(GET_ONE, fields=names("query:nope", "query:fields"), task="PLMCP-83")]
    )

    assert len(found) == 1
    assert "query:nope" in found[0]


def test_a_field_entry_needs_its_task_too() -> None:
    partly = {**FULL, GET_ONE: used(GET_ONE)}

    both = names("query:fields", "query:page")

    found = check(partly, [entry(GET_ONE, fields=both, task="PLMCP-1")], {})

    assert len(found) == 1


def test_an_entry_with_neither_task_nor_approval_is_a_problem() -> None:
    assert len(check(ONLY_GET, [entry(PATCH_ONE)])) == 1


def write_source(tmp_path: Path, text: str) -> Approval:
    _ = (tmp_path / "decision.md").write_text(text, encoding="utf-8")
    return Approval(
        quote="The owner approved leaving it out", date="2026-11-23", source="decision.md"
    )


def test_an_exclusion_with_its_quoted_approval_and_date_counts(tmp_path: Path) -> None:
    approval = write_source(tmp_path, "The owner approved leaving it out on 2026-11-23.")

    assert check(ONLY_GET, [entry(PATCH_ONE, approval=approval)], root=tmp_path) == []


@pytest.mark.parametrize(
    "text",
    [
        "Nobody approved anything on 2026-11-23.",
        "The owner approved leaving it out on a Monday.",
    ],
    ids=["quote not in the source", "date not in the source"],
)
def test_an_exclusion_whose_approval_is_not_in_the_repository_is_a_problem(
    tmp_path: Path, text: str
) -> None:
    approval = write_source(tmp_path, text)

    assert len(check(ONLY_GET, [entry(PATCH_ONE, approval=approval)], root=tmp_path)) == 1


def test_an_exclusion_whose_source_file_is_missing_is_a_problem(tmp_path: Path) -> None:
    approval = Approval(quote="x", date="2026-11-23", source="nowhere.md")

    assert len(check(ONLY_GET, [entry(PATCH_ONE, approval=approval)], root=tmp_path)) == 1


def observed(port: str, method: str, operation: Operation, query: str = "") -> Observation:
    return Observation(
        call=(port, method), operation=operation, query=names(*query.split()), body=NONE
    )


def test_a_request_of_an_adapter_method_a_tool_reaches_covers_its_endpoint() -> None:
    reached = {"get_thing": calls(("ThingReader", "get"))}

    found = covering([observed("ThingReader", "get", GET_ONE, "fields")], reached)

    assert found == {GET_ONE: Usage(names("get_thing"), names("fields"), NONE)}


def test_a_request_no_tool_reaches_covers_nothing() -> None:
    reached = {"get_thing": calls(("ThingReader", "other"))}

    assert covering([observed("ThingReader", "get", GET_ONE)], reached) == {}


def test_the_raw_api_tool_never_covers_an_endpoint() -> None:
    reached = {"paperless_api_get": calls(("RawReader", "get"))}

    assert covering([observed("RawReader", "get", GET_ONE)], reached) == {}


def test_schema_fields_read_query_parameters_and_body_properties() -> None:
    schema: dict[str, object] = {
        "paths": {
            "/api/things/{id}/": {
                "parameters": [{"name": "id", "in": "path"}],
                "patch": {
                    "parameters": [{"name": "fields", "in": "query"}],
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/P"}}
                        }
                    },
                },
                "get": {"parameters": [{"name": "page", "in": "query"}]},
            }
        },
        "components": {"schemas": {"P": {"properties": {"name": {}, "color": {}}}}},
    }

    found = schema_fields(schema, {PATCH_ONE: names("version")})

    assert found[PATCH_ONE] == Fields(names("fields", "version"), names("name", "color"))
    assert found[GET_ONE] == Fields(names("page"), NONE)


TREE = {
    "domain/ports.py": (
        "class ThingReader(Protocol):\n"
        "    async def get(self): ...\n"
        "    async def other(self): ...\n"
        "    async def deep(self): ...\n"
        "class ThingWriter(Protocol):\n"
        "    async def put(self): ...\n"
    ),
    "services/things.py": (
        "class ThingService:\n"
        "    def __init__(self, reader, writer, helper):\n"
        "        self._reader: ThingReader = reader\n"
        "        self._writer: ThingWriter = writer\n"
        "        self._helper: HelperService = helper\n"
        "    async def read(self):\n"
        "        return await self._load()\n"
        "    async def _load(self):\n"
        "        return await self._reader.get()\n"
        "    async def save(self):\n"
        "        return await self._writer.put()\n"
        "    async def through(self):\n"
        "        return await self._helper.assist()\n"
        "class HelperService:\n"
        "    def __init__(self, reader):\n"
        "        self._reader: ThingReader = reader\n"
        "    async def assist(self):\n"
        "        return await self._reader.deep()\n"
    ),
    "tools/_shared.py": "class Services:\n    things: ThingService\n",
    "tools/things.py": (
        "@provider.tool()\n"
        "async def get_thing(ctx):\n"
        "    return await services(ctx).things.read()\n"
        "@provider.tool()\n"
        "async def change_thing(ctx):\n"
        "    things = services(ctx).things\n"
        "    await things.through()\n"
        "    return await things.save()\n"
        "def not_a_tool(ctx):\n"
        "    return services(ctx).things.save()\n"
    ),
}


def make_tree(root: Path) -> Path:
    for name, text in TREE.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        _ = (root / name).write_text(text, encoding="utf-8")
    return root


def test_a_tool_reaches_the_port_methods_its_service_calls_use(tmp_path: Path) -> None:
    found = tool_calls(make_tree(tmp_path))

    assert found == {
        "get_thing": calls(("ThingReader", "get")),
        "change_thing": calls(("ThingWriter", "put"), ("ThingReader", "deep")),
    }


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

[[exclusion]]
operation = "DELETE /api/things/{id}/"
quote = "The owner approved leaving it out"
date = "2026-11-23"
source = "decision.md"
"""


def test_entries_are_read_from_their_file(tmp_path: Path) -> None:
    path = tmp_path / "entries.toml"
    _ = path.write_text(ENTRIES_TOML, encoding="utf-8")
    approval = Approval("The owner approved leaving it out", "2026-11-23", "decision.md")

    found = load_entries(path)

    assert found.entries == [
        entry(PATCH_ONE, task="PLMCP-83"),
        entry(("GET", "/api/other/"), task="PLMCP-83"),
        entry(GET_ONE, fields=names("query:page", "query:version"), task="PLMCP-84"),
        entry(("DELETE", "/api/things/{id}/"), approval=approval),
    ]
    assert found.undeclared == {GET_ONE: names("version")}


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
    covered = {GET_ONE: used(GET_ONE, query="fields")}
    entries = [
        entry(PATCH_ONE, task="PLMCP-83"),
        entry(GET_ONE, fields=names("query:page"), task="PLMCP-84"),
    ]

    page = render(SCHEMA, covered, entries)

    assert "| `GET /api/things/{id}/` | `get_thing` |  |" in page
    assert "| `PATCH /api/things/{id}/` |  | PLMCP-83 |" in page
    assert "| `GET /api/things/{id}/` | `query:page` | PLMCP-84 |" in page


def test_a_long_list_of_covering_tools_is_cut_and_counted() -> None:
    crowd = Usage(names(*(f"tool_{n}" for n in range(8))), NONE, NONE)

    page = render(SCHEMA, {**FULL, GET_ONE: crowd}, [])

    assert "`tool_0`, `tool_1`, `tool_2`, `tool_3`, `tool_4` and 3 more" in page


def test_an_exclusion_is_shown_with_its_quote_and_date() -> None:
    approval = Approval("The owner approved leaving it out", "2026-11-23", "decision.md")

    page = render(SCHEMA, ONLY_GET, [entry(PATCH_ONE, approval=approval)])

    assert 'excluded, "The owner approved leaving it out" (2026-11-23, decision.md)' in page


# The real thing: what the adapter tests send, which tool reaches it, and what is still open.
ROOT = Path(__file__).parents[2]
PAGE = ROOT / "docs" / "api-coverage.md"
ENTRIES = ROOT / "docs" / "api-coverage.toml"
SCHEMA_FILE = ROOT / "docs" / "vendor" / "paperless" / "openapi.json"


@pytest.fixture(scope="module")
def adapter_requests() -> list[Observation]:
    """The requests of the whole adapter suite, run once more in this process."""
    schema_guard.OBSERVED.clear()
    code = pytest.main(
        ["tests/adapter", "-q", "-p", "no:cacheprovider", "--no-cov", "--rootdir", str(ROOT)]
    )
    assert code == 0, "the adapter tests fail, so the coverage cannot be derived"
    return list(schema_guard.OBSERVED)


SRC = ROOT / "src" / "mcp_server_paperless"


@pytest.fixture(scope="module")
def covered(adapter_requests: list[Observation]) -> dict[Operation, Usage]:
    return covering(adapter_requests, tool_calls(SRC))


def hand_listed() -> tuple[list[Entry], dict[Operation, frozenset[str]]]:
    """The entries of the file, and the query parameters the schema omits (guard and file)."""
    found = load_entries(ENTRIES)
    undeclared = {
        operation: frozenset(params) | found.undeclared.get(operation, frozenset())
        for operation, params in schema_guard.UNDECLARED_QUERY_PARAMETERS.items()
    }
    return found.entries, found.undeclared | undeclared


def schema() -> dict[str, object]:
    return cast("dict[str, object]", json.loads(SCHEMA_FILE.read_text(encoding="utf-8")))


def test_the_scan_of_the_code_finds_every_tool_and_the_ports_most_of_them_reach() -> None:
    found = tool_calls(SRC)

    assert set(found) == set(TOOL_TOOLSETS)
    assert sum(1 for calls in found.values() if calls) > len(found) / 2


def test_no_endpoint_or_field_is_neither_covered_nor_listed_and_no_entry_is_stale(
    covered: dict[Operation, Usage],
) -> None:
    entries, undeclared = hand_listed()

    found = problems(
        schema_fields(schema(), undeclared), covered, entries, backlog_statuses(ROOT), ROOT
    )

    assert found == []


def test_the_coverage_table_is_current(
    request: pytest.FixtureRequest, covered: dict[Operation, Usage]
) -> None:
    entries, undeclared = hand_listed()
    page = render(schema_fields(schema(), undeclared), covered, entries)

    if request.config.getoption("--write-docs"):
        _ = PAGE.write_text(page, encoding="utf-8")
    assert PAGE.read_text(encoding="utf-8") == page, "run `just docs-coverage`"
