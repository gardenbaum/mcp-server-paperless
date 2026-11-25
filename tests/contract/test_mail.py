"""Contract of MailReader and MailWriter: the same assertions against the fake and a live Paperless.

Accounts and rules get a unique name per test; the `mail` fixture deletes what a test made, rules
before accounts, also when it failed (the stack is shared). The E2E stack has no IMAP server, so
the connection test can only fail there: it is checked against hosts that refuse connections or do
not exist. A login that works, a login that fails and a server that never answers are covered by
the fake (tests/services/test_mail.py) and by the adapter tests, which script the wire. Processed
mail is written only by a real mail fetch, so its rows are listed on the fake only
(tests/services/test_mail.py); here the filters and the paging of an empty list are checked.
"""

import uuid
from contextlib import suppress
from typing import TYPE_CHECKING

import anyio
import pytest
from pydantic import SecretStr

from contract.conftest import EXPIRES, TOKEN
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, Validation
from mcp_server_paperless.domain.mail import (
    MailAccount,
    MailAccountValues,
    MailFacts,
    MailRule,
    MailRuleValues,
    ProcessedMail,
)
from mcp_server_paperless.paperless.mail import unreachable_server
from mcp_server_paperless.services.log_records import read_log_records
from mcp_server_paperless.services.mail_log_lines import about_mail, about_run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from contract.conftest import Backend, Ports, Processed
    from mcp_server_paperless.domain.mail import ProcessedStatus

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-8"),
    pytest.mark.decision("decision-18"),
]
LIST_ACCOUNTS, GET_ACCOUNT = "MailReader.list_mail_accounts", "MailReader.get_mail_account"
LIST_RULES, GET_RULE = "MailReader.list_mail_rules", "MailReader.get_mail_rule"
PAGE_RULES = "MailReader.list_mail_rule_page"
LIST_PROCESSED = "MailReader.list_processed_mail"
DELETE_PROCESSED = "MailWriter.delete_processed_mail"
GET_PROCESSED = "MailReader.get_processed_mail"
CREATE_ACCOUNT = "MailWriter.create_mail_account"
UPDATE_ACCOUNT = "MailWriter.update_mail_account"
DELETE_ACCOUNT = "MailWriter.delete_mail_account"
TEST_ACCOUNT, PROCESS_ACCOUNT = "MailWriter.test_mail_account", "MailWriter.process_mail_account"
CREATE_RULE, UPDATE_RULE = "MailWriter.create_mail_rule", "MailWriter.update_mail_rule"
DELETE_RULE = "MailWriter.delete_mail_rule"
PASSWORD = "contract-Passw0rd-4711"
# A host that nothing listens on (port 1 of the machine Paperless runs on).
REFUSING = "127.0.0.1"
UNKNOWN = 999_999
MAILING = frozenset({"view_mailaccount", "add_mailaccount", "view_mailrule"})


class Mail:
    """Accounts and rules a test made; they are deleted when it ends."""

    def __init__(self, ports: Ports) -> None:
        self.ports: Ports = ports
        self.accounts: list[int] = []
        self.rules: list[int] = []

    def name(self) -> str:
        return f"contract-{uuid.uuid4().hex[:12]}"

    async def account(self, **values: object) -> MailAccount:
        """A new account on a host that refuses connections, unless the test says otherwise."""
        fields: dict[str, object] = {
            "name": self.name(),
            "imap_server": REFUSING,
            "imap_port": 1,
            "username": "user@example.com",
            "password": SecretStr(PASSWORD),
        }
        made = await self.ports.mail_writes.create_mail_account(
            MailAccountValues.model_validate(fields | values)
        )
        self.accounts.append(made.id)
        return made

    async def rule(self, account_id: int, **values: object) -> MailRule:
        fields: dict[str, object] = {"name": self.name(), "account_id": account_id}
        made = await self.ports.mail_writes.create_mail_rule(
            MailRuleValues.model_validate(fields | values)
        )
        self.rules.append(made.id)
        return made


