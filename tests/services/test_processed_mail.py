"""ProcessedMailService: one processed mail, its whole error, and what follows without its record.

The rows of `refetch` are those of the table in the design of PLMCP-81 (2.2); the live tests of
PLMCP-81.2 prove them against a mail server.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Final, final, override

import pytest
from fakes.accounts import ADMIN
from fakes.mail import FakeMailAccount, FakeMailRule, FakeProcessedMail
from fakes.paperless import FakePaperless, FakeState, FakeUser, seeded

from mcp_server_paperless.domain.documents import SignedIn
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
    accepting,
)
from mcp_server_paperless.domain.mail import Reference
from mcp_server_paperless.services.bulk_documents import DRY_RUN, TO_GO_AHEAD
from mcp_server_paperless.services.processed_mail import (
    ERROR_SLICE,
    FEWER,
    MAX_RECORDS,
    ProcessedMailService,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.mail import (
        MailAccount,
        MailRule,
        ProcessedMail,
        ProcessedMailDeletion,
        ProcessedMailDetail,
        ProcessedMailPage,
    )

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-80")]

DAY = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
NEXT: Final = (
    "the next fetch (every 10 minutes by default, or process_mail_account; an earlier rule of the "
    "account can stop it or take the mail first)"
)
MATCHING: Final = "if it still matches the rule's folder, age and filters"
HIDDEN: Final = (
    "You may not see the rule of this mail: without this record it may be imported again and then "
    "deleted or moved on the mail server"
)
TAKEN: Final = (
    "the files the rule takes from it are imported (again, if they were before, unless Paperless "
    "rejects duplicates); if every one of them is consumed"
)
NOT_KNOWN: Final = (
    "The rule's settings of now count, not those of then; whether the mail still exists on the "
    "server, and records you may not see, are not known here. A record keeps out the mail with its "
    "folder and uid: one stored without the folder's UIDVALIDITY any such mail, one with an older "
    "UIDVALIDITY none, unless the server names none; which it is is not shown."
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
    assert found.refetch == (f"{HIDDEN}. {NOT_KNOWN}")


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
        f"it is not, {NEXT} takes it again {MATCHING}: {TAKEN}, the rule then {again}. {NOT_KNOWN}"
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
        f"takes it again {MATCHING}: {TAKEN}, the rule then {again}. {NOT_KNOWN}"
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
        f"Without this record {NEXT} takes the mail again {MATCHING}{condition}: {TAKEN}, the rule "
        f"then {then}. {NOT_KNOWN}"
    )


async def test_a_mail_without_a_file_is_looked_at_again_and_mostly_nothing_happens() -> None:
    setup = Setup()
    setup.rule(action="flag")
    setup.mail(status="PROCESSED_WO_CONSUMPTION")

    assert await setup.refetch() == (
        f"Without this record {NEXT} looks at the mail again {MATCHING} and is not flagged. "
        "Nothing happens to it unless the rule or the mail now gives a file: then that is "
        f"imported and, if it is consumed, the rule flags the mail. {NOT_KNOWN}"
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
        "folder 'Bills', not 'INBOX': it does not look at this mail while so. Once that "
        "no longer holds: without this record the next fetch"
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
        "Other records with the folder and uid of this mail keep it out until they are deleted "
        "too: 2, 6. "
        "Once that no longer holds: without this record the next fetch"
    )
    assert text.startswith(start)


async def test_other_records_are_looked_for_whatever_the_rule_takes_now() -> None:
    """The rule may have taken the mail and its attachments when the records were written."""
    setup = Setup()
    setup.rule(consumption_scope="eml")
    setup.mail(status="FAILED")
    setup.mail()

    assert (await setup.refetch()).startswith("Other records with the folder and uid of this mail")


async def test_a_failure_to_look_for_other_records_does_not_fail_the_read() -> None:
    setup = Setup()
    setup.rule()
    setup.mail(status="FAILED", error="boom")

    @final
    class Unlisted(FakePaperless):
        @override
        async def list_processed_mail(self, **_: object) -> ProcessedMailPage:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)

    fake = Unlisted(setup.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake)
    found = await service.get(1, error_from=0)

    assert found.error == "boom"
    assert found.refetch.startswith("Without this record")
    assert found.refetch.endswith("Other records of the same mail could not be looked for.")


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
        f"record {NEXT} takes the mail again {MATCHING}: {TAKEN}, the rule then DELETES the mail "
        "on the mail server. "
        "1 with status SUCCESS: 3 'Taken' (received 2026-11-10 08:00 UTC, rule 'Other', id 2). "
        "For it: without this record the mail is not fetched while it is read on the mail server. "
        f"If it is not, {NEXT} takes it again {MATCHING}: {TAKEN}, the rule then marks the mail as "
        "read. "
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
        f"{DRY_RUN} Confirming would permanently delete 1 record of processed mail,"
    )
    assert report.message.endswith(f"is not shown. {TO_GO_AHEAD}")
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
    kept = "kept out by the records 3, 4, 5 and 2 more with its uid, which stay for now"
    assert f"1 'Invoice' (received 2026-11-10 08:00 UTC, rule 'Bills', id 1; {kept})" in text
    assert "one with an older UIDVALIDITY none" in text
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
    assert f"For it: {HIDDEN[0].lower()}{HIDDEN[1:]}." in report.message


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
    with pytest.raises(PaperlessMcpError) as late_rule:
        _ = await service.delete([1, 2], dry_run=False, confirm=True)
    assert late_rule.value.message.endswith("Nothing was deleted.")

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
    assert report.message.endswith(FEWER)


@pytest.mark.decision("decision-83")
async def test_three_records_that_stay_are_named_and_a_swapped_fourth_is_asked_again() -> None:
    setup = Setup()
    setup.rule()
    for _ in range(4):
        setup.mail(status="FAILED")

    async def asked() -> ConfirmationRequired:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await setup.delete(1, confirm=False)
        return raised.value

    assert "kept out by the records 2, 3, 4 with its uid" in (await asked()).message
    setup.mail(status="FAILED")
    before = await asked()
    setup.state.processed_mail[-1].id = 50
    after = await asked()

    assert "2, 3, 4 and 1 more with its uid" in before.message
    assert before.message == after.message
    assert before.fingerprint != after.fingerprint


@pytest.mark.decision("decision-83")
async def test_records_of_another_rule_keep_no_mail_out() -> None:
    setup = Setup()
    setup.rule()
    setup.rule(id=2, name="Other")
    setup.mail(status="FAILED")
    setup.mail(status="FAILED", rule=2)
    setup.mail(status="FAILED", rule=2)

    text = (await setup.delete(1, 2, dry_run=True)).message

    assert text.count("kept out by") == 1
    assert "2 'Invoice' (received 2026-11-13 12:00 UTC, rule 'Other', id 2; kept out" in text


@pytest.mark.decision("decision-83")
async def test_the_time_a_mail_arrived_is_said_in_utc() -> None:
    setup = Setup()
    setup.rule()
    setup.mail(received=DAY.astimezone(timezone(timedelta(hours=2))))

    assert "(received 2026-11-10 08:00 UTC," in (await setup.delete(1, dry_run=True)).message


@pytest.mark.decision("decision-83")
async def test_nothing_is_sent_when_the_last_read_took_too_long() -> None:
    setup = Setup()
    setup.rule()
    failed_mails(setup)

    @final
    class Slow(FakePaperless):
        @override
        async def get_signed_in(self) -> SignedIn | None:
            setup.now += 40
            return await super().get_signed_in()

    fake = Slow(setup.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake, clock=lambda: setup.now)
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await service.delete([1], dry_run=False, confirm=True)
    assert raised.value.message.endswith("Nothing was deleted.")


@pytest.mark.decision("decision-83")
@pytest.mark.parametrize(
    ("error", "reads"),
    [(UpstreamTimeout("silent"), 2), (UpstreamUnavailable("slow down", status=429), 1)],
)
async def test_reading_again_ends_when_paperless_is_silent_or_asks_for_less(
    error: PaperlessMcpError, reads: int
) -> None:
    setup = Setup()
    setup.rule()
    failed_mails(setup, 4)
    after: list[int] = []

    @final
    class Ending(FakePaperless):
        @override
        async def get_processed_mail(self, mail_id: int) -> ProcessedMail:
            if not self.state.processed_mail:
                after.append(mail_id)
                raise error
            return await super().get_processed_mail(mail_id)

    fake = Ending(setup.state, user=ADMIN[0], allow_writes=True)
    service = ProcessedMailService(fake, fake, fake, clock=lambda: 0.0)
    report = await service.delete([1, 2, 3, 4], dry_run=False, confirm=True)

    assert len(after) == reads
    assert {outcome for _, outcome in outcomes(report)} == {"unknown"}


@pytest.mark.decision("decision-83")
async def test_a_hidden_account_does_not_stop_the_deletion() -> None:
    rights = {"view_processedmail", "delete_processedmail", "view_mailrule"}
    setup = Setup("mailman", permissions=rights, writes=True)
    setup.rule(owner=None)
    setup.mail(owner=None, status="FAILED")

    assert outcomes(await setup.delete(1)) == [(1, "deleted")]


IMPORTED: Final = {
    "eml": (
        "the mail itself is imported (again, if it was before, unless Paperless rejects "
        "duplicates); if it is consumed"
    ),
    "eml_and_attachments": (
        "the mail itself and the files the rule takes from it are imported (again, if they were "
        "before, unless Paperless rejects duplicates); as soon as the mail itself, or every one of "
        "the files, is consumed, even if the other fails"
    ),
    "attachments": TAKEN,
}


@pytest.mark.parametrize("scope", IMPORTED)
@pytest.mark.parametrize("status", ["FAILED", "PROCESSED_WO_CONSUMPTION", "SUCCESS"])
async def test_when_the_rule_acts_follows_from_what_it_takes(scope: str, status: str) -> None:
    """The mail itself and its attachments are queued apart, each with the action behind it."""
    setup = Setup()
    setup.rule(action="delete", consumption_scope=scope)
    setup.mail(status=status)

    text = await setup.refetch()

    acts = f"{IMPORTED[scope]}, the rule then DELETES the mail on the mail server"
    if (status, scope) == ("PROCESSED_WO_CONSUMPTION", "attachments"):
        assert "imported and, if it is consumed, the rule DELETES the mail" in text
    else:
        assert acts in text
    if status != "SUCCESS":
        assert text.startswith(f"Without this record {NEXT} ")


async def test_other_records_are_those_of_the_folder_as_the_rule_writes_it() -> None:
    """Paperless compares the folder of a record exactly, a mail server INBOX in any case."""
    setup = Setup()
    setup.rule(folder="inbox")
    setup.mail(status="FAILED", folder="INBOX")
    setup.mail(status="FAILED", folder="inbox")
    setup.mail(status="FAILED", folder="INBOX")

    assert "until they are deleted too: 2. " in await setup.refetch()


async def test_a_record_of_another_folder_has_the_others_of_that_folder() -> None:
    setup = Setup()
    setup.rule(folder="Bills")
    setup.mail(status="FAILED")
    setup.mail(status="FAILED")
    setup.mail(status="FAILED", folder="Bills")

    assert "until they are deleted too: 2. " in await setup.refetch()


@pytest.mark.decision("decision-83")
async def test_only_a_call_that_ran_out_of_time_suggests_fewer_records() -> None:
    setup = Setup(make=Vanishing, writes=True)
    setup.rule()
    failed_mails(setup, 4)

    assert "Delete fewer" not in (await setup.delete(1, 2, 3)).message


async def test_the_inbox_is_the_same_in_any_case() -> None:
    setup = Setup()
    setup.rule(folder="inbox")
    setup.mail(status="FAILED", folder="INBOX")
    other = Setup()
    other.rule(folder="bills")
    other.mail(status="FAILED", folder="Bills")

    assert (await setup.refetch()).startswith("Without this record")
    assert (await other.refetch()).startswith("The rule now reads folder 'bills', not 'Bills'")


async def test_other_records_of_a_hidden_rule_are_named_as_well() -> None:
    setup = Setup("mailman", permissions={"view_processedmail", "delete_processedmail"})
    setup.mail(owner=None)
    setup.mail(owner=None)

    read = await setup.refetch()
    asked = (await setup.delete(1, dry_run=True)).message

    assert read.startswith("Other records with the folder and uid of this mail keep it out")
    assert f"{HIDDEN[0].lower()}{HIDDEN[1:]}" in read
    assert "kept out by the records 2 with its uid, which stay for now" in asked


@pytest.mark.decision("decision-83")
async def test_a_row_two_pages_show_is_one_record() -> None:
    setup = Setup()
    setup.rule()
    setup.mail(status="FAILED")
    setup.mail(status="FAILED")

    @final
    class Doubling(FakePaperless):
        @override
        async def list_processed_mail(self, **given: object) -> ProcessedMailPage:
            found = await super().list_processed_mail(**given)  # pyright: ignore[reportArgumentType] -- passed on
            return found.model_copy(update={"mails": found.mails * 2})

    fake = Doubling(setup.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake)

    assert "until they are deleted too: 2. " in (await service.get(1, error_from=0)).refetch


@pytest.mark.decision("decision-83")
async def test_a_page_that_vanishes_is_told_as_a_change() -> None:
    setup = Setup()
    setup.rule()
    failed_mails(setup, 101)

    @final
    class Shrinking(FakePaperless):
        @override
        async def list_processed_mail(self, **given: object) -> ProcessedMailPage:
            found = await super().list_processed_mail(**given)  # pyright: ignore[reportArgumentType] -- passed on
            del self.state.processed_mail[1:]
            return found

    fake = Shrinking(setup.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake, clock=lambda: 0.0)
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await service.delete([1], dry_run=True, confirm=False)

    assert raised.value.describe() == (
        "The records of rule 1 changed while they were read. Nothing was deleted. "
        "Hint: Call this again."
    )


@pytest.mark.decision("decision-83")
async def test_a_failed_read_before_the_deletion_says_that_nothing_was_deleted() -> None:
    setup = Setup()
    setup.rule()
    failed_mails(setup)

    @final
    class Silent(FakePaperless):
        @override
        async def get_mail_rule(self, rule_id: int) -> MailRule:
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)

    fake = Silent(setup.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake, clock=lambda: 0.0)
    with pytest.raises(UpstreamTimeout) as raised:
        _ = await service.delete([1], dry_run=False, confirm=True)

    assert raised.value.message == "Paperless did not respond in time. Nothing was deleted."


@pytest.mark.decision("decision-83")
async def test_a_missing_permission_is_told_before_the_records_are_read() -> None:
    rights = {"view_processedmail", "view_uisettings"}
    setup = Setup("mailman", permissions=rights)

    with pytest.raises(PermissionDenied, match="delete_processedmail"):
        _ = await setup.delete(1, 2, 3)


@pytest.mark.decision("decision-83")
async def test_records_of_another_user_are_told_before_rules_and_lists_are_read() -> None:
    rights = {"view_processedmail", "view_uisettings", "delete_processedmail"}
    setup = Setup("mailman", permissions=rights)
    setup.mail(owner=None)

    @final
    class Foreign(FakePaperless):
        @override
        async def get_processed_mail(self, mail_id: int) -> ProcessedMail:
            read = await super().get_processed_mail(mail_id)
            return read.model_copy(update={"owner": 2})

        @override
        async def get_mail_rule(self, rule_id: int) -> MailRule:
            raise AssertionError

    fake = Foreign(setup.state, user="mailman")
    service = ProcessedMailService(fake, fake, fake, clock=lambda: 0.0)
    with pytest.raises(PermissionDenied, match="You do not own the records 1"):
        _ = await service.delete([1], dry_run=False, confirm=True)


@pytest.mark.decision("decision-83")
async def test_a_visible_rule_after_a_hidden_one_is_explained() -> None:
    setup = Setup("mailman", permissions={"view_processedmail", "view_mailrule"})
    setup.rule(id=1, owner=2)
    setup.rule(id=2, owner=None, action="delete")
    setup.mail(owner=None, rule=1, status="FAILED")
    setup.mail(owner=None, rule=2, status="FAILED")

    text = (await setup.delete(1, 2, dry_run=True)).message

    assert "the rule then DELETES the mail" in text
    assert f"{HIDDEN[0].lower()}{HIDDEN[1:]}" in text


@pytest.mark.decision("decision-83")
@pytest.mark.parametrize("changed", [0, 1])
async def test_the_mailboxes_of_all_accounts_are_bound(changed: int) -> None:
    setup = Setup()
    second = replace(setup.state.mail_accounts[0], id=2, name="Second")
    setup.state.mail_accounts.append(second)
    setup.rule()
    setup.rule(id=2, account=2)
    setup.mail(status="FAILED")
    setup.mail(status="FAILED", rule=2)

    async def asked() -> str:
        with pytest.raises(ConfirmationRequired) as raised:
            _ = await setup.delete(1, 2, confirm=False)
        return raised.value.fingerprint

    before = await asked()
    setup.state.mail_accounts[changed].imap_port = 143

    assert await asked() != before


@pytest.mark.decision("decision-83")
async def test_a_refusal_of_paperless_is_passed_on_as_it_is() -> None:
    setup = Setup(make=Failing)
    assert isinstance(setup.fake, Failing)
    setup.fake.error = PermissionDenied("Not you. Nothing was deleted.", hint="Ask.")
    setup.rule()
    failed_mails(setup)

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.delete(1)

    assert raised.value.describe() == "Not you. Nothing was deleted. Hint: Ask."


SLOW: Final = (
    *("get_signed_in", "get_processed_mail", "get_mail_rule", "get_mail_account"),
    "list_processed_mail",
)


@pytest.mark.decision("decision-83")
@pytest.mark.parametrize("slow", SLOW)
async def test_the_next_request_is_not_sent_after_forty_seconds(slow: str) -> None:
    """Each kind of request checks the time before it is sent."""
    setup = Setup()
    setup.rule()
    failed_mails(setup)
    sent: list[str] = []

    @final
    class Slow(FakePaperless):
        def _note(self, name: str) -> None:
            sent.append(name)
            if name == slow:
                setup.now += 40

        @override
        async def get_signed_in(self) -> SignedIn | None:
            self._note("get_signed_in")
            return await super().get_signed_in()

        @override
        async def get_processed_mail(self, mail_id: int) -> ProcessedMail:
            self._note("get_processed_mail")
            return await super().get_processed_mail(mail_id)

        @override
        async def get_mail_rule(self, rule_id: int) -> MailRule:
            self._note("get_mail_rule")
            return await super().get_mail_rule(rule_id)

        @override
        async def get_mail_account(self, account_id: int) -> MailAccount:
            self._note("get_mail_account")
            return await super().get_mail_account(account_id)

        @override
        async def list_processed_mail(self, **given: object) -> ProcessedMailPage:
            self._note("list_processed_mail")
            return await super().list_processed_mail(**given)  # pyright: ignore[reportArgumentType] -- passed on

    fake = Slow(setup.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake, clock=lambda: setup.now)
    with pytest.raises(PaperlessMcpError, match="within 40 seconds"):
        _ = await service.delete([1], dry_run=False, confirm=True)

    assert tuple(sent) == SLOW[: SLOW.index(slow) + 1]


@pytest.mark.decision("decision-83")
async def test_a_deletion_names_the_records_of_the_folder_as_the_rule_writes_it() -> None:
    setup = Setup()
    setup.rule(folder="inbox")
    setup.mail(status="FAILED", folder="INBOX")
    setup.mail(status="FAILED", folder="inbox")
    setup.mail(status="FAILED", folder="INBOX")

    assert "kept out by the records 2 with its uid" in (await setup.delete(1, dry_run=True)).message


LIMIT: Final = 100_000


async def confirmation_of(above: int) -> Setup:
    """100 records whose confirmation is ``above`` characters longer than the limit."""
    setup = Setup(writes=True)
    for number in range(1, 101):
        folder = f"{number:0>254}"
        scope = "eml_and_attachments"
        setup.rule(
            id=number,
            name="n",
            action="move",
            action_parameter=folder,
            consumption_scope=scope,
            enabled=False,
        )
        setup.mail(rule=number, subject="s", status="FAILED", received=DAY)
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.delete(*range(1, 101), confirm=False)
    missing = LIMIT + above - len(raised.value.describe())
    for rule in setup.state.mail_rules:
        added = min(missing, 59)
        rule.name += "n" * added
        missing -= added
    for mail in setup.state.processed_mail:
        added = min(missing, 59)
        mail.subject += "s" * added
        missing -= added
    assert missing == 0
    return setup


@pytest.mark.decision("decision-83")
async def test_a_confirmation_at_the_limit_is_shown_whole_and_can_be_accepted() -> None:
    setup = await confirmation_of(0)
    ids = range(1, 101)

    preview = await setup.delete(*ids, dry_run=True)
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.delete(*ids, confirm=False)
    with accepting(raised.value.fingerprint):
        accepted = await setup.delete(*ids, confirm=False)

    assert len(raised.value.describe()) == LIMIT
    assert "refused" not in preview.message
    assert accepted.message == "100 of 100 records were deleted."
    assert setup.left() == []


@pytest.mark.decision("decision-83")
async def test_a_confirmation_at_the_limit_can_be_confirmed_with_the_argument() -> None:
    setup = await confirmation_of(0)

    _ = await setup.delete(*range(1, 101), confirm=True)

    assert setup.left() == []


@pytest.mark.decision("decision-83")
async def test_a_confirmation_above_the_limit_is_refused_also_when_confirmed_or_accepted() -> None:
    setup = await confirmation_of(1)
    ids = range(1, 101)
    effect = (await setup.delete(*ids, dry_run=True)).message
    said = (
        "confirmation would be longer than what can be shown: 100001 characters (at most 100000) "
        "for 100 records"
    )

    for confirm in (False, True):
        with pytest.raises(Validation) as raised:
            _ = await setup.delete(*ids, confirm=confirm)
        assert raised.value.message == f"The {said}. Nothing was deleted."
        assert raised.value.hint == FEWER
    assert setup.left() == list(ids)
    refused = f"{DRY_RUN} This call would be refused: the {said}. {FEWER} Confirmed, it would "
    assert effect.startswith(f"{refused}permanently delete 100 records of processed mail,")
    assert effect.endswith("which it is is not shown.")
    assert TO_GO_AHEAD not in effect


@pytest.mark.decision("decision-83")
async def test_an_accepted_confirmation_that_has_become_too_long_is_refused() -> None:
    setup = await confirmation_of(0)
    ids = range(1, 101)
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.delete(*ids, confirm=False)
    setup.state.processed_mail[99].subject += "s"

    with accepting(raised.value.fingerprint), pytest.raises(Validation):
        _ = await setup.delete(*ids, confirm=False)

    assert setup.left() == list(ids)


HIDDEN_SAID: Final = (
    "Not visible to you any more, so deleted or hidden: {}. Paperless did not confirm the "
    "deletion: Paperless did not respond in time. It is not repeated: check with "
    "list_processed_mail before you call this again."
)
MAY: Final = {"view_processedmail", "delete_processedmail", "view_mailrule", "view_uisettings"}


@final
class Unconfirmed(FakePaperless):
    """A deletion without an answer, during which the caller's rights can change."""

    error: PaperlessMcpError = UpstreamTimeout("Paperless did not respond in time.")
    done: Sequence[int] = ()
    hidden: Sequence[int] = ()
    asked: int = 0
    after: SignedIn | PaperlessMcpError | None = None
    changed: bool = False

    @override
    async def delete_processed_mail(self, mail_ids: Sequence[int]) -> None:
        rows = self.state.processed_mail
        rows[:] = [row for row in rows if row.id not in self.done]
        for row in rows:
            if row.id in self.hidden:
                row.owner = 2
        self.asked = -1
        raise self.error

    @override
    async def get_signed_in(self) -> SignedIn | None:
        if not self.asked or not self.changed:
            return await super().get_signed_in()
        if isinstance(self.after, PaperlessMcpError):
            raise self.after
        return self.after


