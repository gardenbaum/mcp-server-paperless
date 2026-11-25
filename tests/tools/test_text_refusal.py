"""Every tool refuses a lone surrogate wherever it takes text, and sends nothing (PLMCP-118).

The places come from the schemas the server offers, so a new tool or argument is in the sweep. Only
the place itself is filled: a tool that did not refuse first would name its missing arguments.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, cast

import observation
import pytest
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client, FastMCP
from harness import serve
from mcp.types import TextContent, Tool

from mcp_server_paperless.domain.texts import REFUSED_TEXT

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

BAD = "a\ud800b"
REASON = "is not valid text. Hint: Give the "


def places(schema: Json, defs: Json) -> Iterator[object]:
    """A value for each place of ``schema`` that takes free text, with a lone surrogate there."""
    if "$ref" in schema:
        yield from places(cast("Json", defs[str(schema["$ref"]).rsplit("/", 1)[-1]]), defs)
        return
    options = [
        s for key in ("anyOf", "oneOf", "allOf") for s in cast("list[Json]", schema.get(key, []))
    ]
    for option in options:
        yield from places(option, defs)
    if options or "enum" in schema or "const" in schema:
        return
    match schema.get("type"):
        case "string":
            yield BAD
        case "array":
            yield from ([v] for v in places(cast("Json", schema.get("items", {})), defs))
        case "object":
            yield from in_object(schema, defs)
        case None:  # takes anything
            yield from (BAD, [BAD], {"key": [BAD]}, {BAD: 1})
        case _:
            pass


def in_object(schema: Json, defs: Json) -> Iterator[Json]:
    for name, part in cast("dict[str, Json]", schema.get("properties", {})).items():
        yield from ({name: value} for value in places(part, defs))
    more = schema.get("additionalProperties", False)
    if more is not False:
        yield {BAD: 1}
        inner = more if isinstance(more, dict) else {}
        yield from ({"key": value} for value in places(inner, defs))


def calls(tools: list[tuple[str, Json]]) -> list[tuple[str, Json]]:
    return [
        (name, cast("Json", arguments))
        for name, schema in tools
        for arguments in in_object(schema, cast("Json", schema.get("$defs", {})))
    ]


def server(*, stdio: bool = False, root: str = "") -> FastMCP:
    """The whole surface; over stdio with the variants that read and save local files."""
    dirs = [root] if stdio else []
    fake = FakePaperless(seeded(), user="admin", allow_writes=True)
    fake.state.ai_enabled = True
    return serve(fake, toolsets=["all"], stdio=stdio, upload_dirs=dirs, download_dirs=dirs)


def surface(tools: list[Tool]) -> list[tuple[str, Json]]:
    return [(tool.name, tool.input_schema) for tool in tools]


async def unrefused(offered: FastMCP) -> list[str]:
    """What did not refuse with the reason, or asked Paperless for something."""
    before = observation.RECORDING
    observation.RECORDING = True
    found: list[str] = []
    try:
        async with Client(offered) as client:
            for name, arguments in calls(surface(await client.list_tools())):
                observation.PORT_CALLS.clear()
                try:
                    result = await client.call_tool(name, arguments, raise_on_error=False)
                except UnicodeEncodeError:  # an answer that quotes the text cannot be sent either
                    found.append(f"{name} {arguments!r}: no answer")
                    continue
                text = " ".join(c.text for c in result.content if isinstance(c, TextContent))
                if not result.is_error or REASON not in text or observation.PORT_CALLS:
                    found.append(f"{name} {arguments!r}: {text[:150]} {observation.PORT_CALLS}")
    finally:
        observation.RECORDING = before
        observation.PORT_CALLS.clear()
    return found


@pytest.mark.parametrize("stdio", [False, True])
async def test_every_text_of_every_tool_is_refused_and_nothing_is_sent(
    tmp_path: Path, *, stdio: bool
) -> None:
    assert await unrefused(server(stdio=stdio, root=str(tmp_path))) == []


async def test_the_sweep_knows_the_places_the_task_names(tmp_path: Path) -> None:
    async with Client(server(stdio=True, root=str(tmp_path))) as client:
        tools = await client.list_tools()
        writes = {
            tool.name
            for tool in tools
            if not (tool.annotations and tool.annotations.read_only_hint)
        }
    swept = calls(surface(tools))

    for wanted in (
        ("update_document", {"title": BAD}),
        ("update_documents", {"documents": [{"title": BAD}]}),
        ("create_taxonomy_item", {"name": BAD}),
        ("create_taxonomy_item", {"select_options": [BAD]}),
        ("add_note", {"note": BAD}),
        ("upload_document", {"custom_fields": {"key": BAD}}),
        ("upload_document", {"custom_fields": {BAD: 1}}),
        ("upload_documents", {"paths": [BAD]}),
    ):
        assert wanted in swept, [a for n, a in swept if n == wanted[0]]
    # Write tools without any text: these take ids, flags and choices only.
    assert writes - {name for name, _ in swept} == set()


async def test_a_new_tool_that_does_not_refuse_is_found() -> None:
    """The sweep is no check of a list: a server without the refusal fails it."""
    offered = FastMCP("without")

    @offered.tool
    async def plain(name: str) -> str:
        return name.encode("ascii", "replace").decode()

    @offered.tool
    async def nested(rows: list[dict[str, list[str]]], other: int = 0) -> int:
        return len(rows) + other

    found = await unrefused(offered)

    assert [line.split(":")[0] for line in found] == [
        "plain {'name'",
        r"nested {'rows'",
        r"nested {'rows'",
    ]


async def test_the_refusal_names_the_place_and_repeats_nothing_of_the_text() -> None:
    async with Client(server()) as client:
        for arguments, where in (
            ({"title": "secret \udfff"}, "title"),
            ({"title": "fine", "custom_fields": {"Amount": ["x", BAD]}}, "custom_fields.Amount.1"),
            ({"custom_fields": {BAD: 1}}, "name of a value in custom_fields"),
            ({BAD: 1}, "name of an argument"),
            ({REFUSED_TEXT: "title"}, "title"),
        ):
            result = await client.call_tool("update_document", arguments, raise_on_error=False)
            text = cast("TextContent", result.content[0]).text
            assert text == (
                f"The {where} is not valid text. Hint: Give the {where} in plain characters: "
                "it holds half of a character (a lone surrogate)."
            )


async def test_whole_characters_outside_the_basic_plane_are_text() -> None:
    async with Client(server()) as client:
        _ = await client.call_tool("add_note", {"document_id": 1, "note": "\U0001f9fe � \0x"[:5]})


# --- the wire of a real stdio server --------------------------------------------------------------

STDIO = """
import sys
sys.path[:0] = [{tests!r}]
import observation
observation.RECORDING = True
from fakes.paperless import FakePaperless, seeded
from harness import serve
from mcp_server_paperless.cli import run_stdio
run_stdio(
    serve(
        FakePaperless(seeded(), user="admin", allow_writes=True),
        toolsets=["all"],
        stdio=True,
        upload_dirs=[{root!r}],
        download_dirs=[{root!r}],
    )
)
print("CALLS", *{{call.tool for call in observation.PORT_CALLS if call.tool}}, file=sys.stderr)
"""
START: Json = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "x", "version": "1"},
    },
}


def test_every_text_is_refused_on_the_wire_of_stdio_too(tmp_path: Path) -> None:
    """JSON.stringify sends a lone surrogate as an escape, which the SDK drops without an answer."""
    tests = str(Path(__file__).parents[1])
    log = tmp_path / "log"
    with (
        log.open("w") as stderr,
        subprocess.Popen(  # noqa: S603 -- this interpreter and a script of this file
            [sys.executable, "-c", STDIO.format(tests=tests, root=str(tmp_path))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            env={},
        ) as process,
    ):
        # Without an answer there is nothing to read: the end of the process ends the wait.
        limit = threading.Timer(90, process.kill)
        limit.start()

        def ask(message: Json) -> Json:
            assert process.stdin
            assert process.stdout
            _ = process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            for line in cast("Iterator[str]", process.stdout):
                answer = cast("Json", json.loads(line))
                if answer.get("id") == message["id"]:
                    return answer
            pytest.fail(f"no answer to {message}")

        def call(number: object, name: str, arguments: Json, **more: object) -> Json:
            params = {"name": name, "arguments": arguments, **more}
            return ask({"jsonrpc": "2.0", "id": number, "method": "tools/call", "params": params})

        try:
            _ = ask(START)
            assert process.stdin
            _ = process.stdin.write('{"jsonrpc": "2.0", "method": "notifications/initialized"}\n')
            listed = cast(
                "Json", ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]
            )
            tools = [
                (str(t["name"]), cast("Json", t["inputSchema"]))
                for t in cast("list[Json]", listed["tools"])
            ]
            swept = calls(tools)
            for number, (name, arguments) in enumerate(swept, 10):
                result = cast("Json", call(number, name, arguments)["result"])
                assert result["isError"], (name, arguments)
                assert REASON in str(result["content"]), (name, arguments, result)
            # What is no argument is replaced, so it has an answer, too.
            unknown = call("unknown", BAD, {}, _meta={"x": BAD})
            _ = process.stdin.write("no json \\ud800\n")
            fine = call("fine", "add_note", {"document_id": 1, "note": "\U0001f9fe \\ud800"})
            process.stdin.close()
            _ = process.wait(20)
        finally:
            limit.cancel()
            process.kill()

    assert len(swept) > 300
    assert "Unknown tool" in str(unknown)
    assert "\U0001f9fe \\ud800" in str(cast("Json", fine["result"])["structuredContent"])
    assert [line for line in log.read_text().splitlines() if "CALLS" in line] == ["CALLS add_note"]
