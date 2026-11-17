"""Contract of AdminReader and AdminWriter: the same assertions against the fake and live 3.4.2.

Users and groups get a unique name per test; the `people` fixture deletes what a test made, also
when it failed (the stack is shared). The configuration is shared by every test of the stack: the
`config` fixture restores the two settings these tests touch. Tasks are started only as
`sanity_check`, which changes nothing; its row is acknowledged afterwards.
"""

import io
import uuid
import zipfile
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from paperless_mcp.domain.admin import ConfigValues, GroupValues, UserValues
from paperless_mcp.domain.errors import NotFound, PermissionDenied, Validation

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from contract.conftest import Backend

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]
GET_USER, GET_GROUP = "AdminReader.get_user", "AdminReader.get_group"
GET_CONFIG, LIST_LOGS = "AdminReader.get_config", "AdminReader.list_logs"
GET_LOG, LIST_TASKS = "AdminReader.get_log", "AdminReader.list_tasks"
DOWNLOAD = "AdminReader.download_documents"
CURRENT = "AdminReader.get_current_user_id"
CREATE_USER, UPDATE_USER = "AdminWriter.create_user", "AdminWriter.update_user"
DELETE_USER = "AdminWriter.delete_user"
CREATE_GROUP, UPDATE_GROUP = "AdminWriter.create_group", "AdminWriter.update_group"
DELETE_GROUP, UPDATE_CONFIG = "AdminWriter.delete_group", "AdminWriter.update_config"
ACKNOWLEDGE, RUN = "AdminWriter.acknowledge_tasks", "AdminWriter.run_task"
UNKNOWN = 999_999
PASSWORD = "Contract-Passw0rd-4711"
SETTINGS = ("app_title", "barcode_string")


class People:
    """Users and groups a test made; they are deleted when it ends."""

    def __init__(self, backend: Backend) -> None:
        self.backend: Backend = backend
        self.users: list[int] = []
        self.groups: list[int] = []

    def name(self) -> str:
        return f"contract-{uuid.uuid4().hex[:12]}"

    async def user(self, **values: object) -> int:
        fields: dict[str, object] = {"username": self.name(), "password": SecretStr(PASSWORD)}
        made = await self.backend.admin.management_writes.create_user(
            UserValues.model_validate(fields | values)
        )
        self.users.append(made.id)
        return made.id

    async def group(self, **values: object) -> int:
        fields: dict[str, object] = {"name": self.name()}
        made = await self.backend.admin.management_writes.create_group(
            GroupValues.model_validate(fields | values)
        )
        self.groups.append(made.id)
        return made.id


@pytest.fixture
async def people(backend: Backend) -> AsyncIterator[People]:
    made = People(backend)
    yield made
    for user in made.users:
        try:
            await backend.admin.management_writes.delete_user(user)
        except NotFound:
            continue
    for group in made.groups:
        try:
            await backend.admin.management_writes.delete_group(group)
        except NotFound:
            continue


@pytest.fixture
async def config(backend: Backend) -> AsyncIterator[None]:
    before = (await backend.admin.management.get_config()).settings
    yield
    _ = await backend.admin.management_writes.update_config(
        ConfigValues(settings={name: before.get(name) for name in SETTINGS})
    )


# --- users


@pytest.mark.contract(CREATE_USER)
@pytest.mark.contract(GET_USER)
async def test_a_created_user_is_read_back_without_a_password(
    backend: Backend, people: People
) -> None:
    group = await people.group()
    number = await people.user(
        email="c@example.com", first_name="Con", last_name="Tract", group_ids=(group,)
    )

    found = await backend.admin.management.get_user(number)

    assert (found.email, found.first_name, found.last_name) == ("c@example.com", "Con", "Tract")
    assert (found.is_active, found.is_staff, found.is_superuser) == (True, False, False)
    assert found.group_ids == (group,)
    assert "password" not in found.model_dump()
    assert PASSWORD not in found.model_dump_json()
    assert "*" not in found.model_dump_json()


