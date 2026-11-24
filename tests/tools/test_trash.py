"""The trash toolset through the in-memory client on the seeded fake (PLMCP-27).

The toolset is off by default (the default profile stays within its tool budget). `empty_trash`
needs ids or all=true, and confirm in both cases (decision-6, decision-15).
"""

import base64
import json
from typing import TYPE_CHECKING, cast

import pytest
from confirmation import YES, answering
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextContent

if TYPE_CHECKING:
    from fastmcp import FastMCP

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

type Json = dict[str, object]

READING = frozenset({"list_trash", "get_trash_entry", "get_trashed_file"})
TOOLS = READING | {"restore_from_trash", "empty_trash"}
CONFIRM_DESCRIPTION = (
    "Must be true to go ahead at once. Without it a client that can show dialogs asks the "
    "user, and the tool runs only if they accept; with any other nothing changes and the "
    "error says what would happen. Describe that to the user and pass true only once they "
    "agree, or if they already asked for exactly this."
)


def fake(state: FakeState | None = None, *, writes: bool = True) -> FakePaperless:
    return FakePaperless(state or seeded(), user="admin", allow_writes=writes)


def server(source: FakePaperless, **settings: object) -> FastMCP:
    return serve(source, toolsets=["trash"], **settings)  # pyright: ignore[reportArgumentType] -- settings


async def delete(source: FakePaperless, *documents: int) -> None:
    for document in documents:
        await source.delete_document(document)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


# --- the toolset -------------------------------------------------------------------------------


async def test_the_trash_toolset_is_off_by_default_and_has_five_tools_when_selected() -> None:
    async with Client(serve(fake())) as client:
        default = {t.name for t in await client.list_tools()}
    async with Client(server(fake())) as client:
        selected = {t.name for t in await client.list_tools()}

    assert not TOOLS & default
    assert selected == TOOLS


async def test_read_only_mode_leaves_only_the_reading_tools() -> None:
    async with Client(serve(fake(), toolsets=["trash"], read_only=True)) as client:
        offered = {t.name for t in await client.list_tools()}

    assert offered == READING


async def test_the_annotations_tell_which_tool_destroys_and_which_can_repeat() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t.annotations for t in await client.list_tools()}

    hints = {
        name: (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint)
        for name, a in tools.items()
        if a
    }
    assert hints == {
        **dict.fromkeys(READING, (True, None, True, False)),
        "restore_from_trash": (False, False, True, False),
        "empty_trash": (False, True, False, False),
    }


async def test_empty_trash_takes_a_described_confirm_that_defaults_to_false() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t for t in await client.list_tools()}

    schema = tools["empty_trash"].input_schema
    properties = cast("dict[str, Json]", schema["properties"])
    assert properties["confirm"]["default"] is False
    assert properties["confirm"]["description"] == CONFIRM_DESCRIPTION
    assert schema.get("required", []) == []  # nothing is required, and nothing defaults to all
    assert properties["all"]["default"] is False
    assert properties["document_ids"]["default"] is None
    description = " ".join((tools["empty_trash"].description or "").split())
    assert "never empties everything by default" in description
    assert "whatever the trash holds then" in description
    assert "at most 100" in description


async def test_the_descriptions_say_where_ids_come_from_and_what_is_lost() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t.description or "" for t in await client.list_tools()}

    assert "version" in tools["list_trash"]
    assert "a superuser sees every entry" in tools["list_trash"]
    assert "list_trash" in tools["restore_from_trash"]
    assert "for good" in tools["empty_trash"]
    assert "confirm" in tools["empty_trash"]


# --- listing and restoring ---------------------------------------------------------------------


async def test_a_deleted_document_is_listed_and_then_restored() -> None:
    source = fake()
    await delete(source, 1)

    listed = await call(source, "list_trash")
    restored = await call(source, "restore_from_trash", document_ids=[1])

    assert (listed["count"], listed["page"], listed["next_page"]) == (1, 1, None)
    assert listed["web_url"] == "http://paperless.test/trash"
    [entry] = cast("list[Json]", listed["entries"])
    assert (entry["id"], entry["title"], entry["version_of"]) == (1, "e2e-public-invoice", None)
    assert str(entry["deleted_at"]).startswith("20")
    assert [(e["id"], e["web_url"]) for e in cast("list[Json]", restored["entries"])] == [
        (1, "http://paperless.test/documents/1/details")
    ]
    assert restored["message"] == (
        "Restored 1 document from the trash: document 1 'e2e-public-invoice'."
    )
    assert (await call(source, "list_trash"))["count"] == 0


async def test_the_list_is_paged() -> None:
    source = fake()
    await delete(source, 1, 2)

    first = await call(source, "list_trash", page=1, page_size=1)

    assert (first["count"], first["next_page"], len(cast("list[Json]", first["entries"]))) == (
        2,
        2,
        1,
    )
    assert "Page 3 of the trash" in await refusal(source, "list_trash", page=3, page_size=1)


