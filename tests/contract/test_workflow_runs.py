"""Contract of WorkflowWriter.run_workflow, on the fake and on a live Paperless (decision-75).

The workflows have only manual triggers, so a live one acts on nothing but the documents it is run
on; the live backend deletes them (SCRATCH_WORKFLOW) and the documents of each test.
"""

import time
import uuid
from typing import TYPE_CHECKING

import anyio
import pytest

from contract.conftest import SCRATCH_TAG, SCRATCH_WORKFLOW
from mcp_server_paperless.domain.documents import DocumentPatch
from mcp_server_paperless.domain.errors import PermissionDenied, Validation
from mcp_server_paperless.domain.taxonomy import TaxonomyValues
from mcp_server_paperless.domain.workflows import (
    Workflow,
    WorkflowAction,
    WorkflowRun,
    WorkflowTrigger,
    WorkflowValues,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from contract.conftest import Backend, Ports

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-8"),
    pytest.mark.decision("decision-75"),
    pytest.mark.contract("WorkflowWriter.run_workflow"),
]

MISSING = 999_999
# What a user needs to run workflows; `view_paperlesstask` to see how the run ended.
RUNNER = frozenset({"view_workflow", "view_document", "change_document"})
SETTLE_SECONDS = 60.0


MANUAL = WorkflowTrigger(type=5)


def manual(**filters: JsonValue) -> WorkflowTrigger:
    return WorkflowTrigger(type=5, settings=filters)


def assign(**settings: JsonValue) -> WorkflowAction:
    return WorkflowAction(type=1, settings=settings)


async def workflow(
    ports: Ports,
    *actions: WorkflowAction,
    triggers: tuple[WorkflowTrigger, ...] = (MANUAL,),
    enabled: bool = True,
) -> Workflow:
    values = WorkflowValues(enabled=enabled, triggers=triggers, actions=actions)
    name = f"{SCRATCH_WORKFLOW}{uuid.uuid4().hex}"
    return await ports.workflow_writes.create_workflow(name, values)


async def new_tag(ports: Ports, parent: int | None = None) -> int:
    name = f"{SCRATCH_TAG}{uuid.uuid4().hex}"
    values = TaxonomyValues() if parent is None else TaxonomyValues(parent=parent)
    return (await ports.taxonomy_writes.create_taxonomy_item("tag", name, values)).id


async def field(ports: Ports, name: str) -> int:
    return next(f.id for f in await ports.taxonomy.list_taxonomy("custom_field") if f.name == name)


async def tags(ports: Ports, document: int) -> set[int]:
    return set((await ports.documents.get_document(document)).tags)


async def run(
    ports: Ports, number: int, *documents: int, filter_documents: bool = True
) -> WorkflowRun:
    return await ports.workflow_writes.run_workflow(
        number, documents, filter_documents=filter_documents
    )


async def settled(backend: Backend, done: WorkflowRun) -> None:
    """Wait, as the admin, for a run its user could not follow."""
    deadline = time.monotonic() + SETTLE_SECONDS
    while not (task := await backend.admin.tasks.get_task(done.task_id)).is_final:
        assert time.monotonic() < deadline, f"task {done.task_id} still {task.state}"
        await anyio.sleep(0.5)
    assert task.state == "success", task


async def test_a_run_applies_the_actions_in_order_to_each_document_once(backend: Backend) -> None:
    ports = backend.admin
    parent = await new_tag(ports)
    child, gone = await new_tag(ports, parent), await new_tag(ports)
    first, second, other = backend.new_documents(3)
    text = await field(ports, "e2e-string")
    flow = await workflow(
        ports,
        assign(assign_tags=[child, gone], assign_title="contract run"),
        assign(assign_custom_fields=[text], assign_custom_fields_values={str(text): "ran"}),
        WorkflowAction(type=2, settings={"remove_tags": [gone]}),
    )

    done = await run(ports, flow.id, first, second)

    assert (done.failed, done.unconfirmed) == ((), None)
    for number in (first, second):
        record = await ports.documents.get_document(number)
        assert {parent, child} <= set(record.tags)
        assert gone not in record.tags
        assert record.title == "contract run"
        assert [(v.field, v.value) for v in record.custom_fields] == [(text, "ran")]
    assert (await ports.documents.get_document(other)).title != "contract run"


