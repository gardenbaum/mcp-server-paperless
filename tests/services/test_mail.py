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

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    Ambiguous,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.mail import MailRuleSettings, NotText, Reference
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.mail import MAX_ERROR, MailService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.mail import RuleClearable

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-18"),
]

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
        self.service: MailService = MailService(self.fake, self.fake, taxonomy, self.fake)

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
    assert result.message == (
        "Mail account 'Invoices': Paperless could not connect to the mail server or log in. "
        "Check the server name, port, security setting, username and password."
    )
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
        "new documents appear after consumption (check the Paperless task list) and "
        "list_processed_mail shows "
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


@pytest.mark.parametrize(
    ("days", "reach"), [(0, "of any age"), (1, "up to 1 day old")], ids=["zero", "one"]
)
async def test_the_age_limit_is_shown_as_paperless_applies_it(days: int, reach: str) -> None:
    # Paperless filters by age only when maximum_age > 0: zero covers mails of any age.
    setup = Setup(writes=False)
    account = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.create_rule(
            MailRuleSettings(
                name="Purge all", account_id=account, action="delete", maximum_age_days=days
            ),
            confirm=False,
        )

    assert f"It covers mails in folder 'INBOX' {reach};" in refusal(raised)
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
        {"consumption_scope": "eml"},
        {"attachment_type": "all_files"},
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


async def test_letting_later_rules_run_after_an_importing_rule_needs_confirm() -> None:
    setup = Setup(writes=False)
    account = setup.account()
    first = setup.rule(account, "First", stop_processing=True, order=1)
    _ = setup.rule(account, "Purge", action="delete", order=2)

    with pytest.raises(ConfirmationRequired) as turned_off:
        _ = await setup.service.update_rule(
            first, MailRuleSettings(stop_processing=False), confirm=False
        )
    with pytest.raises(ConfirmationRequired) as reordered:
        _ = await setup.service.update_rule(first, MailRuleSettings(order=5), confirm=False)

    expected = (
        "Nothing was changed. Confirming would change the order or stop_processing of the "
        "enabled mail rule 'First' (id 1), so that the enabled rule 'Purge' (id 2) of the same "
        "account, which deletes mail, may run for mails that 'First' already imported from."
    )
    assert refusal(turned_off) == expected
    assert refusal(reordered) == expected


async def test_stop_processing_and_order_are_free_without_a_removing_rule_nearby() -> None:
    setup = Setup()
    account = setup.account()
    first = setup.rule(account, "First", stop_processing=True)
    _ = setup.rule(account, "Marker", action="flag")
    _ = setup.rule(account, "Off", action="delete", enabled=False)
    other = setup.account("Other")
    _ = setup.rule(other, "Elsewhere", action="delete")

    changed = await setup.service.update_rule(
        first, MailRuleSettings(stop_processing=False, order=4), confirm=False
    )
    turned_on = await setup.service.update_rule(
        first, MailRuleSettings(stop_processing=True), confirm=False
    )

    assert (changed.stop_processing, changed.order, turned_on.stop_processing) == (False, 4, True)


async def test_a_rule_owned_by_someone_else_needs_confirm_when_created() -> None:
    setup = Setup(writes=False)
    account = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.create_rule(
            MailRuleSettings(name="Bills", account_id=account, owner="e2e-reader"), confirm=False
        )

    assert refusal(raised) == (
        "Nothing was changed. Confirming would create the mail rule 'Bills' for user "
        "'e2e-reader' (id 3): the owner takes it over, and you may not be able to change it "
        "again."
    )
    assert setup.state.mail_rules == []


async def test_a_removing_rule_for_someone_else_names_both_effects_when_created() -> None:
    setup = Setup(writes=False)
    account = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.create_rule(
            MailRuleSettings(name="Purge", account_id=account, action="delete", owner="e2e-reader"),
            confirm=False,
        )

    text = refusal(raised)
    assert "which deletes each mail from the mail server" in text
    takeover = "the owner takes it over, and you may not be able to change it again."
    assert text.endswith(f"; and it belongs to user 'e2e-reader' (id 3): {takeover}")
    assert setup.state.mail_rules == []


