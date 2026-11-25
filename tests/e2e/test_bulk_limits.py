"""Bulk calls on Paperless 3.5.1 behind a rate limit (HTTP 429): they end, and ask nothing more."""

import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from contract.conftest import delete_and_empty_trash
from fastmcp import Client
from harness import paperless_settings, server_settings
from interleaving import before
from ratelimit import LimitingTransport, RateLimit

from e2e.helpers import consume_text, settled
from e2e.test_bulk import make, raw, tags_of
from e2e.test_bulk_items import notes_of, statuses
from e2e.test_run_workflow import assign, scratch, workflow
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy
from mcp_server_paperless.server import Ports, create_server, paperless_ports

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterator

    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Json, Stack

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.anyio,
    pytest.mark.decision("decision-71"),
    pytest.mark.decision("decision-73"),
]

__all__ = ["scratch"]  # the fixture

NOTE = "plmcp119 paid"


@pytest.fixture
def documents(stack: Stack) -> Iterator[list[int]]:
    created = [consume_text(stack.admin, f"plmcp119-{uuid.uuid4().hex}") for _ in range(3)]
    yield created
    delete_and_empty_trash(stack, created)


@asynccontextmanager
async def limiting(
    stack: Stack, limit: RateLimit, ports: Callable[[Ports], Ports] = lambda ports: ports
) -> AsyncGenerator[Client[FastMCPTransport]]:
    """A client of the server whose way to Paperless is limited."""
    transport = LimitingTransport(limit)
    async with httpx2.AsyncClient(base_url=stack.base_url, transport=transport, timeout=30) as http:
        paperless = PaperlessClient(
            http, StaticTokenProvider(stack.admin_token), retry=RetryPolicy(), upload_timeout=30
        )
        server = create_server(
            paperless_settings(stack.base_url, stack.admin_token),
            server_settings(toolsets=["core", "bulk", "workflows"]),
            ports=lambda _client: ports(paperless_ports(paperless)),
        )
        async with Client(server) as client:
            yield client


def titles(stack: Stack, documents: list[int]) -> list[object]:
    return [raw(stack, number)["title"] for number in documents]


async def test_update_documents_stops_and_names_what_was_not_changed(
    stack: Stack, documents: list[int]
) -> None:
    limit, old = RateLimit(), titles(stack, documents)

    def second(ports: Ports) -> Ports:
        writes = before(ports.document_writes, "update_document", limit.begin, at=2)
        return replace(ports, document_writes=writes)

    async with limiting(stack, limit, second) as client:
        result = await client.call_tool(
            "update_documents",
            {
                "documents": [{"document_id": n, "title": f"plmcp119 {n}"} for n in documents],
                "confirm": True,
            },
        )

    assert result.structured_content is not None
    answer: Json = result.structured_content
    assert statuses(answer) == ["changed", "failed", "not_started"]
    assert answer["left"] == documents[1:]
    assert "Stopped: " in str(answer["message"])
    assert limit.asked == [f"PATCH /api/documents/{documents[1]}/"]
    assert titles(stack, documents) == [f"plmcp119 {documents[0]}", *old[1:]]


async def test_add_notes_stops_and_names_where_no_note_was_added(
    stack: Stack, documents: list[int]
) -> None:
    limit = RateLimit(after=f"POST /api/documents/{documents[0]}/notes/")

    async with limiting(stack, limit) as client:
        result = await client.call_tool(
            "add_notes", {"document_ids": documents, "note": NOTE, "confirm": True}
        )

    assert result.structured_content is not None
    answer: Json = result.structured_content
    assert statuses(answer) == ["added", "failed", "not_started"]
    assert answer["left"] == documents[1:]
    # A read is tried again by the client, nothing else is asked.
    assert len(set(limit.asked)) == 1, limit.asked
    assert [len(notes_of(stack, number)) for number in documents] == [1, 0, 0]


async def test_a_bulk_edit_changes_nothing(
    stack: Stack, documents: list[int], scratch: str
) -> None:
    limit, tag = RateLimit(), make(stack, "tags", {"name": f"{scratch}tag"})

    def first(ports: Ports) -> Ports:
        writes = before(ports.bulk_writes, "bulk_edit_documents", limit.begin)
        return replace(ports, bulk_writes=writes)

    async with limiting(stack, limit, first) as client:
        result = await client.call_tool(
            "bulk_edit_documents",
            {
                "document_ids": documents,
                "operation": {"op": "add_tags", "tags": [tag]},
                "confirm": True,
            },
            raise_on_error=False,
        )

    assert result.is_error
    assert "429" in str(result.content)
    assert limit.asked == ["POST /api/documents/bulk_edit/"]
    assert not any(tag in tags_of(stack, number) for number in documents)


async def test_a_bulk_edit_whose_task_cannot_be_followed_is_unconfirmed_and_asks_nothing_more(
    stack: Stack, documents: list[int], scratch: str
) -> None:
    limit = RateLimit(after="POST /api/documents/bulk_edit/")
    tag = make(stack, "tags", {"name": f"{scratch}tag"})

    async with limiting(stack, limit) as client:
        result = await client.call_tool(
            "bulk_edit_documents",
            {
                "document_ids": documents,
                "operation": {"op": "add_tags", "tags": [tag]},
                "confirm": True,
            },
        )

    assert result.structured_content is not None
    answer: Json = result.structured_content
    task_id = str(answer["unconfirmed_task_id"])
    assert "Paperless limits the rate of requests (HTTP 429)" in str(answer["message"])
    # The client itself tries a read again (decision-71), nothing else is asked.
    assert set(limit.asked) == {"GET /api/tasks/"}
    outcomes = cast("Json", answer["outcomes"])
    assert set(cast("list[int]", outcomes["unknown"])) == set(documents)
    _ = settled(stack.admin, task_id, "the bulk edit")  # it was accepted, and runs on
    assert all(tag in tags_of(stack, number) for number in documents)


async def test_run_workflow_runs_nothing(stack: Stack, documents: list[int], scratch: str) -> None:
    limit, tag = RateLimit(), make(stack, "tags", {"name": f"{scratch}tag"})
    _ = workflow(stack, f"{scratch}flow", assign(assign_tags=[tag]))

    def first(ports: Ports) -> Ports:
        writes = before(ports.workflow_writes, "run_workflow", limit.begin)
        return replace(ports, workflow_writes=writes)

    async with limiting(stack, limit, first) as client:
        result = await client.call_tool(
            "run_workflow",
            {"workflow": f"{scratch}flow", "document_ids": documents, "confirm": True},
            raise_on_error=False,
        )

    assert result.is_error
    assert "429" in str(result.content)
    assert limit.asked == ["POST /api/documents/bulk_edit/"]
    assert not any(tag in tags_of(stack, number) for number in documents)


async def test_read_documents_stops_and_names_the_rest(stack: Stack, documents: list[int]) -> None:
    limit = RateLimit()

    def second(ports: Ports) -> Ports:
        reader = before(ports.documents, "get_document_content", limit.begin, at=2)
        return replace(ports, documents=reader)

    async with limiting(stack, limit, second) as client:
        result = await client.call_tool(
            "read_documents", {"document_ids": documents, "fields": ["title", "content"]}
        )

    assert result.structured_content is not None
    answer: Json = result.structured_content
    assert answer["left"] == documents[1:]
    assert "Reading text stopped" in str(answer["notices"])
    assert len(set(limit.asked)) == 1, limit.asked
