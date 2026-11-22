"""The exact texts and the plumbing of the admin services, pinned one by one (mutation tests).

Messages, hints and confirmations are part of the contract (the surface and the decision say
so), so each is compared whole. Seeded: users admin 2 (superuser), reader 3, blind 4; group 1.
"""

import json
from dataclasses import replace
from typing import TYPE_CHECKING, final, override

import pytest
from fakes.accounts import ADMIN
from fakes.paperless import FakePaperless, FakeState, FakeTask, seeded
from pydantic import SecretStr

from mcp_server_paperless.domain.admin import GroupValues, NotText, UserValues
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    PaperlessMcpError,
    UpstreamTimeout,
    Validation,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.admin_accounts import AccountAdmin
from mcp_server_paperless.services.admin_system import (
    MAX_DOCUMENTS,
    MAX_LOG_CHARS,
    MAX_LOG_LINES,
    SystemAdmin,
)
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.admin import (
        ArchiveContent,
        ArchiveFile,
        Compression,
        UserRecord,
    )

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-26")]
READER_ID, GROUP = 3, 1
SELF = (
    "'admin' is the account this server signs in with; {what} would lock this server out of "
    "Paperless. Hint: Ask another administrator to do this in the Paperless web UI."
)


@final
class Setup:
    def __init__(self, *, writes: bool = True) -> None:
        self.state: FakeState = seeded()
        self.fake: FakePaperless = FakePaperless(self.state, user=ADMIN[0], allow_writes=writes)
        self.taxonomy: TaxonomyResolver = TaxonomyResolver(
            self.fake, StaticTokenProvider("t"), ttl=3600.0
        )
        self.accounts: AccountAdmin = AccountAdmin(self.fake, self.fake, self.taxonomy)
        self.system: SystemAdmin = SystemAdmin(self.fake, self.fake, max_bytes=10**6)


async def failure[E: PaperlessMcpError](call: object, kind: type[E]) -> str:
    with pytest.raises(kind) as raised:
        await call  # pyright: ignore[reportGeneralTypeIssues] -- a coroutine
    return raised.value.describe()


# --- accounts: secrets and flags


async def test_the_password_checks_say_exactly_what_is_wrong() -> None:
    setup = Setup(writes=False)

    assert await failure(
        setup.accounts.create_user(username="c", password=NotText()), Validation
    ) == ("The password must be text. Hint: Pass the password as a string.")
    assert await failure(
        setup.accounts.create_user(username="c", password=SecretStr("")), Validation
    ) == ("The password must not be empty. Hint: Pass a password, or leave it out.")
    assert await failure(
        setup.accounts.create_user(username="c", password=SecretStr("**")), Validation
    ) == (
        "The password cannot consist only of asterisks: Paperless takes that to mean 'keep the "
        "saved one'. Hint: Leave the password out to keep the saved one."
    )


async def test_superuser_and_staff_are_named_in_one_refusal() -> None:
    setup = Setup(writes=False)

    both = await failure(
        setup.accounts.create_user(username="c", is_superuser=True, is_staff=True),
        ConfirmationRequired,
    )
    staff = await failure(
        setup.accounts.create_user(username="c", is_staff=True), ConfirmationRequired
    )

    expected = (
        "Nothing was changed. Confirming would create user 'c' as a superuser, who sees and "
        "changes everything whatever is granted; with staff status, which lets them sign in "
        "to the Django admin site and read the logs. Hint:"
    )
    assert both.startswith(expected)
    expected_staff = (
        "Nothing was changed. Confirming would create user 'c' with staff status, which lets them "
        "sign in to the Django admin site and read the logs. Hint:"
    )
    assert staff.startswith(expected_staff)


