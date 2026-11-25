"""WorkflowExplanations: why a workflow ran on a document or did not."""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast, final, override

import anyio
import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeTask, seeded
from fakes.workflows import TRIGGER_DEFAULTS
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.documents import HistoryEntry
from mcp_server_paperless.domain.errors import (
    NotFound,
    PermissionDenied,
    UpstreamTimeout,
    Validation,
)
from mcp_server_paperless.domain.workflows import (
    FilterCheck,
    LogFinding,
    Workflow,
    WorkflowAction,
    WorkflowTrigger,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.workflow_explanation import (
    MAX_CHARS,
    NOTE,
    WorkflowExplanations,
    content_matches,
)
from mcp_server_paperless.services.workflows import WorkflowService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fakes.paperless import FakeDocument
    from pydantic import JsonValue

    from mcp_server_paperless.domain.documents import (
        DocumentPage,
        DocumentQuery,
        DocumentRecord,
        DocumentText,
    )
    from mcp_server_paperless.domain.taxonomy import TaxonomyItem, TaxonomyKind
    from mcp_server_paperless.domain.workflows import WorkflowExplanation

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-88")]

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
TASK = "0badcafe-1111-4222-8333-444455556666"
ADDED = datetime(2026, 11, 11, 19, 47, 30, tzinfo=UTC)
MATCHING, HANDLERS = "paperless.matching", "paperless.handlers"
TAG_ACTION = WorkflowAction(id=8, type=1, settings={"assign_tags": [4]})


def trigger(
    kind: int = 2, number: int = 7, settings: dict[str, JsonValue] | None = None, **more: JsonValue
) -> WorkflowTrigger:
    return WorkflowTrigger(
        id=number, type=kind, settings={**TRIGGER_DEFAULTS, **(settings or {}), **more}
    )


def line(
    message: str, *, at: str = "19:47:20", task: str | None = None, name: str = MATCHING
) -> str:
    prefix = f"[{task}] " if task else ""
    return f"[2026-11-11 {at},123] [INFO] [{name}] {prefix}{message}"


class Failing(FakePaperless):
    broken: bool = False
    nameless: bool = False
    pages: int = 0
    slow: float = 0.0

    @override
    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        for _ in range(20 if self.slow > 0 else 0):  # as the adapter reads page after page
            self.pages += 1
            await anyio.sleep(self.slow)
        return await super().list_taxonomy(kind)

    @override
    async def list_workflows(
        self, *, enabled: bool | None = None, trigger_type: int | None = None
    ) -> tuple[Workflow, ...]:
        for _ in range(20 if self.slow < 0 else 0):
            self.pages += 1
            await anyio.sleep(-self.slow)
        return await super().list_workflows(enabled=enabled, trigger_type=trigger_type)

    search_error: Exception | None = None

    @override
    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        if self.search_error is not None:
            raise self.search_error
        return await super().search_documents(query, page=page, page_size=page_size)

    @override
    async def get_document(self, document_id: int) -> DocumentRecord:
        found = await super().get_document(document_id)
        return found.model_copy(update={"original_file_name": None}) if self.nameless else found

    @override
    async def get_document_content(
        self, document_id: int, *, version_id: int | None = None
    ) -> DocumentText:
        if self.broken:
            message = "Paperless did not answer in time."
            raise UpstreamTimeout(message)
        return await super().get_document_content(document_id, version_id=version_id)


@final
class Setup:
    def __init__(
        self, user: str = ADMIN[0], *, grant: Sequence[str] = (), now: datetime | None = None
    ) -> None:
        self.state = seeded()
        self.state.users = [
            replace(u, permissions=u.permissions | frozenset(grant)) if u.username == user else u
            for u in self.state.users
        ]
        self.document: FakeDocument = next(d for d in self.state.documents if d.id == 1)
        self.document.added = ADDED
        self.document.history = []
        self.state.logs["paperless"] = []
        self.state.tasks = []
        self.fake = Failing(self.state, user=user)  # without `allow_writes`
        resolver = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        workflows = WorkflowService(self.fake, self.fake, resolver)
        self.times: list[float] = []
        self.service = WorkflowExplanations(
            self.fake, self.fake, workflows, resolver, clock=self._clock, now=lambda: now or NOW
        )

    def _clock(self) -> float:
        return self.times.pop(0) if self.times else 0.0

    def put(self, values: dict[str, object]) -> None:
        """Give the document these values."""
        place = self.state.documents.index(self.document)
        self.document = replace(self.document, **values)
        self.state.documents[place] = self.document

    def flow(
        self,
        *triggers: WorkflowTrigger,
        actions: Sequence[WorkflowAction] = (TAG_ACTION,),
        enabled: bool = True,
        name: str = "W",
    ) -> None:
        self.state.workflows = [
            Workflow(
                id=1, name=name, order=3, enabled=enabled, triggers=triggers, actions=tuple(actions)
            )
        ]

    def consumed(self, task_id: str = TASK, *, document: int = 1, minutes: int = -1) -> None:
        self.state.tasks.append(
            FakeTask(
                id=len(self.state.tasks) + 1,
                task_id=task_id,
                status="success",
                created=ADDED + timedelta(minutes=minutes),
                document_ids=(document,),
                trigger_source="api_upload",
                started=ADDED - timedelta(seconds=30),
                finished=ADDED + timedelta(seconds=5),
            )
        )

    async def explain(self) -> WorkflowExplanation:
        return await self.service.explain(1, 1)

    async def checks(self, *triggers: WorkflowTrigger) -> list[FilterCheck]:
        self.flow(*triggers)
        [explained] = (await self.explain()).triggers
        return list(explained.checks)


type Case = tuple[dict[str, JsonValue], dict[str, object], str, str]


def references(one: str, plural: str) -> list[Case]:
    return [
        ({f"filter_has_{one}": 1}, {one: 1}, f"has_{one}", "ok"),
        ({f"filter_has_{one}": 1}, {one: None}, f"has_{one}", "no"),
        ({f"filter_has_any_{plural}": [1]}, {one: 1}, f"has_any_{plural}", "ok"),
        ({f"filter_has_any_{plural}": [1]}, {one: None}, f"has_any_{plural}", "no"),
        ({f"filter_has_not_{plural}": [1]}, {one: None}, f"has_not_{plural}", "ok"),
        ({f"filter_has_not_{plural}": [1]}, {one: 1}, f"has_not_{plural}", "no"),
    ]


CASES: list[Case] = [
    ({"filter_has_tags": [1, 2]}, {"tags": [1]}, "has_tags", "ok"),
    ({"filter_has_tags": [1, 2]}, {"tags": [4]}, "has_tags", "no"),
    ({"filter_has_tags": [1, 2]}, {"tags": []}, "has_tags", "no"),
    ({"filter_has_all_tags": [1, 2]}, {"tags": [1, 2, 4]}, "has_all_tags", "ok"),
    ({"filter_has_all_tags": [1, 2]}, {"tags": [1]}, "has_all_tags", "no"),
    ({"filter_has_not_tags": [1]}, {"tags": [2]}, "has_not_tags", "ok"),
    ({"filter_has_not_tags": [1]}, {"tags": [1, 2]}, "has_not_tags", "no"),
    ({"filter_filename": "*INV*.pdf"}, {"file_name": "my-inv-1.PDF"}, "filename", "ok"),
    ({"filter_filename": "inv*"}, {"file_name": "my-inv-1.pdf"}, "filename", "no"),
    *references("correspondent", "correspondents"),
    *references("document_type", "document_types"),
    *references("storage_path", "storage_paths"),
]


@pytest.mark.parametrize("kind", [2, 3, 5])
@pytest.mark.parametrize(
    ("settings", "values", "name", "result"),
    CASES,
    ids=[f"{c[2]}-{c[3]}-{n}" for n, c in enumerate(CASES)],
)
async def test_every_filter_is_compared_with_the_document(
    settings: dict[str, JsonValue],
    values: dict[str, object],
    name: str,
    result: str,
    kind: int,
) -> None:
    setup = Setup()
    setup.put(values)

    [check] = await setup.checks(trigger(kind, settings=settings))
    answer = await setup.explain()

    assert (check.filter, check.result) == (name, result)
    assert answer.triggers[0].matches == {"ok": "yes", "no": "no"}[result]
    assert answer.would_match_now == answer.triggers[0].matches
    assert answer.did_run == "unknown"


async def test_the_checks_stand_in_the_order_of_paperless() -> None:
    setup = Setup()
    every: dict[str, JsonValue] = {
        "filter_filename": "x",
        "filter_custom_field_query": '["e2e-string","exists",true]',
        "filter_has_tags": [1],
        "filter_has_all_tags": [1],
        "filter_has_not_tags": [1],
        "match": "x",
        "matching_algorithm": 1,
    }
    for single, several in (
        ("correspondent", "correspondents"),
        ("document_type", "document_types"),
        ("storage_path", "storage_paths"),
    ):
        every |= {
            f"filter_has_{single}": 1,
            f"filter_has_any_{several}": [1],
            f"filter_has_not_{several}": [1],
        }
    checks = await setup.checks(trigger(settings=every))

    assert [c.filter for c in checks] == [
        *("match", "has_tags", "has_all_tags", "has_not_tags"),
        *("has_any_correspondents", "has_correspondent", "has_not_correspondents"),
        *("has_any_document_types", "has_document_type", "has_not_document_types"),
        *("has_any_storage_paths", "has_storage_path", "has_not_storage_paths"),
        *("custom_field_query", "filename"),
    ]


@pytest.mark.parametrize(
    ("match", "algorithm", "ignore_case", "text", "found"),
    [
        ("invoice", 1, True, "An INVOICE here", True),
        ("invoice", 1, False, "An INVOICE here", False),
        ("other invoice", 1, True, "an invoice", True),
        ("invoice", 1, True, "invoices", False),
        ("an invoice", 2, True, "an old invoice", True),
        ("an bill", 2, True, "an old invoice", False),
        ('"old   invoice"', 2, True, "an old\n invoice", True),
        ("a.c", 1, True, "abc", False),
        ("old invoice", 3, True, "an old invoice", True),
        ("old invoice", 3, True, "an old  invoice", False),
        ("  ", 1, True, "anything", False),
        ("x", 4, True, "x", None),
        ("x", 5, True, "x", None),
        ("x", 9, True, "x", None),
    ],
)
def test_content_is_matched_as_paperless_does(
    match: str, algorithm: int, text: str, *, ignore_case: bool, found: bool | None
) -> None:
    assert content_matches(text, match, algorithm, ignore_case=ignore_case) is found


async def test_the_text_of_the_document_is_compared_and_never_shown() -> None:
    setup = Setup()
    setup.document.content = "secret words here"

    [found] = await setup.checks(trigger(match="words", matching_algorithm=1))
    [missed] = await setup.checks(trigger(match="other", matching_algorithm=2))
    [regex] = await setup.checks(trigger(match="(a+)+$", matching_algorithm=4))
    [other_case] = await setup.checks(
        trigger(match="WORDS", matching_algorithm=1, is_insensitive=False)
    )
    [any_case] = await setup.checks(trigger(match="WORDS", matching_algorithm=1))
    assert (other_case.result, any_case.result) == ("no", "ok")
    setup.fake.broken = True
    [unread] = await setup.checks(trigger(match="words", matching_algorithm=3))
    answer = await setup.explain()

    assert [c.result for c in (found, missed, regex, unread)] == ["ok", "no", "unknown", "unknown"]
    assert regex.why == "regex is not checked here"
    assert "the text: Paperless did not answer in time." in answer.not_checked
    assert answer.would_match_now == "unknown"
    assert "secret" not in answer.model_dump_json()


async def test_the_text_is_not_read_for_a_consumption_trigger() -> None:
    setup = Setup()
    setup.fake.broken = True
    setup.flow(trigger(1, match="x", matching_algorithm=1, filter_filename="a"))

    assert "the text" not in str((await setup.explain()).not_checked)


async def test_a_custom_field_query_is_asked_of_paperless() -> None:
    setup = Setup()
    setup.document.custom_fields = {1: "x"}

    for query, result in (
        ('["e2e-string", "exact", "x"]', "ok"),
        ('["e2e-string", "exact", "y"]', "no"),
        ('["no such field", "exists", true]', "no"),
        ("{", "no"),
    ):
        [check] = await setup.checks(trigger(filter_custom_field_query=query))
        assert (check.filter, check.result) == ("custom_field_query", result)
    setup.times = [0.0, 0.0, 99.0, 99.0, 99.0, 99.0]
    [late] = await setup.checks(trigger(filter_custom_field_query="[]"))
    assert late.result == "unknown"


async def test_what_a_consumption_trigger_saw_is_mostly_unknown() -> None:
    setup = Setup()
    setup.document.file_name = "a.pdf"
    settings: dict[str, JsonValue] = {
        "sources": [1, 4],
        "filter_mailrule": 3,
        "filter_filename": "a*",
        "filter_path": "/in/*",
    }
    setup.consumed()

    checks = await setup.checks(trigger(1, settings=settings))
    other = await setup.checks(trigger(1, sources=[], filter_filename="b*"))

    assert [(c.filter, c.result) for c in checks] == [
        ("sources", "unknown"),
        ("mailrule", "unknown"),
        ("filename", "unknown"),
        ("path", "unknown"),
    ]
    assert checks[0].why == (
        "where it came from (it came by api_upload) has to be consume_folder, web_ui"
    )
    assert [(c.filter, c.result) for c in other] == [("filename", "unknown")]
    assert other[0].why == (
        "the stored file name 'a.pdf' does not match 'b*', but the name it was consumed under may "
        "have been another one"
    )


async def test_a_document_without_a_file_name_passes_but_not_a_schedule() -> None:
    setup = Setup()
    setup.fake.nameless = True

    [added] = await setup.checks(trigger(filter_filename="a"))
    scheduled = await setup.checks(trigger(4, filter_filename="a"))

    assert (added.result, added.why) == ("ok", "it has no original file name")
    assert [c.result for c in scheduled if c.filter == "filename"] == ["no"]


@pytest.mark.parametrize(
    ("settings", "result"),
    [
        ({"schedule_date_field": "added", "schedule_offset_days": 3}, "no"),
        ({"schedule_date_field": "added", "schedule_offset_days": -3}, "ok"),
        ({"schedule_date_field": "modified", "schedule_offset_days": 0}, "ok"),
        ({"schedule_date_field": "created", "schedule_offset_days": 400}, "no"),
        ({"schedule_date_field": "created", "schedule_offset_days": 2}, "ok"),
        ({"schedule_date_field": "custom_field", "schedule_date_custom_field": 3}, "ok"),
        (
            {
                "schedule_date_field": "custom_field",
                "schedule_date_custom_field": 3,
                "schedule_offset_days": 100,
            },
            "no",
        ),
        ({"schedule_date_field": "custom_field", "schedule_date_custom_field": 4}, "no"),
        ({"schedule_date_field": "custom_field", "schedule_date_custom_field": 5}, "no"),
        ({"schedule_date_field": "custom_field", "schedule_date_custom_field": 1}, "no"),
    ],
)
async def test_a_schedule_is_compared_by_its_date(
    settings: dict[str, JsonValue], result: str
) -> None:
    setup = Setup()
    setup.document.added = NOW - timedelta(days=1)
    setup.document.modified = NOW - timedelta(hours=1)
    setup.document.created = NOW.date() - timedelta(days=30)
    old = (NOW - timedelta(days=500)).date().isoformat()
    setup.document.custom_fields = {3: NOW.date().isoformat(), 4: old, 1: "no date"}

    once = await setup.checks(trigger(4, settings=settings))
    first = await setup.explain()
    again = await setup.checks(trigger(4, settings=settings, schedule_is_recurring=True))
    second = await setup.explain()

    for checks in (once, again):
        assert (checks[0].filter, checks[0].result) == ("schedule_date_field", result)
    assert [(c.filter, c.result) for c in once[1:]] == [("schedule_is_recurring", "unknown")]
    assert [(c.filter, c.result) for c in again[1:]] == [
        ("schedule_recurring_interval_days", "unknown")
    ]
    wanted = {"ok": "unknown", "no": "no"}[result]
    assert [a.triggers[0].matches for a in (first, second)] == [wanted, wanted]


async def test_the_last_change_of_a_value_is_told_by_the_history() -> None:
    setup = Setup()
    setup.document.tags = [4]

    def entry(number: int, day: int, actor: str | None, **changes: JsonValue) -> HistoryEntry:
        return HistoryEntry(
            id=number,
            timestamp=datetime(2026, 11, day, tzinfo=UTC),
            action="update",
            actor=actor,
            changes=dict(changes),
        )

    setup.document.history = [
        entry(3, 22, None, tags={"type": "m2m"}),
        entry(2, 21, "bob", tags={"type": "m2m"}, correspondent=["None", "x"]),
    ]
    tags, correspondent, name = await setup.checks(
        trigger(filter_has_tags=[4], filter_has_correspondent=1, filter_filename="*")
    )

    assert tags.last_change == (
        "2026-11-22T00:00:00+00:00 by nobody (a workflow, automatic matching or the system)"
    )
    assert correspondent.last_change == "2026-11-21T00:00:00+00:00 by 'bob'"
    assert name.last_change is None


@pytest.mark.parametrize(
    ("changes", "wanted"),
    [
        ({"content": ["old OCTOPUS", "new ZEBRA"]}, "2026-11-22T00:00:00+00:00 by 'bob'"),
        ({"Version Added": ["None", 7]}, "2026-11-22T00:00:00+00:00 by 'bob'"),
        ({"title": ["a", "b"]}, None),
    ],
)
@pytest.mark.parametrize("match", ["ACME", "absent"])
async def test_the_last_change_of_the_text_is_told_without_the_text(
    changes: dict[str, JsonValue], wanted: str | None, match: str
) -> None:
    setup = Setup()
    setup.document.history = [
        HistoryEntry(
            id=3,
            timestamp=datetime(2026, 11, 22, tzinfo=UTC),
            action="update",
            actor="bob",
            changes=changes,
        )
    ]

    [check] = await setup.checks(trigger(matching_algorithm=1, match=match))
    answer = await setup.explain()

    assert (check.filter, check.last_change) == ("match", wanted)
    assert "OCTOPUS" not in repr(answer)
    assert "ZEBRA" not in repr(answer)


async def test_names_the_user_may_not_see_are_numbers() -> None:
    setup = Setup(READER[0], grant=("view_workflow",))
    setup.document.tags = [3, 1]

    [check] = await setup.checks(trigger(filter_has_tags=[3]))

    assert check.result == "ok"
    assert "#3" in check.why
    assert "e2e-private" not in check.why
    assert "'e2e-finance'" in check.why


async def test_without_the_right_to_list_tags_they_are_numbers() -> None:
    setup = Setup(READER[0], grant=("view_workflow",))
    user = setup.fake.user
    setup.fake.user = replace(user, permissions=user.permissions - {"view_tag"})
    setup.document.tags = [1]

    [check] = await setup.checks(trigger(filter_has_tags=[1]))

    assert check.why == "it has the tags #1; wanted is one of #1"


async def test_what_may_not_be_read_is_named_and_the_rest_is_told() -> None:
    setup = Setup(READER[0], grant=("view_workflow",))
    setup.flow(trigger())

    answer = await setup.explain()

    assert answer.note == NOTE
    assert [text.split(":")[0] for text in answer.not_checked] == [
        "the history (needs the right to view the audit log)",
        "the consume task (needs the right to view tasks)",
        "the log (needs a staff user)",
    ]
    assert answer.would_match_now == "yes"
    with pytest.raises(PermissionDenied):
        _ = await Setup(READER[0]).service.explain(1, 1)
    with pytest.raises(NotFound):
        _ = await setup.service.explain(1, 999)
    with pytest.raises(NotFound):
        _ = await setup.service.explain("nope", 1)


async def test_no_source_is_asked_after_the_time_is_up() -> None:
    setup = Setup()
    setup.flow(trigger())
    setup.times = [0.0, 0.0, 1.0, 40.0, 41.0]

    answer = await setup.explain()

    assert answer.not_checked == (
        "the consume task (needs the right to view tasks): the time was up",
        "the log (needs a staff user): the time was up",
    )


MATCHED = "Document matched WorkflowTrigger 7 from Workflow: W"
APPLIED = "Applying WorkflowAction 8 from Workflow: W"
MISSED = "Document did not match Workflow: W"


async def test_the_log_is_this_document_s_only_by_its_consume_task() -> None:
    setup = Setup()
    setup.consumed()
    setup.consumed("ffffffff-1111-4222-8333-444455556666", document=2)
    setup.flow(trigger())
    setup.state.logs["paperless"] = [
        line("Rest of something", at="10:00:00"),
        line(MISSED, task="0badcafe"),
        line("Document filename secret-name.pdf does not match x", task="0badcafe"),
        line(MATCHED, task="0badcafe"),
        line(APPLIED, task="0badcafe", name=HANDLERS),
        line(MATCHED, task="ffffffff"),
        line(MATCHED, at="19:48:30"),
        line(MATCHED, at="19:59:00"),
        line("Document matched WorkflowTrigger 99 from Workflow: W", at="19:48:30"),
        line(MATCHED + "2", at="19:48:30"),
    ]

    answer = await setup.explain()

    assert answer.log == (
        LogFinding(
            time="2026-11-11 19:47:20",
            said="the document did not match: the file name does not match",
            whose="likely this document",
        ),
        LogFinding(
            time="2026-11-11 19:47:20",
            said="the document matched trigger 7",
            whose="likely this document",
        ),
        LogFinding(
            time="2026-11-11 19:47:20", said="action 8 was applied", whose="likely this document"
        ),
        LogFinding(
            time="2026-11-11 19:48:30",
            said="the document matched trigger 7",
            whose="perhaps this document",
        ),
    )
    assert (answer.did_run, answer.log_from) == ("likely", "2026-11-11 10:00:00")
    assert answer.message.endswith("who may upload or change documents can influence.")
    assert "secret-name" not in answer.model_dump_json()


async def test_without_the_task_nothing_is_certain() -> None:
    setup = Setup()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [line(MATCHED, task="0badcafe"), line(MATCHED)]

    answer = await setup.explain()

    assert [f.whose for f in answer.log] == ["perhaps this document"]
    assert answer.did_run == "unknown"
    assert answer.not_checked == ("the consume task of the document was not found",)
    assert answer.message.endswith("Whether it ran is not known.")


async def test_a_record_of_not_matching_is_no_run() -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [line(MISSED, task="0badcafe")]

    answer = await setup.explain()

    assert answer.did_run == "unknown"
    assert answer.log[0].said == "the document did not match"


async def test_the_time_zone_of_paperless_is_that_of_its_timestamps() -> None:
    setup = Setup()
    zone = timezone(timedelta(hours=1))
    setup.document.added = ADDED.astimezone(zone)
    setup.flow(trigger())
    setup.state.logs["paperless"] = [line(MATCHED, at="20:47:40"), line(MATCHED, at="19:47:40")]

    answer = await setup.explain()

    assert [f.time for f in answer.log] == ["2026-11-11 20:47:40"]


async def test_records_near_a_change_of_the_document_are_perhaps_its_own() -> None:
    setup = Setup()
    setup.document.history = [
        HistoryEntry(
            id=1,
            timestamp=datetime(2026, 11, 11, 21, 0, tzinfo=UTC),
            action="update",
            actor=None,
            changes={},
        )
    ]
    setup.flow(trigger(3))
    setup.state.logs["paperless"] = [line(MISSED, at="21:01:30"), line(MISSED, at="21:02:30")]

    assert [f.time for f in (await setup.explain()).log] == ["2026-11-11 21:01:30"]


REASONS = [
    ("Document source ApiUpload not in ['MailFetch']", "the source does not fit"),
    ("Document mail rule None != 3", "the mail rule does not fit"),
    ("Document filename P does not match x", "the file name does not match"),
    ("Document path P does not match x", "the path does not match"),
    ("Document content matching settings for algorithm '1' did not match", "the content does"),
    ("Document tags [P] do not include [P]", "the tags do not fit"),
    ("Document tags [P] do not contain all of [P]", "the tags do not fit"),
    ("Document tags [P] include excluded tags [P]", "the tags do not fit"),
    ("Document correspondent P is excluded by [P]", "the correspondent does not fit"),
    ("Document correspondent P is not one of [P]", "the correspondent does not fit"),
    ("Document correspondent P does not match P", "the correspondent does not fit"),
    ("Document doc type P is excluded by [P]", "the document type does not fit"),
    ("Document doc type P is not one of [P]", "the document type does not fit"),
    ("Document doc type P does not match P", "the document type does not fit"),
    ("Document storage path P is excluded by [P]", "the storage path does not fit"),
    ("Document storage path P is not one of [P]", "the storage path does not fit"),
    ("Document storage path P does not match P", "the storage path does not fit"),
    ("Document custom fields do not match the configured custom field query", "the custom field"),
    ("Invalid custom field query configuration", "the custom field query is not valid"),
    ("No matching triggers with type 2 found", "it has no trigger for this event"),
    ("Something new about P", ""),
    ("Document correspondent P", ""),
    ("Document tags P", ""),
    ("Invalid custom field query configuration of P", ""),
    ("No matching triggers with type P found", ""),
    ("A Document filename P does not match x", ""),
    ("Document content matching settings for algorithm '1' did not match P", ""),
]


@pytest.mark.parametrize(("reason", "said"), REASONS, ids=[str(n) for n in range(len(REASONS))])
async def test_reasons_are_told_without_names(reason: str, said: str) -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    planted = reason.replace("P", "planted\nname")
    setup.state.logs["paperless"] = [
        line(MISSED, task="0badcafe"),
        *line(planted, task="0badcafe").split("\n"),
    ]

    answer = await setup.explain()

    [finding] = answer.log
    assert finding.said.startswith("the document did not match" + (f": {said}" if said else ""))
    if not said:
        assert finding.said == "the document did not match"
    assert "planted" not in answer.model_dump_json()


async def test_a_reason_of_another_task_or_logger_is_not_taken() -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    reason = "Document correspondent x does not match y"
    for after in (line(reason), line(reason, task="0badcafe", name=HANDLERS)):
        setup.state.logs["paperless"] = [line(MISSED, task="0badcafe"), after]
        assert (await setup.explain()).log[0].said == "the document did not match"


async def test_only_the_last_findings_are_told() -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [
        line(MATCHED, at=f"19:47:{n + 2:02d}", task="0badcafe") for n in range(30)
    ]

    found = (await setup.explain()).log

    assert (len(found), found[-1].time) == (20, "2026-11-11 19:47:31")


@pytest.mark.parametrize(
    ("triggers", "actions", "enabled", "verdict", "said"),
    [
        ((trigger(),), (TAG_ACTION,), False, "no", "is not enabled, so it does not run."),
        ((), (TAG_ACTION,), True, "no", "has no trigger, so it does not run."),
        ((trigger(),), (), True, "yes", "has no action, so it does nothing."),
        ((trigger(),), (TAG_ACTION,), True, "yes", "matches the document as both are now."),
        (
            (trigger(filter_has_tags=[99]),),
            (TAG_ACTION,),
            True,
            "no",
            "First reason: has_tags: it has the tags",
        ),
        ((trigger(match="x", matching_algorithm=4),), (TAG_ACTION,), True, "unknown", "may match"),
        ((trigger(5),), (TAG_ACTION,), True, "yes", "It runs only when a user runs it by hand."),
        ((trigger(99),), (TAG_ACTION,), True, "unknown", "may match"),
        (
            (trigger(filter_has_tags=[99]), trigger(5, 9)),
            (TAG_ACTION,),
            True,
            "yes",
            "matches the document as both are now.",
        ),
    ],
)
async def test_the_message_says_what_stands_in_the_way(
    triggers: tuple[WorkflowTrigger, ...],
    actions: tuple[WorkflowAction, ...],
    verdict: str,
    said: str,
    *,
    enabled: bool,
) -> None:
    setup = Setup()
    setup.flow(*triggers, actions=actions, enabled=enabled)

    answer = await setup.explain()

    assert answer.would_match_now == verdict
    assert said in answer.message
    assert answer.message.startswith("The workflow 'W' (id 1) ")


async def test_the_answer_is_bounded_and_says_what_is_left_out() -> None:
    setup = Setup()
    many = cast("list[JsonValue]", list(range(1, 200)))
    filters: dict[str, JsonValue] = {
        f"filter_{n}_tags": many for n in ("has", "has_all", "has_not")
    }
    setup.flow(*(trigger(2, n, filters) for n in range(1, 200)))

    answer = await setup.explain()

    assert len(json.dumps(answer.model_dump(mode="json"))) <= MAX_CHARS
    assert 0 < len(answer.triggers) < 199
    gone = 199 - len(answer.triggers)
    assert answer.not_checked[-1] == (
        f"{gone} of the triggers are left out: the answer would be too long"
    )
    assert answer.triggers[0].checks[0].why.endswith("#8, #9, #10 and 189 more")


WORDS = st.text(alphabet='ab c"\n.', max_size=12)


@given(text=WORDS, match=WORDS, algorithm=st.integers(1, 3), ignore_case=st.booleans())
def test_content_matching_is_that_of_the_paperless_source(
    text: str, match: str, algorithm: int, *, ignore_case: bool
) -> None:
    """Against a copy of `matches` and `_split_match` of 3.5.1."""
    flags = re.IGNORECASE if ignore_case else 0
    terms = [
        re.escape(re.sub(r"\s+", " ", (t[0] or t[1]).strip())).replace(r"\ ", r"\s+")
        for t in cast("list[tuple[str, str]]", re.findall(r'"([^"]+)"|(\S+)', match))
    ]
    if not match.strip():
        expected = False
    elif algorithm == 3:
        expected = bool(re.search(rf"\b{re.escape(match)}\b", text, flags))
    else:
        hits = [bool(re.search(rf"\b{term}\b", text, flags)) for term in terms]
        expected = any(hits) if algorithm == 1 else all(hits)

    assert content_matches(text, match, algorithm, ignore_case=ignore_case) is expected


FORGED = [
    line(MATCHED, task="0badcafe", name="paperless.mail"),
    line(MATCHED, task="0badcafe", name=HANDLERS),
    line(APPLIED, task="0badcafe"),
    line(APPLIED, task="0badcafe", name="paperless.tasks"),
    line(MISSED, task="0badcafe", name="paperless.consumer"),
    line(f"Consuming {MATCHED}", task="0badcafe"),
    line(f"{MATCHED} Private", task="0badcafe"),
    line(f"{MISSED} Private", task="0badcafe"),
    line(f"{APPLIED} Private", task="0badcafe", name=HANDLERS),
    line(MATCHED.lower(), task="0badcafe"),
    line("Document matched WorkflowTrigger 77 from Workflow: W", task="0badcafe"),
    line(f"{MATCHED}\nplanted", task="0badcafe"),
    line(MATCHED, task="ffffffff"),
]


@pytest.mark.parametrize("forged", FORGED, ids=[str(n) for n in range(len(FORGED))])
async def test_a_line_that_only_looks_like_one_is_not_taken(forged: str) -> None:
    setup = Setup()
    setup.consumed()
    setup.consumed("ffffffff-1111-4222-8333-444455556666", document=2)
    setup.flow(trigger())
    setup.state.logs["paperless"] = forged.split("\n")

    answer = await setup.explain()

    assert answer.log == ()
    assert answer.did_run == "unknown"
    assert "planted" not in answer.model_dump_json()


async def test_a_record_start_in_foreign_text_without_the_task_is_at_most_perhaps() -> None:
    """A file name may hold a line break and a whole record; nobody knows a task id beforehand."""
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [
        line("Consuming planted", task="0badcafe", name="paperless.consumer"),
        line(MATCHED),
    ]

    answer = await setup.explain()

    assert [f.whose for f in answer.log] == ["perhaps this document"]
    assert answer.did_run == "unknown"


async def test_a_workflow_without_a_name_takes_no_record_of_another() -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger(), name="")
    setup.state.logs["paperless"] = [
        line(MISSED, task="0badcafe"),
        line(MATCHED, task="0badcafe"),
    ]

    assert (await setup.service.explain(1, 1)).log == ()


