"""PaperlessBulkWriter on the Paperless 3.4.2 wire format (schema-guarded)."""

import json
from typing import TYPE_CHECKING

import httpx2
import pytest

from adapter.conftest import streaming
from adapter.test_queued import row as task_row
from mcp_server_paperless.domain.bulk import (
    FieldsChange,
    ObjectPermissions,
    PermissionsChange,
    ReferenceChange,
    ReprocessChange,
    TagsChange,
    TrashDocuments,
)
from mcp_server_paperless.domain.errors import (
    PaperlessMcpError,
    PermissionDenied,
    TaskFailed,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.paperless.bulk import PaperlessBulkWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory, RecordingSleep
    from mcp_server_paperless.domain.bulk import BulkKind, DocumentChange

pytestmark = pytest.mark.anyio

type Json = dict[str, object]
OK: Json = {"result": "OK"}


class Recorded:
    """Answers every request with one body and records method, path, query and JSON body."""

    def __init__(self, body: object = OK, status: int = 200) -> None:
        self.body: object = body
        self.status: int = status
        self.sent: list[tuple[str, str, dict[str, str], object]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET" and request.url.path == "/api/tasks/":
            # The task a queued document change names has succeeded; waiting for it is not logged.
            done = {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [task_row("success", TASK)],
            }
            return httpx2.Response(200, json=done)
        payload: object = json.loads(request.content) if request.content else None
        self.sent.append((request.method, request.url.path, dict(request.url.params), payload))
        if isinstance(self.body, str):
            return httpx2.Response(self.status, text=self.body)
        return httpx2.Response(self.status, json=self.body)


def body(method: str, parameters: Json, documents: list[int] | None = None) -> Json:
    return {"documents": documents or [4, 7], "method": method, "parameters": parameters}


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            TagsChange(add=(3, 5), remove=(9,)),
            body("modify_tags", {"add_tags": [3, 5], "remove_tags": [9]}),
        ),
        (TagsChange(add=(3,)), body("modify_tags", {"add_tags": [3], "remove_tags": []})),
        (
            ReferenceChange(field="correspondent", value=12),
            body("set_correspondent", {"correspondent": 12}),
        ),
        (
            ReferenceChange(field="correspondent", value=None),
            body("set_correspondent", {"correspondent": None}),
        ),
        (
            ReferenceChange(field="document_type", value=2),
            body("set_document_type", {"document_type": 2}),
        ),
        (
            ReferenceChange(field="storage_path", value=None),
            body("set_storage_path", {"storage_path": None}),
        ),
        (
            # The dict form keeps the other fields; the list form would empty a field's value.
            FieldsChange(values={5: "x", 6: [1, 2]}, remove=(8,)),
            body(
                "modify_custom_fields",
                {"add_custom_fields": {"5": "x", "6": [1, 2]}, "remove_custom_fields": [8]},
            ),
        ),
        (
            FieldsChange(remove=(8,)),
            body("modify_custom_fields", {"add_custom_fields": {}, "remove_custom_fields": [8]}),
        ),
    ],
    ids=[
        "tags",
        "tags-add-only",
        "correspondent",
        "correspondent-cleared",
        "document-type",
        "storage-path-cleared",
        "custom-fields",
        "custom-fields-remove-only",
    ],
)
async def test_a_document_change_is_one_bulk_edit_request(
    make_client: ClientFactory, change: DocumentChange, expected: Json
) -> None:
    server = Recorded(QUEUED)

    _ = await PaperlessBulkWriter(make_client(server)).bulk_edit_documents((4, 7), change)

    assert server.sent == [("POST", "/api/documents/bulk_edit/", {}, expected)]


@pytest.mark.parametrize(
    ("change", "endpoint"), [(TrashDocuments(), "delete"), (ReprocessChange(), "reprocess")]
)
async def test_trash_and_reprocess_have_endpoints_of_their_own(
    make_client: ClientFactory, change: DocumentChange, endpoint: str
) -> None:
    # API 10 deprecates these forms of bulk_edit (decision-72).
    server = Recorded(QUEUED)

    _ = await PaperlessBulkWriter(make_client(server)).bulk_edit_documents((4, 7), change)

    assert server.sent == [("POST", f"/api/documents/{endpoint}/", {}, {"documents": [4, 7]})]


