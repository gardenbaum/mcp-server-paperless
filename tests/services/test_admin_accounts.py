"""AccountAdmin: users and groups, the confirmations, the self-protection and the secrets.

The fakes of the "nothing may be written" cases are created without `allow_writes`, so any write
on an unconfirmed or invalid path fails. Seeded: users admin 2 (superuser), reader 3, blind 4;
group 1 `e2e-group`.
"""

from dataclasses import replace
from typing import final, override

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakePaperless, FakeState, FakeUser, seeded
from pydantic import SecretStr

from mcp_server_paperless.domain.admin import NotText
from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    Ambiguous,
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.admin_accounts import CANNOT_TELL, AccountAdmin
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-15"),
    pytest.mark.decision("decision-18"),
]
ADMIN_ID, READER_ID, GROUP = 2, 3, 1
PASSWORD = "plain-words-test-password"


@final
class Setup:
    def __init__(
        self, user: str = ADMIN[0], *, writes: bool = True, grant: frozenset[str] | None = None
    ) -> None:
        self.state: FakeState = seeded()
        self.state.users = [
            replace(u, permissions=u.permissions | (grant or frozenset()))
            if u.username == user
            else u
            for u in self.state.users
        ]
        self.fake: FakePaperless = FakePaperless(self.state, user=user, allow_writes=writes)
        self.taxonomy: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.service: AccountAdmin = AccountAdmin(self.fake, self.fake, self.taxonomy)

    def user(self, number: int) -> FakeUser:
        [found] = [u for u in self.state.users if u.id == number]
        return found

    def put(self, number: int, **changes: object) -> None:
        self.state.users = [
            replace(u, **changes) if u.id == number else u for u in self.state.users
        ]


async def refusal(call: object) -> str:
    """The message of the ConfirmationRequired a call raises."""
    with pytest.raises(ConfirmationRequired) as raised:
        await call  # pyright: ignore[reportGeneralTypeIssues] -- test data built from dicts
    assert raised.value.hint == CONFIRM_HINT
    assert raised.value.message.startswith("Nothing was changed. Confirming would ")
    return raised.value.message


# --- reading


async def test_a_user_is_read_by_username_or_id_with_group_names() -> None:
    setup = Setup()
    setup.put(READER_ID, groups=frozenset({GROUP}), email="r@example.com")

    by_name = await setup.service.get_user("e2e-reader")
    by_id = await setup.service.get_user(READER_ID)

    assert by_name == by_id
    assert (by_name.username, by_name.email, by_name.is_superuser) == (
        "e2e-reader",
        "r@example.com",
        False,
    )
    assert [(g.id, g.name) for g in by_name.groups] == [(GROUP, "e2e-group")]
    assert "password" not in by_name.model_dump()


async def test_an_unknown_or_ambiguous_user_is_an_error() -> None:
    setup = Setup()

    with pytest.raises(NotFound):
        _ = await setup.service.get_user("nobody")
    with pytest.raises(NotFound):
        _ = await setup.service.get_user(999)


async def test_a_group_is_read_by_name_or_id() -> None:
    setup = Setup()
    setup.state.groups = [
        replace(g, permissions=frozenset({"view_tag"})) for g in setup.state.groups
    ]

    found = await setup.service.get_group("e2e-group")

    assert found == await setup.service.get_group(GROUP)
    assert (found.id, found.name, found.permissions) == (GROUP, "e2e-group", ("view_tag",))


async def test_a_user_who_may_not_list_names_can_still_pass_ids() -> None:
    setup = Setup("e2e-reader", grant=frozenset({"view_user"}))

    found = await setup.service.get_user(ADMIN_ID)

    assert found.username == "admin"
    assert [g.name for g in found.groups] == []


async def test_without_the_permission_a_user_is_denied_with_a_hint() -> None:
    setup = Setup("e2e-blind")

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.service.get_user(ADMIN_ID)

    assert raised.value.hint


# --- creating users