async def test_the_reason_is_taken_only_from_the_record_that_follows() -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [
        line(MISSED, task="0badcafe"),
        line("something else", task="0badcafe"),
        line("Document correspondent x does not match y", task="0badcafe"),
    ]

    assert (await setup.explain()).log[0].said == "the document did not match"


# --- review of #161

ZURICH_WINTER, ZURICH_SUMMER = timezone(timedelta(hours=1)), timezone(timedelta(hours=2))


def changed(at: datetime) -> HistoryEntry:
    return HistoryEntry(id=1, timestamp=at, action="update", actor=None, changes={"title": []})


@pytest.mark.parametrize(
    ("added", "modified", "change", "near", "far"),
    [
        # Winter: the log is one hour ahead of the history, which comes in UTC.
        (
            "2026-01-10T11:00:00+01:00",
            "2026-01-12T09:00:00+01:00",
            "2026-01-12T08:00:00+00:00",
            "2026-01-12 09:01:00",
            "2026-01-12 08:01:00",
        ),
        # Summer: two hours.
        (
            "2026-07-10T11:00:00+02:00",
            "2026-07-12T10:00:00+02:00",
            "2026-07-12T08:00:00+00:00",
            "2026-07-12 10:01:00",
            "2026-07-12 08:01:00",
        ),
        # Added in winter, changed in summer, last modified in summer.
        (
            "2026-01-10T11:00:00+01:00",
            "2026-07-12T10:00:00+02:00",
            "2026-07-12T08:00:00+00:00",
            "2026-07-12 10:01:00",
            "2026-07-12 07:01:00",
        ),
        # Added and last modified in winter, changed in summer: an hour is allowed for.
        (
            "2026-01-10T11:00:00+01:00",
            "2026-12-01T09:00:00+01:00",
            "2026-07-12T08:00:00+00:00",
            "2026-07-12 10:01:00",
            "2026-07-12 11:01:00",
        ),
        (
            "2026-11-10T11:00:00+00:00",
            "2026-11-12T08:00:00+00:00",
            "2026-11-12T08:00:00+00:00",
            "2026-11-12 08:01:00",
            "2026-11-12 09:01:00",
        ),
    ],
    ids=["winter", "summer", "both", "unseen-summer", "utc"],
)
async def test_the_history_in_utc_is_compared_in_the_time_zone_of_paperless(
    added: str, modified: str, change: str, near: str, far: str
) -> None:
    setup = Setup()
    setup.put(
        {
            "added": datetime.fromisoformat(added),
            "modified": datetime.fromisoformat(modified),
        }
    )
    setup.document.history = [changed(datetime.fromisoformat(change))]
    setup.flow(trigger(3))
    setup.state.logs["paperless"] = [
        f"[{moment},000] [INFO] [{MATCHING}] {MISSED}" for moment in (near, far)
    ]

    assert [f.time for f in (await setup.explain()).log] == [near]


