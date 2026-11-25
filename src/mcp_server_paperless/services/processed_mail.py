"""Processed mail: one record with its whole error, and deleting records (decisions 80 and 83).

Paperless fetches a mail that matches a rule, was not yet acted on by the rule's action and has no
record of this rule, folder and uid. So a record is only one of the things that keep a mail out,
and what happens without it follows from the rule as it is now and the status of the record.

Paperless skips ids that do not exist and names every id it was sent as deleted, so each record is
read before, and again afterwards. Nothing is sent twice.

Residual race: between the last read and the deletion, and until the next fetch, someone may change
the rule or the mail.
"""

import math
import time
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    DESTINATION_LIMIT,
    MAX_CONFIRMATION,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
    both_ends,
    confirmation_length,
    quoted,
)
from mcp_server_paperless.domain.mail import (
    DeletedRecord,
    ProcessedMailDeletion,
    ProcessedMailDetail,
    Reference,
)
from mcp_server_paperless.services.bulk_documents import DRY_RUN, TO_GO_AHEAD
from mcp_server_paperless.services.document_runner import (
    OUT_OF_TIME,
    RUN_SECONDS,
    Ended,
    cut,
    one_by_one,
    refused,
    stopped_by,
)
from mcp_server_paperless.services.mail import does, mailbox
from mcp_server_paperless.services.sharing import plural

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from mcp_server_paperless.domain.documents import SignedIn
    from mcp_server_paperless.domain.mail import (
        MailRule,
        MailScope,
        ProcessedMail,
        RecordOutcome,
    )
    from mcp_server_paperless.domain.ports import DocumentReader, MailReader, MailWriter

# Characters of an error in one answer (the size of a content slice).
ERROR_SLICE: Final = 20_000
# Records of a rule that are read to find others of the same mail.
MAX_RECORDS: Final = 1000
PAGE: Final = 100
KNOWN: Final = frozenset({"SUCCESS", "FAILED", "PROCESSED_WO_CONSUMPTION"})
# Records one call deletes, other records of a mail its confirmation names, and characters of a
# subject.
MAX_DELETED: Final = 100
NAMED: Final = 3
FEWER: Final = "Delete fewer records per call."
NOTHING: Final = "Nothing was deleted."
SUBJECT: Final = 60
NEXT: Final = (
    "the next fetch (every 10 minutes by default, or process_mail_account; an earlier rule of the "
    "account can stop it or take the mail first)"
)
AGAIN: Final = "again, if {} before, unless Paperless rejects duplicates"
CAVEAT: Final = (
    "The rule's settings of now count, not those of then; whether the mail still exists on the "
    "server, and records you may not see, are not known here. A record keeps out the mail with its "
    "folder and uid: one stored without the folder's UIDVALIDITY any such mail, one with an older "
    "UIDVALIDITY none, unless the server names none; which it is is not shown."
)
BOUNDED: Final = (
    f"Only the newest {MAX_RECORDS:,} records of the rule were looked at for others of the same "
    "mail."
)
OTHERS: Final = (
    "Other records with the folder and uid of this mail keep it out until they are deleted too"
)
NO_LONGER: Final = "Once that no longer holds: "
# What a rule imports of a mail, and when its action runs. The mail itself and its attachments are
# queued apart, each with the action behind it.
IMPORTED: Final[Mapping[MailScope, str]] = {
    "attachments": (
        f"the files the rule takes from it are imported ({AGAIN.format('they were')}); if every "
        "one of them is consumed"
    ),
    "eml": f"the mail itself is imported ({AGAIN.format('it was')}); if it is consumed",
    "eml_and_attachments": (
        "the mail itself and the files the rule takes from it are imported "
        f"({AGAIN.format('they were')}); as soon as the mail itself, or every one of the files, is "
        "consumed, even if the other fails"
    ),
}
HIDDEN: Final = (
    "you may not see the rule of this mail: without this record it may be imported again and then "
    "deleted or moved on the mail server"
)


def _marked(rule: MailRule) -> str | None:
    """How a mail is marked that the rule's action was run on; None if it is taken away instead."""
    parameter = rule.action_parameter or ""
    if rule.action == "tag" and "apple:" not in parameter.lower():
        return f"tagged {both_ends(parameter, DESTINATION_LIMIT)}"
    return {"mark_read": "read", "flag": "flagged", "tag": "flagged"}.get(rule.action)