async def test_a_rule_owned_by_the_caller_needs_no_confirmation_when_created() -> None:
    setup = Setup()
    account = setup.account()

    made = await setup.service.create_rule(
        MailRuleSettings(name="Bills", account_id=account, owner="admin"), confirm=False
    )
    elsewhere = await setup.service.create_rule(
        MailRuleSettings(name="Other", account_id=account, owner="e2e-reader"), confirm=True
    )

    assert made.owner is not None
    assert made.owner.id == 2
    assert elsewhere.owner is not None
    assert elsewhere.owner.id == 3


async def test_a_caller_who_cannot_be_identified_confirms_every_owner() -> None:
    permissions = frozenset({"view_mailaccount", "add_mailrule", "change_mailaccount"})
    setup = Setup(READER[0], writes=False, permissions=permissions)
    account = setup.account(owner=None)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.create_rule(
            MailRuleSettings(name="Bills", account_id=account, owner=3), confirm=False
        )

    assert "for user 3:" in refusal(raised)


async def test_a_fetch_by_a_user_who_is_not_a_superuser_warns_about_rules_it_cannot_see() -> None:
    setup = Setup(
        READER[0],
        writes=False,
        permissions=frozenset(
            {"view_mailaccount", "view_mailrule", "add_mailaccount", "view_uisettings"}
        ),
    )
    account = setup.account(owner=None)
    _ = setup.rule(account, "Mine", owner=None)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.process_account(account, confirm=False)

    assert refusal(raised).endswith(
        " Rules of other users on this account that you cannot see run too."
    )


# --- connection changes, in detail


async def test_a_port_only_change_needs_confirm() -> None:
    setup = Setup(writes=False)
    number = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_account(number, imap_port=143)

    assert refusal(raised) == (
        "Nothing was changed. Confirming would point mail account 'Invoices' (id 1) at "
        f"{HOST} port 143 (SSL) instead of {HOST} port {PORT} (SSL): Paperless keeps the saved "
        "password and sends it to the new server from its next fetch on."
    )


async def test_a_security_change_without_a_password_needs_confirm_and_is_named() -> None:
    setup = Setup(writes=False)
    number = setup.account()

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_account(number, imap_security="starttls")

    assert f"at {HOST} port {PORT} (STARTTLS) instead of {HOST} port {PORT} (SSL)" in refusal(
        raised
    )


async def test_an_account_that_has_no_encryption_can_be_renamed_and_kept_that_way() -> None:
    setup = Setup()
    number = setup.account(imap_security="none")

    renamed = await setup.service.update_account(number, name="Plain", imap_security="none")

    assert (renamed.name, renamed.imap_security) == ("Plain", "none")


async def test_the_same_security_in_another_spelling_of_the_call_is_no_change() -> None:
    setup = Setup(writes=False)
    number = setup.account()

    same = await setup.service.update_account(number, imap_security="ssl", imap_server=HOST)

    assert same.imap_security == "ssl"


# --- the action and its parameter


@pytest.mark.parametrize(("action", "parameter"), [("move", "Archive"), ("tag", "paperless")])
async def test_switching_to_move_or_tag_with_a_parameter_succeeds(
    action: str, parameter: str
) -> None:
    setup = Setup()
    number = setup.rule(setup.account(), enabled=False)

    changed = await setup.service.update_rule(
        number,
        MailRuleSettings.model_validate({"action": action, "action_parameter": parameter}),
        confirm=False,
    )

    assert (changed.action, changed.action_parameter) == (action, parameter)


