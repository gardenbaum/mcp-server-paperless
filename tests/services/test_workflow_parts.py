"""WorkflowParts: one trigger or action added, changed or removed, the others untouched."""

import json
from dataclasses import replace
from typing import TYPE_CHECKING, cast, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeState, seeded
from fakes.workflows import ACTION_DEFAULTS, TRIGGER_DEFAULTS
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    Validation,
)
from mcp_server_paperless.domain.workflows import (
    ActionInput,
    EmailSettings,
    PartEdit,
    PartWrite,
    TriggerInput,
    WebhookSettings,
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.workflow_parts import WorkflowParts
from mcp_server_paperless.services.workflows import HIDDEN, WorkflowService

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.workflows import PartKind, WorkflowInfo

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-21"),
    pytest.mark.decision("decision-82"),
]

URL = "https://user:planted@h.example/x"
HOOK: dict[str, JsonValue] = {
    "url": URL,
    "use_params": True,
    "as_json": False,
    "params": None,
    "body": None,
    "headers": {"X-Token": "planted"},
    "include_document": False,
}
MAIL: dict[str, JsonValue] = {
    "subject": "s",
    "body": "b",
    "to": "a@example.org",
    "include_document": False,
}
NEVER = TriggerInput(type="consumption", filename="never-*.pdf")
RETITLE = ActionInput(type="assignment", assign_title="new")
TRASH = ActionInput(type="move to trash")


def trigger(number: int, kind: int = 2, **settings: JsonValue) -> WorkflowTrigger:
    return WorkflowTrigger(id=number, type=kind, settings={**TRIGGER_DEFAULTS, **settings})


def action(number: int, kind: int = 1, **settings: JsonValue) -> WorkflowAction:
    return WorkflowAction(id=number, type=kind, settings={**ACTION_DEFAULTS, **settings})


START = trigger(1, 1, filter_filename="never-*.none")
BY_HAND = trigger(4, 5, filter_filename="x")
TITLE = action(11, assign_title="t", assign_tags=[1])
REMOVES = action(12, 2, remove_tags=[1])
SENDS = action(13, 3, email=MAIL)
CALLS = action(14, 4, webhook=HOOK)
UNLOCKS = action(15, 5, passwords=["planted"])
BINS = action(16, 6)
READS = action(17, 7)
SUGGESTS = action(18, 8, ai_suggestion_fields=["title"])
ALL = (TITLE, REMOVES, SENDS, CALLS, UNLOCKS, BINS, READS, SUGGESTS)
TRIGGERS = (START, trigger(2, filter_has_tags=[1], match="m", matching_algorithm=1), BY_HAND)


class Recording(FakePaperless):
    """Remembers what was sent; `before`, `after` and `fails` act around the write."""

    sent: list[tuple[PartKind, tuple[PartWrite, ...]]]
    before: Callable[[], None] | None = None
    after: Callable[[], None] | None = None
    fails: PaperlessMcpError | None = None
    unwritten: bool = False
    unread: PaperlessMcpError | None = None

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.sent = []

    @override
    async def update_workflow_parts(
        self, workflow_id: int, kind: PartKind, parts: Sequence[PartWrite]
    ) -> None:
        self.sent.append((kind, tuple(parts)))
        if self.before is not None:
            self.before()
        if not self.unwritten:
            await super().update_workflow_parts(workflow_id, kind, parts)
        if self.after is not None:
            self.after()
        if self.fails is not None:
            raise self.fails

    @override
    async def get_workflow(self, workflow_id: int) -> Workflow:
        if self.unread is not None:
            raise self.unread
        return await super().get_workflow(workflow_id)


@final
class Setup:
    def __init__(
        self, user: str = ADMIN[0], *, writes: bool = True, grant: Sequence[str] = ()
    ) -> None:
        self.state = seeded()
        self.state.workflow_object_id[0] = 50
        self.state.users = [
            replace(u, permissions=u.permissions | frozenset(grant)) if u.username == user else u
            for u in self.state.users
        ]
        self.fake = Recording(self.state, user=user, writes=writes)
        resolver = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        self.workflows = WorkflowService(self.fake, self.fake, resolver)
        self.parts = WorkflowParts(self.fake, self.fake, self.workflows)

    def seed(
        self,
        triggers: Sequence[WorkflowTrigger] = TRIGGERS,
        actions: Sequence[WorkflowAction] = ALL,
        *,
        enabled: bool = False,
        name: str = "W",
    ) -> Workflow:
        made = Workflow(
            id=len(self.state.workflows) + 1,
            name=name,
            order=0,
            enabled=enabled,
            triggers=tuple(triggers),
            actions=tuple(actions),
        )
        self.state.workflows.append(made)
        return made

    @property
    def now(self) -> Workflow:
        return self.state.workflows[0]

    async def do(self, *, confirm: object = False, **edit: object) -> WorkflowInfo:
        return await self.parts.update("W", PartEdit.model_validate(edit), confirm=confirm is True)

    async def asked(self, **edit: object) -> str:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await self.do(**edit)
        assert self.fake.sent == []
        assert "planted" not in raised.value.message
        return raised.value.message


