"""MailService: accounts, rules and processed mail behind the confirmation flow (PLMCP-29).

Fakes of the "unconfirmed" cases are created without `allow_writes`: the write guard raises on any
write, so a passing test proves nothing was written. The fake is the only mail server that can log
in, fail to log in or stay silent here (the E2E stack has no IMAP server). Every test that handles
a password uses `PASSWORD` and checks that it appears in no result, message or log line.
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, final

import pytest
from fakes.accounts import ADMIN, READER
from fakes.mail import FakeMailAccount, FakeMailRule, FakeProcessedMail
from fakes.paperless import FakePaperless, FakeState, FakeTag, seeded
from pydantic import SecretStr

from paperless_mcp.domain.errors import (
    CONFIRM_HINT,
    Ambiguous,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
)
from paperless_mcp.domain.mail import MailRuleSettings
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.mail import MAX_ERROR, MailService
from paperless_mcp.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.mail import RuleClearable

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-6")]

PASSWORD = "S3cret-Passw0rd!x"
HOST, PORT = "imap.example.com", 993
DAY = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


@final
class Setup:
    def __init__(
        self,
        user: str = ADMIN[0],
        *,
        writes: bool = True,
        permissions: frozenset[str] | None = None,
    ) -> None:
        self.state: FakeState = seeded()
        if permissions is not None:
            self.state.users = [
                replace(u, permissions=permissions) if u.username == user else u
                for u in self.state.users
            ]
        self.fake: FakePaperless = FakePaperless(self.state, user=user, allow_writes=writes)
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        self.service: MailService = MailService(self.fake, self.fake, taxonomy)

    def account(self, name: str = "Invoices", **fields: object) -> int:
        number = max((a.id for a in self.state.mail_accounts), default=0) + 1
        values: dict[str, object] = {
            "name": name,
            "imap_server": HOST,
            "imap_port": PORT,
            "username": "me@example.com",
            "password": PASSWORD,
            "owner": 2,
        }
        self.state.mail_accounts.append(FakeMailAccount(id=number, **(values | fields)))  # pyright: ignore[reportArgumentType] -- test data
        return number

    def rule(self, account: int, name: str = "Bills", **fields: object) -> int:
        number = max((r.id for r in self.state.mail_rules), default=0) + 1
        values: dict[str, object] = {"name": name, "account": account, "owner": 2}
        self.state.mail_rules.append(FakeMailRule(id=number, **(values | fields)))  # pyright: ignore[reportArgumentType] -- test data
        return number

    def processed(self, rule: int, subject: str, **fields: object) -> None:
        number = max((m.id for m in self.state.processed_mail), default=0) + 1
        values: dict[str, object] = {"rule": rule, "subject": subject, "owner": 2}
        self.state.processed_mail.append(FakeProcessedMail(id=number, **(values | fields)))  # pyright: ignore[reportArgumentType] -- test data


def refusal(error: pytest.ExceptionInfo[ConfirmationRequired]) -> str:
    assert error.value.hint == CONFIRM_HINT
    return error.value.message


# --- accounts: reading


async def test_accounts_are_listed_in_pages_without_a_password() -> None:
    setup = Setup()
    for number in range(3):
        _ = setup.account(f"Account {number}")

    first = await setup.service.list_accounts(page=1, page_size=2)
    second = await setup.service.list_accounts(page=2, page_size=2)

    assert (first.count, first.page, first.next_page) == (3, 1, 2)
    assert [a.name for a in first.accounts] == ["Account 0", "Account 1"]
    assert (second.next_page, [a.name for a in second.accounts]) == (None, ["Account 2"])
    dumped = first.model_dump_json() + second.model_dump_json()
    assert PASSWORD not in dumped
    assert "password" not in dumped
    assert first.accounts[0].token_expires is None
    assert first.accounts[0].imap_security == "ssl"


async def test_a_page_beyond_the_last_one_is_not_found() -> None:
    setup = Setup()
    _ = setup.account()

    with pytest.raises(NotFound, match="There is no page 3: the list has 1 page"):
        _ = await setup.service.list_accounts(page=3, page_size=10)


async def test_an_account_is_read_by_id() -> None:
    setup = Setup()
    number = setup.account(expiration=DAY, account_type="gmail_oauth", is_token=True)

    found = await setup.service.get_account(number)

    assert (found.id, found.name, found.imap_server) == (number, "Invoices", HOST)
    assert (found.account_type, found.is_token, found.token_expires) == ("gmail_oauth", True, DAY)
    assert PASSWORD not in found.model_dump_json()


# --- accounts: writing


async def test_an_account_is_created_and_the_password_is_nowhere_in_the_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    setup = Setup()

    made = await setup.service.create_account(
        name="Invoices",
        imap_server=HOST,
        imap_port=PORT,
        username="me@example.com",
        password=SecretStr(PASSWORD),
        imap_security="starttls",
        character_set=None,
    )

    assert (made.name, made.imap_server, made.imap_port, made.imap_security) == (
        "Invoices",
        HOST,
        PORT,
        "starttls",
    )
    assert PASSWORD not in made.model_dump_json()
    assert PASSWORD not in caplog.text
    assert setup.state.mail_accounts[0].password == PASSWORD


async def test_an_account_takes_a_character_set() -> None:
    setup = Setup()

    made = await setup.service.create_account(
        name="Invoices",
        imap_server=HOST,
        imap_port=PORT,
        username="u",
        password=SecretStr(PASSWORD),
        imap_security=None,
        character_set="ISO-8859-1",
    )

    assert made.character_set == "ISO-8859-1"


@pytest.mark.parametrize("password", ["", "   ", "*", "***"])
async def test_a_blank_or_asterisk_password_is_refused_without_echoing_it(password: str) -> None:
    setup = Setup()

    with pytest.raises(Validation) as raised:
        _ = await setup.service.create_account(
            name="Invoices",
            imap_server=HOST,
            imap_port=PORT,
            username="u",
            password=SecretStr(password),
            imap_security=None,
            character_set=None,
        )

    assert "password" in raised.value.message
    assert "*" not in raised.value.describe()
    assert setup.state.mail_accounts == []


async def test_an_update_without_changes_is_refused() -> None:
    setup = Setup()
    number = setup.account()

    with pytest.raises(Validation, match="Nothing to change"):
        _ = await setup.service.update_account(number)


async def test_an_update_that_changes_nothing_writes_nothing() -> None:
    setup = Setup(writes=False)
    number = setup.account()

    same = await setup.service.update_account(number, name="Invoices", imap_port=PORT)

    assert same.name == "Invoices"


async def test_a_rename_needs_no_confirmation() -> None:
    setup = Setup()
    number = setup.account()

    changed = await setup.service.update_account(number, name="Bills", character_set="ISO-8859-1")

    assert (changed.name, changed.character_set) == ("Bills", "ISO-8859-1")
    assert setup.state.mail_accounts[0].password == PASSWORD


async def test_a_new_password_replaces_the_saved_one_and_is_not_echoed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    setup = Setup()
    number = setup.account()

    changed = await setup.service.update_account(number, password=SecretStr("another new password"))

    assert setup.state.mail_accounts[0].password == "another new password"
    assert "another new password" not in changed.model_dump_json() + caplog.text
    assert PASSWORD not in changed.model_dump_json()


async def test_pointing_an_account_elsewhere_needs_confirm_as_the_saved_password_goes_too() -> None:
    setup = Setup(writes=False)
    number = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_account(number, imap_server="mail.other.example")

    assert refusal(raised) == (
        "Nothing was changed. Confirming would point mail account 'Invoices' (id 1) at "
        f"mail.other.example port {PORT} (SSL) instead of {HOST} port {PORT} (SSL): Paperless "
        "keeps the saved password and sends it to the new server from its next fetch on."
    )
    assert PASSWORD not in raised.value.describe()


async def test_a_new_server_with_a_new_password_needs_no_confirmation() -> None:
    setup = Setup()
    number = setup.account()

    changed = await setup.service.update_account(
        number, imap_server="mail.other.example", password=SecretStr("another new password")
    )

    assert changed.imap_server == "mail.other.example"


async def test_a_confirmed_redirect_changes_the_server_and_keeps_the_password() -> None:
    setup = Setup()
    number = setup.account()

    changed = await setup.service.update_account(
        number, imap_port=143, imap_security="starttls", confirm=True
    )

    assert (changed.imap_port, changed.imap_security) == (143, "starttls")
    assert setup.state.mail_accounts[0].password == PASSWORD


async def test_turning_off_encryption_needs_confirm_even_with_a_new_password() -> None:
    setup = Setup(writes=False)
    number = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_account(
            number, imap_security="none", password=SecretStr("another new password")
        )

    assert refusal(raised) == (
        "Nothing was changed. Confirming would turn off the encryption of mail account "
        f"'Invoices' (id 1): the password and the mail then travel as plain text to {HOST} "
        f"port {PORT}."
    )


async def test_redirecting_without_encryption_names_both_effects() -> None:
    setup = Setup(writes=False)
    number = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_account(
            number, imap_server="x.example", imap_security="none"
        )

    message = refusal(raised)
    assert "point mail account 'Invoices' (id 1) at x.example port 993 (no encryption)" in message
    assert "; and turn off the encryption" in message


async def test_updating_an_account_that_does_not_exist_is_not_found() -> None:
    with pytest.raises(NotFound, match="No mail account 9"):
        _ = await Setup().service.update_account(9, name="x")


async def test_an_account_the_user_may_not_change_is_refused_by_paperless() -> None:
    setup = Setup(READER[0], permissions=frozenset({"view_mailaccount"}))
    number = setup.account(owner=None)

    with pytest.raises(PermissionDenied):
        _ = await setup.service.update_account(number, name="x")


# --- accounts: delete


async def test_deleting_an_account_names_its_rules_and_deletes_nothing_unconfirmed() -> None:
    setup = Setup(writes=False)
    number = setup.account()
    _ = setup.rule(number, "Bills")
    _ = setup.rule(number, "Receipts")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.delete_account(number, confirm=False)

    assert refusal(raised) == (
        "Nothing was changed. Confirming would delete mail account 'Invoices' (id 1) with its "
        "2 rules ('Bills', 'Receipts') and the records of the mail they processed; Paperless "
        "stops fetching from it, the mail on the server stays."
    )
    assert len(setup.state.mail_accounts) == 1


async def test_deleting_an_account_without_rules_says_so() -> None:
    setup = Setup(writes=False)
    number = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.delete_account(number, confirm=False)

    assert refusal(raised) == (
        "Nothing was changed. Confirming would delete mail account 'Invoices' (id 1); Paperless "
        "stops fetching from it, the mail on the server stays."
    )


async def test_a_user_who_cannot_list_rules_gets_a_refusal_that_says_so() -> None:
    setup = Setup(
        READER[0], writes=False, permissions=frozenset({"view_mailaccount", "delete_mailaccount"})
    )
    number = setup.account(owner=None)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.delete_account(number, confirm=False)

    assert "with its rules and the records of the mail they processed" in refusal(raised)


async def test_a_confirmed_delete_removes_the_account_and_its_rules() -> None:
    setup = Setup()
    number = setup.account()
    _ = setup.rule(number)

    gone = await setup.service.delete_account(number, confirm=True)

    assert gone.id == number
    assert gone.message == (
        "Mail account 'Invoices' (id 1) was deleted with its 1 rule; Paperless no longer fetches "
        "from it."
    )
    assert (setup.state.mail_accounts, setup.state.mail_rules) == ([], [])


async def test_deleting_an_account_that_does_not_exist_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().service.delete_account(9, confirm=True)


# --- test and process


async def test_a_connection_test_reports_a_login_that_works() -> None:
    setup = Setup()
    number = setup.account()
    setup.state.mail_hosts.add(HOST)
    setup.state.mail_logins.add((HOST, PORT, "me@example.com", PASSWORD))

    result = await setup.service.test_account(number)

    assert result.success
    assert result.message == "Mail account 'Invoices': Paperless connected and logged in."
    assert PASSWORD not in result.model_dump_json()


async def test_a_connection_test_reports_a_login_that_fails_without_the_password() -> None:
    setup = Setup()
    number = setup.account()
    setup.state.mail_hosts.add(HOST)

    result = await setup.service.test_account(number)

    assert not result.success
    assert result.message.startswith("Mail account 'Invoices': Paperless could not log in")
    assert PASSWORD not in result.model_dump_json()


async def test_a_connection_test_reports_an_unreachable_and_a_silent_server() -> None:
    setup = Setup()
    unreachable = setup.account("Gone")
    silent = setup.account("Silent", imap_server="silent.example")
    setup.state.mail_silent_hosts.add("silent.example")

    first = await setup.service.test_account(unreachable)
    second = await setup.service.test_account(silent)

    assert "could not connect" in first.message
    assert "did not answer" in second.message
    assert not first.success
    assert not second.success


async def test_testing_an_account_that_does_not_exist_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().service.test_account(9)


async def test_a_fetch_needs_confirm_and_names_what_each_rule_does_to_the_mail() -> None:
    setup = Setup(writes=False)
    number = setup.account()
    _ = setup.rule(number, "Bills", action="delete")
    _ = setup.rule(number, "Receipts", action="move", action_parameter="Archive")
    _ = setup.rule(number, "Flagged", action="flag")
    _ = setup.rule(number, "Read", action="mark_read")
    _ = setup.rule(number, "Marked", action="tag", action_parameter="paperless")
    _ = setup.rule(number, "Off", action="delete", enabled=False)
    other = setup.account("Other")
    _ = setup.rule(other, "Elsewhere", action="delete")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.process_account(number, confirm=False)

    assert refusal(raised) == (
        f"Nothing was changed. Confirming would fetch the mail of account 'Invoices' ({HOST}) "
        "now: Paperless connects to the mail server and imports documents from the mails that "
        "its 5 enabled rules match, then acts on those mails on the server. Rule 'Bills' "
        "(id 1) deletes the mail; rule 'Receipts' (id 2) moves the mail to 'Archive'; rule "
        "'Flagged' (id 3) flags the mail; rule 'Read' (id 4) marks the mail as read; rule "
        "'Marked' (id 5) tags the mail 'paperless'. A deleted mail cannot be put back by this "
        "server."
    )
    assert setup.state.mail_fetches == []


async def test_a_fetch_of_an_account_without_enabled_rules_says_nothing_is_imported() -> None:
    setup = Setup(writes=False)
    number = setup.account()
    _ = setup.rule(number, "Off", enabled=False)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.process_account(number, confirm=False)

    assert refusal(raised).endswith(
        "now: the account has no enabled rule, so Paperless imports nothing."
    )


async def test_a_fetch_that_only_marks_mail_read_does_not_warn_about_losing_mail() -> None:
    setup = Setup(writes=False)
    number = setup.account()
    _ = setup.rule(number, "Read", action="mark_read")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.process_account(number, confirm=False)

    assert "cannot be put back" not in refusal(raised)


async def test_a_confirmed_fetch_is_queued_and_says_where_the_outcome_shows() -> None:
    setup = Setup()
    number = setup.account()

    started = await setup.service.process_account(number, confirm=True)

    assert setup.state.mail_fetches == [number]
    assert started.id == number
    assert started.message == (
        "Paperless queued a fetch of the mail of account 'Invoices'. It runs in the background: "
        "new documents appear after consumption (see list_tasks) and list_processed_mail shows "
        "the mails it handled."
    )


async def test_a_fetch_for_an_account_that_does_not_exist_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().service.process_account(9, confirm=True)


async def test_a_user_who_cannot_list_rules_gets_a_fetch_refusal_that_says_so() -> None:
    setup = Setup(
        READER[0], writes=False, permissions=frozenset({"view_mailaccount", "add_mailaccount"})
    )
    number = setup.account(owner=None)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.process_account(number, confirm=False)

    assert "its rules could not be read, so what they do to the mail is unknown" in refusal(raised)


# --- rules: reading


async def test_rules_are_listed_with_taxonomy_names() -> None:
    setup = Setup()
    account = setup.account()
    _ = setup.rule(
        account,
        assign_tags=(1, 2),
        assign_correspondent=1,
        assign_correspondent_from="selected",
        assign_document_type=1,
        owner=2,
    )

    listing = await setup.service.list_rules(account_id=None, page=1, page_size=25)

    [rule] = listing.rules
    assert [(t.id, t.name) for t in rule.assign_tags] == [(1, "e2e-finance"), (2, "e2e-invoices")]
    assert rule.assign_correspondent is not None
    assert rule.assign_correspondent.name == "e2e-acme"
    assert rule.assign_document_type is not None
    assert rule.assign_document_type.name == "e2e-invoice"
    assert rule.owner is not None
    assert rule.owner.name == "admin"
    assert rule.maximum_age_days == 30


async def test_rules_can_be_listed_for_one_account() -> None:
    setup = Setup()
    first, second = setup.account("A"), setup.account("B")
    _ = setup.rule(first, "One")
    _ = setup.rule(second, "Two")

    listing = await setup.service.list_rules(account_id=second, page=1, page_size=25)

    assert (listing.count, [r.name for r in listing.rules]) == (1, ["Two"])


async def test_a_user_who_may_not_see_taxonomy_names_still_sees_the_ids() -> None:
    setup = Setup(READER[0], permissions=frozenset({"view_mailrule"}))
    account = setup.account(owner=None)
    _ = setup.rule(account, assign_tags=(1,), owner=None, assign_correspondent=1)

    listing = await setup.service.list_rules(account_id=None, page=1, page_size=25)

    [rule] = listing.rules
    assert [(t.id, t.name) for t in rule.assign_tags] == [(1, None)]
    assert rule.assign_correspondent is not None
    assert rule.assign_correspondent.name is None


async def test_a_rule_is_read_by_id_and_a_missing_one_is_not_found() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), "Bills")

    assert (await setup.service.get_rule(number)).name == "Bills"
    with pytest.raises(NotFound, match="No mail rule 9"):
        _ = await setup.service.get_rule(9)


# --- rules: create


async def test_a_rule_is_created_with_taxonomy_by_name() -> None:
    setup = Setup()
    account = setup.account()

    made = await setup.service.create_rule(
        MailRuleSettings(
            name="Invoices",
            account_id=account,
            assign_tags=("e2e-finance", "E2E-INVOICES"),
            assign_correspondent="e2e-acme",
            assign_document_type="e2e-invoice",
            owner="admin",
            maximum_age_days=14,
            filter_from="billing@example.com",
        ),
        confirm=False,
    )

    assert [(t.id, t.name) for t in made.assign_tags] == [(1, "e2e-finance"), (2, "e2e-invoices")]
    assert made.assign_correspondent is not None
    assert (made.assign_correspondent.id, made.assign_correspondent.name) == (1, "e2e-acme")
    assert made.assign_correspondent_from == "selected"
    assert made.assign_document_type is not None
    assert made.assign_document_type.id == 1
    assert made.owner is not None
    assert made.owner.id == 2
    assert (made.maximum_age_days, made.filter_from, made.action) == (
        14,
        "billing@example.com",
        "mark_read",
    )
    [stored] = setup.state.mail_rules
    assert stored.assign_tags == (1, 2)


async def test_a_numeric_name_is_an_id() -> None:
    setup = Setup()
    account = setup.account()

    made = await setup.service.create_rule(
        MailRuleSettings(
            name="R", account_id=account, assign_tags=("1", 2), assign_correspondent=1
        ),
        confirm=False,
    )

    assert [t.id for t in made.assign_tags] == [1, 2]
    assert made.assign_correspondent is not None
    assert made.assign_correspondent.id == 1


async def test_an_unknown_or_ambiguous_name_stops_the_rule_before_anything_is_written() -> None:
    setup = Setup(writes=False)
    account = setup.account()

    with pytest.raises(NotFound, match="No tag is named 'nope'"):
        _ = await setup.service.create_rule(
            MailRuleSettings(name="R", account_id=account, assign_tags=("nope",)), confirm=False
        )
    with pytest.raises(NotFound, match="No correspondent is named 'nobody'"):
        _ = await setup.service.create_rule(
            MailRuleSettings(name="R", account_id=account, assign_correspondent="nobody"),
            confirm=False,
        )


async def test_an_ambiguous_name_stops_the_rule_before_anything_is_written() -> None:
    setup = Setup(writes=False)
    account = setup.account()
    setup.state.tags.append(FakeTag(id=9, name="E2E-Finance"))

    with pytest.raises(Ambiguous):
        _ = await setup.service.create_rule(
            MailRuleSettings(name="R", account_id=account, assign_tags=("E2E-FINANCE",)),
            confirm=False,
        )


async def test_a_rule_needs_a_name_and_an_account() -> None:
    setup = Setup()
    account = setup.account()

    with pytest.raises(Validation, match="needs a name"):
        _ = await setup.service.create_rule(MailRuleSettings(account_id=account), confirm=False)
    with pytest.raises(Validation, match="needs an account"):
        _ = await setup.service.create_rule(MailRuleSettings(name="R"), confirm=False)
    with pytest.raises(NotFound, match="No mail account 9"):
        _ = await setup.service.create_rule(MailRuleSettings(name="R", account_id=9), confirm=False)


@pytest.mark.parametrize("action", ["move", "tag"])
async def test_moving_or_tagging_needs_an_action_parameter(action: str) -> None:
    setup = Setup(writes=False)
    account = setup.account()

    with pytest.raises(Validation) as raised:
        _ = await setup.service.create_rule(
            MailRuleSettings.model_validate({"name": "R", "account_id": account, "action": action}),
            confirm=True,
        )

    assert "action_parameter" in raised.value.message


async def test_a_correspondent_with_the_selected_source_given_or_kept_is_taken_as_is() -> None:
    setup = Setup()
    account = setup.account()

    made = await setup.service.create_rule(
        MailRuleSettings(
            name="R",
            account_id=account,
            assign_correspondent="e2e-acme",
            assign_correspondent_from="selected",
        ),
        confirm=False,
    )
    other = await setup.service.update_rule(
        made.id, MailRuleSettings(assign_correspondent=1, name="R2"), confirm=False
    )

    assert made.assign_correspondent_from == "selected"
    assert (other.name, other.assign_correspondent_from) == ("R2", "selected")


async def test_a_correspondent_needs_the_selected_source() -> None:
    setup = Setup()
    account = setup.account()

    with pytest.raises(Validation, match="assign_correspondent_from"):
        _ = await setup.service.create_rule(
            MailRuleSettings(
                name="R",
                account_id=account,
                assign_correspondent="e2e-acme",
                assign_correspondent_from="name",
            ),
            confirm=False,
        )


async def test_a_rule_that_deletes_mail_needs_confirm() -> None:
    setup = Setup(writes=False)
    account = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.create_rule(
            MailRuleSettings(
                name="Purge",
                account_id=account,
                action="delete",
                filter_from="billing@example.com",
                maximum_age_days=7,
            ),
            confirm=False,
        )

    assert refusal(raised) == (
        "Nothing was changed. Confirming would create the enabled mail rule 'Purge' for account "
        "'Invoices', which deletes each mail from the mail server after Paperless imported "
        "documents from it. It covers mails in folder 'INBOX' up to 7 days old, from "
        "'billing@example.com'; a deleted mail cannot be put back by this server."
    )
    assert setup.state.mail_rules == []


async def test_a_rule_that_moves_mail_needs_confirm_and_names_the_folder() -> None:
    setup = Setup(writes=False)
    account = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.create_rule(
            MailRuleSettings(
                name="Archive",
                account_id=account,
                action="move",
                action_parameter="Done",
                folder="Billing",
                filter_subject="Invoice",
                filter_attachment_filename_include="*.pdf",
            ),
            confirm=False,
        )

    assert refusal(raised) == (
        "Nothing was changed. Confirming would create the enabled mail rule 'Archive' for "
        "account 'Invoices', which moves each mail to the folder 'Done' on the mail server after "
        "Paperless imported documents from it. It covers mails in folder 'Billing' up to 30 days "
        "old, with subject 'Invoice', with attachments named '*.pdf'."
    )


async def test_a_confirmed_removing_rule_is_created() -> None:
    setup = Setup()
    account = setup.account()

    made = await setup.service.create_rule(
        MailRuleSettings(name="Purge", account_id=account, action="delete"), confirm=True
    )

    assert (made.action, made.enabled) == ("delete", True)


@pytest.mark.parametrize("action", ["mark_read", "flag"])
async def test_a_rule_that_only_marks_mail_needs_no_confirmation(action: str) -> None:
    setup = Setup()
    account = setup.account()

    made = await setup.service.create_rule(
        MailRuleSettings.model_validate({"name": "R", "account_id": account, "action": action}),
        confirm=False,
    )

    assert made.action == action


async def test_a_tagging_rule_needs_no_confirmation() -> None:
    setup = Setup()
    account = setup.account()

    made = await setup.service.create_rule(
        MailRuleSettings(name="R", account_id=account, action="tag", action_parameter="paperless"),
        confirm=False,
    )

    assert (made.action, made.action_parameter) == ("tag", "paperless")


async def test_a_disabled_rule_that_deletes_mail_needs_no_confirmation_until_it_is_enabled() -> (
    None
):
    setup = Setup()
    account = setup.account()

    made = await setup.service.create_rule(
        MailRuleSettings(name="Purge", account_id=account, action="delete", enabled=False),
        confirm=False,
    )

    assert made.enabled is False
    with pytest.raises(ConfirmationRequired):
        _ = await setup.service.update_rule(made.id, MailRuleSettings(enabled=True), confirm=False)


# --- rules: update


async def test_an_empty_rule_update_is_refused() -> None:
    setup = Setup()
    number = setup.rule(setup.account())

    with pytest.raises(Validation, match="Nothing to change"):
        _ = await setup.service.update_rule(number, MailRuleSettings(), confirm=False)


async def test_a_rule_update_changes_only_what_differs() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), filter_from="a@example.com", folder="Billing")

    changed = await setup.service.update_rule(
        number, MailRuleSettings(maximum_age_days=7, folder="Billing"), confirm=False
    )

    assert (changed.maximum_age_days, changed.folder, changed.filter_from) == (
        7,
        "Billing",
        "a@example.com",
    )


async def test_an_update_that_changes_nothing_writes_nothing_even_for_a_removing_rule() -> None:
    setup = Setup(writes=False)
    number = setup.rule(setup.account(), action="delete", folder="INBOX")

    same = await setup.service.update_rule(number, MailRuleSettings(folder="INBOX"), confirm=False)

    assert same.action == "delete"


async def test_empty_strings_and_clear_empty_what_they_name() -> None:
    setup = Setup()
    number = setup.rule(
        setup.account(),
        filter_from="a@example.com",
        filter_subject="s",
        assign_correspondent=1,
        assign_correspondent_from="selected",
        assign_document_type=1,
        action="tag",
        action_parameter="kw",
    )
    clear: Sequence[RuleClearable] = ("assign_correspondent", "assign_document_type")

    changed = await setup.service.update_rule(
        number,
        MailRuleSettings(filter_from="", filter_subject="", action="mark_read"),
        clear=clear,
        confirm=False,
    )

    assert (changed.filter_from, changed.filter_subject) == (None, None)
    assert (changed.assign_correspondent, changed.assign_document_type) == (None, None)
    assert (changed.action, changed.action_parameter) == ("mark_read", None)


async def test_assigning_a_name_and_clearing_it_in_one_call_is_refused() -> None:
    setup = Setup()
    number = setup.rule(setup.account())

    with pytest.raises(Validation, match="both set and cleared"):
        _ = await setup.service.update_rule(
            number,
            MailRuleSettings(assign_correspondent="e2e-acme"),
            clear=("assign_correspondent",),
            confirm=False,
        )


async def test_tags_are_replaced_as_a_whole_and_emptied_with_an_empty_list() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), assign_tags=(1, 2))

    replaced = await setup.service.update_rule(
        number, MailRuleSettings(assign_tags=("e2e-private",)), confirm=False
    )
    emptied = await setup.service.update_rule(
        number, MailRuleSettings(assign_tags=()), confirm=False
    )

    assert [t.id for t in replaced.assign_tags] == [3]
    assert emptied.assign_tags == ()


async def test_a_rule_can_move_to_another_account() -> None:
    setup = Setup()
    first, second = setup.account("A"), setup.account("B")
    number = setup.rule(first)

    changed = await setup.service.update_rule(
        number, MailRuleSettings(account_id=second), confirm=False
    )

    assert changed.account_id == second
    with pytest.raises(NotFound, match="No mail account 9"):
        _ = await setup.service.update_rule(number, MailRuleSettings(account_id=9), confirm=False)


async def test_switching_to_deleting_needs_confirm() -> None:
    setup = Setup(writes=False)
    number = setup.rule(setup.account(), "Bills")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_rule(
            number, MailRuleSettings(action="delete"), confirm=False
        )

    assert refusal(raised) == (
        "Nothing was changed. Confirming would change the enabled mail rule 'Bills' (id 1) of "
        "account 'Invoices', which then deletes each mail from the mail server after Paperless "
        "imported documents from it. It covers mails in folder 'INBOX' up to 30 days old; a "
        "deleted mail cannot be put back by this server."
    )


@pytest.mark.parametrize(
    "change",
    [
        {"folder": "Other"},
        {"filter_from": "x@example.com"},
        {"maximum_age_days": 90},
        {"action_parameter": "Elsewhere"},
        {"account_id": 2},
        {"filter_attachment_filename_exclude": "*.png"},
    ],
)
async def test_widening_what_a_removing_rule_reaches_needs_confirm(
    change: dict[str, object],
) -> None:
    setup = Setup(writes=False)
    first = setup.account("A")
    _ = setup.account("B")
    number = setup.rule(first, action="move", action_parameter="Done", filter_from="a@example.com")

    with pytest.raises(ConfirmationRequired):
        _ = await setup.service.update_rule(
            number, MailRuleSettings.model_validate(change), confirm=False
        )


async def test_renaming_or_ordering_a_removing_rule_needs_no_confirmation() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), action="delete")

    changed = await setup.service.update_rule(
        number,
        MailRuleSettings(name="Renamed", order=3, assign_tags=("e2e-finance",)),
        confirm=False,
    )

    assert (changed.name, changed.order) == ("Renamed", 3)


async def test_a_confirmed_change_of_a_removing_rule_is_written() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), action="delete")

    changed = await setup.service.update_rule(
        number, MailRuleSettings(folder="Other"), confirm=True
    )

    assert changed.folder == "Other"


async def test_a_new_move_action_needs_its_folder() -> None:
    setup = Setup(writes=False)
    number = setup.rule(setup.account())

    with pytest.raises(Validation, match="action_parameter"):
        _ = await setup.service.update_rule(number, MailRuleSettings(action="move"), confirm=True)


async def test_an_owner_change_needs_confirm_and_says_what_it_does() -> None:
    setup = Setup(writes=False)
    number = setup.rule(setup.account(), "Bills")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_rule(number, MailRuleSettings(owner=3), confirm=False)
    with pytest.raises(ConfirmationRequired) as cleared:
        _ = await setup.service.update_rule(
            number, MailRuleSettings(), clear=("owner",), confirm=False
        )

    assert refusal(raised) == (
        "Nothing was changed. Confirming would hand the mail rule 'Bills' (id 1) over to user "
        "'e2e-reader' (id 3): the new owner takes it over, and you may not be able to change it "
        "again."
    )
    assert refusal(cleared) == (
        "Nothing was changed. Confirming would hand the mail rule 'Bills' (id 1) over to every "
        "user who may view mail rules: the new owner takes it over, and you may not be able to "
        "change it again."
    )


async def test_a_confirmed_owner_change_is_written_and_the_same_owner_needs_none() -> None:
    setup = Setup()
    number = setup.rule(setup.account())

    same = await setup.service.update_rule(number, MailRuleSettings(owner="admin"), confirm=False)
    changed = await setup.service.update_rule(
        number, MailRuleSettings(owner="e2e-reader"), confirm=True
    )

    assert same.owner is not None
    assert same.owner.id == 2
    assert changed.owner is not None
    assert changed.owner.id == 3


async def test_updating_a_rule_that_does_not_exist_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().service.update_rule(9, MailRuleSettings(name="x"), confirm=False)


# --- rules: delete


async def test_deleting_a_rule_needs_confirm_and_names_the_account() -> None:
    setup = Setup(writes=False)
    account = setup.account()
    number = setup.rule(account, "Bills")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.delete_rule(number, confirm=False)

    assert refusal(raised) == (
        "Nothing was changed. Confirming would delete the mail rule 'Bills' (id 1) of account "
        "'Invoices' and the records of the mail it processed; Paperless stops importing with it, "
        "the mail on the server stays."
    )


async def test_a_confirmed_rule_delete_removes_it() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), "Bills")
    setup.processed(number, "A mail")

    gone = await setup.service.delete_rule(number, confirm=True)

    assert gone.message == "Mail rule 'Bills' (id 1) was deleted."
    assert (setup.state.mail_rules, setup.state.processed_mail) == ([], [])


async def test_deleting_a_rule_that_does_not_exist_is_not_found() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().service.delete_rule(9, confirm=True)


# --- processed mail


async def test_processed_mail_is_listed_as_compact_rows_newest_first() -> None:
    setup = Setup()
    rule = setup.rule(setup.account())
    setup.processed(rule, "Old", processed=DAY)
    setup.processed(
        rule,
        "Bad",
        processed=DAY.replace(day=12),
        status="FAILED",
        error="Traceback (most recent call last):\n  File x\n" + "boom " * 100,
    )

    listing = await setup.service.list_processed(rule_id=None, status=None, page=1, page_size=25)

    assert (listing.count, listing.page, listing.next_page) == (2, 1, None)
    assert [m.subject for m in listing.mails] == ["Bad", "Old"]
    bad = listing.mails[0]
    assert bad.status == "FAILED"
    assert bad.rule_id == rule
    assert bad.error is not None
    assert len(bad.error) <= MAX_ERROR
    assert "\n" not in bad.error
    assert bad.error.endswith("…")
    assert listing.mails[1].error is None
    assert set(listing.mails[0].model_dump()) == {
        "id",
        "rule_id",
        "subject",
        "folder",
        "received",
        "processed",
        "status",
        "error",
    }


async def test_processed_mail_can_be_filtered_and_paged() -> None:
    setup = Setup()
    account = setup.account()
    first, second = setup.rule(account, "A"), setup.rule(account, "B")
    for number in range(3):
        setup.processed(first, f"A{number}", processed=DAY.replace(day=10 + number))
    setup.processed(second, "B0", status="FAILED")

    one = await setup.service.list_processed(rule_id=first, status=None, page=1, page_size=2)
    rest = await setup.service.list_processed(rule_id=first, status=None, page=2, page_size=2)
    failed = await setup.service.list_processed(rule_id=None, status="FAILED", page=1, page_size=25)

    assert (one.count, one.next_page, [m.subject for m in one.mails]) == (3, 2, ["A2", "A1"])
    assert (rest.next_page, [m.subject for m in rest.mails]) == (None, ["A0"])
    assert [m.subject for m in failed.mails] == ["B0"]


async def test_a_page_beyond_the_last_one_of_the_processed_mail_is_not_found() -> None:
    setup = Setup()

    with pytest.raises(NotFound, match="Page 4 of the processed mail"):
        _ = await setup.service.list_processed(rule_id=None, status=None, page=4, page_size=25)