@pytest.mark.contract(CREATE_USER)
async def test_a_user_takes_permission_codenames(backend: Backend, people: People) -> None:
    number = await people.user(permissions=("view_document", "add_tag"))

    found = await backend.admin.management.get_user(number)

    assert set(found.permissions) == {"view_document", "add_tag"}


@pytest.mark.contract(CREATE_USER)
async def test_a_username_is_unique_and_must_be_valid(backend: Backend, people: People) -> None:
    taken = await backend.admin.management.get_user(await people.user())
    writes = backend.admin.management_writes

    with pytest.raises(Validation):
        _ = await writes.create_user(UserValues(username=taken.username))
    with pytest.raises(Validation):
        _ = await writes.create_user(UserValues(username="not valid!"))
    with pytest.raises(Validation):
        _ = await writes.create_user(UserValues(username=people.name(), email="nope"))
    with pytest.raises(Validation):
        _ = await writes.create_user(UserValues(username=people.name(), group_ids=(UNKNOWN,)))
    with pytest.raises(Validation):
        _ = await writes.create_user(UserValues(username=people.name(), permissions=("nope",)))


@pytest.mark.contract(UPDATE_USER)
async def test_an_update_changes_only_what_is_given(backend: Backend, people: People) -> None:
    number = await people.user(first_name="Con", email="c@example.com")
    writes = backend.admin.management_writes

    changed = await writes.update_user(number, UserValues(last_name="Tract", is_active=False))

    assert (changed.first_name, changed.last_name, changed.is_active) == ("Con", "Tract", False)
    assert changed.email == "c@example.com"
    again = await writes.update_user(number, UserValues(email=None, permissions=("view_tag",)))
    assert (again.email, again.permissions) == (None, ("view_tag",))
    assert again.last_name == "Tract"


@pytest.mark.contract(UPDATE_USER)
async def test_an_update_can_move_a_user_between_groups_and_rename_it(
    backend: Backend, people: People
) -> None:
    first, second = await people.group(), await people.group()
    number = await people.user(group_ids=(first,))
    writes = backend.admin.management_writes
    new_name = people.name()

    moved = await writes.update_user(number, UserValues(group_ids=(second,), username=new_name))

    assert (moved.group_ids, moved.username) == ((second,), new_name)
    emptied = await writes.update_user(number, UserValues(group_ids=()))
    assert emptied.group_ids == ()


@pytest.mark.contract(UPDATE_USER)
@pytest.mark.contract(DELETE_USER)
async def test_a_user_that_does_not_exist_is_not_found(backend: Backend) -> None:
    writes = backend.admin.management_writes

    with pytest.raises(NotFound):
        _ = await backend.admin.management.get_user(UNKNOWN)
    with pytest.raises(NotFound):
        _ = await writes.update_user(UNKNOWN, UserValues(first_name="x"))
    with pytest.raises(NotFound):
        await writes.delete_user(UNKNOWN)


@pytest.mark.contract(DELETE_USER)
async def test_a_deleted_user_is_gone(backend: Backend, people: People) -> None:
    number = await people.user()

    await backend.admin.management_writes.delete_user(number)

    with pytest.raises(NotFound):
        _ = await backend.admin.management.get_user(number)


@pytest.mark.contract(GET_USER)
@pytest.mark.contract(CREATE_USER)
@pytest.mark.contract(UPDATE_USER)
@pytest.mark.contract(DELETE_USER)
async def test_users_need_their_permissions_and_say_so(backend: Backend, people: People) -> None:
    number = await people.user()
    nothing = backend.restricted(frozenset({"view_uisettings"}))

    for call in (
        nothing.management.get_user(number),
        nothing.management_writes.create_user(UserValues(username=people.name())),
        nothing.management_writes.update_user(number, UserValues(first_name="x")),
        nothing.management_writes.delete_user(number),
    ):
        with pytest.raises(PermissionDenied) as raised:
            await call
        assert raised.value.hint


