"""WorkflowService: workflows by name or id, nested triggers and actions with taxonomy by name."""

import re
from dataclasses import replace
from typing import TYPE_CHECKING, cast, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeState, seeded
from pydantic import ValidationError

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    Ambiguous,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.workflows import (
    ActionInput,
    EmailSettings,
    TriggerInput,
    WebhookSettings,
    Workflow,
    WorkflowAction,
    WorkflowCreate,
    WorkflowEdit,
    WorkflowTrigger,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.workflows import TRASH_NOTE, WorkflowService

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from mcp_server_paperless.domain.workflows import WorkflowValues

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-5"),
    pytest.mark.decision("decision-15"),
    pytest.mark.decision("decision-21"),
]

WORKFLOW_PERMISSIONS = ("view_workflow", "add_workflow", "change_workflow", "delete_workflow")
NEVER = TriggerInput(type="consumption", filename="never-*.pdf")
RETITLE = ActionInput(type="assignment", assign_title="x")
MAIL = ActionInput(type="email", email=EmailSettings(subject="s", body="b", to="a@example.org"))


class Recording(FakePaperless):
    """A fake that remembers every write it was given."""

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.writes: list[str] = []
        self.values: list[WorkflowValues] = []

    @override
    async def create_workflow(self, name: str, values: WorkflowValues) -> Workflow:
        self.writes.append("create")
        self.values.append(values)
        return await super().create_workflow(name, values)

    @override
    async def update_workflow(self, workflow_id: int, values: WorkflowValues) -> Workflow:
        self.writes.append("update")
        self.values.append(values)
        return await super().update_workflow(workflow_id, values)

    @override
    async def delete_workflow(self, workflow_id: int) -> None:
        self.writes.append("delete")
        await super().delete_workflow(workflow_id)


@final
class Setup:
    def __init__(
        self, user: str = ADMIN[0], *, writes: bool = True, grant: tuple[str, ...] = ()
    ) -> None:
        self.state: FakeState = seeded()
        self.state.users = [
            replace(u, permissions=u.permissions | frozenset(grant)) if u.username == user else u
            for u in self.state.users
        ]
        self.fake: Recording = Recording(self.state, user=user, writes=writes)
        self.resolver: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.workflows: WorkflowService = WorkflowService(self.fake, self.fake, self.resolver)

    def stored(self, name: str) -> Workflow:
        return next(w for w in self.state.workflows if w.name == name)

    def seed(self, name: str, *, enabled: bool = True, **given: object) -> Workflow:
        """A workflow Paperless holds already."""
        workflow = Workflow.model_validate(
            {
                "id": max((w.id for w in self.state.workflows), default=0) + 1,
                "name": name,
                "order": 0,
                "enabled": enabled,
                "triggers": [],
                "actions": [],
                **given,
            }
        )
        self.state.workflows.append(workflow)
        return workflow


def headers(settings: Mapping[str, JsonValue]) -> JsonValue:
    """The headers of the webhook in an action's settings."""
    return cast("dict[str, dict[str, JsonValue]]", settings)["webhook"]["headers"]


async def test_the_workflows_are_listed_with_taxonomy_names_and_only_non_default_settings() -> None:
    setup = Setup()
    _ = setup.seed(
        "Filing",
        order=2,
        triggers=[
            WorkflowTrigger(
                id=5,
                type=2,
                settings={
                    "sources": [1, 2, 3],
                    "matching_algorithm": 1,
                    "match": "invoice",
                    "is_insensitive": False,
                    "filter_has_tags": [1, 99],
                    "filter_has_not_tags": [],
                    "filter_has_correspondent": 1,
                    "filter_path": None,
                    "schedule_recurring_interval_days": 1,
                },
            ),
            WorkflowTrigger(
                id=6,
                type=1,
                settings={"sources": [1], "filter_filename": "*.pdf", "filter_mailrule": 4},
            ),
            WorkflowTrigger(id=15, type=9, settings={"matching_algorithm": 9}),
            WorkflowTrigger(
                id=7,
                type=4,
                settings={
                    "schedule_date_field": "custom_field",
                    "schedule_date_custom_field": 1,
                    "schedule_offset_days": -2,
                    "schedule_is_recurring": True,
                    "schedule_recurring_interval_days": 7,
                },
            ),
        ],
        actions=[
            WorkflowAction(
                id=8,
                type=1,
                settings={
                    "assign_title": "{{ correspondent }}",
                    "assign_tags": [2, 4],
                    "assign_owner": 2,
                    "assign_view_groups": [1],
                    "assign_custom_fields": [1],
                    "assign_custom_fields_values": {"1": "v"},
                    "assign_correspondent": None,
                    "remove_all_tags": False,
                },
            ),
            WorkflowAction(id=9, type=2, settings={"remove_all_tags": True, "remove_tags": []}),
            WorkflowAction(
                id=10,
                type=4,
                settings={
                    "webhook": {
                        "url": "https://example.org/hook",
                        "use_params": True,
                        "as_json": False,
                        "params": None,
                        "body": None,
                        "headers": {"X-Token": "secret"},
                        "include_document": True,
                    }
                },
            ),
            WorkflowAction(id=11, type=5, settings={"passwords": "hunter2"}),
            WorkflowAction(id=16, type=5, settings={"passwords": None}),
            WorkflowAction(
                id=17,
                type=3,
                settings={
                    "email": {
                        "subject": "s",
                        "body": "b",
                        "to": "a@example.org",
                        "include_document": False,
                        "id": None,
                    }
                },
            ),
            WorkflowAction(id=12, type=6),
            WorkflowAction(
                id=13,
                type=8,
                settings={
                    "ai_suggestion_fields": ["tags"],
                    "ai_create_missing": True,
                    "ai_overwrite_existing": True,
                },
            ),
            WorkflowAction(id=14, type=77),
        ],
    )

    listing = await setup.workflows.list()

    assert listing.count == 1
    [workflow] = listing.workflows
    assert (workflow.id, workflow.name, workflow.order, workflow.enabled) == (1, "Filing", 2, True)
    assert [(t.id, t.type, t.type_name, t.settings) for t in workflow.triggers] == [
        (
            5,
            2,
            "document added",
            {
                "matching_algorithm": "any",
                "match": "invoice",
                "is_insensitive": False,
                "has_tags": ["e2e-finance", 99],
                "has_correspondent": "e2e-acme",
            },
        ),
        (
            6,
            1,
            "consumption started",
            {"sources": ["consume_folder"], "filename": "*.pdf", "mailrule": 4},
        ),
        (15, 9, "unknown type 9", {"matching_algorithm": 9}),
        (
            7,
            4,
            "scheduled",
            {
                "schedule_date_field": "custom_field",
                "schedule_date_custom_field": "e2e-string",
                "schedule_offset_days": -2,
                "schedule_is_recurring": True,
                "schedule_recurring_interval_days": 7,
            },
        ),
    ]
    assert [(a.id, a.type, a.type_name, a.settings) for a in workflow.actions] == [
        (
            8,
            1,
            "assignment",
            {
                "assign_title": "{{ correspondent }}",
                "assign_tags": ["e2e-invoices", "e2e-inbox"],
                "assign_owner": "admin",
                "assign_view_groups": ["e2e-group"],
                "assign_custom_fields": ["e2e-string"],
                "assign_custom_fields_values": {"e2e-string": "v"},
            },
        ),
        (9, 2, "removal", {"remove_all_tags": True}),
        (
            10,
            4,
            "webhook",
            {
                "webhook": {
                    "url": "https://example.org/hook",
                    "use_params": True,
                    "as_json": False,
                    "headers": {"X-Token": "(hidden)"},
                    "include_document": True,
                }
            },
        ),
        (11, 5, "password removal", {"passwords": "(hidden)"}),
        (16, 5, "password removal", {}),
        (
            17,
            3,
            "email",
            {
                "email": {
                    "subject": "s",
                    "body": "b",
                    "to": "a@example.org",
                    "include_document": False,
                }
            },
        ),
        (12, 6, "move to trash", {}),
        (
            13,
            8,
            "apply AI suggestions",
            {
                "ai_suggestion_fields": ["tags"],
                "ai_create_missing": True,
                "ai_overwrite_existing": True,
            },
        ),
        (14, 77, "unknown type 77", {}),
    ]


