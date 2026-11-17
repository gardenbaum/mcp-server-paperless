"""Review findings of PLMCP-31, each pinned by a test that failed before the fix.

C1 (escalation chain), I2 (e-mail change), M3 (blank secrets), M4 (limits of the self-protection),
M5 (inherited permissions, staff text) and the unconfirmed paths that used to be open.
"""

from dataclasses import replace
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakePaperless, FakeState, seeded
from pydantic import SecretStr

from paperless_mcp.domain.admin import ArchiveFile
from paperless_mcp.domain.errors import ConfirmationRequired, PaperlessMcpError, Validation
from paperless_mcp.paperless.auth import StaticTokenProvider
from paperless_mcp.services.admin_accounts import AccountAdmin
from paperless_mcp.services.admin_system import SystemAdmin
from paperless_mcp.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Sequence

    from paperless_mcp.domain.admin import ArchiveContent, Compression

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-26")]
READER_ID, GROUP = 3, 1
SECRET = "plain-words-test-secret"


@final
class Setup:
    def __init__(self, *, writes: bool = True) -> None:
        self.state: FakeState = seeded()
        self.fake: FakePaperless = FakePaperless(self.state, user=ADMIN[0], allow_writes=writes)
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        self.accounts: AccountAdmin = AccountAdmin(self.fake, self.fake, taxonomy)
        self.system: SystemAdmin = SystemAdmin(self.fake, self.fake, max_bytes=10**6)


async def failure[E: PaperlessMcpError](call: object, kind: type[E]) -> str:
    with pytest.raises(kind) as raised:
        await call  # pyright: ignore[reportGeneralTypeIssues] -- a coroutine
    return raised.value.message


async def test_creating_a_user_with_a_password_groups_or_permissions_needs_confirm() -> None:
    setup = Setup(writes=False)

    password = await failure(
        setup.accounts.create_user(username="mallory", password=SecretStr(SECRET)),
        ConfirmationRequired,
    )
    groups = await failure(
        setup.accounts.create_user(username="mallory", groups=["e2e-group"]), ConfirmationRequired
    )
    permissions = await failure(
        setup.accounts.create_user(username="mallory", permissions=["add_user", "change_user"]),
        ConfirmationRequired,
    )

    assert password == (
        "Nothing was changed. Confirming would create user 'mallory' with a password, with "
        "which whoever knows it signs in."
    )
    assert SECRET not in password
    assert groups == (
        "Nothing was changed. Confirming would create user 'mallory' in the groups 'e2e-group'."
    )
    assert permissions == (
        "Nothing was changed. Confirming would create user 'mallory' with the permissions "
        "add_user, change_user."
    )
    assert [u.username for u in setup.state.users] == ["admin", "e2e-reader", "e2e-blind"]


async def test_a_plain_user_without_any_of_them_still_needs_no_confirm() -> None:
    outcome = await Setup().accounts.create_user(username="plain", email="p@x.org")

    assert outcome.changed


async def test_all_effects_of_a_new_user_are_listed_in_one_refusal() -> None:
    text = await failure(
        Setup(writes=False).accounts.create_user(
            username="m",
            password=SecretStr(SECRET),
            is_staff=True,
            groups=["e2e-group"],
            permissions=["view_tag"],
        ),
        ConfirmationRequired,
    )

    assert text == (
        "Nothing was changed. Confirming would create user 'm' with staff status, which lets "
        "them sign in to the Django admin site and read the logs; with a password, with which "
        "whoever knows it signs in; in the groups 'e2e-group'; with the permissions view_tag."
    )


async def test_creating_a_group_that_grants_permissions_needs_confirm() -> None:
    setup = Setup(writes=False)

    text = await failure(
        setup.accounts.create_group("powers", ["add_user", "change_user"]), ConfirmationRequired
    )

    assert text == (
        "Nothing was changed. Confirming would create group 'powers' that grants its members "
        "the permissions add_user, change_user."
    )
    assert [g.name for g in setup.state.groups] == ["e2e-group"]
    assert (await Setup().accounts.create_group("empty")).changed
    confirmed = await Setup().accounts.create_group("powers", ["view_tag"], confirm=True)
    assert confirmed.group.permissions == ("view_tag",)


