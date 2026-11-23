"""Derivation and checks behind the coverage of the Paperless API (PLMCP-91).

Nothing here is a claim: what an adapter method sends comes from the requests the adapter tests
make (`adapter.schema_guard` records them, after checking them against the vendored schema), and
which tools reach that method comes from the code (tools call services, services call ports).
The hand-written part is only what is still open (a task) or excluded (an owner approval).
"""

import ast
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

type Operation = tuple[str, str]  # ("GET", "/api/tags/{id}/")
type Call = tuple[str, str]  # (port, method)
type Json = dict[str, object]

# The tool that sends any GET itself: it covers nothing in particular (decision-25).
RAW_PORT = "RawReader"
CLOSED = frozenset({"Done", "Archived"})  # a task in one of these cannot own an open entry
METHODS = ("get", "put", "post", "patch", "delete")


class Fields(NamedTuple):
    query: frozenset[str]
    body: frozenset[str]


class Observation(NamedTuple):
    """One request an adapter method sent, checked against the schema."""

    call: Call
    operation: Operation
    query: frozenset[str]
    body: frozenset[str]


class Usage(NamedTuple):
    """The tools that reach an endpoint, and the fields they send."""

    tools: frozenset[str]
    query: frozenset[str]
    body: frozenset[str]


@dataclass(frozen=True)
class Approval:
    """The owner's approval of an exclusion: a quote and its date, from a file of the repository."""

    quote: str
    date: str
    source: str


@dataclass(frozen=True)
class Entry:
    """An endpoint (or some of its fields) no tool covers: open under a task, or excluded."""

    operation: Operation
    fields: frozenset[str] | None  # "query:x" or "body:y"; None: the endpoint itself
    task: str | None
    approval: Approval | None


def _resolve(schema: Json, node: object) -> Json:
    """`node` with its `$ref` followed."""
    found = cast("Json", node)
    schemas = cast("Json", cast("Json", schema["components"])["schemas"])
    while "$ref" in found:
        found = cast("Json", schemas[str(found["$ref"]).rsplit("/", 1)[-1]])
    return found


def _properties(schema: Json, node: object) -> set[str]:
    found = _resolve(schema, node)
    names = set(cast("Json", found.get("properties", {})))
    for key in ("allOf", "oneOf", "anyOf"):
        for part in cast("list[Json]", found.get(key, [])):
            names |= _properties(schema, part)
    return names


def schema_fields(
    schema: Json, undeclared: Mapping[Operation, frozenset[str]]
) -> dict[Operation, Fields]:
    """Every operation of the schema with its query parameters and request body properties.

    `undeclared` adds the query parameters the generated schema omits (listed by hand).
    """
    found: dict[Operation, Fields] = {}
    for path, item in cast("dict[str, Json]", schema["paths"]).items():
        shared = cast("list[Json]", item.get("parameters", []))
        for method in METHODS:
            if method not in item:
                continue
            operation = cast("Json", item[method])
            declared = shared + cast("list[Json]", operation.get("parameters", []))
            query = {str(p["name"]) for p in declared if p["in"] == "query"}
            body: set[str] = set()
            content = cast(
                "Json", cast("Json", operation.get("requestBody", {})).get("content", {})
            )
            for media in content.values():
                body |= _properties(schema, cast("Json", media).get("schema", {}))
            key = (method.upper(), path)
            found[key] = Fields(
                frozenset(query | undeclared.get(key, frozenset())), frozenset(body)
            )
    return found


def covering(
    observed: Iterable[Observation], tools: Mapping[str, frozenset[Call]]
) -> dict[Operation, Usage]:
    """The endpoints some tool reaches, with those tools and the fields they send."""
    found: dict[Operation, list[Usage]] = defaultdict(list)
    for seen in observed:
        if seen.call[0] == RAW_PORT:
            continue
        reaching = frozenset(name for name, calls in tools.items() if seen.call in calls)
        if reaching:
            found[seen.operation].append(Usage(reaching, seen.query, seen.body))
    return {
        operation: Usage(
            frozenset().union(*(u.tools for u in usages)),
            frozenset().union(*(u.query for u in usages)),
            frozenset().union(*(u.body for u in usages)),
        )
        for operation, usages in found.items()
    }