@pytest.mark.parametrize(
    "answer", [{"unexpected": True}, {"result": "OK"}], ids=["other", "no-task"]
)
async def test_an_answer_that_is_not_the_queued_result_is_unexpected(answer: Json) -> None:
    # Every document change names the task it queued; an answer without one is not believed.
    client, _ = streaming(lambda _: httpx2.Response(200, json=answer))

    with pytest.raises(PaperlessMcpError, match="unexpected response") as raised:
        _ = await PaperlessBulkWriter(client).bulk_edit_documents((4,), TrashDocuments())

    assert "may have been applied" in (raised.value.hint or "")


async def test_a_refused_document_change_says_nothing_was_changed(
    make_client: ClientFactory,
) -> None:
    # Paperless checks every document first and answers a plain-text 403 for the whole batch.
    server = Recorded("Insufficient permissions", 403)

    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessBulkWriter(make_client(server)).bulk_edit_documents(
            (4,), TrashDocuments()
        )

    assert "nothing was changed" in raised.value.message
    assert "change permission" in (raised.value.hint or "")
    assert "owner" in (raised.value.hint or "")


async def test_rejected_values_are_a_validation_error_with_the_reason(
    make_client: ClientFactory,
) -> None:
    server = Recorded(
        {"add_custom_fields": {"111": {"non_field_errors": ["Enter a valid integer."]}}}, 400
    )
    change = FieldsChange(values={111: "abc"})

    with pytest.raises(Validation, match="Enter a valid integer"):
        _ = await PaperlessBulkWriter(make_client(server)).bulk_edit_documents((4,), change)