def ids(actions: Sequence[WorkflowAction | WorkflowTrigger]) -> list[int | None]:
    return [part.id for part in actions]


def named(parts: Sequence[PartWrite]) -> list[PartWrite]:
    """What is sent of actions that stay: nothing but their ids."""
    return [PartWrite(id=part.id) for part in parts]


async def test_an_action_is_added_last_and_the_others_travel_by_id_only() -> None:
    setup = Setup()
    before = setup.seed()

    info = await setup.do(operation="add", action=RETITLE)

    [(kind, sent)] = setup.fake.sent
    assert kind == "actions"
    assert list(sent[:-1]) == [PartWrite(id=a.id) for a in ALL]
    assert sent[-1] == PartWrite(type=1, settings={"assign_title": "new"})
    assert setup.now.actions[:-1] == before.actions
    assert setup.now.actions[-1].settings["assign_title"] == "new"
    assert setup.now.triggers == before.triggers
    assert info.actions[-1].settings == {"assign_title": "new"}
    assert "planted" not in info.model_dump_json()


@pytest.mark.parametrize(("position", "place"), [(1, 0), (4, 3), (9, 8)])
async def test_an_action_is_added_at_a_position(position: int, place: int) -> None:
    setup = Setup()
    before = setup.seed()

    _ = await setup.do(operation="add", action=RETITLE, position=position)

    actions = list(setup.now.actions)
    assert actions.pop(place).settings["assign_title"] == "new"
    assert tuple(actions) == before.actions


@pytest.mark.parametrize("position", [0, -1, 10])
async def test_a_position_that_is_not_there_is_refused(position: int) -> None:
    setup = Setup()
    _ = setup.seed()

    with pytest.raises(Validation, match=f"no position {position}: the workflow has 9 places"):
        _ = await setup.do(operation="add", action=RETITLE, position=position)

    assert setup.fake.sent == []


async def test_a_trigger_is_added_and_the_others_travel_in_full() -> None:
    setup = Setup()
    before = setup.seed()

    _ = await setup.do(
        operation="add", trigger=TriggerInput(type="updated", has_tags=["e2e-inbox"])
    )

    [(kind, sent)] = setup.fake.sent
    assert kind == "triggers"
    assert list(sent) == [
        *(PartWrite(id=t.id, type=t.type, settings=t.settings) for t in TRIGGERS),
        PartWrite(type=3, settings={"filter_has_tags": [4]}),
    ]
    assert setup.now.triggers[:-1] == before.triggers
    assert setup.now.actions == before.actions


async def test_the_named_settings_of_a_trigger_change_and_the_rest_stays() -> None:
    setup = Setup()
    _ = setup.seed()

    _ = await setup.do(
        operation="change",
        trigger_id=2,
        trigger=TriggerInput(type="added", match="other"),
        clear=("has_tags", "is_insensitive"),
    )

    changed = setup.now.triggers[1]
    assert changed.settings == {
        **TRIGGERS[1].settings,
        "match": "other",
        "filter_has_tags": [],
        "is_insensitive": True,
    }
    assert (setup.now.triggers[0], setup.now.triggers[2]) == (TRIGGERS[0], TRIGGERS[2])


async def test_every_setting_of_a_trigger_can_be_cleared_to_its_default() -> None:
    changed: dict[str, JsonValue] = {
        "sources": [2],
        "filter_mailrule": 5,
        "matching_algorithm": 3,
        "match": "m",
        "is_insensitive": False,
        "filter_has_correspondent": 1,
        "filter_custom_field_query": '["e2e-url","exists",true]',
        "schedule_offset_days": 5,
        "schedule_is_recurring": True,
        "schedule_recurring_interval_days": 3,
        "schedule_date_field": "created",
        "schedule_date_custom_field": 3,
    }
    names = (
        *("sources", "mailrule", "matching_algorithm", "match", "is_insensitive"),
        *("has_correspondent", "custom_field_query", "schedule_offset_days"),
        *("schedule_is_recurring", "schedule_recurring_interval_days"),
        *("schedule_date_field", "schedule_date_custom_field"),
    )
    setup = Setup()
    _ = setup.seed([trigger(2, 4, **changed)], [])

    _ = await setup.do(operation="change", trigger_id=2, clear=names)

    assert setup.now.triggers[0].settings == TRIGGER_DEFAULTS


async def test_only_the_named_settings_of_an_action_are_sent() -> None:
    setup = Setup()
    before = setup.seed()

    _ = await setup.do(
        operation="change",
        action_id=11,
        action=ActionInput(type="assignment", assign_correspondent="e2e-acme"),
        clear=("assign_tags",),
    )

    [(_, sent)] = setup.fake.sent
    assert sent[0] == PartWrite(id=11, settings={"assign_correspondent": 1, "assign_tags": []})
    assert list(sent[1:]) == named(sent[1:])
    assert setup.now.actions[0].settings == {
        **TITLE.settings,
        "assign_correspondent": 1,
        "assign_tags": [],
    }
    assert setup.now.actions[1:] == before.actions[1:]


