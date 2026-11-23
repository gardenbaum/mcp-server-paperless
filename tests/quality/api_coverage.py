"""Checks and the table behind the coverage of the Paperless API (PLMCP-91).

Nothing here is a claim. What the tools send comes from the tool tests: which port method a tool
calls with which arguments is recorded, and the real adapter says which request that is
(`tests/quality/derive.py`). The hand-written part is only what is still open (a task), excluded
(an owner approval found in the repository) or sent by the adapter on its own.
"""

import re
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, NamedTuple, cast

from observation import Operation, scalar_text

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from observation import Observation

type Json = dict[str, object]

RAW_PORT = (
    "RawReader"  # the tool that sends any GET itself covers nothing in particular (decision-25)
)
CLOSED = frozenset({"Done", "Archived"})  # a task in one of these cannot own an open entry
METHODS = ("get", "put", "post", "patch", "delete")
SAVED_VIEW_TOOL = "run_saved_view"
# What shows that the owner, and nobody else, approved something.
OWNER_APPROVED = re.compile(
    r"owner\s+(?:has\s+)?(?:approved|accepted|agreed|decided)|(?:approved|accepted|agreed)\s+by\s+the\s+owner",
    re.IGNORECASE,
)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SHOWN = 5  # tools named per endpoint: the name lookups of most tools touch the same few


@dataclass(frozen=True)
class Fields:
    """What the schema offers for an operation, as `query:x` names and `body:a.b` paths."""

    query: frozenset[str]
    body: frozenset[str]
    enums: Mapping[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])


class Usage(NamedTuple):
    """The tools that reach an endpoint, and for each field and value the tools that send it."""

    tools: frozenset[str]
    used: Mapping[str, frozenset[str]]


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
    fields: frozenset[str] | None  # `query:x`, `body:y`, `body:y=value`; None: the endpoint itself
    task: str | None
    approval: Approval | None


def _schemas(schema: Json) -> Json:
    return cast("Json", cast("Json", schema["components"])["schemas"])


@dataclass
class _Found:
    """What a descent through a schema collected."""

    paths: set[str] = field(default_factory=set[str])
    enums: dict[str, set[str]] = field(default_factory=dict[str, set[str]])


def _walk(schema: Json, node: object, path: str, chain: frozenset[str], found: _Found) -> None:
    """Collect the property paths and enum values below `node` (a schema once per chain)."""
    here = cast("Json", node)
    if "$ref" in here:
        ref = str(here["$ref"])
        if ref not in chain:
            target = _schemas(schema)[ref.rsplit("/", 1)[-1]]
            _walk(schema, target, path, chain | {ref}, found)
        return
    listed = cast("list[object]", here.get("enum", [])) or (
        [True, False] if here.get("type") == "boolean" else []
    )
    texts = {scalar_text(v) for v in listed if v not in (None, "")}
    if texts - {None}:
        found.enums.setdefault(path, set()).update(cast("set[str]", texts - {None}))
    for key in ("allOf", "oneOf", "anyOf"):
        for part in cast("list[object]", here.get(key, [])):
            _walk(schema, part, path, chain, found)
    for name, sub in cast("Json", here.get("properties", {})).items():
        child = f"{path}.{name}" if path else name
        found.paths.add(child)
        _walk(schema, sub, child, chain, found)
    if "items" in here:
        _walk(schema, here["items"], f"{path}[]", chain, found)


