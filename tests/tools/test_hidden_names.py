"""Reading tools name no tool the operator hides (PLMCP-90.3, decision-111).

Hidden is what read-only mode, a read-only toolset or a disabled tool takes away. A toolset that is
only off is no secret: the overview names its tools (decision-41). Writing tools are not covered.
"""

from typing import TYPE_CHECKING, Annotated, Literal, cast

import pytest
from fastmcp import Client, FastMCP
from harness import admin, serve
from hidden_names import EVERY, READING, named
from mcp.types import Tool
from pydantic import Field

from mcp_server_paperless.tools._shared import TOOL_TOOLSETS, TOOLSETS
from mcp_server_paperless.tools.passages import PASSAGES, HiddenNames, Passage

if TYPE_CHECKING:
    from collections.abc import Collection

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-111")]

WRITING = sorted(EVERY - READING)


async def listed(
    *,
    read_only: Collection[str] = (),
    everywhere: bool = False,
    disabled: tuple[str, ...] = (),
    directory: str | None = None,
) -> dict[str, Tool]:
    """tools/list with every toolset on, these read-only; with a directory over stdio."""
    directories = [directory] if directory else []
    server = serve(
        admin(ai_enabled=True),
        toolsets=[f"{name}:read" if name in read_only else name for name in sorted(TOOLSETS)],
        read_only=everywhere,
        disabled_tools=disabled,
        stdio=directory is not None,
        upload_dirs=directories,
        download_dirs=directories,
    )
    async with Client(server) as client:
        return {tool.name: tool for tool in await client.list_tools()}


def problems(tools: dict[str, Tool]) -> dict[str, set[str]]:
    """The reading tools that name a tool which is not offered, and which."""
    hidden = EVERY - set(tools)
    found = {name: named(tool, hidden) for name, tool in tools.items() if name in READING}
    return {name: names for name, names in found.items() if names}


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("toolset", sorted(TOOLSETS))
async def test_no_reading_tool_names_what_a_read_only_toolset_hides(
    tmp_path_factory: pytest.TempPathFactory, toolset: str, *, local: bool
) -> None:
    directory = str(tmp_path_factory.mktemp("scans")) if local else None

    tools = await listed(read_only=frozenset({toolset}), directory=directory)

    allowed = set[str]() if local else {"upload_documents"}  # a tool of stdio only
    assert {
        name: names - allowed for name, names in problems(tools).items() if names - allowed
    } == {}


async def test_no_reading_tool_names_a_hidden_one_where_everything_is_read_only() -> None:
    assert problems(await listed(read_only=frozenset(TOOLSETS))) == {}
    assert problems(await listed(everywhere=True)) == {}


@pytest.mark.parametrize("disabled", WRITING)
async def test_no_reading_tool_names_a_writing_tool_that_is_disabled(disabled: str) -> None:
    assert problems(await listed(disabled=(disabled,))) == {}


@pytest.mark.parametrize("disabled", sorted(READING & set(PASSAGES)))
async def test_each_text_changes_only_where_a_tool_it_names_is_hidden(disabled: str) -> None:
    """Disabling a reading tool changes no text of the others."""
    everything = await listed()
    without = await listed(disabled=(disabled,))

    assert without == {name: tool for name, tool in everything.items() if name != disabled}


async def test_only_the_marked_texts_change_and_only_by_their_passages() -> None:
    everything = await listed()
    for toolset in sorted(TOOLSETS):
        tools = await listed(read_only=frozenset({toolset}))
        hidden = {name for name in WRITING if TOOL_TOOLSETS.get(name) == toolset}
        changed = {name for name, tool in tools.items() if tool != everything[name]}
        assert changed == {
            name
            for name, passages in PASSAGES.items()
            if name in tools and any(passage.tools <= hidden for passage in passages)
        }, toolset
        for name in changed:
            before = everything[name].model_dump(mode="json")
            after = tools[name].model_dump(mode="json")
            # Descriptions shrink; names, parameters and types are the same.
            assert skeleton(before) == skeleton(after)


def skeleton(value: object) -> object:
    """A JSON value without the texts of its descriptions."""
    if isinstance(value, dict):
        return {
            key: "" if key == "description" and isinstance(inner, str) else skeleton(inner)
            for key, inner in cast("dict[str, object]", value).items()
        }
    if isinstance(value, list):
        return [skeleton(inner) for inner in cast("list[object]", value)]
    return value


async def test_every_passage_is_in_a_text_of_its_reading_tool_and_names_what_it_is_for() -> None:
    tools = await listed()
    assert set(PASSAGES) <= READING
    for name, passages in PASSAGES.items():
        for passage in passages:
            texts = "\n".join(descriptions(tools[name].model_dump(mode="json")))
            assert passage.text in texts, (name, passage)
            assert passage.tools <= set(WRITING), (name, passage)
            for tool in passage.tools:
                assert tool in passage.text, (name, passage)
                assert tool not in passage.instead, (name, passage)
            assert len(passage.instead) < len(passage.text), (name, passage)


