"""search_documents through the assembled server against the seeded Paperless 3.4.2 stack."""

from datetime import date, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from mcp_server_paperless.server import create_server
from mcp_server_paperless.services.search_syntax import query_notices

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]


async def search(stack: Stack, token: str, **arguments: object) -> list[Json]:
    server = create_server(paperless_settings(stack.base_url, token), server_settings())
    async with Client(server) as client:
        arguments = {"title_contains": "e2e-p", **arguments}
        result = await client.call_tool("search_documents", arguments)
    assert result.structured_content is not None
    return cast("list[Json]", result.structured_content["documents"])


async def titles(stack: Stack, **arguments: object) -> set[str]:
    return {str(row["title"]) for row in await search(stack, stack.admin_token, **arguments)}


async def test_name_filters_resolve_on_live_paperless(stack: Stack) -> None:
    assert await titles(stack, correspondent="E2E-ACME", tags_all=["e2e-finance"]) == {
        "e2e-public-invoice"
    }
    assert await titles(stack, owner="admin", tags_none=["e2e-inbox"]) == {"e2e-private-note"}


async def test_has_filters_find_documents_without_a_correspondent_or_type(stack: Stack) -> None:
    assert await titles(stack, has_correspondent=False, has_document_type=False) == {
        "e2e-private-note"
    }
    assert await titles(stack, has_correspondent=True, has_document_type=True) == {
        "e2e-public-invoice"
    }


async def test_unsupported_query_syntax_is_named_on_live_paperless(stack: Stack) -> None:
    server = create_server(paperless_settings(stack.base_url, stack.admin_token), server_settings())
    async with Client(server) as client:
        noisy = await client.call_tool("search_documents", {"query": "acme not zebra"})
        clean = await client.call_tool("search_documents", {"query": "acme AND NOT zebra"})

    assert noisy.structured_content is not None
    assert clean.structured_content is not None
    assert len(cast("list[str]", noisy.structured_content["notices"])) == 1
    assert clean.structured_content["notices"] == []
    assert clean.structured_content["count"] == 1


@pytest.mark.parametrize("field", ["type", "document_type", "correspondent"])
async def test_field_presence_works_on_documents_with_and_without_a_value(
    stack: Stack, field: str
) -> None:
    assert await titles(stack, query=f"{field}:*") == {"e2e-public-invoice"}
    assert await titles(stack, query=f"NOT {field}:*") == {"e2e-private-note"}


async def test_the_constructs_named_in_notices_are_misread_by_live_paperless(stack: Stack) -> None:
    both = {"e2e-public-invoice", "e2e-private-note"}

    assert await titles(stack, query="title:invoice OR title:note") == both
    assert await titles(stack, query="title:invoice || title:note") == set()
    assert await titles(stack, query="!acme") == await titles(stack, query="acme")
    unknown = await titles(stack, query="nosuchfield:acme")
    assert unknown == await titles(stack, query="nosuchfield OR acme") == {"e2e-public-invoice"}
    assert await titles(stack, query="NOT nosuchfield:*") == both
    assert await titles(stack, query="acme") == {"e2e-public-invoice"}
    # Each is misread, and each is named in a notice.
    for misread in ("acme Not zebra", "NOT(zebra)", "acme AND", "title: zebra", 'NOT"acme"'):
        assert await titles(stack, query=misread) == set()
        assert query_notices(misread)
    for included in ("acme&&zebra", "-acme", "!acme", "Title:acme", "NOT -type:*"):
        assert await titles(stack, query=included) == {"e2e-public-invoice"}
        assert query_notices(included)
    assert await titles(stack, query="NOT zebra") == both
    assert await titles(stack, query="acme NOT -zebra") == {"e2e-public-invoice"}


async def test_date_ranges_on_live_paperless(stack: Stack) -> None:
    [row] = await search(stack, stack.admin_token, title_contains="e2e-public-invoice")
    created = date.fromisoformat(str(row["created"]))
    day = timedelta(days=1)

    assert "e2e-public-invoice" in await titles(stack, created_from=str(created))
    assert "e2e-public-invoice" not in await titles(stack, created_to=str(created - day))


async def test_full_text_rows_are_readable_and_hold_no_content(stack: Stack) -> None:
    [row] = await search(stack, stack.reader_token, query="acme")

    assert row["title"] == "e2e-public-invoice"
    assert "<b>acme</b>" in str(row["highlights"]).lower()
    assert "content" not in row
    assert row["correspondent"] == {"id": stack.seeded.correspondent, "name": "e2e-acme"}
    values = {field["name"]: field["value"] for field in cast("list[Json]", row["custom_fields"])}
    assert (values["e2e-select"], values["e2e-monetary"]) == ("alpha", "EUR 12.50")
    assert str(row["web_url"]).endswith(f"/documents/{row['id']}/details")