def unconfirmed(user: str = ADMIN[0], permissions: set[str] | None = None) -> Setup:
    setup = Setup(user, permissions=permissions, make=Unconfirmed)
    setup.rule(owner=None)
    for number in range(1, 4):
        setup.mail(uid=f"u{number}", status="FAILED", received=DAY, owner=None)
    return setup


@pytest.mark.decision("decision-107")
async def test_without_an_answer_a_hidden_record_is_not_said_to_be_deleted() -> None:
    setup = unconfirmed("mailman", MAY)
    assert isinstance(setup.fake, Unconfirmed)
    setup.fake.done, setup.fake.hidden = (1,), (2,)

    report = await setup.delete(1, 2, 3)

    assert outcomes(report) == [(1, "unknown"), (2, "unknown"), (3, "still_there")]
    assert report.message == (
        "0 of 3 records were seen to be deleted. Still there: 3. Not known: 1, 2. "
        + HIDDEN_SAID.format("1, 2")
    )
    assert setup.left() == [2, 3]


@pytest.mark.decision("decision-107")
async def test_without_an_answer_a_superuser_sees_what_is_gone() -> None:
    setup = unconfirmed()
    assert isinstance(setup.fake, Unconfirmed)
    setup.fake.done, setup.fake.hidden = (1,), (2,)

    report = await setup.delete(1, 2)

    assert outcomes(report) == [(1, "deleted"), (2, "still_there")]
    assert "deleted or hidden" not in report.message