@pytest.mark.parametrize("action", ["move", "tag"])
async def test_switching_to_move_or_tag_without_a_parameter_names_what_is_missing(
    action: str,
) -> None:
    setup = Setup(writes=False)
    number = setup.rule(setup.account())

    with pytest.raises(Validation) as raised:
        _ = await setup.service.update_rule(
            number, MailRuleSettings.model_validate({"action": action}), confirm=False
        )

    what = "the folder to move the mail to" if action == "move" else "the tag to set"
    assert raised.value.message == f"The action {action} needs action_parameter: {what}."
    assert raised.value.hint == f"Pass action_parameter with {what}."


async def test_an_empty_parameter_on_a_moving_rule_is_refused_and_a_new_one_is_taken() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), action="move", action_parameter="Done", enabled=False)

    with pytest.raises(Validation, match="The action move needs action_parameter"):
        _ = await setup.service.update_rule(
            number, MailRuleSettings(action_parameter=""), confirm=False
        )
    changed = await setup.service.update_rule(
        number, MailRuleSettings(action_parameter="Elsewhere"), confirm=False
    )

    assert changed.action_parameter == "Elsewhere"


async def test_an_empty_parameter_on_a_flagging_rule_is_taken_as_none() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), action="flag", action_parameter="old")

    changed = await setup.service.update_rule(
        number, MailRuleSettings(action_parameter=""), confirm=False
    )

    assert changed.action_parameter is None


async def test_an_action_that_stays_the_same_with_its_parameter_changes_the_parameter() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), action="tag", action_parameter="a", enabled=False)

    changed = await setup.service.update_rule(
        number, MailRuleSettings(action="tag", action_parameter="b"), confirm=False
    )

    assert changed.action_parameter == "b"


# --- paging


async def test_an_empty_list_has_one_empty_first_page() -> None:
    setup = Setup()

    accounts = await setup.service.list_accounts(page=1, page_size=2)
    rules = await setup.service.list_rules(account_id=None, page=1, page_size=2)

    assert (accounts.count, accounts.next_page, accounts.accounts) == (0, None, ())
    assert (rules.count, rules.next_page, rules.rules) == (0, None, ())
    with pytest.raises(NotFound, match=r"There is no page 2: the list has 1 page\."):
        _ = await setup.service.list_accounts(page=2, page_size=2)


@pytest.mark.parametrize(
    ("total", "page", "names", "following"),
    [
        (4, 1, ["A0", "A1"], 2),
        (4, 2, ["A2", "A3"], None),
        (5, 2, ["A2", "A3"], 3),
        (5, 3, ["A4"], None),
        (1, 1, ["A0"], None),
    ],
)
async def test_pages_end_exactly_where_the_list_ends(
    total: int, page: int, names: list[str], following: int | None
) -> None:
    setup = Setup()
    account = setup.account()
    for number in range(total):
        _ = setup.account(f"B{number}")
        _ = setup.rule(account, f"A{number}", order=number)

    listing = await setup.service.list_rules(account_id=account, page=page, page_size=2)

    assert ([r.name for r in listing.rules], listing.next_page) == (names, following)
    assert listing.page == page


async def test_a_page_past_the_end_counts_the_pages() -> None:
    setup = Setup()
    for number in range(4):
        _ = setup.account(f"B{number}")

    with pytest.raises(NotFound) as raised:
        _ = await setup.service.list_accounts(page=3, page_size=2)

    assert raised.value.message == "There is no page 3: the list has 2 pages."
    assert raised.value.hint == "Start again at page 1."


# --- everything a row carries