@pytest.mark.parametrize("hours", [1, 2])
async def test_the_task_window_is_compared_in_the_time_zone_of_the_task(hours: int) -> None:
    setup = Setup()
    zone = timezone(timedelta(hours=hours))
    setup.document.added = ADDED.astimezone(zone)
    setup.state.tasks = [
        FakeTask(
            id=1,
            task_id=TASK,
            status="success",
            created=(ADDED - timedelta(minutes=1)).astimezone(zone),
            started=(ADDED - timedelta(seconds=30)).astimezone(zone),
            finished=(ADDED + timedelta(seconds=5)).astimezone(zone),
            document_ids=(1,),
        )
    ]
    setup.flow(trigger())
    local = f"{19 + hours}:47:20"
    setup.state.logs["paperless"] = [line(MATCHED, at=local, task="0badcafe")]

    answer = await setup.explain()

    assert [f.whose for f in answer.log] == ["likely this document"]
    assert answer.did_run == "likely"


@pytest.mark.parametrize("at", ["19:46:30", "19:47:50", "20:47:20"])
async def test_a_record_outside_the_time_of_the_task_is_not_its_own(at: str) -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [line(MATCHED, at=at, task="0badcafe")]

    answer = await setup.explain()

    assert (answer.log, answer.did_run) == ((), "unknown")