@pytest.fixture
async def mail(backend: Backend) -> AsyncIterator[Mail]:
    made = Mail(backend.admin)
    yield made
    for rule in made.rules:
        try:
            await backend.admin.mail_writes.delete_mail_rule(rule)
        except NotFound:
            continue
    for account in made.accounts:
        try:
            await backend.admin.mail_writes.delete_mail_account(account)
        except NotFound:
            continue


async def tags(ports: Ports) -> dict[str, int]:
    return {item.name: item.id for item in await ports.taxonomy.list_taxonomy("tag")}


# --- accounts


@pytest.mark.contract(CREATE_ACCOUNT)
@pytest.mark.contract(LIST_ACCOUNTS)
@pytest.mark.contract(GET_ACCOUNT)
async def test_a_created_account_has_the_defaults_and_no_password(
    backend: Backend, mail: Mail
) -> None:
    made = await mail.account()

    assert (made.imap_server, made.imap_port, made.username) == (REFUSING, 1, "user@example.com")
    assert (made.imap_security, made.character_set) == ("ssl", "UTF-8")
    assert (made.account_type, made.is_token, made.expiration) == ("imap", False, None)
    assert made.can_change
    assert await backend.admin.mail.get_mail_account(made.id) == made
    assert made in await backend.admin.mail.list_mail_accounts()
    for shown in (made, *await backend.admin.mail.list_mail_accounts()):
        assert "password" not in shown.model_dump()
        assert PASSWORD not in shown.model_dump_json()
        assert "*" not in shown.model_dump_json()


@pytest.mark.contract(CREATE_ACCOUNT)
async def test_an_account_takes_its_security_and_character_set(
    backend: Backend, mail: Mail
) -> None:
    made = await mail.account(imap_security="starttls", character_set="ISO-8859-1")

    assert (made.imap_security, made.character_set) == ("starttls", "ISO-8859-1")
    assert (await backend.admin.mail.get_mail_account(made.id)).imap_security == "starttls"


@pytest.mark.contract(CREATE_ACCOUNT)
async def test_an_account_needs_its_connection_data_and_a_unique_name(
    backend: Backend, mail: Mail
) -> None:
    first = await mail.account()

    with pytest.raises(Validation, match="imap_server"):
        _ = await backend.admin.mail_writes.create_mail_account(
            MailAccountValues(name=mail.name(), imap_port=993)
        )
    with pytest.raises(Validation, match="name"):
        _ = await mail.account(name=first.name)


@pytest.mark.contract(CREATE_ACCOUNT)
@pytest.mark.contract(UPDATE_ACCOUNT)
@pytest.mark.parametrize("password", ["*", "*****"])
async def test_a_password_of_asterisks_is_refused(
    backend: Backend, mail: Mail, password: str
) -> None:
    made = await mail.account()

    with pytest.raises(Validation, match="asterisks"):
        _ = await mail.account(password=SecretStr(password))
    with pytest.raises(Validation, match="asterisks"):
        _ = await backend.admin.mail_writes.update_mail_account(
            made.id, MailAccountValues(password=SecretStr(password))
        )


@pytest.mark.contract(UPDATE_ACCOUNT)
async def test_an_update_changes_only_what_is_set(backend: Backend, mail: Mail) -> None:
    made = await mail.account(imap_security="none")
    renamed = mail.name()

    changed = await backend.admin.mail_writes.update_mail_account(
        made.id, MailAccountValues(name=renamed, imap_port=143)
    )

    assert (changed.name, changed.imap_port) == (renamed, 143)
    assert (changed.imap_server, changed.imap_security) == (made.imap_server, "none")
    assert await backend.admin.mail.get_mail_account(made.id) == changed


@pytest.mark.contract(UPDATE_ACCOUNT)
async def test_a_new_password_is_accepted_and_never_shown(backend: Backend, mail: Mail) -> None:
    made = await mail.account()

    changed = await backend.admin.mail_writes.update_mail_account(
        made.id, MailAccountValues(password=SecretStr("another-Passw0rd-1"))
    )

    assert changed == made
    assert "another-Passw0rd-1" not in repr(changed)


@pytest.mark.contract(UPDATE_ACCOUNT)
async def test_updating_an_account_that_does_not_exist_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound, match="No mail account"):
        _ = await backend.admin.mail_writes.update_mail_account(
            UNKNOWN, MailAccountValues(name="x")
        )