async def test_custom_field_values_show_the_field_names_even_without_the_field_list() -> None:
    setup = Setup()
    _ = setup.seed(
        "w",
        actions=[
            WorkflowAction(
                type=1, settings={"assign_custom_fields_values": {"1": "v", "odd": "x", "99": "y"}}
            )
        ],
    )

    [workflow] = (await setup.workflows.list()).workflows

    assert workflow.actions[0].settings == {
        "assign_custom_fields_values": {"e2e-string": "v", "odd": "x", "99": "y"}
    }


async def test_names_that_the_user_may_not_look_up_are_shown_as_ids() -> None:
    setup = Setup(READER[0], grant=("view_workflow",))
    _ = setup.seed("w", actions=[WorkflowAction(type=1, settings={"assign_owner": 2})])

    [workflow] = (await setup.workflows.list()).workflows

    assert workflow.actions[0].settings == {"assign_owner": 2}


async def test_a_workflow_is_read_by_name_ignoring_case_or_by_id() -> None:
    setup = Setup()
    _ = setup.seed("Filing")

    assert (await setup.workflows.get("FILING")).id == 1
    assert (await setup.workflows.get(1)).name == "Filing"
    assert (await setup.workflows.get("1")).name == "Filing"


async def test_an_unknown_workflow_is_not_found_with_suggestions() -> None:
    setup = Setup()
    _ = setup.seed("Filing")

    with pytest.raises(NotFound, match="No workflow is named 'Filling'") as raised:
        _ = await setup.workflows.get("Filling")

    assert raised.value.hint is not None
    assert "Filing" in raised.value.hint
    assert "list_workflows" in raised.value.hint
    assert "list_taxonomy" not in raised.value.hint


async def test_an_unknown_workflow_id_names_the_list_tool_not_the_taxonomy() -> None:
    with pytest.raises(NotFound, match="No workflow has id 9") as raised:
        _ = await Setup().workflows.get(9)

    assert raised.value.hint == "Call list_workflows to see the ids."


async def test_two_workflows_with_one_name_are_ambiguous() -> None:
    setup = Setup()
    _ = setup.seed("Same")
    _ = setup.seed("SAME")

    with pytest.raises(Ambiguous, match="2 workflows match"):
        _ = await setup.workflows.get("same")


async def test_a_user_without_the_view_permission_is_denied() -> None:
    with pytest.raises(PermissionDenied):
        _ = await Setup(READER[0]).workflows.list()


async def test_a_created_workflow_resolves_types_and_names_to_what_paperless_stores() -> None:
    setup = Setup()
    spec = WorkflowCreate(
        name="  Invoices ",
        order=4,
        enabled=False,
        triggers=(
            TriggerInput(
                type="Document Added",
                has_tags=["e2e-finance", 3],
                has_all_tags=["e2e-invoices"],
                has_not_tags=["e2e-inbox"],
                has_any_correspondents=["E2E-ACME"],
                has_not_correspondents=[1],
                has_any_document_types=["e2e-invoice"],
                has_not_document_types=["e2e-invoice"],
                has_any_storage_paths=["e2e-archive"],
                has_not_storage_paths=["e2e-archive"],
                has_correspondent="e2e-acme",
                has_document_type="e2e-invoice",
                has_storage_path="e2e-archive",
                match="invoice",
                matching_algorithm="Regex",
                is_insensitive=False,
                custom_field_query='["e2e-string", "exists", true]',
            ),
            TriggerInput(
                type="consumption",
                sources=["Api_Upload", 3, "consume folder"],
                path="/in/*",
                filename="*.pdf",
                mailrule=2,
            ),
            TriggerInput(
                type=4,
                schedule_date_field="custom_field",
                schedule_date_custom_field="e2e-date",
                schedule_offset_days=-3,
                schedule_is_recurring=True,
                schedule_recurring_interval_days=7,
            ),
            TriggerInput(type="updated"),
        ),
        actions=(
            ActionInput(
                type="Assignment",
                assign_title="t",
                assign_tags=["e2e-finance", 2],
                assign_correspondent="e2e-acme",
                assign_document_type="e2e-invoice",
                assign_storage_path="e2e-archive",
                assign_owner="admin",
                assign_view_users=["e2e-reader"],
                assign_view_groups=["e2e-group"],
                assign_change_users=[2],
                assign_change_groups=[1],
                assign_custom_fields=["e2e-string"],
                assign_custom_fields_values={"e2e-integer": 5, "3": "x"},
            ),
            ActionInput(
                type=2,
                remove_all_tags=True,
                remove_tags=["e2e-inbox"],
                remove_all_correspondents=False,
                remove_correspondents=["e2e-acme"],
                remove_document_types=["e2e-invoice"],
                remove_storage_paths=["e2e-archive"],
                remove_custom_fields=["e2e-string"],
                remove_owners=["admin"],
                remove_view_users=["e2e-reader"],
                remove_view_groups=["e2e-group"],
                remove_change_users=["admin"],
                remove_change_groups=["e2e-group"],
            ),
            MAIL,
            ActionInput(type="move to trash"),
            ActionInput(type="remote OCR"),
        ),
    )

    made = await setup.workflows.create(spec, confirm=True)

    assert (made.name, made.order, made.enabled) == ("Invoices", 4, False)
    stored = setup.fake.values[0]  # what the writer was given, without Paperless's defaults
    assert stored.triggers is not None
    assert stored.actions is not None
    assert [(t.type, t.settings) for t in stored.triggers] == [
        (
            2,
            {
                "filter_has_tags": [1, 3],
                "filter_has_all_tags": [2],
                "filter_has_not_tags": [4],
                "filter_has_any_correspondents": [1],
                "filter_has_not_correspondents": [1],
                "filter_has_any_document_types": [1],
                "filter_has_not_document_types": [1],
                "filter_has_any_storage_paths": [1],
                "filter_has_not_storage_paths": [1],
                "filter_has_correspondent": 1,
                "filter_has_document_type": 1,
                "filter_has_storage_path": 1,
                "match": "invoice",
                "matching_algorithm": 4,
                "is_insensitive": False,
                "filter_custom_field_query": '["e2e-string", "exists", true]',
            },
        ),
        (
            1,
            {
                "sources": [2, 3, 1],
                "filter_path": "/in/*",
                "filter_filename": "*.pdf",
                "filter_mailrule": 2,
            },
        ),
        (
            4,
            {
                "schedule_date_field": "custom_field",
                "schedule_date_custom_field": 3,
                "schedule_offset_days": -3,
                "schedule_is_recurring": True,
                "schedule_recurring_interval_days": 7,
            },
        ),
        (3, {}),
    ]
    assert stored.actions[0].settings == {
        "assign_title": "t",
        "assign_tags": [1, 2],
        "assign_correspondent": 1,
        "assign_document_type": 1,
        "assign_storage_path": 1,
        "assign_owner": 2,
        "assign_view_users": [3],
        "assign_view_groups": [1],
        "assign_change_users": [2],
        "assign_change_groups": [1],
        "assign_custom_fields": [1, 5, 3],
        "assign_custom_fields_values": {"5": 5, "3": "x"},
    }
    assert stored.actions[1].settings == {
        "remove_all_tags": True,
        "remove_tags": [4],
        "remove_all_correspondents": False,
        "remove_correspondents": [1],
        "remove_document_types": [1],
        "remove_storage_paths": [1],
        "remove_custom_fields": [1],
        "remove_owners": [2],
        "remove_view_users": [3],
        "remove_view_groups": [1],
        "remove_change_users": [2],
        "remove_change_groups": [1],
    }
    assert [a.type for a in stored.actions] == [1, 2, 3, 6, 7]
    assert stored.actions[2].settings["email"] == {
        "subject": "s",
        "body": "b",
        "to": "a@example.org",
        "include_document": False,
    }
    assert made.actions[2].settings["email"] == stored.actions[2].settings["email"]