async def test_a_plain_user_is_created_without_confirm_and_the_password_is_never_shown() -> None:
    setup = Setup()

    outcome = await setup.service.create_user(
        username="carol",
        password=SecretStr(PASSWORD),
        email="c@example.com",
        groups=["e2e-group"],
        permissions=["view_document"],
        confirm=True,
    )

    assert outcome.changed
    assert (outcome.user.username, outcome.user.email) == ("carol", "c@example.com")
    assert [g.name for g in outcome.user.groups] == ["e2e-group"]
    assert outcome.user.permissions == ("view_document",)
    assert "carol" in outcome.message
    assert PASSWORD not in outcome.model_dump_json() + outcome.message
    stored = setup.user(outcome.user.id)
    assert stored.has_password
    assert stored.groups == frozenset({GROUP})


@pytest.mark.parametrize(
    ("flags", "words"),
    [
        ({"is_superuser": True}, ["superuser", "sees and changes everything"]),
        ({"is_staff": True}, ["staff", "read the logs"]),
    ],
)
async def test_creating_a_superuser_or_staff_user_needs_confirm(
    flags: dict[str, bool], words: list[str]
) -> None:
    setup = Setup(writes=False)

    message = await refusal(setup.service.create_user(username="carol", **flags))  # pyright: ignore[reportArgumentType] -- flags of the parametrisation

    for word in words:
        assert word in message
    assert "'carol'" in message
    assert [u.username for u in setup.state.users] == ["admin", "e2e-reader", "e2e-blind"]


async def test_a_confirmed_superuser_is_created() -> None:
    setup = Setup()

    outcome = await setup.service.create_user(username="carol", is_superuser=True, confirm=True)

    assert outcome.user.is_superuser


@pytest.mark.parametrize(
    ("password", "reason"), [("", "must not be empty"), ("***", "cannot consist only of asterisks")]
)
async def test_a_blank_or_asterisk_password_is_refused_without_echoing_it(
    password: str, reason: str
) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.service.create_user(username="carol", password=SecretStr(password))

    assert reason in raised.value.message
    assert "***" not in raised.value.describe()


async def test_a_password_that_is_not_text_is_refused() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.service.create_user(username="carol", password=NotText())

    assert raised.value.message == "The password must be text."


async def test_unknown_groups_are_not_found_before_anything_is_written() -> None:
    setup = Setup(writes=False)

    with pytest.raises(NotFound):
        _ = await setup.service.create_user(username="carol", groups=["nope"])


async def test_a_taken_username_is_a_validation_error() -> None:
    setup = Setup()

    with pytest.raises(Validation):
        _ = await setup.service.create_user(username="admin")


# --- updating users


async def test_cosmetic_changes_need_no_confirm_and_a_repeat_changes_nothing() -> None:
    setup = Setup()

    first = await setup.service.update_user("e2e-reader", first_name="Rita", last_name="Reed")
    again = await setup.service.update_user("e2e-reader", first_name="Rita", last_name="Reed")

    assert first.changed
    assert (first.user.first_name, first.user.last_name) == ("Rita", "Reed")
    assert not again.changed
    assert "already" in again.message


async def test_an_empty_email_removes_the_address() -> None:
    setup = Setup()
    setup.put(READER_ID, email="r@x.org")

    outcome = await setup.service.update_user(READER_ID, email="")

    assert outcome.user.email is None


async def test_nothing_given_is_a_validation_error() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.service.update_user(READER_ID)

    assert raised.value.message.startswith("Nothing to change")


@pytest.mark.parametrize(
    ("change", "effect"),
    [
        ({"is_superuser": True}, "make 'e2e-reader' a superuser"),
        ({"is_staff": True}, "give 'e2e-reader' staff status"),
        ({"is_active": False}, "deactivate 'e2e-reader'"),
        ({"password": SecretStr(PASSWORD)}, "set a new password for 'e2e-reader'"),
        ({"groups": ["e2e-group"]}, "add 'e2e-reader' to the groups 'e2e-group'"),
        ({"permissions": ["add_tag"]}, "grant 'e2e-reader' the permissions 'add_tag'"),
    ],
)
async def test_every_change_of_access_needs_confirm_and_names_its_effect(
    change: dict[str, object], effect: str
) -> None:
    setup = Setup(writes=False)

    message = await refusal(setup.service.update_user(READER_ID, **change))  # pyright: ignore[reportArgumentType] -- test data built from dicts

    assert effect in message
    assert PASSWORD not in message
    assert setup.user(READER_ID) == setup.state.users[1]