async def test_a_user_is_created_with_every_field_and_the_cache_forgets_the_old_list() -> None:
    setup = Setup()
    _ = await setup.taxonomy.items("user")

    outcome = await setup.accounts.create_user(
        username="carol",
        email="c@x.org",
        first_name="Carol",
        last_name="Archer",
        is_active=False,
        is_staff=True,
        is_superuser=True,
        groups=[GROUP],
        permissions=["view_tag"],
        confirm=True,
    )

    user = outcome.user
    assert (user.first_name, user.last_name, user.is_active) == ("Carol", "Archer", False)
    assert (user.is_staff, user.is_superuser, user.permissions) == (True, True, ("view_tag",))
    assert outcome.message == f"Created user 'carol' (id {user.id})."
    assert (await setup.taxonomy.resolve("user", "carol")).id == user.id


async def test_an_update_writes_every_field_it_is_given() -> None:
    setup = Setup()
    _ = await setup.taxonomy.items("user")

    outcome = await setup.accounts.update_user(
        "e2e-reader",
        username="rita",
        last_name="Reader",
        permissions=["add_tag"],
        password=SecretStr("Pw-123456"),
        confirm=True,
    )

    assert outcome.message == "Updated user 'rita': username, last_name, permissions, password."
    assert (outcome.user.username, outcome.user.last_name) == ("rita", "Reader")
    assert outcome.user.permissions == ("add_tag",)
    assert next(u for u in setup.state.users if u.id == READER_ID).has_password
    assert (await setup.taxonomy.resolve("user", "rita")).id == READER_ID


async def test_the_refusal_of_several_changes_joins_them_with_semicolons() -> None:
    setup = Setup(writes=False)

    text = await failure(
        setup.accounts.update_user(
            READER_ID, is_staff=True, permissions=["add_tag", "add_note"], is_superuser=True
        ),
        ConfirmationRequired,
    )

    expected = (
        "Nothing was changed. Confirming would make 'e2e-reader' a superuser, who sees and "
        "changes everything whatever is granted; give 'e2e-reader' staff status, which lets them "
        "sign in to the Django admin site and read the logs; grant 'e2e-reader' the permissions "
        "add_tag, add_note; take the permissions "
    )
    assert text.startswith(expected)


async def test_nothing_given_and_the_server_account_are_refused_with_their_hints() -> None:
    setup = Setup(writes=False)
    hint = " Hint: Pass at least one field to change."

    assert await failure(setup.accounts.update_user(READER_ID), Validation) == (
        "Nothing to change: no field was given." + hint
    )
    assert await failure(
        setup.accounts.update_user("admin", is_superuser=False), Validation
    ) == SELF.format(what="removing its superuser status or deactivating it")
    assert await failure(setup.accounts.delete_user("admin", confirm=True), Validation) == (
        SELF.format(what="deleting it")
    )


async def test_reactivating_or_keeping_flags_that_are_already_set_needs_no_confirm() -> None:
    setup = Setup()

    outcome = await setup.accounts.update_user(READER_ID, is_active=True, first_name="Rita")

    assert outcome.changed
    assert outcome.message == "Updated user 'e2e-reader': first_name."


async def test_a_deleted_user_is_forgotten_and_the_texts_are_exact() -> None:
    setup = Setup(writes=False)
    setup.state.users = [
        replace(u, is_superuser=True) if u.id == READER_ID else u for u in setup.state.users
    ]
    plain = Setup(writes=False)

    assert await failure(setup.accounts.delete_user(READER_ID), ConfirmationRequired) == (
        (
            "Nothing was changed. Confirming would delete user 'e2e-reader' for good. Documents "
            "and "
            "other objects they own lose their owner (every user with the permission may then see "
            "them), their grants are removed and they can no longer sign in; the user is a "
            "superuser. Hint: "
        )
        + _hint()
    )
    assert "removed and they can no longer sign in. Hint:" in await failure(
        plain.accounts.delete_user("e2e-blind"), ConfirmationRequired
    )
    live = Setup()
    _ = await live.taxonomy.items("user")
    done = await live.accounts.delete_user("e2e-blind", confirm=True)
    assert done.message == "Deleted user 'e2e-blind'."
    with pytest.raises(PaperlessMcpError):
        _ = await live.taxonomy.resolve("user", "e2e-blind")