def _then(rule: MailRule) -> str:
    if rule.action == "delete":
        return "DELETES the mail on the mail server"
    return does(rule.action, rule.action_parameter)


def _status(status: str) -> str:
    return status if status in KNOWN else quoted(status)


def _outcome(status: str, rule: MailRule) -> str:
    """What happens to a mail with this status once its record is gone."""
    marked = _marked(rule)
    matching = "if it still matches the rule's folder, age and filters"
    unmarked = matching + (f" and is not {marked}" if marked else "")
    taken = f"{IMPORTED[rule.consumption_scope]}, the rule then {_then(rule)}"
    nothing = status == "PROCESSED_WO_CONSUMPTION" and rule.consumption_scope == "attachments"
    if status in {"FAILED", "PROCESSED_WO_CONSUMPTION"} and not nothing:
        return f"without this record {NEXT} takes the mail again {unmarked}: {taken}"
    if nothing:
        return (
            f"without this record {NEXT} looks at the mail again {unmarked}. Nothing happens to "
            "it unless the rule or the mail now gives a file: then that is imported and, if it is "
            f"consumed, the rule {_then(rule)}"
        )
    if status != "SUCCESS":
        return f"not known for the status {_status(status)}"
    again = f"{NEXT} takes it again {matching}: {taken}"
    if marked:
        return (
            f"without this record the mail is not fetched while it is {marked} on the mail "
            f"server. If it is not, {again}"
        )
    return (
        f"the rule {does(rule.action, rule.action_parameter)}, so it should not be in the folder "
        "any more; a mail that was put back has a new uid and is fetched with or without this "
        f"record. If it is still there, {again}"
    )


def same_folder(one: str, other: str) -> bool:
    """Whether a mail server means the same folder: INBOX has no case, the others may."""
    return one == other or one.upper() == other.upper() == "INBOX"


def _obstacles(mail: ProcessedMail, rule: MailRule) -> str:
    """What keeps the rule from the mail as it is now."""
    said = ""
    if not rule.enabled:
        said += "The rule is disabled: it fetches nothing until it is switched on. "
    if not same_folder(rule.folder, mail.folder):
        folders = f"{quoted(rule.folder)}, not {quoted(mail.folder)}"
        said += f"The rule now reads folder {folders}: it does not look at this mail while so. "
    return said


def _others(mail: ProcessedMail, rule: MailRule | None, rows: Sequence[ProcessedMail]) -> list[int]:
    """Ids of the other records of the same mail among the records of its rule.

    Paperless compares their folder exactly with the one of the rule.
    """
    folder = rule.folder if rule and same_folder(rule.folder, mail.folder) else mail.folder
    return sorted(
        {
            row.id
            for row in rows
            if row.id != mail.id and (row.folder, row.uid) == (folder, mail.uid)
        }
    )


def _ids(ids: Iterable[int]) -> str:
    return ", ".join(map(str, ids))