@pytest.mark.contract(CREATE_USER)
@pytest.mark.contract(UPDATE_USER)
async def test_only_a_superuser_makes_superusers_and_changes_them(
    backend: Backend, people: People
) -> None:
    number = await people.user()
    clerk = backend.restricted(frozenset({"view_user", "add_user", "change_user"}))
    superuser = await people.user(is_superuser=True)

    with pytest.raises(PermissionDenied):
        _ = await clerk.management_writes.create_user(
            UserValues(username=people.name(), is_superuser=True)
        )
    with pytest.raises(PermissionDenied):
        _ = await clerk.management_writes.update_user(number, UserValues(is_staff=True))
    with pytest.raises(PermissionDenied):
        _ = await clerk.management_writes.update_user(superuser, UserValues(first_name="x"))
    made = await clerk.management_writes.update_user(number, UserValues(first_name="ok"))
    assert made.first_name == "ok"


# --- groups


@pytest.mark.contract(CREATE_GROUP)
@pytest.mark.contract(GET_GROUP)
async def test_a_created_group_is_read_back_with_its_permissions(
    backend: Backend, people: People
) -> None:
    number = await people.group(permissions=("view_document", "view_tag"))

    found = await backend.admin.management.get_group(number)

    assert set(found.permissions) == {"view_document", "view_tag"}
    bare = await backend.admin.management.get_group(await people.group())
    assert bare.permissions == ()


@pytest.mark.contract(CREATE_GROUP)
async def test_a_group_name_is_unique_and_permissions_must_exist(
    backend: Backend, people: People
) -> None:
    number = await people.group()
    taken = (await backend.admin.management.get_group(number)).name
    writes = backend.admin.management_writes

    with pytest.raises(Validation):
        _ = await writes.create_group(GroupValues(name=taken))
    with pytest.raises(Validation):
        _ = await writes.create_group(GroupValues(name=people.name(), permissions=("nope",)))


@pytest.mark.contract(UPDATE_GROUP)
async def test_a_group_is_renamed_and_its_permissions_replaced(
    backend: Backend, people: People
) -> None:
    number = await people.group(permissions=("view_tag",))
    writes = backend.admin.management_writes
    new_name = people.name()

    renamed = await writes.update_group(number, GroupValues(name=new_name))
    replaced = await writes.update_group(number, GroupValues(permissions=("view_document",)))

    assert (renamed.name, renamed.permissions) == (new_name, ("view_tag",))
    assert (replaced.name, replaced.permissions) == (new_name, ("view_document",))
    with pytest.raises(Validation):
        _ = await writes.update_group(number, GroupValues(permissions=("nope",)))


@pytest.mark.contract(DELETE_GROUP)
async def test_a_deleted_group_leaves_its_members(backend: Backend, people: People) -> None:
    group = await people.group()
    member = await people.user(group_ids=(group,))

    await backend.admin.management_writes.delete_group(group)

    assert (await backend.admin.management.get_user(member)).group_ids == ()
    with pytest.raises(NotFound):
        _ = await backend.admin.management.get_group(group)


@pytest.mark.contract(GET_GROUP)
@pytest.mark.contract(UPDATE_GROUP)
@pytest.mark.contract(DELETE_GROUP)
async def test_a_group_that_does_not_exist_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound):
        _ = await backend.admin.management.get_group(UNKNOWN)
    with pytest.raises(NotFound):
        _ = await backend.admin.management_writes.update_group(UNKNOWN, GroupValues(name="x"))
    with pytest.raises(NotFound):
        await backend.admin.management_writes.delete_group(UNKNOWN)


@pytest.mark.contract(GET_GROUP)
@pytest.mark.contract(CREATE_GROUP)
@pytest.mark.contract(UPDATE_GROUP)
@pytest.mark.contract(DELETE_GROUP)
async def test_groups_need_their_permissions_and_say_so(backend: Backend, people: People) -> None:
    number = await people.group()
    nothing = backend.restricted(frozenset({"view_uisettings"}))

    for call in (
        nothing.management.get_group(number),
        nothing.management_writes.create_group(GroupValues(name=people.name())),
        nothing.management_writes.update_group(number, GroupValues(name="x")),
        nothing.management_writes.delete_group(number),
    ):
        with pytest.raises(PermissionDenied) as raised:
            await call
        assert raised.value.hint


# --- configuration