async def test_reactivating_a_user_needs_confirm() -> None:
    setup = Setup(writes=False)
    setup.put(READER_ID, is_active=False)

    message = await refusal(setup.service.update_user(READER_ID, is_active=True))

    assert "reactivate 'e2e-reader', who can then sign in again" in message
    assert not setup.user(READER_ID).is_active


async def test_taking_away_access_names_what_goes() -> None:
    setup = Setup(writes=False)
    setup.put(
        READER_ID,
        is_superuser=True,
        is_staff=True,
        groups=frozenset({GROUP}),
        permissions=frozenset({"view_tag", "view_document"}),
    )

    message = await refusal(
        setup.service.update_user(
            READER_ID, is_superuser=False, is_staff=False, groups=[], permissions=["view_tag"]
        )
    )

    assert "remove superuser status from 'e2e-reader'" in message
    assert "remove staff status from 'e2e-reader'" in message
    assert "remove 'e2e-reader' from the groups 'e2e-group'" in message
    assert "take the permissions 'view_document' away from 'e2e-reader'" in message


async def test_a_confirmed_change_is_written_and_unchanged_flags_need_no_confirm() -> None:
    setup = Setup()

    outcome = await setup.service.update_user(
        READER_ID, is_superuser=False, is_active=True, groups=["e2e-group"], confirm=True
    )

    assert outcome.user.groups[0].name == "e2e-group"
    again = await setup.service.update_user(READER_ID, is_superuser=False, is_active=True)
    assert not again.changed


async def test_the_server_user_cannot_lose_its_own_access() -> None:
    setup = Setup(writes=False)

    for change in ({"is_superuser": False}, {"is_active": False}):
        with pytest.raises(Validation) as raised:
            _ = await setup.service.update_user(ADMIN_ID, confirm=True, **change)  # pyright: ignore[reportArgumentType] -- flags of the loop
        assert "account this server signs in with" in raised.value.message
        assert raised.value.hint


async def test_the_server_user_may_change_other_things_about_itself() -> None:
    setup = Setup()

    outcome = await setup.service.update_user(ADMIN_ID, first_name="Ada")

    assert outcome.user.first_name == "Ada"


async def test_a_clerk_cannot_make_superusers_and_hears_why() -> None:
    setup = Setup("e2e-reader", grant=frozenset({"view_user", "change_user", "view_group"}))

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.service.update_user("e2e-blind", is_superuser=True, confirm=True)

    assert "superuser" in (raised.value.hint or "")


# --- the second factor


@pytest.mark.decision("decision-103")
async def test_a_second_factor_is_removed_only_with_a_yes_that_names_the_user() -> None:
    setup = Setup()
    setup.put(READER_ID, mfa=True)

    message = await refusal(setup.service.update_user("e2e-reader", remove_second_factor=True))
    assert setup.user(READER_ID).mfa is True
    done = await setup.service.update_user("e2e-reader", remove_second_factor=True, confirm=True)

    assert "remove the second factor of 'e2e-reader'" in message
    assert "the password alone" in message
    assert (done.changed, done.user.mfa_enabled, setup.user(READER_ID).mfa) == (True, False, False)
    assert "Removed the second factor of 'e2e-reader'" in done.message


@pytest.mark.decision("decision-103")
async def test_the_question_says_that_the_user_is_a_superuser() -> None:
    setup = Setup(writes=False)
    setup.put(READER_ID, mfa=True, is_superuser=True)

    message = await refusal(setup.service.update_user("e2e-reader", remove_second_factor=True))

    assert "the password alone; the user is a superuser" in message


@pytest.mark.decision("decision-103")
async def test_without_a_second_factor_that_is_said_and_nothing_is_sent() -> None:
    done = await Setup(writes=False).service.update_user("e2e-reader", remove_second_factor=True)

    assert done.changed is False
    assert "has no second factor" in done.message