async def test_flags_texts_and_values_of_actions_can_be_cleared() -> None:
    setup = Setup()
    held = action(11, assign_title="t", assign_owner=2, assign_custom_fields_values={"1": "x"})
    _ = setup.seed(
        actions=[held, action(12, 2, remove_all_owners=True, remove_tags=[1], remove_all_tags=True)]
    )
    values = ("assign_title", "assign_owner", "assign_custom_fields_values")

    _ = await setup.do(operation="change", action_id=11, clear=values)
    _ = await setup.do(operation="change", action_id=12, clear=("remove_all_owners", "remove_tags"))

    assert setup.now.actions[0].settings == ACTION_DEFAULTS
    assert setup.now.actions[1].settings == {**ACTION_DEFAULTS, "remove_all_tags": True}


async def test_values_of_custom_fields_join_the_fields_the_action_assigns() -> None:
    setup = Setup()
    _ = setup.seed(actions=[action(11, assign_custom_fields=[2])])
    change = ActionInput(type=1, assign_custom_fields_values={"e2e-string": "x"})

    _ = await setup.do(operation="change", action_id=11, action=change)

    [(_, [sent])] = setup.fake.sent
    assert sent.settings == {
        "assign_custom_fields_values": {"1": "x"},
        "assign_custom_fields": [2, 1],
    }
    both = change.model_copy(update={"assign_custom_fields": [3]})
    _ = await setup.do(operation="change", action_id=11, action=both)
    assert setup.now.actions[0].settings["assign_custom_fields"] == [3, 1]


async def test_an_action_is_moved_also_one_this_server_cannot_write() -> None:
    setup = Setup()
    _ = setup.seed()

    _ = await setup.do(operation="change", action_id=15, position=1)
    _ = await setup.do(operation="change", action_id=11, position=8)
    _ = await setup.do(
        operation="change",
        action_id=12,
        position=2,
        action=ActionInput(type="removal", remove_all_tags=True),
    )

    assert ids(setup.now.actions) == [15, 12, 13, 14, 16, 17, 18, 11]
    assert all(list(sent) == named(sent) for _, sent in setup.fake.sent[:2])
    assert sorted(setup.now.actions, key=lambda a: a.id or 0)[2:] == list(ALL[2:])


@pytest.mark.parametrize("number", [1, 2, 4])
async def test_a_trigger_is_removed(number: int) -> None:
    setup = Setup()
    _ = setup.seed(actions=[TITLE])

    _ = await setup.do(operation="remove", trigger_id=number)

    assert setup.now.triggers == tuple(t for t in TRIGGERS if t.id != number)
    assert setup.now.actions == (TITLE,)


@pytest.mark.parametrize("gone", [a for a in ALL if a.id not in {14, 15}], ids=str)
async def test_an_action_without_hidden_values_is_removed_from_a_disabled_workflow(
    gone: WorkflowAction,
) -> None:
    setup = Setup()
    _ = setup.seed()

    _ = await setup.do(operation="remove", action_id=gone.id)

    assert setup.now.actions == tuple(a for a in ALL if a is not gone)


async def test_the_last_trigger_and_the_last_action_can_go_and_that_is_said() -> None:
    setup = Setup()
    _ = setup.seed([BY_HAND], [TITLE])

    first = await setup.do(operation="remove", trigger_id=4)
    second = await setup.do(operation="remove", action_id=11)

    assert first.note == "It has no trigger now: it does nothing."
    assert second.note == "It has no action now: it does nothing."
    assert (setup.now.triggers, setup.now.actions) == ((), ())


async def test_a_trigger_and_an_action_with_the_same_id_are_told_apart() -> None:
    setup = Setup()
    _ = setup.seed([trigger(7)], [action(7, assign_title="t")])

    _ = await setup.do(operation="remove", trigger_id=7)
    assert (ids(setup.now.triggers), ids(setup.now.actions)) == ([], [7])
    _ = await setup.do(operation="remove", action_id=7)
    assert setup.now.actions == ()


ADD, CHANGE, REMOVE = {"operation": "add"}, {"operation": "change"}, {"operation": "remove"}


