"""search_documents through the in-memory client on the seeded fake."""

import json
import logging
from typing import TYPE_CHECKING, cast, override

import pytest
from fakes.paperless import FakeDocument, FakePaperless, seeded
from fastmcp import Client
from harness import admin, serve
from mcp.shared.exceptions import MCPError
from mcp.types import TextResourceContents

from mcp_server_paperless.services.documents import text_state

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import DocumentRecord

pytestmark = pytest.mark.anyio

type Json = dict[str, object]


async def call(arguments: Json, fake: FakePaperless | None = None) -> Json:
    async with Client(serve(fake or admin())) as client:
        result = await client.call_tool("search_documents", arguments)
    assert result.structured_content is not None
    return result.structured_content


async def test_names_and_full_text_find_the_document_as_a_compact_row() -> None:
    output = await call({"query": "acme", "correspondent": "E2E-ACME", "tags_all": ["e2e-inbox"]})

    assert (output["count"], output["page"], output["next_page"]) == (1, 1, None)
    [row] = cast("list[Json]", output["documents"])
    assert row["title"] == "e2e-public-invoice"
    assert row["correspondent"] == {"id": 1, "name": "e2e-acme"}
    assert row["highlights"] == "E2E public invoice <b>ACME</b> 2026"
    assert row["web_url"] == "http://paperless.test/documents/1/details"
    assert "content" not in row


@pytest.mark.parametrize(
    ("arguments", "title"),
    [
        ({"has_correspondent": False}, "e2e-private-note"),
        ({"has_correspondent": True}, "e2e-public-invoice"),
        ({"has_document_type": False}, "e2e-private-note"),
        ({"has_document_type": True}, "e2e-public-invoice"),
        ({"has_storage_path": False}, "e2e-private-note"),
        ({"has_storage_path": True}, "e2e-public-invoice"),
        ({"has_tags": True, "tags_none": ["e2e-private"]}, "e2e-public-invoice"),
    ],
)
async def test_the_has_filters_find_documents_with_or_without_a_value(
    arguments: Json, title: str
) -> None:
    output = await call(arguments)

    assert [row["title"] for row in cast("list[Json]", output["documents"])] == [title]


async def test_has_tags_false_finds_the_untagged_documents() -> None:
    state = seeded()
    state.documents.append(FakeDocument(id=3, title="bare"))

    output = await call({"has_tags": False}, FakePaperless(state, user="admin"))

    assert [row["title"] for row in cast("list[Json]", output["documents"])] == ["bare"]


@pytest.mark.parametrize(("has", "title"), [(True, "copy"), (False, "bare")])
async def test_has_duplicates_finds_the_documents_with_or_without_a_copy(
    title: str, *, has: bool
) -> None:
    state = seeded()
    state.documents = [
        FakeDocument(id=3, title="bare"),
        FakeDocument(id=4, title="copy", original=b"%PDF same"),
    ]
    state.trash.append(FakeDocument(id=5, title="copy", original=b"%PDF same"))

    output = await call({"has_duplicates": has}, FakePaperless(state, user="admin"))

    assert [row["title"] for row in cast("list[Json]", output["documents"])] == [title]


async def test_the_duplicates_of_a_document_are_a_section_of_it() -> None:
    state = seeded()
    for number in (3, 4):
        state.documents.append(FakeDocument(id=number, title="copy", original=b"%PDF same"))

    arguments: Json = {"document_id": 3, "include": ["duplicates"]}
    output = await get("get_document", arguments, FakePaperless(state, user="admin"))

    found = cast("Json", output["duplicates"])
    assert (found["total"], found["truncated"]) == (1, False)
    [copy] = cast("list[Json]", found["documents"])
    assert (copy["id"], copy["match"]) == (4, "same_file")


async def test_unsupported_query_syntax_is_named_in_the_notices() -> None:
    output = await call({"query": "acme -zebra"})

    assert [n for n in cast("list[str]", output["notices"]) if "-zebra" in n]
    assert (await call({"query": "acme"}))["notices"] == []


async def test_the_query_description_states_the_operator_rules() -> None:
    async with Client(serve(admin())) as client:
        tool = next(t for t in await client.list_tools() if t.name == "search_documents")

    properties = cast("dict[str, Json]", tool.input_schema["properties"])
    text = str(properties["query"]["description"])
    rules = ("uppercase", "leading minus or ! does not exclude", "'type:*' is presence")
    assert all(rule in text for rule in rules)
    assert "does not work" not in text


async def test_the_default_page_holds_25_documents() -> None:
    state = seeded()
    state.documents.extend(FakeDocument(id=n, title=f"bulk-{n}") for n in range(10, 40))

    output = await call({"title_contains": "bulk"}, FakePaperless(state, user="admin"))

    assert (output["count"], output["next_page"]) == (30, 2)
    assert len(cast("list[Json]", output["documents"])) == 25