@pytest.mark.contract(GET_ACCOUNT)
async def test_an_account_that_does_not_exist_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound, match="No mail account"):
        _ = await backend.admin.mail.get_mail_account(UNKNOWN)


@pytest.mark.contract(DELETE_ACCOUNT)
async def test_a_deleted_account_takes_its_rules_with_it(backend: Backend, mail: Mail) -> None:
    made = await mail.account()
    rule = await mail.rule(made.id)

    await backend.admin.mail_writes.delete_mail_account(made.id)

    assert made.id not in {a.id for a in await backend.admin.mail.list_mail_accounts()}
    with pytest.raises(NotFound):
        _ = await backend.admin.mail.get_mail_rule(rule.id)
    with pytest.raises(NotFound):
        await backend.admin.mail_writes.delete_mail_account(made.id)


@pytest.mark.decision("decision-90")
@pytest.mark.contract(PROCESS_ACCOUNT)
async def test_the_log_lines_of_a_fetch_have_the_known_forms(backend: Backend, mail: Mail) -> None:
    idle, refused = await mail.account(), await mail.account()
    _ = await mail.rule(refused.id)
    nothing = MailFacts()
    told: set[str] = set()

    for _ in range(60):  # another fetch can make one skip, so it is asked for again
        for account in (idle, refused):
            await backend.admin.mail_writes.process_mail_account(account.id)
        runs = await read_log_records(backend.admin.management, "paperless")
        told |= {r.message for r in runs if about_run(r, idle.name)}
        with suppress(NotFound):  # not yet: the first fetch of all makes this log
            mails = await read_log_records(backend.admin.management, "mail")
            told |= {r.message[:40] for r in mails if about_mail(r, refused.name, (), nothing)}
        if len(told) >= 3:
            break
        await anyio.sleep(1)

    assert told == {
        f"No rules enabled for account {idle.name}. Skipping.",
        f"Processing mail account {refused.name}"[:40],
        f"Error while retrieving mailbox {refused.name}: "[:40],
    }


@pytest.mark.contract(TEST_ACCOUNT)
@pytest.mark.parametrize("host", [REFUSING, "no-such-host.invalid"])
async def test_a_connection_that_fails_is_a_result_not_an_error(
    backend: Backend, mail: Mail, host: str
) -> None:
    made = await mail.account(imap_server=host)

    result = await backend.admin.mail_writes.test_mail_account(made.id)

    assert result == unreachable_server()
    assert not result.success
    assert PASSWORD not in result.message


@pytest.mark.contract(TEST_ACCOUNT)
@pytest.mark.contract(PROCESS_ACCOUNT)
async def test_testing_or_fetching_an_account_that_does_not_exist_is_not_found(
    backend: Backend,
) -> None:
    with pytest.raises(NotFound, match="No mail account"):
        _ = await backend.admin.mail_writes.test_mail_account(UNKNOWN)
    with pytest.raises(NotFound, match="No mail account"):
        await backend.admin.mail_writes.process_mail_account(UNKNOWN)


@pytest.mark.contract(PROCESS_ACCOUNT)
async def test_a_fetch_is_queued(backend: Backend, mail: Mail) -> None:
    made = await mail.account()

    assert await backend.admin.mail_writes.process_mail_account(made.id) is None


@pytest.mark.contract(TEST_ACCOUNT)
@pytest.mark.contract(PROCESS_ACCOUNT)
async def test_testing_needs_the_change_permission_and_fetching_the_add_permission(
    backend: Backend, mail: Mail
) -> None:
    """Probed on 3.4.2: `process` is a POST, which asks for `add_mailaccount`, not for `change`."""
    user = backend.restricted(MAILING)
    made = await user.mail_writes.create_mail_account(
        MailAccountValues(
            name=f"contract-{uuid.uuid4().hex[:12]}",
            imap_server=REFUSING,
            imap_port=1,
            username="u",
            password=SecretStr(PASSWORD),
        )
    )
    mail.accounts.append(made.id)

    with pytest.raises(PermissionDenied):
        _ = await user.mail_writes.test_mail_account(made.id)
    assert await user.mail_writes.process_mail_account(made.id) is None


