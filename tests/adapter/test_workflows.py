"""PaperlessWorkflowReader and PaperlessWorkflowWriter on the Paperless 3.4.2 wire format."""

import json
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.workflows import (
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
    WorkflowValues,
)
from mcp_server_paperless.paperless.pagination import MAX_ITEMS
from mcp_server_paperless.paperless.workflows import (
    PaperlessWorkflowReader,
    PaperlessWorkflowWriter,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from adapter.conftest import ClientFactory

pytestmark = pytest.mark.anyio

type Json = dict[str, object]

TRIGGER_WIRE: Json = {
    "id": 5,
    "sources": [1, 2, 3],
    "type": 2,
    "filter_path": None,
    "filter_filename": "*invoice*",
    "filter_mailrule": None,
    "matching_algorithm": 0,
    "match": "",
    "is_insensitive": True,
    "filter_has_tags": [1],
    "filter_has_all_tags": [],
    "filter_has_not_tags": [],
    "filter_custom_field_query": None,
    "filter_has_any_correspondents": [],
    "filter_has_not_correspondents": [],
    "filter_has_any_document_types": [],
    "filter_has_not_document_types": [],
    "filter_has_any_storage_paths": [],
    "filter_has_not_storage_paths": [],
    "filter_has_correspondent": None,
    "filter_has_document_type": None,
    "filter_has_storage_path": None,
    "schedule_offset_days": 0,
    "schedule_is_recurring": False,
    "schedule_recurring_interval_days": 1,
    "schedule_date_field": "added",
    "schedule_date_custom_field": None,
}
ACTION_WIRE: Json = {
    "id": 6,
    "type": 3,
    "assign_title": None,
    "assign_tags": [],
    "remove_all_tags": False,
    "email": {
        "id": 9,
        "subject": "New: {{doc_title}}",
        "body": "see it",
        "to": "a@example.org",
        "include_document": False,
    },
    "webhook": None,
    "passwords": None,
}
WIRE: Json = {
    "id": 3,
    "name": "plmcp30-probe",
    "order": 1,
    "enabled": True,
    "triggers": [TRIGGER_WIRE],
    "actions": [ACTION_WIRE],
}
TRIGGER_SETTINGS = cast(
    "dict[str, JsonValue]", {k: v for k, v in TRIGGER_WIRE.items() if k not in {"id", "type"}}
)
ACTION_SETTINGS = cast(
    "dict[str, JsonValue]",
    {
        **{k: v for k, v in ACTION_WIRE.items() if k not in {"id", "type"}},
        "email": {
            "subject": "New: {{doc_title}}",
            "body": "see it",
            "to": "a@example.org",
            "include_document": False,
        },
    },
)
WORKFLOW = Workflow(
    id=3,
    name="plmcp30-probe",
    order=1,
    enabled=True,
    triggers=(WorkflowTrigger(id=5, type=2, settings=TRIGGER_SETTINGS),),
    actions=(WorkflowAction(id=6, type=3, settings=ACTION_SETTINGS),),
)


class Recording:
    """Answers each request with the next scripted response and records method, path, body."""

    def __init__(self, *answers: httpx2.Response) -> None:
        self.answers: list[httpx2.Response] = list(answers)
        self.sent: list[tuple[str, str, Json | None, dict[str, str]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = cast("Json", json.loads(request.content)) if request.content else None
        self.sent.append((request.method, request.url.path, body, dict(request.url.params)))
        return self.answers.pop(0)


def page(*rows: Json, next_link: str | None = None, count: int | None = None) -> httpx2.Response:
    body = {
        "count": len(rows) if count is None else count,
        "next": next_link,
        "previous": None,
        "results": list(rows),
    }
    return httpx2.Response(200, json=body)


async def test_the_workflows_are_listed_with_triggers_and_actions(
    make_client: ClientFactory,
) -> None:
    server = Recording(page(WIRE))

    found = await PaperlessWorkflowReader(make_client(server)).list_workflows()

    assert found == (WORKFLOW,)
    [(method, path, _, params)] = server.sent
    assert (method, path) == ("GET", "/api/workflows/")
    assert params == {"page": "1", "page_size": "100"}


async def test_the_nested_ids_of_an_action_are_not_kept(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(200, json=WIRE))

    found = await PaperlessWorkflowReader(make_client(server)).get_workflow(3)

    assert found.actions[0].settings["email"] == ACTION_SETTINGS["email"]


async def test_every_page_of_workflows_is_read(make_client: ClientFactory) -> None:
    link = "http://paperless.test/api/workflows/?page=2&page_size=100"
    second = {**WIRE, "id": 4, "name": "second"}
    server = Recording(page(WIRE, next_link=link, count=2), page(second, count=2))

    found = await PaperlessWorkflowReader(make_client(server)).list_workflows()

    assert [workflow.id for workflow in found] == [3, 4]


async def test_more_workflows_than_the_cap_fail_instead_of_resolving_on_a_part(
    make_client: ClientFactory,
) -> None:
    server = Recording(page(WIRE, count=MAX_ITEMS + 1))

    with pytest.raises(PaperlessMcpError, match=f"more than {MAX_ITEMS} workflows"):
        _ = await PaperlessWorkflowReader(make_client(server)).list_workflows()


async def test_a_user_without_the_view_permission_is_denied(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(403, json={"detail": "no"}))

    with pytest.raises(PermissionDenied):
        _ = await PaperlessWorkflowReader(make_client(server)).list_workflows()


async def test_one_workflow_is_read_by_id(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(200, json=WIRE))

    found = await PaperlessWorkflowReader(make_client(server)).get_workflow(3)

    assert found == WORKFLOW
    assert server.sent == [("GET", "/api/workflows/3/", None, {})]


async def test_a_missing_workflow_is_not_found_with_a_hint(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No Workflow matches."}))

    with pytest.raises(NotFound, match="No workflow 9") as raised:
        _ = await PaperlessWorkflowReader(make_client(server)).get_workflow(9)

    assert raised.value.hint == "Call list_workflows to see the workflows you can use."


async def test_a_created_workflow_sends_its_settings_and_always_both_lists(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(201, json=WIRE), httpx2.Response(201, json=WIRE))
    writer = PaperlessWorkflowWriter(make_client(server))
    values = WorkflowValues(
        name="ignored",
        order=1,
        enabled=False,
        triggers=(WorkflowTrigger(id=77, type=2, settings={"filter_has_tags": [1]}),),
        actions=(WorkflowAction(id=88, type=6),),
    )

    made = await writer.create_workflow("plmcp30-probe", values)
    _ = await writer.create_workflow("bare", WorkflowValues())

    assert made == WORKFLOW
    assert [sent[:3] for sent in server.sent] == [
        (
            "POST",
            "/api/workflows/",
            {
                "name": "plmcp30-probe",
                "order": 1,
                "enabled": False,
                "triggers": [{"type": 2, "filter_has_tags": [1]}],
                "actions": [{"type": 6}],
            },
        ),
        ("POST", "/api/workflows/", {"name": "bare", "triggers": [], "actions": []}),
    ]


async def test_an_update_sends_only_what_was_set(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(200, json=WIRE), httpx2.Response(200, json=WIRE))
    writer = PaperlessWorkflowWriter(make_client(server))

    _ = await writer.update_workflow(3, WorkflowValues(name="renamed", enabled=False))
    _ = await writer.update_workflow(
        3,
        WorkflowValues(
            triggers=(), actions=(WorkflowAction(type=1, settings={"assign_title": "x"}),)
        ),
    )

    assert [sent[:3] for sent in server.sent] == [
        ("PATCH", "/api/workflows/3/", {"name": "renamed", "enabled": False}),
        (
            "PATCH",
            "/api/workflows/3/",
            {"triggers": [], "actions": [{"type": 1, "assign_title": "x"}]},
        ),
    ]


async def test_a_delete_sends_a_delete_and_expects_no_body(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(204))

    await PaperlessWorkflowWriter(make_client(server)).delete_workflow(3)

    assert server.sent == [("DELETE", "/api/workflows/3/", None, {})]


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (400, {"name": ["workflow with this name already exists."]}, Validation),
        (400, {"actions": [{"type": ['"99" is not a valid choice.']}]}, Validation),
        (403, {"detail": "no"}, PermissionDenied),
        (404, {"detail": "No Workflow matches the given query."}, NotFound),
    ],
)
async def test_failed_writes_name_the_cause(
    make_client: ClientFactory, status: int, body: Json, error: type[PaperlessMcpError]
) -> None:
    server = Recording(*[httpx2.Response(status, json=body)] * 3)
    writer = PaperlessWorkflowWriter(make_client(server))

    with pytest.raises(error):
        _ = await writer.create_workflow("x", WorkflowValues())
    with pytest.raises(error):
        _ = await writer.update_workflow(3, WorkflowValues(name="y"))
    with pytest.raises(error):
        await writer.delete_workflow(3)


async def test_a_write_to_a_missing_workflow_names_its_id_and_says_what_to_do(
    make_client: ClientFactory,
) -> None:
    missing = httpx2.Response(404, json={"detail": "No Workflow matches the given query."})
    server = Recording(missing, missing)
    writer = PaperlessWorkflowWriter(make_client(server))

    with pytest.raises(NotFound, match="No workflow 7 exists") as updated:
        _ = await writer.update_workflow(7, WorkflowValues(name="y"))
    with pytest.raises(NotFound, match="No workflow 8 exists") as deleted:
        await writer.delete_workflow(8)

    assert updated.value.hint == deleted.value.hint
    assert updated.value.hint == "Call list_workflows to see the workflows you can use."


async def test_an_action_without_a_type_is_an_error_not_a_guess(
    make_client: ClientFactory,
) -> None:
    # The schema requires the type of a trigger but not of an action.
    wire = {**WIRE, "actions": [{"id": 1}]}
    server = Recording(httpx2.Response(200, json=wire))

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await PaperlessWorkflowReader(make_client(server)).get_workflow(3)

    assert raised.value.message == "Paperless sent a workflow trigger or action without a type."


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ({"enabled": True}, {"enabled": "true"}),
        ({"enabled": False, "trigger_type": 5}, {"enabled": "false", "trigger_type": "5"}),
        ({"trigger_type": 1}, {"trigger_type": "1"}),
    ],
)
async def test_the_list_is_filtered_by_paperless(
    make_client: ClientFactory, given: dict[str, int], sent: dict[str, str]
) -> None:
    server = Recording(page(WIRE))
    reader = PaperlessWorkflowReader(make_client(server))

    found = await reader.list_workflows(
        enabled=cast("bool | None", given.get("enabled")), trigger_type=given.get("trigger_type")
    )

    assert found == (WORKFLOW,)
    [(_, path, _, params)] = server.sent
    assert path == "/api/workflows/"
    assert params == {"page": "1", "page_size": "100", **sent}