@dataclass(frozen=True, slots=True)
class _Plan:
    """What a call would delete, and everything that decides what follows from it."""

    mails: tuple[ProcessedMail, ...]
    rules: dict[int, MailRule | None]
    mailboxes: dict[int, object]
    # The records of the same mail that stay, by the id of the one that goes.
    staying: dict[int, list[int]]
    # The rules of which not all records were looked at.
    bounded: tuple[int, ...]

    def _item(self, mail: ProcessedMail) -> str:
        rule = self.rules[mail.rule_id]
        name = f"rule {quoted(rule.name)}, id {rule.id}" if rule else f"rule id {mail.rule_id}"
        stay = self.staying.get(mail.id, [])
        kept = ""
        if stay:
            more = f" and {len(stay) - NAMED} more" if len(stay) > NAMED else ""
            which = f"{_ids(stay[:NAMED])}{more}"
            kept = f"; kept out by the records {which} with its uid, which stay for now"
        received = f"{mail.received.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
        return f"{mail.id} {quoted(mail.subject)} (received {received}, {name}{kept})"

    def _follows(self, mail: ProcessedMail) -> str:
        rule = self.rules[mail.rule_id]
        if rule is None:
            return HIDDEN
        said = _obstacles(mail, rule)
        outcome = _outcome(mail.status, rule)
        return f"{said[0].lower()}{said[1:]}{NO_LONGER}{outcome}" if said else outcome

    @property
    def effect(self) -> str:
        """Every record, with what follows once per kind of outcome."""
        groups: dict[tuple[str, str], list[ProcessedMail]] = {}
        for mail in self.mails:
            groups.setdefault((_status(mail.status), self._follows(mail)), []).append(mail)
        parts: list[str] = []
        for (status, follows), mails in groups.items():
            items = ", ".join(map(self._item, mails))
            which = "it" if len(mails) == 1 else "each"
            parts.append(f"{len(mails)} with status {status}: {items}. For {which}: {follows}.")
        for number in self.bounded:
            rule = self.rules[number]
            name = f" {quoted(rule.name)} (id {number})" if rule else ""
            parts.append(BOUNDED.replace("the rule", f"the rule{name}"))
        return (
            f"permanently delete {plural(len(self.mails), 'record')} of processed mail, which "
            f"cannot be brought back; documents and the mail itself stay. {' '.join(parts)} "
            f"{CAVEAT.removesuffix('.')}"
        )

    @property
    def too_long(self) -> str:
        """Why this cannot be confirmed, with the sizes; or nothing."""
        length = confirmation_length(self.effect, CONFIRM_HINT)
        if length <= MAX_CONFIRMATION:
            return ""
        return (
            f"the confirmation would be longer than what can be shown: {length} characters (at "
            f"most {MAX_CONFIRMATION}) for {plural(len(self.mails), 'record')}"
        )

    @property
    def scope(self) -> object:
        """What the confirmation is for, in the order of the call."""
        return (self.mails, self.rules, self.mailboxes, self.staying, self.bounded)