async def test_a_removed_tag_takes_its_child_tags_off(backend: Backend) -> None:
    ports = backend.admin
    parent = await new_tag(ports)
    child = await new_tag(ports, parent)
    [document] = backend.new_documents(1)
    adding = await workflow(ports, assign(assign_tags=[child]))
    _ = await run(ports, adding.id, document)
    assert {parent, child} <= await tags(ports, document)
    removing = await workflow(ports, WorkflowAction(type=2, settings={"remove_tags": [parent]}))

    _ = await run(ports, removing.id, document)

    assert not {parent, child} & await tags(ports, document)


async def test_where_a_document_came_from_and_a_schedule_do_not_filter_a_run(
    backend: Backend,
) -> None:
    ports = backend.admin
    given = await new_tag(ports)
    [document] = backend.new_documents(1)
    flow = await workflow(
        ports,
        assign(assign_tags=[given]),
        triggers=(
            manual(
                sources=[],
                filter_path="/never/*",
                match="never-in-any-document",
                schedule_offset_days=9,
            ),
        ),
    )

    _ = await run(ports, flow.id, document)

    assert given in await tags(ports, document)


async def test_a_value_that_is_not_assigned_is_not_written(backend: Backend) -> None:
    ports = backend.admin
    text, number = await field(ports, "e2e-string"), await field(ports, "e2e-integer")
    [document] = backend.new_documents(1)
    values: JsonValue = {str(text): "x", str(number): 5}
    flow = await workflow(
        ports, assign(assign_custom_fields=[text], assign_custom_fields_values=values)
    )

    _ = await run(ports, flow.id, document)

    record = await ports.documents.get_document(document)
    assert [(v.field, v.value) for v in record.custom_fields] == [(text, "x")]


async def test_the_filters_of_the_manual_trigger_skip_documents_silently_unless_left_out(
    backend: Backend,
) -> None:
    ports = backend.admin
    wanted, given = await new_tag(ports), await new_tag(ports)
    first, second = backend.new_documents(2)
    tagging = await workflow(ports, assign(assign_tags=[wanted]))
    _ = await run(ports, tagging.id, first)
    flow = await workflow(
        ports, assign(assign_tags=[given]), triggers=(manual(filter_has_tags=[wanted]),)
    )

    filtered = await run(ports, flow.id, first, second)

    assert (filtered.failed, filtered.unconfirmed) == ((), None)
    assert given in await tags(ports, first)
    assert given not in await tags(ports, second)

    _ = await run(ports, flow.id, second, filter_documents=False)

    assert given in await tags(ports, second)


async def test_only_the_manual_triggers_decide_and_one_that_matches_is_enough(
    backend: Backend,
) -> None:
    ports = backend.admin
    never, given = await new_tag(ports), await new_tag(ports)
    [document] = backend.new_documents(1)
    by_added = await workflow(
        ports,
        assign(assign_tags=[given]),
        # The trigger without filters is of another type; it acts on nothing here: it needs a tag.
        triggers=(
            manual(filter_has_tags=[never]),
            WorkflowTrigger(type=2, settings={"filter_has_tags": [never]}),
        ),
    )
    _ = await run(ports, by_added.id, document)
    assert given not in await tags(ports, document)

    either = await workflow(
        ports, assign(assign_tags=[given]), triggers=(manual(filter_has_tags=[never]), MANUAL)
    )
    _ = await run(ports, either.id, document)

    assert given in await tags(ports, document)


async def test_a_run_that_fails_on_a_document_names_it_and_may_have_done_a_part(
    backend: Backend,
) -> None:
    ports = backend.admin
    wanted, given = await new_tag(ports), await new_tag(ports)
    first, second = backend.new_documents(2)
    tagging = await workflow(ports, assign(assign_tags=[wanted]))
    _ = await run(ports, tagging.id, second)
    number = await field(ports, "e2e-integer")
    flow = await workflow(
        ports,
        assign(assign_tags=[given]),
        # Paperless stores a value of the wrong type with the workflow, and fails when it runs.
        assign(assign_custom_fields=[number], assign_custom_fields_values={str(number): "abc"}),
        triggers=(manual(filter_has_tags=[wanted]),),
    )

    done = await run(ports, flow.id, first, second)

    # The first does not match, so it was skipped and is not named.
    assert done.failed == (second,)
    assert given not in await tags(ports, first)
    assert given in await tags(ports, second)

    whole = await run(ports, flow.id, first, second, filter_documents=False)

    assert set(whole.failed) == {first, second}


