"""How to get uploads by path: said over stdio where they are off, and nowhere else (decision-47).

The operator enables them, never the assistant (decision-7, decision-41). Over HTTP nothing hints
at it, because no setting offers a path there.
"""

import base64
import json
from typing import TYPE_CHECKING, Final, cast

import pytest
from e2e.helpers import pdf
from fakes.paperless import FakePaperless, seeded
from fastmcp import Client
from harness import serve

from mcp_server_paperless.tools.upload_hint import PATH_OFF, PATH_REFUSED

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-7"),
    pytest.mark.decision("decision-47"),
]

type Json = dict[str, object]
TOOLS: Final = ("upload_document", "upload_document_version")
SETTING: Final = "UPLOAD_DIRS"
SECRET: Final = "/home/someone/secret.pdf"


def fake() -> FakePaperless:
    return FakePaperless(seeded(), user="admin", allow_writes=True)


def stdio(**settings: object) -> FastMCP:
    given: dict[str, object] = {"toolsets": ["all"], "max_upload_bytes": 2000} | settings
    return serve(fake(), stdio=True, **given)  # pyright: ignore[reportArgumentType] -- settings


def remote(**settings: object) -> FastMCP:
    given: dict[str, object] = {"toolsets": ["all"], "max_upload_bytes": 2000} | settings
    return serve(fake(), **given)  # pyright: ignore[reportArgumentType] -- settings


async def refusal(server: FastMCP, tool: str, arguments: Json) -> str:
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return "".join(getattr(block, "text", "") for block in result.content)


def content(data: str) -> Json:
    return {"document_id": 1, "content_base64": data, "filename": "x.pdf"}


BAD: Final = {
    "large": base64.b64encode(bytes(4000)).decode(),
    "invalid": "%%%",
    "empty": "====",
}


async def everything(server: FastMCP) -> str:
    """Every text a client can read without calling an upload tool."""
    async with Client(server) as client:
        tools = [t.model_dump_json() for t in await client.list_tools()]
        overview = await client.call_tool("get_instance_overview", {})
        instructions = client.initialize_result and client.initialize_result.instructions
    return json.dumps([tools, instructions, str(overview.structured_content)])


@pytest.mark.parametrize("tool", TOOLS)
async def test_the_description_says_who_enables_a_path_only_over_stdio_without_one(
    tool: str, tmp_path: Path
) -> None:
    async def description(server: FastMCP) -> str:
        async with Client(server) as client:
            [found] = [t for t in await client.list_tools() if t.name == tool]
        return found.description or ""

    off = await description(stdio())
    assert off.endswith(PATH_OFF)
    assert "operator, not you" in off
    assert off == f"{await description(remote())} {PATH_OFF}"
    assert SETTING not in await description(stdio(upload_dirs=[str(tmp_path)]))


@pytest.mark.parametrize("tool", TOOLS)
async def test_the_schema_and_the_annotations_are_those_of_the_remote_tool(tool: str) -> None:
    async def shape(server: FastMCP) -> Json:
        async with Client(server) as client:
            [found] = [t for t in await client.list_tools() if t.name == tool]
        return cast("Json", found.model_dump(exclude={"description"}))

    assert await shape(stdio()) == await shape(remote())


async def test_over_http_nothing_tells_of_the_setting(tmp_path: Path) -> None:
    for server in (remote(), remote(upload_dirs=[str(tmp_path)])):
        assert SETTING not in await everything(server)
        for tool in TOOLS:
            for data in BAD.values():
                assert SETTING not in await refusal(server, tool, content(data))
            with_path = {**content(BAD["invalid"]), "path": SECRET}
            assert SETTING not in await refusal(server, tool, with_path)


@pytest.mark.parametrize("bad", BAD)
async def test_a_refused_content_says_how_a_path_is_enabled(bad: str) -> None:
    arguments = content(BAD[bad])
    version = await refusal(stdio(), "upload_document_version", arguments)
    del arguments["document_id"]
    document = await refusal(stdio(), "upload_document", arguments)

    for text in (document, version):
        assert text.endswith(PATH_OFF)
        assert "Hint: " in text


async def test_a_refused_content_says_nothing_when_a_path_is_enabled(tmp_path: Path) -> None:
    arguments = content(BAD["invalid"])
    del arguments["document_id"]

    text = await refusal(stdio(upload_dirs=[str(tmp_path)]), "upload_document", arguments)

    assert SETTING not in text


@pytest.mark.parametrize("tool", TOOLS)
async def test_a_call_with_a_path_is_told_and_the_path_is_not_repeated(tool: str) -> None:
    text = await refusal(stdio(), tool, {"document_id": 1, "path": SECRET})

    assert text == f"{PATH_REFUSED} Hint: {PATH_OFF}"
    assert SECRET not in text


async def test_other_tools_and_other_calls_are_not_touched() -> None:
    other = await refusal(stdio(), "get_document", {"document_id": 1, "path": SECRET})
    off = await refusal(stdio(toolsets=["core"]), TOOLS[1], {"path": SECRET})
    disabled = await refusal(stdio(disabled_tools=[TOOLS[0]]), TOOLS[0], {"path": SECRET})
    async with Client(stdio()) as client:
        done = await client.call_tool(
            "upload_document",
            {"content_base64": base64.b64encode(pdf("x")).decode(), "filename": "x.pdf"},
        )
        none = await client.call_tool("upload_document", raise_on_error=False)

    for text in (other, off, disabled, str(none.content)):
        assert PATH_REFUSED not in text
    assert done.structured_content is not None
