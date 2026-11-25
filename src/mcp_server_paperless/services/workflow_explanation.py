"""Why a workflow ran on a document or did not: an inference from today's state (decision-88).

Paperless keeps no record of runs that can be read, so the settings of the workflow are compared
with the document as it is now, in the order of Paperless's own checks. History, consume task and
log say more where the user may read them; the log is told in this server's words, because its
lines hold names and texts of other documents.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Final, cast

from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.domain.errors import (
    PaperlessMcpError,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.workflows import (
    MATCHING_ALGORITHMS,
    SOURCES,
    TRIGGER_FIELDS,
    TRIGGER_TYPES,
    FilterCheck,
    LogFinding,
    TriggerExplanation,
    WorkflowExplanation,
)
from mcp_server_paperless.services.log_records import read_log_records
from mcp_server_paperless.services.workflows import CONSUMPTION, MANUAL, SCHEDULED, is_empty

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.admin import TaskRow
    from mcp_server_paperless.domain.documents import DocumentRecord, HistoryEntry, NameOrId
    from mcp_server_paperless.domain.ports import AdminReader, DocumentReader
    from mcp_server_paperless.domain.taxonomy import TaxonomyKind
    from mcp_server_paperless.domain.workflows import (
        CheckResult,
        Verdict,
        Workflow,
        WorkflowTrigger,
    )
    from mcp_server_paperless.services.log_records import LogRecord
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver
    from mcp_server_paperless.services.workflows import WorkflowService

NOTE: Final = (
    "Paperless keeps no readable record of workflow runs. This compares the settings of today with "
    "the document of today; both may have been different when the workflow could have run."
)
# Nothing is asked of Paperless this many seconds after the call began.
SECONDS: Final = 40.0
MAX_CHARS: Final = 50_000
MAX_HISTORY, MAX_NAMES, MAX_FINDINGS, MAX_TASKS = 50, 10, 20, 100
YEAR: Final = timedelta(days=365)  # a date in a custom field counts that long
NEAR: Final = timedelta(minutes=2)
LOG: Final = "paperless"
# How the refusal of a query begins; another refusal says nothing of the document.
REJECTED_QUERY: Final = "rejected the request: custom_field_query"
MATCHING, HANDLERS = "paperless.matching", "paperless.handlers"
FIRES: Final[Mapping[int, str]] = {
    1: "once, before the document was stored",
    2: "once, after the document was stored",
    3: "when the document is changed (not by notes, other workflows or new versions)",
    4: "regularly, by a date of the document",
    5: "only when a user runs it by hand",
}
# What Paperless says why a document does not match, and how it is told here: never with names.
# Names may hold any text: a reason is told only as far as its beginning and form make it certain.
_LIST: Final = r"\[.*\]"
REASONS: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(pattern, re.DOTALL), said)
    for pattern, said in (
        (rf"Document source .* not in {_LIST}", "the source does not fit"),
        (r"Document mail rule .* != \d+", "the mail rule does not fit"),
        (r"Document filename .* does not match .*", "the file name does not match"),
        (r"Document path .* does not match .*", "the path does not match"),
        (
            r"Document content matching settings for algorithm '\d+' did not match",
            "the content does not match",
        ),
        (
            r"Document tags \[.*\] (do not include|do not contain all of|include excluded tags) .*",
            "the tags do not fit",
        ),
        (
            r"Document correspondent .* (is not one of|is excluded by|does not match) .*",
            "the correspondent does not fit",
        ),
        (
            r"Document doc type .* (is not one of|is excluded by|does not match) .*",
            "the document type does not fit",
        ),
        (
            r"Document storage path .* (is not one of|is excluded by|does not match) .*",
            "the storage path does not fit",
        ),
        (
            r"Document custom fields do not match the configured custom field query",
            "the custom field query does not match",
        ),
        (r"Invalid custom field query configuration", "the custom field query is not valid"),
        (r"No matching triggers with type \d+ found", "it has no trigger for this event"),
    )
)
# The attribute of the history a filter depends on.
HISTORY: Final[Mapping[str, str]] = {
    "tag": "tags",
    "correspondent": "correspondent",
    "document_type": "document_type",
    "storage_path": "storage_path",
}


@dataclass(slots=True)
class _Facts:
    """What is known of the document, and what could not be looked at."""

    document: DocumentRecord
    now: datetime
    names: dict[str, Mapping[int, str]]
    history: Sequence[HistoryEntry] = ()
    text: str | None = None
    queries: dict[str, bool | None] = field(default_factory=dict[str, "bool | None"])
    tasks: Sequence[TaskRow] = ()
    records: Sequence[LogRecord] = ()
    missing: list[str] = field(default_factory=list[str])


def _ids(value: JsonValue) -> list[int]:
    return [n for n in value if isinstance(n, int)] if isinstance(value, list) else []


def content_matches(text: str, match: str, algorithm: int, *, ignore_case: bool) -> bool | None:
    """Whether a content filter matches, as Paperless 3.5.1 decides; None where it is not checked.

    A regular expression of someone else can run forever and fuzzy matching needs a library, so
    neither is checked here.
    """
    if algorithm not in {1, 2, 3}:
        return None
    if not match.strip():
        return False
    flags = re.IGNORECASE if ignore_case else re.NOFLAG
    if algorithm == 3:  # noqa: PLR2004 -- exact
        return re.search(rf"\b{re.escape(match)}\b", text, flags) is not None
    found = cast("list[tuple[str, str]]", re.findall(r'"([^"]+)"|(\S+)', match))
    terms = (re.sub(r"\s+", " ", (phrase or word).strip()) for phrase, word in found)
    words = [re.escape(term).replace(r"\ ", r"\s+") for term in terms]
    hits = (re.search(rf"\b{word}\b", text, flags) is not None for word in words)
    return any(hits) if algorithm == 1 else all(hits)


def _closest(stamps: Sequence[datetime], moment: datetime | None) -> timedelta:
    """The offset of the stamp that is closest to ``moment``."""
    at = moment or stamps[0]
    return min(stamps, key=lambda stamp: abs(stamp - at)).utcoffset() or timedelta()


def _offsets(facts: _Facts, moment: datetime | None = None) -> set[timedelta]:
    """The offsets from UTC Paperless may have at ``moment`` (default: at any time).

    The log has local times and the history UTC; a document's own timestamps have the offset that
    Paperless had then. Where none of them is within a day of the moment, an hour of summer time is
    allowed for.
    """
    stamps = [facts.document.added, facts.document.modified]
    stamps += [task.started for task in facts.tasks if task.started is not None]
    known = {stamp.utcoffset() or timedelta() for stamp in stamps}
    close = [s for s in stamps if moment is not None and abs(s - moment) <= timedelta(days=1)]
    if close:
        return {_closest(close, moment)}
    hour = timedelta(hours=1)
    return {offset + step for offset in known for step in (-hour, timedelta(), hour)}


def _days(facts: _Facts, moment: datetime) -> set[date]:
    """The days it may be in Paperless at ``moment``: near midnight there are two."""
    return {(moment + offset).date() for offset in _offsets(facts)}


def _utc(record: LogRecord, offset: timedelta) -> datetime:
    return (record.time - offset).replace(tzinfo=UTC)


class _Checks:
    """The checks of one trigger against the document."""

    def __init__(self, trigger: WorkflowTrigger, facts: _Facts) -> None:
        self._trigger: WorkflowTrigger = trigger
        self._facts: _Facts = facts
        self.rows: list[FilterCheck] = []

    def _add(self, name: str, result: CheckResult, why: str, attribute: str | None = None) -> None:
        self.rows.append(
            FilterCheck(filter=name, result=result, why=why, last_change=self._changed(attribute))
        )

    def _changed(self, attribute: str | None) -> str | None:
        if attribute is None:
            return None
        for entry in self._facts.history:
            if attribute in entry.changes:
                by = (
                    quoted(entry.actor)
                    if entry.actor
                    else ("nobody (a workflow, automatic matching or the system)")
                )
                return f"{entry.timestamp.isoformat(timespec='seconds')} by {by}"
        return None

    def _value(self, name: str) -> JsonValue:
        return self._trigger.settings.get(TRIGGER_FIELDS[name].wire)

    def _named(self, kind: str, numbers: Sequence[int]) -> str:
        names = self._facts.names.get(kind, {})
        shown = [quoted(names[n]) if n in names else f"#{n}" for n in numbers[:MAX_NAMES]]
        more = f" and {len(numbers) - MAX_NAMES} more" if len(numbers) > MAX_NAMES else ""
        return f"{', '.join(shown)}{more}" if shown else "none"

    def consumption(self) -> None:
        """What a consumption trigger looked at; only the file name is still known for sure."""
        facts = self._facts
        sources = _ids(self._value("sources"))
        if sources:
            wanted = ", ".join(SOURCES.get(n, str(n)) for n in sources)
            came = sorted({task.trigger_source for task in facts.tasks})
            known = f"it came by {', '.join(came)}" if came else "not known"
            self._add("sources", "unknown", f"where it came from ({known}) has to be {wanted}")
        if self._value("mailrule") is not None:
            self._add("mailrule", "unknown", "which mail rule fetched it cannot be read")
        self._consumed_name()
        if not is_empty(self._value("path")):
            self._add("path", "unknown", "the path it was consumed from is not kept")

    def _consumed_name(self) -> None:
        """The name a file was consumed under is not kept: parts and e-mails get another one."""
        pattern = self._value("filename")
        if is_empty(pattern):
            return
        name = self._facts.document.original_file_name
        why = "the name it was consumed under is not kept"
        if name is not None:
            verb = "matches" if self._fits(name, pattern) else "does not match"
            why = (
                f"the stored file name {quoted(name)} {verb} {quoted(str(pattern))}, but the name "
                "it was consumed under may have been another one"
            )
        self._add("filename", "unknown", why)

    @staticmethod
    def _fits(name: str, pattern: JsonValue) -> bool:
        return fnmatchcase(name.lower(), str(pattern).lower())

    def _filename(self, *, scheduled: bool) -> None:
        pattern = self._value("filename")
        if is_empty(pattern):
            return
        name = self._facts.document.original_file_name
        if name is None:
            result, why = ("no" if scheduled else "ok"), "it has no original file name"
            self._add("filename", cast("CheckResult", result), why)
            return
        fits = self._fits(name, pattern)
        verb = "matches" if fits else "does not match"
        why = f"the file name {quoted(name)} {verb} {quoted(str(pattern))}"
        self._add("filename", "ok" if fits else "no", why, "original_filename")

    def existing(self) -> None:
        """The filters of every other trigger, in the order of Paperless."""
        if self._trigger.type != SCHEDULED:
            self._content()
            self._references()
            return
        # A schedule looks for documents first, and reads the text of those it found.
        self._schedule()
        self._references()
        if self._value("schedule_is_recurring"):
            why = "when it last ran on the document is not readable"
            self._add("schedule_recurring_interval_days", "unknown", why)
        else:
            why = "it runs once per document; whether it has is not readable"
            self._add("schedule_is_recurring", "unknown", why)
        self._content()

    def _references(self) -> None:
        document = self._facts.document
        self._sets("has_tags", lambda here, there: bool(here & there), "one of")
        self._sets("has_all_tags", lambda here, there: there <= here, "all of")
        self._sets("has_not_tags", lambda here, there: not here & there, "none of")
        for single, kind, plural_, value in (
            ("correspondent", "correspondent", "correspondents", document.correspondent),
            ("document_type", "document_type", "document_types", document.document_type),
            ("storage_path", "storage_path", "storage_paths", document.storage_path),
        ):
            self._one(f"has_any_{plural_}", kind, value, "one of", lambda v, ids: v in ids)
            self._one(f"has_{single}", kind, value, "", lambda v, ids: v in ids)
            self._one(f"has_not_{plural_}", kind, value, "none of", lambda v, ids: v not in ids)
        self._query()
        self._filename(scheduled=self._trigger.type == SCHEDULED)

    def _content(self) -> None:
        algorithm = self._value("matching_algorithm")
        if not isinstance(algorithm, int) or algorithm == 0:
            return
        label = MATCHING_ALGORITHMS.get(algorithm, str(algorithm))
        text = self._facts.text
        match = str(self._value("match") or "")
        found = (
            None
            if text is None
            else content_matches(
                text, match, algorithm, ignore_case=self._value("is_insensitive") is not False
            )
        )
        if found is None:
            checked = algorithm in {1, 2, 3}
            why = "the text could not be read" if checked else f"{label} is not checked here"
            self._add("match", "unknown", why, "content")
            return
        verb = "matches" if found else "does not match"
        self._add(
            "match",
            "ok" if found else "no",
            f"the text {verb} {quoted(match)} ({label})",
            "content",
        )

    def _sets(self, name: str, fits: Callable[[set[int], set[int]], bool], which: str) -> None:
        wanted = _ids(self._value(name))
        if not wanted:
            return
        here = self._facts.document.tags
        good = fits(set(here), set(wanted))
        why = f"it has the tags {self._named('tag', here)}; wanted is {which} " + self._named(
            "tag", wanted
        )
        self._add(name, "ok" if good else "no", why, "tags")

    def _one(
        self,
        name: str,
        kind: str,
        value: int | None,
        which: str,
        fits: Callable[[int | None, Sequence[int]], bool],
    ) -> None:
        stored = self._value(name)
        wanted = [stored] if isinstance(stored, int) else _ids(stored)
        if not wanted:
            return
        here = self._named(kind, [] if value is None else [value])
        label = kind.replace("_", " ")
        why = f"its {label} is {here}; wanted is {which} ".replace("  ", " ")
        why += self._named(kind, wanted)
        self._add(name, "ok" if fits(value, wanted) else "no", why, HISTORY[kind])

    def _query(self) -> None:
        query = self._value("custom_field_query")
        if is_empty(query):
            return
        found = self._facts.queries.get(str(query))
        if found is None:
            self._add("custom_field_query", "unknown", "Paperless could not be asked")
            return
        verb = "finds" if found else "does not find"
        why = f"asked now, Paperless {verb} it by the query"
        self._add("custom_field_query", "ok" if found else "no", why, "custom_fields")

    def _schedule(self) -> None:
        offset = self._value("schedule_offset_days")
        name = str(self._value("schedule_date_field") or "added")
        try:
            days = timedelta(days=offset if isinstance(offset, int) else 0)
            result, why = self._due(name, days)
        except OverflowError:
            result, why = "unknown", "the offset is too large"
            said = f"the schedule of trigger {self._trigger.id}: its offset is too large"
            self._facts.missing.append(said)
        self._add("schedule_date_field", result, why)

    def _due(self, name: str, days: timedelta) -> tuple[CheckResult, str]:
        """Whether the date of the document plus ``days`` is reached."""
        facts, told = self._facts, f"{name} {days.days:+d} days"
        stamp = {"added": facts.document.added, "modified": facts.document.modified}.get(name)
        if stamp is not None:
            due = stamp + days
            return _result({due <= facts.now}), f"due at {due.isoformat()} ({told})"
        day = facts.document.created if name == "created" else self._field_day()
        if day is None:
            return "no", "the document has no such date"
        due_day = day + days
        reached = {day <= today for today in _days(facts, facts.now - days)}
        if name == "custom_field":
            early = _days(facts, facts.now - YEAR)
            reached = {ok and day >= first for ok in reached for first in early}
            told += f"; Paperless looks at dates of the last {YEAR.days} days"
        return _result(reached), f"due on {due_day.isoformat()} ({told})"

    def _field_day(self) -> date | None:
        number = self._value("schedule_date_custom_field")
        values = [v.value for v in self._facts.document.custom_fields if v.field == number]
        try:
            return date.fromisoformat(str(values[0])) if values else None
        except ValueError:
            return None


def _result(found: set[bool]) -> CheckResult:
    """Yes or no where every possibility says so, else unknown."""
    if len(found) > 1:
        return "unknown"
    return "ok" if True in found else "no"


def _verdict(rows: Sequence[FilterCheck]) -> Verdict:
    results = {row.result for row in rows}
    if "no" in results:
        return "no"
    return "unknown" if "unknown" in results else "yes"


def _explained(trigger: WorkflowTrigger, facts: _Facts) -> TriggerExplanation:
    checks = _Checks(trigger, facts)
    known = trigger.type in TRIGGER_TYPES
    if trigger.type == CONSUMPTION:
        checks.consumption()
    elif known:
        checks.existing()
    return TriggerExplanation(
        id=trigger.id,
        type_name=TRIGGER_TYPES.get(trigger.type, f"unknown type {trigger.type}"),
        fires=FIRES.get(trigger.type, "not known to this server"),
        matches=_verdict(checks.rows) if known else "unknown",
        checks=tuple(checks.rows),
    )


def _near(record: LogRecord, facts: _Facts) -> bool:
    """Whether a record is close to the arrival or a change of the document."""
    added = facts.document.added
    if abs(_utc(record, added.utcoffset() or timedelta()) - added) <= NEAR:
        return True
    return any(
        abs(_utc(record, offset) - entry.timestamp) <= NEAR
        for entry in facts.history
        for offset in _offsets(facts, entry.timestamp)
    )


def _inside(record: LogRecord, task: TaskRow, facts: _Facts) -> bool:
    """Whether a record has the prefix of the consume task and was written while it ran."""
    if record.task != task.task_id[:8] or task.started is None:
        return False
    at = _utc(record, task.started.utcoffset() or timedelta())
    start = task.started.replace(microsecond=0)
    return start <= at <= (task.finished or facts.now)


def _findings(workflow: Workflow, facts: _Facts) -> list[LogFinding]:
    """What the log says of the workflow, where it is about this document or may be."""
    triggers = {
        f"Document matched WorkflowTrigger {t.id} from Workflow: {workflow.name}": t.id
        for t in workflow.triggers
    }
    actions = {
        f"Applying WorkflowAction {a.id} from Workflow: {workflow.name}": a.id
        for a in workflow.actions
    }
    missed = f"Document did not match Workflow: {workflow.name}"
    found: list[LogFinding] = []
    for place, record in enumerate(facts.records):
        # The whole text and the logger: foreign text may look like a part of it.
        said = None
        if record.logger == MATCHING:
            if record.message in triggers:
                said = f"the document matched trigger {triggers[record.message]}"
            elif record.message == missed:
                said = "the document did not match" + _reason(
                    facts.records[place + 1 : place + 2], record
                )
        elif record.logger == HANDLERS and record.message in actions:
            said = f"action {actions[record.message]} was applied"
        if said is None:
            continue
        if any(_inside(record, task, facts) for task in facts.tasks):
            whose = "likely this document"
        elif record.task is None and _near(record, facts):
            whose = "perhaps this document"
        else:
            continue
        found.append(LogFinding(time=record.time.isoformat(sep=" "), said=said, whose=whose))
    return found


def _reason(following: Sequence[LogRecord], record: LogRecord) -> str:
    for after in following:
        if after.task == record.task and after.logger == record.logger:
            for pattern, said in REASONS:
                if pattern.fullmatch(after.message):
                    return f": {said}"
    return ""


class WorkflowExplanations:
    """Explains why a workflow ran on a document or did not."""

    def __init__(  # noqa: PLR0913 -- what it reads, and two clocks
        self,
        documents: DocumentReader,
        management: AdminReader,
        workflows: WorkflowService,
        taxonomy: TaxonomyResolver,
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Read through the ports; ``workflows`` finds the workflow, ``taxonomy`` knows names."""
        self._documents: DocumentReader = documents
        self._management: AdminReader = management
        self._workflows: WorkflowService = workflows
        self._taxonomy: TaxonomyResolver = taxonomy
        self._clock: Callable[[], float] = clock
        self._now: Callable[[], datetime] = now

    async def explain(self, workflow: NameOrId, document_id: int) -> WorkflowExplanation:
        """Compare a workflow with a document.

        Raises:
            NotFound: No such workflow, or no such document the user may see.
            Ambiguous: The name matches several workflows.
            PermissionDenied: The user may not view workflows or documents.
        """
        until = self._clock() + SECONDS
        found = await self._workflows.find(workflow)
        document = await self._documents.get_document(document_id)
        facts = _Facts(document=document, names={}, now=self._now())
        await self._read(found, facts, until)
        triggers = [_explained(trigger, facts) for trigger in found.triggers]
        findings = _findings(found, facts)
        ran = any(f.whose.startswith("likely") and "not match" not in f.said for f in findings)
        if len(findings) > MAX_FINDINGS:
            left = len(findings) - MAX_FINDINGS
            facts.missing.append(f"{left} findings of the log are left out")
            likely = [f for f in findings if f.whose.startswith("likely")]
            perhaps = [f for f in findings if not f.whose.startswith("likely")]
            shown = likely[-MAX_FINDINGS:]
            findings = [*shown, *perhaps[len(perhaps) - (MAX_FINDINGS - len(shown)) :]]
        answer = WorkflowExplanation(
            note=NOTE,
            workflow_id=found.id,
            workflow=found.name,
            enabled=found.enabled,
            order=found.order,
            document_id=document.id,
            would_match_now=_overall(found, triggers),
            did_run="likely" if ran else "unknown",
            message=_message(found, triggers, ran=ran),
            triggers=tuple(triggers),
            log=tuple(findings),
            log_from=facts.records[0].time.isoformat(sep=" ") if facts.records else None,
            not_checked=tuple(facts.missing),
        )
        return _bounded(answer)

    def _naming(self, kind: TaxonomyKind, facts: _Facts) -> Callable[[], Awaitable[None]]:
        async def names() -> None:
            facts.names[kind] = {item.id: item.name for item in await self._taxonomy.items(kind)}

        return names

    async def _read(self, workflow: Workflow, facts: _Facts, until: float) -> None:
        """What may be missing: each source is asked once, and said where it cannot be read."""
        for label, read in self._sources(workflow, facts):
            if self._clock() >= until:
                facts.missing.append(f"{label}: the time was up")
                continue
            try:
                await read()
            except PaperlessMcpError as error:
                facts.missing.append(f"{label}: {error.message}")

    def _sources(
        self, workflow: Workflow, facts: _Facts
    ) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
        """What is read of the document and its past, with what it needs."""
        number = facts.document.id

        async def text() -> None:
            facts.text = (await self._documents.get_document_content(number)).content

        async def history() -> None:
            entries = await self._documents.list_document_history(number)
            facts.history = entries[:MAX_HISTORY]
            if len(entries) > MAX_HISTORY:
                older = len(entries) - MAX_HISTORY
                facts.missing.append(f"{older} older entries of the history are not looked at")

        async def log() -> None:
            facts.records = await read_log_records(self._management, LOG)

        kinds: set[TaxonomyKind] = {
            setting.kind
            for name, setting in TRIGGER_FIELDS.items()
            if name.startswith("has_")
            and setting.kind is not None
            and any(not is_empty(t.settings.get(setting.wire)) for t in workflow.triggers)
        }
        found: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            (f"the names of {kind.replace('_', ' ')}s", self._naming(kind, facts))
            for kind in sorted(kinds)
        ]
        later = [t.settings for t in workflow.triggers if t.type != CONSUMPTION]
        if any(settings.get("matching_algorithm") in {1, 2, 3} for settings in later):
            found.append(("the text", text))
        queries = {str(q) for s in later if not is_empty(q := s.get("filter_custom_field_query"))}
        found += [("a custom field query", self._asking(q, facts)) for q in sorted(queries)]
        return [
            *found,
            ("the history (needs the right to view the audit log)", history),
            ("the consume task (needs the right to view tasks)", self._consumed(facts)),
            ("the log (needs a staff user)", log),
        ]

    def _consumed(self, facts: _Facts) -> Callable[[], Awaitable[None]]:
        async def task() -> None:
            added = facts.document.added
            page = await self._management.list_tasks(
                state=None,
                task_type="consume_file",
                acknowledged=None,
                page=1,
                page_size=MAX_TASKS,
                created=(added - timedelta(days=1), added),
            )
            facts.tasks = [t for t in page.rows if facts.document.id in t.document_ids]
            if page.next_page is not None:
                said = f"only the newest {MAX_TASKS} consume tasks of that day were looked at"
                facts.missing.append(said)
            if not facts.tasks:
                facts.missing.append("the consume task of the document was not found")

        return task

    def _asking(self, query: str, facts: _Facts) -> Callable[[], Awaitable[None]]:
        async def ask() -> None:
            try:
                expression = cast("JsonValue", json.loads(query))
                if not isinstance(expression, list) or not expression:
                    facts.queries[query] = False  # Paperless takes what is none as no match
                    return
                wanted = DocumentQuery(ids=(facts.document.id,), custom_field_query=expression)
                page = await self._documents.search_documents(wanted, page=1, page_size=1)
            except ValueError, RecursionError:
                facts.queries[query] = False
                return
            except Validation as error:
                if False:
                    raise
                facts.queries[query] = False  # Paperless takes a query it rejects as no match
                return
            facts.queries[query] = page.count > 0

        return ask