async def test_a_workflow_with_nothing_but_a_name_is_created_enabled_and_empty() -> None:
    setup = Setup()

    made = await setup.workflows.create(WorkflowCreate(name="bare"), confirm=False)

    assert (made.enabled, made.triggers, made.actions, made.order) == (True, (), (), 0)
    assert setup.fake.values[0].model_fields_set == {"enabled", "triggers", "actions"}


async def test_a_trigger_type_is_taken_by_number_label_or_the_short_name() -> None:
    setup = Setup()
    types = (1, "1", "consumption", "Consumption Started", "consumption_started")
    types += (2, "added", "document added", "Document_Added", 3, "updated", 4, "Scheduled")
    triggers = tuple(TriggerInput(type=t, filename="f") for t in types[:5])
    triggers += tuple(TriggerInput(type=t) for t in types[5:])

    made = await setup.workflows.create(
        WorkflowCreate(name="t", triggers=triggers, enabled=False), confirm=False
    )

    assert [t.type for t in made.triggers] == [1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 4, 4]


async def test_an_action_type_is_taken_by_number_label_or_the_short_name() -> None:
    setup = Setup()
    given = (1, "assignment", 2, "Removal", "e-mail", "WEBHOOK", "Move_To_Trash", "trash", "ocr")
    actions: list[ActionInput] = []
    for kind in given:
        match str(kind).lower().replace("_", " "):
            case "e-mail" | "email":
                actions.append(
                    ActionInput(type=kind, email=EmailSettings(subject="s", body="b", to="t"))
                )
            case "webhook":
                actions.append(
                    ActionInput(type=kind, webhook=WebhookSettings(url="https://h.example/x"))
                )
            case _:
                actions.append(ActionInput(type=kind))
    triggers = (NEVER,)

    made = await setup.workflows.create(
        WorkflowCreate(name="a", triggers=triggers, actions=tuple(actions), enabled=False),
        confirm=False,
    )

    assert [a.type for a in made.actions] == [1, 1, 2, 2, 3, 4, 6, 6, 7]


@pytest.mark.parametrize(
    ("triggers", "message"),
    [
        (TriggerInput(type=9), "No workflow trigger type 9"),
        (TriggerInput(type="hourly"), "No workflow trigger type is called 'hourly'"),
        (TriggerInput(type=0), "No workflow trigger type 0"),
    ],
)
async def test_an_unsupported_trigger_type_is_rejected_naming_the_types_there_are(
    triggers: TriggerInput, message: str
) -> None:
    setup = Setup()

    with pytest.raises(Validation, match=message) as raised:
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", triggers=(triggers,)), confirm=True
        )

    assert raised.value.hint is not None
    assert "consumption" in raised.value.hint
    assert "scheduled" in raised.value.hint
    assert setup.fake.writes == []


@pytest.mark.decision("decision-86")
@pytest.mark.parametrize("kind", [5, "manual", "Manual"])
async def test_a_manual_trigger_is_written_with_the_filters_paperless_reads(
    kind: int | str,
) -> None:
    setup = Setup()
    trigger = TriggerInput(
        type=kind,
        filename="*.pdf",
        match="x",
        matching_algorithm="any",
        has_tags=["e2e-finance"],
        custom_field_query='["e2e-url","exists",true]',
    )

    made = await setup.workflows.create(
        WorkflowCreate(name="n", triggers=(trigger,), actions=(RETITLE,)), confirm=False
    )
    again = await setup.workflows.update(
        "n", WorkflowEdit(triggers=(trigger, NEVER)), confirm=False
    )

    [shown] = made.triggers
    assert (shown.type, shown.type_name) == (5, "manual")
    assert shown.settings == {
        "filename": "*.pdf",
        "match": "x",
        "matching_algorithm": "any",
        "has_tags": ["e2e-finance"],
        "custom_field_query": '["e2e-url","exists",true]',
    }
    assert [t.type for t in again.triggers] == [5, 1]


@pytest.mark.decision("decision-86")
@pytest.mark.parametrize(
    "setting",
    [
        {"sources": ["web_ui"]},
        {"path": "/x"},
        {"mailrule": 1},
        {"schedule_offset_days": 1},
        {"schedule_date_field": "added"},
    ],
    ids=str,
)
async def test_a_setting_paperless_ignores_on_a_manual_trigger_is_refused(
    setting: dict[str, object],
) -> None:
    setup = Setup()
    trigger = TriggerInput.model_validate({"type": "manual", **setting})

    with pytest.raises(Validation, match="only applies to"):
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", triggers=(trigger,)), confirm=True
        )

    assert setup.fake.writes == []


@pytest.mark.decision("decision-86")
async def test_a_workflow_with_only_a_manual_trigger_is_confirmed_as_run_by_hand() -> None:
    setup = Setup(writes=False)
    spec = WorkflowCreate(name="n", triggers=(TriggerInput(type="manual"),), actions=(MAIL,))

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.workflows.create(spec, confirm=False)

    assert "runs only when a user runs it by hand (manual)" in raised.value.message


