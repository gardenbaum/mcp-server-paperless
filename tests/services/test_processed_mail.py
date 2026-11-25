"""ProcessedMailService: one processed mail, its whole error, and what follows without its record.

The rows of `refetch` are those of the table in the design of PLMCP-81 (2.2); the live tests of
PLMCP-81.2 prove them against a mail server.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, final, override

import pytest
from fakes.accounts import ADMIN
from fakes.mail import FakeMailAccount, FakeMailRule, FakeProcessedMail
from fakes.paperless import FakePaperless, FakeState, FakeUser, seeded

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    AuthenticationFailed,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.domain.mail import Reference
from mcp_server_paperless.services.processed_mail import (
    ERROR_SLICE,
    MAX_RECORDS,
    ProcessedMailService,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.mail import (
        MailRule,
        ProcessedMail,
        ProcessedMailDeletion,
        ProcessedMailDetail,
    )

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
    def __init__(
        self,
        user: str = ADMIN[0],
        *,
        permissions: set[str] | None = None,
        writes: bool = False,
        make: type[FakePaperless] = FakePaperless,
    ) -> None:
        self.state: FakeState = seeded()
        if permissions is not None:
            self.state.users.append(
                FakeUser(id=77, username=user, permissions=frozenset(permissions))
            )
        self.fake: FakePaperless = make(self.state, user=user, allow_writes=writes)
        self.now: float = 0.0
        self.service: ProcessedMailService = ProcessedMailService(
            self.fake, self.fake, self.fake, clock=lambda: self.now
        )
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

    async def delete(
        self, *ids: int, confirm: bool = True, dry_run: bool = False
    ) -> ProcessedMailDeletion:
        return await self.service.delete(ids, dry_run=dry_run, confirm=confirm)

    def left(self) -> list[int]:
        return [mail.id for mail in self.state.processed_mail]

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

    assert await setup.refetch() == f"Not known for the status 'LATER'. {NOT_KNOWN}"


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


# --- deleting records


def failed_mails(setup: Setup, count: int = 1, **fields: object) -> None:
    for number in range(1, count + 1):
        setup.mail(uid=f"u{number}", status="FAILED", received=DAY, **fields)


def outcomes(report: ProcessedMailDeletion) -> list[tuple[int, str]]:
    return [(mail.id, mail.outcome) for mail in report.mails]


@pytest.mark.decision("decision-83")
async def test_the_confirmation_names_each_record_and_what_follows() -> None:
    setup = Setup()
    setup.rule(action="delete")
    setup.rule(id=2, name="Other", account=1)
    failed_mails(setup, 2)
    setup.mail(subject="Taken", rule=2, received=DAY)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.delete(1, 2, 3, confirm=False)

    assert raised.value.hint == CONFIRM_HINT
    assert raised.value.message == (
        "Nothing was changed. Confirming would permanently delete 3 records of processed mail, "
        "which cannot be brought back; documents and the mail itself stay. "
        "2 with status FAILED: 1 'Invoice' (received 2026-11-10 08:00 UTC, rule 'Bills', id 1), "
        "2 'Invoice' (received 2026-11-10 08:00 UTC, rule 'Bills', id 1). For each: without this "
        f"record {NEXT} takes the mail again {MATCHING}: all its files are imported, also those "
        "that were imported before the failure (a second time, unless Paperless rejects "
        "duplicates), then the rule DELETES the mail on the mail server. "
        "1 with status SUCCESS: 3 'Taken' (received 2026-11-10 08:00 UTC, rule 'Other', id 2). "
        "For it: without this record the mail is not fetched while it is read on the mail server. "
        f"If it is not, {NEXT} takes it again {MATCHING}: its documents are imported again (a "
        "second time, unless Paperless rejects duplicates), then the rule marks the mail as read. "
        f"{NOT_KNOWN}"
    )
    assert setup.left() == [1, 2, 3]


@pytest.mark.decision("decision-83")
async def test_a_preview_says_the_same_and_deletes_nothing() -> None:
    setup = Setup()
    setup.rule()
    failed_mails(setup)

    report = await setup.delete(1, dry_run=True)

    assert report.dry_run
    assert outcomes(report) == [(1, "would_delete")]
    assert report.message.startswith(
        "Nothing was changed. Confirming would permanently delete 1 record of processed mail,"
    )
    assert setup.left() == [1]


@pytest.mark.decision("decision-83")
async def test_confirmed_records_are_deleted_and_read_again() -> None:
    setup = Setup(writes=True)
    setup.rule()
    failed_mails(setup, 3)

    report = await setup.delete(3, 1)

    assert outcomes(report) == [(3, "deleted"), (1, "deleted")]
    assert not report.dry_run
    [first, _] = report.mails
    assert (first.subject, first.status, first.rule_id) == ("Invoice", "FAILED", 1)
    assert report.message == "2 of 2 records were deleted."
    assert setup.left() == [2]


@pytest.mark.decision("decision-83")
async def test_one_record_is_counted_as_one() -> None:
    setup = Setup(writes=True)
    setup.rule()
    failed_mails(setup)

    assert (await setup.delete(1)).message == "1 of 1 record was deleted."


@pytest.mark.decision("decision-83")
async def test_the_subject_in_the_result_is_cut() -> None:
    setup = Setup()
    setup.rule()
    setup.mail(subject="s" * 256)

    [row] = (await setup.delete(1, dry_run=True)).mails

    assert row.subject == "s" * 59 + "…"


@pytest.mark.decision("decision-83")
@pytest.mark.parametrize("dry_run", [False, True])
async def test_a_missing_record_refuses_everything_and_every_one_is_named(dry_run: bool) -> None:  # noqa: FBT001 -- the case
    """Paperless would skip it and say it deleted it (T1)."""
    setup = Setup()
    setup.rule()
    failed_mails(setup, 2)

    with pytest.raises(NotFound) as raised:
        _ = await setup.delete(1, 8, 2, 9, dry_run=dry_run)

    assert raised.value.describe() == (
        "No processed mail 8, 9 exists that this user may see. Nothing was deleted. "
        "Hint: Leave them out; list_processed_mail shows the records."
    )


@pytest.mark.decision("decision-83")
@pytest.mark.parametrize(("ids", "said"), [((1, 1), "listed twice"), ((), "1 to 100")])
async def test_wrong_ids_are_refused_before_anything_is_read(
    ids: tuple[int, ...], said: str
) -> None:
    with pytest.raises(Validation, match=said):
        _ = await Setup().delete(*ids)
    with pytest.raises(Validation, match="1 to 100"):
        _ = await Setup().delete(*range(1, 102))


@pytest.mark.decision("decision-83")
async def test_other_records_that_stay_are_named_and_those_that_go_are_not() -> None:
    setup = Setup()
    setup.rule(consumption_scope="eml_and_attachments")
    for _ in range(7):
        setup.mail(status="FAILED", received=DAY)
    setup.mail(uid="2", status="FAILED", received=DAY)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.delete(1, 2, 8, confirm=False)

    text = raised.value.message
    kept = "kept out by the records 3, 4, 5 and 2 more of the same mail, which stay"
    assert f"1 'Invoice' (received 2026-11-10 08:00 UTC, rule 'Bills', id 1; {kept})" in text
    assert "8 'Invoice' (received 2026-11-10 08:00 UTC, rule 'Bills', id 1)." in text


@pytest.mark.decision("decision-83")
async def test_siblings_are_read_once_per_rule_and_a_bounded_search_is_said() -> None:
    setup = Setup()
    setup.rule(consumption_scope="eml_and_attachments")
    failed_mails(setup, 2, processed=DAY)
    for number in range(MAX_RECORDS):
        setup.mail(uid=f"x{number}", processed=DAY.replace(day=11))
    read: list[int] = []

    @final
    class Counting(FakePaperless):
        @override
        async def list_processed_mail(self, **given: object):  # noqa: ANN202 -- the fake's page
            read.append(1)
            return await super().list_processed_mail(**given)  # pyright: ignore[reportArgumentType] -- passed on

    fake = Counting(setup.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake, clock=lambda: 0.0)

    report = await service.delete([1, 2], dry_run=True, confirm=False)

    assert len(read) == MAX_RECORDS // 100
    assert "Only the newest 1,000 records of the rule 'Bills' (id 1) were looked at" in (
        report.message
    )


@pytest.mark.decision("decision-83")
async def test_a_record_of_a_hidden_rule_is_shown_with_its_id_and_not_known() -> None:
    setup = Setup("mailman", permissions={"view_processedmail", "delete_processedmail"})
    setup.rule()
    setup.mail(owner=None, received=DAY)

    report = await setup.delete(1, dry_run=True)

    assert "1 'Invoice' (received 2026-11-10 08:00 UTC, rule id 1)" in report.message
    assert "For it: not known: you may not see the rule" in report.message


@pytest.mark.decision("decision-83")
@pytest.mark.parametrize("changed", ["record", "rule", "account", "sibling"])
async def test_what_changed_after_the_question_is_asked_again(changed: str) -> None:
    setup = Setup()
    setup.rule(consumption_scope="eml_and_attachments")
    setup.mail(status="FAILED", received=DAY)

    async def asked() -> str:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await setup.delete(1, confirm=False)
        return raised.value.fingerprint

    before = await asked()
    assert await asked() == before
    if changed == "record":
        setup.state.processed_mail[0].error = "other"
    elif changed == "rule":
        setup.state.mail_rules[0].stop_processing = True
    elif changed == "account":
        setup.state.mail_accounts[0].imap_server = "other.example"
    else:
        setup.mail(status="FAILED")

    assert await asked() != before


@pytest.mark.decision("decision-83")
@pytest.mark.parametrize("dry_run", [False, True])
async def test_a_missing_permission_and_a_record_of_another_are_told_beforehand(
    dry_run: bool,  # noqa: FBT001 -- the case
) -> None:
    rights = {"view_processedmail", "view_uisettings"}
    none = Setup("mailman", permissions=rights)
    none.mail(owner=None)
    with pytest.raises(PermissionDenied, match="delete_processedmail") as raised:
        _ = await none.delete(1, dry_run=dry_run)
    assert "Nothing was deleted" in raised.value.message

    foreign = Setup("mailman", permissions=rights | {"delete_processedmail"})

    @final
    class Sharing(FakePaperless):
        """Records of another user that are shared with the caller to view."""

        @override
        async def get_processed_mail(self, mail_id: int) -> ProcessedMail:
            [row] = [r for r in self.state.processed_mail if r.id == mail_id]
            row.owner, owner = None, row.owner
            read = await super().get_processed_mail(mail_id)
            row.owner = owner
            return read.model_copy(update={"owner": owner})

    fake = Sharing(foreign.state, user="mailman")
    service = ProcessedMailService(fake, fake, fake, clock=lambda: 0.0)
    foreign.mail(owner=2)
    foreign.mail(owner=77)
    foreign.mail(owner=None)
    with pytest.raises(PermissionDenied) as refused:
        _ = await service.delete([1, 2, 3], dry_run=dry_run, confirm=True)
    assert refused.value.describe() == (
        "You do not own the records 1, and they have an owner: Paperless would refuse all. Nothing "
        "was deleted. Hint: Leave them out."
    )
    _ = await service.delete([2, 3], dry_run=True, confirm=False)


@pytest.mark.decision("decision-83")
async def test_a_superuser_deletes_records_of_anyone() -> None:
    setup = Setup(writes=True)
    setup.rule()
    setup.mail(owner=3)

    assert outcomes(await setup.delete(1)) == [(1, "deleted")]


@pytest.mark.decision("decision-83")
async def test_without_knowing_the_caller_paperless_decides() -> None:
    setup = Setup("mailman", permissions={"view_processedmail"})
    setup.fake.allow_writes = True
    setup.mail(owner=None)

    with pytest.raises(PermissionDenied, match="Nothing was deleted"):
        _ = await setup.delete(1)
    assert setup.left() == [1]


class Failing(FakePaperless):
    """A Paperless whose deletion ends with an error, done or not."""

    error: PaperlessMcpError = UpstreamTimeout("Paperless did not respond in time.")
    done: Sequence[int] = ()

    @override
    async def delete_processed_mail(self, mail_ids: Sequence[int]) -> None:
        self.state.processed_mail[:] = [
            row for row in self.state.processed_mail if row.id not in self.done
        ]
        raise self.error


@pytest.mark.decision("decision-83")
async def test_a_lost_answer_is_followed_by_reading_each_record() -> None:
    setup = Setup(make=Failing)
    assert isinstance(setup.fake, Failing)
    setup.fake.done = (1,)
    setup.rule()
    failed_mails(setup, 2)

    report = await setup.delete(1, 2)

    assert outcomes(report) == [(1, "deleted"), (2, "still_there")]
    assert report.message == (
        "1 of 2 records were deleted. Still there: 2. Paperless did not confirm the deletion: "
        "Paperless did not respond in time. It is not repeated: check with list_processed_mail "
        "before you call this again."
    )


@pytest.mark.decision("decision-83")
@pytest.mark.parametrize(
    "error",
    [
        AuthenticationFailed("no"),
        Validation("no"),
        UpstreamUnavailable("slow down", status=429),
    ],
)
async def test_a_refusal_is_an_error_that_says_nothing_was_deleted(
    error: PaperlessMcpError,
) -> None:
    setup = Setup(make=Failing)
    assert isinstance(setup.fake, Failing)
    setup.fake.error = error
    setup.rule()
    failed_mails(setup)

    with pytest.raises(type(error)) as raised:
        _ = await setup.delete(1)

    assert raised.value.message.endswith("Nothing was deleted.")


@final
class Vanishing(FakePaperless):
    """A Paperless that cannot be read once something was deleted."""

    @override
    async def get_processed_mail(self, mail_id: int) -> ProcessedMail:
        if len(self.state.processed_mail) < 2:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)
        return await super().get_processed_mail(mail_id)


@pytest.mark.decision("decision-83")
async def test_what_cannot_be_read_again_is_unknown() -> None:
    setup = Setup(make=Vanishing, writes=True)
    setup.rule()
    failed_mails(setup, 4)

    report = await setup.delete(1, 2, 3)

    assert outcomes(report) == [(1, "unknown"), (2, "unknown"), (3, "unknown")]
    assert "0 of 3 records were seen to be deleted. Not known: 1, 2, 3." in report.message
    assert "before you call this again" in report.message


@pytest.mark.decision("decision-83")
async def test_after_forty_seconds_nothing_is_started() -> None:
    late = Setup()
    late.rule()
    failed_mails(late, 2)

    @final
    class Slow(FakePaperless):
        @override
        async def get_processed_mail(self, mail_id: int) -> ProcessedMail:
            late.now += 40
            return await super().get_processed_mail(mail_id)

    fake = Slow(late.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake, clock=lambda: late.now)
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await service.delete([1, 2], dry_run=False, confirm=True)
    assert raised.value.describe() == (
        "The records could not be read within 40 seconds. Nothing was deleted. "
        "Hint: Delete fewer records per call."
    )

    setup = Setup()
    setup.rule()
    failed_mails(setup, 2)

    @final
    class SlowRule(FakePaperless):
        @override
        async def get_mail_rule(self, rule_id: int) -> MailRule:
            setup.now += 40
            return await super().get_mail_rule(rule_id)

    fake = SlowRule(setup.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake, clock=lambda: setup.now)
    with pytest.raises(PaperlessMcpError, match="Nothing was deleted"):
        _ = await service.delete([1, 2], dry_run=False, confirm=True)

    reading = Setup(writes=True)
    reading.rule()
    failed_mails(reading, 2)

    @final
    class SlowAfter(FakePaperless):
        @override
        async def delete_processed_mail(self, mail_ids: Sequence[int]) -> None:
            await super().delete_processed_mail(mail_ids)
            reading.now += 40

    fake = SlowAfter(reading.state, user=ADMIN[0], allow_writes=True)
    service = ProcessedMailService(fake, fake, fake, clock=lambda: reading.now)
    report = await service.delete([1, 2], dry_run=False, confirm=True)
    assert outcomes(report) == [(1, "unknown"), (2, "unknown")]
    assert "40 seconds have passed." in report.message