@pytest.mark.contract(LIST_ACCOUNTS)
@pytest.mark.contract(GET_RULE)
@pytest.mark.contract(LIST_RULES)
@pytest.mark.contract(LIST_PROCESSED)
@pytest.mark.contract(CREATE_ACCOUNT)
@pytest.mark.contract(CREATE_RULE)
@pytest.mark.contract(UPDATE_RULE)
@pytest.mark.contract(DELETE_RULE)
@pytest.mark.contract(DELETE_ACCOUNT)
@pytest.mark.contract(GET_ACCOUNT)
@pytest.mark.contract(UPDATE_ACCOUNT)
async def test_a_user_without_mail_permissions_is_denied_everywhere(backend: Backend) -> None:
    blind = backend.blind

    with pytest.raises(PermissionDenied):
        _ = await blind.mail.list_mail_accounts()
    with pytest.raises(PermissionDenied):
        _ = await blind.mail.get_mail_account(1)
    with pytest.raises(PermissionDenied):
        _ = await blind.mail.list_mail_rules()
    with pytest.raises(PermissionDenied):
        _ = await blind.mail.list_mail_rule_page(page=1, page_size=1)
    with pytest.raises(PermissionDenied):
        _ = await blind.mail.get_mail_rule(1)
    with pytest.raises(PermissionDenied):
        _ = await blind.mail.list_processed_mail(
            rule_id=None, status=None, ordering="-processed", page=1, page_size=25
        )
    with pytest.raises(PermissionDenied):
        _ = await blind.mail.get_processed_mail(1)
    with pytest.raises(PermissionDenied):
        _ = await blind.mail_writes.create_mail_account(MailAccountValues(name="x"))
    with pytest.raises(PermissionDenied):
        _ = await blind.mail_writes.update_mail_account(1, MailAccountValues(name="x"))
    with pytest.raises(PermissionDenied):
        await blind.mail_writes.delete_mail_account(1)
    with pytest.raises(PermissionDenied):
        _ = await blind.mail_writes.create_mail_rule(MailRuleValues(name="x", account_id=1))
    with pytest.raises(PermissionDenied):
        _ = await blind.mail_writes.update_mail_rule(1, MailRuleValues(name="x"))
    with pytest.raises(PermissionDenied):
        await blind.mail_writes.delete_mail_rule(1)


# --- rules


@pytest.mark.contract(CREATE_RULE)
@pytest.mark.contract(GET_RULE)
@pytest.mark.contract(LIST_RULES)
async def test_a_created_rule_has_paperlesss_defaults(backend: Backend, mail: Mail) -> None:
    account = await mail.account()

    made = await mail.rule(account.id)

    assert (made.account_id, made.enabled, made.order, made.folder) == (
        account.id,
        True,
        0,
        "INBOX",
    )
    assert (made.maximum_age, made.action, made.action_parameter) == (30, "mark_read", None)
    assert (made.attachment_type, made.consumption_scope, made.pdf_layout) == (
        "attachments_only",
        "attachments",
        "default",
    )
    assert (made.assign_title_from, made.assign_correspondent_from) == ("subject", "none")
    assert (made.assign_tags, made.assign_correspondent, made.assign_document_type) == (
        (),
        None,
        None,
    )
    assert (made.assign_owner_from_rule, made.stop_processing, made.can_change) == (
        True,
        False,
        True,
    )
    assert made.filter_from is None
    assert await backend.admin.mail.get_mail_rule(made.id) == made
    assert made in await backend.admin.mail.list_mail_rules()