@pytest.mark.decision("decision-86")
@pytest.mark.parametrize("kind", [8, "apply AI suggestions", "Apply-AI-Suggestions"])
async def test_applying_ai_suggestions_is_written_and_confirmed_while_enabled(
    kind: int | str,
) -> None:
    setup = Setup()
    ai = ActionInput(type=kind, ai_suggestion_fields=["title", "tags"], ai_create_missing=True)
    triggers = (TriggerInput(type="added"),)
    spec = WorkflowCreate(name="n", triggers=triggers, actions=(ai,))

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.workflows.create(spec, confirm=False)
    off = await setup.workflows.create(spec.model_copy(update={"enabled": False}), confirm=False)
    with pytest.raises(ConfirmationRequired):
        _ = await setup.workflows.update("n", WorkflowEdit(enabled=True), confirm=False)

    assert (
        "ask the AI backend about the text of every matching document and apply what it suggests "
        "for title, tags, only where there is no value (tags are only added), creating tags, "
        "correspondents and document types that do not exist"
    ) in raised.value.message
    [action] = off.actions
    assert (action.type, action.type_name) == (8, "apply AI suggestions")
    assert action.settings == {"ai_suggestion_fields": ["title", "tags"], "ai_create_missing": True}


@pytest.mark.decision("decision-86")
async def test_applying_ai_suggestions_needs_fields_and_keeps_to_its_settings() -> None:
    setup = Setup()

    for action, said in (
        (ActionInput(type=8), "needs ai_suggestion_fields"),
        (ActionInput(type=8, ai_create_missing=True), "needs ai_suggestion_fields"),
        (ActionInput(type=8, ai_suggestion_fields=["title"], assign_title="x"), "belongs to"),
        (ActionInput(type=1, ai_overwrite_existing=True), "belongs to apply AI suggestions"),
    ):
        with pytest.raises(Validation, match=said):
            _ = await setup.workflows.create(
                WorkflowCreate(name="n", actions=(action,)), confirm=True
            )
    for fields in ([], ["content"]):
        with pytest.raises(ValidationError):
            _ = ActionInput.model_validate({"type": 8, "ai_suggestion_fields": fields})
    assert setup.fake.writes == []


@pytest.mark.decision("decision-86")
async def test_a_list_with_ai_suggestions_is_replaced_like_any_other() -> None:
    setup = Setup()
    ai = WorkflowAction(id=4, type=8, settings={"ai_suggestion_fields": ["title"]})
    _ = setup.seed("w", enabled=False, actions=[ai])

    done = await setup.workflows.update("w", WorkflowEdit(actions=(RETITLE,)), confirm=False)

    assert [a.type for a in done.actions] == [1]


async def test_a_manual_trigger_is_listed_by_its_label() -> None:
    setup = Setup()
    _ = setup.seed("w", enabled=False, triggers=[WorkflowTrigger(id=1, type=5, settings={})])

    listed = await setup.workflows.get("w")

    assert [(t.type, t.type_name) for t in listed.triggers] == [(5, "manual")]


async def test_replacing_triggers_never_drops_one_of_an_unknown_type() -> None:
    setup = Setup()
    _ = setup.seed(
        "w",
        enabled=False,
        triggers=[WorkflowTrigger(id=1, type=6, settings={}), WorkflowTrigger(id=2, type=2)],
    )

    with pytest.raises(Validation, match="holds triggers this server cannot write") as raised:
        _ = await setup.workflows.update("w", WorkflowEdit(triggers=(NEVER,)), confirm=False)
    renamed = await setup.workflows.update("w", WorkflowEdit(name="v"), confirm=False)

    assert "unknown type 6 (id 1)" in raised.value.message
    assert "web UI" in (raised.value.hint or "")
    assert setup.fake.writes == ["update"]
    assert [t.type for t in renamed.triggers] == [6, 2]  # untouched, in the order stored


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        (5, r"action type 'password removal' \(5\) cannot be written by this server"),
        ("Password Removal", r"action type 'password removal' \(5\) cannot be written"),
        (99, "No workflow action type 99"),
        ("shell", "No workflow action type is called 'shell'"),
    ],
)
async def test_an_unsupported_action_type_is_rejected_with_the_reason(
    kind: int | str, message: str
) -> None:
    setup = Setup()

    with pytest.raises(Validation, match=message) as raised:
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", actions=(ActionInput(type=kind),)), confirm=True
        )

    assert raised.value.hint is not None
    assert setup.fake.writes == []


async def test_the_reasons_name_what_to_do_instead() -> None:
    setup = Setup()

    with pytest.raises(Validation) as password:
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", actions=(ActionInput(type=5),)), confirm=True
        )

    assert "passwords" in password.value.message
    assert "web UI" in (password.value.hint or "")


@pytest.mark.parametrize(
    ("trigger", "message"),
    [
        (TriggerInput(type="added", path="/in"), "'path' only applies to consumption"),
        (TriggerInput(type="updated", sources=["web_ui"]), "'sources' only applies to consumption"),
        (TriggerInput(type="scheduled", filename="x"), "'filename' only applies to consumption"),
        (TriggerInput(type="added", mailrule=1), "'mailrule' only applies to consumption"),
        (
            TriggerInput(type="consumption", filename="x", has_tags=["e2e-finance"]),
            "'has_tags' does not apply to consumption",
        ),
        (
            TriggerInput(type="consumption", filename="x", match="x"),
            "'match' does not apply to consumption",
        ),
        (
            TriggerInput(type="consumption", filename="x", schedule_offset_days=1),
            "'schedule_offset_days' only applies to scheduled",
        ),
        (
            TriggerInput(type="added", schedule_is_recurring=True),
            "'schedule_is_recurring' only applies to scheduled",
        ),
    ],
)
async def test_a_trigger_setting_that_does_not_fit_the_type_is_an_error_not_ignored(
    trigger: TriggerInput, message: str
) -> None:
    setup = Setup()

    with pytest.raises(Validation, match=message):
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", triggers=(trigger,)), confirm=True
        )

    assert setup.fake.writes == []


@pytest.mark.parametrize(
    ("action", "message"),
    [
        (
            ActionInput(type="move to trash", assign_title="x"),
            "'assign_title' belongs to assignment",
        ),
        (
            ActionInput(type="assignment", remove_all_tags=True),
            "'remove_all_tags' belongs to removal",
        ),
        (
            ActionInput(type="removal", email=EmailSettings(subject="s", body="b", to="t")),
            "'email' belongs to email",
        ),
        (
            ActionInput(type="assignment", webhook=WebhookSettings(url="https://h.example")),
            "'webhook' belongs to webhook",
        ),
        (ActionInput(type="email"), "An email action needs email settings"),
        (ActionInput(type="webhook"), "A webhook action needs webhook settings"),
    ],
)
async def test_an_action_setting_that_does_not_fit_the_type_is_an_error_not_ignored(
    action: ActionInput, message: str
) -> None:
    setup = Setup()

    with pytest.raises(Validation, match=message):
        _ = await setup.workflows.create(WorkflowCreate(name="n", actions=(action,)), confirm=True)

    assert setup.fake.writes == []


