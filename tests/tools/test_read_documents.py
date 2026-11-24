"""read_documents through the in-memory client: choices, fields, limits, read-only mode."""

from typing import TYPE_CHECKING, cast

import pytest
from fakes.accounts import PUBLIC_NOTE
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve
from mcp.types import TextContent

from mcp_server_paperless.domain.selection import MAX_ANSWER, SelectionFilters
from mcp_server_paperless.tools._shared import DOCUMENT_TEXT

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-69"),
    pytest.mark.decision("decision-70"),
]

type Json = dict[str, object]
NAME = "read_documents"
# Where a search tool only pages and orders.
PAGING = {"ordering", "page", "page_size"}


def fake(state: FakeState | None = None, *, user: str = "admin") -> FakePaperless:
    # No write is allowed: a read that writes fails its test.
    return FakePaperless(state or seeded(), user=user)


async def called(source: FakePaperless, **arguments: object) -> CallToolResult:
    async with Client(serve(source, toolsets=["bulk"])) as client:
        return await client.call_tool(NAME, arguments, raise_on_error=False)


async def call(source: FakePaperless, **arguments: object) -> Json:
    result = await called(source, **arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, **arguments: object) -> str:
    result = await called(source, **arguments)
    assert result.is_error
    return str(result.content)


def rows(output: Json) -> list[Json]:
    return cast("list[Json]", output["documents"])


def ids(output: Json) -> list[object]:
    return [row["id"] for row in rows(output)]


async def test_read_only_mode_leaves_exactly_this_tool_of_the_bulk_toolset() -> None:
    async with Client(serve(fake(), toolsets=["bulk"], read_only=True)) as client:
        names = {tool.name for tool in await client.list_tools()}
    async with Client(serve(fake())) as client:
        default = {tool.name for tool in await client.list_tools()}

    assert names == {NAME}
    assert NAME not in default


async def test_documents_are_chosen_by_ids_filters_or_view() -> None:
    by_ids = await call(fake(), document_ids=[2, 1, 99])
    by_filter = await call(fake(), filters={"tags_all": ["e2e-finance"]})
    by_view = await call(fake(), saved_view="e2e-view")
    without = await call(fake(), filters={"title_contains": "e2e-p"}, exclude_ids=[1])

    assert (ids(by_ids), by_ids["missing"]) == ([2, 1], [99])
    assert ids(by_filter) == ids(by_view) == [1]
    assert (ids(without), without["matched"], without["selected_ids"]) == ([2], 2, [2])
    assert set(rows(by_filter)[0]) == {
        *("id", "web_url", "title", "created", "correspondent", "document_type", "tags"),
    }


async def test_fields_and_text_come_on_request_only() -> None:
    bare = await call(fake(), document_ids=[1], fields=[])
    full = await call(fake(), document_ids=[1], fields=["notes", "content"], content_chars=3)

    assert set(rows(bare)[0]) == {"id", "web_url"}
    [row] = rows(full)
    assert set(row) == {"id", "web_url", "notes", "content", "content_total", "content_next"}
    assert (row["content"], row["content_next"]) == ("E2E", 3)
    assert cast("list[Json]", row["notes"])[0]["note"] == PUBLIC_NOTE


async def test_a_page_holds_twenty_five_documents_and_at_most_a_hundred() -> None:
    state = seeded()
    state.documents += [FakeDocument(id=n, title=f"scratch {n}") for n in range(10, 120)]
    chosen: Json = {"filters": {"title_contains": "scratch"}, "fields": []}

    first = await call(fake(state), **chosen)
    second = await call(fake(state), **chosen, page=2, page_size=100)

    assert (len(rows(first)), first["next_page"], first["matched"]) == (25, 2, 110)
    assert first["selected_ids"] == list(range(119, 9, -1))
    assert (ids(second), second["next_page"]) == (list(range(19, 9, -1)), None)
    assert "less than or equal to 100" in await refusal(fake(state), **chosen, page_size=101)


