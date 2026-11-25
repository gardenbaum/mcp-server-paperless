"""One processed mail: its whole error, and what happens without its record (decision-80).

Paperless fetches a mail that matches a rule, was not yet acted on by the rule's action and has no
record of this rule, folder and uid. So a record is only one of the things that keep a mail out,
and what happens without it follows from the rule as it is now and the status of the record.
"""

from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import (
    DESTINATION_LIMIT,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    both_ends,
    quoted,
)
from mcp_server_paperless.domain.mail import ProcessedMailDetail, Reference
from mcp_server_paperless.services.mail import does

if TYPE_CHECKING:
    from mcp_server_paperless.domain.mail import MailRule, ProcessedMail
    from mcp_server_paperless.domain.ports import MailReader

# Characters of an error in one answer (the size of a content slice).
ERROR_SLICE: Final = 20_000
# Records of a rule that are read to find others of the same mail.
MAX_RECORDS: Final = 1000
PAGE: Final = 100
NEXT: Final = "the next fetch (every 10 minutes by default, or process_mail_account)"
AGAIN: Final = "a second time, unless Paperless rejects duplicates"
CAVEAT: Final = (
    "The rule's settings of now count, not those of then; whether the mail still exists on the "
    "server, and records you may not see, are not known here."
)
BOUNDED: Final = (
    f"Only the newest {MAX_RECORDS:,} records of the rule were looked at for others of the same "
    "mail."
)
OTHERS: Final = (
    "Other records with the folder and uid of this mail keep it out until they are deleted too"
)
ANEW: Final = (
    "(unless the folder was made anew on the server since: then a uid can mean another mail)"
)
NO_RULE: Final = (
    "Not known: you may not see the rule of this mail, which decides whether it is fetched again "
    "without this record."
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


def _outcome(status: str, rule: MailRule) -> str:
    """What happens to a mail with this status once its record is gone."""
    marked = _marked(rule)
    matching = "if it still matches the rule's folder, age and filters"
    unmarked = matching + (f" and is not {marked}" if marked else "")
    if status == "FAILED":
        return (
            f"without this record {NEXT} takes the mail again {unmarked}: all its files are "
            f"imported, also those that were imported before the failure ({AGAIN}), then the rule "
            f"{_then(rule)}."
        )
    if status == "PROCESSED_WO_CONSUMPTION":
        return (
            f"without this record {NEXT} looks at the mail again {unmarked}. Nothing happens to "
            "it unless the rule or the mail now gives a file: then that is imported and the rule "
            f"{_then(rule)}."
        )
    if status != "SUCCESS":
        return f"not known for the status {status}."
    again = (
        f"{NEXT} takes it again {matching}: its documents are imported again ({AGAIN}), then the "
        f"rule {_then(rule)}."
    )
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


class ProcessedMailService:
    """Reads one processed mail for the current user."""

    def __init__(self, reader: MailReader) -> None:
        """Read through ``reader``."""
        self._reader: MailReader = reader

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
            refetch=NO_RULE if rule is None else await self._refetch(mail, rule),
        )

    async def _rule(self, rule_id: int) -> MailRule | None:
        try:
            return await self._reader.get_mail_rule(rule_id)
        except NotFound, PermissionDenied:
            return None

    async def _refetch(self, mail: ProcessedMail, rule: MailRule) -> str:
        """Whether and when the mail is fetched again without its record, and what follows."""
        first: list[str] = []
        if not rule.enabled:
            first.append("The rule is disabled: it fetches nothing until it is switched on.")
        if rule.folder != mail.folder:
            folders = f"{quoted(rule.folder)}, not {quoted(mail.folder)}"
            first.append(
                f"The rule now reads folder {folders}: it does not look at this mail while so."
            )
        last = [CAVEAT]
        try:
            others, complete = await self._others(mail)
        except PaperlessMcpError:
            last.append("Other records of the same mail could not be looked for.")
        else:
            if others:
                ids = ", ".join(map(str, others))
                first.append(f"{OTHERS}: {ids} {ANEW}.")
            if not complete:
                last.append(BOUNDED)
        outcome = _outcome(mail.status, rule)
        said = f"Otherwise: {outcome}" if first else outcome[0].upper() + outcome[1:]
        return " ".join([*first, said, *last])

    async def _others(self, mail: ProcessedMail) -> tuple[list[int], bool]:
        """Ids of other records of the same mail, and whether all records were looked at."""
        others: list[int] = []
        for page in range(1, MAX_RECORDS // PAGE + 1):
            found = await self._reader.list_processed_mail(
                rule_id=mail.rule_id,
                status=None,
                ordering="-processed",
                page=page,
                page_size=PAGE,
            )
            others += [
                row.id
                for row in found.mails
                if row.id != mail.id and (row.folder, row.uid) == (mail.folder, mail.uid)
            ]
            if found.next_page is None:
                return sorted(others), True
        return sorted(others), False