def unused(fields: Fields, usage: Usage) -> set[str]:
    """The fields of an endpoint no tool sends, as `query:x` and `body:y`."""
    return {f"query:{n}" for n in fields.query - usage.query} | {
        f"body:{n}" for n in fields.body - usage.body
    }


def _text(operation: Operation) -> str:
    return f"{operation[0]} {operation[1]}"


def _owner(entry: Entry, statuses: Mapping[str, str], root: Path) -> list[str]:
    """What is wrong with who owns an entry: a task that is there and not Done, or an approval."""
    where = _text(entry.operation)
    if entry.approval is not None:
        approval = entry.approval
        source = root / approval.source
        text = source.read_text(encoding="utf-8") if source.is_file() else ""
        if approval.quote in text and approval.date in text:
            return []
        return [f"{where}: approval {approval.quote!r} ({approval.date}) not in {approval.source}"]
    if entry.task is None:
        return [f"{where}: the entry has neither a task nor an owner approval"]
    status = statuses.get(entry.task)
    if status is None:
        return [f"{where}: task {entry.task} does not exist"]
    if status in CLOSED:
        return [f"{where}: task {entry.task} is {status} but the entry is still open"]
    return []


def problems(
    schema: Mapping[Operation, Fields],
    covered: Mapping[Operation, Usage],
    entries: Sequence[Entry],
    statuses: Mapping[str, str],
    root: Path,
) -> list[str]:
    """Everything that is wrong: gaps, stale entries, missing tasks, approvals not in the repo."""
    found: list[str] = []
    listed = {e.operation for e in entries if e.fields is None}
    named: dict[Operation, set[str]] = defaultdict(set)
    for entry in entries:
        if entry.operation not in schema:
            found.append(f"{_text(entry.operation)}: no such operation in the schema")
        elif entry.fields is None and entry.operation in covered:
            found.append(f"{_text(entry.operation)}: listed, but a tool already covered it")
        found += _owner(entry, statuses, root)
        if entry.fields is not None:
            named[entry.operation] |= entry.fields
    for operation, fields in schema.items():
        if operation not in covered:
            if operation not in listed:
                found.append(f"{_text(operation)}: neither covered nor listed")
            continue
        free = unused(fields, covered[operation])
        listed_fields = named.get(operation, set())
        found += [
            f"{_text(operation)}: {name} is used by no tool and not listed"
            for name in sorted(free - listed_fields)
        ]
        found += [
            f"{_text(operation)}: {name} is listed, but a tool uses it or the schema lacks it"
            for name in sorted(listed_fields - free)
        ]
    return found


def _classes(root: Path, folder: str) -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for path in sorted((root / folder).glob("*.py"))
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef)
    }


def _annotated(cls: ast.ClassDef) -> dict[str, str]:
    """The attribute types a class declares: `self._reader: TrashReader = ...` and class fields."""
    types: dict[str, str] = {}
    for node in ast.walk(cls):
        if isinstance(node, ast.AnnAssign) and isinstance(node.annotation, ast.Name):
            target = node.target
            if isinstance(target, ast.Attribute):
                types[target.attr] = node.annotation.id
            elif isinstance(target, ast.Name):
                types[target.id] = node.annotation.id
    return types


def _methods(cls: ast.ClassDef) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    return {
        node.name: node
        for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }


def _functions(root: Path) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    """The module-level functions of the services, by name."""
    return {
        node.name: node
        for path in sorted((root / "services").glob("*.py"))
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }


def _target(
    call: ast.Call,
    owner_name: str | None,
    types: Mapping[str, str],
    ports: Mapping[str, set[str]],
    services: Mapping[str, ast.ClassDef],
) -> tuple[tuple[str | None, str] | None, set[Call]]:
    """What a call leads to: a service method or helper to follow, and the port methods it is.

    `types` holds the declared types of `self.x` and of the parameters. A call on `self`, on a
    `self._x` or on a parameter with a declared type is resolved by that type. Any other
    `x.method()` counts for the ports in scope (those of the class and the parameters) that have a
    method of that name; no port method is named like a builtin one, so a `dict.items()` is not
    taken for a port.
    """
    if isinstance(call.func, ast.Name):
        return (None, call.func.id), set()
    if not isinstance(call.func, ast.Attribute):
        return None, set()
    owner, called = call.func.value, call.func.attr
    if isinstance(owner, ast.Name) and owner.id == "self":
        return (owner_name, called), set()
    kind = None
    if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name):
        kind = types.get(f"{owner.value.id}.{owner.attr}") if owner.value.id == "self" else None
    elif isinstance(owner, ast.Name):
        kind = types.get(owner.id)
    if kind in services:
        return (kind, called), set()
    if kind is not None and kind in ports:
        return None, {(kind, called)} if called in ports[kind] else set()
    in_scope = {t for t in types.values() if t in ports}
    return None, {(p, called) for p in in_scope if called in ports[p]}