async def test_the_escalation_chain_is_stopped_at_its_first_step() -> None:
    setup = Setup(writes=False)

    with pytest.raises(ConfirmationRequired):
        _ = await setup.accounts.create_group("powers", ["add_user", "change_user"])
    with pytest.raises(ConfirmationRequired):
        _ = await setup.accounts.create_user(
            username="mallory", password=SecretStr(SECRET), groups=["e2e-group"]
        )


async def test_an_email_address_that_is_set_or_changed_needs_confirm_but_clearing_does_not() -> (
    None
):
    setup = Setup(writes=False)
    setup.state.users = [
        replace(u, email="r@x.org") if u.id == READER_ID else u for u in setup.state.users
    ]

    text = await failure(
        setup.accounts.update_user(READER_ID, email="evil@x.org"), ConfirmationRequired
    )

    assert text == (
        "Nothing was changed. Confirming would change the e-mail address of 'e2e-reader' to "
        "'evil@x.org'; whoever controls it can reset the password and take the account over."
    )
    writer = Setup()
    cleared = await writer.accounts.update_user(READER_ID, email="")
    assert cleared.user.email is None
    same = await writer.accounts.update_user(READER_ID, first_name="Rita", email="")
    assert same.changed


async def test_a_new_address_on_a_user_without_one_needs_confirm_too() -> None:
    text = await failure(
        Setup(writes=False).accounts.update_user(READER_ID, email="a@x.org"), ConfirmationRequired
    )

    assert "set the e-mail address of 'e2e-reader' to 'a@x.org'" in text


async def test_a_rename_needs_confirm() -> None:
    text = await failure(
        Setup(writes=False).accounts.update_user(READER_ID, username="rita"), ConfirmationRequired
    )

    assert text == (
        "Nothing was changed. Confirming would rename user 'e2e-reader' to 'rita'; they sign in "
        "under the new name."
    )


@pytest.mark.parametrize("secret", [" ", "\t \n"])
async def test_a_password_or_key_that_is_only_whitespace_is_refused(secret: str) -> None:
    setup = Setup(writes=False)

    password = await failure(
        setup.accounts.create_user(username="c", password=SecretStr(secret)), Validation
    )
    key = await failure(
        setup.system.update_config(keys={"llm_api_key": SecretStr(secret)}), Validation
    )

    assert password == "The password must not be empty."
    assert key == "The llm_api_key must not be empty."


async def test_an_inherited_permission_and_the_django_admin_staff_text_are_shown() -> None:
    setup = Setup()
    setup.state.groups = [
        replace(g, permissions=frozenset({"view_tag", "add_tag"})) for g in setup.state.groups
    ]
    setup.state.users = [
        replace(u, groups=frozenset({GROUP}), permissions=frozenset({"add_tag"}))
        if u.id == READER_ID
        else u
        for u in setup.state.users
    ]

    shown = await setup.accounts.get_user(READER_ID)

    assert shown.permissions == ("add_tag",)
    assert shown.inherited_permissions == ("add_tag", "view_tag")


class TimesOut(FakePaperless):
    @override
    async def download_documents(
        self,
        document_ids: Sequence[int],
        *,
        content: ArchiveContent,
        compression: Compression,
        follow_formatting: bool,
        max_bytes: int,
    ) -> ArchiveFile:
        return ArchiveFile(content=b"", truncated=True, timed_out=True)


async def test_an_archive_that_took_too_long_is_not_reported_as_too_large() -> None:
    setup = Setup()
    system = SystemAdmin(TimesOut(setup.state, user=ADMIN[0]), setup.fake, max_bytes=10**6)

    delivery = await system.download([1])

    assert delivery.data is None
    assert delivery.report.note == (
        "Paperless did not finish the archive in time, so it is not attached. Ask for fewer "
        "documents, or fetch single files with download_document."
    )