def _hint() -> str:
    return ConfirmationRequired("x").hint or ""


# --- groups


async def test_group_texts_and_the_cache() -> None:
    setup = Setup()
    _ = await setup.taxonomy.items("group")

    made = await setup.accounts.create_group("books", ["view_tag"], confirm=True)
    assert (await setup.taxonomy.resolve("group", "books")).id == made.group.id
    assert made.message == f"Created group 'books' (id {made.group.id})."
    assert await failure(setup.accounts.update_group("books"), Validation) == (
        "Nothing to change: no field was given. Hint: Pass a new name or the permissions."
    )
    changed = await setup.accounts.update_group(
        "books", name="ledgers", permissions=["add_tag"], confirm=True
    )
    assert changed.message == "Updated group 'ledgers': name, permissions."
    assert (await setup.taxonomy.resolve("group", "ledgers")).id == made.group.id
    gone = await setup.accounts.delete_group("ledgers", confirm=True)
    assert gone.message == "Deleted group 'ledgers'."
    with pytest.raises(PaperlessMcpError):
        _ = await setup.taxonomy.resolve("group", "ledgers")


async def test_the_group_refusals_are_exact() -> None:
    setup = Setup(writes=False)
    setup.state.groups = [
        replace(g, permissions=frozenset({"view_tag"})) for g in setup.state.groups
    ]
    tail = " Hint: " + _hint()

    assert await failure(
        setup.accounts.update_group(GROUP, permissions=["add_tag", "add_note"]),
        ConfirmationRequired,
    ) == (
        (
            "Nothing was changed. Confirming would give the members of 'e2e-group' the permissions "
            "add_tag, add_note; take the permissions view_tag away from them."
        )
        + tail
    )
    assert await failure(setup.accounts.delete_group(GROUP), ConfirmationRequired) == (
        (
            "Nothing was changed. Confirming would delete group 'e2e-group' for good; its members "
            "lose the permissions it grants and objects shared with the group are no longer "
            "shared with them."
        )
        + tail
    )


# --- system: configuration


def mark(**given: object) -> dict[str, object]:
    return dict(given)


async def test_the_configuration_checks_say_exactly_what_is_wrong() -> None:
    setup = Setup(writes=False)
    system = setup.system

    assert await failure(system.update_config({}), Validation) == (
        "Nothing to change: no setting and no key was given. Hint: Pass settings, or a key "
        "such as llm_api_key."
    )
    assert await failure(
        system.update_config({"nope": 1, "app_logo": "x", "llm_api_key": "k"}), Validation
    ) == (
        "These cannot be changed as settings: nope, app_logo (a file upload: change it in the "
        "web UI), llm_api_key (pass it as the key of the same name). Hint: The description of "
        "update_app_config lists the settings."
    )
    assert await failure(system.update_config(keys={"app_title": SecretStr("x")}), Validation) == (
        "app_title is not an API key setting. Hint: The keys are llm_api_key, "
        "llm_embedding_api_key, remote_ocr_api_key."
    )
    assert await failure(system.update_config(keys={"llm_api_key": NotText()}), Validation) == (
        "The llm_api_key must be text. Hint: Pass the key as a string."
    )
    assert await failure(system.update_config(keys={"llm_api_key": SecretStr("")}), Validation) == (
        "The llm_api_key must not be empty. Hint: Pass the key, or leave it out."
    )
    assert await failure(
        system.update_config(keys={"llm_api_key": SecretStr("**")}), Validation
    ) == (
        "The llm_api_key cannot consist only of asterisks: Paperless takes that to mean "
        "'keep the saved one'. Hint: Leave the llm_api_key out to keep the saved one."
    )