async def test_over_a_thousand_matches_are_refused_with_the_count() -> None:
    state = seeded()
    state.documents += [FakeDocument(id=n, title="scratch") for n in range(10, 1011)]

    text = await refusal(fake(state), filters={"title_contains": "scratch"}, fields=[])

    assert "1001 documents match; at most 1000 may." in text


async def test_the_answer_of_a_call_is_at_most_a_hundred_thousand_characters() -> None:
    state = seeded()
    state.documents += [
        FakeDocument(id=n, title=f"scratch {n}", content="ä" * 900, custom_fields={1: "v" * 2000})
        for n in range(10, 110)
    ]

    result = await called(
        fake(state),
        filters={"title_contains": "scratch"},
        fields=["custom_fields", "content"],
        page_size=100,
    )

    [block] = result.content
    assert isinstance(block, TextContent)
    assert len(block.text) <= MAX_ANSWER
    assert result.structured_content is not None
    assert result.structured_content["truncated"] is True
    left = cast("list[int]", result.structured_content["left"])
    assert set(left) | set(ids(result.structured_content)) == set(range(10, 110))


@pytest.mark.parametrize(
    ("arguments", "part"),
    [
        ({}, "No document is chosen"),
        ({"document_ids": []}, "at least 1 item"),
        ({"document_ids": list(range(1, 102))}, "at most 100 items"),
        ({"filters": {"inbox": True}, "exclude_ids": list(range(101))}, "at most 100 items"),
        ({"filters": {"tag": "x"}}, "Extra inputs are not permitted"),
        ({"filters": {}}, "holds no filter"),
        ({"filters": {"inbox": True}, "saved_view": "e2e-view"}, "cannot be combined"),
        ({"document_ids": [1], "content_chars": 0}, "greater than or equal to 1"),
        ({"document_ids": [1], "content_chars": 20_001}, "less than or equal to 20000"),
        ({"document_ids": [1], "fields": ["text"]}, "Input should be"),
        ({"document_ids": [1], "page": 0}, "greater than or equal to 1"),
        ({"document_ids": [1], "page": 2}, "beyond the last page"),
    ],
)
async def test_a_call_that_means_nothing_or_too_much_is_refused(arguments: Json, part: str) -> None:
    assert part in await refusal(fake(), **arguments)


async def test_documents_that_no_longer_match_refuse_the_call() -> None:
    state = seeded()
    filters = {"tags_all": ["e2e-finance"]}
    assert ids(await call(fake(state), document_ids=[1], filters=filters)) == [1]
    state.documents[0].tags = []

    text = await refusal(fake(state), document_ids=[1], filters=filters)

    assert "1 of the named documents no longer matches: 1." in text


async def test_a_restricted_user_reads_what_it_sees_and_no_notes() -> None:
    found = await call(fake(user="e2e-reader"), document_ids=[1, 2], fields=["title", "content"])

    assert (ids(found), found["missing"]) == ([1], [2])
    text = await refusal(fake(user="e2e-reader"), document_ids=[1], fields=["notes"])
    assert "may not view notes" in text


async def test_the_description_says_that_text_is_data() -> None:
    async with Client(serve(fake(), toolsets=["bulk"])) as client:
        [tool] = [t for t in await client.list_tools() if t.name == NAME]

    assert " ".join((tool.description or "").split()).endswith(DOCUMENT_TEXT)
    assert len((tool.description or "").encode()) <= 2048


async def test_the_filters_are_those_of_search_documents() -> None:
    async with Client(serve(fake(), toolsets=["core", "bulk"])) as client:
        schemas = {tool.name: cast("Json", tool.input_schema) for tool in await client.list_tools()}

    def bare(schema: object) -> dict[str, Json]:
        properties = cast("dict[str, Json]", cast("Json", schema)["properties"])
        said = {"description", "title"}
        return {
            name: {key: value for key, value in prop.items() if key not in said}
            for name, prop in properties.items()
        }

    [ours, _] = cast("list[Json]", bare(schemas[NAME])["filters"]["anyOf"])
    offered = bare(schemas["search_documents"])
    assert ours["additionalProperties"] is False
    assert bare(ours) == {name: prop for name, prop in offered.items() if name not in PAGING}
    assert set(bare(ours)) == set(SelectionFilters.model_fields)