@pytest.mark.parametrize(
    "edit",
    [
        ADD,
        {**ADD, "trigger": NEVER, "action": RETITLE},
        {**ADD, "action": RETITLE, "action_id": 11},
        {**ADD, "trigger": NEVER, "trigger_id": 1},
        {**ADD, "action": RETITLE, "clear": ("assign_tags",)},
        {**ADD, "trigger": NEVER, "position": 1},
        {**ADD, "action": RETITLE.model_copy(update={"id": 11})},
        CHANGE,
        {**CHANGE, "trigger_id": 1, "action_id": 11, "clear": ("match",)},
        {**CHANGE, "action_id": 11},
        {**CHANGE, "trigger_id": 1},
        {**CHANGE, "action_id": 11, "trigger": NEVER},
        {**CHANGE, "trigger_id": 1, "action": RETITLE},
        {**CHANGE, "trigger_id": 1, "position": 1},
        {**CHANGE, "trigger_id": 1, "trigger": NEVER, "position": 1},
        {**CHANGE, "action_id": 11, "action": RETITLE.model_copy(update={"id": 12})},
        {**CHANGE, "action_id": 11, "action": ActionInput(type="assignment")},
        {**CHANGE, "action_id": 11, "clear": ("remove_tags",)},
        {**CHANGE, "action_id": 11, "clear": ("assign_title",), "action": RETITLE},
        {**CHANGE, "action_id": 13, "clear": ("email",)},
        {**CHANGE, "action_id": 14, "clear": ("webhook",)},
        {**CHANGE, "action_id": 16, "clear": ("assign_title",)},
        {**CHANGE, "trigger_id": 2, "clear": ("filter_has_tags",)},
        {
            **CHANGE,
            "trigger_id": 2,
            "clear": ("match",),
            "trigger": TriggerInput(type=2, match="x"),
        },
        REMOVE,
        {**REMOVE, "trigger_id": 1, "action_id": 11},
        {**REMOVE, "action_id": 11, "action": RETITLE},
        {**REMOVE, "action_id": 11, "position": 1},
        {**REMOVE, "action_id": 11, "clear": ("assign_title",)},
        {**CHANGE, "action_id": 11, "action": TRASH},
        {**CHANGE, "trigger_id": 2, "trigger": NEVER},
        {**CHANGE, "action_id": 15, "clear": ("assign_title",)},
        {**CHANGE, "action_id": 18, "action": RETITLE},
        {**CHANGE, "trigger_id": 4, "clear": ("filename",)},
        {**ADD, "action": ActionInput(type="password removal")},
        {**ADD, "action": ActionInput(type="apply AI suggestions")},
        {**ADD, "trigger": TriggerInput(type="manual")},
        {**ADD, "trigger": TriggerInput(type="consumption")},
        {**CHANGE, "trigger_id": 1, "clear": ("filename",)},
        {**CHANGE, "trigger_id": 2, "trigger": TriggerInput(type=2, matching_algorithm="none")},
        {**CHANGE, "action_id": 13, "action": ActionInput(type="email")},
    ],
)
async def test_what_does_not_belong_together_is_refused_and_nothing_is_sent(
    edit: dict[str, object],
) -> None:
    setup = Setup(writes=False)
    before = setup.seed(enabled=True)

    with pytest.raises(Validation) as raised:
        _ = await setup.do(confirm=True, **edit)

    assert not isinstance(raised.value, ConfirmationRequired)
    assert raised.value.hint
    assert setup.fake.sent == []
    assert setup.now == before


async def test_the_matching_of_a_trigger_is_judged_with_what_it_stores() -> None:
    setup = Setup()
    _ = setup.seed([trigger(2)], [])

    with pytest.raises(Validation, match="needs a matching_algorithm"):
        _ = await setup.do(**CHANGE, trigger_id=2, trigger=TriggerInput(type=2, match="x"))
    algorithm = TriggerInput(type=2, matching_algorithm="any")
    _ = await setup.do(**CHANGE, trigger_id=2, trigger=algorithm)
    _ = await setup.do(**CHANGE, trigger_id=2, trigger=TriggerInput(type=2, match="x"))
    with pytest.raises(Validation, match="needs a matching_algorithm"):
        _ = await setup.do(
            **CHANGE,
            trigger_id=2,
            trigger=TriggerInput(type=2, match="y"),
            clear=("matching_algorithm",),
        )


@pytest.mark.parametrize("edit", [{"trigger_id": 99}, {"action_id": 99}, {"action_id": 1}])
async def test_a_part_of_another_workflow_or_none_is_refused(edit: dict[str, object]) -> None:
    setup = Setup(writes=False)
    _ = setup.seed()
    _ = setup.seed([trigger(99)], [action(99)], name="Other")

    for operation in ("change", "remove"):
        clear = ("match",) if operation == "change" else ()
        with pytest.raises(NotFound, match="holds no"):
            _ = await setup.do(operation=operation, clear=clear, confirm=True, **edit)

    assert setup.fake.sent == []


async def test_a_part_several_workflows_hold_is_not_changed_but_can_be_removed() -> None:
    setup = Setup()
    _ = setup.seed([trigger(2)], [CALLS])
    other = setup.seed([trigger(2)], [CALLS], name="Other", enabled=True)

    for edit in (
        {"action_id": 14, "action": ActionInput(type=4, webhook=WebhookSettings(url="http://a"))},
        {"trigger_id": 2, "clear": ("match",)},
    ):
        with pytest.raises(Validation, match=r"other workflows hold .*: 'Other' \(id 2\)"):
            _ = await setup.do(operation="change", confirm=True, **edit)
    # The other workflow keeps the hidden values, so none are lost.
    _ = await setup.do(operation="remove", action_id=14)
    _ = await setup.do(operation="remove", trigger_id=2)

    assert (setup.now.triggers, setup.now.actions) == ((), ())
    assert setup.state.workflows[1] == other