@pytest.mark.parametrize(
    ("trigger", "message"),
    [
        (
            TriggerInput(type="added", matching_algorithm="fuzzy-ish"),
            "No matching algorithm is called 'fuzzy-ish'",
        ),
        (TriggerInput(type="added", matching_algorithm=9), "No matching algorithm 9"),
        (
            TriggerInput(type="consumption", filename="x", sources=["ftp"]),
            "No source is called 'ftp'",
        ),
        (TriggerInput(type="consumption", filename="x", sources=[9]), "No source 9"),
        (
            TriggerInput(type="scheduled", schedule_date_field="deleted"),
            "No schedule date field 'deleted'",
        ),
        (
            TriggerInput(type="added", custom_field_query="[not json"),
            "custom_field_query is not valid JSON",
        ),
        (TriggerInput(type="added", has_tags=["nope"]), "No tag is named 'nope'"),
        (TriggerInput(type="added", has_correspondent="nope"), "No correspondent is named 'nope'"),
        (
            TriggerInput(type="scheduled", schedule_date_custom_field="nope"),
            "No custom field is named 'nope'",
        ),
    ],
)
async def test_a_value_that_does_not_exist_is_refused_before_anything_is_written(
    trigger: TriggerInput, message: str
) -> None:
    setup = Setup()

    with pytest.raises(NotFound if "named" in message else Validation, match=message):
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", triggers=(trigger,)), confirm=True
        )

    assert setup.fake.writes == []


async def test_a_name_in_an_action_that_matches_nothing_is_not_found() -> None:
    setup = Setup()

    with pytest.raises(NotFound, match="No user is named 'nobody'"):
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", actions=(ActionInput(type=1, assign_owner="nobody"),)),
            confirm=True,
        )
    with pytest.raises(NotFound, match="No custom field is named 'nope'"):
        _ = await setup.workflows.create(
            WorkflowCreate(
                name="n",
                actions=(ActionInput(type=1, assign_custom_fields_values={"nope": 1}),),
            ),
            confirm=True,
        )

    assert setup.fake.writes == []


async def test_a_blank_name_and_a_taken_name_are_rejected_before_the_write() -> None:
    setup = Setup()
    _ = setup.seed("Filing")

    with pytest.raises(Validation, match="name is empty"):
        _ = await setup.workflows.create(WorkflowCreate(name="  "), confirm=False)
    with pytest.raises(Validation, match="is already named 'Filing' \\(id 1\\)"):
        _ = await setup.workflows.create(WorkflowCreate(name="FILING"), confirm=False)

    assert setup.fake.writes == []


async def test_a_hidden_header_value_has_nothing_to_keep_on_a_new_workflow() -> None:
    setup = Setup()
    hook = WebhookSettings(url="https://h.example/x", headers={"X-Token": "(hidden)"})

    with pytest.raises(Validation, match="Header 'X-Token' is shown as '\\(hidden\\)'"):
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", actions=(ActionInput(type=4, webhook=hook),), enabled=False),
            confirm=True,
        )

    assert setup.fake.writes == []


async def test_a_webhook_is_stored_with_its_settings_and_its_headers() -> None:
    setup = Setup()
    hook = WebhookSettings(
        url="https://h.example/x?token=1",
        use_params=False,
        as_json=True,
        params={"a": "b"},
        body="{}",
        headers={"X-Token": "real"},
        include_document=True,
    )

    made = await setup.workflows.create(
        WorkflowCreate(name="n", actions=(ActionInput(type=4, webhook=hook),), enabled=False),
        confirm=False,
    )

    assert setup.stored("n").actions[0].settings["webhook"] == {
        "url": "https://h.example/x?token=1",
        "use_params": False,
        "as_json": True,
        "params": {"a": "b"},
        "body": "{}",
        "headers": {"X-Token": "real"},
        "include_document": True,
    }
    assert made.actions[0].settings["webhook"] == {
        "url": "https://h.example/x?token=1",
        "use_params": False,
        "as_json": True,
        "params": {"a": "b"},
        "body": "{}",
        "headers": {"X-Token": "(hidden)"},
        "include_document": True,
    }


async def test_a_workflow_that_is_disabled_or_only_assigns_needs_no_confirmation() -> None:
    setup = Setup(writes=True)
    acting = (ActionInput(type="move to trash"), MAIL)

    off = await setup.workflows.create(
        WorkflowCreate(name="off", triggers=(NEVER,), actions=acting, enabled=False), confirm=False
    )
    tags = await setup.workflows.create(
        WorkflowCreate(
            name="tags",
            triggers=(NEVER,),
            actions=(
                ActionInput(
                    type=1, assign_tags=["e2e-finance"], assign_title="t", assign_correspondent=1
                ),
            ),
        ),
        confirm=False,
    )

    assert (off.enabled, tags.enabled) == (False, True)


@pytest.mark.parametrize(
    ("actions", "acts"),
    [
        ((ActionInput(type="move to trash"),), "move the matching documents to the trash"),
        ((MAIL,), "send an e-mail to 'a@example.org'"),
        (
            (
                ActionInput(
                    type="email",
                    email=EmailSettings(
                        subject="s", body="b", to="a@x.org, b@x.org", include_document=True
                    ),
                ),
            ),
            "send an e-mail to 'a@x.org', 'b@x.org' with the document attached",
        ),
        (
            (
                ActionInput(
                    type="webhook",
                    webhook=WebhookSettings(url="https://user:pw@hooks.example:8443/a/b?token=1"),
                ),
            ),
            "send a POST request to 'https://hooks.example:8443', with credentials (not shown)",
        ),
        (
            (
                ActionInput(
                    type="webhook",
                    webhook=WebhookSettings(url="https://h.example/x", include_document=True),
                ),
            ),
            "send a POST request to 'h.example' with the document",
        ),
        (
            (
                ActionInput(
                    type="removal",
                    remove_all_tags=True,
                    remove_tags=["e2e-inbox"],
                    remove_view_users=["admin"],
                ),
            ),
            "remove from the matching documents: all tags, tags, view users",
        ),
        (
            (ActionInput(type="assignment", assign_owner="admin"),),
            "change who owns or may see the matching documents",
        ),
        (
            (ActionInput(type="assignment", assign_change_groups=["e2e-group"]),),
            "change who owns or may see the matching documents",
        ),
        (
            (ActionInput(type="assignment", assign_view_users=["admin"]),),
            "change who owns or may see the matching documents",
        ),
    ],
)
async def test_an_enabled_workflow_that_acts_outward_or_takes_things_away_needs_confirm(
    actions: tuple[ActionInput, ...], acts: str
) -> None:
    setup = Setup(writes=False)
    spec = WorkflowCreate(
        name="Cleanup",
        triggers=(TriggerInput(type="added", has_tags=["e2e-inbox"]),),
        actions=actions,
    )

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.workflows.create(spec, confirm=False)

    assert raised.value.message == (
        "Nothing was changed. Confirming would create the workflow 'Cleanup', which is enabled "
        "and runs by itself on every document that matches its triggers (document added, "
        f"filtered by has_tags) and will: {acts}."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert setup.state.workflows == []


async def test_the_refusal_lists_every_acting_action_and_every_trigger() -> None:
    setup = Setup(writes=False)
    spec = WorkflowCreate(
        name="Many",
        triggers=(NEVER, TriggerInput(type="scheduled")),
        actions=(RETITLE, ActionInput(type="move to trash"), MAIL),
    )

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.workflows.create(spec, confirm=False)

    assert raised.value.message == (
        "Nothing was changed. Confirming would create the workflow 'Many', which is enabled "
        "and runs by itself on every document that matches its triggers (consumption started, "
        "filtered by filename; scheduled, also on documents that already exist) and will: "
        "move the matching documents to the trash; send an e-mail to 'a@example.org'."
    )


async def test_the_refusal_says_when_there_is_no_trigger_yet() -> None:
    setup = Setup(writes=False)

    with pytest.raises(
        ConfirmationRequired, match=r"\(no trigger yet: it does nothing until one is added\)"
    ):
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", actions=(ActionInput(type="move to trash"),)), confirm=False
        )