@pytest.mark.contract(CREATE_RULE)
async def test_a_rule_takes_every_setting_and_the_taxonomy_by_id(
    backend: Backend, mail: Mail
) -> None:
    account = await mail.account()
    known = await tags(backend.admin)
    chosen = tuple(sorted(known.values())[:2])
    correspondent = (await backend.admin.taxonomy.list_taxonomy("correspondent"))[0].id
    document_type = (await backend.admin.taxonomy.list_taxonomy("document_type"))[0].id

    made = await mail.rule(
        account.id,
        enabled=False,
        order=5,
        folder="Billing",
        filter_from="billing@example.com",
        filter_to="me@example.com",
        filter_subject="Invoice",
        filter_body="amount due",
        filter_attachment_filename_include="*.pdf",
        filter_attachment_filename_exclude="logo*",
        maximum_age=90,
        attachment_type="all_files",
        consumption_scope="eml_and_attachments",
        pdf_layout="html_text",
        action="move",
        action_parameter="Archive",
        assign_title_from="filename",
        assign_tags=chosen,
        assign_correspondent_from="selected",
        assign_correspondent=correspondent,
        assign_document_type=document_type,
        assign_owner_from_rule=False,
        stop_processing=True,
    )

    assert (made.enabled, made.order, made.folder, made.maximum_age) == (False, 5, "Billing", 90)
    assert (made.filter_from, made.filter_to, made.filter_subject, made.filter_body) == (
        "billing@example.com",
        "me@example.com",
        "Invoice",
        "amount due",
    )
    assert (
        made.filter_attachment_filename_include,
        made.filter_attachment_filename_exclude,
    ) == ("*.pdf", "logo*")
    assert (made.attachment_type, made.consumption_scope, made.pdf_layout) == (
        "all_files",
        "eml_and_attachments",
        "html_text",
    )
    assert (made.action, made.action_parameter, made.assign_title_from) == (
        "move",
        "Archive",
        "filename",
    )
    assert (made.assign_tags, made.assign_correspondent_from) == (chosen, "selected")
    assert (made.assign_correspondent, made.assign_document_type) == (correspondent, document_type)
    assert (made.assign_owner_from_rule, made.stop_processing) == (False, True)
    assert await backend.admin.mail.get_mail_rule(made.id) == made


@pytest.mark.contract(CREATE_RULE)
async def test_a_rule_that_moves_or_tags_needs_a_parameter(mail: Mail) -> None:
    account = await mail.account()

    for action in ("move", "tag"):
        with pytest.raises(Validation, match="action parameter is required"):
            _ = await mail.rule(account.id, action=action)
    flagged = await mail.rule(account.id, action="flag")
    assert flagged.action == "flag"


@pytest.mark.contract(CREATE_RULE)
async def test_a_rule_needs_a_name_an_account_and_known_taxonomy(
    backend: Backend, mail: Mail
) -> None:
    account = await mail.account()
    first = await mail.rule(account.id)

    with pytest.raises(Validation, match="name"):
        _ = await backend.admin.mail_writes.create_mail_rule(MailRuleValues(account_id=account.id))
    with pytest.raises(Validation, match="account"):
        _ = await backend.admin.mail_writes.create_mail_rule(MailRuleValues(name=mail.name()))
    with pytest.raises(Validation, match="account"):
        _ = await mail.rule(UNKNOWN)
    with pytest.raises(Validation, match="assign_tags"):
        _ = await mail.rule(account.id, assign_tags=(UNKNOWN,))
    with pytest.raises(Validation, match="assign_correspondent"):
        _ = await mail.rule(account.id, assign_correspondent=UNKNOWN)
    with pytest.raises(Validation, match="assign_document_type"):
        _ = await mail.rule(account.id, assign_document_type=UNKNOWN)
    with pytest.raises(Validation, match="owner"):
        _ = await mail.rule(account.id, owner=UNKNOWN)
    with pytest.raises(Validation, match="unreasonably large"):
        _ = await mail.rule(account.id, maximum_age=40_000)
    with pytest.raises(Validation, match="unique"):
        _ = await mail.rule(account.id, name=first.name)


@pytest.mark.contract(LIST_RULES)
async def test_rules_are_listed_by_their_order(backend: Backend, mail: Mail) -> None:
    account = await mail.account()
    late = await mail.rule(account.id, order=900_002)
    early = await mail.rule(account.id, order=900_001)

    listed = [
        r.id for r in await backend.admin.mail.list_mail_rules() if r.id in {late.id, early.id}
    ]

    assert listed == [early.id, late.id]