async def test_a_run_can_move_documents_to_the_trash(backend: Backend) -> None:
    ports = backend.admin
    [document] = backend.new_documents(1)
    flow = await workflow(ports, WorkflowAction(type=6))

    done = await run(ports, flow.id, document)

    assert (done.failed, done.unconfirmed) == ((), None)
    assert backend.in_trash(document)


async def test_a_webhook_and_an_email_that_cannot_be_sent_do_not_fail_the_run(
    backend: Backend,
) -> None:
    ports = backend.admin
    given = await new_tag(ports)
    [document] = backend.new_documents(1)
    # Nothing can be sent: the stack has no mail server, and nothing listens on the port.
    hook: dict[str, JsonValue] = {"url": "http://127.0.0.1:9/never", "use_params": False}
    mail: dict[str, JsonValue] = {"subject": "s", "body": "b", "to": "a@example.org"}
    flow = await workflow(
        ports,
        WorkflowAction(type=3, settings={"email": mail}),
        WorkflowAction(type=4, settings={"webhook": hook}),
        assign(assign_tags=[given]),
    )

    done = await run(ports, flow.id, document)

    assert (done.failed, done.unconfirmed) == ((), None)
    assert given in await tags(ports, document)


async def test_an_unusable_workflow_and_an_unknown_document_are_refused_and_nothing_runs(
    backend: Backend,
) -> None:
    ports = backend.admin
    given = await new_tag(ports)
    [document] = backend.new_documents(1)
    off = await workflow(ports, assign(assign_tags=[given]), enabled=False)
    added = WorkflowTrigger(type=2, settings={"filter_has_tags": [given]})
    automatic = await workflow(ports, assign(assign_tags=[given]), triggers=(added,))
    usable = await workflow(ports, assign(assign_tags=[given]))

    with pytest.raises(Validation, match="not enabled"):
        _ = await run(ports, off.id, document)
    with pytest.raises(Validation, match="manual trigger"):
        _ = await run(ports, automatic.id, document)
    with pytest.raises(Validation, match="Workflow does not exist"):
        _ = await run(ports, MISSING, document)
    with pytest.raises(Validation, match="don't exist or were specified twice"):
        _ = await run(ports, usable.id, document, MISSING)
    with pytest.raises(Validation, match="don't exist or were specified twice"):
        _ = await run(ports, usable.id, document, document)

    assert given not in await tags(ports, document)


@pytest.mark.parametrize(
    ("permissions", "change"),
    [
        (RUNNER - {"view_workflow"}, True),
        (RUNNER - {"change_document"}, True),
        (RUNNER, False),
        # A grant to change is not enough: the run needs ownership.
        (RUNNER, True),
    ],
    ids=["no view_workflow", "no change_document", "view grant", "change grant"],
)
async def test_a_user_without_the_rights_runs_nothing(
    backend: Backend, permissions: frozenset[str], *, change: bool
) -> None:
    ports = backend.admin
    given = await new_tag(ports)
    first, second = backend.new_documents(2)
    flow = await workflow(ports, assign(assign_tags=[given]))
    granted, viewed = ([first], []) if change else ([], [first])
    user = backend.restricted(permissions, change=granted, view=viewed)

    for documents in ((first,), (second,)):  # shared, and hidden from the user
        with pytest.raises(PermissionDenied):
            _ = await run(user, flow.id, *documents)

    assert given not in await tags(ports, first) | await tags(ports, second)


async def test_one_document_of_another_owner_refuses_the_whole_run(backend: Backend) -> None:
    ports = backend.admin
    given = await new_tag(ports)
    unowned, owned = backend.new_documents(2)
    flow = await workflow(ports, assign(assign_tags=[given]))
    user = backend.restricted(RUNNER, change=[owned])
    _ = await ports.document_writes.update_document(unowned, DocumentPatch(owner=None))

    with pytest.raises(PermissionDenied):
        _ = await run(user, flow.id, unowned, owned)
    assert given not in await tags(ports, unowned) | await tags(ports, owned)

    # Without `view_paperlesstask` the user cannot tell how it ended.
    done = await run(user, flow.id, unowned)

    assert done.unconfirmed is not None
    await settled(backend, done)
    assert given in await tags(ports, unowned)