async def test_actions_that_have_an_order_in_another_workflow_are_not_rewritten() -> None:
    setup = Setup()
    before = setup.seed([BY_HAND], [TITLE, REMOVES])
    other = setup.seed([BY_HAND], [REMOVES, BINS], name="Other")

    for edit in (
        {"operation": "add", "action": RETITLE},
        {"operation": "change", "action_id": 11, "action": RETITLE},
        {"operation": "change", "action_id": 11, "position": 2},
        {"operation": "remove", "action_id": 11},
    ):
        with pytest.raises(Validation, match="could change their order there"):
            _ = await setup.do(**edit)
    assert (setup.state.workflows, setup.fake.sent) == ([before, other], [])
    _ = await setup.do(operation="remove", action_id=12)
    assert ids(setup.now.actions) == [11]


@pytest.mark.parametrize(
    ("triggers", "actions", "edit", "said"),
    [
        ([START, BY_HAND], [READS], {**REMOVE, "trigger_id": 1}, "remote OCR needs"),
        ([BY_HAND], [], {**ADD, "action": ActionInput(type="remote OCR")}, "remote OCR needs"),
        ([START, BY_HAND], [SUGGESTS], {**REMOVE, "trigger_id": 4}, "AI suggestions needs"),
    ],
    ids=["ocr-trigger", "ocr-action", "ai-trigger"],
)
async def test_the_rules_on_triggers_and_actions_together_are_checked_here(
    triggers: list[WorkflowTrigger],
    actions: list[WorkflowAction],
    edit: dict[str, object],
    said: str,
) -> None:
    setup = Setup(writes=False)
    _ = setup.seed(triggers, actions)

    with pytest.raises(Validation, match=said):
        _ = await setup.do(**edit)


async def test_a_workflow_that_already_breaks_a_rule_can_still_be_repaired() -> None:
    setup = Setup()
    _ = setup.seed([BY_HAND], [READS, TITLE])

    _ = await setup.do(operation="remove", action_id=11)
    _ = await setup.do(operation="remove", action_id=17)

    assert setup.now.actions == ()


# --- confirmations


@pytest.mark.parametrize(
    ("edit", "lead"),
    [
        ({**ADD, "action": RETITLE}, "add an assignment action to"),
        ({**ADD, "action": TRASH}, "add a move to trash action to"),
        ({**ADD, "trigger": NEVER}, "add a 'consumption started' trigger to"),
        ({**CHANGE, "action_id": 11, "action": RETITLE}, "change the assignment action 11 of"),
        ({**CHANGE, "action_id": 11, "position": 2}, "move the assignment action 11 of"),
        (
            {**CHANGE, "trigger_id": 2, "clear": ("match",)},
            "change the 'document added' trigger 2 of",
        ),
        ({**REMOVE, "action_id": 11}, "remove the assignment action 11 from"),
        ({**REMOVE, "trigger_id": 2}, "remove the 'document added' trigger 2 from"),
    ],
)
async def test_every_operation_on_an_enabled_workflow_that_acts_is_confirmed(
    edit: dict[str, object], lead: str
) -> None:
    setup = Setup(writes=False)
    before = setup.seed([trigger(2)], [TITLE, SENDS], enabled=True)

    message = await setup.asked(**edit)

    assert f"Confirming would {lead} the workflow 'W' (id 1), which is enabled" in message
    assert "send an e-mail to 'a@example.org'" in message
    assert setup.now == before
    allowed = Setup()
    _ = allowed.seed([trigger(2)], [TITLE, SENDS], enabled=True)
    _ = await allowed.do(confirm=True, **edit)
    assert len(allowed.fake.sent) == 1


@pytest.mark.parametrize("kept", [UNLOCKS, SUGGESTS, action(19, 99)], ids=["5", "8", "99"])
async def test_an_action_that_cannot_be_written_counts_as_acting(kept: WorkflowAction) -> None:
    setup = Setup(writes=False)
    _ = setup.seed([trigger(2)], [kept, TITLE], enabled=True)

    assert "which is enabled" in await setup.asked(operation="remove", action_id=11)


async def test_plain_filing_and_removing_the_only_acting_action_need_no_yes() -> None:
    setup = Setup()
    _ = setup.seed([trigger(2)], [TITLE, SENDS], enabled=True)

    _ = await setup.do(operation="remove", action_id=13)
    _ = await setup.do(operation="add", action=RETITLE)
    _ = await setup.do(operation="add", trigger=NEVER)

    assert len(setup.fake.sent) == 3


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    ("gone", "lost"),
    [
        (CALLS, "of the webhook action 14 the value of 'X-Token', the credentials of its URL"),
        (UNLOCKS, "of the password removal action 15 its passwords"),
        (
            action(
                14,
                4,
                webhook={
                    **HOOK,
                    "url": "https://h.example/",
                    "headers": {"A": "planted", "B": "c"},
                },
            ),
            "of the webhook action 14 the values of 'A', 'B'",
        ),
        (
            action(14, 4, webhook={**HOOK, "url": "https://planted/x:y@h", "headers": None}),
            "of the webhook action 14 any credentials of its URL",
        ),
    ],
    ids=["webhook", "passwords", "headers", "unclear"],
)
async def test_removing_hidden_values_is_confirmed_also_while_disabled(
    gone: WorkflowAction, lost: str, *, enabled: bool
) -> None:
    setup = Setup(writes=False)
    _ = setup.seed([trigger(2)], [TITLE, gone], enabled=enabled)

    message = await setup.asked(operation="remove", action_id=gone.id)

    label = lost.removeprefix("of ").split(" action")[0]
    assert f"remove {label} action {gone.id} from the workflow 'W' (id 1)" in message
    assert f"and lose for good what is stored hidden: {lost}" in message