@pytest.mark.contract(PAGE_RULES)
async def test_rules_are_listed_page_by_page_by_their_order(backend: Backend, mail: Mail) -> None:
    account = await mail.account()
    late = await mail.rule(account.id, order=900_002)
    early = await mail.rule(account.id, order=900_001)
    reader = backend.admin.mail

    first = await reader.list_mail_rule_page(page=1, page_size=1)
    assert (len(first.rules), first.next_page) == (1, 2)
    # The two have the highest numbers, so they are on the last pages.
    ours: list[int] = []
    for page in (first.count - 1, first.count):
        found = await reader.list_mail_rule_page(page=page, page_size=1)
        ours += [rule.id for rule in found.rules]
    assert found.next_page is None
    assert ours == [early.id, late.id]
    with pytest.raises(NotFound, match="does not exist"):
        _ = await reader.list_mail_rule_page(page=9999, page_size=100)


@pytest.mark.contract(GET_RULE)
async def test_a_rule_that_does_not_exist_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound, match="No mail rule"):
        _ = await backend.admin.mail.get_mail_rule(UNKNOWN)


@pytest.mark.contract(UPDATE_RULE)
async def test_a_rule_update_changes_only_what_is_set(backend: Backend, mail: Mail) -> None:
    account = await mail.account()
    made = await mail.rule(account.id, filter_from="a@example.com", folder="Billing")

    changed = await backend.admin.mail_writes.update_mail_rule(
        made.id, MailRuleValues(enabled=False, maximum_age=7)
    )

    assert (changed.enabled, changed.maximum_age) == (False, 7)
    assert (changed.filter_from, changed.folder, changed.name) == (
        "a@example.com",
        "Billing",
        made.name,
    )
    assert await backend.admin.mail.get_mail_rule(made.id) == changed


@pytest.mark.contract(UPDATE_RULE)
async def test_a_rule_update_clears_what_is_set_to_none(backend: Backend, mail: Mail) -> None:
    account = await mail.account()
    known = await tags(backend.admin)
    correspondent = (await backend.admin.taxonomy.list_taxonomy("correspondent"))[0].id
    made = await mail.rule(
        account.id,
        filter_from="a@example.com",
        filter_subject="s",
        assign_tags=tuple(known.values())[:1],
        assign_correspondent=correspondent,
        assign_correspondent_from="selected",
    )

    changed = await backend.admin.mail_writes.update_mail_rule(
        made.id,
        MailRuleValues(
            filter_from=None, filter_subject=None, assign_tags=(), assign_correspondent=None
        ),
    )

    assert (changed.filter_from, changed.filter_subject) == (None, None)
    assert (changed.assign_tags, changed.assign_correspondent) == ((), None)


@pytest.mark.contract(UPDATE_RULE)
async def test_a_new_action_needs_its_parameter_and_leaves_an_old_one_behind(
    backend: Backend, mail: Mail
) -> None:
    account = await mail.account()
    made = await mail.rule(account.id, action="move", action_parameter="Archive")

    with pytest.raises(Validation, match="action parameter is required"):
        _ = await backend.admin.mail_writes.update_mail_rule(made.id, MailRuleValues(action="tag"))
    flagged = await backend.admin.mail_writes.update_mail_rule(
        made.id, MailRuleValues(action="flag")
    )
    assert (flagged.action, flagged.action_parameter) == ("flag", "Archive")
    cleared = await backend.admin.mail_writes.update_mail_rule(
        made.id, MailRuleValues(action="mark_read", action_parameter=None)
    )
    assert (cleared.action, cleared.action_parameter) == ("mark_read", None)


@pytest.mark.contract(UPDATE_RULE)
async def test_a_rule_can_move_to_another_account(backend: Backend, mail: Mail) -> None:
    first, second = await mail.account(), await mail.account()
    made = await mail.rule(first.id)

    changed = await backend.admin.mail_writes.update_mail_rule(
        made.id, MailRuleValues(account_id=second.id)
    )

    assert changed.account_id == second.id
    with pytest.raises(Validation, match="account"):
        _ = await backend.admin.mail_writes.update_mail_rule(
            made.id, MailRuleValues(account_id=UNKNOWN)
        )


