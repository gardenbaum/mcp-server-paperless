"""PaperlessAdminReader and PaperlessAdminWriter on the Paperless 3.4.2 wire format."""

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast, override

import httpx2
import pytest
from pydantic import SecretStr

from adapter.conftest import streaming
from mcp_server_paperless.domain.admin import (
    AppConfigRecord,
    ConfigValues,
    GroupRecord,
    GroupValues,
    TaskRow,
    UserRecord,
    UserValues,
)
from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.paperless.admin import (
    DOWNLOAD_SECONDS,
    PaperlessAdminReader,
    PaperlessAdminWriter,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy, Streamed

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-18")]
type Json = dict[str, object]

PASSWORD = "hunter2-Tr0ub4dor&3"
MASK = "*" * 91
KEY = "test-token-not-a-secret"
KEY_MASK = "*" * len(KEY)

USER_WIRE: Json = {
    "id": 5,
    "username": "alice",
    "email": "alice@example.com",
    "password": MASK,
    "first_name": "Alice",
    "last_name": "Archer",
    "date_joined": "2026-11-12T17:44:05.838953Z",
    "is_staff": False,
    "is_active": True,
    "is_superuser": False,
    "groups": [1, 3],
    "user_permissions": ["view_document", "add_tag"],
    "inherited_permissions": ["documents.view_tag", "documents.view_document"],
    "is_mfa_enabled": True,
}
USER = UserRecord(
    id=5,
    username="alice",
    email="alice@example.com",
    first_name="Alice",
    last_name="Archer",
    is_active=True,
    is_staff=False,
    is_superuser=False,
    group_ids=(1, 3),
    permissions=("view_document", "add_tag"),
    inherited_permissions=("view_document", "view_tag"),
    date_joined=datetime(2026, 11, 12, 17, 44, 5, 838953, tzinfo=UTC),
    mfa_enabled=True,
)
GROUP_WIRE: Json = {"id": 3, "name": "accounting", "permissions": ["view_document", "view_tag"]}
GROUP = GroupRecord(id=3, name="accounting", permissions=("view_document", "view_tag"))
CONFIG_WIRE: Json = {
    "id": 1,
    "externally_configured_variables": ["PAPERLESS_OCR_LANGUAGE"],
    "user_args": None,
    "barcode_tag_mapping": {"ASN": "tag"},
    "llm_embedding_api_key": None,
    "llm_api_key": KEY_MASK,
    "remote_ocr_api_key": None,
    "output_type": "pdfa",
    "pages": 5,
    "language": "",
    "mode": None,
    "archive_file_generation": None,
    "image_dpi": None,
    "unpaper_clean": None,
    "deskew": True,
    "rotate_pages": False,
    "rotate_pages_threshold": 12.5,
    "app_title": "Archive",
    "app_logo": "http://paperless.test/media/logo/logo.png",
    "ai_enabled": None,
}
CONFIG = AppConfigRecord(
    id=1,
    externally_configured_variables=("PAPERLESS_OCR_LANGUAGE",),
    settings={
        "barcode_tag_mapping": {"ASN": "tag"},
        "output_type": "pdfa",
        "pages": 5,
        "language": "",
        "deskew": True,
        "rotate_pages": False,
        "rotate_pages_threshold": 12.5,
        "app_title": "Archive",
        "app_logo": "http://paperless.test/media/logo/logo.png",
    },
    keys_configured=("llm_api_key",),
)
TASK_WIRE: Json = {
    "id": 41,
    "task_id": "8c6f5f2e-6e4d-4a1a-9d45-0a8c0b2f3c11",
    "task_type": "consume_file",
    "task_type_display": "Consume File",
    "trigger_source": "api_upload",
    "trigger_source_display": "API Upload",
    "status": "failure",
    "status_display": "Failure",
    "date_created": "2026-11-17T08:00:00Z",
    "date_started": "2026-11-17T08:00:01Z",
    "date_done": "2026-11-17T08:00:05.5Z",
    "duration_seconds": 4.5,
    "wait_time_seconds": 1.0,
    "input_data": {"filename": "scan.pdf"},
    "result_data": {"error_message": "scan.pdf: not a PDF\nTraceback: secret path"},
    "related_document_ids": [],
    "acknowledged": False,
    "owner": 2,
}
TASK = TaskRow(
    id=41,
    task_id="8c6f5f2e-6e4d-4a1a-9d45-0a8c0b2f3c11",
    task_type="consume_file",
    trigger_source="api_upload",
    state="failure",
    created=datetime(2026, 11, 17, 8, tzinfo=UTC),
    started=datetime(2026, 11, 17, 8, 0, 1, tzinfo=UTC),
    finished=datetime(2026, 11, 17, 8, 0, 5, 500000, tzinfo=UTC),
    duration_seconds=4.5,
    document_ids=(),
    acknowledged=False,
    message="scan.pdf: not a PDF",
)


class Recording:
    """Answers each request with the next scripted response and records method, path, body."""

    def __init__(self, *answers: httpx2.Response) -> None:
        self.answers: list[httpx2.Response] = list(answers)
        self.sent: list[tuple[str, str, Json | None, dict[str, str]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = cast("Json", json.loads(request.content)) if request.content else None
        self.sent.append((request.method, request.url.path, body, dict(request.url.params)))
        return self.answers.pop(0)


def ok(body: object, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json=body)


def page(*rows: Json, next_link: str | None = None, count: int | None = None) -> httpx2.Response:
    body = {
        "count": len(rows) if count is None else count,
        "next": next_link,
        "previous": None,
        "results": list(rows),
    }
    return httpx2.Response(200, json=body)


def forbidden() -> httpx2.Response:
    return httpx2.Response(403, json={"detail": "You do not have permission."})


def not_found() -> httpx2.Response:
    return httpx2.Response(404, json={"detail": "Not found."})


# --- users


async def test_a_user_is_read_without_any_trace_of_the_password(make_client: ClientFactory) -> None:
    server = Recording(ok(USER_WIRE))

    found = await PaperlessAdminReader(make_client(server)).get_user(5)

    assert found == USER
    assert server.sent == [("GET", "/api/users/5/", None, {})]
    assert MASK not in repr(found)
    assert "password" not in found.model_dump_json()


async def test_a_blank_email_address_is_no_address(make_client: ClientFactory) -> None:
    server = Recording(ok({**USER_WIRE, "email": ""}))

    found = await PaperlessAdminReader(make_client(server)).get_user(5)

    assert found.email is None


async def test_a_user_that_does_not_exist_is_not_found(make_client: ClientFactory) -> None:
    server = Recording(not_found())

    with pytest.raises(NotFound) as raised:
        _ = await PaperlessAdminReader(make_client(server)).get_user(99)

    assert raised.value.describe() == (
        "No user 99 exists that this Paperless user may see. "
        "Hint: Find the id with list_users (permissions toolset) or list_taxonomy "
        "(kind user), or pass the username."
    )


async def test_reading_a_user_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminReader(make_client(Recording(forbidden()))).get_user(5)

    assert raised.value.describe() == (
        "The Paperless user may not view users. "
        "Hint: It needs the view_user permission, or must be a superuser."
    )


async def test_a_user_is_created_with_only_the_fields_that_are_set(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok(USER_WIRE, 201))
    values = UserValues(
        username="alice",
        password=SecretStr(PASSWORD),
        group_ids=(1, 3),
        permissions=("view_document",),
        is_staff=False,
    )

    created = await PaperlessAdminWriter(make_client(server)).create_user(values)

    assert created == USER
    assert server.sent == [
        (
            "POST",
            "/api/users/",
            {
                "username": "alice",
                "password": PASSWORD,
                "groups": [1, 3],
                "user_permissions": ["view_document"],
                "is_staff": False,
            },
            {},
        )
    ]


async def test_a_password_made_of_asterisks_is_refused_before_anything_is_sent(
    make_client: ClientFactory,
) -> None:
    server = Recording()
    values = UserValues(username="alice", password=SecretStr("*****"))

    with pytest.raises(Validation) as raised:
        _ = await PaperlessAdminWriter(make_client(server)).create_user(values)

    assert "*****" not in raised.value.describe()
    assert "only of asterisks" in raised.value.message
    assert server.sent == []


async def test_a_rejected_user_never_quotes_the_password_in_the_error_or_the_log(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    server = Recording(ok({"username": ["A user with that username already exists."]}, 400))
    values = UserValues(username="alice", password=SecretStr(PASSWORD))

    with pytest.raises(Validation) as raised:
        _ = await PaperlessAdminWriter(make_client(server)).create_user(values)

    assert raised.value.describe() == (
        "Paperless rejected the request: username: A user with that username already exists."
    )
    assert PASSWORD not in caplog.text
    assert PASSWORD not in repr(values)


async def test_creating_a_user_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminWriter(make_client(Recording(forbidden()))).create_user(
            UserValues(username="alice")
        )

    assert raised.value.describe() == (
        "The Paperless user may not create users. "
        "Hint: It needs the add_user permission, or must be a superuser; only a superuser "
        "may make a superuser or staff user."
    )


async def test_a_user_is_updated_with_only_the_fields_that_are_set(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok(USER_WIRE))
    values = UserValues(is_active=False, email=None, group_ids=())

    changed = await PaperlessAdminWriter(make_client(server)).update_user(5, values)

    assert changed == USER
    assert server.sent == [
        ("PATCH", "/api/users/5/", {"is_active": False, "email": "", "groups": []}, {})
    ]


async def test_updating_a_user_that_does_not_exist_is_not_found(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(NotFound):
        _ = await PaperlessAdminWriter(make_client(Recording(not_found()))).update_user(
            99, UserValues(first_name="x")
        )


async def test_updating_a_user_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminWriter(make_client(Recording(forbidden()))).update_user(
            5, UserValues(first_name="x")
        )

    assert raised.value.describe() == (
        "The Paperless user may not change this user. "
        "Hint: It needs the change_user permission, or must be a superuser; only a superuser "
        "may change a superuser or the superuser and staff status of anyone."
    )


async def test_a_user_is_deleted_and_the_request_is_not_repeated(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(204))

    await PaperlessAdminWriter(make_client(server)).delete_user(5)

    assert server.sent == [("DELETE", "/api/users/5/", None, {})]


async def test_deleting_a_user_that_does_not_exist_is_not_found(make_client: ClientFactory) -> None:
    with pytest.raises(NotFound):
        await PaperlessAdminWriter(make_client(Recording(not_found()))).delete_user(99)


async def test_deleting_a_user_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        await PaperlessAdminWriter(make_client(Recording(forbidden()))).delete_user(5)

    assert raised.value.hint == "It needs the delete_user permission, or must be a superuser."


# --- groups


async def test_a_group_is_read_with_its_permissions(make_client: ClientFactory) -> None:
    server = Recording(ok(GROUP_WIRE))

    found = await PaperlessAdminReader(make_client(server)).get_group(3)

    assert found == GROUP
    assert server.sent == [("GET", "/api/groups/3/", None, {})]


async def test_a_group_that_does_not_exist_is_not_found(make_client: ClientFactory) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await PaperlessAdminReader(make_client(Recording(not_found()))).get_group(99)

    assert raised.value.describe() == (
        "No group 99 exists that this Paperless user may see. "
        "Hint: Find the id with list_groups (permissions toolset) or list_taxonomy "
        "(kind group), or pass the group name."
    )


async def test_reading_a_group_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminReader(make_client(Recording(forbidden()))).get_group(3)

    assert raised.value.describe() == (
        "The Paperless user may not view groups. "
        "Hint: It needs the view_group permission, or must be a superuser."
    )


async def test_a_group_is_created_with_its_permissions(make_client: ClientFactory) -> None:
    server = Recording(ok(GROUP_WIRE, 201))
    values = GroupValues(name="accounting", permissions=("view_document", "view_tag"))

    created = await PaperlessAdminWriter(make_client(server)).create_group(values)

    assert created == GROUP
    assert server.sent == [
        (
            "POST",
            "/api/groups/",
            {"name": "accounting", "permissions": ["view_document", "view_tag"]},
            {},
        )
    ]


async def test_a_group_without_permissions_is_created_with_an_empty_list(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok({**GROUP_WIRE, "permissions": []}, 201))

    _ = await PaperlessAdminWriter(make_client(server)).create_group(GroupValues(name="empty"))

    assert server.sent[0][2] == {"name": "empty", "permissions": []}


async def test_a_rejected_group_passes_paperless_messages_on(make_client: ClientFactory) -> None:
    server = Recording(ok({"name": ["group with this name already exists."]}, 400))

    with pytest.raises(Validation) as raised:
        _ = await PaperlessAdminWriter(make_client(server)).create_group(GroupValues(name="x"))

    assert "name: group with this name already exists." in raised.value.message


async def test_creating_a_group_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminWriter(make_client(Recording(forbidden()))).create_group(
            GroupValues(name="x")
        )

    assert raised.value.describe() == (
        "The Paperless user may not create groups. "
        "Hint: It needs the add_group permission, or must be a superuser."
    )


async def test_a_group_is_updated_with_only_the_fields_that_are_set(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok(GROUP_WIRE))

    _ = await PaperlessAdminWriter(make_client(server)).update_group(
        3, GroupValues(permissions=("view_tag",))
    )

    assert server.sent == [("PATCH", "/api/groups/3/", {"permissions": ["view_tag"]}, {})]


async def test_updating_a_group_that_does_not_exist_is_not_found(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(NotFound):
        _ = await PaperlessAdminWriter(make_client(Recording(not_found()))).update_group(
            99, GroupValues(name="x")
        )


async def test_updating_a_group_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminWriter(make_client(Recording(forbidden()))).update_group(
            3, GroupValues(name="x")
        )

    assert raised.value.hint == "It needs the change_group permission, or must be a superuser."


async def test_a_group_is_deleted_without_a_retry(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(204))

    await PaperlessAdminWriter(make_client(server)).delete_group(3)

    assert server.sent == [("DELETE", "/api/groups/3/", None, {})]


async def test_deleting_a_group_that_does_not_exist_is_not_found(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(NotFound):
        await PaperlessAdminWriter(make_client(Recording(not_found()))).delete_group(99)


async def test_deleting_a_group_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        await PaperlessAdminWriter(make_client(Recording(forbidden()))).delete_group(3)

    assert raised.value.hint == "It needs the delete_group permission, or must be a superuser."


# --- application config


async def test_the_config_holds_only_what_is_set_and_never_a_key(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok([CONFIG_WIRE]))

    found = await PaperlessAdminReader(make_client(server)).get_config()

    assert found == CONFIG
    assert server.sent == [("GET", "/api/config/", None, {})]
    assert KEY_MASK not in repr(found)
    assert "llm_api_key" not in found.settings


async def test_a_config_without_a_row_is_an_error(make_client: ClientFactory) -> None:
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await PaperlessAdminReader(make_client(Recording(ok([])))).get_config()

    assert raised.value.message == "Paperless has no application configuration."


async def test_the_config_is_changed_in_place_with_nulls_resetting_a_setting(
    make_client: ClientFactory,
) -> None:
    changed = {**CONFIG_WIRE, "app_title": "New", "pages": None}
    server = Recording(ok([CONFIG_WIRE]), ok(changed))
    values = ConfigValues(settings={"app_title": "New", "pages": None})

    found = await PaperlessAdminWriter(make_client(server)).update_config(values)

    assert found.settings["app_title"] == "New"
    assert "pages" not in found.settings
    assert server.sent == [
        ("GET", "/api/config/", None, {}),
        ("PATCH", "/api/config/1/", {"app_title": "New", "pages": None}, {}),
    ]


async def test_a_key_goes_into_the_request_and_nowhere_else(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    server = Recording(ok([CONFIG_WIRE]), ok(CONFIG_WIRE))
    values = ConfigValues(secrets={"llm_api_key": SecretStr(KEY)})

    found = await PaperlessAdminWriter(make_client(server)).update_config(values)

    assert server.sent[1][2] == {"llm_api_key": KEY}
    assert found.keys_configured == ("llm_api_key",)
    assert KEY not in repr(values) + repr(found) + caplog.text
    assert KEY_MASK not in found.model_dump_json()


async def test_a_key_made_of_asterisks_is_refused_before_anything_is_sent(
    make_client: ClientFactory,
) -> None:
    server = Recording()
    values = ConfigValues(secrets={"llm_api_key": SecretStr("****")})

    with pytest.raises(Validation) as raised:
        _ = await PaperlessAdminWriter(make_client(server)).update_config(values)

    assert "****" not in raised.value.describe()
    assert server.sent == []


async def test_a_rejected_config_passes_paperless_messages_on(make_client: ClientFactory) -> None:
    server = Recording(
        ok([CONFIG_WIRE]),
        ok({"app_title": ["Not a title Paperless accepts."]}, 400),
    )

    with pytest.raises(Validation) as raised:
        _ = await PaperlessAdminWriter(make_client(server)).update_config(
            ConfigValues(settings={"app_title": "x"})
        )

    assert "app_title: Not a title Paperless accepts." in raised.value.message


async def test_changing_the_config_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok([CONFIG_WIRE]), forbidden())

    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminWriter(make_client(server)).update_config(
            ConfigValues(settings={"pages": 2})
        )

    assert raised.value.describe() == (
        "The Paperless user may not change the application configuration. "
        "Hint: It needs the change_applicationconfiguration permission, or must be a superuser."
    )


async def test_reading_the_config_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    # Paperless asks for view_applicationconfiguration on a read too.
    server = Recording(forbidden())

    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminReader(make_client(server)).get_config()

    assert raised.value.describe() == (
        "The Paperless user may not read the application configuration. "
        "Hint: Paperless needs the view_applicationconfiguration permission (or a superuser) "
        "to read it."
    )


async def test_changing_the_config_needs_to_read_it_first_and_says_so_when_that_is_refused(
    make_client: ClientFactory,
) -> None:
    server = Recording(forbidden())

    with pytest.raises(PermissionDenied, match="read the application configuration"):
        _ = await PaperlessAdminWriter(make_client(server)).update_config(
            ConfigValues(settings={"pages": 2})
        )

    assert server.sent == [("GET", "/api/config/", None, {})]


# --- logs


async def test_the_logs_are_listed_by_name(make_client: ClientFactory) -> None:
    server = Recording(ok(["paperless", "mail", "celery"]))

    found = await PaperlessAdminReader(make_client(server)).list_logs()

    assert found == ("paperless", "mail", "celery")
    assert server.sent == [("GET", "/api/logs/", None, {})]


async def test_listing_the_logs_without_staff_status_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminReader(make_client(Recording(forbidden()))).list_logs()

    assert raised.value.describe() == (
        "The Paperless user may not read the logs. Hint: Only staff users may read the logs."
    )


async def test_the_last_lines_of_a_log_are_read(make_client: ClientFactory) -> None:
    server = Recording(ok(["line one", "line two"]))

    found = await PaperlessAdminReader(make_client(server)).get_log("mail", limit=50)

    assert found == ("line one", "line two")
    assert server.sent == [("GET", "/api/logs/mail/", None, {"limit": "50"})]


async def test_a_log_that_does_not_exist_is_not_found(make_client: ClientFactory) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await PaperlessAdminReader(make_client(Recording(not_found()))).get_log("nope", limit=5)

    assert raised.value.describe() == (
        "No log 'nope' exists. Hint: Call list_logs for the logs this instance has."
    )


async def test_reading_a_log_without_staff_status_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied):
        _ = await PaperlessAdminReader(make_client(Recording(forbidden()))).get_log("mail", limit=5)


# --- tasks


async def test_tasks_are_listed_newest_first_with_their_database_id(
    make_client: ClientFactory,
) -> None:
    link = "http://paperless.test/api/tasks/?page=3"
    server = Recording(page(TASK_WIRE, count=40, next_link=link))

    found = await PaperlessAdminReader(make_client(server)).list_tasks(
        state=None, task_type=None, acknowledged=None, page=2, page_size=10
    )

    assert (found.count, found.next_page, found.rows) == (40, 3, (TASK,))
    assert server.sent == [
        ("GET", "/api/tasks/", None, {"page": "2", "page_size": "10", "ordering": "-date_created"})
    ]


async def test_tasks_are_filtered_by_state_type_and_acknowledgement(
    make_client: ClientFactory,
) -> None:
    server = Recording(page())

    _ = await PaperlessAdminReader(make_client(server)).list_tasks(
        state="failure", task_type="sanity_check", acknowledged=False, page=1, page_size=25
    )

    assert server.sent[0][3] == {
        "page": "1",
        "page_size": "25",
        "ordering": "-date_created",
        "status": "failure",
        "task_type": "sanity_check",
        "acknowledged": "false",
    }


async def test_a_task_without_an_error_message_has_none(make_client: ClientFactory) -> None:
    wire = {**TASK_WIRE, "status": "success", "result_data": {"document_id": 7}}
    wire = {**wire, "related_document_ids": [7], "date_done": None, "duration_seconds": None}
    server = Recording(page(wire))

    found = await PaperlessAdminReader(make_client(server)).list_tasks(
        state=None, task_type=None, acknowledged=None, page=1, page_size=25
    )

    [row] = found.rows
    assert (row.message, row.document_ids, row.finished, row.duration_seconds) == (
        None,
        (7,),
        None,
        None,
    )


async def test_a_task_page_past_the_end_is_not_found(make_client: ClientFactory) -> None:
    with pytest.raises(NotFound) as raised:
        _ = await PaperlessAdminReader(make_client(Recording(not_found()))).list_tasks(
            state=None, task_type=None, acknowledged=None, page=9, page_size=25
        )

    assert raised.value.describe() == (
        "Page 9 of the tasks does not exist. "
        "Hint: List the tasks with page=1; count and next_page tell how far it goes."
    )


async def test_listing_tasks_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminReader(make_client(Recording(forbidden()))).list_tasks(
            state=None, task_type=None, acknowledged=None, page=1, page_size=25
        )

    assert (
        raised.value.hint == "It needs the view_paperlesstask permission, or must be a superuser."
    )


async def test_tasks_are_acknowledged_by_id(make_client: ClientFactory) -> None:
    server = Recording(ok({"result": 2}))

    count = await PaperlessAdminWriter(make_client(server)).acknowledge_tasks([41, 42])

    assert count == 2
    assert server.sent == [("POST", "/api/tasks/acknowledge/", {"tasks": [41, 42]}, {})]


async def test_acknowledging_unknown_tasks_is_a_validation_error(
    make_client: ClientFactory,
) -> None:
    server = Recording(
        ok({"tasks": ["Some tasks in tasks don't exist or were specified twice."]}, 400)
    )

    with pytest.raises(Validation) as raised:
        _ = await PaperlessAdminWriter(make_client(server)).acknowledge_tasks([999])

    assert "don't exist or were specified twice" in raised.value.message


async def test_acknowledging_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminWriter(make_client(Recording(forbidden()))).acknowledge_tasks([1])

    assert raised.value.hint == (
        "It needs the change_paperlesstask permission, or must be a superuser."
    )


async def test_a_task_is_started_and_its_id_returned_without_a_retry(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok({"task_id": "abc-123"}))

    started = await PaperlessAdminWriter(make_client(server)).run_task("sanity_check")

    assert started == "abc-123"
    assert server.sent == [("POST", "/api/tasks/run/", {"task_type": "sanity_check"}, {})]


async def test_starting_a_task_without_the_permission_says_what_is_needed(
    make_client: ClientFactory,
) -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminWriter(make_client(Recording(forbidden()))).run_task("llm_index")

    assert raised.value.describe() == (
        "The Paperless user may not start tasks. Hint: Only a superuser may start tasks."
    )


async def test_a_task_paperless_refuses_to_start_is_a_validation_error(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok({"error": "Task type 'x' cannot be manually triggered"}, 400))

    with pytest.raises(Validation):
        _ = await PaperlessAdminWriter(make_client(server)).run_task("sanity_check")


# --- bulk download


async def test_documents_are_downloaded_as_one_archive(make_client: ClientFactory) -> None:
    sent: list[tuple[str, str, Json]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.method, request.url.path, cast("Json", json.loads(request.content))))
        return httpx2.Response(200, content=b"PK-zip", headers={"content-type": "application/zip"})

    client, _ = streaming(handler)
    del make_client

    found = await PaperlessAdminReader(client).download_documents(
        [1, 2],
        content="both",
        compression="deflated",
        follow_formatting=True,
        max_bytes=100,
    )

    assert (found.content, found.truncated) == (b"PK-zip", False)
    assert sent == [
        (
            "POST",
            "/api/documents/bulk_download/",
            {
                "documents": [1, 2],
                "content": "both",
                "compression": "deflated",
                "follow_formatting": True,
            },
        )
    ]


async def test_an_archive_over_the_limit_is_reported_and_not_returned() -> None:
    client, _ = streaming(lambda _: httpx2.Response(200, content=b"x" * 50))

    found = await PaperlessAdminReader(client).download_documents(
        [1], content="archive", compression="none", follow_formatting=False, max_bytes=10
    )

    assert (found.content, found.truncated) == (b"", True)


async def test_downloading_documents_the_user_may_not_see_says_what_is_needed() -> None:
    client, _ = streaming(lambda _: httpx2.Response(403, text="Insufficient permissions"))

    with pytest.raises(PermissionDenied) as raised:
        _ = await PaperlessAdminReader(client).download_documents(
            [1], content="archive", compression="none", follow_formatting=False, max_bytes=10
        )

    assert raised.value.describe() == (
        "The Paperless user may not download these documents. "
        "Hint: It needs the view_document permission and must be allowed to see every one of "
        "the documents."
    )


async def test_unknown_documents_are_a_validation_error() -> None:
    body = {"documents": ["Some documents in documents don't exist or were specified twice."]}
    client, _ = streaming(lambda _: httpx2.Response(400, json=body))

    with pytest.raises(Validation) as raised:
        _ = await PaperlessAdminReader(client).download_documents(
            [999], content="archive", compression="none", follow_formatting=False, max_bytes=10
        )

    assert "don't exist or were specified twice" in raised.value.message


async def test_an_archive_that_takes_too_long_is_a_timeout() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        message = "slow"
        raise httpx2.ReadTimeout(message)

    client, _ = streaming(handler)

    with pytest.raises(UpstreamTimeout):
        _ = await PaperlessAdminReader(client).download_documents(
            [1], content="archive", compression="none", follow_formatting=False, max_bytes=10
        )


@pytest.mark.parametrize(
    "result", [None, {"error_message": None}, {"error_message": " \n "}, "text"]
)
async def test_a_task_whose_result_has_no_error_text_has_no_message(
    make_client: ClientFactory, result: object
) -> None:
    server = Recording(page({**TASK_WIRE, "result_data": result}))

    found = await PaperlessAdminReader(make_client(server)).list_tasks(
        state=None, task_type=None, acknowledged=None, page=1, page_size=25
    )

    assert found.rows[0].message is None


# --- exact bodies, texts and request options (mutation tests)


async def test_every_user_field_reaches_the_body_and_none_values_are_left_out(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok(USER_WIRE, 201), ok(USER_WIRE))
    writer = PaperlessAdminWriter(make_client(server))
    everything = UserValues(
        username="alice",
        email="a@x.org",
        first_name="Alice",
        last_name="Archer",
        password=SecretStr(PASSWORD),
        is_active=True,
        is_staff=True,
        is_superuser=True,
        group_ids=(1,),
        permissions=("view_tag",),
    )

    _ = await writer.create_user(everything)
    _ = await writer.update_user(5, UserValues(group_ids=None, permissions=None, password=None))

    assert server.sent[0][2] == {
        "username": "alice",
        "email": "a@x.org",
        "first_name": "Alice",
        "last_name": "Archer",
        "password": PASSWORD,
        "is_active": True,
        "is_staff": True,
        "is_superuser": True,
        "groups": [1],
        "user_permissions": ["view_tag"],
    }
    assert server.sent[1][2] == {}


async def test_secrets_made_of_asterisks_are_refused_with_the_exact_text(
    make_client: ClientFactory,
) -> None:
    writer = PaperlessAdminWriter(make_client(Recording()))

    with pytest.raises(Validation) as password:
        _ = await writer.create_user(UserValues(username="a", password=SecretStr("**")))
    with pytest.raises(Validation) as key:
        _ = await writer.update_config(ConfigValues(secrets={"llm_api_key": SecretStr("**")}))

    assert password.value.describe() == (
        "The password cannot consist only of asterisks: Paperless takes that to mean 'keep the "
        "saved one'. Hint: Leave the password out to keep the saved one."
    )
    assert key.value.describe() == (
        "The API key cannot consist only of asterisks: Paperless takes that to mean 'keep the "
        "saved one'. Hint: Leave the API key out to keep the saved one."
    )


@pytest.mark.parametrize("kind", ["user", "group"])
async def test_a_missing_user_or_group_names_its_id_in_every_write(
    make_client: ClientFactory, kind: str
) -> None:
    writer = PaperlessAdminWriter(make_client(Recording(not_found(), not_found())))
    texts: list[str] = []
    for call in (
        (
            writer.update_user(7, UserValues(first_name="x"))
            if kind == "user"
            else writer.update_group(7, GroupValues(name="x"))
        ),
        writer.delete_user(7) if kind == "user" else writer.delete_group(7),
    ):
        with pytest.raises(NotFound) as raised:
            await call
        texts.append(raised.value.message)

    assert texts == [f"No {kind} 7 exists that this Paperless user may see."] * 2


@pytest.mark.parametrize("kind", ["user", "group"])
async def test_a_deletion_is_never_retried(make_client: ClientFactory, kind: str) -> None:
    server = Recording(httpx2.Response(503), httpx2.Response(204))
    writer = PaperlessAdminWriter(make_client(server))

    with pytest.raises(UpstreamUnavailable):
        await (writer.delete_user(7) if kind == "user" else writer.delete_group(7))

    assert len(server.sent) == 1


async def test_the_acknowledged_filter_is_sent_as_true_or_false(
    make_client: ClientFactory,
) -> None:
    server = Recording(page(), page())
    reader = PaperlessAdminReader(make_client(server))

    for flag in (True, False):
        _ = await reader.list_tasks(
            state=None, task_type=None, acknowledged=flag, page=1, page_size=5
        )

    assert [sent[3]["acknowledged"] for sent in server.sent] == ["true", "false"]


class Spy(PaperlessClient):
    """A client that records the limits of a streamed POST instead of sending it."""

    seen: dict[str, object] = {}  # noqa: RUF012 -- a test spy, one instance per test

    @override
    async def stream_post(
        self, path: str, *, json: object, max_bytes: int, max_seconds: float
    ) -> Streamed:
        self.seen = {"path": path, "max_bytes": max_bytes, "max_seconds": max_seconds}
        return Streamed(b"zip", truncated=False)


async def test_the_download_has_its_own_time_limit() -> None:
    http = httpx2.AsyncClient(base_url="http://paperless.test")
    spy = Spy(http, StaticTokenProvider("t"), retry=RetryPolicy(), upload_timeout=1.0)

    _ = await PaperlessAdminReader(spy).download_documents(
        [1], content="archive", compression="none", follow_formatting=False, max_bytes=77
    )

    assert spy.seen == {
        "path": "/api/documents/bulk_download/",
        "max_bytes": 77,
        "max_seconds": DOWNLOAD_SECONDS,
    }
    await http.aclose()


async def test_a_group_update_sends_only_what_is_set(make_client: ClientFactory) -> None:
    server = Recording(ok(GROUP_WIRE), ok(GROUP_WIRE))
    writer = PaperlessAdminWriter(make_client(server))

    _ = await writer.update_group(3, GroupValues(name="x"))
    _ = await writer.update_group(3, GroupValues(name="x", permissions=None))

    assert [sent[2] for sent in server.sent] == [{"name": "x"}, {"name": "x"}]


async def test_the_server_account_is_read_from_the_ui_settings(make_client: ClientFactory) -> None:
    body: Json = {
        "user": {"id": 2, "username": "admin", "is_staff": True, "is_superuser": True},
        "settings": {},
        "permissions": [],
    }
    server = Recording(ok(body))

    found = await PaperlessAdminReader(make_client(server)).get_current_user_id()

    assert found == 2
    assert server.sent == [("GET", "/api/ui_settings/", None, {})]


async def test_inherited_permissions_are_codenames_without_duplicates_and_empty_for_superusers(
    make_client: ClientFactory,
) -> None:
    wire = {
        **USER_WIRE,
        "inherited_permissions": ["auth.view_user", "documents.view_tag", "change_tag"] * 2,
    }
    superuser = {**USER_WIRE, "is_superuser": True, "inherited_permissions": ["auth.view_user"]}
    server = Recording(ok(wire), ok(superuser))
    reader = PaperlessAdminReader(make_client(server))

    plain = await reader.get_user(5)
    boss = await reader.get_user(5)

    assert plain.inherited_permissions == ("change_tag", "view_tag", "view_user")
    assert boss.inherited_permissions == ()


@pytest.mark.parametrize("name", ["../users", "a/b", "Paperless", "", "log.txt", "mail "])
async def test_a_log_name_that_is_not_a_plain_name_is_not_found_and_nothing_is_sent(
    make_client: ClientFactory, name: str
) -> None:
    server = Recording()

    with pytest.raises(NotFound) as raised:
        _ = await PaperlessAdminReader(make_client(server)).get_log(name, limit=5)

    assert raised.value.message == f"No log '{name}' exists."
    assert server.sent == []


class Slow(PaperlessClient):
    """A client whose streamed POST ends early, by time or by size."""

    result: Streamed = Streamed(b"", truncated=False)

    @override
    async def stream_post(
        self, path: str, *, json: object, max_bytes: int, max_seconds: float
    ) -> Streamed:
        return self.result


@pytest.mark.parametrize(
    ("result", "timed_out"),
    [(Streamed(b"x" * 77, truncated=True), False), (Streamed(b"x" * 5, truncated=True), True)],
)
async def test_a_cut_off_archive_says_whether_it_was_size_or_time(
    result: Streamed, *, timed_out: bool
) -> None:
    http = httpx2.AsyncClient(base_url="http://paperless.test")
    slow = Slow(http, StaticTokenProvider("t"), retry=RetryPolicy(), upload_timeout=1.0)
    slow.result = result

    found = await PaperlessAdminReader(slow).download_documents(
        [1], content="archive", compression="none", follow_formatting=False, max_bytes=77
    )

    assert (found.content, found.truncated, found.timed_out) == (b"", True, timed_out)
    await http.aclose()
