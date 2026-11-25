"""A rate limit (HTTP 429) ends a bulk call through the client, and nothing more is asked."""

from typing import TYPE_CHECKING, cast

import pytest
from confirmation import source
from fakes.paperless import FakeDocument, FakeState, seeded
from fastmcp import Client
from harness import serve
from interleaving import before
from ratelimit import RateLimit, limited_port

from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-71"),
    pytest.mark.decision("decision-73"),
]

type Json = dict[str, object]

INBOX = 4
DOCUMENTS = [10, 11, 12]
BY_ID: Json = {"document_ids": DOCUMENTS, "confirm": True}
UPDATE: Json = {
    "documents": [{"document_id": n, "title": f"Invoice {n}"} for n in DOCUMENTS],
    "confirm": True,
}


def world() -> FakeState:
    state = seeded()
    state.documents += [FakeDocument(id=n, title=f"Scan {n}", owner=2) for n in DOCUMENTS]
    state.workflows = [
        Workflow(
            id=3,
            name="Paid",
            order=0,
            enabled=True,
            triggers=(WorkflowTrigger(id=1, type=5),),
            actions=(WorkflowAction(id=2, type=1, settings={"assign_tags": [INBOX]}),),
        )
    ]
    return state


async def call(
    state: FakeState, tool: str, arguments: Json, *, method: str, at: int = 1
) -> tuple[CallToolResult, list[str]]:
    """The answer of a tool whose limit begins with call ``at`` of ``method``; what was asked."""
    limit = RateLimit()
    port = before(limited_port(source(state), limit), method, limit.begin, at=at)
    server = serve(
        port,
        taxonomy=port,
        documents=port,
        document_writes=port,
        bulk_writes=port,
        views=port,
        workflows=port,
        workflow_writes=port,
        toolsets=["all"],
    )
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    return result, limit.asked


def output(result: CallToolResult) -> Json:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


def statuses(answer: Json) -> list[object]:
    return [row["status"] for row in cast("list[Json]", answer["documents"])]


def titles(state: FakeState) -> list[str]:
    return [d.title for d in state.documents if d.id in DOCUMENTS]


def tagged(state: FakeState) -> list[int]:
    return [d.id for d in state.documents if d.id in DOCUMENTS and INBOX in d.tags]


async def test_a_rate_limit_ends_update_documents() -> None:
    state = world()

    result, asked = await call(state, "update_documents", UPDATE, method="update_document", at=2)

    answer = output(result)
    assert statuses(answer) == ["changed", "failed", "not_started"]
    assert (answer["left"], asked) == ([11, 12], ["update_document"])
    assert "Stopped: Paperless is temporarily unavailable (HTTP 429)." in str(answer["message"])
    assert titles(state) == ["Invoice 10", "Scan 11", "Scan 12"]


async def test_a_rate_limit_on_the_first_change_is_an_error_that_changed_nothing() -> None:
    state = world()

    result, asked = await call(state, "update_documents", UPDATE, method="update_document")

    assert result.is_error
    assert "HTTP 429" in str(result.content)
    assert asked == ["update_document"]
    assert titles(state) == ["Scan 10", "Scan 11", "Scan 12"]


async def test_a_rate_limit_ends_add_notes() -> None:
    state = world()

    result, asked = await call(
        state, "add_notes", {**BY_ID, "note": "Paid"}, method="add_note", at=2
    )

    answer = output(result)
    assert statuses(answer) == ["added", "failed", "not_started"]
    assert (answer["left"], asked) == ([11, 12], ["add_note"])
    assert [len(d.notes) for d in state.documents if d.id in DOCUMENTS] == [1, 0, 0]


@pytest.mark.parametrize(
    ("tool", "arguments", "method"),
    [
        (
            "bulk_edit_documents",
            {"operation": {"op": "add_tags", "tags": ["e2e-inbox"]}, "dry_run": False},
            "bulk_edit_documents",
        ),
        ("run_workflow", {"workflow": "Paid"}, "run_workflow"),
    ],
)
async def test_a_rate_limit_on_the_one_request_of_a_call_changes_nothing(
    tool: str, arguments: Json, method: str
) -> None:
    state = world()

    result, asked = await call(state, tool, {**BY_ID, **arguments}, method=method)

    assert result.is_error
    assert "HTTP 429" in str(result.content)
    assert (asked, tagged(state)) == ([method], [])


async def test_a_rate_limit_ends_reading_and_names_the_rest() -> None:
    result, asked = await call(
        world(),
        "read_documents",
        {"document_ids": DOCUMENTS, "fields": ["title", "content"]},
        method="get_document_content",
        at=2,
    )

    answer = output(result)
    assert (answer["left"], asked) == ([11, 12], ["get_document_content"])
    assert "Reading text stopped" in str(answer["notices"])
