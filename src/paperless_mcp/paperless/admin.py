"""AdminReader and AdminWriter on the user, group, config, log, task and bulk download endpoints.

Probed on 3.4.2 (PLMCP-31):

- Users and groups answer 404 for an unknown id and 400 with DRF field messages for bad input.
  A user's ``password`` is masked in every answer (the mask is as long as the hash); the row model
  here does not declare it, so neither the password nor its mask gets past the parse. A password
  made of ``*`` only means "unchanged" on a write and is ignored, so it is refused up front.
  ``user_permissions`` and a group's ``permissions`` are permission codenames without the app
  label; an unknown one is a 400. Deleting a group in use removes it from its members.
- ``GET /api/config/`` is a plain list with one row that any signed-in user may read (DRF asks
  for no permission on a GET); a PATCH needs ``change_applicationconfiguration``.
  The list has one row. The three API keys are masked as long as the
  key, so even the mask gives the length away: the row model turns each into whether it holds a
  value and drops it. Settings that are null follow the server's environment. A PATCH of
  ``null`` resets a setting; names Paperless does not know are ignored without an error.
- ``GET /api/logs/`` lists the existing log names (staff only), ``/api/logs/{name}/?limit=N``
  the last N lines; a name that does not exist (or has no file yet) is a 404.
- ``/api/tasks/`` is paginated (v10) and filters ``status``, ``task_type`` and ``acknowledged``;
  ``acknowledge`` takes database ids and refuses ids that do not exist (400). ``run`` is for
  superusers only and starts ``train_classifier``, ``sanity_check`` or ``llm_index``; any other
  type is a 400. Its answer is the Celery id, not the database id.
- ``POST /api/documents/bulk_download/`` answers the ZIP at once (200, no file name); a document
  that does not exist is a 400, one the user may not see a 403 (text) for the whole request, and
  an empty list an empty archive.
"""

import re
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, ClassVar, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, JsonValue, RootModel

from paperless_mcp.domain.admin import (
    CONFIG_READ_ONLY,
    CONFIG_SECRETS,
    CONFIG_SETTINGS,
    AppConfigRecord,
    ArchiveFile,
    GroupRecord,
    GroupValues,
    TaskRow,
    TaskRowPage,
    UserRecord,
    UserValues,
    check_secret,
)
from paperless_mcp.domain.errors import NotFound, PaperlessMcpError, PermissionDenied
from paperless_mcp.domain.tasks import TaskState
from paperless_mcp.paperless.pagination import Page

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from paperless_mcp.domain.admin import (
        ArchiveContent,
        Compression,
        ConfigValues,
        RunnableTask,
        TaskType,
    )
    from paperless_mcp.paperless.client import PaperlessClient

USERS: Final = "/api/users/"
GROUPS: Final = "/api/groups/"
CONFIG: Final = "/api/config/"
UI_SETTINGS: Final = "/api/ui_settings/"
LOGS: Final = "/api/logs/"
TASKS: Final = "/api/tasks/"
BULK_DOWNLOAD: Final = "/api/documents/bulk_download/"
# Building and sending a large archive takes a while; the web worker holds the request meanwhile.
DOWNLOAD_SECONDS: Final = 120.0
# The first line of a failed task's message: later lines can be a traceback with server paths.
MAX_MESSAGE: Final = 300
# A log name is a plain word: anything else would change the path of the request.
LOG_NAME: Final = re.compile(r"[a-z0-9_]+")

# What each refusal tells the caller: (what the user may not do, what it needs).
VIEW_USER: Final = ("view users", "It needs the view_user permission, or must be a superuser.")
ADD_USER: Final = (
    "create users",
    (
        "It needs the add_user permission, or must be a superuser; only a superuser may make a "
        "superuser or staff user."
    ),
)
CHANGE_USER: Final = (
    "change this user",
    (
        "It needs the change_user permission, or must be a superuser; only a superuser may "
        "change a superuser or the superuser and staff status of anyone."
    ),
)
DELETE_USER: Final = (
    "delete this user",
    "It needs the delete_user permission, or must be a superuser.",
)
VIEW_GROUP: Final = (
    "view groups",
    "It needs the view_group permission, or must be a superuser.",
)
ADD_GROUP: Final = (
    "create groups",
    "It needs the add_group permission, or must be a superuser.",
)
CHANGE_GROUP: Final = (
    "change this group",
    "It needs the change_group permission, or must be a superuser.",
)
DELETE_GROUP: Final = (
    "delete this group",
    "It needs the delete_group permission, or must be a superuser.",
)
READ_CONFIG: Final = (
    "read the application configuration",
    (
        "Paperless 3.5 and later need the view_applicationconfiguration permission (or a "
        "superuser); 3.4 lets every signed-in user read it."
    ),
)
CHANGE_CONFIG: Final = (
    "change the application configuration",
    "It needs the change_applicationconfiguration permission, or must be a superuser.",
)
READ_LOGS: Final = ("read the logs", "Only staff users may read the logs.")
VIEW_TASKS: Final = (
    "view tasks",
    "It needs the view_paperlesstask permission, or must be a superuser.",
)
ACK_TASKS: Final = (
    "acknowledge tasks",
    "It needs the change_paperlesstask permission, or must be a superuser.",
)
RUN_TASKS: Final = ("start tasks", "Only a superuser may start tasks.")
DOWNLOAD: Final = (
    "download these documents",
    "It needs the view_document permission and must be allowed to see every one of the documents.",
)


