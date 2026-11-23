"""Where something goes is shown to the user in full or not confirmed at all (decision-44).

A host or address that is cut loses the part that decides where it leads. What makes things safer
(switching off, deleting, pointing elsewhere) works whatever the old destination looks like.
"""

from typing import TYPE_CHECKING

import pytest
from fakes.accounts import ADMIN
from fakes.mail import FakeMailAccount, FakeMailRule
from fakes.paperless import FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.errors import ConfirmationRequired, Validation
from mcp_server_paperless.domain.mail import MailRuleSettings
from mcp_server_paperless.domain.workflows import (
    ActionInput,
    Workflow,
    WorkflowAction,
    WorkflowEdit,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.admin_accounts import AccountAdmin
from mcp_server_paperless.services.admin_system import SystemAdmin
from mcp_server_paperless.services.mail import MailService
from mcp_server_paperless.services.sharing import SharingService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.workflows import MAX_DESTINATIONS, WorkflowService

if TYPE_CHECKING:
    from collections.abc import Awaitable

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-44")]

# 253 characters, the most a host name has; what it means is at its end.
GOOD = "imap.bank.example." + "x" * 222 + ".good.example"
EVIL = "imap.bank.example." + "x" * 222 + ".evil.example"
LONG = "imap.bank.example." + "x" * 250 + ".evil.example"
HIDDEN = "mail\u200b.example"
HIDING = "cannot be shown to the user in full"


class Setup:
    def __init__(self, *, writes: bool = False) -> None:
        self.state: FakeState = seeded()
        self.state.email_enabled = True
        self.fake: FakePaperless = FakePaperless(self.state, user=ADMIN[0], allow_writes=writes)
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=0.0)
        self.mail: MailService = MailService(self.fake, self.fake, taxonomy, self.fake)
        self.sharing: SharingService = SharingService(
            self.fake, self.fake, self.fake, web_url="https://p.example"
        )
        self.workflows: WorkflowService = WorkflowService(self.fake, self.fake, taxonomy)
        self.accounts: AccountAdmin = AccountAdmin(self.fake, self.fake, taxonomy)
        self.system: SystemAdmin = SystemAdmin(self.fake, self.fake, max_bytes=10)

    def account(self, host: str, security: str = "ssl") -> None:
        self.state.mail_accounts = [
            FakeMailAccount(
                id=1,
                name="Mail",
                imap_server=host,
                imap_port=993,
                username="u",
                password="p",
                imap_security=security,  # pyright: ignore[reportArgumentType] -- a literal
                owner=2,
            )
        ]
        self.state.mail_rules = [FakeMailRule(id=1, name="Rule", account=1, owner=2)]

    def workflow(self, *actions: WorkflowAction, enabled: bool = False) -> None:
        self.state.workflows = [
            Workflow(id=1, name="W", order=0, enabled=enabled, triggers=(), actions=actions)
        ]

    def email_action(self, to: str) -> WorkflowAction:
        return WorkflowAction(id=2, type=3, settings={"email": {"to": to, "subject": "s"}})

    def webhook_action(self, url: str) -> WorkflowAction:
        return WorkflowAction(id=3, type=4, settings={"webhook": {"url": url}})

    async def email(self, *recipients: str, confirm: bool = False) -> object:
        return await self.sharing.email(
            [1],
            recipients=recipients,
            subject="s",
            message="m",
            use_archive_version=True,
            confirm=confirm,
        )


async def asked(call: Awaitable[object]) -> str:
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await call
    return raised.value.message


async def refused(call: Awaitable[object]) -> str:
    """The message of a refusal that no confirmation gets past."""
    with pytest.raises(Validation) as raised:
        _ = await call
    assert not isinstance(raised.value, ConfirmationRequired)
    assert "Nothing was changed" in raised.value.message
    return raised.value.message


# --- shown in full ------------------------------------------------------------------------------


@pytest.mark.parametrize("host", [GOOD, EVIL])
async def test_hosts_that_differ_only_at_their_end_are_told_apart(host: str) -> None:
    assert len(host) == 253
    setup = Setup()
    setup.account("old.example")
    assert f"'{host}'" in await asked(setup.mail.update_account(1, imap_server=host))
    assert f"to '{host}' port" in await asked(
        setup.mail.update_account(1, imap_server=host, imap_security="none")
    )
    setup.account(host)
    assert f"'{host}'" in await asked(setup.mail.process_account(1, confirm=False))
    address = f"a@{host[2:]}"
    assert f"'{address}'" in await asked(setup.email(address))
    setup.workflow(
        setup.email_action(f"b@x.org, {address}"), setup.webhook_action(f"https://{host}/p")
    )
    message = await asked(setup.workflows.update(1, WorkflowEdit(enabled=True), confirm=False))
    assert f"'{address}'" in message
    assert f"'{host}'" in message
    assert f"'{address}'" in await asked(setup.accounts.update_user(3, email=address))
    url = f"https://{host}/v1"
    assert f"'{url[:254]}'" in await asked(setup.system.update_config({"llm_endpoint": url[:254]}))


async def test_every_recipient_is_named() -> None:
    setup = Setup()
    addresses = [f"user{n}@x.org" for n in range(MAX_DESTINATIONS)]
    setup.workflow(setup.email_action(",".join(addresses)))

    message = await asked(setup.workflows.update(1, WorkflowEdit(enabled=True), confirm=False))
    sharing = await asked(setup.email(*addresses))

    assert all(f"'{address}'" in message and f"'{address}'" in sharing for address in addresses)


