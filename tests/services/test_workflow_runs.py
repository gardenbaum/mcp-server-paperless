"""WorkflowRuns: a workflow run by hand on named documents, and nothing but what was confirmed.

The fakes of the "nothing may run" cases refuse writes: a write fails the test.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeSavedView, FakeState, FakeTag, seeded
from harness import workflow_runs
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    Ambiguous,
    AuthenticationFailed,
    ConfirmationRequired,
    Conflict,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    TaskFailed,
    UpstreamTimeout,
    UpstreamUnavailable,
    Validation,
    accepting,
)
from mcp_server_paperless.domain.selection import Selection
from mcp_server_paperless.domain.tasks import Unconfirmed
from mcp_server_paperless.domain.views import FilterRule
from mcp_server_paperless.domain.workflows import (
    ACTION_FIELDS,
    Workflow,
    WorkflowAction,
    WorkflowRun,
    WorkflowTrigger,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.bulk_operations import named
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.workflows import MAX_DESTINATIONS, TRASH_NOTE

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.documents import NameOrId, SignedIn
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from mcp_server_paperless.domain.workflows import WorkflowRunReport
    from mcp_server_paperless.services.workflow_runs import WorkflowRuns

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-69"),
    pytest.mark.decision("decision-71"),
    pytest.mark.decision("decision-75"),
]

DAY = date(2026, 1, 1)
FINANCE, INBOX, ADMIN_ID, READER_ID = 1, 4, 2, 3
SCANS = {"title_contains": "Scan"}
MANUAL = WorkflowTrigger(id=1, type=5)
TAGGING = WorkflowAction(id=2, type=1, settings={"assign_tags": [INBOX]})
NOT_AGAIN = (
    "What it does may be done already, e-mails and webhooks included: do not run it again on your "
    "own; ask the user what they want."
)
UNSEEN = (
    " Whether e-mails, webhooks, password removals and AI suggestions worked is not told: only the "
    "Paperless logs show it."
)


class World(FakePaperless):
    """A fake whose run can end in other ways, and that counts its runs."""

    def __init__(self, state: FakeState, *, user: str, writes: bool) -> None:
        super().__init__(state, user=user, allow_writes=writes)
        self.ends: PaperlessMcpError | Unconfirmed | None = None
        self.listed: list[str] = []
        self.titled: list[tuple[int, ...]] = []
        # Whether Paperless does not list the permissions of the signed-in user.
        self.unlisted: bool = False

    @override
    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        self.listed.append(kind)
        return await super().list_taxonomy(kind)

    @override
    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        self.titled.append(tuple(document_ids))
        return await super().get_document_titles(document_ids)

    @override
    async def get_signed_in(self) -> SignedIn | None:
        user = await super().get_signed_in()
        if user is None or not self.unlisted:
            return user
        return user.model_copy(update={"permissions": None})

    @override
    async def run_workflow(
        self, workflow_id: int, document_ids: Sequence[int], *, filter_documents: bool
    ) -> WorkflowRun:
        done = await super().run_workflow(
            workflow_id, document_ids, filter_documents=filter_documents
        )
        if isinstance(self.ends, PaperlessMcpError):
            raise self.ends
        return done.model_copy(update={"unconfirmed": self.ends}) if self.ends else done


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, writes: bool = True, scans: int = 2) -> None:
        self.state: FakeState = seeded()
        self.state.documents += [
            FakeDocument(
                id=100 + n, title=f"Scan {n}", owner=ADMIN_ID, created=DAY - timedelta(days=n)
            )
            for n in range(1, scans + 1)
        ]
        self.flow()
        self.fake: World = World(self.state, user=user, writes=writes)
        self.runs: WorkflowRuns = workflow_runs(self.fake)

    def flow(
        self,
        *actions: WorkflowAction,
        triggers: tuple[WorkflowTrigger, ...] = (MANUAL,),
        enabled: bool = True,
    ) -> None:
        self.state.workflows = [
            Workflow(
                id=3,
                name="Paid",
                order=0,
                enabled=enabled,
                triggers=triggers,
                actions=actions or (TAGGING,),
            )
        ]

    def may(self, user: str, *permissions: str) -> None:
        """Give the user the permissions."""
        self.state.users = [
            replace(u, permissions=u.permissions | {*permissions}) if u.username == user else u
            for u in self.state.users
        ]
        self.fake = World(self.state, user=user, writes=self.fake.allow_writes)
        self.runs = workflow_runs(self.fake)

    def without(self, user: str, permission: str) -> None:
        """Take a permission from the user."""
        self.state.users = [
            replace(u, permissions=u.permissions - {permission}) if u.username == user else u
            for u in self.state.users
        ]
        self.may(user)

    def document(self, number: int) -> FakeDocument:
        return next(d for d in self.state.documents if d.id == number)

    async def run(
        self,
        workflow: NameOrId = "Paid",
        *,
        apply_filters: bool = True,
        dry_run: bool = False,
        confirm: bool = False,
        **selection: object,
    ) -> WorkflowRunReport:
        chosen = Selection.model_validate(selection or {"document_ids": (101, 102)})
        return await self.runs.run(
            workflow, chosen, apply_filters=apply_filters, dry_run=dry_run, confirm=confirm
        )

    async def asked(
        self, *, apply_filters: bool = True, confirm: bool = False, **selection: object
    ) -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await self.run(
                "Paid", apply_filters=apply_filters, dry_run=False, confirm=confirm, **selection
            )
        return raised.value


def bare(actions: Sequence[str]) -> tuple[str, ...]:
    """What the actions do, without the numbers of their steps."""
    return tuple(told.partition(": ")[2] for told in actions)


def action(number: int, **settings: JsonValue) -> WorkflowAction:
    return WorkflowAction(id=number + 10, type=number, settings=settings)


def mail(to: str, **more: JsonValue) -> WorkflowAction:
    return action(3, email={"subject": "s", "body": "b", "to": to, **more})


def hook(url: str, **more: JsonValue) -> WorkflowAction:
    return action(4, webhook={"url": url, **more})


# --- what a confirmation says


async def test_the_confirmation_names_the_workflow_every_document_and_what_it_does() -> None:
    setup = Setup(writes=False)
    setup.flow(
        action(
            1,
            assign_title="{{ created_year }} paid",
            assign_tags=[FINANCE, INBOX],
            assign_correspondent=1,
            assign_custom_fields=[1],
            assign_custom_fields_values={"1": "settled"},
        ),
        action(2, remove_tags=[FINANCE], remove_all_correspondents=True),
        mail("a@example.org,b@example.org", include_document=True),
        hook("https://user:pw@hooks.example.org/in?token=s3"),
        action(6),
    )

    refusal = await setup.asked()

    assert refusal.message == (
        "Nothing was changed. Confirming would run the workflow 'Paid' (id 3), which will (the "
        "most far-reaching first, the numbers are the order it runs in): step 3 of 5: send an "
        "e-mail to 'a@example.org', 'b@example.org' with the document attached; step 4 of 5: send "
        "a POST request to 'hooks.example.org', with credentials (not shown); step 5 of 5: move "
        "each of them to the trash, after everything else; step 1 of 5: assign title "
        "'{{ created_year }} paid', tags 'e2e-finance', 'e2e-inbox', correspondent 'e2e-acme', "
        "custom fields 'e2e-string': 'settled'; step 2 of 5: remove tags 'e2e-finance' (with their "
        "child tags 'e2e-invoices' and any you cannot see), all correspondents. It runs on 2 "
        "documents (101 'Scan 1', 102 'Scan 2'), all of which match: its manual trigger has no "
        "filters."
    )
    assert refusal.hint == CONFIRM_HINT


@pytest.mark.parametrize(
    ("actions", "told"),
    [
        (
            (action(1, assign_owner=READER_ID, assign_view_users=[READER_ID]),),
            (
                "change who owns or may see the matching documents; assign owner 'e2e-reader', "
                "view users 'e2e-reader'."
            ),
        ),
        (
            (action(2, remove_all_permissions=True, remove_owners=[READER_ID]),),
            "remove owners 'e2e-reader', all permissions.",
        ),
        ((action(1, assign_tags=[999]),), "assign tags #999."),
        ((action(1),), "do nothing."),
        ((action(2),), "do nothing."),
        (
            (action(7),),
            "run remote OCR (Paperless ignores it here: it needs a document being consumed).",
        ),
        (
            (action(5, passwords=["hush"]),),
            (
                "unlock each matching document with stored passwords and add the result as a new "
                "version."
            ),
        ),
        (
            (action(8),),
            "apply AI suggestions to no field (Paperless skips it).",
        ),
        ((action(99),), "run an action of type 99."),
    ],
)
async def test_every_kind_of_action_is_told(actions: tuple[WorkflowAction, ...], told: str) -> None:
    setup = Setup(writes=False)
    setup.flow(*actions)

    refusal = await setup.asked()

    assert (
        f"the numbers are the order it runs in): {told} It runs on 2 documents"
        in refusal.message.replace("step 1 of 1: ", "")
    )
    assert "hush" not in refusal.message


async def test_a_workflow_without_actions_does_nothing_and_says_so() -> None:
    setup = Setup(writes=False)
    setup.state.workflows = [setup.state.workflows[0].model_copy(update={"actions": ()})]

    assert (await setup.asked()).message.count("): do nothing. It runs on 2 documents") == 1


FILTERED = WorkflowTrigger(id=1, type=5, settings={"filter_has_tags": [FINANCE]})
ADDED = WorkflowTrigger(id=2, type=2, settings={})


@pytest.mark.parametrize(
    ("triggers", "apply_filters", "told"),
    [
        (
            (FILTERED, ADDED),
            True,
            (
                ", skipping those that do not match its manual trigger (manual, filtered by "
                "has_tags); Paperless does not tell which it skips."
            ),
        ),
        (
            (FILTERED, WorkflowTrigger(id=3, type=5, settings={"filter_has_document_type": 1})),
            True,
            (
                ", skipping those that do not match its manual trigger (manual, filtered by "
                "has_tags; manual, filtered by has_document_type); Paperless does not "
                "tell which it skips."
            ),
        ),
        (
            (FILTERED, MANUAL),
            True,
            ", all of which match: its manual trigger has no filters.",
        ),
        (
            (FILTERED, ADDED),
            False,
            ", each of which counts as matching: the filters of its triggers are not applied.",
        ),
    ],
)
async def test_the_confirmation_says_what_the_filters_of_the_trigger_do(
    triggers: tuple[WorkflowTrigger, ...], *, apply_filters: bool, told: str
) -> None:
    setup = Setup(writes=False)
    setup.flow(triggers=triggers)

    refusal = await setup.asked(apply_filters=apply_filters)

    assert refusal.message.endswith(f"102 'Scan 2'){told}")


async def test_a_destination_that_cannot_be_shown_and_too_many_of_them_are_refused() -> None:
    setup = Setup(writes=False)
    addresses = [f"a{n}@example.org" for n in range(MAX_DESTINATIONS + 1)]

    for actions in (
        (mail("a@b@example.org"),),
        (hook("https://one.example\\@other.example/x"),),
        (mail(",".join(addresses)),),
        (*(mail(address) for address in addresses),),
    ):
        setup.flow(*actions)
        for confirm in (True, False):
            with pytest.raises(Validation) as raised:
                _ = await setup.run(confirm=confirm)
            assert not isinstance(raised.value, ConfirmationRequired)


# --- nothing but what was shown


async def test_confirmed_ids_run_exactly_there_with_the_filters_as_asked() -> None:
    for apply_filters in (True, False):
        setup = Setup(scans=3)

        report = await setup.run(confirm=True, apply_filters=apply_filters)

        assert setup.state.workflow_runs == [(3, (101, 102), apply_filters)]
        assert [INBOX in setup.document(n).tags for n in (101, 102, 103)] == [True, True, False]
        assert (report.workflow_id, report.workflow) == (3, "Paid")
        assert report.actions == ("step 1 of 1: assign tags 'e2e-inbox'",)
        assert (report.apply_filters, report.dry_run, report.started) == (
            apply_filters,
            False,
            True,
        )
        assert (report.matched, report.selected_ids) == (2, (101, 102))
        assert (report.failed, report.blocked, report.also_matching) == ((), (), ())
        assert report.unconfirmed_task_id is None
        assert report.message == "Ran the workflow 'Paid' (id 3) on 2 documents."


@pytest.mark.parametrize("confirm", [False, True])
async def test_a_filter_never_runs_anything_by_itself_and_names_the_next_call(
    *, confirm: bool
) -> None:
    setup = Setup(writes=False)

    refusal = await setup.asked(confirm=confirm, filters=SCANS)

    assert "on 2 documents (101 'Scan 1', 102 'Scan 2')" in refusal.message
    assert refusal.hint == (
        "Show the user these documents and the effect. Once they agree, call again with the same "
        "workflow, apply_filters, filters (or saved_view) and exclude_ids, and with "
        "document_ids=[101, 102] and confirm=true: it runs on exactly these, and on none if one of "
        "them no longer matches. With filters or saved_view alone, confirm=true is not enough."
    )


@given(
    dry_run=st.booleans(),
    confirm=st.booleans(),
    apply_filters=st.booleans(),
    by_view=st.booleans(),
    scans=st.integers(0, 3),
)
async def test_no_call_by_filter_or_view_alone_runs_anything(
    *, dry_run: bool, confirm: bool, apply_filters: bool, by_view: bool, scans: int
) -> None:
    setup = Setup(writes=False, scans=scans)
    rules = (FilterRule(rule_type=0, value="Scan"),)
    setup.state.saved_views = [FakeSavedView(id=9, name="scans", owner=2, filter_rules=rules)]
    selection: dict[str, object] = {"saved_view": 9} if by_view else {"filters": SCANS}

    try:
        report = await setup.run(
            "Paid", dry_run=dry_run, confirm=confirm, apply_filters=apply_filters, **selection
        )
    except ConfirmationRequired:
        return
    assert not report.started


async def test_an_accepted_dialog_runs_exactly_what_it_named() -> None:
    setup = Setup()
    refusal = await setup.asked(filters=SCANS)

    with accepting(refusal.fingerprint):
        report = await setup.run(filters=SCANS)

    assert setup.state.workflow_runs == [(3, (101, 102), True)]
    assert report.started


def joins(setup: Setup) -> None:
    setup.state.documents.append(FakeDocument(id=110, title="Scan 10", owner=ADMIN_ID))


def leaves(setup: Setup) -> None:
    setup.document(102).title = "Another"


def other_value(setup: Setup) -> None:
    setup.document(102).correspondent = 1


def other_owner(setup: Setup) -> None:
    setup.document(102).owner = None


def other_file(setup: Setup) -> None:
    [root] = setup.document(102).versions
    setup.document(102).versions.insert(0, root.model_copy(update={"id": 500, "is_root": False}))


def other_action(setup: Setup) -> None:
    # The same words: the name belongs to another tag now.
    setup.state.tags.append(FakeTag(id=77, name="e2e-inbox"))
    setup.state.tags = [t for t in setup.state.tags if t.id != INBOX]
    setup.flow(action(1, assign_tags=[77]))
    setup.runs = workflow_runs(setup.fake)


def other_text(setup: Setup) -> None:
    setup.flow(mail("a@example.org", include_document=False))


def other_filter(setup: Setup) -> None:
    trigger = WorkflowTrigger(id=1, type=5, settings={"filter_has_tags": [INBOX]})
    setup.flow(triggers=(trigger,))


CHANGES = [joins, leaves, other_value, other_owner, other_file, other_action, other_filter]


@pytest.mark.parametrize("change", CHANGES)
async def test_an_accepted_dialog_does_not_cover_what_changed_while_it_was_open(
    change: Callable[[Setup], None],
) -> None:
    setup = Setup(writes=False)
    setup.flow(triggers=(FILTERED,))
    refusal = await setup.asked(filters=SCANS)
    change(setup)

    with accepting(refusal.fingerprint):
        again = await setup.asked(filters=SCANS, confirm=True)

    assert again.fingerprint != refusal.fingerprint


async def test_the_body_of_an_email_is_bound_although_it_is_not_shown() -> None:
    setup = Setup(writes=False)
    setup.flow(mail("a@example.org"))
    refusal = await setup.asked()
    setup.flow(action(3, email={"subject": "s", "body": "other", "to": "a@example.org"}))

    with accepting(refusal.fingerprint):
        again = await setup.asked()

    assert again.message == refusal.message
    assert again.fingerprint != refusal.fingerprint


async def test_a_dialog_with_filters_does_not_cover_a_run_without_them() -> None:
    setup = Setup(writes=False)
    refusal = await setup.asked(apply_filters=True)

    with accepting(refusal.fingerprint):
        again = await setup.asked(apply_filters=False)

    assert again.fingerprint != refusal.fingerprint


async def test_ids_with_the_filter_run_on_these_and_name_what_matches_besides() -> None:
    setup = Setup(scans=4)

    report = await setup.run(confirm=True, filters=SCANS, document_ids=[102, 101])

    assert setup.state.workflow_runs == [(3, (102, 101), True)]
    assert (report.matched, report.also_matching) == (4, (103, 104))
    assert report.message.endswith(" 2 other documents match and were not touched (also_matching).")


async def test_ids_that_no_longer_match_and_missing_ones_run_nothing() -> None:
    setup = Setup(writes=False)
    leaves(setup)

    with pytest.raises(Validation, match="no longer matches: 102"):
        _ = await setup.run(confirm=True, filters=SCANS, document_ids=[101, 102])
    with pytest.raises(NotFound, match="999"):
        _ = await setup.run(confirm=True, document_ids=[101, 999])


async def test_an_exclusion_that_is_not_there_refuses_the_run_and_its_preview() -> None:
    setup = Setup(writes=False)
    rules = (FilterRule(rule_type=0, value="Scan"), FilterRule(rule_type=17, value="888"))
    setup.state.saved_views = [FakeSavedView(id=9, name="scans", owner=2, filter_rules=rules)]

    for dry_run in (True, False):
        with pytest.raises(Validation, match="the selection excludes tag 888"):
            _ = await setup.run(dry_run=dry_run, saved_view=9)


async def test_what_could_not_be_checked_is_told() -> None:
    setup = Setup(READER[0], writes=False, scans=0)
    setup.state.documents.append(FakeDocument(id=101, title="Scan 1", owner=READER_ID))
    setup.may(READER[0], "view_workflow", "view_savedview", "change_document")
    setup.without(READER[0], "view_tag")
    rules = (FilterRule(rule_type=0, value="Scan"), FilterRule(rule_type=17, value="888"))
    setup.state.saved_views = [FakeSavedView(id=9, name="scans", owner=3, filter_rules=rules)]

    refusal = await setup.asked(saved_view=9)

    assert "whether tag 888 still exists, which the selection excludes" in refusal.message


async def test_at_most_a_hundred_documents_run_and_a_filter_never_goes_on_by_itself() -> None:
    setup = Setup(scans=101)

    with pytest.raises(Validation) as raised:
        _ = await setup.run(dry_run=True, filters=SCANS)
    with pytest.raises(Validation, match="between 1 and 100"):
        _ = await setup.run(confirm=True, document_ids=list(range(101, 202)))
    whole = await setup.run(confirm=True, filters=SCANS, document_ids=list(range(101, 201)))

    assert raised.value.message == (
        "101 documents match, and a workflow that ran leaves them matching: at most 100 may be "
        "chosen by filters or saved_view."
    )
    assert raised.value.hint == (
        "Narrow the filters, or add document_ids: read_documents shows them."
    )
    assert len(whole.selected_ids) == 100
    assert whole.also_matching == (201,)


async def test_a_hundred_documents_by_filter_are_all_named() -> None:
    setup = Setup(writes=False, scans=100)

    refusal = await setup.asked(filters=SCANS)

    assert all(f"{n} 'Scan {n - 100}'" in refusal.message for n in range(101, 201))


async def test_more_than_a_thousand_matches_are_refused() -> None:
    with pytest.raises(Validation, match="1001 documents match; at most 1000 may"):
        _ = await Setup(writes=False, scans=1001).run(dry_run=True, filters=SCANS)


async def test_nothing_matches_and_nothing_is_asked_or_run() -> None:
    setup = Setup(writes=False)

    report = await setup.run(filters={"title_contains": "no such title"})

    assert (report.started, report.matched, report.selected_ids) == (False, 0, ())
    assert report.message == "Nothing was run: no document matches."
    assert report.dry_run is False


async def test_a_preview_of_nothing_is_still_a_preview() -> None:
    report = await Setup(writes=False).run(
        dry_run=True, filters={"title_contains": "no such title"}
    )

    assert (report.dry_run, report.started) == (True, False)
    assert report.message == "Nothing was run: no document matches."


# --- previews


async def test_a_preview_runs_nothing_and_says_what_confirming_would_do() -> None:
    setup = Setup(writes=False)

    report = await setup.run(dry_run=True)

    assert (report.dry_run, report.started, report.selected_ids) == (True, False, (101, 102))
    assert report.actions == ("step 1 of 1: assign tags 'e2e-inbox'",)
    assert report.message == (
        "Dry run, nothing was run. Confirming would run the workflow 'Paid' (id 3) and do what "
        "actions says on 2 documents (101 'Scan 1', 102 'Scan 2'), all of which match: its manual "
        "trigger has no filters. To go ahead, call again without dry_run; pass confirm=true only "
        "if the user already agreed."
    )


async def test_a_preview_by_filter_names_the_ids_of_the_next_call() -> None:
    setup = Setup(writes=False, scans=3)

    alone = await setup.run(dry_run=True, filters=SCANS)
    beside = await setup.run(dry_run=True, filters=SCANS, document_ids=[101, 102])

    assert alone.selected_ids == (101, 102, 103)
    assert "document_ids set to selected_ids" in alone.message
    assert beside.also_matching == (103,)
    assert beside.message.endswith(" 1 other document matches and is not touched (also_matching).")


# --- the workflow


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("confirm", [False, True])
async def test_an_unusable_workflow_is_refused(*, dry_run: bool, confirm: bool) -> None:
    setup = Setup(writes=False)

    setup.flow(enabled=False)
    with pytest.raises(Validation) as off:
        _ = await setup.run(dry_run=dry_run, confirm=confirm)
    setup.flow(triggers=(ADDED,))
    with pytest.raises(Validation) as automatic:
        _ = await setup.run(dry_run=dry_run, confirm=confirm)

    assert off.value.message == (
        "Nothing was run: the workflow 'Paid' (id 3) is not enabled, so Paperless does not run it."
    )
    assert off.value.hint == "If the user wants that, switch it on with update_workflow first."
    assert automatic.value.message == (
        "Nothing was run: the workflow 'Paid' (id 3) has no manual trigger, so it cannot be run by "
        "hand."
    )
    assert automatic.value.hint == "Add a manual trigger to it in the Paperless web UI."


async def test_an_unknown_or_ambiguous_workflow_is_refused() -> None:
    setup = Setup(writes=False)
    with pytest.raises(NotFound, match="workflow"):
        _ = await setup.run("Nope")
    with pytest.raises(NotFound, match="999"):
        _ = await setup.run(999)
    [flow] = setup.state.workflows
    setup.state.workflows = [
        flow.model_copy(update={"name": name, "id": n}) for n, name in ((3, "PAID"), (4, "paid"))
    ]
    with pytest.raises(Ambiguous):
        _ = await setup.run("Paid")


# --- restricted users


async def test_a_user_who_may_not_view_workflows_runs_nothing() -> None:
    with pytest.raises(PermissionDenied):
        _ = await Setup(READER[0], writes=False).run(document_ids=[1])


def restricted(*, owner: int | None, settings: bool = True, writes: bool = False) -> Setup:
    """A user who may run workflows and change both documents, the second owned by `owner`."""
    setup = Setup(READER[0], writes=writes)
    for number, owned_by in ((101, READER_ID), (102, owner)):
        setup.document(number).owner = owned_by
        setup.document(number).viewers = {READER_ID}
        setup.document(number).editors = {READER_ID}
    rights = ("view_workflow", "change_document", "view_paperlesstask")
    setup.may(READER[0], *rights)
    if not settings:
        setup.state.users = [
            replace(u, permissions=u.permissions - {"view_uisettings"}) if u.id == READER_ID else u
            for u in setup.state.users
        ]
        setup.fake = World(setup.state, user=READER[0], writes=writes)
        setup.runs = workflow_runs(setup.fake)
    else:
        setup.may(READER[0], "view_uisettings")
    return setup


async def test_a_document_of_another_owner_is_known_beforehand_and_refuses_the_run() -> None:
    setup = restricted(owner=ADMIN_ID)

    preview = await setup.run(dry_run=True)
    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.run(confirm=True)

    assert preview.blocked == (102,)
    assert preview.message == (
        "Dry run, nothing was run. The run would be refused as a whole: you may not change or "
        "do not own 1 document (102 'Scan 2')."
    )
    assert raised.value.message == (
        "You may not change or do not own 1 document (102 'Scan 2'), so Paperless would refuse the "
        "whole run; nothing was run."
    )
    assert raised.value.hint == (
        "Leave those documents out: a workflow runs by hand only on documents you own, or that "
        "have no owner."
    )


async def test_a_view_grant_is_known_beforehand_too() -> None:
    setup = restricted(owner=ADMIN_ID, settings=False)
    setup.document(102).editors = set()

    with pytest.raises(PermissionDenied, match="102 'Scan 2'"):
        _ = await setup.run(confirm=True)


async def test_where_the_user_cannot_be_read_paperless_decides() -> None:
    setup = restricted(owner=ADMIN_ID, settings=False, writes=True)

    with pytest.raises(PermissionDenied, match="refused to run the workflow"):
        _ = await setup.run(confirm=True)

    assert setup.state.workflow_runs == []


async def test_own_and_unowned_documents_run() -> None:
    setup = restricted(owner=None, writes=True)

    report = await setup.run(confirm=True)

    assert report.started
    assert setup.state.workflow_runs == [(3, (101, 102), True)]


async def test_a_superuser_runs_it_on_documents_of_others() -> None:
    setup = Setup()
    setup.document(102).owner = READER_ID

    assert (await setup.run(confirm=True)).started


# --- how a run ends


async def test_a_run_that_failed_on_some_documents_says_so_first() -> None:
    setup = Setup(scans=3)
    setup.state.workflow_fails_on = {102}

    report = await setup.run(confirm=True, document_ids=[101, 102, 103])

    assert report.started
    assert report.failed == (102,)
    assert report.message == (
        "The workflow 'Paid' (id 3) failed on 1 of 3 documents: 102. Some of its actions may have "
        "run there; the Paperless logs show the reason. It ran on the others. "
        f"{NOT_AGAIN}"
    )


async def test_a_run_that_failed_on_every_document_is_an_error() -> None:
    setup = Setup()
    setup.state.workflow_fails_on = {101, 102}

    with pytest.raises(TaskFailed) as raised:
        _ = await setup.run(confirm=True)

    assert raised.value.message == (
        "The workflow 'Paid' (id 3) failed on 2 of 2 documents: 101, 102. Some of its actions may "
        "have run there; the Paperless logs show the reason."
    )
    assert raised.value.hint == NOT_AGAIN


async def test_with_filters_the_others_may_have_been_skipped() -> None:
    setup = Setup(scans=3)
    setup.flow(triggers=(FILTERED,))
    setup.document(102).tags = [FINANCE]

    done = await setup.run(confirm=True, document_ids=[101, 102, 103])
    setup.state.workflow_fails_on = {102}
    partly = await setup.run(confirm=True, document_ids=[101, 102, 103])
    without = await setup.run(confirm=True, apply_filters=False, document_ids=[101, 103])

    assert done.message == (
        "Ran the workflow 'Paid' (id 3) on 3 documents, except those that do not match its manual "
        "trigger; Paperless does not tell which."
    )
    assert "It ran on the others, except those that do not match" in partly.message
    assert without.message == "Ran the workflow 'Paid' (id 3) on 2 documents."


@pytest.mark.parametrize(
    "ends",
    [
        UpstreamTimeout("Paperless did not answer in time."),
        UpstreamUnavailable("Paperless is unavailable.", status=503),
        TaskFailed("The task of the workflow run failed.", hint="other"),
        PaperlessMcpError("Paperless sent an unexpected response."),
    ],
)
async def test_after_a_lost_answer_or_a_failed_task_it_is_not_known_what_ran(
    ends: PaperlessMcpError,
) -> None:
    setup = Setup()
    setup.fake.ends = ends
    message = ends.message

    with pytest.raises(type(ends)) as raised:
        _ = await setup.run(confirm=True)

    assert raised.value.message == (
        f"{message} On which of 2 documents (101 'Scan 1', 102 'Scan 2') the workflow 'Paid' (id "
        "3) ran is unknown."
    )
    assert raised.value.hint == NOT_AGAIN
    assert len(setup.state.workflow_runs) == 1


@pytest.mark.parametrize(
    "ends",
    [
        PermissionDenied("Refused.", hint="why"),
        Validation("Rejected.", hint="why"),
        NotFound("Not found.", hint="why"),
        Conflict("Conflict.", hint="why"),
        AuthenticationFailed("No.", hint="why"),
        UpstreamUnavailable("Too many requests.", hint="why", status=429),
    ],
)
async def test_a_refusal_ran_nothing_and_is_told_as_it_is(ends: PaperlessMcpError) -> None:
    setup = Setup()
    setup.fake.ends = ends
    message = ends.message

    with pytest.raises(type(ends)) as raised:
        _ = await setup.run(confirm=True)

    assert (raised.value.message, raised.value.hint) == (message, "why")


async def test_a_run_that_could_not_be_followed_names_its_task_and_is_not_repeated() -> None:
    setup = Setup()
    setup.fake.ends = Unconfirmed(task_id="t-1", reason="it was still running after 60 seconds")

    report = await setup.run(confirm=True)

    assert (report.started, report.unconfirmed_task_id) == (True, "t-1")
    assert report.message == (
        "Asked Paperless to run the workflow 'Paid' (id 3) on 2 documents. Not confirmed: "
        "Paperless queued task t-1, but it was still running after 60 seconds. Read it with "
        f"get_task. {NOT_AGAIN}"
    )


async def test_the_results_of_mails_and_webhooks_are_said_to_be_unknown() -> None:
    for acting in (mail("a@example.org"), hook("https://example.org/x"), action(5), action(8)):
        setup = Setup()
        setup.flow(acting)

        report = await setup.run(confirm=True)

        assert report.message == f"Ran the workflow 'Paid' (id 3) on 2 documents.{UNSEEN}"


async def test_documents_in_the_trash_can_be_restored() -> None:
    setup = Setup()
    setup.flow(action(6))

    preview = await setup.run(dry_run=True)
    report = await setup.run(confirm=True)

    assert TRASH_NOTE not in preview.message
    assert report.message == f"Ran the workflow 'Paid' (id 3) on 2 documents. {TRASH_NOTE}"
    assert {d.id for d in setup.state.trash} >= {101, 102}


async def test_what_a_run_counts_is_looked_up_anew() -> None:
    setup = Setup()
    taxonomy = TaxonomyResolver(setup.fake, StaticTokenProvider("t"), ttl=3600.0)
    setup.runs = workflow_runs(setup.fake, taxonomy)
    before = {t.id: t.document_count for t in await taxonomy.items("tag")}

    _ = await setup.run(confirm=True)

    after = {t.id: t.document_count for t in await taxonomy.items("tag")}
    assert (after[INBOX] or 0) == (before[INBOX] or 0) + 2


# --- filters: only what Paperless reads for a document that exists

# What the web UI of 3.5.1 stores for a new manual trigger.
WEB_UI: dict[str, JsonValue] = {
    "sources": [],
    "filter_filename": None,
    "filter_path": None,
    "filter_mailrule": None,
    "filter_has_tags": [],
    "filter_has_all_tags": [],
    "filter_has_not_tags": [],
    "filter_has_any_correspondents": [],
    "filter_has_not_correspondents": [],
    "filter_has_any_document_types": [],
    "filter_has_not_document_types": [],
    "filter_has_any_storage_paths": [],
    "filter_has_not_storage_paths": [],
    "filter_custom_field_query": None,
    "filter_has_correspondent": None,
    "filter_has_document_type": None,
    "filter_has_storage_path": None,
    "matching_algorithm": 0,
    "match": "",
    "is_insensitive": True,
    "schedule_offset_days": 0,
    "schedule_is_recurring": False,
    "schedule_recurring_interval_days": 1,
    "schedule_date_field": "added",
    "schedule_date_custom_field": None,
}


@pytest.mark.parametrize(
    "more",
    [
        {},
        {"filter_path": "/x/*", "filter_mailrule": 4, "sources": [1]},
        {"match": "word", "is_insensitive": False},
        {
            "schedule_offset_days": 3,
            "schedule_is_recurring": True,
            "schedule_date_field": "created",
        },
        {"schedule_recurring_interval_days": 9, "schedule_date_custom_field": 2},
    ],
)
async def test_what_paperless_does_not_read_for_a_manual_run_is_no_filter(
    more: dict[str, JsonValue],
) -> None:
    setup = Setup()
    setup.flow(triggers=(WorkflowTrigger(id=1, type=5, settings={**WEB_UI, **more}),))

    preview = await setup.run(dry_run=True)
    done = await setup.run(confirm=True)

    assert "all of which match: its manual trigger has no filters" in preview.message
    assert done.message == "Ran the workflow 'Paid' (id 3) on 2 documents."


@pytest.mark.parametrize(
    ("name", "value", "shown"),
    [
        ("matching_algorithm", 1, "matching_algorithm"),
        ("filter_filename", "*.pdf", "filename"),
        ("filter_custom_field_query", '["1", "exact", 2]', "custom_field_query"),
        ("filter_has_tags", [1], "has_tags"),
        ("filter_has_all_tags", [1], "has_all_tags"),
        ("filter_has_not_tags", [1], "has_not_tags"),
        ("filter_has_any_correspondents", [1], "has_any_correspondents"),
        ("filter_has_not_correspondents", [1], "has_not_correspondents"),
        ("filter_has_any_document_types", [1], "has_any_document_types"),
        ("filter_has_not_document_types", [1], "has_not_document_types"),
        ("filter_has_any_storage_paths", [1], "has_any_storage_paths"),
        ("filter_has_not_storage_paths", [1], "has_not_storage_paths"),
        ("filter_has_correspondent", 1, "has_correspondent"),
        ("filter_has_document_type", 1, "has_document_type"),
        ("filter_has_storage_path", 1, "has_storage_path"),
    ],
)
async def test_every_filter_paperless_reads_is_one(name: str, value: JsonValue, shown: str) -> None:
    setup = Setup(writes=False)
    settings = {**WEB_UI, name: value, "filter_path": "/x/*"}
    setup.flow(triggers=(WorkflowTrigger(id=1, type=5, settings=settings),))

    refusal = await setup.asked()

    assert f"its manual trigger (manual, filtered by {shown}); Paperless" in refusal.message


# --- what comes along, names and values


def family(setup: Setup) -> None:
    """Finance > Paid > Cash, and Finance under a tag that is not visible."""
    setup.state.tags += [
        FakeTag(id=61, name="Paid tag", parent=FINANCE),
        FakeTag(id=62, name="Cash", parent=61),
    ]


async def test_parent_tags_of_an_assignment_and_child_tags_of_a_removal_are_named() -> None:
    setup = Setup(writes=False)
    family(setup)
    setup.flow(action(1, assign_tags=[62]), action(2, remove_tags=[FINANCE]))

    message = (await setup.asked()).message

    assert "assign tags 'Cash' (with their parent tags 'Paid tag', 'e2e-finance')" in message
    assert (
        "remove tags 'e2e-finance' (with their child tags 'e2e-invoices', 'Paid tag', 'Cash' and "
        "any you cannot "
        "see)"
    ) in message


async def test_child_tags_that_cannot_be_seen_are_always_mentioned() -> None:
    setup = Setup(writes=False)
    setup.flow(action(2, remove_tags=[INBOX]))

    assert (
        "remove tags 'e2e-inbox' (with any child tags you cannot see)."
        in (await setup.asked()).message
    )


async def test_a_parent_tag_that_is_hidden_is_named_by_its_number() -> None:
    setup = Setup(writes=False)
    setup.state.tags.append(FakeTag(id=61, name="Orphan", parent=888))
    setup.flow(action(1, assign_tags=[61]))

    assert "assign tags 'Orphan' (with their parent tags #888)" in (await setup.asked()).message


async def test_a_parent_that_is_assigned_too_is_named_once() -> None:
    setup = Setup(writes=False)
    family(setup)
    setup.flow(action(1, assign_tags=[61, FINANCE]))

    assert "assign tags 'Paid tag', 'e2e-finance'." in (await setup.asked()).message


async def test_without_the_list_of_tags_it_is_said_that_tags_come_along() -> None:
    setup = Setup(READER[0], writes=False)
    for number in (101, 102):
        setup.document(number).owner = READER_ID
    setup.flow(action(1, assign_tags=[INBOX]), action(2, remove_tags=[FINANCE]))
    setup.may(READER[0], "view_workflow", "change_document")
    setup.without(READER[0], "view_tag")

    message = (await setup.asked()).message

    assert "assign tags #4 (with their parent tags, which you may not list)" in message
    assert "remove tags #1 (with their child tags, which you may not list)" in message


async def test_the_far_reaching_actions_come_first_wherever_they_stand() -> None:
    setup = Setup(writes=False)
    setup.flow(
        TAGGING,
        action(2, remove_tags=[FINANCE]),
        action(1, assign_owner=READER_ID),
        action(2, remove_all_owners=True),
        action(2, remove_change_groups=[1]),
        action(8, ai_suggestion_fields=["title"]),
        action(5, passwords=["hush"]),
        action(99),
        mail("a@example.org"),
        hook("https://example.org/x"),
        action(6),
    )

    actions = bare((await setup.run(dry_run=True)).actions)

    assert [told.split(" ")[0] for told in actions[-2:]] == ["assign", "remove"]
    assert actions[-2:] == (
        "assign tags 'e2e-inbox'",
        "remove tags 'e2e-finance' (with their child tags 'e2e-invoices' and any you cannot see)",
    )
    starts = ("change", "assign owner", "remove all owners", "remove change groups", "ask the AI")
    for start in (*starts, "unlock", "run an action", "send an e-mail", "send a POST", "move"):
        assert any(told.startswith(start) for told in actions[:-2]), start


@pytest.mark.parametrize(
    ("settings", "told"),
    [
        (
            {
                "ai_suggestion_fields": ["title", "document_type"],
                "ai_create_missing": True,
                "ai_overwrite_existing": True,
            },
            (
                "for title, document type, over existing values, creating tags, "
                "correspondents and document types that do not exist."
            ),
        ),
        (
            {"ai_suggestion_fields": ["tags", "new' thing"]},
            (
                'for tags, "new\' thing", only where there is no value (tags are only added), '
                "without "
                "creating anything."
            ),
        ),
    ],
)
async def test_what_the_ai_may_do_is_told_in_words(
    settings: dict[str, JsonValue], told: str
) -> None:
    setup = Setup(writes=False)
    setup.flow(action(8, **settings))

    assert (
        f"of each of them and apply what it suggests {told} It runs"
        in (await setup.asked()).message
    )


async def test_select_labels_and_linked_titles_are_shown_instead_of_ids() -> None:
    setup = Setup(writes=False)
    fields = {f.name: f.id for f in setup.state.custom_fields}
    select, link = fields["e2e-select"], fields["e2e-documentlink"]
    options = next(f for f in setup.state.custom_fields if f.id == select).select_options
    assert options
    first = options[0]
    values: dict[str, JsonValue] = {
        str(select): first.id,
        str(link): [101, 999],
        "8888": "x",
        "777": "never assigned",
    }
    setup.flow(
        action(
            1,
            assign_custom_fields=[select, link, 8888, fields["e2e-string"]],
            assign_custom_fields_values=values,
        )
    )

    message = (await setup.asked()).message

    assert (
        f"assign custom fields 'e2e-select': '{first.label}', 'e2e-documentlink': 101 'Scan 1',"
        " #999, #8888: 'x', 'e2e-string' (without a value, where it is missing)"
    ) in message
    # Paperless writes only the fields that are assigned.
    assert "never" not in message


async def test_two_fields_with_one_name_are_both_told() -> None:
    setup = Setup(writes=False)
    first = setup.state.custom_fields[0]
    setup.state.custom_fields = [first, replace(first, id=70)]
    values: dict[str, JsonValue] = {str(first.id): "one", "70": "two"}
    setup.flow(action(1, assign_custom_fields=[first.id, 70], assign_custom_fields_values=values))

    message = (await setup.asked()).message

    assert f"'{first.name}': 'one', '{first.name}': 'two'" in message


@pytest.mark.parametrize(
    ("number", "name"),
    [(n, name) for n in (1, 2) for name in ACTION_FIELDS[n] if "custom_fields" not in name],
)
async def test_every_setting_that_writes_or_takes_away_is_told(number: int, name: str) -> None:
    setup = Setup(writes=False)
    shape = ACTION_FIELDS[number][name].shape
    given: dict[str, JsonValue] = {"flag": True, "list": [1], "one": 1, "text": "x"}
    value = given[shape]
    setup.flow(action(number, **{name: value}))

    actions = bare((await setup.run(dry_run=True)).actions)

    what = name.removeprefix("assign_").removeprefix("remove_").replace("_", " ")
    verb = "assign" if number == 1 else "remove"
    assert any(told.startswith(f"{verb} {what}") for told in actions), actions


async def test_removed_custom_fields_are_told() -> None:
    setup = Setup(writes=False)
    setup.flow(action(2, remove_custom_fields=[1]), action(2, remove_all_custom_fields=True))

    actions = bare((await setup.run(dry_run=True)).actions)

    assert actions == ("remove custom fields 'e2e-string'", "remove all custom fields")


# --- bound values


BOUND: list[tuple[str, object]] = [
    ("title", "Scan 2 "),
    ("created", DAY - timedelta(days=40)),
    ("added", datetime(2026, 1, 1, tzinfo=UTC)),
    ("correspondent", 1),
    ("document_type", 1),
    ("storage_path", 1),
    ("tags", [FINANCE]),
    ("custom_fields", {1: "x"}),
    ("owner", None),
]


@pytest.mark.parametrize(("name", "value"), BOUND)
async def test_every_current_value_is_bound(name: str, value: object) -> None:
    setup = Setup(writes=False)
    refusal = await setup.asked()
    vars(setup.document(102))[name] = value

    with accepting(refusal.fingerprint):
        again = await setup.asked()

    assert again.message == refusal.message or name == "title"
    assert again.fingerprint != refusal.fingerprint


async def test_the_triggers_are_bound_with_their_values_and_those_that_are_not_shown() -> None:
    for before, after in (
        (FILTERED, WorkflowTrigger(id=1, type=5, settings={"filter_has_tags": [INBOX]})),
        (MANUAL, MANUAL.model_copy(update={"id": 7})),
    ):
        setup = Setup(writes=False)
        setup.flow(triggers=(before,))
        refusal = await setup.asked()
        setup.flow(triggers=(after,))

        with accepting(refusal.fingerprint):
            again = await setup.asked()

        assert again.message == refusal.message
        assert again.fingerprint != refusal.fingerprint


# --- rights and ends


async def test_a_missing_permission_to_change_documents_is_told_beforehand() -> None:
    setup = restricted(owner=None)
    setup.without(READER[0], "change_document")

    preview = await setup.run(dry_run=True)
    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.run(confirm=True)

    assert preview.blocked == (101, 102)
    assert preview.message == (
        "Dry run, nothing was run. The run would be refused as a whole: you lack the permission to "
        "change documents."
    )
    assert raised.value.message == (
        "You lack the permission to change documents, so Paperless would refuse the whole run; "
        "nothing was run."
    )
    assert raised.value.hint == "Ask an administrator for the permission change_document."


async def test_a_partial_failure_still_tells_of_mails_and_the_trash() -> None:
    setup = Setup()
    setup.flow(mail("a@example.org"), action(6))
    setup.state.workflow_fails_on = {102}

    report = await setup.run(confirm=True)

    assert report.message.endswith(f"{NOT_AGAIN}{UNSEEN} {TRASH_NOTE}")


async def test_a_child_that_is_removed_too_is_named_once() -> None:
    setup = Setup(writes=False)
    family(setup)
    setup.flow(action(2, remove_tags=[61, 62]))

    message = (await setup.asked()).message

    assert "remove tags 'Paid tag', 'Cash' (with any child tags you cannot see)." in message


async def test_removed_permissions_come_first_and_ignored_ocr_does_not() -> None:
    setup = Setup(writes=False)
    setup.flow(action(7), TAGGING, action(2, remove_all_permissions=True))

    actions = bare((await setup.run(dry_run=True)).actions)

    assert actions[0] == "remove all permissions"

    setup.flow(TAGGING, action(7))
    assert bare((await setup.run(dry_run=True)).actions)[0] == "assign tags 'e2e-inbox'"


async def test_an_empty_text_writes_no_value() -> None:
    setup = Setup(writes=False)
    setup.flow(action(1, assign_custom_fields=[1], assign_custom_fields_values={"1": ""}))

    assert (
        "custom fields 'e2e-string' (without a value, where it is missing)"
        in (await setup.asked()).message
    )


# --- the order it runs in, and fresh tags


async def test_workflows_that_run_the_same_actions_in_another_order_are_told_apart() -> None:
    setup = Setup(writes=False)
    clear, give = action(2, remove_all_tags=True), action(1, assign_tags=[INBOX])
    setup.flow(clear, give)
    first = await setup.run(dry_run=True)
    asked = await setup.asked()
    setup.flow(give, clear)
    second = await setup.run(dry_run=True)
    again = await setup.asked()

    assert first.actions == (
        "step 1 of 2: remove all tags",
        "step 2 of 2: assign tags 'e2e-inbox'",
    )
    assert second.actions == (
        "step 1 of 2: assign tags 'e2e-inbox'",
        "step 2 of 2: remove all tags",
    )
    assert asked.message != again.message


async def test_the_trash_keeps_its_number_and_comes_after_everything_else() -> None:
    setup = Setup(writes=False)
    setup.flow(action(6), mail("a@example.org"), TAGGING)

    assert (await setup.run(dry_run=True)).actions == (
        "step 1 of 3: move each of them to the trash, after everything else",
        "step 2 of 3: send an e-mail to 'a@example.org'",
        "step 3 of 3: assign tags 'e2e-inbox'",
    )


async def test_both_clauses_of_one_action_have_its_number() -> None:
    setup = Setup(writes=False)
    setup.flow(TAGGING, action(1, assign_owner=READER_ID))

    assert (await setup.run(dry_run=True)).actions == (
        "step 2 of 2: change who owns or may see the matching documents",
        "step 2 of 2: assign owner 'e2e-reader'",
        "step 1 of 2: assign tags 'e2e-inbox'",
    )


@pytest.mark.parametrize(
    "acting",
    [action(2, remove_tags=[FINANCE]), action(1, assign_tags=[FINANCE])],
    ids=["remove", "assign"],
)
async def test_tags_are_listed_anew_once_per_run(acting: WorkflowAction) -> None:
    setup = Setup(writes=False)
    setup.flow(acting)
    refusal = await setup.asked()
    before = setup.fake.listed.count("tag")
    if acting.type == 2:
        setup.state.tags.append(FakeTag(id=61, name="New child", parent=FINANCE))
    else:
        setup.state.tags = [
            replace(t, parent=INBOX) if t.id == FINANCE else t for t in setup.state.tags
        ]

    with accepting(refusal.fingerprint):
        again = await setup.asked(confirm=False)

    assert setup.fake.listed.count("tag") - before == 1
    assert again.fingerprint != refusal.fingerprint
    assert ("'New child'" if acting.type == 2 else "parent tags 'e2e-inbox'") in again.message


async def test_without_tags_in_the_actions_they_are_not_listed_anew() -> None:
    setup = Setup(writes=False)
    setup.flow(action(1, assign_correspondent=1), triggers=(FILTERED,))
    _ = await setup.asked()
    before = setup.fake.listed.count("tag")

    _ = await setup.asked()

    assert setup.fake.listed.count("tag") == before


async def test_the_children_of_two_removed_parents_are_all_named() -> None:
    setup = Setup(writes=False)
    setup.state.tags += [
        FakeTag(id=61, name="Child", parent=FINANCE),
        FakeTag(id=62, name="Other child", parent=INBOX),
    ]
    setup.flow(action(2, remove_tags=[FINANCE, INBOX]))

    assert (
        "(with their child tags 'e2e-invoices', 'Child', 'Other child' and any you cannot see)"
    ) in (await setup.asked()).message


async def test_where_permissions_are_not_listed_paperless_decides() -> None:
    setup = restricted(owner=None, writes=True)
    setup.without(READER[0], "change_document")
    setup.fake.unlisted = True

    preview = await setup.run(dry_run=True)
    with pytest.raises(PermissionDenied, match="refused to run the workflow"):
        _ = await setup.run(confirm=True)

    assert preview.blocked == ()


@pytest.mark.parametrize("listed", [True, False])
async def test_documents_that_all_have_no_owner_run(*, listed: bool) -> None:
    setup = restricted(owner=None, writes=True)
    setup.document(101).owner = None
    setup.fake.unlisted = not listed

    assert (await setup.run(confirm=True)).started


async def test_linked_documents_are_read_a_hundred_at_a_time() -> None:
    setup = Setup(writes=False)
    link = next(f.id for f in setup.state.custom_fields if f.name == "e2e-documentlink")
    linked: list[JsonValue] = [*range(5000, 5201), 101, 101]
    setup.flow(
        action(1, assign_custom_fields=[link], assign_custom_fields_values={str(link): linked})
    )

    message = (await setup.run(dry_run=True)).actions[0]

    assert [len(ids) for ids in setup.fake.titled[:3]] == [100, 100, 2]
    assert "#5200, 101 'Scan 1'" in message


# --- a confirmation of at most 100,000 characters (owner, 2026-11-24)

LIMIT = 100_000
MANY = tuple(range(1000, 1100))


def steps(count: int) -> list[WorkflowAction]:
    return [action(1, assign_title=f"{n:04}" + "y" * 50) for n in range(count)]


async def confirmed_length(setup: Setup) -> int:
    """How long a confirmation within the limit is, without the words around the effect."""
    message = (await setup.asked(document_ids=MANY)).message
    return len(message) - len("Nothing was changed. Confirming would .")


async def long_run(length: int) -> Setup:
    """A run on a hundred documents whose confirmation has `length` characters."""
    setup = Setup(writes=False, scans=0)
    setup.state.documents += [FakeDocument(id=n, title="x", owner=ADMIN_ID) for n in MANY]
    setup.flow(*steps(1050))  # a bit less than the limit
    left = length - await confirmed_length(setup)
    for number in MANY:
        more = min(left, 59)
        setup.document(number).title += "x" * more
        left -= more
    assert left == 0
    return setup


async def test_a_confirmation_of_exactly_the_limit_is_asked() -> None:
    setup = await long_run(LIMIT)

    assert await confirmed_length(setup) == LIMIT
    preview = await setup.run(dry_run=True, document_ids=MANY)
    assert not preview.incomplete
    assert len(preview.actions) == 1050


@pytest.mark.parametrize("confirm", [False, True])
async def test_one_character_more_is_refused_also_with_confirm(*, confirm: bool) -> None:
    setup = await long_run(LIMIT + 1)

    with pytest.raises(Validation) as raised:
        _ = await setup.run(confirm=confirm, document_ids=MANY)

    assert type(raised.value) is Validation  # no dialog
    message = raised.value.message
    assert message.startswith(
        "Nothing was run: the confirmation would have 100001 characters, and at most 100000 can "
    )
    assert message.endswith(" of them are the documents.")
    assert "'" not in message.replace("'Paid'", "")
    assert raised.value.hint == (
        "Choose fewer documents per call; the actions alone fit into one confirmation."
    )


async def test_an_accepted_dialog_does_not_cover_a_run_that_became_too_long() -> None:
    setup = await long_run(LIMIT)
    refusal = await setup.asked(document_ids=MANY)
    next(d for d in reversed(setup.state.documents) if len(d.title) < 59).title += "z"

    with accepting(refusal.fingerprint), pytest.raises(Validation) as raised:
        _ = await setup.run(confirm=True, document_ids=MANY)

    assert "100001 characters" in raised.value.message


async def test_a_filter_alone_gets_the_same_refusal() -> None:
    setup = await long_run(LIMIT + 1)

    with pytest.raises(Validation, match="100001 characters"):
        _ = await setup.run(confirm=True, filters={"title_contains": "x"}, exclude_ids=[])


async def test_the_sizes_of_actions_and_documents_are_told() -> None:
    setup = await long_run(LIMIT + 1)
    preview = await setup.run(dry_run=True, document_ids=MANY)
    actions = len("; ".join(preview.actions))

    with pytest.raises(Validation) as raised:
        _ = await setup.run(document_ids=MANY)

    assert f"{actions} of them are the actions, " in raised.value.message
    rows = [(number, setup.document(number).title) for number in MANY]
    assert f"{len(named(rows))} of them are the documents" in raised.value.message


async def test_where_fewer_documents_do_not_help_the_hint_says_so() -> None:
    setup = Setup(writes=False)
    setup.flow(*steps(1200))

    with pytest.raises(Validation) as raised:
        _ = await setup.run(confirm=True)

    assert raised.value.hint == (
        "Fewer documents do not help: split the workflow into several, or run it in the Paperless "
        "web UI."
    )


async def test_the_preview_of_a_run_that_is_too_long_shows_the_first_actions() -> None:
    setup = Setup(writes=False)
    setup.flow(*steps(1200))

    report = await setup.run(dry_run=True)

    shown = len("; ".join(report.actions))
    assert report.incomplete
    assert report.selected_ids == (101, 102)
    assert LIMIT - 120 < shown <= LIMIT
    assert report.actions[0].startswith("step 1 of 1200: ")
    assert report.message.startswith(
        "Dry run, nothing was run. The run would be refused: the confirmation would have "
    )
    assert "actions shows only the first " in report.message
    assert report.message.endswith("or run it in the Paperless web UI.")
    assert len(report.message) < 2000


async def test_actions_that_fit_stay_whole_where_the_documents_are_too_many() -> None:
    setup = await long_run(LIMIT + 1)

    report = await setup.run(dry_run=True, document_ids=MANY)

    assert not report.incomplete
    assert len(report.actions) == 1050
    assert "actions shows only" not in report.message
    assert report.selected_ids == MANY
    fewer = "Choose fewer documents per call; the actions alone fit into one confirmation."
    assert report.message.endswith(fewer)


async def test_missing_rights_are_told_before_the_length() -> None:
    setup = await long_run(LIMIT + 1)
    for number in MANY:
        setup.document(number).owner = ADMIN_ID
    setup.document(1000).viewers = {READER_ID}
    setup.document(1000).editors = set()
    setup.may(READER[0], "view_workflow", "change_document", "view_uisettings")
    setup.flow(*steps(1200))

    with pytest.raises(PermissionDenied, match="You may not change or do not own"):
        _ = await setup.run(confirm=True, document_ids=[1000])
