"""paperless_api_get through the in-memory client on the seeded fake (decision-25)."""

from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeTag, seeded
from fastmcp import Client
from harness import admin, serve

from paperless_mcp.server import CATALOGUE

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-25")]

type Json = dict[str, object]
TOOL = "paperless_api_get"


async def call(arguments: Json, fake: FakePaperless | None = None, **settings: object) -> Json:
    _ = settings.setdefault("toolsets", ["raw"])
    async with Client(serve(fake or admin(), **settings)) as client:  # pyright: ignore[reportArgumentType] -- harness keywords
        result = await client.call_tool(TOOL, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def error_text(arguments: Json) -> str:
    async with Client(serve(admin(), toolsets=["raw"])) as client:
        result = await client.call_tool(TOOL, arguments, raise_on_error=False)
    assert result.is_error
    return str(cast("Json", result.content[0].model_dump())["text"])


async def offered(**settings: object) -> set[str]:
    async with Client(serve(admin(), **settings)) as client:  # pyright: ignore[reportArgumentType] -- harness keywords
        return {tool.name for tool in await client.list_tools()}


async def test_the_toolset_is_off_by_default() -> None:
    assert TOOL not in await offered()
    assert TOOL not in await offered(toolsets=["core", "ai"])


async def test_the_toolset_is_offered_when_named_or_with_all() -> None:
    assert await offered(toolsets=["raw"]) == {TOOL}
    assert TOOL in await offered(toolsets=["all"])


async def test_read_only_mode_keeps_it() -> None:
    assert await offered(toolsets=["raw"], read_only=True) == {TOOL}


async def test_it_can_be_switched_off_by_name() -> None:
    assert TOOL not in await offered(toolsets=["all"], disabled_tools=[TOOL])


def test_the_tool_is_read_only_idempotent_and_closed_world() -> None:
    hints = CATALOGUE.annotations[TOOL]
    assert (hints.read_only_hint, hints.idempotent_hint, hints.open_world_hint) == (
        True,
        True,
        False,
    )
    assert hints.destructive_hint is not True
    assert CATALOGUE.tool_toolsets[TOOL] == "raw"


async def test_a_path_and_params_return_the_json() -> None:
    output = await call({"path": "/api/tags/", "params": {"page_size": 2}})

    assert output["status"] == 200
    assert output["truncated"] is False
    data = cast("Json", output["data"])
    assert data["count"] == 4
    assert len(cast("list[Json]", data["results"])) == 2


async def test_a_long_answer_is_cut_and_marked() -> None:
    state = seeded()
    state.tags.extend(FakeTag(id=100 + n, name=f"bulk-tag-{n:05d}") for n in range(1500))

    output = await call(
        {"path": "/api/tags/", "params": {"page_size": 1500}}, FakePaperless(state, user="admin")
    )

    assert output["truncated"] is True
    assert output["data"] is None
    assert len(str(output["text"])) == 20_000
    assert "page_size" in str(output["note"])


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("https://evil.test/api/tags/", "refused"),
        ("/api/../admin/", "refused"),
        ("/api/profile/", "holds secrets"),
        ("/api/documents/1/download/", "returns a file"),
        ("/api/tags", "redirected"),
        ("/api/nothing/", "redirected"),
    ],
)
async def test_refusals_and_failures_are_tool_errors(path: str, message: str) -> None:
    assert message in await error_text({"path": path})


async def test_a_bad_param_is_a_tool_error() -> None:
    assert "param" in await error_text({"path": "/api/tags/", "params": {"a b": 1}})
