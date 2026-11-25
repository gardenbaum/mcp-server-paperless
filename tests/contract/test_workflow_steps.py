"""The workflow tools as operations, on the fake and on a live Paperless.

The search for used objects, the workflows that hold a part, the refusals of the operations on
single parts and the explanation are no port methods: the tools run as they are on the contract
backends (contract/serving.py). Every workflow is disabled, has a trigger nothing matches, or acts
only on documents with a tag of its own.
"""

import uuid
from typing import TYPE_CHECKING, cast

import pytest

from contract.conftest import SCRATCH_TAG
from contract.serving import output, refusal
from contract.test_mail import mail
from contract.test_workflows import NEVER, PARTS, RETITLE, kept, safe, scratch
from mcp_server_paperless.domain.documents import DocumentPatch
from mcp_server_paperless.domain.errors import NotFound
from mcp_server_paperless.domain.taxonomy import TaxonomyValues
from mcp_server_paperless.domain.workflows import (
    PartWrite,
    WorkflowAction,
    WorkflowTrigger,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from contract.conftest import Backend, Ports
    from contract.serving import Json
    from contract.test_mail import Mail
    from mcp_server_paperless.domain.taxonomy import TaxonomyKind
    from mcp_server_paperless.domain.workflows import Workflow

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-81")]

__all__ = ["mail"]  # the fixture

HOOK = "https://user:planted@h.invalid/x"
AS_SHOWN = "is a stored one as it is shown, without its credentials"
PART = "update_workflow_part"


async def make(
    ports: Ports,
    *,
    triggers: tuple[WorkflowTrigger, ...] = (NEVER,),
    actions: tuple[WorkflowAction, ...] = (RETITLE,),
    enabled: bool = False,
) -> Workflow:
    values = safe(triggers=triggers, actions=actions, enabled=enabled)
    return await ports.workflow_writes.create_workflow(scratch(), values)


async def seeded(ports: Ports, kind: TaxonomyKind, name: str) -> int:
    [found] = [item.id for item in await ports.taxonomy.list_taxonomy(kind) if item.name == name]
    return found


# --- the search for used objects


# The kind, the name of a seeded object of it, and the setting of a trigger or an action that uses
# it, by the name of the tools and by Paperless's.
USES: tuple[tuple[TaxonomyKind, str, str, str, str], ...] = (
    ("tag", "e2e-finance", "trigger", "has_tags", "filter_has_tags"),
    ("tag", "e2e-finance", "action", "remove_tags", "remove_tags"),
    ("correspondent", "e2e-acme", "trigger", "has_correspondent", "filter_has_correspondent"),
    ("correspondent", "e2e-acme", "action", "assign_correspondent", "assign_correspondent"),
    ("document_type", "e2e-invoice", "trigger", "has_document_type", "filter_has_document_type"),
    ("document_type", "e2e-invoice", "action", "assign_document_type", "assign_document_type"),
    ("storage_path", "e2e-archive", "trigger", "has_storage_path", "filter_has_storage_path"),
    ("storage_path", "e2e-archive", "action", "assign_storage_path", "assign_storage_path"),
    ("custom_field", "e2e-string", "action", "assign_custom_fields", "assign_custom_fields"),
    ("user", "e2e-reader", "action", "assign_owner", "assign_owner"),
    ("user", "e2e-reader", "action", "assign_view_users", "assign_view_users"),
    ("group", "e2e-group", "action", "assign_change_groups", "assign_change_groups"),
)


async def using(ports: Ports, *workflows: Workflow, **given: object) -> dict[str, object]:
    """Which of these workflows use the object, and where."""
    listed = cast("list[Json]", (await output(ports, "list_workflows", given))["workflows"])
    mine = {workflow.name for workflow in workflows}
    return {str(row["name"]): row["used_in"] for row in listed if row["name"] in mine}


@pytest.mark.parametrize(("kind", "name", "part", "setting", "wire"), USES)
async def test_the_workflows_that_use_an_object_are_found_with_the_place(  # noqa: PLR0913, PLR0917 -- the cases
    backend: Backend, kind: TaxonomyKind, name: str, part: str, setting: str, wire: str
) -> None:
    ports = backend.admin
    number = await seeded(ports, kind, name)
    single = wire in {
        *("assign_correspondent", "assign_document_type", "assign_storage_path", "assign_owner"),
        *("filter_has_correspondent", "filter_has_document_type", "filter_has_storage_path"),
    }
    value: JsonValue = number if single else [number]
    if part == "trigger":
        used = await make(ports, triggers=(WorkflowTrigger(type=2, settings={wire: value}),))
    else:
        action = WorkflowAction(type=2 if "remove" in wire else 1, settings={wire: value})
        used = await make(ports, actions=(action,))
    other = await make(ports)
    [holder] = used.triggers if part == "trigger" else used.actions

    expected = {used.name: [{"part": part, "id": holder.id, "setting": setting}]}
    assert await using(ports, used, other, uses_kind=kind, uses=name) == expected
    assert await using(ports, used, other, uses_kind=kind, uses=number) == expected


async def test_a_custom_field_in_a_query_and_a_mail_rule_are_found_too(
    backend: Backend, mail: Mail
) -> None:
    ports = backend.admin
    rule = await mail.rule((await mail.account()).id)
    query = '["OR",[["e2e-string","exists",true]]]'
    used = await make(
        ports,
        triggers=(
            WorkflowTrigger(type=1, settings={"filter_mailrule": rule.id}),
            WorkflowTrigger(type=2, settings={"filter_custom_field_query": query}),
        ),
    )
    other = await make(ports)
    by_type = {trigger.type: trigger.id for trigger in used.triggers}

    assert await using(ports, used, other, uses_kind="mail_rule", uses=rule.id) == {
        used.name: [{"part": "trigger", "id": by_type[1], "setting": "mailrule"}]
    }
    assert await using(ports, used, other, uses_kind="custom_field", uses="e2e-string") == {
        used.name: [{"part": "trigger", "id": by_type[2], "setting": "custom_field_query"}]
    }
    assert await using(ports, used, other, uses_kind="mail_rule", uses=999_999) == {}
    assert "plmcp119-no-such-tag" in await refusal(
        ports, "list_workflows", uses_kind="tag", uses="plmcp119-no-such-tag"
    )


async def test_the_workflows_that_hold_a_single_part_are_named(backend: Backend) -> None:
    ports = backend.admin
    first, second = await make(ports), await make(ports)
    [trigger], [action] = first.triggers, first.actions
    viewer = backend.restricted(frozenset({"view_workflow", "view_uisettings"}))

    async def holders(user: Ports, **part: object) -> list[object]:
        shown = cast("Json", (await output(user, "get_workflow", part))["part"])
        return [row["name"] for row in cast("list[Json]", shown["workflows"])]

    for user in (ports, viewer):
        assert await holders(user, trigger_id=trigger.id) == [first.name]
        assert await holders(user, action_id=action.id) == [first.name]
    # Paperless lets two workflows hold the same part.
    own = [PartWrite(id=second.actions[0].id), PartWrite(id=action.id)]
    await ports.workflow_writes.update_workflow_parts(second.id, "actions", own)
    await ports.workflow_writes.update_workflow_parts(
        second.id, "triggers", [kept(second.triggers[0]), kept(trigger)]
    )

    for user in (ports, viewer):
        assert await holders(user, trigger_id=trigger.id) == [first.name, second.name]
        assert await holders(user, action_id=action.id) == [first.name, second.name]
    assert "No workflow action 999999" in await refusal(ports, "get_workflow", action_id=999_999)


# --- single parts


@pytest.mark.decision("decision-82")
@pytest.mark.contract(PARTS)
async def test_one_trigger_is_removed_and_the_others_stay(backend: Backend) -> None:
    writer, reader = backend.admin.workflow_writes, backend.admin.workflows
    made = await make(
        backend.admin,
        triggers=(NEVER, WorkflowTrigger(type=5), WorkflowTrigger(type=2, settings={"match": "x"})),
    )
    gone = made.triggers[1].id
    assert gone is not None

    await writer.update_workflow_parts(
        made.id, "triggers", [kept(made.triggers[0]), kept(made.triggers[2])]
    )

    after = await reader.get_workflow(made.id)
    assert {t.id: t for t in after.triggers} == {
        t.id: t for t in (made.triggers[0], made.triggers[2])
    }
    assert after.actions == made.actions
    with pytest.raises(NotFound):
        _ = await reader.get_trigger(gone)


@pytest.mark.decision("decision-82")
async def test_a_trigger_is_added_changed_and_removed_by_the_tool(backend: Backend) -> None:
    ports = backend.admin
    made = await make(ports, triggers=(NEVER, WorkflowTrigger(type=5)))
    never, by_hand = made.triggers

    async def part(**given: object) -> Workflow:
        _ = await output(ports, PART, {"workflow": made.name, **given})
        return await ports.workflows.get_workflow(made.id)

    added = await part(
        operation="add", trigger={"type": "added", "match": "n", "matching_algorithm": "any"}
    )
    [new] = [t for t in added.triggers if t.id not in {never.id, by_hand.id}]
    changed = await part(operation="change", trigger_id=new.id, trigger={"type": 2, "match": "m"})
    removed = await part(operation="remove", trigger_id=by_hand.id)

    assert (new.type, new.settings["match"]) == (2, "n")
    assert [t.settings["match"] for t in changed.triggers if t.id == new.id] == ["m"]
    assert {t.id for t in removed.triggers} == {never.id, new.id}
    for workflow in (added, changed, removed):
        assert never in workflow.triggers
        assert workflow.actions == made.actions


@pytest.mark.decision("decision-21")
@pytest.mark.decision("decision-82")
async def test_operations_on_parts_that_decision_21_refuses_change_nothing(
    backend: Backend,
) -> None:
    ports = backend.admin
    acting = WorkflowAction(type=6)
    made = await make(
        ports,
        triggers=(NEVER,),
        actions=(RETITLE, acting, WorkflowAction(type=5, settings={"passwords": ["planted"]})),
        enabled=True,
    )
    ocr = await make(ports, actions=(WorkflowAction(type=7),))
    trigger, (title, trash, locked) = made.triggers[0], made.actions
    call: Json = {"workflow": made.name}
    add: Json = {**call, "operation": "add"}
    change: Json = {**call, "operation": "change"}

    for given, told in (
        # What results is enabled and acts: confirm.
        ({**add, "action": {"type": "assignment", "assign_title": "x"}}, "Nothing was changed. Co"),
        (
            {**add, "trigger": {"type": "added", "has_tags": ["e2e-finance"]}},
            "Nothing was changed. Confirming wo",
        ),
        ({**call, "operation": "remove", "action_id": title.id}, "move the matching documents to"),
        # A consumption trigger needs a filter, remote OCR a consumption trigger.
        ({**change, "trigger_id": trigger.id, "clear": ["filename"]}, "needs a path, filename or"),
        (
            {"workflow": ocr.name, "operation": "remove", "trigger_id": ocr.triggers[0].id},
            "remote OCR needs a consumption trigger",
        ),
        # Types this server does not write, settings of another type, names that do not exist.
        ({**add, "action": {"type": "password removal"}}, "cannot be written by this server"),
        ({**change, "action_id": locked.id, "action": {"type": 5}}, "written"),
        (
            {**add, "action": {"type": "move to trash", "assign_title": "x"}},
            "'assign_title' belongs to assignment actions",
        ),
        ({**add, "trigger": {"type": "hourly"}}, "No workflow trigger type is called 'hourly'"),
        (
            {**add, "action": {"type": "assignment", "assign_tags": ["plmcp119-no-such-tag"]}},
            "plmcp119-no-such-tag",
        ),
        ({**call, "operation": "remove", "action_id": 999_999}, "holds no action 999999"),
        (
            {**change, "action_id": trash.id, "action": {"type": "assignment"}},
            "a change does not change the type",
        ),
    ):
        text = await refusal(ports, PART, given)
        assert told in text, (given, text)
        assert "planted" not in text

    assert await ports.workflows.get_workflow(made.id) == made
    assert await ports.workflows.get_workflow(ocr.id) == ocr


@pytest.mark.decision("decision-66")
@pytest.mark.decision("decision-82")
async def test_operations_on_parts_that_decision_66_refuses_change_nothing(
    backend: Backend,
) -> None:
    ports = backend.admin
    secret: JsonValue = {
        "url": HOOK,
        "use_params": False,
        "body": "b",
        "headers": {"X-Token": "planted"},
    }
    hidden: JsonValue = {"url": "https://h.invalid/@planted", "use_params": False}
    made = await make(
        ports,
        actions=(
            WorkflowAction(type=4, settings={"webhook": secret}),
            WorkflowAction(type=4, settings={"webhook": hidden}),
        ),
    )
    webhook, other = made.actions
    add: Json = {"workflow": made.name, "operation": "add"}
    change: Json = {"workflow": made.name, "operation": "change"}

    def hook(url: str, headers: Json | None = None) -> Json:
        more: Json = {} if headers is None else {"headers": headers}
        return {"type": "webhook", "webhook": {"url": url, **more}}

    cases: list[tuple[Json, str]] = [
        # The shown form of a stored URL without the id of its action.
        ({**add, "action": hook("https://h.invalid/x")}, AS_SHOWN),
        ({**change, "action_id": other.id, "action": hook("https://h.invalid/x")}, AS_SHOWN),
        # What stands for a URL that is not shown is never written.
        ({**add, "action": hook("a URL that is not shown")}, AS_SHOWN),
        # A hidden value of a header stays only where one is stored, and never travels.
        (
            {**add, "action": hook("https://n.invalid/y", {"X-Token": "(hidden)"})},
            "has no stored value to keep",
        ),
        (
            {
                **change,
                "action_id": webhook.id,
                "action": hook("https://n.invalid/y", {"X-Token": "(hidden)"}),
            },
            "cannot keep its stored value: the URL now points to another",
        ),
        # Losing what is stored hidden is confirmed, also while the workflow is disabled.
        (
            {"workflow": made.name, "operation": "remove", "action_id": webhook.id},
            "lose for good what is stored hidden",
        ),
    ]
    for given, told in cases:
        text = await refusal(ports, PART, given)
        assert told in text, (given, text)
        assert "planted" not in text
    assert await ports.workflows.get_workflow(made.id) == made

    done = await output(
        ports, PART, workflow=made.name, operation="remove", action_id=webhook.id, confirm=True
    )
    assert "planted" not in str(done)
    assert (await ports.workflows.get_workflow(made.id)).actions == (other,)


# --- explain_workflow


async def explained(ports: Ports, workflow: Workflow, document: int) -> Json:
    return await output(ports, "explain_workflow", workflow=workflow.name, document_id=document)


@pytest.mark.decision("decision-88")
async def test_a_workflow_is_explained_that_matches_and_one_that_does_not(
    backend: Backend,
) -> None:
    ports, (document,) = backend.admin, backend.new_documents(1)
    name = f"{SCRATCH_TAG}{uuid.uuid4().hex}"
    tag = (await ports.taxonomy_writes.create_taxonomy_item("tag", name, TaxonomyValues())).id
    filtered = WorkflowTrigger(type=3, settings={"filter_has_tags": [tag]})
    words = WorkflowTrigger(
        type=3,
        settings={"filter_has_tags": [tag], "match": "plmcp119-absent", "matching_algorithm": 1},
    )
    flow = await make(ports, triggers=(filtered,), enabled=True)
    wordy = await make(ports, triggers=(words,), enabled=True)
    off = await make(ports, triggers=(filtered,))

    before = await explained(ports, flow, document)
    _ = await ports.document_writes.update_document(document, DocumentPatch(tags=(tag,)))
    after = await explained(ports, flow, document)
    missed = await explained(ports, wordy, document)
    disabled = await explained(ports, off, document)
    missing = await refusal(ports, "explain_workflow", workflow=flow.name, document_id=999_999)

    def checks(answer: Json) -> dict[object, object]:
        [trigger] = cast("list[Json]", answer["triggers"])
        return {row["filter"]: row["result"] for row in cast("list[Json]", trigger["checks"])}

    assert (before["would_match_now"], checks(before)) == ("no", {"has_tags": "no"})
    assert "does not match the document as both are now. First reason: has_tags" in str(
        before["message"]
    )
    assert f"wanted is one of '{name}'" in str(before["triggers"])
    assert (after["would_match_now"], checks(after)) == ("yes", {"has_tags": "ok"})
    assert "matches the document as both are now." in str(after["message"])
    assert missed["would_match_now"] == "no"
    assert checks(missed) == {"match": "no", "has_tags": "ok"}
    assert disabled["would_match_now"] == "no"
    assert "is not enabled, so it does not run." in str(disabled["message"])
    assert "999999" in missing
    for answer in (before, after, missed, disabled):
        assert "keeps no readable record" in str(answer["note"])


@pytest.mark.decision("decision-88")
async def test_a_restricted_user_gets_an_explanation_with_gaps(backend: Backend) -> None:
    ports, (document, theirs) = backend.admin, backend.new_documents(2)
    _ = await ports.document_writes.update_document(document, DocumentPatch(owner=None))
    tagged = WorkflowTrigger(
        type=3, settings={"filter_has_tags": [await seeded(ports, "tag", "e2e-finance")]}
    )
    flow = await make(ports, triggers=(tagged,))
    user = backend.restricted(frozenset({"view_workflow", "view_document", "view_uisettings"}))

    found = await explained(user, flow, document)
    hidden = await refusal(user, "explain_workflow", workflow=flow.name, document_id=theirs)
    nobody = await refusal(
        backend.restricted(frozenset({"view_document", "view_uisettings"})),
        "explain_workflow",
        workflow=flow.id,
        document_id=document,
    )

    gaps = cast("list[str]", found["not_checked"])
    assert [gap.split(" (")[0].split(":")[0] for gap in gaps] == [
        *("the names of tags", "the history", "the consume task", "the log"),
    ]
    assert (found["did_run"], found["log"]) == ("unknown", [])
    [trigger] = cast("list[Json]", found["triggers"])
    [check] = cast("list[Json]", trigger["checks"])
    assert (check["filter"], check["result"]) == ("has_tags", "no")
    assert "e2e-finance" not in str(found)
    assert str(theirs) in hidden
    assert "may not perform this action" in nobody