async def test_a_setting_that_does_not_change_is_skipped_and_the_rest_still_goes() -> None:
    setup = Setup()
    setup.state.config = {"app_title": "Archive"}

    outcome = await setup.system.update_config({"app_title": "Archive", "pages": 2}, confirm=True)

    assert outcome.changed == ("pages",)
    assert outcome.message == "Changed 1 setting: pages."
    nothing = await setup.system.update_config({"pages": 2})
    assert nothing.message == "Nothing changed: the configuration already is as asked."


async def test_the_refusal_of_a_configuration_change_is_exact() -> None:
    setup = Setup(writes=False)
    setup.state.config = {"app_title": "Archive"}
    setup.state.config_keys = {"llm_api_key"}
    tail = " Hint: " + _hint()

    assert await failure(
        setup.system.update_config(
            {"app_title": "Vault", "llm_endpoint": "https://llm.example/v1"},
            keys={"llm_api_key": SecretStr("k"), "remote_ocr_api_key": SecretStr("k")},
        ),
        ConfirmationRequired,
    ) == (
        (
            "Nothing was changed. Confirming would change app_title from 'Archive' to 'Vault'; set "
            "llm_endpoint to 'https://llm.example/v1' (it follows the server's environment now); "
            "replace the saved llm_api_key (its value is never shown); save the remote_ocr_api_key "
            "you gave (its value is never shown); this changes where Paperless sends document text "
            "or how it reaches the service that gets it."
        )
        + tail
    )


async def test_exactly_five_settings_are_all_named_and_six_are_counted() -> None:
    setup = Setup(writes=False)
    names = ["pages", "image_dpi", "barcode_dpi", "barcode_max_pages", "llm_context_size"]

    five = await failure(setup.system.update_config(dict.fromkeys(names, 5)), ConfirmationRequired)
    six = await failure(
        setup.system.update_config(dict.fromkeys([*names, "llm_request_timeout"], 5)),
        ConfirmationRequired,
    )

    assert "more settings" not in five
    assert five.count("; set ") == 4
    assert "; and 1 more settings" in six


async def test_values_are_shown_whole_up_to_sixty_characters_and_cut_after() -> None:
    setup = Setup(writes=False)
    exact = list(range(10, 25))  # JSON of exactly 60 characters
    longer = [*exact, 7]
    text_60, text_61 = "a" * 60, "a" * 61

    async def shown(value: object) -> str:
        return await failure(setup.system.update_config({"user_args": value}), ConfirmationRequired)  # pyright: ignore[reportArgumentType] -- any JSON value

    assert len(json.dumps(exact)) == 60
    assert f"set user_args to {json.dumps(exact)} (" in await shown(exact)
    cut = json.dumps(longer)[:59] + "…"
    assert f"set user_args to {cut} (" in await shown(longer)
    assert f"to '{text_60}' (" in await shown(text_60)
    assert f"to '{'a' * 59}…'" in await shown(text_61)


async def test_a_reset_and_a_changed_value_name_the_old_value() -> None:
    setup = Setup(writes=False)
    setup.state.config = {"pages": 3}

    text = await failure(setup.system.update_config({"pages": None}), ConfirmationRequired)

    assert "reset pages (now 3) to the server's environment." in text


# --- system: logs


async def test_the_line_limits_are_inclusive_and_refused_with_a_hint() -> None:
    setup = Setup()
    for lines in (1, MAX_LOG_LINES):
        assert (await setup.system.get_log("celery", lines=lines)).lines == ()
    refusal = await failure(setup.system.get_log("celery", lines=0), Validation)
    assert refusal == (
        f"lines must be between 1 and {MAX_LOG_LINES}. Hint: Ask for fewer lines, or read the "
        "log in the web UI."
    )
    with pytest.raises(Validation):
        _ = await setup.system.get_log("celery", lines=MAX_LOG_LINES + 1)