def _overall(workflow: Workflow, triggers: Sequence[TriggerExplanation]) -> Verdict:
    if not workflow.enabled or all(t.matches == "no" for t in triggers):
        return "no"
    return "yes" if any(t.matches == "yes" for t in triggers) else "unknown"


def _message(workflow: Workflow, triggers: Sequence[TriggerExplanation], *, ran: bool) -> str:
    subject = f"The workflow {quoted(workflow.name)} (id {workflow.id})"
    if not workflow.enabled:
        told = "is not enabled, so it does not run."
    elif not triggers:
        told = "has no trigger, so it does not run."
    elif not workflow.actions:
        told = "has no action, so it does nothing."
    else:
        told = {
            "yes": "matches the document as both are now.",
            "no": "does not match the document as both are now.",
            "unknown": "may match the document; not everything can be checked.",
        }[_overall(workflow, triggers)]
        wrong = next((c for t in triggers for c in t.checks if c.result == "no"), None)
        if wrong is not None and _overall(workflow, triggers) == "no":
            told += f" First reason: {wrong.filter}: {wrong.why}."
    if workflow.enabled and all(t.type == MANUAL for t in workflow.triggers) and triggers:
        told += " It runs only when a user runs it by hand."
    if ran:
        told += (
            " It likely ran on this document: the log says so, which people who may upload can "
            "influence."
        )
    else:
        told += " Whether it ran is not known."
    return f"{subject} {told}"


def _bounded(answer: WorkflowExplanation) -> WorkflowExplanation:
    """``answer`` within ``MAX_CHARS`` characters: the last triggers go, and that is said."""

    def size(value: object) -> int:
        return len(json.dumps(value))

    if size(answer.model_dump(mode="json")) <= MAX_CHARS:
        return answer
    said = f"{len(answer.triggers)} of the triggers are left out: the answer would be too long"
    bare = answer.model_copy(update={"triggers": (), "not_checked": (*answer.not_checked, said)})
    room = MAX_CHARS - size(bare.model_dump(mode="json"))
    kept = 0
    for trigger in answer.triggers:
        room -= size(trigger.model_dump(mode="json")) + 2
        if room < 0:
            break
        kept += 1
    gone = len(answer.triggers) - kept
    said = f"{gone} of the triggers are left out: the answer would be too long"
    missing = (*answer.not_checked, said)
    return answer.model_copy(update={"triggers": answer.triggers[:kept], "not_checked": missing})