async def test_a_custom_field_query_passes_through() -> None:
    output = await call({"custom_field_query": ["e2e-date", "range", ["2026-01-01", "today"]]})

    assert output["count"] == 1


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"tags_any": ["e2e-financ"]}, "Did you mean 'e2e-finance'"),
        ({"query": "x", "more_like_id": 1}, "Pass either query or more_like_id"),
        ({"custom_field_query": ["e2e-nope", "exact", 1]}, "'e2e-nope' is not a valid"),
        ({"page_size": 101}, "less than or equal to 100"),
        ({"page": 0}, "greater than or equal to 1"),
        (
            {"has_correspondent": False, "correspondent": "e2e-acme"},
            "has_correspondent=false contradicts correspondent",
        ),
        ({"has_tags": False, "tags_any": ["e2e-finance"]}, "has_tags=false contradicts tags_any"),
    ],
)
async def test_bad_filters_are_tool_errors_with_a_hint(arguments: Json, message: str) -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("search_documents", arguments, raise_on_error=False)

    assert result.is_error
    assert message in str(result.content)


async def get(name: str, arguments: Json, fake: FakePaperless | None = None) -> Json:
    async with Client(serve(fake or admin())) as client:
        result = await client.call_tool(name, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def test_get_document_returns_names_readable_fields_and_a_link() -> None:
    output = await get("get_document", {"document_id": 1})

    assert output["title"] == "e2e-public-invoice"
    assert output["correspondent"] == {"id": 1, "name": "e2e-acme"}
    assert output["notes_count"] == 1
    assert output["web_url"] == "http://paperless.test/documents/1/details"
    values = {f["name"]: f["value"] for f in cast("list[Json]", output["custom_fields"])}
    assert (values["e2e-select"], values["e2e-monetary"]) == ("alpha", "EUR 12.50")
    for absent in ("content", "notes", "history", "file_metadata", "versions", "suggestions"):
        assert absent not in output or output[absent] is None
    assert output["unavailable"] == []


async def test_get_document_include_adds_the_asked_sections() -> None:
    output = await get(
        "get_document", {"document_id": 1, "include": ["notes", "history", "metadata"]}
    )

    assert [n["note"] for n in cast("list[Json]", output["notes"])] == ["e2e remark zebra"]
    history = cast("Json", output["history"])
    assert history["total"] == 2
    assert "changed, the text is not shown" in str(history)
    assert "ACME" not in str(history)
    assert cast("Json", output["file_metadata"])["original_filename"] == "e2e-public-invoice.pdf"
    assert output["versions"] is None


async def test_a_section_the_user_may_not_read_is_listed_as_unavailable() -> None:
    fake = FakePaperless(seeded(), user="e2e-reader")

    output = await get("get_document", {"document_id": 1, "include": ["notes", "metadata"]}, fake)

    assert output["unavailable"] == ["notes"]
    assert output["notes"] is None


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"document_id": 999}, "No document 999 exists that this user may see"),
        ({"document_id": 1, "include": ["content"]}, "Input should be"),
    ],
)
async def test_get_document_errors_are_tool_errors(arguments: Json, message: str) -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("get_document", arguments, raise_on_error=False)

    assert result.is_error
    assert message in str(result.content)


async def test_a_missing_document_error_names_the_next_tool() -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("get_document", {"document_id": 999}, raise_on_error=False)

    assert "Hint: Take the id from a search_documents row." in str(result.content)


async def test_get_document_content_returns_a_slice_with_the_next_offset() -> None:
    state = seeded()
    state.documents[0].content = "0123456789"

    output = await get(
        "get_document_content",
        {"document_id": 1, "offset": 2, "max_chars": 5},
        FakePaperless(state, user="admin"),
    )

    assert output == {
        "document_id": 1,
        "offset": 2,
        "content": "23456",
        "total_chars": 10,
        "next_offset": 7,
        "version_id": 1,
        "text_state": text_state(1, 1, "0123456789"),
        "web_url": "http://paperless.test/documents/1/details",
    }


async def test_get_document_content_defaults_to_20000_characters_from_the_start() -> None:
    state = seeded()
    state.documents[0].content = "x" * 50_000

    output = await get(
        "get_document_content", {"document_id": 1}, FakePaperless(state, user="admin")
    )

    assert (output["offset"], len(cast("str", output["content"]))) == (0, 20_000)
    assert (output["total_chars"], output["next_offset"]) == (50_000, 20_000)