async def test_restoring_something_that_is_not_in_the_trash_is_a_tool_error_with_a_hint() -> None:
    text = await refusal(fake(), "restore_from_trash", document_ids=[1])

    assert "Not found in the trash: 1." in text
    assert "Hint: 1 is not deleted. List the trash with list_trash." in text


async def test_restoring_without_ids_or_with_too_many_is_refused_by_the_schema() -> None:
    async with Client(server(fake())) as client:
        for ids in ([], list(range(1, 102))):
            result = await client.call_tool(
                "restore_from_trash", {"document_ids": ids}, raise_on_error=False
            )
            assert result.is_error


# --- emptying ---------------------------------------------------------------------------------


async def test_emptying_with_nothing_named_is_a_tool_error_even_with_confirm() -> None:
    source = fake(writes=False)  # a write would raise

    nothing: list[Json] = [
        {},
        {"confirm": True},
        {"all": False, "confirm": True},
    ]
    for arguments in nothing:
        text = await refusal(source, "empty_trash", **arguments)
        assert "Name what to empty: nothing was emptied." in text
        assert "Pass document_ids from list_trash, or all=true" in text


async def test_ids_must_be_positive_and_at_most_100_and_not_empty_in_both_tools() -> None:
    async with Client(server(fake(writes=False))) as client:
        for tool in ("restore_from_trash", "empty_trash"):
            for ids in ([], [0], list(range(1, 102))):
                result = await client.call_tool(tool, {"document_ids": ids}, raise_on_error=False)
                assert result.is_error, (tool, ids)
        tools = {t.name: t for t in await client.list_tools()}
    for name in ("restore_from_trash", "empty_trash"):
        properties = cast("dict[str, Json]", tools[name].input_schema["properties"])
        assert "100" in str(properties["document_ids"]), name


async def test_emptying_ids_without_confirm_names_what_would_be_lost_and_changes_nothing() -> None:
    source = fake()
    await delete(source, 1)

    text = await refusal(source, "empty_trash", document_ids=[1])

    assert "Nothing was changed. Confirming would permanently delete 1 entry" in text
    assert "document 1 'e2e-public-invoice'" in text
    assert "for good" in text
    assert "confirm=true" in text
    assert [d.id for d in source.state.trash] == [1]


async def test_emptying_with_confirm_deletes_the_named_entries_for_good() -> None:
    source = fake()
    await delete(source, 1, 2)

    output = await call(source, "empty_trash", document_ids=[1], confirm=True)

    assert (output["document_ids"], output["count"]) == ([1], 1)
    assert [d.id for d in source.state.trash] == [2]


async def test_all_needs_confirm_too_and_then_empties_every_entry() -> None:
    source = fake()
    await delete(source, 1, 2)

    text = await refusal(source, "empty_trash", all=True)
    assert "permanently delete ALL 2 entries of the Paperless trash" in text
    assert len(source.state.trash) == 2

    output = await call(source, "empty_trash", all=True, confirm=True)

    assert output["count"] == 2
    assert source.state.trash == []


async def test_ids_and_all_together_are_a_tool_error() -> None:
    source = fake()
    await delete(source, 1)

    text = await refusal(source, "empty_trash", document_ids=[1], all=True, confirm=True)

    assert "Pass exactly one of document_ids, filter and all=true." in text
    assert len(source.state.trash) == 1


# --- filters (PLMCP-78) ---------------------------------------------------------------------------


async def test_the_list_is_narrowed_by_every_filter() -> None:
    source = fake()
    await delete(source, 1, 2)
    source.state.trash[0].document_type = source.state.document_types[0].id
    name = source.state.document_types[0].name

    for arguments in (
        {"title": "PUBLIC-inv"},
        {"document_type": name},
        {"ids": [1]},
        {"ids": [1, 2], "document_type": name, "kind": "document", "deleted_from": "2020-01-01"},
    ):
        listed = await call(source, "list_trash", **arguments)
        assert [e["id"] for e in cast("list[Json]", listed["entries"])] == [1], arguments
        assert (listed["count"], listed["truncated"]) == (1, False)
    for arguments in ({"kind": "version"}, {"deleted_to": "2020-01-01"}):
        assert (await call(source, "list_trash", **arguments))["count"] == 0, arguments


async def test_a_blank_title_and_unknown_filter_fields_are_refused() -> None:
    source = fake(writes=False)

    for wanted, said in (({"title": "  "}, "at least 1 character"), ({"titel": "x"}, "titel")):
        assert said in await refusal(source, "restore_from_trash", filter=wanted)
    assert "at least 1 character" in await refusal(source, "list_trash", title="  ")


async def test_a_filter_restores_nothing_and_names_the_ids_to_pass() -> None:
    source = fake(writes=False)
    writer = fake(source.state)
    await delete(writer, 1, 2)

    text = await refusal(source, "restore_from_trash", filter={"title": "public"})

    assert "Nothing was restored. The filter matches 1 entry: document 1" in text
    assert "document_ids=[1]" in text