@pytest.mark.contract(UPDATE_RULE)
async def test_updating_a_rule_that_does_not_exist_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound, match="No mail rule"):
        _ = await backend.admin.mail_writes.update_mail_rule(UNKNOWN, MailRuleValues(name="x"))


@pytest.mark.contract(DELETE_RULE)
async def test_a_deleted_rule_is_gone(backend: Backend, mail: Mail) -> None:
    account = await mail.account()
    made = await mail.rule(account.id)

    await backend.admin.mail_writes.delete_mail_rule(made.id)

    assert made.id not in {r.id for r in await backend.admin.mail.list_mail_rules()}
    with pytest.raises(NotFound, match="No mail rule"):
        await backend.admin.mail_writes.delete_mail_rule(made.id)


# --- processed mail


@pytest.mark.contract(LIST_PROCESSED)
async def test_the_processed_mail_of_a_new_rule_is_empty_and_filters_are_accepted(
    backend: Backend, mail: Mail
) -> None:
    account = await mail.account()
    made = await mail.rule(account.id)

    statuses = (None, "SUCCESS", "FAILED", "PROCESSED_WO_CONSUMPTION")
    for status in statuses:
        found = await backend.admin.mail.list_processed_mail(
            rule_id=made.id, status=status, ordering="-processed", page=1, page_size=25
        )
        assert (found.count, found.next_page, found.mails) == (0, None, ())
    orderings = ("-processed", "processed", "-received", "received")
    for ordering in orderings:
        everything = await backend.admin.mail.list_processed_mail(
            rule_id=None, status=None, ordering=ordering, page=1, page_size=25
        )
        assert everything.count >= 0


@pytest.mark.contract(GET_PROCESSED)
async def test_a_processed_mail_that_does_not_exist_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound, match="No processed mail"):
        _ = await backend.admin.mail.get_processed_mail(UNKNOWN)


async def made(
    backend: Backend, mail: Mail, *rows: Processed, owned: bool = False
) -> tuple[ProcessedMail, ...]:
    """Records of a new rule, in the order they were processed."""
    rule = await mail.rule((await mail.account()).id)
    backend.new_processed(rule.id, rows, owner=rule.owner if owned else None)
    found = await backend.admin.mail.list_processed_mail(
        rule_id=rule.id, status=None, ordering="processed", page=1, page_size=25
    )
    return found.mails


@pytest.mark.contract(LIST_PROCESSED)
@pytest.mark.contract(GET_PROCESSED)
async def test_records_are_listed_by_status_and_order_and_read_one_by_one(
    backend: Backend, mail: Mail
) -> None:
    rows = await made(
        backend,
        mail,
        ("One", "11", "SUCCESS", None),
        ("Two", "12", "FAILED", "Traceback\nboom"),
        ("Three", "13", "PROCESSED_WO_CONSUMPTION", None),
    )

    assert [(r.subject, r.uid, r.status, r.error) for r in rows] == [
        ("One", "11", "SUCCESS", None),
        ("Two", "12", "FAILED", "Traceback\nboom"),
        ("Three", "13", "PROCESSED_WO_CONSUMPTION", None),
    ]
    reader = backend.admin.mail
    statuses: tuple[ProcessedStatus, ...] = ("SUCCESS", "FAILED", "PROCESSED_WO_CONSUMPTION")
    for row, status in zip(rows, statuses, strict=True):
        assert await reader.get_processed_mail(row.id) == row
        found = await reader.list_processed_mail(
            rule_id=row.rule_id, status=status, ordering="-processed", page=1, page_size=25
        )
        assert found.mails == (row,)
    newest = await reader.list_processed_mail(
        rule_id=rows[0].rule_id, status=None, ordering="-processed", page=1, page_size=25
    )
    assert newest.mails == rows[::-1]


@pytest.mark.decision("decision-83")
@pytest.mark.contract(DELETE_PROCESSED)
async def test_records_are_deleted_and_an_id_that_is_none_is_skipped(
    backend: Backend, mail: Mail
) -> None:
    """T1: that is why only another read is believed."""
    first, second, kept = await made(
        backend, mail, *((subject, "1", "FAILED", None) for subject in "abc")
    )
    writer, reader = backend.admin.mail_writes, backend.admin.mail

    await writer.delete_processed_mail([first.id, second.id, UNKNOWN])
    await writer.delete_processed_mail([UNKNOWN])

    for gone in (first, second):
        with pytest.raises(NotFound):
            _ = await reader.get_processed_mail(gone.id)
    assert await reader.get_processed_mail(kept.id) == kept