async def test_a_changed_webhook_keeps_what_is_written_back_as_shown() -> None:
    setup = Setup()
    _ = setup.seed([trigger(2)], [CALLS])
    hook = WebhookSettings(url="https://h.example/x", headers={"X-Token": HIDDEN}, as_json=True)

    info = await setup.do(**CHANGE, action_id=14, action=ActionInput(type=4, webhook=hook))

    stored = cast("dict[str, JsonValue]", setup.now.actions[0].settings["webhook"])
    assert stored == {**HOOK, "as_json": True}
    assert info.note == "Kept as stored, with any credentials: the webhook URL of action 1."
    assert "planted" not in info.model_dump_json()


async def test_a_changed_webhook_that_loses_hidden_values_is_confirmed_while_disabled() -> None:
    setup = Setup()
    _ = setup.seed([trigger(2)], [TITLE, CALLS])
    hook = WebhookSettings(url="https://@h.example/x")
    edit = {**CHANGE, "action_id": 14, "action": ActionInput(type=4, webhook=hook)}

    message = await setup.asked(**edit)
    info = await setup.do(confirm=True, **edit)

    assert "the value of 'X-Token', the credentials of its URL" in message
    assert info.note == "Removed the credentials of: the webhook URL of action 2."


async def test_a_changed_webhook_of_an_enabled_workflow_says_what_becomes_of_credentials() -> None:
    setup = Setup(writes=False)
    _ = setup.seed([trigger(2)], [TITLE, CALLS], enabled=True)
    hook = WebhookSettings(url="https://new:planted@h.example/x", headers={"X-Token": HIDDEN})

    message = await setup.asked(
        **CHANGE, action_id=14, position=2, action=ActionInput(type=4, webhook=hook)
    )

    assert "with credentials (not shown), and replace the credentials of the stored URL" in message
    assert "lose for good" not in message


async def test_a_new_webhook_is_no_copy_of_a_stored_one() -> None:
    setup = Setup()
    _ = setup.seed([trigger(2)], [CALLS])
    shown = ActionInput(type=4, webhook=WebhookSettings(url="https://h.example/x"))

    with pytest.raises(Validation, match="webhook URL of action 2 is a stored one"):
        _ = await setup.do(operation="add", action=shown)

    other = Setup()
    _ = other.seed([trigger(2)], [TITLE])
    _ = other.seed([], [CALLS], name="Other")
    info = await other.do(operation="add", action=shown, position=1)
    assert "without the credentials that another workflow stores" in str(info.note)
    assert "action 1" in str(info.note)
    on = Setup(writes=False)
    _ = on.seed([trigger(2)], [TITLE], enabled=True)
    _ = on.seed([], [CALLS], name="Other")
    assert "without the credentials that another" in await on.asked(operation="add", action=shown)


async def test_a_changed_url_that_another_workflow_stores_with_credentials_is_no_copy() -> None:
    setup = Setup()
    plain = action(14, 4, webhook={**HOOK, "url": "https://o.example/", "headers": None})
    _ = setup.seed([trigger(2)], [plain])
    _ = setup.seed([], [action(20, 4, webhook=HOOK)], name="Other")
    shown = ActionInput(type=4, webhook=WebhookSettings(url="https://h.example/x"))

    info = await setup.do(**CHANGE, action_id=14, action=shown)

    assert "without the credentials that another workflow stores" in str(info.note)


async def test_a_new_header_value_loses_the_stored_one() -> None:
    setup = Setup(writes=False)
    _ = setup.seed([trigger(2)], [CALLS])
    hook = WebhookSettings(url="https://h.example/x", headers={"X-Token": "other"})

    message = await setup.asked(**CHANGE, action_id=14, action=ActionInput(type=4, webhook=hook))

    assert message.endswith("of the webhook action 14 the value of 'X-Token'.")


@pytest.mark.parametrize(
    ("edit", "said"),
    [
        ({"action_id": 13, "clear": ("email",)}, "'email' cannot be cleared"),
        ({"action_id": 14, "clear": ("webhook",)}, "'webhook' cannot be cleared"),
        ({"action_id": 15, "clear": ("assign_title",)}, "'password removal' is not written"),
        ({"trigger_id": 4, "clear": ("filename",)}, "'manual' is not written"),
    ],
)
async def test_the_refusal_of_a_change_says_why(edit: dict[str, object], said: str) -> None:
    setup = Setup(writes=False)
    _ = setup.seed()

    with pytest.raises(Validation, match=said):
        _ = await setup.do(**CHANGE, confirm=True, **edit)