async def test_the_character_limit_is_exact() -> None:
    setup = Setup()
    setup.state.logs["mail"] = ["x" * 99] * (MAX_LOG_CHARS // 100)

    fits = await setup.system.get_log("mail", lines=MAX_LOG_LINES)
    setup.state.logs["mail"] = ["x" * 99] * (MAX_LOG_CHARS // 100 + 1)
    over = await setup.system.get_log("mail", lines=MAX_LOG_LINES)

    assert (fits.returned_lines, fits.truncated) == (MAX_LOG_CHARS // 100, False)
    assert (over.returned_lines, over.truncated) == (MAX_LOG_CHARS // 100, True)


async def test_a_line_that_alone_is_too_long_is_cut_to_the_limit_minus_one() -> None:
    setup = Setup()
    setup.state.logs["mail"] = ["a" * MAX_LOG_CHARS * 2]

    found = await setup.system.get_log("mail", lines=1)

    assert [len(line) for line in found.lines] == [MAX_LOG_CHARS - 1]


# --- system: tasks and downloads


async def test_the_default_page_has_twenty_five_tasks_and_the_filter_is_passed_on() -> None:
    setup = Setup()
    setup.state.tasks = [
        FakeTask(id=n, task_id=f"u{n}", acknowledged=n % 2 == 0) for n in range(1, 41)
    ]

    first = await setup.system.list_tasks()
    seen = await setup.system.list_tasks(acknowledged=True, page_size=100)

    assert len(first.tasks) == 25
    assert seen.count == 20
    assert all(t.acknowledged for t in seen.tasks)


async def test_acknowledging_a_repeated_id_is_refused_with_a_hint() -> None:
    assert await failure(Setup().system.acknowledge_tasks([1, 1]), Validation) == (
        "A task id is listed twice. Hint: List every id once."
    )


class Recorder(FakePaperless):
    seen: dict[str, object] = {}  # noqa: RUF012 -- a test spy, one instance per test

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
        self.seen = {
            "ids": list(document_ids),
            "content": content,
            "compression": compression,
            "follow_formatting": follow_formatting,
            "max_bytes": max_bytes,
        }
        return await super().download_documents(
            document_ids,
            content=content,
            compression=compression,
            follow_formatting=follow_formatting,
            max_bytes=max_bytes,
        )


async def test_the_download_options_reach_the_port_and_have_quiet_defaults() -> None:
    setup = Setup()
    spy = Recorder(setup.state, user=ADMIN[0])
    system = SystemAdmin(spy, setup.fake, max_bytes=10**6)

    _ = await system.download([1])
    assert spy.seen == {
        "ids": [1],
        "content": "archive",
        "compression": "none",
        "follow_formatting": False,
        "max_bytes": 10**6,
    }
    _ = await system.download([1, 2], content="both", compression="lzma", follow_formatting=True)
    assert (spy.seen["content"], spy.seen["compression"], spy.seen["follow_formatting"]) == (
        "both",
        "lzma",
        True,
    )


async def test_the_document_count_limits_and_texts_are_exact() -> None:
    setup = Setup()
    text = (
        f"Give between 1 and {MAX_DOCUMENTS} document ids, each once. Hint: Find ids with "
        "search_documents."
    )

    assert await failure(setup.system.download([]), Validation) == text
    exactly = list(range(1, MAX_DOCUMENTS + 1))
    assert await failure(setup.system.download([*exactly, 101]), Validation) == text
    # exactly MAX_DOCUMENTS ids is accepted by the service; the fake then rejects unknown ones
    with pytest.raises(Validation) as raised:
        _ = await setup.system.download(exactly)
    assert "Give between" not in raised.value.message


async def test_the_over_limit_note_is_exact() -> None:
    setup = Setup()
    system = SystemAdmin(setup.fake, setup.fake, max_bytes=10)

    delivery = await system.download([1])

    assert delivery.report.note == (
        "The archive is over the download limit of 10 bytes, so it is not attached. Ask for "
        "fewer documents, fetch single files with download_document, or ask the administrator "
        "to raise PAPERLESS_MCP_MAX_DOWNLOAD_BYTES."
    )


class UserSpy(FakePaperless):
    values: object = None

    @override
    async def update_user(self, user_id: int, values: UserValues) -> UserRecord:
        self.values = values
        return await super().update_user(user_id, values)


async def test_an_empty_email_reaches_the_port_as_no_address() -> None:
    setup = Setup()
    setup.state.users = [
        replace(u, email="r@x.org") if u.id == READER_ID else u for u in setup.state.users
    ]
    spy = UserSpy(setup.state, user=ADMIN[0], allow_writes=True)
    service = AccountAdmin(spy, spy, setup.taxonomy)

    _ = await service.update_user(READER_ID, email="")

    assert spy.values == UserValues(email=None)


async def test_several_added_groups_are_listed_with_commas() -> None:
    setup = Setup(writes=False)
    writer = FakePaperless(setup.state, user=ADMIN[0], allow_writes=True)
    _ = await writer.create_group(GroupValues(name="second"))
    _ = await writer.create_group(GroupValues(name="third"))
    setup.state.users = [
        replace(u, groups=frozenset({GROUP})) if u.id == READER_ID else u for u in setup.state.users
    ]

    text = await failure(
        setup.accounts.update_user(READER_ID, groups=["second", "third"]), ConfirmationRequired
    )

    assert (
        "add 'e2e-reader' to the groups 'second', 'third'; remove 'e2e-reader' from the groups"
        in text
    )


async def test_the_changed_settings_are_listed_in_the_message() -> None:
    setup = Setup()

    outcome = await setup.system.update_config({"app_title": "A", "pages": 2}, confirm=True)

    assert outcome.message == "Changed 2 settings: app_title, pages."


async def test_the_oldest_line_that_would_overflow_by_one_is_dropped() -> None:
    setup = Setup()
    setup.state.logs["mail"] = ["o" * 100, *["n" * 99] * 199]

    found = await setup.system.get_log("mail", lines=MAX_LOG_LINES)

    assert (found.returned_lines, found.truncated) == (199, True)


# --- fail closed when the server's own account cannot be determined


class UnknownCaller(FakePaperless):
    """A fake that cannot say who the server is: its identity call fails."""

    @override
    async def get_current_user_id(self) -> int:
        message = "Paperless did not respond in time."
        raise UpstreamTimeout(message)


CANNOT_TELL = (
    "Could not find out which account this server signs in with, so nothing that could lock "
    "it out was done. Hint: Try again in a moment; changes that cannot affect the server's own "
    "account (names, e-mail, groups, permissions) are not held back."
)


async def test_without_the_identity_a_deletion_or_lockout_is_refused() -> None:
    setup = Setup()
    service = AccountAdmin(
        UnknownCaller(setup.state, user=ADMIN[0], allow_writes=True),
        setup.fake,
        setup.taxonomy,
    )

    assert await failure(service.delete_user("e2e-reader", confirm=True), Validation) == CANNOT_TELL
    for lockout in ({"is_active": False}, {"is_superuser": False}):
        assert (
            await failure(
                service.update_user("e2e-reader", confirm=True, **lockout),  # pyright: ignore[reportArgumentType] -- flags of the loop
                Validation,
            )
            == CANNOT_TELL
        )
    assert [u.username for u in setup.state.users] == ["admin", "e2e-reader", "e2e-blind"]
    assert next(u for u in setup.state.users if u.id == READER_ID).is_active


async def test_without_the_identity_changes_that_cannot_lock_out_still_work() -> None:
    setup = Setup()
    service = AccountAdmin(
        UnknownCaller(setup.state, user=ADMIN[0], allow_writes=True),
        setup.fake,
        setup.taxonomy,
    )

    outcome = await service.update_user(
        "e2e-reader", first_name="Rita", is_active=True, is_superuser=True, confirm=True
    )

    assert (outcome.user.first_name, outcome.user.is_superuser) == ("Rita", True)