async def test_a_service_address_is_named_behind_any_number_of_settings() -> None:
    setup = Setup()
    names = ("pages", "image_dpi", "barcode_dpi", "barcode_max_pages", "llm_context_size")
    settings = {**dict.fromkeys(names, 5), "llm_request_timeout": 5, "llm_endpoint": "https://e.x"}

    message = await asked(setup.system.update_config(settings))

    assert "'https://e.x'" in message
    assert "and 1 more settings" in message


# --- refused, with and without confirm ----------------------------------------------------------


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("host", [LONG, HIDDEN])
async def test_a_destination_that_cannot_be_shown_is_refused(host: str, *, confirm: bool) -> None:
    setup = Setup()
    setup.account("old.example")
    calls: list[Awaitable[object]] = [
        setup.mail.update_account(1, imap_server=host, confirm=confirm),
        setup.email(f"a@{host}", confirm=confirm),
        setup.accounts.update_user(3, email=f"a@{host}", confirm=confirm),
        setup.system.update_config({"llm_endpoint": f"https://{host}"}, confirm=confirm),
        setup.mail.create_rule(
            MailRuleSettings(name="N", account_id=1, action="move", action_parameter=host),
            confirm=confirm,
        ),
        setup.mail.update_rule(
            1,
            MailRuleSettings(action="move", action_parameter=host),
            clear=(),
            confirm=confirm,
        ),
    ]
    for call in calls:
        assert HIDING in await refused(call)
    setup.account(host)
    assert HIDING in await refused(setup.mail.process_account(1, confirm=confirm))
    for action in (setup.email_action(f"a@{host}"), setup.webhook_action(f"https://{host}/")):
        setup.workflow(action)
        assert HIDING in await refused(
            setup.workflows.update(1, WorkflowEdit(enabled=True), confirm=confirm)
        )


@pytest.mark.parametrize("confirm", [False, True])
async def test_more_destinations_than_a_dialog_can_list_are_refused(*, confirm: bool) -> None:
    setup = Setup()
    many = ",".join(f"user{n}@x.org" for n in range(MAX_DESTINATIONS))
    setup.workflow(setup.email_action(many), setup.webhook_action("https://one.example/"))

    message = await refused(setup.workflows.update(1, WorkflowEdit(enabled=True), confirm=confirm))

    assert f"{MAX_DESTINATIONS + 1} destinations" in message
    assert str(MAX_DESTINATIONS) in message


async def test_the_refusal_shows_both_ends_of_what_it_cannot_show() -> None:
    setup = Setup()
    setup.account("old.example")

    message = await refused(setup.mail.update_account(1, imap_server=LONG))

    assert "'imap.bank.example" in message
    assert "evil.example'" in message
    assert len(message) < 400


# --- making it safer always works ---------------------------------------------------------------


async def test_a_bad_workflow_can_be_switched_off_renamed_repaired_and_deleted() -> None:
    setup = Setup(writes=True)
    bad = setup.webhook_action(f"https://{LONG}/")
    setup.workflow(bad, enabled=True)
    setup.state.workflow_object_id[0] = 9

    _ = await setup.workflows.update(1, WorkflowEdit(name="Renamed"), confirm=False)
    trash = ActionInput.model_validate({"type": "move_to_trash"})
    _ = await setup.workflows.update(1, WorkflowEdit(actions=(trash,)), confirm=True)
    setup.workflow(bad, enabled=True)
    off = await setup.workflows.update(1, WorkflowEdit(enabled=False), confirm=False)
    assert off.enabled is False
    _ = await setup.workflows.delete(1, confirm=True)

    assert setup.state.workflows == []


async def test_a_bad_mail_account_can_be_moved_encrypted_and_deleted() -> None:
    setup = Setup()
    setup.account(LONG)
    moving = await asked(setup.mail.update_account(1, imap_server="good.example"))
    assert "'good.example'" in moving
    assert "evil.example'" in moving  # the old one with its end
    setup.account(LONG, security="none")
    encrypting = await asked(setup.mail.update_account(1, imap_security="ssl"))
    assert "evil.example'" in encrypting
    assert "'Mail'" in await asked(setup.mail.delete_account(1, confirm=False))


async def test_a_bad_mail_rule_can_be_switched_off_and_deleted() -> None:
    setup = Setup(writes=True)
    setup.account("old.example")
    setup.state.mail_rules[0].action = "move"
    setup.state.mail_rules[0].action_parameter = LONG

    _ = await setup.mail.update_rule(1, MailRuleSettings(enabled=False), clear=(), confirm=False)
    assert "'Rule'" in await asked(setup.mail.delete_rule(1, confirm=False))

    assert setup.state.mail_rules[0].enabled is False


async def test_a_bad_service_address_can_be_replaced_and_reset() -> None:
    setup = Setup()
    setup.state.config = {"llm_endpoint": f"https://{LONG}"}

    replaced = await asked(setup.system.update_config({"llm_endpoint": "https://good.example"}))
    reset = await asked(setup.system.update_config({"llm_endpoint": None}))

    assert "'https://good.example'" in replaced
    assert "evil.example'" in replaced
    assert "evil.example'" in reset


# --- a read for the scope breaks nothing --------------------------------------------------------


async def test_a_rule_whose_account_the_caller_cannot_see_is_still_handed_over() -> None:
    setup = Setup()
    setup.account("old.example")
    setup.state.mail_rules[0].owner = 3
    setup.state.mail_accounts.clear()

    message = await asked(
        setup.mail.update_rule(1, MailRuleSettings(owner=2), clear=(), confirm=False)
    )

    assert "hand the mail rule 'Rule' (id 1) over" in message