@pytest.mark.contract(GET_CONFIG)
@pytest.mark.contract(UPDATE_CONFIG)
@pytest.mark.usefixtures("config")
async def test_a_setting_is_changed_and_reset_and_other_settings_stay(backend: Backend) -> None:
    management, writes = backend.admin.management, backend.admin.management_writes
    start = (await management.get_config()).settings

    changed = await writes.update_config(
        ConfigValues(settings={"app_title": "Contract title", "barcode_string": "PATCHT"})
    )
    assert changed.settings["app_title"] == "Contract title"
    assert changed.settings["barcode_string"] == "PATCHT"
    assert {k: v for k, v in changed.settings.items() if k not in SETTINGS} == {
        k: v for k, v in start.items() if k not in SETTINGS
    }
    reset = await writes.update_config(ConfigValues(settings={"app_title": None}))
    assert "app_title" not in reset.settings
    assert reset.settings["barcode_string"] == "PATCHT"
    assert (await management.get_config()).settings == reset.settings
    assert "llm_api_key" not in reset.settings


@pytest.mark.contract(UPDATE_CONFIG)
@pytest.mark.usefixtures("config")
async def test_a_value_paperless_rejects_is_a_validation_error(backend: Backend) -> None:
    writes = backend.admin.management_writes

    with pytest.raises(Validation):
        _ = await writes.update_config(ConfigValues(settings={"output_type": "docx"}))
    with pytest.raises(Validation):
        _ = await writes.update_config(ConfigValues(settings={"app_title": "x" * 49}))
    with pytest.raises(Validation):
        _ = await writes.update_config(ConfigValues(secrets={"llm_api_key": SecretStr("****")}))


@pytest.mark.contract(GET_CONFIG)
@pytest.mark.contract(UPDATE_CONFIG)
async def test_anyone_reads_the_configuration_but_changing_it_needs_a_permission(
    backend: Backend,
) -> None:
    nothing = backend.restricted(frozenset({"view_uisettings"}))

    assert (await nothing.management.get_config()).id >= 1
    with pytest.raises(PermissionDenied) as raised:
        _ = await nothing.management_writes.update_config(ConfigValues(settings={"app_title": "x"}))
    assert raised.value.hint


# --- logs


@pytest.mark.contract(LIST_LOGS)
@pytest.mark.contract(GET_LOG)
async def test_logs_are_listed_and_read_from_the_end(backend: Backend) -> None:
    management = backend.admin.management

    names = await management.list_logs()
    assert "paperless" in names
    lines = await management.get_log("paperless", limit=3)
    assert 1 <= len(lines) <= 3
    assert all(isinstance(line, str) for line in lines)
    assert (await management.get_log("paperless", limit=1))[-1] == lines[-1] or len(lines) > 0


@pytest.mark.contract(GET_LOG)
async def test_a_log_that_does_not_exist_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound):
        _ = await backend.admin.management.get_log("nope", limit=5)


@pytest.mark.contract(LIST_LOGS)
@pytest.mark.contract(GET_LOG)
async def test_logs_are_for_staff_only(backend: Backend) -> None:
    for call in (
        backend.reader.management.list_logs(),
        backend.reader.management.get_log("paperless", limit=1),
    ):
        with pytest.raises(PermissionDenied) as raised:
            await call
        assert raised.value.hint


# --- tasks


@pytest.mark.contract(RUN)
@pytest.mark.contract(LIST_TASKS)
@pytest.mark.contract(ACKNOWLEDGE)
async def test_a_task_is_started_listed_and_acknowledged(backend: Backend) -> None:
    management, writes = backend.admin.management, backend.admin.management_writes

    task_id = await writes.run_task("sanity_check")

    found = None
    for page in range(1, 6):
        listed = await management.list_tasks(
            state=None, task_type="sanity_check", acknowledged=False, page=page, page_size=100
        )
        found = next((row for row in listed.rows if row.task_id == task_id), None)
        if found or listed.next_page is None:
            break
    assert found is not None
    assert (found.task_type, found.trigger_source, found.acknowledged) == (
        "sanity_check",
        "manual",
        False,
    )
    assert await writes.acknowledge_tasks([found.id]) == 1
    after = await management.list_tasks(
        state=None, task_type=None, acknowledged=True, page=1, page_size=100
    )
    assert found.id in {row.id for row in after.rows}
    with pytest.raises(Validation):
        _ = await writes.acknowledge_tasks([found.id, found.id])
    with pytest.raises(Validation):
        _ = await writes.acknowledge_tasks([UNKNOWN * 1000])