async def test_a_confirmed_workflow_that_acts_is_created() -> None:
    setup = Setup()

    made = await setup.workflows.create(
        WorkflowCreate(name="n", triggers=(NEVER,), actions=(ActionInput(type="move to trash"),)),
        confirm=True,
    )

    assert [a.type_name for a in made.actions] == ["move to trash"]
    assert made.note == TRASH_NOTE
    assert setup.fake.writes == ["create"]


async def test_the_refusal_names_unknown_trigger_types_and_every_filter() -> None:
    setup = Setup()
    _ = setup.seed(
        "w",
        triggers=[
            WorkflowTrigger(type=9),
            WorkflowTrigger(type=2, settings={"filter_has_tags": [1], "match": "x"}),
        ],
    )

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.workflows.update(
            "w", WorkflowEdit(actions=(ActionInput(type="move to trash"),)), confirm=False
        )

    assert "(unknown type 9; document added, filtered by has_tags)" in raised.value.message


async def test_a_hidden_header_on_a_new_action_with_an_id_has_nothing_to_keep() -> None:
    setup = Setup()
    hook = WebhookSettings(url="https://h.example/x", headers={"X-Token": "(hidden)"})

    with pytest.raises(Validation, match="has no stored value to keep"):
        _ = await setup.workflows.create(
            WorkflowCreate(
                name="n", actions=(ActionInput(type=4, id=5, webhook=hook),), enabled=False
            ),
            confirm=False,
        )


async def test_a_taxonomy_id_is_looked_up_like_a_name_and_an_unknown_one_is_not_found() -> None:
    setup = Setup()

    for given in ("99", 99):
        with pytest.raises(NotFound, match="No tag has id 99"):
            _ = await setup.workflows.create(
                WorkflowCreate(name="n", triggers=(TriggerInput(type="added", has_tags=[given]),)),
                confirm=False,
            )
    made = await setup.workflows.create(
        WorkflowCreate(name="n", triggers=(TriggerInput(type="added", has_tags=["1", 2]),)),
        confirm=False,
    )

    assert made.triggers[0].settings == {"has_tags": ["e2e-finance", "e2e-invoices"]}
    assert setup.fake.writes == ["create"]


async def test_a_user_without_the_add_permission_is_denied_after_the_confirmation_step() -> None:
    setup = Setup(READER[0], grant=("view_workflow",))

    with pytest.raises(PermissionDenied):
        _ = await setup.workflows.create(WorkflowCreate(name="n"), confirm=False)


async def test_a_rename_changes_only_the_name_and_needs_no_confirmation() -> None:
    setup = Setup()
    _ = await setup.workflows.create(
        WorkflowCreate(name="old", triggers=(NEVER,), actions=(MAIL,)), confirm=True
    )

    renamed = await setup.workflows.update("old", WorkflowEdit(name=" new "), confirm=False)

    assert renamed.name == "new"
    assert setup.fake.values[-1].model_fields_set == {"name"}
    assert [a.type_name for a in renamed.actions] == ["email"]


async def test_new_triggers_and_actions_replace_the_old_ones_and_are_resolved() -> None:
    setup = Setup()
    _ = await setup.workflows.create(
        WorkflowCreate(name="w", triggers=(NEVER,), actions=(RETITLE,)), confirm=False
    )

    after = await setup.workflows.update(
        "w",
        WorkflowEdit(
            triggers=(TriggerInput(type="added", has_tags=["e2e-finance"]),),
            actions=(ActionInput(type=1, assign_tags=["e2e-inbox"]),),
            order=3,
            enabled=False,
        ),
        confirm=False,
    )

    assert [(t.type_name, t.settings) for t in after.triggers] == [
        ("document added", {"has_tags": ["e2e-finance"]})
    ]
    assert [a.settings for a in after.actions] == [{"assign_tags": ["e2e-inbox"]}]
    assert (after.order, after.enabled) == (3, False)
    assert setup.fake.values[-1].model_fields_set == {"triggers", "actions", "order", "enabled"}


async def test_an_update_can_empty_the_lists() -> None:
    setup = Setup()
    _ = await setup.workflows.create(
        WorkflowCreate(name="w", triggers=(NEVER,), actions=(RETITLE,)), confirm=False
    )

    after = await setup.workflows.update("w", WorkflowEdit(triggers=(), actions=()), confirm=False)

    assert (after.triggers, after.actions) == ((), ())


async def test_an_update_that_names_nothing_is_refused() -> None:
    setup = Setup()
    _ = setup.seed("w")

    with pytest.raises(Validation, match="Nothing to change: no setting was named"):
        _ = await setup.workflows.update("w", WorkflowEdit(), confirm=False)

    assert setup.fake.writes == []


async def test_a_rename_to_the_name_of_another_workflow_is_refused_but_its_own_is_fine() -> None:
    setup = Setup()
    _ = setup.seed("one")
    _ = setup.seed("two")

    with pytest.raises(Validation, match="is already named 'one' \\(id 1\\)"):
        _ = await setup.workflows.update("two", WorkflowEdit(name="ONE"), confirm=False)
    with pytest.raises(Validation, match="name is empty"):
        _ = await setup.workflows.update("two", WorkflowEdit(name=" "), confirm=False)
    same = await setup.workflows.update("two", WorkflowEdit(name="two", order=2), confirm=False)

    assert (same.name, same.order) == ("two", 2)


async def test_the_update_of_a_missing_workflow_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().workflows.update(99, WorkflowEdit(name="x"), confirm=False)