async def test_every_consume_task_of_the_document_counts() -> None:
    setup = Setup()
    setup.consumed("11111111-1111-4222-8333-444455556666", minutes=-2)
    setup.consumed()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [
        line(MATCHED, task="11111111"),
        line(MATCHED, task="0badcafe"),
        line(MATCHED, task="22222222"),
    ]

    assert len((await setup.explain()).log) == 2


@pytest.mark.parametrize(
    "values",
    [
        {"task_type": "mail_fetch"},
        {"document_ids": (2,)},
        {"created": ADDED - timedelta(days=2)},
        {"created": ADDED + timedelta(minutes=5)},
        {"started": None},
    ],
    ids=["type", "document", "older", "later", "never started"],
)
async def test_only_the_consume_tasks_before_the_document_count(values: dict[str, object]) -> None:
    setup = Setup()
    setup.consumed()
    setup.state.tasks = [replace(setup.state.tasks[0], **values)]
    setup.flow(trigger())
    setup.state.logs["paperless"] = [line(MATCHED, task="0badcafe")]

    assert (await setup.explain()).did_run == "unknown"


async def test_a_task_that_has_not_ended_counts_until_now() -> None:
    setup = Setup()
    setup.consumed()
    setup.state.tasks = [replace(setup.state.tasks[0], finished=None)]
    setup.flow(trigger())
    setup.state.logs["paperless"] = [line(MATCHED, task="0badcafe")]

    assert (await setup.explain()).did_run == "likely"