class ProcessedMailService:
    """Reads and deletes records of processed mail for the current user."""

    def __init__(
        self,
        reader: MailReader,
        writer: MailWriter,
        documents: DocumentReader,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Read through ``reader``, delete through ``writer``; ``documents`` names the caller."""
        self._reader: MailReader = reader
        self._writer: MailWriter = writer
        self._documents: DocumentReader = documents
        self._clock: Callable[[], float] = clock

    async def get(self, mail_id: int, *, error_from: int) -> ProcessedMailDetail:
        """One processed mail with ``ERROR_SLICE`` characters of its error from ``error_from``.

        Raises:
            NotFound: No such record, or the user may not see it.
            PermissionDenied: The user may not view processed mail.
        """
        mail = await self._reader.get_processed_mail(mail_id)
        rule = await self._rule(mail.rule_id)
        error = mail.error or ""
        end = error_from + ERROR_SLICE
        return ProcessedMailDetail(
            id=mail.id,
            rule=Reference(id=mail.rule_id, name=rule.name if rule else None),
            folder=mail.folder,
            uid=mail.uid,
            subject=mail.subject,
            received=mail.received,
            processed=mail.processed,
            status=mail.status,
            owner=mail.owner,
            error=None if mail.error is None else error[error_from:end],
            error_from=error_from,
            error_total=len(error),
            error_next=end if end < len(error) else None,
            refetch=await self._refetch(mail, rule),
        )

    async def _rule(self, rule_id: int) -> MailRule | None:
        try:
            return await self._reader.get_mail_rule(rule_id)
        except NotFound, PermissionDenied:
            return None

    async def _refetch(self, mail: ProcessedMail, rule: MailRule | None) -> str:
        """Whether and when the mail is fetched again without its record, and what follows."""
        first, last = _obstacles(mail, rule) if rule else "", CAVEAT
        try:
            rows, complete = await self._records(mail.rule_id)
        except PaperlessMcpError:
            last += " Other records of the same mail could not be looked for."
        else:
            if others := _others(mail, rule, rows):
                first += f"{OTHERS}: {_ids(others)}. "
            if not complete:
                last += f" {BOUNDED}"
        outcome = _outcome(mail.status, rule) if rule else HIDDEN
        said = f"{NO_LONGER}{outcome}" if first else outcome[0].upper() + outcome[1:]
        return f"{first}{said}. {last}"

    async def _records(
        self, rule_id: int, until: float = math.inf
    ) -> tuple[list[ProcessedMail], bool]:
        """The newest records of the rule, and whether these are all."""
        rows: list[ProcessedMail] = []
        for page in range(1, MAX_RECORDS // PAGE + 1):
            self._in_time(until)
            try:
                found = await self._reader.list_processed_mail(
                    rule_id=rule_id, status=None, ordering="-processed", page=page, page_size=PAGE
                )
            except NotFound:  # the list got shorter meanwhile
                message = f"The records of rule {rule_id} changed while they were read."
                raise PaperlessMcpError(message, hint="Call this again.") from None
            rows += found.mails
            if found.next_page is None:
                return rows, True
        return rows, False

    def _in_time(self, until: float) -> None:
        if self._clock() >= until:
            message = f"The records could not be read within {RUN_SECONDS:g} seconds."
            raise PaperlessMcpError(message, hint=FEWER)

    # --- deleting

    async def delete(
        self, mail_ids: Sequence[int], *, dry_run: bool, confirm: bool
    ) -> ProcessedMailDeletion:
        """Delete 1 to ``MAX_DELETED`` records after a confirmation; ``dry_run`` only shows it.

        Raises:
            ConfirmationRequired: ``confirm`` is false and the user did not accept exactly this.
                Nothing was changed.
            Validation: No ids, too many, or one is listed twice, or the confirmation would
                be longer than ``MAX_CONFIRMATION``; nothing was deleted.
            NotFound: A record does not exist or is hidden; every one is named.
            PermissionDenied: The user may not view or delete the records; nothing was deleted.
            PaperlessMcpError: The records could not be read in time; nothing was deleted.
        """
        until = self._clock() + RUN_SECONDS
        try:
            plan = await self._plan(mail_ids, until)
            self._in_time(until)
        except PaperlessMcpError as error:
            _not_done(error)
            raise
        if dry_run:
            said = f"{DRY_RUN} Confirming would {plan.effect}. {TO_GO_AHEAD}"
            if plan.too_long:
                said = (
                    f"{DRY_RUN} This call would be refused: {plan.too_long}. {FEWER} "
                    f"Confirmed, it would {plan.effect}."
                )
            return _report(plan.mails, {}, said, dry_run=True)
        if plan.too_long:
            message = f"{plan.too_long[0].upper()}{plan.too_long[1:]}. {NOTHING}"
            raise Validation(message, hint=FEWER)
        if not confirm:
            ConfirmationRequired.require(plan.effect, scope=plan.scope)
        failure = ""
        try:
            await self._writer.delete_processed_mail(mail_ids)
        except PaperlessMcpError as error:
            if refused(error):
                _not_done(error)
                raise
            failure = f" Paperless did not confirm the deletion: {error.message}"
        return await self._read_back(plan.mails, failure, until)

    async def _plan(self, mail_ids: Sequence[int], until: float) -> _Plan:
        if not 1 <= len(mail_ids) <= MAX_DELETED:
            message = f"Pass 1 to {MAX_DELETED} ids of processed mail."
            raise Validation(message, hint="list_processed_mail shows them.")
        if len(set(mail_ids)) != len(mail_ids):
            message = "An id is listed twice."
            raise Validation(message, hint="List every id once.")
        user = await self._caller(until)
        mails = await self._mails(mail_ids, until)
        _check_owners(mails, user)
        rules: dict[int, MailRule | None] = {}
        mailboxes: dict[int, object] = {}
        staying: dict[int, list[int]] = {}
        bounded: list[int] = []
        for number in dict.fromkeys(mail.rule_id for mail in mails):
            self._in_time(until)
            rule = rules[number] = await self._rule(number)
            if rule is not None and rule.account_id not in mailboxes:
                self._in_time(until)
                mailboxes[rule.account_id] = await self._mailbox(rule.account_id)
            rows, complete = await self._records(number, until)
            if not complete:
                bounded.append(number)
            for mail in mails:
                others = _others(mail, rule, rows) if mail.rule_id == number else []
                stay = [other for other in others if other not in mail_ids]
                if stay:
                    staying[mail.id] = stay
        return _Plan(tuple(mails), rules, mailboxes, staying, tuple(bounded))

    async def _mails(self, mail_ids: Sequence[int], until: float) -> list[ProcessedMail]:
        mails: list[ProcessedMail] = []
        missing: list[int] = []
        for number in mail_ids:
            self._in_time(until)
            try:
                mails.append(await self._reader.get_processed_mail(number))
            except NotFound:
                missing.append(number)
        if missing:
            message = f"No processed mail {_ids(missing)} exists that this user may see."
            hint = "Leave them out; list_processed_mail shows the records."
            raise NotFound(message, hint=hint)
        return mails

    async def _mailbox(self, account_id: int) -> object:
        try:
            return mailbox(await self._reader.get_mail_account(account_id))
        except NotFound, PermissionDenied:
            return None

    async def _caller(self, until: float) -> SignedIn | None:
        """The user of the call, if known.

        Raises:
            PermissionDenied: The user is known to lack the permission.
        """
        self._in_time(until)
        user = await self._documents.get_signed_in()
        if user is None or user.is_superuser or user.permissions is None:
            return user
        if "delete_processedmail" not in user.permissions:
            message = "You lack the permission delete_processedmail."
            raise PermissionDenied(message, hint="Ask an administrator for it.")
        return user

    async def _read_back(
        self, mails: Sequence[ProcessedMail], failure: str, until: float
    ) -> ProcessedMailDeletion:
        """What became of each record: Paperless names what it was asked for, not what is gone."""

        async def look(mail: ProcessedMail) -> Ended[tuple[int, RecordOutcome]]:
            try:
                _ = await self._reader.get_processed_mail(mail.id)
            except NotFound:
                return Ended((mail.id, "deleted"))
            except PaperlessMcpError as error:
                return Ended((mail.id, "unknown"), stop=stopped_by(error), silent=True)
            return Ended((mail.id, "still_there"))

        rows, stopped = await one_by_one(mails, look, until=until, clock=self._clock)
        outcomes = dict(rows)
        for mail in mails:
            _ = outcomes.setdefault(mail.id, "unknown")
        by = {
            outcome: [number for number, found in outcomes.items() if found == outcome]
            for outcome in ("deleted", "still_there", "unknown")
        }
        seen = "seen to be deleted" if by["unknown"] else "deleted"
        verb = "was" if len(mails) == 1 else "were"
        said = f"{len(by['deleted'])} of {plural(len(mails), 'record')} {verb} {seen}."
        if by["still_there"]:
            said += f" Still there: {_ids(by['still_there'])}."
        if by["unknown"]:
            said += f" Not known: {_ids(by['unknown'])}. {stopped}".rstrip()
        said += failure
        if failure or by["unknown"]:
            said += (
                " It is not repeated: check with list_processed_mail before you call this again."
            )
        if stopped == OUT_OF_TIME:
            said += f" {FEWER}"
        return _report(mails, outcomes, said, dry_run=False)


def _check_owners(mails: Sequence[ProcessedMail], user: SignedIn | None) -> None:
    """Say beforehand what Paperless is known to refuse.

    Raises:
        PermissionDenied: A record belongs to another user.
    """
    if user is None or user.is_superuser:
        return
    foreign = [mail.id for mail in mails if mail.owner not in {None, user.id}]
    if foreign:
        message = (
            f"You do not own the records {_ids(foreign)}, and they have an owner: Paperless would "
            "refuse all."
        )
        raise PermissionDenied(message, hint="Leave them out.")


def _not_done(error: PaperlessMcpError) -> None:
    """Add to ``error`` that nothing was deleted."""
    if not error.message.endswith(NOTHING):
        error.message = f"{error.message.removesuffix('.')}. {NOTHING}"


def _report(
    mails: Sequence[ProcessedMail],
    outcomes: Mapping[int, RecordOutcome],
    message: str,
    *,
    dry_run: bool,
) -> ProcessedMailDeletion:
    return ProcessedMailDeletion(
        dry_run=dry_run,
        mails=tuple(
            DeletedRecord(
                id=mail.id,
                rule_id=mail.rule_id,
                subject=cut(mail.subject, SUBJECT),
                status=mail.status,
                outcome=outcomes.get(mail.id, "would_delete"),
            )
            for mail in mails
        ),
        message=message,
    )