async def test_replacing_the_actions_of_an_enabled_workflow_with_an_acting_one_needs_confirm() -> (
    None
):
    setup = Setup()
    _ = setup.seed("w", triggers=[WorkflowTrigger(type=2)])
    trash = ActionInput(type="move to trash")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.workflows.update("w", WorkflowEdit(actions=(trash,)), confirm=False)

    assert raised.value.message == (
        "Nothing was changed. Confirming would change the workflow 'w' (id 1), which is enabled "
        "and runs by itself on every document that matches its triggers (document added) and "
        "will: move the matching documents to the trash."
    )
    assert setup.fake.writes == []
    done = await setup.workflows.update("w", WorkflowEdit(actions=(trash,)), confirm=True)
    assert [a.type for a in done.actions] == [6]
    assert done.note == TRASH_NOTE
    renamed = await setup.workflows.update("w", WorkflowEdit(name="w2"), confirm=False)
    assert renamed.note == TRASH_NOTE
    plain = await setup.workflows.update("w2", WorkflowEdit(actions=(RETITLE,)), confirm=False)
    assert plain.note is None
    assert all(w.note is None for w in (await setup.workflows.list()).workflows)


@pytest.mark.parametrize(
    ("triggers", "when"),
    [
        (
            [WorkflowTrigger(type=5)],
            (
                "which is enabled and runs only when a user runs it by hand (manual), possibly "
                "on documents its trigger filters do not match, and will: "
            ),
        ),
        (
            [WorkflowTrigger(type=2), WorkflowTrigger(type=5)],
            (
                "which is enabled and runs by itself on every document that matches its "
                "triggers (document added); when a user runs it by hand (manual) it may also "
                "run on documents its filters do not match; it will: "
            ),
        ),
    ],
    ids=["manual only", "manual and automatic"],
)
async def test_a_manual_trigger_is_described_as_run_by_hand_not_by_itself(
    triggers: list[WorkflowTrigger], when: str
) -> None:
    setup = Setup()
    _ = setup.seed("w", triggers=triggers)
    trash = ActionInput(type="move to trash")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.workflows.update("w", WorkflowEdit(actions=(trash,)), confirm=False)

    assert raised.value.message == (
        f"Nothing was changed. Confirming would change the workflow 'w' (id 1), {when}"
        "move the matching documents to the trash."
    )


async def test_new_triggers_for_an_enabled_acting_workflow_need_confirm_too() -> None:
    setup = Setup()
    _ = setup.seed("w", actions=[WorkflowAction(type=6)])

    with pytest.raises(ConfirmationRequired, match="move the matching documents to the trash"):
        _ = await setup.workflows.update(
            "w", WorkflowEdit(triggers=(TriggerInput(type="updated"),)), confirm=False
        )


async def test_switching_an_acting_workflow_on_needs_confirm_and_off_or_again_does_not() -> None:
    setup = Setup()
    _ = setup.seed("off", enabled=False, actions=[WorkflowAction(type=6)])
    _ = setup.seed("on", actions=[WorkflowAction(type=6)])

    with pytest.raises(ConfirmationRequired):
        _ = await setup.workflows.update("off", WorkflowEdit(enabled=True), confirm=False)
    off = await setup.workflows.update("on", WorkflowEdit(enabled=False), confirm=False)
    again = await setup.workflows.update("on", WorkflowEdit(enabled=False, order=2), confirm=False)
    still = await setup.workflows.update("off", WorkflowEdit(enabled=True), confirm=True)

    assert (off.enabled, again.enabled, still.enabled) == (False, False, True)


async def test_saying_enabled_again_on_a_workflow_that_runs_already_needs_no_confirmation() -> None:
    setup = Setup()
    _ = setup.seed("on", actions=[WorkflowAction(type=6)])

    done = await setup.workflows.update("on", WorkflowEdit(enabled=True, order=4), confirm=False)

    assert (done.enabled, done.order) == (True, 4)


async def test_changing_a_disabled_workflow_to_act_needs_no_confirmation() -> None:
    setup = Setup()
    _ = setup.seed("off", enabled=False)

    done = await setup.workflows.update(
        "off", WorkflowEdit(actions=(ActionInput(type="move to trash"),)), confirm=False
    )

    assert [a.type for a in done.actions] == [6]


async def test_replacing_actions_never_drops_one_this_server_cannot_write() -> None:
    setup = Setup()
    _ = setup.seed(
        "w",
        enabled=False,
        triggers=[WorkflowTrigger(id=1, type=2)],
        actions=[
            WorkflowAction(id=3, type=5, settings={"passwords": "x"}),
            WorkflowAction(id=4, type=8, settings={}),
            WorkflowAction(id=5, type=1, settings={"assign_title": "t"}),
        ],
    )

    with pytest.raises(Validation, match="holds actions this server cannot write") as raised:
        _ = await setup.workflows.update("w", WorkflowEdit(actions=(RETITLE,)), confirm=False)
    renamed = await setup.workflows.update("w", WorkflowEdit(name="v", enabled=True), confirm=True)

    assert "password removal (id 3)" in raised.value.message
    assert "AI" not in raised.value.message
    assert raised.value.hint is not None
    assert "web UI" in raised.value.hint
    assert setup.fake.writes == ["update"]
    assert [a.type for a in renamed.actions] == [5, 8, 1]  # untouched, in the order stored


async def test_a_hidden_header_keeps_the_value_stored_for_the_action_with_that_id() -> None:
    setup = Setup()
    _ = setup.seed(
        "w",
        enabled=False,
        actions=[
            WorkflowAction(
                id=7,
                type=4,
                settings={
                    "webhook": {
                        "url": "https://h.example/a",
                        "headers": {"X-Token": "real", "X-Old": "o"},
                    }
                },
            )
        ],
    )
    hook = WebhookSettings(url="https://h.example/b", headers={"X-Token": "(hidden)", "X-New": "n"})

    done = await setup.workflows.update(
        "w", WorkflowEdit(actions=(ActionInput(type=4, id=7, webhook=hook),)), confirm=True
    )

    assert headers(setup.stored("w").actions[0].settings) == {"X-Token": "real", "X-New": "n"}
    assert headers(done.actions[0].settings) == {"X-Token": "(hidden)", "X-New": "(hidden)"}


@pytest.mark.parametrize(
    ("action_id", "message"),
    [
        (None, "Header 'X-Token' is shown as '\\(hidden\\)'"),
        (99, "action 1 is a webhook with the id 99, which is not"),
        (8, "Header 'X-Token' is shown as '\\(hidden\\)'"),
    ],
)
async def test_a_hidden_header_without_a_stored_value_to_keep_is_refused(
    action_id: int | None, message: str
) -> None:
    setup = Setup()
    _ = setup.seed(
        "w",
        enabled=False,
        actions=[
            WorkflowAction(
                id=7, type=4, settings={"webhook": {"url": "u", "headers": {"X-Other": "o"}}}
            ),
            WorkflowAction(id=8, type=1, settings={}),
        ],
    )
    hook = WebhookSettings(url="https://h.example/b", headers={"X-Token": "(hidden)"})

    with pytest.raises(Validation, match=message):
        _ = await setup.workflows.update(
            "w",
            WorkflowEdit(actions=(ActionInput(type=4, id=action_id, webhook=hook),)),
            confirm=False,
        )

    assert setup.fake.writes == []