async def test_more_tasks_history_and_findings_than_are_read_are_named() -> None:
    setup = Setup()
    for number in range(101):
        setup.consumed(f"{number:08x}-1111-4222-8333-444455556666", document=2)
    setup.document.history = [
        changed(ADDED + timedelta(minutes=number)).model_copy(update={"id": number})
        for number in range(60)
    ]
    setup.flow(trigger())
    setup.state.logs["paperless"] = [
        line(MATCHED, at=f"19:{47 + n // 60:02d}:{n % 60:02d}") for n in range(25)
    ]

    answer = await setup.explain()

    assert "10 older entries of the history are not looked at" in answer.not_checked
    assert "only the newest 100 consume tasks of that day were looked at" in answer.not_checked
    assert "5 findings of the log are left out" in answer.not_checked
    assert len(answer.log) == 20


async def test_the_likely_findings_come_first_and_count_before_the_cut() -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [
        line(MATCHED, at="19:47:10", task="0badcafe"),
        *(line(MISSED, at="19:47:30") for _ in range(30)),
    ]

    answer = await setup.explain()

    assert answer.did_run == "likely"
    assert answer.log[0].whose == "likely this document"
    assert len(answer.log) == 20
    assert "11 findings of the log are left out" in answer.not_checked


async def test_a_huge_offset_is_unknown_and_named() -> None:
    for offset in (10**12, -(10**12), 10**7):
        setup = Setup()
        [check, *_] = await setup.checks(trigger(4, schedule_offset_days=offset))
        answer = await setup.explain()

        assert (check.filter, check.result) == ("schedule_date_field", "unknown")
        assert answer.would_match_now == "unknown"
        assert "1 schedule: the offset is too large" in answer.not_checked