def denied(action: tuple[str, str]) -> PermissionDenied:
    """The error for a 403: what the user may not do, and what it would need."""
    what, hint = action
    return PermissionDenied(f"The Paperless user may not {what}.", hint=hint)


class Refused:
    """Turn a 403 into a ``PermissionDenied`` that says what the user may not do and what helps."""

    def __init__(self, action: tuple[str, str]) -> None:
        """Guard a block that does what ``action`` names."""
        self._action: tuple[str, str] = action

    def __enter__(self) -> None:
        """Start the guarded block."""

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """Replace a ``PermissionDenied`` of the block with one that carries the hint."""
        if isinstance(error, PermissionDenied):
            raise denied(self._action) from None


def unseen_user(user_id: int) -> NotFound:
    """The error for a user that does not exist (or that this Paperless user may not see)."""
    return NotFound(
        f"No user {user_id} exists that this Paperless user may see.",
        hint=(
            "Find the id with list_users (permissions toolset) or list_taxonomy (kind user), "
            "or pass the username."
        ),
    )


def unseen_group(group_id: int) -> NotFound:
    """The error for a group that does not exist (or that this Paperless user may not see)."""
    return NotFound(
        f"No group {group_id} exists that this Paperless user may see.",
        hint=(
            "Find the id with list_groups (permissions toolset) or list_taxonomy "
            "(kind group), or pass the group name."
        ),
    )


def unseen_log(name: str) -> NotFound:
    """The error for a log that does not exist."""
    return NotFound(
        f"No log '{name}' exists.", hint="Call list_logs for the logs this instance has."
    )


def beyond_last_page(page: int) -> NotFound:
    """The error for a page past the end of the tasks."""
    return NotFound(
        f"Page {page} of the tasks does not exist.",
        hint="List the tasks with page=1; count and next_page tell how far it goes.",
    )


class _User(BaseModel):
    """A user as Paperless sends it. The ``password`` it also sends is not read at all."""

    id: int
    username: str
    email: str
    first_name: str
    last_name: str
    is_active: bool
    is_staff: bool
    is_superuser: bool
    groups: list[int]
    user_permissions: list[str]
    inherited_permissions: list[str]
    date_joined: datetime
    is_mfa_enabled: bool


class _Me(BaseModel):
    id: int


class _UiSettings(BaseModel):
    user: _Me


class _Group(BaseModel):
    id: int
    name: str
    permissions: list[str]


def _configured(value: object) -> bool:
    return value is not None


type _Saved = Annotated[bool, BeforeValidator(_configured)]


