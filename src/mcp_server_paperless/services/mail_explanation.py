"""Why a mail was not imported, from what Paperless has stored (decision-90).

This server contacts no mail server, so nothing here is a dry run: the settings of the rules are
compared with what the caller knows of the mail, and records, tasks and logs are shown. The mail
server decides how it searches, so a check that says no means "most likely".

Paperless 3.5.1 takes the rules of an account in their order; searches each one's folder for mail of
its age and filters that its action has not marked; leaves out the uids with a record of the rule;
and, in one run, what an earlier rule has queued. ``mail_fetch`` tasks say nothing of the outcome
(``result_data`` is null), so a fetch that was skipped is only in the log ``paperless``.
"""

import time
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import (
    PaperlessMcpError,
    PermissionDenied,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.mail import (
    ExplainedRecord,
    ExplainedTask,
    MailCheck,
    MailExplanation,
    RuleChecks,
)
from mcp_server_paperless.services.document_runner import RUN_SECONDS, cut, stopped_by
from mcp_server_paperless.services.log_records import read_log_records
from mcp_server_paperless.services.mail import one_line
from mcp_server_paperless.services.mail_log_lines import about_mail, about_run, skipped
from mcp_server_paperless.services.processed_mail import MAX_RECORDS, PAGE, same_folder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from mcp_server_paperless.domain.mail import (
        CheckResult,
        MailAccount,
        MailFacts,
        MailRule,
        ProcessedMail,
    )
    from mcp_server_paperless.domain.ports import AdminReader, MailReader
    from mcp_server_paperless.services.log_records import LogRecord

NO_DRY_RUN: Final = (
    "This is not a dry run: no mail server was contacted, and whether the mail exists is not known."
)
CLOSEST: Final = (
    "The closest way to see what a rule does: put the mail into an IMAP folder of its own, make a "
    "rule limited to that folder whose action is mark_read or flag, call process_mail_account, "
    "then list_processed_mail. That imports the mail; the API has no dry run of rules."
)
EXPIRED: Final = (
    "The access token has expired: Paperless renews it at the next fetch, and fetches nothing if "
    "that fails."
)
# Rows of each kind in one answer, and its length.
MAX_PROCESSED: Final = 20
MAX_TASKS: Final = 5
FAILED_READ: Final = 25
MAX_LOGS: Final = 20
LOG_CHARS: Final = 4000
MAX_ANSWER: Final = 50_000
SUBJECT: Final = 256
NOT_STAFF: Final = "Logs need a staff user."
OTHERS: Final = "records and tasks of others that you may not see are not looked at"
UNKNOWN_SKIPS: Final = "whether a fetch was skipped is not known"
# Days at the edge of an age that are unsure: the server counts whole days of its own time zone.
DOUBT: Final = 2
NAMED: Final = 3
# What is not checked, in one answer, and the length of each.
MAX_GAPS: Final = 18
GAP_CHARS: Final = 300
# The colours of Apple Mail that Paperless knows.
COLOURS: Final = frozenset({"red", "orange", "yellow", "blue", "green", "violet", "grey"})


def _check(what: str, result: CheckResult, why: str) -> MailCheck:
    return MailCheck(what=what, result=result, why=why)


def _folder(rule: MailRule, mail: MailFacts) -> MailCheck:
    reads = f"the rule reads folder {quoted(rule.folder)}"
    if mail.folder is None:
        return _check("folder", "unknown", f"{reads}; the mail's folder was not given")
    same = same_folder(rule.folder, mail.folder)
    if same:
        return _check("folder", "ok", reads)
    return _check("folder", "no", f"{reads}, so it most likely does not see the mail")


def _age(rule: MailRule, mail: MailFacts, now: datetime) -> MailCheck:
    if rule.maximum_age <= 0:
        return _check("age", "ok", "the rule takes mail of any age")
    limit = f"the rule takes mail up to {rule.maximum_age} days old, by the mail server's date"
    if mail.received is None:
        return _check("age", "unknown", f"{limit}; when it was received was not given")
    received = mail.received if mail.received.tzinfo else mail.received.replace(tzinfo=UTC)
    try:
        day = received.astimezone(UTC).date()
    except OverflowError:  # the first or the last day there is
        day = received.date()
    days = (day - (now - timedelta(days=rule.maximum_age)).date()).days
    if abs(days) <= DOUBT:
        return _check("age", "unknown", f"{limit}; it is at the edge, whole days count")
    if days > 0:
        return _check("age", "ok", limit)
    return _check("age", "no", f"{limit}, so it is most likely too old; the server decides")


def _contains(what: str, wanted: str | None, given: str | None) -> list[MailCheck]:
    if not wanted:
        return []
    asks = f"the {what} has to contain {quoted(wanted)}"
    if given is None:
        return [_check(what, "unknown", f"{asks}; it was not given")]
    if wanted.casefold() in given.casefold():
        return [_check(what, "ok", asks)]
    return [_check(what, "no", f"{asks}, so it most likely does not match; the server decides")]


def _matches(patterns: str, name: str) -> bool:
    """Paperless's own comparison: a list with commas, without case, the whole name."""
    return any(p and fnmatch(name.lower(), p.lower()) for p in patterns.split(","))


def _attachments(rule: MailRule, mail: MailFacts) -> MailCheck:
    if rule.consumption_scope != "attachments":
        return _check("attachments", "ok", "the rule imports the mail itself")
    if mail.attachment_names is None:
        return _check("attachments", "unknown", "the names of the attachments were not given")
    include = rule.filter_attachment_filename_include
    exclude = rule.filter_attachment_filename_exclude
    left = [
        name
        for name in mail.attachment_names
        if (not include or _matches(include, name)) and not (exclude and _matches(exclude, name))
    ]
    if not left:
        why = "no attachment is left" if mail.attachment_names else "the mail has no attachment"
        return _check("attachments", "no", f"{why}; the rule takes attachments only")
    unsure = (
        "whether Paperless supports their type is not known"
        if rule.attachment_type == "all_files"
        else "whether Paperless supports their type, and whether they are inline, is not known"
    )
    return _check(
        "attachments", "unknown", f"{quoted(left[0])} passes the file name filters; {unsure}"
    )


def _marked(rule: MailRule, mail: MailFacts) -> list[MailCheck]:
    """What the action of the rule leaves out: mail that it has marked already."""
    parameter = rule.action_parameter or ""
    if rule.action == "move":
        why = f"the rule fails if the folder {quoted(parameter)} does not exist on the mail server"
        return [_check("target", "unknown", why)]
    if rule.action == "delete":
        return []
    if rule.action == "tag" and "apple:" not in parameter.lower():
        why = f"mail that is tagged {quoted(parameter)} on the server is left out"
        return [_check("mark", "unknown", why)]
    if rule.action == "tag":
        _, *colours = parameter.split(":")
        if len(colours) != 1 or colours[0].strip().lower() not in COLOURS:
            why = f"{quoted(parameter)} is no colour Paperless knows, so the rule fails"
            return [_check("mark", "no", why)]
    known, state = (
        (mail.is_read, "read") if rule.action == "mark_read" else (mail.is_flagged, "flagged")
    )
    why = f"mail that is {state} on the server is left out"
    result: CheckResult = "unknown" if known is None else "no" if known else "ok"
    return [_check("mark", result, f"{why}, most likely also this one" if known else why)]


def _blocking(rule: MailRule, rows: Sequence[ProcessedMail]) -> list[MailCheck]:
    """Records that keep the mail out if they are of it; their folder is compared exactly."""
    ids = sorted(row.id for row in rows if row.rule_id == rule.id and row.folder == rule.folder)
    if not ids:
        return []
    more = f" and {len(ids) - NAMED} more" if len(ids) > NAMED else ""
    why = (
        f"records {', '.join(map(str, ids[:NAMED]))}{more} have this subject: if one is of this "
        "mail, it keeps it out; get_processed_mail says what follows, delete_processed_mail "
        "deletes them"
    )
    return [_check("record", "unknown", why)]


def _earlier(rule: MailRule, rules: Sequence[MailRule]) -> list[MailCheck]:
    """What the rules before it in the same run can do."""
    # The order of rules with the same number is not known.
    before = [r for r in rules if r.enabled and r.id != rule.id and r.order <= rule.order]
    found: list[MailCheck] = []
    stopping = [r for r in before if r.stop_processing]
    if stopping:
        why = (
            f"rule {quoted(stopping[0].name)} (id {stopping[0].id}) ends the run once any document "
            "was queued in it"
        )
        found.append(_check("earlier rules", "unknown", why))
    # Paperless keeps the mail it queued by the folder as it is written in the rule.
    taking = [r for r in before if r.folder == rule.folder]
    if taking:
        why = (
            f"rule {quoted(taking[0].name)} (id {taking[0].id}) reads the same folder first; what "
            "it queues is left out in that run"
        )
        found.append(_check("earlier rules", "unknown", why))
    return found


def rule_checks(
    rule: MailRule,
    rules: Sequence[MailRule],
    rows: Sequence[ProcessedMail],
    mail: MailFacts,
    now: datetime,
) -> RuleChecks:
    """What can be said of ``rule``, one of the ``rules`` of its account, and the mail.

    ``rows`` are the records with its subject.
    """
    if not rule.enabled:
        checks = [_check("enabled", "no", "the rule is disabled: it fetches nothing")]
    else:
        body = "the body has to contain a text, which only the mail server can tell"
        checks = [
            _folder(rule, mail),
            _age(rule, mail, now),
            *_contains("sender", rule.filter_from, mail.sender),
            *_contains("recipient", rule.filter_to, mail.recipient),
            *_contains("subject", rule.filter_subject, mail.subject),
            *([_check("body", "unknown", body)] if rule.filter_body else []),
            _attachments(rule, mail),
            *_marked(rule, mail),
            *_blocking(rule, rows),
            *_earlier(rule, rules),
        ]
    results = {check.result for check in checks}
    result: CheckResult = "no" if "no" in results else "unknown" if "unknown" in results else "ok"
    return RuleChecks(rule_id=rule.id, name=rule.name, result=result, checks=tuple(checks))


def _account_notes(account: MailAccount, now: datetime) -> list[str]:
    notes: list[str] = []
    if account.is_token and account.expiration is not None and account.expiration < now:
        notes.append(EXPIRED)
    if not account.username:
        notes.append("The account has no username: set it with update_mail_account.")
    return notes


def _found(mail: MailFacts, row: ProcessedMail) -> bool:
    """Whether a record is about a mail with this subject, which Paperless cuts."""
    wanted = (mail.subject or "").strip()[:SUBJECT].strip()
    return row.subject.strip().casefold() == wanted.casefold()


def _message(rules: Sequence[MailRule] | None, chosen: Sequence[MailRule], skips: int) -> str:
    if rules is None:
        said = "The rules could not be read, so nothing is said of them."
    elif not chosen:
        said = "You see no rule of this account: without an enabled one Paperless fetches nothing."
    elif not any(rule.enabled for rule in chosen):
        said = "None of these rules is enabled, so they fetch nothing."
    else:
        said = "A check that says no most likely keeps the mail out; ok is no promise."
    if skips:
        fetches, it = ("1 fetch was", "it") if skips == 1 else (f"{skips} fetches were", "them")
        what = "it" if skips == 1 else "they"
        said += (
            f" The log shows that {fetches} skipped because another one was running: what {what} "
            f"would have fetched was not fetched by {it}; it may have been for other accounts."
        )
    return (
        f"{NO_DRY_RUN} {said} Rules of other users that you may not see can act on the account "
        "too, and are not checked."
    )


def _short(gaps: Sequence[str]) -> list[str]:
    """The gaps within their bounds: the rest is counted."""
    shown = [cut(gap, GAP_CHARS) for gap in gaps[:MAX_GAPS]]
    return [*shown, f"{len(gaps) - MAX_GAPS} more things"] if len(gaps) > MAX_GAPS else shown


def _bounded(found: MailExplanation) -> MailExplanation:
    """The explanation within ``MAX_ANSWER`` characters: later rules are left out and counted."""
    total = len(found.rules)
    shown = total
    while shown and len(found.model_dump_json()) > MAX_ANSWER:
        shown //= 2
        gap = f"{total - shown} of {total} rules: the answer would be too long; pass rule_id"
        others = tuple(g for g in found.not_checked if not g.endswith("pass rule_id"))
        found = found.model_copy(
            update={
                "rules": found.rules[:shown],
                "not_checked": (*others, gap),
                "complete": False,
            }
        )
    return found


class _Asking:
    """Asks one thing after the other until the time is over, and keeps what could not be read."""

    def __init__(self, clock: Callable[[], float]) -> None:
        """Start the time of the call."""
        self._clock: Callable[[], float] = clock
        self._until: float = clock() + RUN_SECONDS
        self.gaps: list[str] = []
        # What is not looked at on purpose, or can never be known.
        self.notes: list[str] = [OTHERS]

    async def ask[T](self, what: str, request: Callable[[], Awaitable[T]]) -> T | None:
        """The answer; None and a gap named ``what`` if there is none.

        Raises:
            PaperlessMcpError: Paperless asks for fewer requests, or rejects the credential.
        """
        if self._clock() >= self._until:
            self.gaps.append(f"{what}: {RUN_SECONDS:g} seconds have passed")
            return None
        try:
            return await request()
        except PaperlessMcpError as error:
            if stopped_by(error):
                raise
            self.gaps.append(f"{what}: {error.message}")
            return None


class MailExplanations:
    """Explains, for the current user, why a mail may not have been imported."""

    def __init__(
        self,
        reader: MailReader,
        management: AdminReader,
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Read accounts, rules and records through ``reader``, tasks and logs through the other."""
        self._reader: MailReader = reader
        self._management: AdminReader = management
        self._clock: Callable[[], float] = clock
        self._now: Callable[[], datetime] = now

    async def explain(
        self, account_id: int, mail: MailFacts, *, rule_id: int | None
    ) -> MailExplanation:
        """What is stored about the import of ``mail`` by the rules of an account.

        Raises:
            NotFound: No such account, or the user may not see it.
            PermissionDenied: The user may not view mail accounts.
            Validation: ``rule_id`` is none of the account's rules the user may see.
        """
        asking = _Asking(self._clock)
        now = self._now()
        account = await self._reader.get_mail_account(account_id)
        rules = await asking.ask("the rules", self._reader.list_mail_rules)
        mine = [rule for rule in rules or () if rule.account_id == account_id]
        chosen = [rule for rule in mine if rule_id in {None, rule.id}]
        if rules is not None and rule_id is not None and not chosen:
            message = f"Account {account_id} has no rule {rule_id} that you may see."
            raise Validation(message, hint="list_mail_rules shows the rules of the account.")
        rows = await self._rows(chosen, mail, asking)
        tasks = await self._tasks(asking)
        logs, since, skips = await self._logs(account, chosen, mail, asking)
        return _bounded(
            MailExplanation(
                message=_message(rules, chosen, skips),
                account=tuple(_account_notes(account, now)),
                rules=tuple(rule_checks(rule, mine, rows, mail, now) for rule in chosen),
                processed=_records(rows, asking),
                tasks=tasks,
                logs=logs,
                logs_from=since,
                closest_way=CLOSEST,
                complete=not asking.gaps,
                not_checked=(*_short(asking.gaps), *asking.notes),
            )
        )

    async def _rows(
        self, rules: Sequence[MailRule], mail: MailFacts, asking: _Asking
    ) -> list[ProcessedMail]:
        """The records of the rules with the subject of the mail, newest first per rule."""
        if not (mail.subject or "").strip():
            asking.gaps.append("processed mail: pass the subject of the mail")
            return []
        rows: list[ProcessedMail] = []
        for rule in rules:
            rows += [row for row in await self._records(rule, asking) if _found(mail, row)]
        return rows

    async def _records(self, rule: MailRule, asking: _Asking) -> list[ProcessedMail]:
        rows: dict[int, ProcessedMail] = {}
        what = f"processed mail of rule {quoted(rule.name)} (id {rule.id})"
        for page in range(1, MAX_RECORDS // PAGE + 1):
            found = await asking.ask(
                what,
                lambda page=page: self._reader.list_processed_mail(
                    rule_id=rule.id, status=None, ordering="-processed", page=page, page_size=PAGE
                ),
            )
            if found is None:
                break
            rows |= {row.id: row for row in found.mails}
            if found.next_page is None:
                break
        else:
            asking.gaps.append(f"{what}: only the newest {MAX_RECORDS:,} were looked at")
        return list(rows.values())

    async def _tasks(self, asking: _Asking) -> tuple[ExplainedTask, ...]:
        """The newest fetches, and the newest files from mails that could not be consumed."""
        fetches = await asking.ask(
            "fetches",
            lambda: self._management.list_tasks(
                state=None, task_type="mail_fetch", acknowledged=None, page=1, page_size=MAX_TASKS
            ),
        )
        failed = await asking.ask(
            "files that could not be consumed",
            lambda: self._management.list_tasks(
                state="failure",
                task_type="consume_file",
                acknowledged=None,
                page=1,
                page_size=FAILED_READ,
            ),
        )
        # Paperless fetches every ten minutes: older fetches alone leave the answer complete.
        for what, found, said in (
            ("fetches", fetches, asking.notes),
            ("files that could not be consumed", failed, asking.gaps),
        ):
            if found and found.count > len(found.rows):
                read = f"only the newest {len(found.rows)} of {found.count} were looked at"
                said.append(f"{what}: {read}")
        rows = failed.rows if failed else ()
        files = [row for row in rows if row.trigger_source == "email_consume"]
        if len(files) > MAX_TASKS:
            asking.gaps.append(
                f"{len(files) - MAX_TASKS} more files from mails that could not be consumed"
            )
        return tuple(
            ExplainedTask(
                task_type=row.task_type,
                state=row.state,
                created=row.created,
                message=one_line(row.message),
            )
            for row in [*(fetches.rows if fetches else ()), *files[:MAX_TASKS]]
        )

    async def _logs(
        self, account: MailAccount, rules: Sequence[MailRule], mail: MailFacts, asking: _Asking
    ) -> tuple[tuple[str, ...], tuple[str, ...], int]:
        """The newest lines about the account, its rules or a skipped fetch.

        Also how far back each log reaches, and how many fetches were skipped.
        """
        names = [rule.name for rule in rules]
        mails = await asking.ask("the log mail", lambda: self._log("mail"))
        runs = await asking.ask("the log paperless", lambda: self._log("paperless"))
        since: list[str] = []
        for name, records in (("mail", mails), ("paperless", runs)):
            if records:
                since.append(f"{name}: {records[0].time}")
            elif records is not None:
                asking.gaps.append(f"the log {name} is empty")
        if runs:
            asking.notes.append(f"whether a fetch was skipped before {runs[0].time} is not known")
        else:
            asking.gaps.append(UNKNOWN_SKIPS)
        skips = sum(map(skipped, runs or ()))
        told = sorted(
            [
                *(r for r in mails or () if about_mail(r, account.name, names, mail)),
                *(r for r in runs or () if skipped(r) or about_run(r, account.name)),
            ],
            key=lambda record: record.time,
        )
        lines: list[str] = []
        used = 0
        for record in reversed(told[-MAX_LOGS:]):
            line = f"{record.time} {one_line(record.message)}"
            if used + len(line) > LOG_CHARS:
                break
            lines.append(line)
            used += len(line)
        if len(lines) < len(told):
            asking.gaps.append(f"{len(told) - len(lines)} older log lines")
        return tuple(reversed(lines)), tuple(since), skips

    async def _log(self, name: str) -> list[LogRecord]:
        try:
            return await read_log_records(self._management, name)
        except PermissionDenied:
            raise PaperlessMcpError(NOT_STAFF) from None


def _records(rows: Sequence[ProcessedMail], asking: _Asking) -> tuple[ExplainedRecord, ...]:
    if len(rows) > MAX_PROCESSED:
        asking.gaps.append(f"{len(rows) - MAX_PROCESSED} more records with this subject")
    return tuple(
        ExplainedRecord(
            id=row.id,
            rule_id=row.rule_id,
            status=row.status,
            processed=row.processed,
            error=one_line(row.error),
        )
        for row in rows[:MAX_PROCESSED]
    )
