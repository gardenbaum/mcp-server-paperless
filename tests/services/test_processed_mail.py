"""ProcessedMailService: one processed mail, its whole error, and what follows without its record.

The rows of `refetch` are those of the table in the design of PLMCP-81 (2.2); the live tests of
PLMCP-81.2 prove them against a mail server.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, final

import pytest
from fakes.accounts import ADMIN
from fakes.mail import FakeMailAccount, FakeMailRule, FakeProcessedMail
from fakes.paperless import FakePaperless, FakeState, FakeUser, seeded

from mcp_server_paperless.domain.errors import NotFound, PermissionDenied
from mcp_server_paperless.domain.mail import Reference
from mcp_server_paperless.services.processed_mail import (
    ERROR_SLICE,
    MAX_RECORDS,
    ProcessedMailService,
)

if TYPE_CHECKING:
    from mcp_server_paperless.domain.mail import ProcessedMailDetail

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-80")]

DAY = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
NEXT: Final = "the next fetch (every 10 minutes by default, or process_mail_account)"
MATCHING: Final = "if it still matches the rule's folder, age and filters"
NOT_KNOWN: Final = (
    "The rule's settings of now count, not those of then; whether the mail still exists on the "
    "server is not known here."
)


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, permissions: set[str] | None = None) -> None:
        self.state: FakeState = seeded()
        if permissions is not None:
            self.state.users.append(
                FakeUser(id=77, username=user, permissions=frozenset(permissions))
            )
        self.fake: FakePaperless = FakePaperless(self.state, user=user)
        self.service: ProcessedMailService = ProcessedMailService(self.fake)
        self.state.mail_accounts.append(
            FakeMailAccount(
                id=1,
                name="Work",
                imap_server="h",
                imap_port=993,
                username="u",
                password="p",
                owner=2,
            )
        )

    def rule(self, **fields: object) -> None:
        values: dict[str, object] = {"id": 1, "name": "Bills", "account": 1, "owner": 2}
        self.state.mail_rules.append(FakeMailRule(**(values | fields)))  # pyright: ignore[reportArgumentType] -- test data

    def mail(self, **fields: object) -> None:
        number = len(self.state.processed_mail) + 1
        values: dict[str, object] = {"id": number, "rule": 1, "subject": "Invoice", "owner": 2}
        self.state.processed_mail.append(FakeProcessedMail(**(values | fields)))  # pyright: ignore[reportArgumentType] -- test data

    async def get(self, number: int = 1, error_from: int = 0) -> ProcessedMailDetail:
        return await self.service.get(number, error_from=error_from)

    async def refetch(self) -> str:
        return (await self.get()).refetch


async def test_a_processed_mail_is_read_with_its_rule_and_its_error() -> None:
    setup = Setup()
    setup.rule()
    setup.mail(uid="77", status="FAILED", error="a\nb", received=DAY, processed=DAY)

    found = await setup.get()

    assert found.model_dump(exclude={"refetch"}) == {
        "id": 1,
        "rule": {"id": 1, "name": "Bills"},
        "folder": "INBOX",
        "uid": "77",
        "subject": "Invoice",
        "received": DAY,
        "processed": DAY,
        "status": "FAILED",
        "owner": 2,
        "error": "a\nb",
        "error_from": 0,
        "error_total": 3,
        "error_next": None,
    }


async def test_a_mail_without_an_error_has_none() -> None:
    setup = Setup()
    setup.rule()
    setup.mail()

    found = await setup.get()

    assert (found.error, found.error_total, found.error_next) == (None, 0, None)


async def test_a_long_error_is_read_to_its_end_in_parts() -> None:
    setup = Setup()
    setup.rule()
    error = "".join(chr(ord("a") + n % 26) for n in range(2 * ERROR_SLICE + 5))
    setup.mail(status="FAILED", error=error)

    parts: list[str] = []
    start: int | None = 0
    while start is not None:
        found = await setup.get(error_from=start)
        assert (found.error_from, found.error_total) == (start, len(error))
        parts.append(found.error or "")
        start = found.error_next
    exact = Setup()
    exact.rule()
    exact.mail(error="e" * ERROR_SLICE)
    past = await setup.get(error_from=len(error) + 1)

    assert [len(part) for part in parts] == [ERROR_SLICE, ERROR_SLICE, 5]
    assert "".join(parts) == error
    assert (await exact.get()).error_next is None
    assert (past.error, past.error_total, past.error_next) == ("", len(error), None)


async def test_a_record_that_does_not_exist_is_not_found() -> None:
    with pytest.raises(NotFound, match="No processed mail 9"):
        _ = await Setup().get(9)


async def test_a_user_without_the_permission_is_denied() -> None:
    with pytest.raises(PermissionDenied):
        _ = await Setup("e2e-blind").get()


@pytest.mark.parametrize(
    "permissions", [{"view_processedmail"}, {"view_processedmail", "view_mailrule"}]
)
async def test_a_rule_the_user_may_not_see_is_its_id_and_the_rest_not_known(
    permissions: set[str],
) -> None:
    setup = Setup("mailman", permissions=permissions)
    setup.rule()
    setup.mail(owner=None)

    found = await setup.get()

    assert found.rule == Reference(id=1, name=None)
    assert found.refetch == (
        "Not known: you may not see the rule of this mail, which decides whether it is fetched "
        "again without this record."
    )


@pytest.mark.parametrize(
    ("action", "parameter", "state", "again"),
    [
        ("mark_read", None, "read", "marks the mail as read"),
        ("flag", None, "flagged", "flags the mail"),
        ("tag", "done", "tagged 'done'", "tags the mail 'done'"),
        ("tag", "Apple:red", "flagged", "tags the mail 'Apple:red'"),
    ],
)
async def test_a_mail_that_was_taken_stays_out_by_what_the_rule_did_to_it(
    action: str, parameter: str | None, state: str, again: str
) -> None:
    setup = Setup()
    setup.rule(action=action, action_parameter=parameter)
    setup.mail()

    assert await setup.refetch() == (
        f"Without this record the mail is not fetched while it is {state} on the mail server. If "
        f"it is not, {NEXT} takes it again {MATCHING}: its documents are imported again (a second "
        f"time, unless Paperless rejects duplicates), then the rule {again}. {NOT_KNOWN}"
    )


@pytest.mark.parametrize(
    ("action", "parameter", "gone", "again"),
    [
        ("delete", None, "deletes the mail", "DELETES the mail on the mail server"),
        ("move", "Done", "moves the mail to 'Done'", "moves the mail to 'Done'"),
    ],
)
async def test_a_mail_that_was_taken_away_is_only_taken_again_if_it_is_there(
    action: str, parameter: str | None, gone: str, again: str
) -> None:
    setup = Setup()
    setup.rule(action=action, action_parameter=parameter)
    setup.mail()

    assert await setup.refetch() == (
        f"The rule {gone}, so it should not be in the folder any more; a mail that was put back "
        f"has a new uid and is fetched with or without this record. If it is still there, {NEXT} "
        f"takes it again {MATCHING}: its documents are imported again (a second time, unless "
        f"Paperless rejects duplicates), then the rule {again}. {NOT_KNOWN}"
    )


@pytest.mark.parametrize(
    ("action", "condition", "then"),
    [
        ("mark_read", " and is not read", "marks the mail as read"),
        ("delete", "", "DELETES the mail on the mail server"),
    ],
)
async def test_a_failed_mail_is_fetched_again_and_the_rule_then_acts(
    action: str, condition: str, then: str
) -> None:
    setup = Setup()
    setup.rule(action=action)
    setup.mail(status="FAILED")

    assert await setup.refetch() == (
        f"Without this record {NEXT} takes the mail again {MATCHING}{condition}: all its files "
        "are imported, also those that were imported before the failure (a second time, unless "
        f"Paperless rejects duplicates), then the rule {then}. {NOT_KNOWN}"
    )


async def test_a_mail_without_a_file_is_looked_at_again_and_mostly_nothing_happens() -> None:
    setup = Setup()
    setup.rule(action="flag")
    setup.mail(status="PROCESSED_WO_CONSUMPTION")

    assert await setup.refetch() == (
        f"Without this record {NEXT} looks at the mail again {MATCHING} and is not flagged. "
        "Nothing happens to it unless the rule or the mail now gives a file: then that is "
        f"imported and the rule flags the mail. {NOT_KNOWN}"
    )


async def test_an_unknown_status_is_not_explained() -> None:
    setup = Setup()
    setup.rule()
    setup.mail(status="LATER")

    assert await setup.refetch() == f"Not known for the status LATER. {NOT_KNOWN}"


async def test_a_disabled_rule_and_another_folder_are_said_first() -> None:
    setup = Setup()
    setup.rule(enabled=False, folder="Bills")
    setup.mail(status="FAILED", folder="INBOX")

    text = await setup.refetch()

    start = (
        "The rule is disabled: it fetches nothing until it is switched on. The rule now reads "
        "folder 'Bills', not 'INBOX': it does not look at this mail while so. Otherwise: "
        "without this record the next fetch"
    )
    assert text.startswith(start)


async def test_other_records_of_the_same_mail_that_keep_it_out_are_named() -> None:
    setup = Setup()
    setup.rule(consumption_scope="eml_and_attachments")
    setup.mail(status="FAILED")
    setup.mail()
    setup.mail(uid="2")
    setup.mail(folder="Other")
    setup.mail(rule=2)
    setup.mail()

    text = await setup.refetch()

    start = (
        "Other records of the same mail keep it out until they are deleted too: 2, 6. "
        "Otherwise: without this record the next fetch"
    )
    assert text.startswith(start)


async def test_only_a_rule_that_can_write_several_records_is_searched() -> None:
    setup = Setup()
    setup.rule(consumption_scope="eml")
    setup.mail(status="FAILED")
    setup.mail()

    assert (await setup.refetch()).startswith("Without this record")


async def test_the_search_for_other_records_is_bounded_and_says_so() -> None:
    setup = Setup()
    setup.rule(consumption_scope="eml_and_attachments")
    setup.mail(status="FAILED", processed=DAY)
    for number in range(MAX_RECORDS):
        setup.mail(uid=f"x{number}", processed=DAY.replace(day=11))

    text = await setup.refetch()
    del setup.state.processed_mail[-1]
    whole = await setup.refetch()

    said = "Only the newest 1,000 records of the rule were looked at for others of the same mail."
    assert said in text
    assert said not in whole
