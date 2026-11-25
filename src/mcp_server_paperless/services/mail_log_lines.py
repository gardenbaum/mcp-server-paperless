"""Which records of the logs are about a mail account, its rules or a mail (decision-90).

Paperless 3.5.1 writes the log ``mail`` with the logger ``paperless_mail`` and tells of whole
fetches in the log ``paperless`` with ``paperless.mail.tasks``. Each form is matched from its start,
so that a name inside a subject names nothing. A line about a mail that names no rule can be about
a mail of another account with the same subject.
"""

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mcp_server_paperless.domain.mail import MailFacts
    from mcp_server_paperless.services.log_records import LogRecord

MAIL: Final = "paperless_mail"
TASKS: Final = "paperless.mail.tasks"
SKIPPED: Final = "Mail account processing is already running; skipping this run."
# What Paperless says of a rule, without naming a mail.
_RULE: Final = re.compile(
    r"""Skipping\ disabled\ rule | Stopping\ processing\ rules
    | Error\ while\ processing\ (?:rule|mail\ \S+):
    | Selecting\ folder\b | Searching\ folder\ with\ criteria\b | No\ new\ mail\ matching\b
    | Processed\ \d+\ matching\ mail""",
    re.VERBOSE,
)
# The forms that name a mail or a file of it.
_OF_MAIL: Final = re.compile(r"Processing mail |Consuming |Skipping attachment ")


def plain(text: str | None) -> str:
    """``text`` as it is compared: without case, and with single spaces."""
    return " ".join((text or "").split()).casefold()


def skipped(record: LogRecord) -> bool:
    """Whether a fetch did nothing because another one was running."""
    return record.logger == TASKS and record.message == SKIPPED


def about_run(record: LogRecord, account: str) -> bool:
    """Whether a record of the log ``paperless`` tells of a fetch of the account."""
    first = record.message.split("\n", 1)[0]
    return record.logger == TASKS and first in {
        f"No rules enabled for account {account}. Skipping.",
        f"Error while processing mail account {account}",
    }


def about_mail(record: LogRecord, account: str, rules: Iterable[str], mail: MailFacts) -> bool:
    """Whether a record of the log ``mail`` is about the account, one of the rules or the mail."""
    if record.logger != MAIL:
        return False
    message = record.message
    if message == f"Processing mail account {account}":
        return True
    if re.fullmatch(rf"Account {re.escape(account)}: Processing \d+ rule\(s\)", message):
        return True
    for phrase in ("authenticating account", "retrieving mailbox"):
        if message.startswith(f"Error while {phrase} {account}:"):
            return True
    for rule in rules:
        start = f"Rule {account}.{rule}: "
        if message.startswith(start):
            said = message.removeprefix(start)
            return _RULE.match(said) is not None or _of_mail(said, mail)
    subject = re.escape(plain(mail.subject))
    skipping = rf"skipping (?:duplicate fetched )?mail '\d+' subject '{subject}' from '"
    return bool(subject) and re.match(skipping, plain(message)) is not None


def _of_mail(said: str, mail: MailFacts) -> bool:
    """Whether what a rule says of a mail can be about this one."""
    if _OF_MAIL.match(said) is None:
        return False
    subject, line = plain(mail.subject), plain(said)
    if not subject:
        return True
    if line.startswith(f"processing mail {subject} from "):
        return True
    if line.startswith("consuming ") and f" from mail {subject} from " in line:
        return True
    names = filter(None, map(plain, mail.attachment_names or ()))
    return any(
        line.startswith((f"skipping attachment {name} ", f"consuming attachment {name} from mail "))
        for name in names
    )