@pytest.mark.decision("decision-103")
async def test_the_second_factor_of_the_server_account_is_never_removed() -> None:
    setup = Setup(writes=False)
    setup.put(ADMIN_ID, mfa=True)

    with pytest.raises(Validation, match="account this server signs in with"):
        _ = await setup.service.update_user(ADMIN_ID, remove_second_factor=True, confirm=True)


@pytest.mark.decision("decision-103")
async def test_where_the_account_is_not_known_no_second_factor_is_removed() -> None:
    @final
    class Nameless(FakePaperless):
        @override
        async def get_current_user_id(self) -> int:
            message = "down"
            raise PaperlessMcpError(message)

    setup = Setup()
    setup.put(READER_ID, mfa=True)
    fake = Nameless(setup.state, user=ADMIN[0])
    service = AccountAdmin(fake, fake, setup.taxonomy)

    with pytest.raises(Validation) as raised:
        _ = await service.update_user("e2e-reader", remove_second_factor=True, confirm=True)

    assert raised.value.message == CANNOT_TELL


@pytest.mark.decision("decision-103")
async def test_the_second_factor_is_removed_in_a_call_of_its_own() -> None:
    setup = Setup(writes=False)
    setup.put(READER_ID, mfa=True)

    others: list[dict[str, object]] = [
        {"first_name": "Rita"},
        {"password": SecretStr(PASSWORD)},
        {"groups": []},
    ]
    for other in others:
        with pytest.raises(Validation, match="in a call of its own"):
            _ = await setup.service.update_user(
                "e2e-reader",
                remove_second_factor=True,
                confirm=True,
                **other,  # pyright: ignore[reportArgumentType] -- test data built from dicts
            )


@pytest.mark.decision("decision-103")
async def test_a_second_factor_that_is_gone_meanwhile_is_no_error() -> None:
    class Gone(FakePaperless):
        @override
        async def remove_second_factor(self, user_id: int) -> None:
            self.state.users[:] = [replace(u, mfa=False) for u in self.state.users]
            message = "gone"
            raise NotFound(message)

    setup = Setup()
    setup.put(READER_ID, mfa=True)
    fake = Gone(setup.state, user=ADMIN[0], allow_writes=True)
    service = AccountAdmin(fake, fake, setup.taxonomy)

    done = await service.update_user("e2e-reader", remove_second_factor=True, confirm=True)

    assert (done.changed, done.user.mfa_enabled) == (False, False)


@pytest.mark.decision("decision-103")
async def test_a_second_factor_that_is_still_there_is_an_error() -> None:
    class Kept(FakePaperless):
        @override
        async def remove_second_factor(self, user_id: int) -> None:
            return None

    setup = Setup()
    setup.put(READER_ID, mfa=True)
    fake = Kept(setup.state, user=ADMIN[0], allow_writes=True)
    service = AccountAdmin(fake, fake, setup.taxonomy)

    with pytest.raises(PaperlessMcpError, match="still has a second factor"):
        _ = await service.update_user("e2e-reader", remove_second_factor=True, confirm=True)


# --- deleting users


async def test_deleting_a_user_needs_confirm_and_says_what_follows() -> None:
    setup = Setup(writes=False)

    message = await refusal(setup.service.delete_user("e2e-reader"))

    assert "delete user 'e2e-reader' for good" in message
    assert "lose their owner" in message
    assert [u.username for u in setup.state.users] == ["admin", "e2e-reader", "e2e-blind"]


async def test_deleting_a_superuser_says_so() -> None:
    setup = Setup(writes=False)
    setup.put(READER_ID, is_superuser=True)

    assert "a superuser" in await refusal(setup.service.delete_user(READER_ID))


async def test_a_confirmed_delete_removes_the_user_and_forgets_it() -> None:
    setup = Setup()
    before = await setup.service.get_user(READER_ID)

    outcome = await setup.service.delete_user(READER_ID, confirm=True)

    assert (outcome.id, outcome.name) == (READER_ID, before.username)
    assert "Deleted" in outcome.message
    with pytest.raises(NotFound):
        _ = await setup.service.get_user(READER_ID)