def _reach(
    start: tuple[str | None, str],
    ports: Mapping[str, set[str]],
    services: Mapping[str, ast.ClassDef],
    functions: Mapping[str, ast.AsyncFunctionDef | ast.FunctionDef],
) -> set[Call]:
    """The port methods a service method reaches, through its class, other services and helpers."""
    found: set[Call] = set()
    todo = [start]
    seen: set[tuple[str | None, str]] = set()
    while todo:
        owner_name, name = todo.pop()
        if (owner_name, name) in seen:
            continue
        seen.add((owner_name, name))
        cls = services.get(owner_name or "")
        function = _methods(cls).get(name) if cls else functions.get(name)
        if function is None:
            continue
        types = {f"self.{name}": kind for name, kind in (_annotated(cls) if cls else {}).items()}
        types |= {
            a.arg: a.annotation.id
            for a in (*function.args.args, *function.args.kwonlyargs)
            if isinstance(a.annotation, ast.Name)
        }
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                follow, ports_called = _target(node, owner_name, types, ports, services)
                found |= ports_called
                if follow is not None:
                    todo.append(follow)
    return found


def _is_services(node: object) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "services"
    )


def _is_tool(function: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "tool"
        for d in function.decorator_list
    )


def _service_calls(function: ast.AsyncFunctionDef | ast.FunctionDef) -> set[tuple[str, str]]:
    """`(field, method)` of every `services(ctx).field.method` in a function, also by alias."""
    aliases: dict[str, str] = {}
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Attribute)
            and _is_services(node.value.value)
            and isinstance(node.targets[0], ast.Name)
        ):
            aliases[node.targets[0].id] = node.value.attr
    found: set[tuple[str, str]] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Attribute) and _is_services(node.value.value):
            found.add((node.value.attr, node.attr))
        elif isinstance(node.value, ast.Name) and node.value.id in aliases:
            found.add((aliases[node.value.id], node.attr))
    return found