@pytest.mark.decision("decision-107")
@pytest.mark.parametrize(
    "after",
    [
        None,
        SignedIn(id=2, is_superuser=False),
        UpstreamTimeout("late"),
        UpstreamUnavailable("slow down", status=429),
    ],
)
async def test_who_is_no_superuser_any_more_or_not_known_to_be_one_has_seen_nothing(
    after: SignedIn | PaperlessMcpError | None,
) -> None:
    setup = unconfirmed()
    assert isinstance(setup.fake, Unconfirmed)
    setup.fake.done, setup.fake.after, setup.fake.changed = (1,), after, True

    report = await setup.delete(1)

    assert outcomes(report) == [(1, "unknown")]
    assert report.message == (
        "0 of 1 record was seen to be deleted. Not known: 1. " + HIDDEN_SAID.format("1")
    )


@pytest.mark.decision("decision-107")
async def test_who_became_a_superuser_meanwhile_may_have_been_shown_less() -> None:
    setup = unconfirmed("mailman", MAY)
    assert isinstance(setup.fake, Unconfirmed)
    setup.fake.done, setup.fake.changed = (1,), True
    setup.fake.after = SignedIn(id=77, is_superuser=True)

    assert outcomes(await setup.delete(1)) == [(1, "unknown")]


@pytest.mark.decision("decision-107")
async def test_after_the_time_of_the_call_who_is_signed_in_is_not_asked_for() -> None:
    setup = Setup()
    setup.rule()
    failed_mails(setup)

    @final
    class Late(FakePaperless):
        sent: bool = False
        looked: bool = False

        @override
        async def delete_processed_mail(self, mail_ids: Sequence[int]) -> None:
            self.sent = True
            self.state.processed_mail.clear()
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)

        @override
        async def get_processed_mail(self, mail_id: int) -> ProcessedMail:
            setup.now += 40 if self.sent else 0
            return await super().get_processed_mail(mail_id)

        @override
        async def get_signed_in(self) -> SignedIn | None:
            assert not self.sent
            return await super().get_signed_in()

    fake = Late(setup.state, user=ADMIN[0])
    service = ProcessedMailService(fake, fake, fake, clock=lambda: setup.now)

    report = await service.delete([1], dry_run=False, confirm=True)

    assert outcomes(report) == [(1, "unknown")]