async def test_a_rule_row_carries_every_setting() -> None:
    setup = Setup()
    account = setup.account()
    number = setup.rule(
        account,
        "Full",
        enabled=False,
        order=4,
        folder="Billing",
        filter_from="f",
        filter_to="t",
        filter_subject="s",
        filter_body="b",
        filter_attachment_filename_include="i",
        filter_attachment_filename_exclude="x",
        maximum_age=9,
        attachment_type="all_files",
        consumption_scope="eml",
        pdf_layout="html",
        action="tag",
        action_parameter="kw",
        assign_title_from="filename",
        assign_tags=(1,),
        assign_correspondent_from="selected",
        assign_correspondent=1,
        assign_document_type=1,
        assign_owner_from_rule=False,
        stop_processing=True,
        owner=2,
    )

    row = await setup.service.get_rule(number)

    assert row.model_dump(
        exclude={"assign_tags", "assign_correspondent", "assign_document_type", "owner"}
    ) == {
        "id": number,
        "name": "Full",
        "account_id": account,
        "enabled": False,
        "order": 4,
        "folder": "Billing",
        "filter_from": "f",
        "filter_to": "t",
        "filter_subject": "s",
        "filter_body": "b",
        "filter_attachment_filename_include": "i",
        "filter_attachment_filename_exclude": "x",
        "maximum_age_days": 9,
        "attachment_type": "all_files",
        "consumption_scope": "eml",
        "pdf_layout": "html",
        "action": "tag",
        "action_parameter": "kw",
        "assign_title_from": "filename",
        "assign_correspondent_from": "selected",
        "assign_owner_from_rule": False,
        "stop_processing": True,
        "can_change": True,
    }
    assert (row.owner, row.assign_correspondent, row.assign_document_type) == (
        Reference(id=2, name="admin"),
        Reference(id=1, name="e2e-acme"),
        Reference(id=1, name="e2e-invoice"),
    )
    assert row.assign_tags == (Reference(id=1, name="e2e-finance"),)


async def test_an_account_row_carries_every_setting() -> None:
    setup = Setup()
    number = setup.account(
        imap_security="none",
        character_set="ISO-8859-1",
        account_type="outlook_oauth",
        is_token=True,
        expiration=DAY,
    )

    row = await setup.service.get_account(number)

    assert row.model_dump() == {
        "id": number,
        "name": "Invoices",
        "imap_server": HOST,
        "imap_port": PORT,
        "imap_security": "none",
        "username": "me@example.com",
        "character_set": "ISO-8859-1",
        "account_type": "outlook_oauth",
        "is_token": True,
        "owner": 2,
        "can_change": True,
        "token_expires": DAY,
    }


async def test_several_rules_that_could_run_after_an_importing_rule_are_all_named() -> None:
    setup = Setup(writes=False)
    account = setup.account()
    first = setup.rule(account, "First")
    _ = setup.rule(account, "Purge", action="delete")
    _ = setup.rule(account, "Archive", action="move", action_parameter="Done")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_rule(first, MailRuleSettings(order=9), confirm=False)

    assert refusal(raised) == (
        "Nothing was changed. Confirming would change the order or stop_processing of the "
        "enabled mail rule 'First' (id 1), so that the enabled rules 'Purge' (id 2) and "
        "'Archive' (id 3) of the same account, which delete or move mail, may run for mails "
        "that 'First' already imported from."
    )


# --- exact messages and hints (mutation: the text is the contract)


async def test_the_password_refusals_have_their_exact_texts() -> None:
    setup = Setup(writes=False)

    async def refused(password: SecretStr) -> Validation:
        with pytest.raises(Validation) as raised:
            _ = await setup.service.create_account(
                name="n",
                imap_server=HOST,
                imap_port=PORT,
                username="u",
                password=password,
                imap_security=None,
                character_set=None,
            )
        return raised.value

    not_text = await refused(NotText())
    blank = await refused(SecretStr("  "))
    stars = await refused(SecretStr("***"))

    assert (not_text.message, not_text.hint) == (
        "The password must be text.",
        "Pass the password as a string.",
    )
    assert (blank.message, blank.hint) == (
        "The password must not be empty.",
        "Pass the password of the mail account.",
    )
    assert stars.message.startswith("A password that consists only of asterisks")
    assert stars.hint == "Leave password out to keep the saved one."