async def test_a_failing_bulk_edit_is_never_retried() -> None:
    # A repeated tag change is harmless but a repeated reprocess queues more work: ask once.
    calls: list[str] = []

    def failing(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(503)

    client, _ = streaming(failing)

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessBulkWriter(client).bulk_edit_documents((4,), ReprocessChange())

    assert calls == ["/api/documents/reprocess/"]


@pytest.mark.parametrize(
    ("kind", "object_type"),
    [
        ("tag", "tags"),
        ("correspondent", "correspondents"),
        ("document_type", "document_types"),
        ("storage_path", "storage_paths"),
    ],
)
async def test_taxonomy_items_are_deleted_by_object_type(
    make_client: ClientFactory, kind: BulkKind, object_type: str
) -> None:
    server = Recorded()

    await PaperlessBulkWriter(make_client(server)).delete_taxonomy_items(kind, (3, 8))

    expected = {"objects": [3, 8], "object_type": object_type, "operation": "delete"}
    assert server.sent == [("POST", "/api/bulk_edit_objects/", {}, expected)]


async def test_permissions_are_sent_with_owner_only_when_it_was_set(
    make_client: ClientFactory,
) -> None:
    server = Recorded()
    permissions = ObjectPermissions(view_users=(3,), view_groups=(1,), change_groups=(1,))
    change = PermissionsChange(permissions=permissions, merge=True)

    await PaperlessBulkWriter(make_client(server)).set_taxonomy_permissions("tag", (5,), change)

    expected = {
        "objects": [5],
        "object_type": "tags",
        "operation": "set_permissions",
        "permissions": {
            "view": {"users": [3], "groups": [1]},
            "change": {"users": [], "groups": [1]},
        },
        "merge": True,
    }
    assert server.sent == [("POST", "/api/bulk_edit_objects/", {}, expected)]


async def test_a_cleared_owner_is_sent_as_null_but_a_left_out_owner_is_not_sent(
    make_client: ClientFactory,
) -> None:
    server = Recorded()
    writer = PaperlessBulkWriter(make_client(server))

    await writer.set_taxonomy_permissions("correspondent", (5,), PermissionsChange())
    await writer.set_taxonomy_permissions("correspondent", (5,), PermissionsChange(owner=None))

    base: Json = {"objects": [5], "object_type": "correspondents", "operation": "set_permissions"}
    assert [sent[3] for sent in server.sent] == [
        {**base, "merge": False},
        {**base, "merge": False, "owner": None},
    ]


async def test_an_owner_is_sent_as_the_user_id(make_client: ClientFactory) -> None:
    server = Recorded()

    await PaperlessBulkWriter(make_client(server)).set_taxonomy_permissions(
        "storage_path", (5,), PermissionsChange(owner=3)
    )

    sent = server.sent[0][3]
    assert isinstance(sent, dict)
    assert (sent["owner"], sent["object_type"], "permissions" in sent) == (
        3,
        "storage_paths",
        False,
    )


async def test_refused_object_changes_say_nothing_was_changed(make_client: ClientFactory) -> None:
    server = Recorded("Insufficient permissions", 403)

    with pytest.raises(PermissionDenied) as raised:
        await PaperlessBulkWriter(make_client(server)).delete_taxonomy_items("tag", (1,))

    assert "nothing was changed" in raised.value.message
    assert "owner" in (raised.value.hint or "")


# --- Paperless 3.5: the document operations queue a bulk_update task (PLMCP-46)

TASK = "5b8f0c2e-0000-4000-8000-000000000001"
QUEUED: Json = {"result": "OK", "task_id": TASK}


class Queue:
    """3.5.1: the POST answers with a task id and the task list tells how far the task is."""

    def __init__(self, *states: str) -> None:
        self.states: list[str] = list(states)
        self.sent: list[tuple[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.sent.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx2.Response(200, json=QUEUED)
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        row = task_row(state, TASK)
        return httpx2.Response(
            200, json={"count": 1, "next": None, "previous": None, "results": [row]}
        )


def queued_writer(
    make_client: ClientFactory, queue: Queue, sleep: RecordingSleep
) -> PaperlessBulkWriter:
    return PaperlessBulkWriter(make_client(queue), sleep=sleep)


async def test_a_queued_document_change_returns_when_its_task_has_succeeded(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    queue = Queue("pending", "started", "success")

    _ = await queued_writer(make_client, queue, sleep).bulk_edit_documents((4, 7), TrashDocuments())

    assert queue.sent == [("POST", "/api/documents/delete/")] + [("GET", "/api/tasks/")] * 3
    assert sleep.delays == [0.2, 0.2]


@pytest.mark.parametrize("ends", ["failure", "revoked"])
async def test_a_queued_document_change_that_fails_is_reported_as_not_applied(
    make_client: ClientFactory, sleep: RecordingSleep, ends: str
) -> None:
    queue = Queue("pending", ends)

    with pytest.raises(TaskFailed) as raised:
        _ = await queued_writer(make_client, queue, sleep).bulk_edit_documents(
            (4,), ReprocessChange()
        )

    assert raised.value.message == "Paperless could not apply the bulk edit: its task failed."
    assert "part of it" in (raised.value.hint or "")


async def test_the_task_is_not_followed_for_the_object_endpoint(
    make_client: ClientFactory,
) -> None:
    server = Recorded()

    await PaperlessBulkWriter(make_client(server)).delete_taxonomy_items("tag", (3,))

    assert [path for _, path, _, _ in server.sent] == ["/api/bulk_edit_objects/"]


async def test_a_queued_change_whose_task_never_shows_up_is_returned_as_unconfirmed(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    def invisible(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            return httpx2.Response(200, json=QUEUED)
        return httpx2.Response(
            200, json={"count": 0, "next": None, "previous": None, "results": []}
        )

    writer = PaperlessBulkWriter(make_client(invisible), sleep=sleep)

    outcome = await writer.bulk_edit_documents((4,), TrashDocuments())

    assert outcome is not None
    assert (outcome.task_id, outcome.reason) == (TASK, "it did not show up in the task list")


async def test_a_confirmed_change_returns_nothing(
    make_client: ClientFactory, sleep: RecordingSleep
) -> None:
    outcome = await queued_writer(make_client, Queue("success"), sleep).bulk_edit_documents(
        (4,), TrashDocuments()
    )

    assert outcome is None