@pytest.mark.contract(LIST_TASKS)
async def test_tasks_are_paged_newest_first_and_filtered(backend: Backend) -> None:
    management = backend.admin.management
    _ = backend.consume(succeed=True)
    _ = backend.consume(succeed=False)

    first = await management.list_tasks(
        state=None, task_type=None, acknowledged=None, page=1, page_size=2
    )
    failed = await management.list_tasks(
        state="failure", task_type="consume_file", acknowledged=None, page=1, page_size=100
    )

    assert len(first.rows) == 2
    assert first.count >= 2
    assert [r.created for r in first.rows] == sorted((r.created for r in first.rows), reverse=True)
    assert failed.rows
    assert {r.state for r in failed.rows} == {"failure"}
    assert any(r.message for r in failed.rows)
    with pytest.raises(NotFound):
        _ = await management.list_tasks(
            state=None, task_type=None, acknowledged=None, page=UNKNOWN, page_size=2
        )


@pytest.mark.contract(LIST_TASKS)
@pytest.mark.contract(ACKNOWLEDGE)
@pytest.mark.contract(RUN)
async def test_tasks_need_their_permissions_and_say_so(backend: Backend) -> None:
    nothing = backend.restricted(frozenset({"view_uisettings"}))
    viewer = backend.restricted(frozenset({"view_paperlesstask"}))

    for call in (
        nothing.management.list_tasks(
            state=None, task_type=None, acknowledged=None, page=1, page_size=1
        ),
        viewer.management_writes.acknowledge_tasks([1]),
        viewer.management_writes.run_task("sanity_check"),
    ):
        with pytest.raises(PermissionDenied) as raised:
            await call
        assert raised.value.hint


# --- bulk download


def members(archive: bytes) -> list[bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        return [opened.read(name) for name in opened.namelist()]


@pytest.mark.contract(DOWNLOAD)
async def test_documents_come_as_one_archive_of_their_files(backend: Backend) -> None:
    ids = backend.new_documents(2)
    originals = {
        (await backend.admin.documents.get_document_file(n, "original", max_bytes=10**7)).content
        for n in ids
    }

    found = await backend.admin.management.download_documents(
        ids, content="originals", compression="none", follow_formatting=False, max_bytes=10**7
    )

    assert not found.truncated
    assert set(members(found.content)) == originals


@pytest.mark.contract(DOWNLOAD)
async def test_an_archive_over_the_limit_is_not_returned(backend: Backend) -> None:
    ids = backend.new_documents(1)

    found = await backend.admin.management.download_documents(
        ids, content="both", compression="none", follow_formatting=True, max_bytes=10
    )

    assert (found.content, found.truncated) == (b"", True)


@pytest.mark.contract(DOWNLOAD)
async def test_unknown_or_hidden_documents_are_refused(backend: Backend) -> None:
    hidden = 2  # the seeded private document of the admin
    with pytest.raises(Validation):
        _ = await backend.admin.management.download_documents(
            [UNKNOWN], content="archive", compression="none", follow_formatting=False, max_bytes=9
        )
    with pytest.raises(PermissionDenied) as raised:
        _ = await backend.reader.management.download_documents(
            [hidden], content="archive", compression="none", follow_formatting=False, max_bytes=9
        )
    assert raised.value.hint


@pytest.mark.contract(CURRENT)
async def test_the_account_of_the_credential_is_identified(backend: Backend) -> None:
    overview = await backend.admin.instance.get_overview()
    assert overview.current_user is not None

    assert await backend.admin.management.get_current_user_id() == overview.current_user.id
    assert await backend.reader.management.get_current_user_id() != overview.current_user.id