def schema_fields(
    schema: Json, undeclared: Mapping[Operation, frozenset[str]]
) -> dict[Operation, Fields]:
    """Every operation of the schema with its query parameters, body paths and enum values.

    `undeclared` adds the query parameters the generated schema omits (listed by hand).
    """
    fields: dict[Operation, Fields] = {}
    for path, item in cast("dict[str, Json]", schema["paths"]).items():
        shared = cast("list[Json]", item.get("parameters", []))
        for method in METHODS:
            if method not in item:
                continue
            operation = cast("Json", item[method])
            query: set[str] = set()
            in_query = _Found()
            for parameter in shared + cast("list[Json]", operation.get("parameters", [])):
                if parameter["in"] == "query":
                    query.add(str(parameter["name"]))
                    _walk(
                        schema,
                        parameter.get("schema", {}),
                        str(parameter["name"]),
                        frozenset(),
                        in_query,
                    )
            in_body = _Found()
            content = cast(
                "Json", cast("Json", operation.get("requestBody", {})).get("content", {})
            )
            for media in content.values():
                _walk(schema, cast("Json", media).get("schema", {}), "", frozenset(), in_body)
            # A list parameter is sent once per value: `status=failure`, not `status[]=failure`.
            enums = {f"query:{n.removesuffix('[]')}": v for n, v in in_query.enums.items()}
            enums |= {f"body:{n}": v for n, v in in_body.enums.items() if n}
            key = (method.upper(), path)
            fields[key] = Fields(
                frozenset(query | undeclared.get(key, frozenset())),
                frozenset(in_body.paths),
                {name: frozenset(values) for name, values in enums.items()},
            )
    return fields