async def test_a_filter_with_confirm_deletes_nothing_without_a_dialog() -> None:
    source = fake(writes=False)
    await delete(fake(source.state), 1, 2)

    text = await refusal(source, "empty_trash", filter={"title": "public"}, confirm=True)

    assert "Confirming would permanently delete 1 entry" in text
    assert "document_ids=[1] and confirm=true" in text


@pytest.mark.parametrize("confirm", [False, True])
async def test_an_accepted_dialog_deletes_what_the_filter_showed(confirm: bool) -> None:  # noqa: FBT001 -- a parametrised flag, not a switch
    source, seen = fake(), list[str]()
    await delete(source, 1, 2)

    for action, left in (("decline", [1, 2]), ("accept", [2])):
        handler = answering(action, YES if action == "accept" else None, seen=seen)
        async with Client(server(source), elicitation_handler=handler) as client:
            _ = await client.call_tool(
                "empty_trash",
                {"filter": {"title": "public"}, "confirm": confirm},
                raise_on_error=False,
            )
        assert [d.id for d in source.state.trash] == left

    assert len(seen) == 2
    assert all("document 1 'e2e-public-invoice'" in message for message in seen)


async def test_an_entry_that_came_while_the_dialog_was_open_is_shown_before_it_goes() -> None:
    source, seen = fake(), list[str]()
    await delete(source, 1)

    async def another() -> None:
        if len(seen) == 1:
            await source.delete_document(2)

    handler = answering("accept", YES, seen=seen, before=another)
    async with Client(server(source), elicitation_handler=handler) as client:
        _ = await client.call_tool("empty_trash", {"filter": {"kind": "document"}})

    assert ["document 2 " in message for message in seen] == [False, True]
    assert source.state.trash == []


# --- looking into an entry ------------------------------------------------------------------------


async def test_an_entry_comes_with_its_fields_and_its_text_in_slices() -> None:
    source = fake()
    source.state.documents[0].content = "0123456789"
    await delete(source, 1)

    plain = await call(source, "get_trash_entry", document_id=1)
    sliced = await call(source, "get_trash_entry", document_id=1, text=True, max_chars=4)

    assert (plain["id"], plain["title"], plain["text"]) == (1, "e2e-public-invoice", None)
    assert plain["web_url"] == "http://paperless.test/trash"
    assert "restore_from_trash" in str(plain["limits"])
    text = cast("Json", sliced["text"])
    assert (text["content"], text["next_offset"], text["total_chars"]) == ("0123", 4, 10)
    assert "Not found in the trash: 2." in await refusal(source, "get_trash_entry", document_id=2)


async def files(source: FakePaperless, **arguments: object) -> list[object]:
    served = server(source, max_download_bytes=10)
    async with Client(served) as client:
        result = await client.call_tool("get_trashed_file", {"document_id": 1, **arguments})
    assert result.structured_content is None
    return list(result.content)


def report(block: object) -> Json:
    assert isinstance(block, TextContent)
    return cast("Json", json.loads(block.text))


def blob(block: object) -> bytes:
    assert isinstance(block, EmbeddedResource)
    assert isinstance(block.resource, BlobResourceContents)
    return base64.b64decode(block.resource.blob)


async def test_the_thumbnail_is_an_image() -> None:
    source = fake()
    await delete(source, 1)

    told, image = await files(source, file="thumbnail")

    assert report(told)["kind"] == "thumbnail"
    assert isinstance(image, ImageContent)
    assert base64.b64decode(image.data) == source.state.trash[0].thumbnail


async def test_a_file_is_attached_or_read_in_parts() -> None:
    source = fake()
    source.state.documents[0].original = b"0123456"
    source.state.documents[0].archive = bytes(range(25))
    await delete(source, 1)

    told, attached = await files(source)
    [over] = await files(source, file="archive")
    first = await files(source, file="archive", offset=0)
    checksum = report(first[0])["checksum"]
    second = await files(source, file="archive", offset=10, checksum=checksum)
    [end] = await files(source, file="archive", offset=25, checksum=checksum)

    assert (report(told)["delivered"], blob(attached)) == (True, b"0123456")
    assert report(over)["delivered"] is False
    assert blob(first[1]) + blob(second[1]) == bytes(range(20))
    assert report(end)["next_offset"] is None
    assert isinstance(second[1], EmbeddedResource)
    assert str(second[1].resource.uri) == "paperless://trash/1/archive?offset=10&length=10"


async def test_parts_belong_to_files_only() -> None:
    source = fake()
    await delete(source, 1)

    text = await refusal(source, "get_trashed_file", document_id=1, file="thumbnail", offset=0)
    assert "A thumbnail is not read in parts." in text
    text = await refusal(source, "get_trashed_file", document_id=1, max_bytes=5)
    assert "for reading a file in parts" in text