NOON = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)  # 12:00 in Zurich


@pytest.mark.parametrize(
    ("now", "day", "offset", "result"),
    [
        (NOON, "2026-07-12", 0, "ok"),
        (NOON, "2026-07-13", 0, "no"),
        (NOON, "2026-07-10", 2, "ok"),
        (NOON, "2026-07-10", 3, "no"),
        (NOON, "2025-07-12", 0, "ok"),
        (NOON, "2025-07-11", 0, "no"),
        # 22:30 UTC is 00:30 of the next day in Zurich, and an hour before it could be yesterday.
        (datetime(2026, 7, 12, 22, 30, tzinfo=UTC), "2026-07-13", 0, "unknown"),
        (datetime(2026, 7, 12, 22, 30, tzinfo=UTC), "2026-07-12", 0, "ok"),
        (datetime(2026, 7, 12, 23, 30, tzinfo=UTC), "2026-07-13", 0, "ok"),
        (datetime(2026, 7, 12, 22, 30, tzinfo=UTC), "2025-07-12", 0, "unknown"),
    ],
)
async def test_dates_are_compared_as_days_of_paperless(
    now: datetime, day: str, offset: int, result: str
) -> None:
    setup = Setup(now=now)
    setup.put({"added": NOON.astimezone(ZURICH_SUMMER), "modified": NOON.astimezone(ZURICH_SUMMER)})
    setup.document.custom_fields = {3: day}
    by_field: dict[str, JsonValue] = {
        "schedule_date_field": "custom_field",
        "schedule_date_custom_field": 3,
        "schedule_offset_days": offset,
    }
    [field, *_] = await setup.checks(trigger(4, settings=by_field))
    setup.document.created = datetime.fromisoformat(day).date()
    [created, *_] = await setup.checks(
        trigger(4, schedule_date_field="created", schedule_offset_days=offset)
    )

    assert field.result == result
    if not day.startswith("2025"):
        assert created.result == result


async def test_the_checks_of_a_schedule_stand_in_its_own_order() -> None:
    setup = Setup()
    checks = await setup.checks(
        trigger(
            4,
            match="x",
            matching_algorithm=1,
            filter_has_tags=[1],
            filter_filename="x",
            filter_custom_field_query='["e2e-string","exists",true]',
        )
    )

    assert [c.filter for c in checks] == [
        *("schedule_date_field", "has_tags", "custom_field_query", "filename"),
        *("schedule_is_recurring", "match"),
    ]


async def test_a_query_paperless_did_not_judge_is_unknown() -> None:
    for error, result in (
        (Validation("Paperless rejected the request: custom_field_query: 0: no field."), "no"),
        (Validation("Paperless rejected the request."), "unknown"),
        (Validation("Paperless rejected the request: page_size: too large"), "unknown"),
        (PermissionDenied("no"), "unknown"),
        (UpstreamTimeout("late"), "unknown"),
    ):
        setup = Setup()
        setup.fake.search_error = error
        [check] = await setup.checks(trigger(filter_custom_field_query='["a","exists",true]'))
        named = [t for t in (await setup.explain()).not_checked if "custom field query" in t]
        assert check.result == result, error
        assert bool(named) == (result == "unknown"), error


@pytest.mark.parametrize("query", ["null", "true", "3", '"x"', "{}", "[]"])
async def test_a_query_that_is_no_expression_matches_nothing(query: str) -> None:
    setup = Setup()

    [check] = await setup.checks(trigger(filter_custom_field_query=query))

    assert (check.filter, check.result) == ("custom_field_query", "no")