@pytest.mark.decision("decision-83")
@pytest.mark.contract(DELETE_PROCESSED)
async def test_one_record_of_another_user_refuses_all_and_the_permission_is_needed(
    backend: Backend, mail: Mail
) -> None:
    """Paperless checks every record before it deletes any, also one older than the forbidden."""
    [free] = await made(backend, mail, ("Free", "1", "FAILED", None))
    [owned] = await made(backend, mail, ("Mine", "1", "FAILED", None), owned=True)
    deleting = backend.restricted(frozenset({"delete_processedmail"}))
    viewing = backend.restricted(frozenset({"view_processedmail"}))

    assert owned.owner is not None
    assert free.id < owned.id
    with pytest.raises(PermissionDenied, match="Nothing was deleted"):
        await deleting.mail_writes.delete_processed_mail([free.id, owned.id])
    with pytest.raises(PermissionDenied, match="Nothing was deleted"):
        await viewing.mail_writes.delete_processed_mail([free.id])
    assert await viewing.mail.get_processed_mail(free.id) == free
    await deleting.mail_writes.delete_processed_mail([free.id])

    with pytest.raises(NotFound):
        _ = await backend.admin.mail.get_processed_mail(free.id)
    assert await backend.admin.mail.get_processed_mail(owned.id) == owned


@pytest.mark.decision("decision-79")
@pytest.mark.contract(GET_ACCOUNT)
@pytest.mark.contract(UPDATE_ACCOUNT)
async def test_a_token_account_is_read_and_changed_but_its_token_is_not(
    backend: Backend, mail: Mail
) -> None:
    number = backend.new_token_account()
    mail.accounts.append(number)
    writer, reader = backend.admin.mail_writes, backend.admin.mail

    read = await reader.get_mail_account(number)
    assert (read.account_type, read.is_token, read.expiration) == ("gmail_oauth", True, EXPIRES)
    with pytest.raises(Validation, match="OAuth access token") as raised:
        _ = await writer.update_mail_account(
            number, MailAccountValues(name="x", password=SecretStr(PASSWORD))
        )
    assert await reader.get_mail_account(number) == read
    renamed = mail.name()
    changed = await writer.update_mail_account(
        number, MailAccountValues(name=renamed, username="me@example.com")
    )

    assert changed == read.model_copy(update={"name": renamed, "username": "me@example.com"})
    assert await reader.get_mail_account(number) == changed
    for shown in (repr(read), repr(changed), raised.value.describe()):
        assert TOKEN not in shown
        assert PASSWORD not in shown


@pytest.mark.contract(LIST_PROCESSED)
async def test_a_page_beyond_the_last_one_of_the_processed_mail_is_not_found(
    backend: Backend, mail: Mail
) -> None:
    account = await mail.account()
    made = await mail.rule(account.id)

    with pytest.raises(NotFound, match="Page 2 of the processed mail"):
        _ = await backend.admin.mail.list_processed_mail(
            rule_id=made.id, status=None, ordering="-processed", page=2, page_size=25
        )


@pytest.mark.contract(UPDATE_RULE)
async def test_an_empty_action_parameter_is_refused_and_none_is_taken_when_the_action_stays(
    backend: Backend, mail: Mail
) -> None:
    """Probed on 3.4.2: "" is blank (400); null is accepted as long as the request has no action."""
    account = await mail.account()
    made = await mail.rule(account.id, action="move", action_parameter="Done")

    with pytest.raises(Validation, match="action_parameter"):
        _ = await backend.admin.mail_writes.update_mail_rule(
            made.id, MailRuleValues(action_parameter="")
        )
    emptied = await backend.admin.mail_writes.update_mail_rule(
        made.id, MailRuleValues(action_parameter=None)
    )
    assert (emptied.action, emptied.action_parameter) == ("move", None)