def covering(observed: Iterable[Observation]) -> dict[Operation, Usage]:
    """The endpoints some tool causes a request to, with the fields and values each tool sends."""
    tools: dict[Operation, set[str]] = defaultdict(set)
    used: dict[Operation, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for seen in observed:
        if seen.tool is None or seen.call[0] == RAW_PORT:
            continue
        tools[seen.operation].add(seen.tool)
        keys = {f"query:{n}" for n in seen.query} | {f"body:{p}" for p in seen.body} | seen.values
        for key in keys:
            used[seen.operation][key].add(seen.tool)
    return {
        operation: Usage(
            frozenset(names), {key: frozenset(who) for key, who in used[operation].items()}
        )
        for operation, names in tools.items()
    }


def field_keys(fields: Fields) -> set[str]:
    """Every field and enum value of an operation as one key each."""
    keys = {f"query:{n}" for n in fields.query} | {f"body:{p}" for p in fields.body}
    return keys | {f"{name}={value}" for name, values in fields.enums.items() for value in values}


def unused(fields: Fields, usage: Usage, internal: frozenset[str] | None = None) -> set[str]:
    """What the schema offers and no tool sends; `internal` fields count as unsent."""
    keys = field_keys(fields)
    return (keys - set(usage.used)) | (keys & (internal or frozenset()))


def _text(operation: Operation) -> str:
    return f"{operation[0]} {operation[1]}"


def _bad_date(text: str) -> bool:
    """Whether `text` is no calendar date written like 2026-11-23."""
    if ISO_DATE.fullmatch(text) is None:
        return True
    try:
        _ = date.fromisoformat(text)
    except ValueError:
        return True
    return False


def _approval_problem(approval: Approval, root: Path) -> str | None:
    """Why an approval does not prove that the owner approved, or None if it does."""
    path = (root / approval.source).resolve()
    if not approval.quote.strip():
        return "the approval has no quote"
    if _bad_date(approval.date):
        return f"the approval's date {approval.date!r} is not a date like 2026-11-23"
    if (root / "backlog").resolve() not in path.parents or path.suffix != ".md":
        return f"{approval.source} is no Markdown file of backlog/ (decisions, tasks, docs)"
    paragraphs = re.split(r"\n\s*\n", path.read_text(encoding="utf-8")) if path.is_file() else []
    if any(
        approval.quote in p and approval.date in p and OWNER_APPROVED.search(p) for p in paragraphs
    ):
        return None
    return (
        f"no paragraph of {approval.source} holds the quote, the date {approval.date} and "
        "words that show the owner approved it"
    )


def _owner(entry: Entry, statuses: Mapping[str, str], root: Path) -> list[str]:
    """What is wrong with who owns an entry: a task that is there and not Done, or an approval."""
    where = _text(entry.operation)
    if entry.approval is not None:
        problem = _approval_problem(entry.approval, root)
        return [] if problem is None else [f"{where}: {problem}"]
    if entry.task is None:
        return [f"{where}: the entry has neither a task nor an owner approval"]
    status = statuses.get(entry.task)
    if status is None:
        return [f"{where}: task {entry.task} does not exist"]
    if status in CLOSED:
        return [f"{where}: task {entry.task} is {status} but the entry is still open"]
    return []


def _entry_problems(
    schema: Mapping[Operation, Fields],
    covered: Mapping[Operation, Usage],
    entries: Sequence[Entry],
) -> list[str]:
    """Entries that name nothing, name something twice or sit on the wrong kind of endpoint."""
    found: list[str] = []
    listed: Counter[tuple[Operation, str | None]] = Counter()
    for entry in entries:
        where = _text(entry.operation)
        if entry.operation not in schema:
            found.append(f"{where}: no such operation in the schema")
        elif entry.fields is None and entry.operation in covered:
            found.append(f"{where}: listed, but a tool already covered it")
        elif entry.fields is not None and entry.operation not in covered:
            found.append(
                f"{where}: has field entries, but no tool covers the operation (not covered)"
            )
        for name in entry.fields or {None}:
            listed[(entry.operation, name)] += 1
    found += [
        f"{_text(operation)}: {name or 'the endpoint'} is listed twice"
        for (operation, name), count in sorted(listed.items(), key=str)
        if count > 1
    ]
    return found


def problems(  # noqa: PLR0913 -- the inputs of the check
    schema: Mapping[Operation, Fields],
    covered: Mapping[Operation, Usage],
    entries: Sequence[Entry],
    statuses: Mapping[str, str],
    *,
    root: Path,
    internal: Mapping[Operation, frozenset[str]] | None = None,
) -> list[str]:
    """Everything that is wrong: gaps, stale entries, missing tasks, approvals not in the repo."""
    internal = internal or {}
    found = _entry_problems(schema, covered, entries)
    for entry in entries:
        found += _owner(entry, statuses, root)
    whole = {e.operation for e in entries if e.fields is None}
    named: dict[Operation, set[str]] = defaultdict(set)
    for entry in entries:
        named[entry.operation] |= entry.fields or set()
    for operation, fields in schema.items():
        if operation not in covered:
            if operation not in whole:
                found.append(f"{_text(operation)}: neither covered nor listed")
            continue
        sent = internal.get(operation, frozenset())
        found += [
            f"{_text(operation)}: {name} is listed as internal, but the schema lacks it"
            for name in sorted(sent - field_keys(fields))
        ]
        free = unused(fields, covered[operation], sent)
        listed = named.get(operation, set())
        found += [
            f"{_text(operation)}: {name} is used by no tool and not listed"
            for name in sorted(free - listed)
        ]
        found += [
            f"{_text(operation)}: {name} is listed, but a tool uses it or the schema lacks it"
            for name in sorted(listed - free)
        ]
    return found


def undeclared_problems(schema: Json, undeclared: Mapping[Operation, frozenset[str]]) -> list[str]:
    """Hand-listed query parameters that the schema has after all, or for a missing operation."""
    declared = schema_fields(schema, {})
    found: list[str] = []
    for operation, params in sorted(undeclared.items()):
        if operation not in declared:
            found.append(f"{_text(operation)}: no such operation in the schema")
            continue
        found += [
            f"{_text(operation)}: query:{name} is declared by the schema"
            for name in sorted(params & declared[operation].query)
        ]
    return found


@dataclass(frozen=True)
class Entries:
    """The hand-written file: entries, parameters the schema omits and fields sent internally."""

    entries: list[Entry]
    undeclared: dict[Operation, frozenset[str]]
    internal: dict[Operation, frozenset[str]]


def _operation(text: str) -> Operation:
    method, path = text.split(" ", 1)
    return method, path


def load_entries(path: Path) -> Entries:
    """Read the sections `open`, `open_fields`, `exclusion`, `undeclared` and `internal`."""
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
    internal = {
        _operation(str(row["operation"])): frozenset(cast("list[str]", row["fields"]))
        for row in data.get("internal", [])
    }
    return Entries(entries, undeclared, internal)


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


def _tools(usage: Usage) -> str:
    names = [f"`{t}`" for t in sorted(usage.tools)]
    more = f" and {len(names) - SHOWN} more" if len(names) > SHOWN else ""
    return ", ".join(names[:SHOWN]) + more


def render(
    schema: Mapping[Operation, Fields],
    covered: Mapping[Operation, Usage],
    entries: Sequence[Entry],
    internal: Mapping[Operation, frozenset[str]],
) -> str:
    """The coverage page: every operation with its tools or owner, then the fields no tool sends."""
    whole = {e.operation: e for e in entries if e.fields is None}
    endpoints: list[str] = []
    fields: list[str] = []
    views: list[str] = []
    for operation in sorted(schema, key=lambda o: (o[1], o[0])):
        usage = covered.get(operation)
        tools = _tools(usage) if usage else ""
        owner = _owner_text(whole[operation]) if operation in whole and usage is None else ""
        endpoints.append(f"| `{_text(operation)}` | {tools} | {owner} |")
        if usage is None:
            continue
        sent = internal.get(operation, frozenset())
        rest = sorted(unused(schema[operation], usage, sent))
        for entry in (e for e in entries if e.operation == operation and e.fields is not None):
            mine = [f for f in rest if f in (entry.fields or ())]
            rest = [f for f in rest if f not in mine]
            if mine:
                names = ", ".join(f"`{f}`" for f in mine)
                fields.append(f"| `{_text(operation)}` | {names} | {_owner_text(entry)} |")
        only = sorted(
            key
            for key, who in usage.used.items()
            if who == frozenset({SAVED_VIEW_TOOL}) and key not in sent
        )
        if only:
            views.append(f"| `{_text(operation)}` | {', '.join(f'`{k}`' for k in only)} |")
    return (
        PAGE_HEAD
        + "\n".join(endpoints)
        + FIELDS_HEAD
        + "\n".join(fields)
        + VIEWS_HEAD
        + "\n".join(views)
        + "\n"
    )


PAGE_HEAD = """\
# Paperless API coverage

<!-- Generated from the tool tests by `just docs-coverage`. Do not edit by hand. -->

Every operation of the vendored schema (`docs/vendor/paperless/openapi.json`, Paperless-ngx
3.4.x) and the tools that cover it. The tool tests run every tool with its arguments; each call a
tool makes to a port is recorded and handed to the real adapter, which builds the request. A tool
covers an operation when one of its calls leads to that request. `paperless_api_get` (raw) never
counts. An operation without a tool has the task under which it is still open, or an exclusion the
owner approved, quoted with its date from the repository. A tool that only looks up names (tags,
correspondents) is named too; at most five tools are shown. The entries are in
[api-coverage.toml](api-coverage.toml); `tests/quality/test_api_coverage.py` fails when an
operation, field or enum value is neither covered nor listed, and when an open entry's task is
missing or done.

Limits. A field counts as sent only when some tool test makes a tool send it, so a field a tool
supports and no tool test uses is listed as open. The requests are built from recorded port calls
and answered with responses taken from the adapter tests, so a request that depends on a later
answer of Paperless may be missed. The adapter's own requests, and those of streamed bodies, are
recorded but not checked against the schema; the adapter tests check the others. Fields the
adapter sends by itself (the `[[internal]]` entries) are listed as open although a tool causes
them.

## Endpoints

| Operation | Covered by | Open task or exclusion |
|---|---|---|
"""

FIELDS_HEAD = """

## Query parameters, body fields and enum values no tool sends

Of the operations a tool covers: what the schema offers and no tool sends. `body:a.b` is a field of
an object, `body:a[].b` one of the objects in a list, and `body:method=rotate` one value of an
enum. Query parameters the generated schema omits (such as `version`) are listed by hand in the
TOML file.

| Operation | Fields | Open task or exclusion |
|---|---|---|
"""

VIEWS_HEAD = """

## Fields only a saved view reaches

These are sent only when `run_saved_view` runs a saved view that holds them; no tool takes them as
an argument.

| Operation | Fields |
|---|---|
"""