async def test_a_single_trigger_and_a_single_action_are_read(make_client: ClientFactory) -> None:
    server = Recording(
        httpx2.Response(200, json=TRIGGER_WIRE), httpx2.Response(200, json=ACTION_WIRE)
    )
    reader = PaperlessWorkflowReader(make_client(server))

    assert await reader.get_trigger(5) == WORKFLOW.triggers[0]
    assert await reader.get_action(6) == WORKFLOW.actions[0]
    assert [(method, path) for method, path, _, _ in server.sent] == [
        ("GET", "/api/workflow_triggers/5/"),
        ("GET", "/api/workflow_actions/6/"),
    ]


async def test_a_missing_trigger_or_action_is_not_found_with_a_hint(
    make_client: ClientFactory,
) -> None:
    gone = {"detail": "No match."}
    server = Recording(httpx2.Response(404, json=gone), httpx2.Response(404, json=gone))
    reader = PaperlessWorkflowReader(make_client(server))

    with pytest.raises(NotFound, match=r"^No workflow trigger 9 exists\.$") as trigger:
        _ = await reader.get_trigger(9)
    with pytest.raises(NotFound, match=r"^No workflow action 9 exists\.$") as action:
        _ = await reader.get_action(9)

    hint = "Call list_workflows to see the ids of triggers and actions."
    assert (trigger.value.hint, action.value.hint) == (hint, hint)


async def test_a_user_without_the_permission_for_a_part_is_denied(
    make_client: ClientFactory,
) -> None:
    no = {"detail": "no"}
    server = Recording(httpx2.Response(403, json=no), httpx2.Response(403, json=no))
    reader = PaperlessWorkflowReader(make_client(server))

    with pytest.raises(PermissionDenied):
        _ = await reader.get_trigger(5)
    with pytest.raises(PermissionDenied):
        _ = await reader.get_action(6)