async def test_a_missing_is_insensitive_ignores_case_as_paperless_does() -> None:
    setup = Setup()
    setup.document.content = "Words"
    plain = {k: v for k, v in TRIGGER_DEFAULTS.items() if k != "is_insensitive"}
    bare = WorkflowTrigger(
        id=7, type=2, settings={**plain, "match": "words", "matching_algorithm": 1}
    )

    [check] = await setup.checks(bare)

    assert check.result == "ok"


async def test_a_blank_match_matches_nothing() -> None:
    setup = Setup()
    setup.document.content = "words"

    for algorithm in (1, 2, 3):
        [check] = await setup.checks(trigger(match=" ", matching_algorithm=algorithm))
        assert check.result == "no"


async def test_names_are_quoted_and_cut() -> None:
    setup = Setup()
    setup.state.tags[0].name = "it's \u202e" + "x" * 100
    setup.document.tags = [1]
    setup.document.file_name = "y" * 200
    setup.flow(trigger(filter_has_tags=[1], filter_filename="z" * 200), name="w" * 200)

    answer = await setup.explain()

    text = answer.model_dump_json(exclude={"workflow"})
    assert "x" * 61 not in text
    assert "y" * 61 not in text
    assert "z" * 61 not in text
    assert "w" * 61 not in text
    assert "\u202e" not in text


async def test_the_lists_of_names_are_read_within_the_time_and_named() -> None:
    setup = Setup()
    setup.document.tags = [1]
    setup.times = [0.0, 0.0, 40.0]
    late = await setup.checks(trigger(filter_has_tags=[1]))
    setup.times = [0.0, 0.0, 40.0]
    answer = await setup.explain()
    denied = Setup(READER[0], grant=("view_workflow",))
    user = denied.fake.user
    denied.fake.user = replace(user, permissions=user.permissions - {"view_tag"})
    denied.document.tags = [1]
    denied.flow(trigger(filter_has_tags=[1]))
    without = await denied.explain()

    assert "#1" in late[0].why
    assert "the names of tags: the time was up" in answer.not_checked
    assert any(text.startswith("the names of tags: ") for text in without.not_checked)


async def test_triggers_that_do_not_fit_are_dropped_from_the_end() -> None:
    setup = Setup()
    many = cast("list[JsonValue]", list(range(1, 200)))
    triggers = [trigger(2, n, {"filter_has_tags": many}) for n in range(1, 400)]
    setup.flow(*triggers)

    answer = await setup.explain()

    assert [t.id for t in answer.triggers] == list(range(1, len(answer.triggers) + 1))
    whole = len(json.dumps(answer.model_dump(mode="json")))
    assert MAX_CHARS * 0.9 < whole <= MAX_CHARS


async def test_a_workflow_with_a_manual_and_another_trigger_does_not_run_only_by_hand() -> None:
    setup = Setup()
    setup.flow(trigger(5, 1), trigger(2, 2))

    assert "only when a user runs it by hand" not in (await setup.explain()).message


async def test_a_consumption_without_a_stored_name_is_unknown() -> None:
    setup = Setup()
    setup.fake.nameless = True

    [check] = await setup.checks(trigger(1, sources=[], filter_filename="a"))
    none = await setup.checks(trigger(1, sources=[], filter_path="/a"))

    assert (check.result, check.why) == (
        "unknown",
        "the name it was consumed under is not kept",
    )
    assert [c.filter for c in none] == ["path"]


async def test_what_cannot_be_checked_many_times_is_named_once_with_its_number() -> None:
    setup = Setup()
    setup.fake.search_error = UpstreamTimeout("late " + "y" * 5000)
    setup.flow(
        *(
            trigger(
                4,
                number,
                schedule_offset_days=10**12,
                filter_custom_field_query=f'["f{number}","exists",true]',
            )
            for number in range(300)
        )
    )

    answer = await setup.explain()

    assert "300 schedules: the offset is too large" in answer.not_checked
    [queries] = [t for t in answer.not_checked if "custom field quer" in t]
    assert queries.startswith("300 custom field queries: late yyy")
    assert len(queries) < 400
    assert len(json.dumps(answer.model_dump(mode="json"))) <= MAX_CHARS


async def test_a_bounded_answer_may_hold_no_trigger() -> None:
    setup = Setup()
    setup.flow(trigger(filter_has_tags=[1]), name="n" * MAX_CHARS)

    answer = await setup.explain()

    assert answer.triggers == ()
    assert "1 of the triggers are left out" in answer.not_checked[-1]


async def test_the_due_day_is_told() -> None:
    setup = Setup()
    setup.document.created = datetime(2026, 11, 1, tzinfo=UTC).date()

    [check, *_] = await setup.checks(
        trigger(4, schedule_date_field="created", schedule_offset_days=3)
    )

    assert check.why == "due on 2026-11-04 (created +3 days)"


async def test_only_the_newest_history_counts_and_only_an_hour_is_allowed_for() -> None:
    setup = Setup()
    moment = datetime(2026, 7, 12, 8, tzinfo=UTC)
    setup.document.history = [changed(moment + timedelta(days=n + 1)) for n in range(50)]
    setup.document.history.append(changed(moment))
    setup.flow(trigger(3))
    setup.state.logs["paperless"] = [f"[2026-07-12 08:01:00,000] [INFO] [{MATCHING}] {MISSED}"]
    assert (await setup.explain()).log == ()

    setup.document.history = [changed(moment)]
    setup.state.logs["paperless"] = [
        f"[2026-07-12 {hour:02d}:01:00,000] [INFO] [{MATCHING}] {MISSED}" for hour in range(24)
    ]
    assert [f.time[11:13] for f in (await setup.explain()).log] == ["07", "08", "09"]


async def test_a_forged_record_start_without_a_date_is_no_record() -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [
        line("Consuming planted", task="0badcafe", name="paperless.consumer"),
        f"[2026-99-99 99:99:99,000] [INFO] [{MATCHING}] {MATCHED}",
        line(MATCHED, task="0badcafe"),
    ]

    answer = await setup.explain()

    assert [f.said for f in answer.log] == ["the document matched trigger 7"]


@pytest.mark.parametrize(
    ("before", "found"),
    [(timedelta(hours=23), True), (timedelta(hours=25), False), (timedelta(0), True)],
    ids=str,
)
async def test_consume_tasks_of_the_day_before_are_looked_at(
    before: timedelta, *, found: bool
) -> None:
    setup = Setup()
    setup.consumed()
    setup.state.tasks = [replace(setup.state.tasks[0], created=ADDED - before)]
    setup.flow(trigger())
    setup.state.logs["paperless"] = [line(MATCHED, task="0badcafe")]

    assert ((await setup.explain()).did_run == "likely") is found


async def test_a_cut_keeps_what_was_named_before() -> None:
    setup = Setup(READER[0], grant=("view_workflow",))
    many = cast("list[JsonValue]", list(range(1, 200)))
    setup.flow(*(trigger(2, n, {"filter_has_tags": many}) for n in range(400)))

    missing = (await setup.explain()).not_checked

    assert [text.split(" ")[1] for text in missing[:3]] == ["history", "consume", "log"]
    assert missing[-1].endswith("the answer would be too long")