async def test_the_server_user_cannot_be_deleted() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.service.delete_user(ADMIN_ID, confirm=True)

    assert "account this server signs in with" in raised.value.message


# --- groups


async def test_a_group_is_created_without_confirm() -> None:
    setup = Setup()

    outcome = await setup.service.create_group("accounting", ["view_document"], confirm=True)

    assert outcome.changed
    assert (outcome.group.name, outcome.group.permissions) == ("accounting", ("view_document",))


async def test_a_group_without_permissions_is_created() -> None:
    setup = Setup()

    outcome = await setup.service.create_group("empty")

    assert outcome.group.permissions == ()


async def test_a_rename_needs_no_confirm_and_a_repeat_changes_nothing() -> None:
    setup = Setup()

    first = await setup.service.update_group("e2e-group", name="renamed")
    again = await setup.service.update_group(GROUP, name="renamed")

    assert first.changed
    assert first.group.name == "renamed"
    assert not again.changed


async def test_changing_what_a_group_grants_needs_confirm_and_names_it() -> None:
    setup = Setup(writes=False)
    setup.state.groups = [
        replace(g, permissions=frozenset({"view_tag", "view_document"})) for g in setup.state.groups
    ]

    message = await refusal(setup.service.update_group(GROUP, permissions=["view_tag", "add_tag"]))

    assert "give the members of 'e2e-group' the permissions 'add_tag'" in message
    assert "take the permissions 'view_document' away from them" in message


async def test_a_confirmed_permission_change_is_written() -> None:
    setup = Setup()

    outcome = await setup.service.update_group(GROUP, permissions=["view_tag"], confirm=True)

    assert outcome.group.permissions == ("view_tag",)


async def test_nothing_given_for_a_group_is_a_validation_error() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation):
        _ = await setup.service.update_group(GROUP)


async def test_deleting_a_group_needs_confirm_and_says_what_follows() -> None:
    setup = Setup(writes=False)

    message = await refusal(setup.service.delete_group("e2e-group"))

    assert "delete group 'e2e-group' for good" in message
    assert "its members lose" in message
    assert [g.name for g in setup.state.groups] == ["e2e-group"]


async def test_a_confirmed_group_delete_removes_it_from_its_members() -> None:
    setup = Setup()
    setup.put(READER_ID, groups=frozenset({GROUP}))

    outcome = await setup.service.delete_group(GROUP, confirm=True)

    assert (outcome.id, outcome.name) == (GROUP, "e2e-group")
    assert setup.user(READER_ID).groups == frozenset()


async def test_an_ambiguous_group_name_lists_the_candidates() -> None:
    setup = Setup()
    setup.state.groups = [
        *setup.state.groups,
        replace(setup.state.groups[0], id=7, name="E2E-Group"),
    ]
    # same name apart from case: exact match wins, so it is not ambiguous
    found = await setup.service.get_group("e2e-group")
    assert found.id == GROUP
    setup.state.groups = [
        *setup.state.groups,
        replace(setup.state.groups[0], id=8, name="e2e-GROUP"),
    ]
    with pytest.raises(Ambiguous):
        _ = await setup.service.get_group("E2E-group")


async def test_granting_on_top_of_what_a_user_has_names_only_the_new_permissions() -> None:
    setup = Setup(writes=False)
    setup.put(READER_ID, permissions=frozenset({"view_tag"}))

    message = await refusal(
        setup.service.update_user(READER_ID, permissions=["view_tag", "add_tag"])
    )

    assert "grant 'e2e-reader' the permissions 'add_tag'" in message
    assert "away" not in message


async def test_granting_on_top_of_what_a_group_has_names_only_the_new_permissions() -> None:
    setup = Setup(writes=False)
    setup.state.groups = [
        replace(g, permissions=frozenset({"view_tag"})) for g in setup.state.groups
    ]

    message = await refusal(setup.service.update_group(GROUP, permissions=["view_tag", "add_tag"]))

    assert "the permissions 'add_tag'" in message
    assert "away" not in message