@pytest.mark.decision("decision-107")
async def test_a_confirmed_deletion_is_seen_by_everybody_without_asking_who_it_is() -> None:
    """Paperless deletes all the ids it finds, or answers 403: no hidden record is left."""

    @final
    class Confirmed(FakePaperless):
        asked: int = 0

        @override
        async def get_signed_in(self) -> SignedIn | None:
            self.asked += 1
            return await super().get_signed_in()

    setup = Setup("mailman", permissions=MAY, make=Confirmed, writes=True)
    setup.rule(owner=None)
    setup.mail(status="FAILED", owner=None)

    assert outcomes(await setup.delete(1)) == [(1, "deleted")]
    assert isinstance(setup.fake, Confirmed)
    assert setup.fake.asked == 1


@pytest.mark.decision("decision-107")
async def test_after_a_rate_limit_who_is_signed_in_is_not_asked_for() -> None:
    @final
    class Limited(FakePaperless):
        sent: bool = False

        @override
        async def delete_processed_mail(self, mail_ids: Sequence[int]) -> None:
            self.sent = True
            self.state.processed_mail.clear()
            message = "Paperless did not respond in time."
            raise UpstreamTimeout(message)

        @override
        async def get_processed_mail(self, mail_id: int) -> ProcessedMail:
            if self.sent and mail_id == 2:
                message = "slow down"
                raise UpstreamUnavailable(message, status=429)
            return await super().get_processed_mail(mail_id)

        @override
        async def get_signed_in(self) -> SignedIn | None:
            assert not self.sent
            return await super().get_signed_in()

    setup = Setup(make=Limited)
    setup.rule()
    failed_mails(setup, 2)

    report = await setup.delete(1, 2)

    assert outcomes(report) == [(1, "unknown"), (2, "unknown")]
    assert "deleted or hidden: 1." in report.message