async def test_a_stored_match_that_restricts_nothing_does_not_stop_another_change() -> None:
    setup = Setup()
    _ = setup.seed([trigger(2, match="old")], [])

    _ = await setup.do(**CHANGE, trigger_id=2, trigger=TriggerInput(type=2, has_tags=[1]))

    assert setup.now.triggers[0].settings["filter_has_tags"] == [1]


async def test_a_header_value_is_not_carried_to_another_host() -> None:
    setup = Setup(writes=False)
    _ = setup.seed([trigger(2)], [CALLS])
    hook = WebhookSettings(url="https://evil.example/x", headers={"X-Token": HIDDEN})

    with pytest.raises(Validation, match="cannot keep its stored value"):
        _ = await setup.do(**CHANGE, action_id=14, action=ActionInput(type=4, webhook=hook))


@pytest.mark.decision("decision-44")
async def test_an_acceptance_does_not_cover_a_workflow_that_changed_meanwhile() -> None:
    setup = Setup()
    _ = setup.seed([trigger(2)], [TITLE, SENDS], enabled=True)
    edit = {**REMOVE, "action_id": 11}
    scopes: list[str] = []
    for title in ("one", "two"):
        held = action(11, assign_title=title)
        setup.state.workflows[0] = setup.now.model_copy(update={"actions": (held, SENDS)})
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await setup.do(**edit)
        scopes.append(raised.value.fingerprint)

    assert scopes[0] != scopes[1]


# --- after the write


async def test_what_someone_else_changed_at_the_same_time_is_reported_by_setting_names() -> None:
    setup = Setup()
    _ = setup.seed([BY_HAND], [TITLE, REMOVES, CALLS])

    def meddle() -> None:
        changed = action(12, 2, remove_tags=[2], remove_all_owners=True)
        hook: dict[str, JsonValue] = {**HOOK, "headers": {"X-Token": "planted too"}}
        mine = setup.now.model_copy(
            update={"actions": (changed, action(14, 4, webhook=hook), action(30))}
        )
        setup.state.workflows[0] = mine.model_copy(update={"triggers": (trigger(4, 2),)})

    setup.fake.after = meddle
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await setup.do(operation="remove", action_id=11)

    assert raised.value.message == (
        "The change was written, but the workflow is not as planned: trigger 4 is of type 2; "
        "trigger 4 has another filter_filename; action 12 has another remove_tags, "
        "remove_all_owners; action 14 has another webhook.headers; action 30 was not planned. "
        "Someone else may have changed it at the same time."
    )
    assert "planted" not in raised.value.message
    assert "do not call again" in str(raised.value.hint)
    assert len(setup.fake.sent) == 1


async def test_a_vanished_action_that_paperless_made_anew_is_reported() -> None:
    setup = Setup()
    _ = setup.seed([BY_HAND], [TITLE, REMOVES])

    def vanish() -> None:
        setup.state.workflows[0] = setup.now.model_copy(update={"actions": (TITLE,)})

    setup.fake.before = vanish
    with pytest.raises(PaperlessMcpError, match="action 12 is of type 1; action 12 has another"):
        _ = await setup.do(operation="add", action=RETITLE)


@pytest.mark.parametrize(
    ("actions", "said"),
    [
        ((TITLE,), "the new action is missing"),
        ((REMOVES, TITLE, action(60, assign_title="new")), "the actions are in another order"),
        ((TITLE, REMOVES, action(60, assign_title="x")), "the new action has another assign_title"),
        ((TITLE, REMOVES, action(60, 6, assign_title="new")), "the new action is of type 6"),
        (
            (TITLE, REMOVES, action(60, assign_title="new"), action(61)),
            "action 61 was not planned",
        ),
        ((TITLE, action(60, assign_title="new")), "action 12 is missing"),
    ],
)
async def test_an_outcome_other_than_planned_is_an_error(
    actions: tuple[WorkflowAction, ...], said: str
) -> None:
    setup = Setup()
    _ = setup.seed([BY_HAND], [TITLE, REMOVES])

    def outcome() -> None:
        setup.state.workflows[0] = setup.now.model_copy(update={"actions": actions})

    setup.fake.after = outcome
    with pytest.raises(PaperlessMcpError, match=said):
        _ = await setup.do(operation="add", action=RETITLE)


async def test_texts_paperless_trims_and_lists_it_sorts_are_no_difference() -> None:
    setup = Setup()
    _ = setup.seed([BY_HAND], [TITLE])

    def stored() -> None:
        now = action(60, assign_title=None, assign_tags=[2, 1], assign_custom_fields_values={})
        setup.state.workflows[0] = setup.now.model_copy(update={"actions": (TITLE, now)})

    setup.fake.after = stored
    info = await setup.do(
        operation="add", action=ActionInput(type=1, assign_title=" ", assign_tags=[1, 2])
    )

    assert len(info.actions) == 2


LOST = UpstreamTimeout("Paperless did not answer in time.")