class _Config(BaseModel):
    """The configuration row. The API keys it sends are turned into flags and dropped."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: int
    externally_configured_variables: list[str]
    llm_api_key: _Saved = False
    llm_embedding_api_key: _Saved = False
    remote_ocr_api_key: _Saved = False


class _Configs(RootModel[list[_Config]]):
    pass


class _Lines(RootModel[list[str]]):
    pass


class _Task(BaseModel):
    id: int
    task_id: str
    task_type: str
    trigger_source: str
    status: TaskState
    date_created: datetime
    date_started: datetime | None
    date_done: datetime | None
    duration_seconds: float | None
    result_data: JsonValue
    related_document_ids: list[int]
    acknowledged: bool


class _Acknowledged(BaseModel):
    result: int


class _Started(BaseModel):
    task_id: str


def _codenames(row: _User) -> tuple[str, ...]:
    """What the groups grant, as codenames without the app label; none for a superuser."""
    if row.is_superuser:
        return ()
    return tuple(sorted({entry.rpartition(".")[2] for entry in row.inherited_permissions}))


def _user(row: _User) -> UserRecord:
    return UserRecord(
        id=row.id,
        username=row.username,
        email=row.email or None,
        first_name=row.first_name,
        last_name=row.last_name,
        is_active=row.is_active,
        is_staff=row.is_staff,
        is_superuser=row.is_superuser,
        group_ids=tuple(row.groups),
        permissions=tuple(row.user_permissions),
        inherited_permissions=_codenames(row),
        date_joined=row.date_joined,
        mfa_enabled=row.is_mfa_enabled,
    )


def _group(row: _Group) -> GroupRecord:
    return GroupRecord(id=row.id, name=row.name, permissions=tuple(row.permissions))


def _config(row: _Config) -> AppConfigRecord:
    wanted = {*CONFIG_SETTINGS, *CONFIG_READ_ONLY}
    extra: dict[str, JsonValue] = row.model_extra or {}
    saved = {
        "llm_api_key": row.llm_api_key,
        "llm_embedding_api_key": row.llm_embedding_api_key,
        "remote_ocr_api_key": row.remote_ocr_api_key,
    }
    return AppConfigRecord(
        id=row.id,
        externally_configured_variables=tuple(row.externally_configured_variables),
        settings={
            name: value for name, value in extra.items() if name in wanted and value is not None
        },
        keys_configured=tuple(name for name in CONFIG_SECRETS if saved[name]),
    )


def _message(result: JsonValue) -> str | None:
    """The first line of the error message of a failed task, cut short."""
    if not isinstance(result, dict):
        return None
    message = result.get("error_message")
    if not isinstance(message, str):
        return None
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    return lines[0][:MAX_MESSAGE] if lines else None


def _task(row: _Task) -> TaskRow:
    return TaskRow(
        id=row.id,
        task_id=row.task_id,
        task_type=row.task_type,
        trigger_source=row.trigger_source,
        state=row.status,
        created=row.date_created,
        started=row.date_started,
        finished=row.date_done,
        duration_seconds=row.duration_seconds,
        document_ids=tuple(row.related_document_ids),
        acknowledged=row.acknowledged,
        message=_message(row.result_data),
    )


def _user_body(values: UserValues) -> dict[str, object]:
    """The request body of the fields set in ``values``; the only place a password is unwrapped."""
    sent = values.model_fields_set
    body: dict[str, object] = {}
    plain = ("username", "first_name", "last_name", "is_active", "is_staff", "is_superuser")
    for name in plain:
        if name in sent:
            body[name] = getattr(values, name)
    if "email" in sent:
        body["email"] = values.email or ""
    if "group_ids" in sent and values.group_ids is not None:
        body["groups"] = list(values.group_ids)
    if "permissions" in sent and values.permissions is not None:
        body["user_permissions"] = list(values.permissions)
    if "password" in sent and values.password is not None:
        secret = values.password.get_secret_value()
        check_secret(secret, "password")
        body["password"] = secret
    return body


def _group_body(values: GroupValues, *, create: bool = False) -> dict[str, object]:
    """The request body of the fields set in ``values``; a new group always has a list."""
    sent = values.model_fields_set
    body: dict[str, object] = {}
    if "name" in sent:
        body["name"] = values.name
    if "permissions" in sent and values.permissions is not None:
        body["permissions"] = list(values.permissions)
    elif create:
        body["permissions"] = []
    return body


class PaperlessAdminReader:
    """``AdminReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def get_user(self, user_id: int) -> UserRecord:
        """See ``AdminReader.get_user``."""
        with Refused(VIEW_USER):
            try:
                return _user(await self._client.get(f"{USERS}{user_id}/", _User))
            except NotFound:
                raise unseen_user(user_id) from None

    async def get_current_user_id(self) -> int:
        """See ``AdminReader.get_current_user_id``: the ui settings, which carry the user."""
        return (await self._client.get(UI_SETTINGS, _UiSettings)).user.id

    async def get_group(self, group_id: int) -> GroupRecord:
        """See ``AdminReader.get_group``."""
        with Refused(VIEW_GROUP):
            try:
                return _group(await self._client.get(f"{GROUPS}{group_id}/", _Group))
            except NotFound:
                raise unseen_group(group_id) from None

    async def get_config(self) -> AppConfigRecord:
        """See ``AdminReader.get_config``."""
        return await _read_config(self._client)

    async def list_logs(self) -> tuple[str, ...]:
        """See ``AdminReader.list_logs``."""
        with Refused(READ_LOGS):
            return tuple((await self._client.get(LOGS, _Lines)).root)

    async def get_log(self, name: str, *, limit: int) -> tuple[str, ...]:
        """See ``AdminReader.get_log``."""
        if not LOG_NAME.fullmatch(name):
            raise unseen_log(name)
        with Refused(READ_LOGS):
            try:
                found = await self._client.get(f"{LOGS}{name}/", _Lines, params={"limit": limit})
            except NotFound:
                raise unseen_log(name) from None
        return tuple(found.root)

    async def list_tasks(
        self,
        *,
        state: TaskState | None,
        task_type: TaskType | None,
        acknowledged: bool | None,
        page: int,
        page_size: int,
    ) -> TaskRowPage:
        """See ``AdminReader.list_tasks``."""
        params: dict[str, str | int] = {
            "page": page,
            "page_size": page_size,
            "ordering": "-date_created",
        }
        if state is not None:
            params["status"] = state
        if task_type is not None:
            params["task_type"] = task_type
        if acknowledged is not None:
            params["acknowledged"] = "true" if acknowledged else "false"
        with Refused(VIEW_TASKS):
            try:
                found = await self._client.get(TASKS, Page[_Task], params=params)
            except NotFound:
                raise beyond_last_page(page) from None
        return TaskRowPage(
            count=found.count,
            next_page=found.next_page,
            rows=tuple(_task(row) for row in found.results),
        )

    async def download_documents(
        self,
        document_ids: Sequence[int],
        *,
        content: ArchiveContent,
        compression: Compression,
        follow_formatting: bool,
        max_bytes: int,
    ) -> ArchiveFile:
        """See ``AdminReader.download_documents``."""
        body = {
            "documents": list(document_ids),
            "content": content,
            "compression": compression,
            "follow_formatting": follow_formatting,
        }
        with Refused(DOWNLOAD):
            found = await self._client.stream_post(
                BULK_DOWNLOAD, json=body, max_bytes=max_bytes, max_seconds=DOWNLOAD_SECONDS
            )
        # Streamed keeps exactly ``max_bytes`` bytes when the size limit cut it; fewer: the time.
        timed_out = found.truncated and len(found.content) < max_bytes
        return ArchiveFile(
            content=b"" if found.truncated else found.content,
            truncated=found.truncated,
            timed_out=timed_out,
        )


