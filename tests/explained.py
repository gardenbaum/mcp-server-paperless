"""Rules that together bring up every check of explain_mail_import, and what each must say.

Shared by the tests of the tool and the contract tests, so both cover the same checks.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from mcp_server_paperless.domain.mail import MailFacts

NOW: Final = datetime.now(UTC)
SUBJECT: Final = "Invoice of Acme"
# What the caller knows: a mail that fits, one that does not, and nothing.
FACTS: Final = (
    MailFacts(
        subject=SUBJECT,
        sender="Acme <a@example.org>",
        recipient="Acme desk <me@example.org>",
        received=NOW - timedelta(days=10),
        folder="inbox",
        attachment_names=("a.PDF",),
        is_read=False,
        is_flagged=False,
    ),
    MailFacts(
        subject="Other",
        sender="b@example.org",
        recipient="you@example.org",
        received=NOW - timedelta(days=99),
        folder="Elsewhere",
        attachment_names=("a.txt",),
        is_read=True,
        is_flagged=True,
    ),
    MailFacts(),
)
KNOWN: Final = ("ok", "no", "unknown")
NO: Final = ("no", "no", "no")
OK: Final = ("ok", "ok", "ok")
NONE: Final = (None, None, None)
NOT_KNOWN: Final = ("unknown", "unknown", "unknown")


@dataclass(frozen=True)
class Case:
    """A rule with these settings, a check, and its result for each of `FACTS`."""

    name: str
    what: str
    fields: dict[str, object]
    results: tuple[str | None, str | None, str | None]


CASES: Final = (
    Case("disabled", "enabled", {"enabled": False}, NO),
    Case("folder", "folder", {"folder": "INBOX"}, KNOWN),
    Case("any age", "age", {"maximum_age": 0}, OK),
    Case("age", "age", {"maximum_age": 30}, KNOWN),
    Case("age at the edge", "age", {"maximum_age": 10}, ("unknown", "no", "unknown")),
    Case("sender", "sender", {"filter_from": "ACME"}, KNOWN),
    Case("recipient", "recipient", {"filter_to": "acme"}, KNOWN),
    Case("subject", "subject", {"filter_subject": "acme"}, KNOWN),
    Case("body", "body", {"filter_body": "acme"}, NOT_KNOWN),
    Case(
        "included files",
        "attachments",
        {"filter_attachment_filename_include": "*.pdf"},
        ("unknown", "no", "unknown"),
    ),
    Case(
        "excluded files",
        "attachments",
        {"filter_attachment_filename_exclude": "*.txt"},
        ("unknown", "no", "unknown"),
    ),
    Case("whole mail", "attachments", {"consumption_scope": "eml"}, OK),
    Case("read", "mark", {"action": "mark_read"}, KNOWN),
    Case("flagged", "mark", {"action": "flag"}, KNOWN),
    Case("tagged", "mark", {"action": "tag", "action_parameter": "done"}, NOT_KNOWN),
    Case("coloured", "mark", {"action": "tag", "action_parameter": "apple:red"}, KNOWN),
    Case("no colour", "mark", {"action": "tag", "action_parameter": "apple:pink"}, NO),
    Case("two colours", "mark", {"action": "tag", "action_parameter": "apple:red:blue"}, NO),
    Case("moved", "target", {"action": "move", "action_parameter": "Done"}, NOT_KNOWN),
    Case("deleted", "mark", {"action": "delete"}, NONE),
    Case("recorded", "record", {}, ("unknown", None, None)),
    Case("first", "earlier rules", {"folder": "Elsewhere", "stop_processing": True}, NONE),
    Case("stopped before", "earlier rules", {"folder": "Third"}, NOT_KNOWN),
    Case("taken before", "earlier rules", {"folder": "Elsewhere"}, NOT_KNOWN),
)
RECORDED: Final = "recorded"