async def test_a_lost_answer_is_looked_up_once_and_never_sent_again() -> None:
    done = Setup()
    _ = done.seed([BY_HAND], [TITLE])
    done.fake.fails = LOST
    info = await done.do(operation="add", action=RETITLE)
    assert len(info.actions) == 2

    undone = Setup()
    _ = undone.seed([BY_HAND], [TITLE])
    undone.fake.fails, undone.fake.unwritten = LOST, True
    with pytest.raises(PaperlessMcpError, match="as it was, so it was not written yet") as raised:
        _ = await undone.do(operation="add", action=RETITLE)
    assert "do not call again" in str(raised.value.hint)

    other = Setup()
    _ = other.seed([BY_HAND], [TITLE])
    other.fake.fails = LOST
    other.fake.after = other.state.workflows.clear
    with pytest.raises(
        PaperlessMcpError, match=r"in time\.; whether it was is not known: No workf"
    ):
        _ = await other.do(operation="add", action=RETITLE)

    half = Setup()
    _ = half.seed([BY_HAND], [TITLE])
    half.fake.fails = LOST
    changed = half.now.model_copy(update={"actions": (BINS,)})

    def meddle() -> None:
        half.state.workflows[0] = changed

    half.fake.after = meddle
    with pytest.raises(PaperlessMcpError, match="not as planned: action 11 is missing"):
        _ = await half.do(operation="add", action=RETITLE)
    assert [len(s.fake.sent) for s in (done, undone, other, half)] == [1, 1, 1, 1]


async def test_a_change_that_cannot_be_checked_says_it_was_written() -> None:
    setup = Setup()
    _ = setup.seed([BY_HAND], [TITLE])
    setup.fake.unread = LOST

    with pytest.raises(PaperlessMcpError, match="was written; it could not be checked: Paperless"):
        _ = await setup.do(operation="add", action=RETITLE)


async def test_a_refusal_of_paperless_passes_and_is_not_looked_up() -> None:
    setup = Setup()
    _ = setup.seed([BY_HAND], [TITLE])
    setup.fake.unwritten, setup.fake.fails = True, Validation("Paperless rejected it.")

    with pytest.raises(Validation, match="Paperless rejected it"):
        _ = await setup.do(operation="add", action=RETITLE)


async def test_a_user_who_may_only_view_cannot_write_and_one_who_may_change_can() -> None:
    viewer = Setup(READER[0], grant=("view_workflow",))
    _ = viewer.seed([BY_HAND], [TITLE])
    with pytest.raises(PermissionDenied):
        _ = await viewer.do(operation="remove", action_id=11)
    nobody = Setup(READER[0])
    _ = nobody.seed([BY_HAND], [TITLE])
    with pytest.raises(PermissionDenied):
        _ = await nobody.do(operation="remove", action_id=11)
    changer = Setup(READER[0], grant=("view_workflow", "change_workflow"))
    # The tag 3 is hidden from this user: it is sent back as read, a new one has to be visible.
    _ = changer.seed([trigger(2, filter_has_tags=[3]), BY_HAND], [TITLE, CALLS])
    _ = await changer.do(operation="remove", trigger_id=4)
    assert changer.now.triggers == (trigger(2, filter_has_tags=[3]),)
    with pytest.raises(NotFound):
        _ = await changer.do(operation="add", trigger=TriggerInput(type=2, has_tags=[3]))


async def test_a_move_to_trash_says_the_way_back() -> None:
    setup = Setup()
    _ = setup.seed([BY_HAND], [TITLE])

    info = await setup.do(operation="add", action=TRASH)

    assert "restore_from_trash" in str(info.note)


# --- property

PARTS = st.lists(st.sampled_from(ALL), unique_by=lambda a: a.id, min_size=1, max_size=8)
NEW = st.sampled_from(
    [RETITLE, TRASH, ActionInput(type=3, email=EmailSettings.model_validate(MAIL))]
)


@given(
    actions=PARTS,
    new=NEW,
    operation=st.sampled_from(["add", "move", "remove"]),
    which=st.integers(0, 7),
    place=st.integers(0, 8),
    enabled=st.booleans(),
)
async def test_no_operation_touches_or_sends_another_action(  # noqa: PLR0913, PLR0917 -- one per choice
    actions: list[WorkflowAction],
    new: ActionInput,
    operation: str,
    which: int,
    place: int,
    enabled: bool,  # noqa: FBT001 -- hypothesis gives it by name
) -> None:
    setup = Setup()
    before = setup.seed(actions=actions, enabled=enabled)
    chosen = actions[which % len(actions)]
    edit: dict[str, object] = {"operation": "remove", "action_id": chosen.id}
    if operation == "add":
        edit = {"operation": "add", "action": new, "position": place % (len(actions) + 1) + 1}
    elif operation == "move":
        edit |= {"operation": "change", "position": place % len(actions) + 1}

    info = await setup.do(confirm=True, **edit)

    [(_, sent)] = setup.fake.sent
    assert "planted" not in json.dumps([part.model_dump(mode="json") for part in sent])
    assert "planted" not in info.model_dump_json()
    left = {a.id: a for a in setup.now.actions}
    for old in actions:
        if operation != "remove" or old is not chosen:
            assert left[old.id] == old
    assert len(left) == len(actions) + {"add": 1, "move": 0, "remove": -1}[operation]
    assert setup.now.triggers == before.triggers