async def _read_config(client: PaperlessClient) -> AppConfigRecord:
    with Refused(READ_CONFIG):
        rows = (await client.get(CONFIG, _Configs)).root
    if not rows:
        message = "Paperless has no application configuration."
        raise PaperlessMcpError(message)
    return _config(rows[0])


class PaperlessAdminWriter:
    """``AdminWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def create_user(self, values: UserValues) -> UserRecord:
        """See ``AdminWriter.create_user``."""
        body = _user_body(values)
        with Refused(ADD_USER):
            return _user(await self._client.call("POST", USERS, _User, json=body))

    async def update_user(self, user_id: int, values: UserValues) -> UserRecord:
        """See ``AdminWriter.update_user``."""
        body = _user_body(values)
        with Refused(CHANGE_USER):
            try:
                row = await self._client.call("PATCH", f"{USERS}{user_id}/", _User, json=body)
            except NotFound:
                raise unseen_user(user_id) from None
        return _user(row)

    async def delete_user(self, user_id: int) -> None:
        """See ``AdminWriter.delete_user``."""
        with Refused(DELETE_USER):
            try:
                _ = await self._client.request("DELETE", f"{USERS}{user_id}/", retry=False)
            except NotFound:
                raise unseen_user(user_id) from None

    async def create_group(self, values: GroupValues) -> GroupRecord:
        """See ``AdminWriter.create_group``."""
        body = _group_body(values, create=True)
        with Refused(ADD_GROUP):
            return _group(await self._client.call("POST", GROUPS, _Group, json=body))

    async def update_group(self, group_id: int, values: GroupValues) -> GroupRecord:
        """See ``AdminWriter.update_group``."""
        body = _group_body(values)
        with Refused(CHANGE_GROUP):
            try:
                row = await self._client.call("PATCH", f"{GROUPS}{group_id}/", _Group, json=body)
            except NotFound:
                raise unseen_group(group_id) from None
        return _group(row)

    async def delete_group(self, group_id: int) -> None:
        """See ``AdminWriter.delete_group``."""
        with Refused(DELETE_GROUP):
            try:
                _ = await self._client.request("DELETE", f"{GROUPS}{group_id}/", retry=False)
            except NotFound:
                raise unseen_group(group_id) from None

    async def update_config(self, values: ConfigValues) -> AppConfigRecord:
        """See ``AdminWriter.update_config``."""
        body: dict[str, object] = dict(values.settings)
        for name, secret in values.secrets.items():
            check_secret(secret.get_secret_value(), "API key")
            body[name] = secret.get_secret_value()
        current = await _read_config(self._client)
        with Refused(CHANGE_CONFIG):
            row = await self._client.call("PATCH", f"{CONFIG}{current.id}/", _Config, json=body)
        return _config(row)

    async def acknowledge_tasks(self, task_ids: Sequence[int]) -> int:
        """See ``AdminWriter.acknowledge_tasks``."""
        with Refused(ACK_TASKS):
            done = await self._client.call(
                "POST", f"{TASKS}acknowledge/", _Acknowledged, json={"tasks": list(task_ids)}
            )
        return done.result

    async def run_task(self, task_type: RunnableTask) -> str:
        """See ``AdminWriter.run_task``: a POST, which the client never retries."""
        with Refused(RUN_TASKS):
            started = await self._client.call(
                "POST", f"{TASKS}run/", _Started, json={"task_type": task_type}
            )
        return started.task_id