def tool_calls(src: Path) -> dict[str, frozenset[Call]]:
    """For every tool the port methods its service calls reach, read from the code in `src`."""
    ports = {
        name: set(_methods(cls))
        for name, cls in _classes(src, "domain").items()
        if any(isinstance(b, ast.Name) and b.id == "Protocol" for b in cls.bases)
    }
    services = _classes(src, "services")
    functions = _functions(src)
    field_types = _annotated(_classes(src, "tools")["Services"])
    found: dict[str, frozenset[Call]] = {}
    for path in sorted((src / "tools").glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and _is_tool(node):
                reached: set[Call] = set()
                for field, method in _service_calls(node):
                    reached |= _reach(
                        (field_types.get(field, ""), method), ports, services, functions
                    )
                found[node.name] = frozenset(reached)
    return found


@dataclass(frozen=True)
class Entries:
    """The hand-written file: entries, and query parameters the generated schema omits."""

    entries: list[Entry]
    undeclared: dict[Operation, frozenset[str]]


def _operation(text: str) -> Operation:
    method, path = text.split(" ", 1)
    return method, path


def load_entries(path: Path) -> Entries:
    """Read `[[open]]`, `[[open_fields]]`, `[[exclusion]]` and `[[undeclared]]` from a TOML file."""
    data = cast("dict[str, list[Json]]", tomllib.loads(path.read_text(encoding="utf-8")))
    entries = [
        Entry(_operation(op), None, str(row["task"]), None)
        for row in data.get("open", [])
        for op in cast("list[str]", row["operations"])
    ]
    entries += [
        Entry(
            _operation(str(row["operation"])),
            frozenset(cast("list[str]", row["fields"])),
            str(row["task"]),
            None,
        )
        for row in data.get("open_fields", [])
    ]
    entries += [
        Entry(
            _operation(str(row["operation"])),
            frozenset(cast("list[str]", row["fields"])) if "fields" in row else None,
            None,
            Approval(str(row["quote"]), str(row["date"]), str(row["source"])),
        )
        for row in data.get("exclusion", [])
    ]
    undeclared = {
        _operation(str(row["operation"])): frozenset(cast("list[str]", row["params"]))
        for row in data.get("undeclared", [])
    }
    return Entries(entries, undeclared)


_FRONT = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def backlog_statuses(root: Path) -> dict[str, str]:
    """Task id to status from the task files; an archived task is `Archived`.

    The files are read directly, so the test runs where the `backlog` CLI is not installed.
    """
    found: dict[str, str] = {}
    for folder, archived in (("tasks", False), ("archive/tasks", True)):
        for path in sorted((root / "backlog" / folder).glob("*.md")):
            front = _FRONT.match(path.read_text(encoding="utf-8"))
            if front is None:
                continue
            fields = dict(line.split(": ", 1) for line in front[1].splitlines() if ": " in line)
            if "id" in fields:
                found[fields["id"].strip("'\"")] = "Archived" if archived else fields["status"]
    return found


def _owner_text(entry: Entry) -> str:
    if entry.approval is not None:
        a = entry.approval
        return f'excluded, "{a.quote}" ({a.date}, {a.source})'
    return str(entry.task)


SHOWN = 5  # tools named per endpoint: the name lookups of most tools touch the same few


def _tools(usage: Usage) -> str:
    names = [f"`{t}`" for t in sorted(usage.tools)]
    more = f" and {len(names) - SHOWN} more" if len(names) > SHOWN else ""
    return ", ".join(names[:SHOWN]) + more


def render(
    schema: Mapping[Operation, Fields],
    covered: Mapping[Operation, Usage],
    entries: Sequence[Entry],
) -> str:
    """The coverage page: every operation with its tools or owner, then the unused fields."""
    whole = {e.operation: e for e in entries if e.fields is None}
    endpoints: list[str] = []
    fields: list[str] = []
    for operation in sorted(schema, key=lambda o: (o[1], o[0])):
        usage = covered.get(operation)
        tools = _tools(usage) if usage else ""
        owner = _owner_text(whole[operation]) if operation in whole and usage is None else ""
        endpoints.append(f"| `{_text(operation)}` | {tools} | {owner} |")
        if usage is None:
            continue
        rest = sorted(unused(schema[operation], usage))
        for entry in (e for e in entries if e.operation == operation and e.fields is not None):
            mine = [f for f in rest if f in (entry.fields or ())]
            rest = [f for f in rest if f not in mine]
            if mine:
                names = ", ".join(f"`{f}`" for f in mine)
                fields.append(f"| `{_text(operation)}` | {names} | {_owner_text(entry)} |")
    return PAGE_HEAD + "\n".join(endpoints) + FIELDS_HEAD + "\n".join(fields) + "\n"


PAGE_HEAD = """\
# Paperless API coverage

<!-- Generated from the code and the tests by `just docs-coverage`. Do not edit by hand. -->

Every operation of the vendored schema (`docs/vendor/paperless/openapi.json`, Paperless-ngx
3.4.x) and the tools that cover it. A tool covers an operation when an adapter method it reaches
(through a service) sends that request in the adapter tests, which check every request against
the schema; a tool that only looks up names (tags, correspondents) is named too, and at most five
tools are shown. `paperless_api_get` (raw) never counts. An operation without a tool has the task
under which it is still open, or an exclusion the owner approved, quoted with its date from the
repository. The entries are in [api-coverage.toml](api-coverage.toml);
`tests/quality/test_api_coverage.py` fails when an operation or field is neither covered nor
listed, and when an open entry's task is missing or done.

## Endpoints

| Operation | Covered by | Open task or exclusion |
|---|---|---|
"""

FIELDS_HEAD = """

## Query parameters and body fields no tool uses

Of the operations a tool covers: what the schema offers and no adapter test sends, so a field
the adapter supports without a test of its own is listed too. Query parameters the generated
schema omits (such as `version`) are listed by hand in the TOML file.

| Operation | Fields | Open task or exclusion |
|---|---|---|
"""