async def test_get_document_content_past_the_end_is_empty_with_the_total() -> None:
    output = await get("get_document_content", {"document_id": 1, "offset": 9999})

    assert (output["content"], output["total_chars"], output["next_offset"]) == ("", 28, None)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"document_id": 999}, "No document 999"),
        ({"document_id": 1, "version_id": 2}, "No version 2 exists on document 1"),
        ({"document_id": 1, "offset": -1}, "greater than or equal to 0"),
        ({"document_id": 1, "max_chars": 0}, "greater than or equal to 1"),
        ({"document_id": 1, "max_chars": 20_001}, "less than or equal to 20000"),
    ],
)
async def test_get_document_content_errors_are_tool_errors(arguments: Json, message: str) -> None:
    async with Client(serve(admin())) as client:
        result = await client.call_tool("get_document_content", arguments, raise_on_error=False)

    assert result.is_error
    assert message in str(result.content)


async def read_json(uri: str, fake: FakePaperless | None = None) -> Json:
    async with Client(serve(fake or admin())) as client:
        [content] = await client.read_resource(uri)
    assert isinstance(content, TextResourceContents)
    assert content.mime_type == "application/json"
    return cast("Json", json.loads(content.text))


async def test_the_document_resource_is_the_detail_as_json() -> None:
    assert await read_json("paperless://documents/1") == await get(
        "get_document", {"document_id": 1}
    )


async def test_the_content_resource_is_a_slice_as_json() -> None:
    state = seeded()
    state.documents[0].content = "0123456789"
    fake = FakePaperless(state, user="admin")

    first = await read_json("paperless://documents/1/content", fake)
    later = await read_json("paperless://documents/1/content?offset=4&max_chars=3", fake)

    assert (first["content"], first["next_offset"], first["total_chars"]) == (
        "0123456789",
        None,
        10,
    )
    assert (later["content"], later["offset"], later["next_offset"]) == ("456", 4, 7)


@pytest.mark.parametrize("uri", ["paperless://documents/999", "paperless://documents/999/content"])
async def test_a_resource_of_a_missing_document_is_an_error_with_the_hint(uri: str) -> None:
    async with Client(serve(admin())) as client:
        with pytest.raises(
            MCPError, match="No document 999 exists that this user may see"
        ) as raised:
            _ = await client.read_resource(uri)
    assert "Hint: Take the id from a search_documents row." in str(raised.value)


@pytest.mark.parametrize("query", ["offset=-1", "max_chars=0", "max_chars=20001"])
async def test_the_content_resource_rejects_a_slice_out_of_range(query: str) -> None:
    async with Client(serve(admin())) as client:
        with pytest.raises(MCPError, match="offset must be at least 0 and max_chars between 1"):
            _ = await client.read_resource(f"paperless://documents/1/content?{query}")


class BrokenDocuments(FakePaperless):
    """Fails like a bug in the adapter: the exception message holds response content."""

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        message = f"secret-detail of {document_id}"
        raise RuntimeError(message)


async def test_a_resource_bug_is_masked_and_logged_by_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = serve(admin(), documents=BrokenDocuments(seeded(), user="admin"))
    caplog.set_level(logging.DEBUG)

    async with Client(server) as client:
        with pytest.raises(MCPError) as raised:
            _ = await client.read_resource("paperless://documents/1")

    assert "Internal error in document_detail; the server log has details." in str(raised.value)
    assert "secret-detail" not in str(raised.value)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert "document failed: RuntimeError" in errors
    assert not [line for line in errors if "secret-detail" in line]


async def test_a_resource_domain_error_is_logged_at_info_by_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    async with Client(serve(admin())) as client:
        with pytest.raises(MCPError, match="No document 999 exists"):
            _ = await client.read_resource("paperless://documents/999")

    logged = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert "document failed: NotFound" in logged
    assert "No document 999" not in caplog.text


@pytest.mark.decision("decision-95")
async def test_the_history_is_read_page_by_page() -> None:
    state = seeded()
    entry = state.documents[0].history[0]
    state.documents[0].history = [entry.model_copy(update={"id": 900 - n}) for n in range(60)]
    fake = FakePaperless(state, user="admin")
    ask: Json = {"document_id": 1, "include": ["history"]}

    first = cast("Json", (await get("get_document", ask, fake))["history"])
    second = cast("Json", (await get("get_document", {**ask, "history_page": 2}, fake))["history"])

    assert (first["page"], first["next_page"], first["total"]) == (1, 2, 60)
    assert (second["page"], second["next_page"], second["truncated"]) == (2, None, False)
    ids = [e["id"] for page in (first, second) for e in cast("list[Json]", page["entries"])]
    assert ids == [900 - n for n in range(60)]


@pytest.mark.decision("decision-95")
@pytest.mark.parametrize("arguments", [{"history_page": 0}, {"history_page": 3}])
async def test_a_page_of_the_history_that_is_not_there_is_refused(arguments: Json) -> None:
    ask = {"document_id": 1, "include": ["history"], **arguments}

    async with Client(serve(FakePaperless(seeded(), user="admin"))) as client:
        result = await client.call_tool("get_document", ask, raise_on_error=False)

    assert result.is_error
