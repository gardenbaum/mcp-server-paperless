"""Why a workflow ran on a document or did not: an inference from today's state (decision-87).

Paperless keeps no record of runs that can be read, so the settings of the workflow are compared
with the document as it is now, in the order of Paperless's own checks. History, consume task and log
say more where the user may read them; the log is told in this server's words, because its lines hold
names and texts of other documents.
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
    PermissionDenied,
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
NEAR: Final = timedelta(minutes=2)
LOG: Final = "paperless"
FIRES: Final[Mapping[int, str]] = {
    1: "once, before the document was stored",
    2: "once, after the document was stored",
    3: "when the document is changed (not by notes, other workflows or new versions)",
    4: "regularly, by a date of the document",
    5: "only when a user runs it by hand",
}
# What Paperless says why a document does not match, and how it is told here: never with names.
REASONS: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(pattern, re.DOTALL), said)
    for pattern, said in (
        (r"Document source .* not in ", "the source is not among those of the trigger"),
        (r"Document mail rule .* != ", "another mail rule"),
        (r"Document filename .* does not match ", "the file name does not match"),
        (r"Document path .* does not match ", "the path does not match"),
        (r"Document content matching settings ", "the content does not match"),
        (r"Document tags .* do not include ", "none of the tags is there"),
        (r"Document tags .* do not contain all of ", "not all of the tags are there"),
        (r"Document tags .* include excluded tags ", "an excluded tag is there"),
        (r"Document correspondent .* is excluded by ", "the correspondent is excluded"),
        (r"Document correspondent ", "another correspondent"),
        (r"Document doc type .* is excluded by ", "the document type is excluded"),
        (r"Document doc type ", "another document type"),
        (r"Document storage path .* is excluded by ", "the storage path is excluded"),
        (r"Document storage path ", "another storage path"),
        (r"Document custom fields do not match ", "the custom field query does not match"),
        (r"Invalid custom field query ", "the custom field query is not valid"),
        (r"No matching triggers with type ", "it has no trigger for this event"),
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
    names: Mapping[str, Mapping[int, str]]
    history: Sequence[HistoryEntry] = ()
    text: str | None = None
    queries: dict[str, bool | None] = field(default_factory=dict[str, "bool | None"])
    task: TaskRow | None = None
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
            known = f"it came by {facts.task.trigger_source}" if facts.task else "not known"
            self._add("sources", "unknown", f"where it came from ({known}) has to be {wanted}")
        if self._value("mailrule") is not None:
            self._add("mailrule", "unknown", "which mail rule fetched it cannot be read")
        self._filename(scheduled=False)
        if not is_empty(self._value("path")):
            self._add("path", "unknown", "the path it was consumed from is not kept")

    def _filename(self, *, scheduled: bool) -> None:
        pattern = self._value("filename")
        if is_empty(pattern):
            return
        name = self._facts.document.original_file_name
        if name is None:
            result, why = ("no" if scheduled else "ok"), "it has no original file name"
            self._add("filename", cast("CheckResult", result), why)
            return
        fits = fnmatchcase(name.lower(), str(pattern).lower())
        verb = "matches" if fits else "does not match"
        why = f"the file name {quoted(name)} {verb} {quoted(str(pattern))}"
        self._add("filename", "ok" if fits else "no", why, "original_filename")

    def existing(self) -> None:
        """The filters of every other trigger, in the order of Paperless."""
        document = self._facts.document
        if self._trigger.type == SCHEDULED:
            self._schedule()
        self._content()
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
            why = "the text could not be read" if text is None else f"{label} is not checked here"
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
        days = timedelta(days=offset if isinstance(offset, int) else 0)
        name = str(self._value("schedule_date_field") or "added")
        base = self._base(name)
        if base is None:
            self._add("schedule_date_field", "no", "the document has no such date")
            return
        now = datetime.now(UTC)
        due = base + days
        if name == "custom_field" and base < now - timedelta(days=365):
            why = f"its date {base.date()} is more than 365 days ago: Paperless does not look"
            self._add("schedule_date_field", "no", why)
        else:
            reached = due <= now
            state = "reached" if reached else "not yet"
            why = f"due on {due.date()} ({name} {days.days:+d} days), {state}"
            self._add("schedule_date_field", "ok" if reached else "no", why)
        if not self._value("schedule_is_recurring"):
            why = "it runs once per document; whether it has is not readable"
            self._add("schedule_is_recurring", "unknown", why)

    def _base(self, name: str) -> datetime | None:
        document = self._facts.document
        if name == "added":
            return document.added
        if name == "modified":
            return document.modified
        day: date | None = document.created if name == "created" else None
        if name == "custom_field":
            number = self._value("schedule_date_custom_field")
            values = [v.value for v in document.custom_fields if v.field == number]
            try:
                day = date.fromisoformat(str(values[0])) if values else None
            except ValueError:
                day = None
        return None if day is None else datetime(day.year, day.month, day.day, tzinfo=UTC)


def _verdict(rows: Sequence[FilterCheck]) -> Verdict:
    results = {row.result for row in rows if row.filter != "schedule_is_recurring"}
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


def _near(record: LogRecord, moments: Sequence[datetime]) -> bool:
    """Whether a record is close to a moment; the log has the time zone of Paperless."""
    return any(abs(record.time.replace(tzinfo=m.tzinfo) - m) <= NEAR for m in moments)


def _findings(workflow: Workflow, facts: _Facts) -> list[LogFinding]:
    """What the log says of the workflow, where it is about this document or may be."""
    mine = facts.task.task_id[:8] if facts.task else None
    moments = [facts.document.added, *(entry.timestamp for entry in facts.history)]
    triggers = {
        f"Document matched WorkflowTrigger {t.id} from Workflow: {workflow.name}": t.id
        for t in workflow.triggers
    }
    actions = {
        f"Applying WorkflowAction {a.id} from Workflow: {workflow.name}": a.id
        for a in workflow.actions
    }
    found: list[LogFinding] = []
    for place, record in enumerate(facts.records):
        if record.message in triggers:
            said = f"the document matched trigger {triggers[record.message]}"
        elif record.message in actions:
            said = f"action {actions[record.message]} was applied"
        elif record.message == f"Document did not match Workflow: {workflow.name}":
            said = "the document did not match" + _reason(
                facts.records[place + 1 : place + 2], record
            )
        else:
            continue
        if mine is not None and record.task == mine:
            whose = "this document"
        elif record.task is None and _near(record, moments):
            whose = "perhaps this document"
        else:
            continue
        found.append(LogFinding(time=record.time.isoformat(sep=" "), said=said, whose=whose))
    return found[-MAX_FINDINGS:]


def _reason(following: Sequence[LogRecord], record: LogRecord) -> str:
    for after in following:
        if after.task == record.task and after.logger == record.logger:
            for pattern, said in REASONS:
                if pattern.match(after.message):
                    return f": {said}"
    return ""


class WorkflowExplanations:
    """Explains why a workflow ran on a document or did not."""

    def __init__(
        self,
        documents: DocumentReader,
        management: AdminReader,
        workflows: WorkflowService,
        taxonomy: TaxonomyResolver,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Read through the ports; ``workflows`` finds the workflow, ``taxonomy`` knows names."""
        self._documents: DocumentReader = documents
        self._management: AdminReader = management
        self._workflows: WorkflowService = workflows
        self._taxonomy: TaxonomyResolver = taxonomy
        self._clock: Callable[[], float] = clock

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
        facts = _Facts(document=document, names=await self._names(found))
        await self._read(found, facts, until)
        triggers = [_explained(trigger, facts) for trigger in found.triggers]
        findings = _findings(found, facts)
        ran = any(f.whose == "this document" and "not match" not in f.said for f in findings)
        answer = WorkflowExplanation(
            note=NOTE,
            workflow_id=found.id,
            workflow=found.name,
            enabled=found.enabled,
            order=found.order,
            document_id=document.id,
            would_match_now=_overall(found, triggers),
            did_run="yes" if ran else "unknown",
            message=_message(found, triggers, ran=ran),
            triggers=tuple(triggers),
            log=tuple(findings),
            log_from=facts.records[0].time.isoformat(sep=" ") if facts.records else None,
            not_checked=tuple(facts.missing),
        )
        return _bounded(answer)

    async def _names(self, workflow: Workflow) -> dict[str, dict[int, str]]:
        kinds: list[TaxonomyKind] = []
        for name, setting in TRIGGER_FIELDS.items():
            used = any(not is_empty(t.settings.get(setting.wire)) for t in workflow.triggers)
            if name.startswith("has_") and setting.kind and used and setting.kind not in kinds:
                kinds.append(setting.kind)
        names: dict[str, dict[int, str]] = {}
        for kind in kinds:
            try:
                names[kind] = {item.id: item.name for item in await self._taxonomy.items(kind)}
            except PermissionDenied:
                names[kind] = {}
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
            facts.history = (await self._documents.list_document_history(number))[:MAX_HISTORY]

        async def log() -> None:
            facts.records = await read_log_records(self._management, LOG)

        found: list[tuple[str, Callable[[], Awaitable[None]]]] = []
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
            facts.task = next((t for t in page.rows if facts.document.id in t.document_ids), None)
            if facts.task is None:
                facts.missing.append("the consume task of the document was not found")

        return task

    def _asking(self, query: str, facts: _Facts) -> Callable[[], Awaitable[None]]:
        async def ask() -> None:
            wanted = DocumentQuery(ids=(facts.document.id,), custom_field_query=query)
            try:
                page = await self._documents.search_documents(wanted, page=1, page_size=1)
            except Validation:
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
        told += " The log says that it ran on this document."
    else:
        told += " Whether it ran is not known."
    return f"{subject} {told}"


def _bounded(answer: WorkflowExplanation) -> WorkflowExplanation:
    """``answer`` within ``MAX_CHARS`` characters: the last triggers go first, and that is said."""
    left = answer
    while len(json.dumps(left.model_dump(mode="json"))) > MAX_CHARS and left.triggers:
        kept = left.triggers[:-1]
        gone = len(answer.triggers) - len(kept)
        said = f"{gone} of the triggers are left out: the answer would be too long"
        left = left.model_copy(
            update={"triggers": kept, "not_checked": (*answer.not_checked, said)}
        )
    return left