async def test_an_update_with_nothing_to_change_has_its_exact_texts() -> None:
    setup = Setup()
    account = setup.account()
    rule = setup.rule(account)

    with pytest.raises(Validation) as for_account:
        _ = await setup.service.update_account(account)
    with pytest.raises(Validation) as for_rule:
        _ = await setup.service.update_rule(rule, MailRuleSettings(), confirm=False)

    assert (for_account.value.message, for_account.value.hint) == (
        "Nothing to change: pass at least one setting.",
        "Name the settings of the account to change.",
    )
    assert (for_rule.value.message, for_rule.value.hint) == (
        "Nothing to change: pass at least one setting.",
        "Name the settings of the rule to change.",
    )


async def test_a_rule_without_name_or_account_has_its_exact_texts() -> None:
    setup = Setup()

    with pytest.raises(Validation) as nameless:
        _ = await setup.service.create_rule(MailRuleSettings(account_id=1), confirm=False)
    with pytest.raises(Validation) as accountless:
        _ = await setup.service.create_rule(MailRuleSettings(name="R"), confirm=False)

    assert (nameless.value.message, nameless.value.hint) == (
        "A mail rule needs a name.",
        "Pass name.",
    )
    assert (accountless.value.message, accountless.value.hint) == (
        "A mail rule needs an account.",
        "Pass account_id; list_mail_accounts shows the ids.",
    )


async def test_a_name_that_is_both_set_and_cleared_has_its_exact_texts() -> None:
    setup = Setup()
    number = setup.rule(setup.account())

    with pytest.raises(Validation) as raised:
        _ = await setup.service.update_rule(
            number, MailRuleSettings(owner="admin"), clear=("owner",), confirm=False
        )

    assert (raised.value.message, raised.value.hint) == (
        "owner is both set and cleared in one call.",
        "Either pass owner or name it in clear.",
    )


async def test_a_correspondent_with_another_source_has_its_exact_texts() -> None:
    setup = Setup()
    number = setup.rule(setup.account())

    with pytest.raises(Validation) as raised:
        _ = await setup.service.update_rule(
            number,
            MailRuleSettings(assign_correspondent=1, assign_correspondent_from="name"),
            confirm=False,
        )

    assert (raised.value.message, raised.value.hint) == (
        "assign_correspondent is used only with assign_correspondent_from selected.",
        "Pass assign_correspondent_from selected, or leave it out.",
    )


async def test_the_refusals_about_unreadable_rules_have_their_exact_texts() -> None:
    permissions = frozenset({"view_mailaccount", "delete_mailaccount", "add_mailaccount"})
    setup = Setup(READER[0], writes=False, permissions=permissions)
    number = setup.account(owner=None)

    with pytest.raises(ConfirmationRequired) as deleting:
        _ = await setup.service.delete_account(number, confirm=False)
    with pytest.raises(ConfirmationRequired) as fetching:
        _ = await setup.service.process_account(number, confirm=False)

    assert refusal(deleting) == (
        "Nothing was changed. Confirming would delete mail account 'Invoices' (id 1) with its "
        "rules and the records of the mail they processed; Paperless stops fetching from it, "
        "the mail on the server stays."
    )
    assert refusal(fetching) == (
        f"Nothing was changed. Confirming would fetch the mail of account 'Invoices' ({HOST}) "
        "now: Paperless connects to the mail server and imports documents; its rules could not "
        "be read, so what they do to the mail is unknown. Rules of other users on this account "
        "that you cannot see run too."
    )


async def test_a_confirmed_delete_by_a_user_who_cannot_list_rules_says_so() -> None:
    permissions = frozenset({"view_mailaccount", "delete_mailaccount"})
    setup = Setup(READER[0], permissions=permissions)
    number = setup.account(owner=None)

    gone = await setup.service.delete_account(number, confirm=True)

    assert gone.message == (
        "Mail account 'Invoices' (id 1) was deleted with its rules; Paperless no longer "
        "fetches from it."
    )