async def test_a_delete_without_confirm_changes_nothing_and_says_what_it_would_do() -> None:
    setup = Setup(writes=False)
    _ = setup.seed(
        "Filing",
        triggers=[WorkflowTrigger(type=2), WorkflowTrigger(type=3)],
        actions=[WorkflowAction(type=6)],
    )

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.workflows.delete("Filing", confirm=False)

    assert raised.value.message == (
        "Nothing was changed. Confirming would delete the workflow 'Filing' (id 1) for good: it "
        "stops running at once and its 2 triggers and 1 action are lost; no document is touched."
    )
    assert raised.value.hint == CONFIRM_HINT
    assert [w.name for w in setup.state.workflows] == ["Filing"]


async def test_a_confirmed_delete_removes_the_workflow() -> None:
    setup = Setup()
    _ = setup.seed("Filing")

    gone = await setup.workflows.delete(1, confirm=True)

    assert (gone.id, gone.name) == (1, "Filing")
    assert gone.message == "The workflow 'Filing' (id 1) was deleted; no document was touched."
    assert setup.state.workflows == []


async def test_a_delete_of_a_missing_workflow_is_not_found_and_without_permission_denied() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().workflows.delete("nope", confirm=True)
    setup = Setup(READER[0], grant=("view_workflow",))
    _ = setup.seed("w")

    with pytest.raises(PermissionDenied):
        _ = await setup.workflows.delete("w", confirm=True)


async def test_new_triggers_and_actions_are_what_the_refusal_names() -> None:
    setup = Setup()
    _ = setup.seed("w", triggers=[WorkflowTrigger(type=3)])

    with pytest.raises(
        ConfirmationRequired, match=r"\(consumption started, filtered by filename\)"
    ):
        _ = await setup.workflows.update(
            "w",
            WorkflowEdit(triggers=(NEVER,), actions=(ActionInput(type="move to trash"),)),
            confirm=False,
        )


@pytest.mark.parametrize("algorithm", [None, "none", 0])
async def test_a_match_without_a_matching_algorithm_is_refused_on_create_and_update(
    algorithm: int | str | None,
) -> None:
    setup = Setup()
    _ = setup.seed(
        "w", triggers=[WorkflowTrigger(type=2, settings={"match": "x", "matching_algorithm": 1})]
    )
    trigger = TriggerInput(type="added", match="invoice", matching_algorithm=algorithm)
    message = "A match needs a matching_algorithm other than none to restrict anything."

    with pytest.raises(Validation, match=message):
        _ = await setup.workflows.create(
            WorkflowCreate(name="n", triggers=(trigger,)), confirm=True
        )
    with pytest.raises(Validation, match=message):
        _ = await setup.workflows.update("w", WorkflowEdit(triggers=(trigger,)), confirm=True)

    assert setup.fake.writes == []


async def test_a_match_with_an_algorithm_or_an_empty_match_is_accepted() -> None:
    setup = Setup()

    made = await setup.workflows.create(
        WorkflowCreate(
            name="n",
            triggers=(
                TriggerInput(type="added", match="a", matching_algorithm="any"),
                TriggerInput(type="added", match="", matching_algorithm="none"),
                TriggerInput(type="added", matching_algorithm="none"),
            ),
        ),
        confirm=False,
    )

    assert len(made.triggers) == 3


@pytest.mark.parametrize(
    ("kind", "acts"),
    [
        (
            5,
            "unlock each matching document with stored passwords and add the result",
        ),
        (8, "apply AI suggestions to no field (Paperless skips it)"),
        (77, "run an action of type 77"),
    ],
)
async def test_switching_on_a_workflow_with_an_action_this_server_cannot_write_needs_confirm(
    kind: int, acts: str
) -> None:
    setup = Setup()
    _ = setup.seed(
        "w",
        enabled=False,
        triggers=[WorkflowTrigger(id=1, type=2)],
        actions=[WorkflowAction(type=kind)],
    )

    with pytest.raises(ConfirmationRequired, match=re.escape(acts)):
        _ = await setup.workflows.update("w", WorkflowEdit(enabled=True), confirm=False)
    done = await setup.workflows.update("w", WorkflowEdit(enabled=True), confirm=True)

    assert done.enabled is True


async def test_a_stored_header_is_not_kept_for_another_host() -> None:
    setup = Setup()
    _ = setup.seed(
        "w",
        enabled=False,
        actions=[
            WorkflowAction(
                id=7,
                type=4,
                settings={
                    "webhook": {
                        "url": "https://hooks.example:8443/a",
                        "headers": {"X-Token": "real"},
                    }
                },
            )
        ],
    )

    def edit(url: str) -> WorkflowEdit:
        hook = WebhookSettings(url=url, headers={"X-Token": "(hidden)"})
        return WorkflowEdit(actions=(ActionInput(type=4, id=7, webhook=hook),))

    for moved in (
        "https://evil.example:8443/a",
        "http://hooks.example:8443/a",
        "https://hooks.example/a",
    ):
        with pytest.raises(Validation, match="points to another scheme, host or port"):
            _ = await setup.workflows.update("w", edit(moved), confirm=False)
    done = await setup.workflows.update(
        "w", edit("https://HOOKS.example:8443/other?x=1"), confirm=False
    )

    assert setup.fake.writes == ["update"]
    assert [a.type for a in done.actions] == [4]


async def test_custom_field_values_add_their_fields_and_headers_left_out_are_removed() -> None:
    setup = Setup()

    made = await setup.workflows.create(
        WorkflowCreate(
            name="n",
            actions=(ActionInput(type=1, assign_custom_fields_values={"e2e-string": "v", "5": 1}),),
        ),
        confirm=False,
    )

    assert made.actions[0].settings["assign_custom_fields"] == ["e2e-string", "e2e-integer"]
    sent = setup.fake.values[0].actions
    assert sent is not None
    assert sent[0].settings["assign_custom_fields"] == [1, 5]


async def test_an_unknown_source_number_is_shown_as_the_number() -> None:
    setup = Setup()
    _ = setup.seed("w", triggers=[WorkflowTrigger(type=1, settings={"sources": [1, 9]})])

    [workflow] = (await setup.workflows.list()).workflows

    assert workflow.triggers[0].settings == {"sources": ["consume_folder", 9]}


@pytest.mark.decision("decision-82")
@pytest.mark.parametrize("name", ["filename", "path"])
async def test_a_blank_filter_is_refused_when_a_workflow_is_created_or_changed(name: str) -> None:
    setup = Setup()
    _ = setup.seed("w", enabled=False)
    blank = TriggerInput.model_validate({"type": "consumption", name: "  "})

    with pytest.raises(Validation, match=f"'{name}' is blank"):
        _ = await setup.workflows.create(WorkflowCreate(name="n", triggers=(blank,)), confirm=True)
    with pytest.raises(Validation, match=f"'{name}' is blank"):
        _ = await setup.workflows.update("w", WorkflowEdit(triggers=(blank,)), confirm=True)
    assert setup.fake.writes == []