def descriptions(value: object) -> list[str]:
    if isinstance(value, dict):
        found: list[str] = []
        for key, inner in cast("dict[str, object]", value).items():
            found += [inner] if key == "description" and isinstance(inner, str) else []
            found += descriptions(inner)
        return found
    if isinstance(value, list):
        return [text for inner in cast("list[object]", value) for text in descriptions(inner)]
    return []


@pytest.mark.parametrize(
    ("toolset", "tool", "text"),
    [
        ("trash", "list_trash", "pass it to get_trash_entry or get_trashed_file), the title"),
        ("trash", "get_trash_entry", "Restore it first to read\nthat."),
        ("trash", "get_trashed_file", "so restore the document first to get those."),
        ("views", "run_saved_view", "naming the rule type. count is the total"),
        ("sharing", "list_share_links", "in Paperless. count is the total"),
        ("sharing", "list_share_bundles", "last_error says why), the size and the expiry."),
        ("permissions", "list_users", "Use it to find the usernames and ids. Unlike"),
        ("permissions", "get_permissions", "nothing changes.\n\nA user or group you may not"),
        ("tasks", "list_tasks", "Each row has the database id, the task_id get_task takes"),
        ("workflows", "list_workflows", "not at its default. Webhook header"),
        ("admin", "get_app_config", "Any signed-in user may read it."),
    ],
)
async def test_what_is_left_reads_as_a_sentence(toolset: str, tool: str, text: str) -> None:
    tools = await listed(read_only=frozenset({toolset}))

    assert text in (tools[tool].description or "")


@pytest.mark.parametrize(
    ("disabled", "text"),
    [
        ("restore_from_trash", "(pass it to get_trash_entry, get_trashed_file or\nempty_trash)"),
        ("empty_trash", "(pass it to get_trash_entry, get_trashed_file or restore_from_trash)"),
    ],
)
async def test_one_of_two_named_tools_keeps_the_other(disabled: str, text: str) -> None:
    tools = await listed(disabled=(disabled,))

    assert text in (tools["list_trash"].description or "")


async def test_self_test_a_name_in_any_text_of_a_tool_is_found_and_a_value_is_not() -> None:
    def tool(**fields: object) -> Tool:
        return Tool.model_validate({"name": "list_tags", "inputSchema": {}, **fields})

    def parameter(**schema: object) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string", **schema}}}

    texts = [
        tool(title="See empty_trash"),
        tool(description="Then call empty_trash."),
        tool(inputSchema=parameter(description="For `empty_trash`.")),
        tool(outputSchema=parameter(description="Of empty_trash")),
        tool(inputSchema={"type": "object", "properties": {"empty_trash": {"type": "string"}}}),
    ]
    values = [
        tool(inputSchema=parameter(enum=["empty_trash"])),
        tool(inputSchema=parameter(const="empty_trash")),
        tool(description="emptying_trash, empty_trashes"),
    ]

    assert [named(t, EVERY) for t in texts] == [{"empty_trash"}] * len(texts)
    assert [named(t, EVERY) for t in values] == [set()] * len(values)


async def test_a_passage_is_taken_out_of_parameters_and_values_stay() -> None:
    """No parameter of a reading tool names a hidden tool today; the next one may."""
    passage = Passage(frozenset({"drop_it"}), " See drop_it.")

    def listing(hidden: set[str]) -> FastMCP:
        server = FastMCP("t", transforms=[HiddenNames(hidden, {"list_it": (passage,)})])

        @server.tool(description="List. See drop_it.")
        def list_it(
            kind: Annotated[Literal["a See drop_it."], Field(description="A kind. See drop_it.")],
            most: Annotated[int, Field(description="At most. See drop_it.")] | None = None,
        ) -> str:
            return f"{kind}{most}"

        assert list_it
        return server

    [whole] = await listing({"other"}).list_tools()
    [cut] = await listing({"drop_it"}).list_tools()

    assert whole.description == "List. See drop_it."
    assert cut.description == "List."
    before, after = (
        cast("dict[str, dict[str, dict[str, object]]]", t.parameters)["properties"]["kind"]
        for t in (whole, cut)
    )
    assert before["description"] == "A kind. See drop_it."
    assert after == {**before, "description": "A kind."}
    assert "a See drop_it." in str(after)
    assert "At most. See drop_it." in str(whole.parameters)
    assert "'At most.'" in str(cut.parameters)  # inside the list of anyOf