async def test_a_fetch_with_one_enabled_rule_says_matches() -> None:
    setup = Setup(writes=False)
    number = setup.account()
    _ = setup.rule(number, "Read")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.process_account(number, confirm=False)

    assert refusal(raised) == (
        f"Nothing was changed. Confirming would fetch the mail of account 'Invoices' ({HOST}) "
        "now: Paperless connects to the mail server and imports documents from the mails that "
        "its 1 enabled rule matches, then acts on those mails on the server. Rule 'Read' "
        "(id 1) marks the mail as read."
    )


async def test_a_caller_who_is_not_identified_is_told_about_rules_it_cannot_see() -> None:
    permissions = frozenset({"view_mailaccount", "view_mailrule", "add_mailaccount"})
    setup = Setup(READER[0], writes=False, permissions=permissions)
    number = setup.account(owner=None)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.process_account(number, confirm=False)

    ending = (
        "no enabled rule, so Paperless imports nothing. Rules of other users on this account "
        "that you cannot see run too."
    )
    assert refusal(raised).endswith(ending)


async def test_a_move_rule_without_a_parameter_is_quoted_as_empty() -> None:
    setup = Setup(writes=False)
    number = setup.account()
    rule = setup.rule(number, "Odd", action="move", action_parameter=None)

    with pytest.raises(ConfirmationRequired) as fetching:
        _ = await setup.service.process_account(number, confirm=False)
    with pytest.raises(ConfirmationRequired) as widening:
        _ = await setup.service.update_rule(rule, MailRuleSettings(folder="Other"), confirm=False)

    assert "Rule 'Odd' (id 1) moves the mail to ''" in refusal(fetching)
    assert "moves each mail to the folder '' on the mail server" in refusal(widening)


async def test_two_effects_are_joined_in_one_refusal() -> None:
    setup = Setup(writes=False)
    number = setup.rule(setup.account(), "Purge", action="delete")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_rule(
            number, MailRuleSettings(folder="Other", owner=3), confirm=False
        )

    message = refusal(raised)
    assert "; and change the enabled mail rule 'Purge'" in message
    assert message.startswith("Nothing was changed. Confirming would hand the mail rule 'Purge'")


async def test_switching_to_a_plain_action_keeps_a_parameter_that_is_given() -> None:
    setup = Setup()
    number = setup.rule(setup.account(), action="move", action_parameter="Done", enabled=False)
    plain = setup.rule(1, "Plain", enabled=False)

    kept = await setup.service.update_rule(
        number, MailRuleSettings(action="flag", action_parameter="given"), confirm=False
    )
    from_nothing = await setup.service.update_rule(
        plain, MailRuleSettings(action="flag", action_parameter="x"), confirm=False
    )

    assert (kept.action, kept.action_parameter) == ("flag", "given")
    assert (from_nothing.action, from_nothing.action_parameter) == ("flag", "x")


async def test_one_moving_rule_beside_a_reordered_one_is_named_with_its_verb() -> None:
    setup = Setup(writes=False)
    account = setup.account()
    first = setup.rule(account, "First")
    _ = setup.rule(account, "Archive", action="move", action_parameter="Done")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.service.update_rule(first, MailRuleSettings(order=9), confirm=False)

    assert "'Archive' (id 2) of the same account, which moves mail, may run" in refusal(raised)


async def test_a_processed_error_is_one_line_cut_at_the_limit() -> None:
    setup = Setup()
    rule = setup.rule(setup.account())
    setup.processed(rule, "exact", error="e" * MAX_ERROR)
    setup.processed(rule, "over", error="o" * (MAX_ERROR + 1))
    setup.processed(rule, "lines", error="first\nsecond   third")

    listing = await setup.service.list_processed(rule_id=None, status=None, page=1, page_size=25)

    by_subject = {m.subject: m.error for m in listing.mails}
    assert by_subject["exact"] == "e" * MAX_ERROR
    assert by_subject["over"] == "o" * (MAX_ERROR - 1) + "…"
    assert by_subject["lines"] == "first second third"