async def test_values_and_patterns_are_told() -> None:
    setup = Setup()
    setup.document.content = "x"
    setup.put({"correspondent": 1, "document_type": None, "storage_path": 1})

    rows = await setup.checks(
        trigger(
            match="it\u202es",
            matching_algorithm=1,
            filter_has_correspondent=1,
            filter_has_document_type=1,
            filter_has_storage_path=1,
        )
    )

    assert [c.why for c in rows] == [
        "the text does not match 'its' (any)",
        "its correspondent is 'e2e-acme'; wanted is 'e2e-acme'",
        "its document type is none; wanted is 'e2e-invoice'",
        "its storage path is 'e2e-archive'; wanted is 'e2e-archive'",
    ]


@pytest.mark.parametrize(
    ("moment", "offset", "result"),
    [
        (NOW, 0, "ok"),
        (NOW + timedelta(seconds=1), 0, "no"),
        (NOW - timedelta(days=1), 1, "ok"),
        (NOW - timedelta(days=1) + timedelta(seconds=1), 1, "no"),
        (NOW + timedelta(days=1), -1, "ok"),
        (NOW + timedelta(days=1, seconds=1), -1, "no"),
    ],
)
async def test_a_moment_is_compared_to_the_second(
    moment: datetime, offset: int, result: str
) -> None:
    for name in ("added", "modified"):
        setup = Setup()
        setup.put({name: moment})
        [check, *_] = await setup.checks(
            trigger(4, schedule_date_field=name, schedule_offset_days=offset)
        )
        assert check.result == result
        assert (moment + timedelta(days=offset)).isoformat() in check.why


@pytest.mark.parametrize(
    ("day", "offset", "result"),
    [("2025-11-20", 30, "ok"), ("2025-11-19", 30, "no"), ("2025-11-20", -30, "ok")],
)
async def test_the_year_of_a_custom_field_does_not_move_with_the_offset(
    day: str, offset: int, result: str
) -> None:
    setup = Setup()
    setup.document.custom_fields = {3: day}

    [check, *_] = await setup.checks(
        trigger(
            4,
            schedule_date_field="custom_field",
            schedule_date_custom_field=3,
            schedule_offset_days=offset,
        )
    )

    assert check.result == result


@pytest.mark.parametrize(
    ("change", "hours"),
    [
        # The closer of two timestamps within a day decides.
        ("2026-03-29T00:10:00+00:00", ["01"]),
        ("2026-03-29T11:50:00+00:00", ["13"]),
        # A day from the nearest one still counts, more does not.
        ("2026-03-30T12:00:00+00:00", ["14"]),
        ("2026-03-30T12:01:00+00:00", ["12", "13", "14", "15"]),
    ],
)
async def test_the_closest_timestamp_within_a_day_decides(change: str, hours: list[str]) -> None:
    setup = Setup()
    setup.put(
        {
            "added": datetime.fromisoformat("2026-03-29T01:00:00+01:00"),
            "modified": datetime.fromisoformat("2026-03-29T14:00:00+02:00"),
        }
    )
    moment = datetime.fromisoformat(change)
    setup.document.history = [changed(moment)]
    setup.flow(trigger(3))
    setup.state.logs["paperless"] = [
        f"[{moment.date()} {hour:02d}:{moment.minute:02d}:00,000] [INFO] [{MATCHING}] {MISSED}"
        for hour in range(24)
    ]

    found = [f.time[11:13] for f in (await setup.explain()).log]

    assert found == hours


async def test_the_start_of_a_task_is_a_known_zone_and_counts_from_its_second() -> None:
    setup = Setup()
    setup.put({"added": ADDED, "modified": ADDED})
    setup.consumed()
    started = datetime(2026, 11, 11, 20, 47, 20, 900_000, tzinfo=ZURICH_WINTER)
    setup.state.tasks = [replace(setup.state.tasks[0], started=started)]
    setup.document.history = [changed(datetime(2026, 7, 12, 8, tzinfo=UTC))]
    setup.flow(trigger())
    setup.state.logs["paperless"] = [
        f"[2026-07-12 {hour:02d}:00:00,000] [INFO] [{MATCHING}] {MISSED}" for hour in range(24)
    ] + [
        line(MATCHED, at="20:47:19", task="0badcafe"),
        line(MATCHED, at="20:47:20", task="0badcafe"),
    ]

    log = (await setup.explain()).log

    assert [f.time[11:13] for f in log if f.whose.startswith("perhaps")] == ["07", "08", "09", "10"]
    assert [f.time[-2:] for f in log if f.whose.startswith("likely")] == ["20"]


async def test_the_newest_of_the_uncertain_findings_are_kept() -> None:
    setup = Setup()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [line(MISSED, at=f"19:47:{n:02d}") for n in range(30)]

    assert [f.time[-2:] for f in (await setup.explain()).log] == [f"{n:02d}" for n in range(10, 30)]


async def test_where_it_came_from_is_that_of_its_own_tasks() -> None:
    setup = Setup()
    setup.consumed("11111111-1111-4222-8333-444455556666", document=2)
    setup.state.tasks = [replace(setup.state.tasks[0], trigger_source="email_consume")]
    setup.consumed()

    [check] = await setup.checks(trigger(1, sources=[1]))
    setup.state.tasks = setup.state.tasks[:1]
    [none] = await setup.checks(trigger(1, sources=[1]))

    assert "(it came by api_upload)" in check.why
    assert "(not known)" in none.why


@pytest.mark.parametrize("query", ["{", "nonsense", "[" * 1_000_000], ids=["cut", "word", "deep"])
async def test_a_query_that_is_no_json_matches_nothing(query: str) -> None:
    [check] = await Setup().checks(trigger(filter_custom_field_query=query))

    assert check.result == "no"


async def test_the_answer_names_what_a_rename_hides() -> None:
    setup = Setup()
    setup.flow(trigger())

    assert "under a former name" in (await setup.explain()).note


async def test_no_page_of_names_is_asked_for_after_the_time_is_up() -> None:
    setup = Setup()
    setup.document.tags = [1]
    setup.flow(trigger(filter_has_tags=[1]))
    setup.fake.slow = 0.05
    setup.times = [0.0, 39.9, 39.9]

    answer = await setup.explain()
    asked = setup.fake.pages
    await anyio.sleep(0.2)

    assert asked < 20
    assert setup.fake.pages == asked
    assert "the names of tags: the time was up" in answer.not_checked
    assert "#1" in answer.triggers[0].checks[0].why


async def test_a_workflow_list_that_takes_too_long_is_an_error_not_an_endless_call() -> None:
    setup = Setup()
    setup.flow(trigger())
    setup.fake.slow = -0.05
    setup.times = [0.0, 39.9]

    with pytest.raises(UpstreamTimeout, match="40 seconds"):
        _ = await setup.explain()

    assert setup.fake.pages < 20


@pytest.mark.parametrize(
    ("at", "late"), [("19:47:20", False), ("19:47:30", False), ("19:47:31", True)]
)
async def test_a_log_that_begins_after_the_document_arrived_is_named(
    at: str, *, late: bool
) -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())
    setup.state.logs["paperless"] = [line("anything", at=at)]

    missing = (await setup.explain()).not_checked

    said = f"the log before 2026-11-11 {at}: it is not read, and the document arrived before"
    assert (said in missing) is late


async def test_an_empty_log_is_named() -> None:
    setup = Setup()
    setup.consumed()
    setup.flow(trigger())

    assert (await setup.explain()).not_checked == ("the log holds no records",)
