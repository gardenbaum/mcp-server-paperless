"""InstanceReader on /api/statistics/, /api/status/, /api/ui_settings/ and next_asn of documents.

Versions come from the ``X-Version`` and ``X-Api-Version`` headers Paperless sends on every
response, so every user gets them. A missing ``X-Version`` is an unknown release, no
error. ``/api/remote_version/`` (Paperless asks GitHub) is read only by ``check_for_update``;
``/api/profile/`` (it returns the user's API token) is deliberately not used.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, JsonValue, RootModel

from mcp_server_paperless.domain.errors import PaperlessMcpError, PermissionDenied
from mcp_server_paperless.domain.instance import (
    NO_ANSWER,
    NOT_READ,
    SWITCHED_OFF,
    ComponentStatus,
    CurrentUser,
    DatabaseStatus,
    FileTypeCount,
    InstanceOverview,
    InstanceStatistics,
    ServiceStatus,
    StatisticsReport,
    StatusReport,
    SystemReport,
    SystemStatus,
    TaskTotals,
    TimedStatus,
    UpdateCheck,
)
from mcp_server_paperless.domain.urls import text_without_credentials
from mcp_server_paperless.paperless.client import parse, unexpected

if TYPE_CHECKING:
    from mcp_server_paperless.paperless.client import PaperlessClient


class _FileType(BaseModel):
    mime_type: str
    mime_type_count: int


# Seconds the check for an update may take.
UPDATE_SECONDS: Final = 10.0
# The release Paperless names when it could not ask for the newest one.
NO_VERSION: Final = "0.0.0"
# The values of PAPERLESS_ENABLE_UPDATE_CHECK that do not switch the check off.
UPDATE_ON: Final[tuple[JsonValue, ...]] = (True, "default")


class _Statistics(BaseModel):
    documents_total: int
    documents_inbox: int | None
    tag_count: int
    correspondent_count: int
    document_type_count: int
    storage_path_count: int


class _FullStatistics(_Statistics):
    inbox_tags: list[int] | None
    document_file_type_counts: list[_FileType]
    character_count: int
    current_asn: int


class _Storage(BaseModel):
    total: int
    available: int


class _Database(BaseModel):
    status: str
    error: str | None


class _Tasks(BaseModel):
    redis_status: str
    redis_error: str | None
    celery_status: str
    celery_error: str | None
    index_status: str
    index_error: str | None
    classifier_status: str
    classifier_error: str | None
    sanity_check_status: str
    sanity_check_error: str | None
    llmindex_status: str
    llmindex_error: str | None


class _Status(BaseModel):
    install_type: str
    storage: _Storage
    database: _Database
    tasks: _Tasks


class _Migrations(BaseModel):
    latest_migration: str | None
    unapplied_migrations: list[str]


class _FullDatabase(_Database):
    type: str
    url: str
    migration_status: _Migrations


class _Summary(BaseModel):
    days: int
    total_count: int
    pending_count: int
    success_count: int
    failure_count: int


class _FullTasks(_Tasks):
    redis_url: str | None
    celery_url: str | None
    index_last_modified: datetime | None
    classifier_last_trained: datetime | None
    sanity_check_last_run: datetime | None
    llmindex_last_modified: datetime | None
    summary: _Summary


class _FullStatus(BaseModel):
    pngx_version: str
    server_os: str
    install_type: str
    storage: _Storage
    database: _FullDatabase
    tasks: _FullTasks


class _User(BaseModel):
    id: int
    username: str
    is_superuser: bool


class _Settings(BaseModel):
    ai_enabled: bool | None = None


class _UiSettings(BaseModel):
    user: _User
    settings: _Settings


class _Checking(BaseModel):
    backend_setting: JsonValue


class _Checks(BaseModel):
    update_checking: _Checking


class _UpdateSettings(BaseModel):
    """What PAPERLESS_ENABLE_UPDATE_CHECK says; Paperless adds it to the settings of every user."""

    settings: _Checks


class _RemoteVersion(BaseModel):
    version: str
    update_available: bool


class _Number(RootModel[int]):
    """The next archive serial number, a bare integer."""


def _component(status: str, error: str | None) -> ComponentStatus:
    shown = None if error is None else text_without_credentials(error)
    return ComponentStatus(status=status, error=shown)


def _system_status(wire: _Status) -> SystemStatus:
    tasks = wire.tasks
    return SystemStatus(
        install_type=wire.install_type,
        storage_total_bytes=wire.storage.total,
        storage_available_bytes=wire.storage.available,
        database=_component(wire.database.status, wire.database.error),
        redis=_component(tasks.redis_status, tasks.redis_error),
        celery=_component(tasks.celery_status, tasks.celery_error),
        search_index=_component(tasks.index_status, tasks.index_error),
        classifier=_component(tasks.classifier_status, tasks.classifier_error),
        sanity_check=_component(tasks.sanity_check_status, tasks.sanity_check_error),
        llm_index=_component(tasks.llmindex_status, tasks.llmindex_error),
    )


def _status_report(wire: _FullStatus) -> StatusReport:
    database, tasks = wire.database, wire.tasks
    return StatusReport(
        paperless_version=wire.pngx_version,
        server_os=wire.server_os,
        install_type=wire.install_type,
        storage_total_bytes=wire.storage.total,
        storage_available_bytes=wire.storage.available,
        database=DatabaseStatus(
            status=database.status,
            error=database.error,
            type=database.type,
            name=database.url,
            latest_migration=database.migration_status.latest_migration,
            unapplied_migrations=tuple(database.migration_status.unapplied_migrations),
        ),
        redis=ServiceStatus(
            status=tasks.redis_status, error=tasks.redis_error, address=tasks.redis_url
        ),
        celery=ServiceStatus(
            status=tasks.celery_status, error=tasks.celery_error, address=tasks.celery_url
        ),
        search_index=TimedStatus(
            status=tasks.index_status, error=tasks.index_error, last=tasks.index_last_modified
        ),
        classifier=TimedStatus(
            status=tasks.classifier_status,
            error=tasks.classifier_error,
            last=tasks.classifier_last_trained,
        ),
        sanity_check=TimedStatus(
            status=tasks.sanity_check_status,
            error=tasks.sanity_check_error,
            last=tasks.sanity_check_last_run,
        ),
        llm_index=TimedStatus(
            status=tasks.llmindex_status,
            error=tasks.llmindex_error,
            last=tasks.llmindex_last_modified,
        ),
        tasks=TaskTotals(
            days=tasks.summary.days,
            total=tasks.summary.total_count,
            pending=tasks.summary.pending_count,
            success=tasks.summary.success_count,
            failure=tasks.summary.failure_count,
        ),
    )


class PaperlessInstanceReader:
    """``InstanceReader`` and ``SystemReportReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def get_overview(self) -> InstanceOverview:
        """See ``InstanceReader.get_overview``."""
        response = await self._client.request("GET", "/api/statistics/")
        statistics = parse(response, _Statistics)
        try:
            api_version = int(response.headers["X-Api-Version"])
        except (KeyError, ValueError) as error:
            raise unexpected(response) from error
        following = await self._unless_forbidden("/api/documents/next_asn/", _Number)
        status = await self._unless_forbidden("/api/status/", _Status)
        ui_settings = await self._unless_forbidden("/api/ui_settings/", _UiSettings)
        user = ui_settings.user if ui_settings else None
        return InstanceOverview(
            paperless_version=response.headers.get("X-Version") or "unknown",
            api_version=api_version,
            statistics=InstanceStatistics(
                documents=statistics.documents_total,
                inbox_documents=statistics.documents_inbox,
                tags=statistics.tag_count,
                correspondents=statistics.correspondent_count,
                document_types=statistics.document_type_count,
                storage_paths=statistics.storage_path_count,
            ),
            system_status=_system_status(status) if status else None,
            current_user=(
                CurrentUser(id=user.id, username=user.username, is_superuser=user.is_superuser)
                if user
                else None
            ),
            ai_enabled=ui_settings.settings.ai_enabled if ui_settings else None,
            next_asn=following.root if following else None,
        )

    async def get_system_report(self) -> SystemReport:
        """See ``SystemReportReader.get_system_report``."""
        found = await self._client.get("/api/statistics/", _FullStatistics)
        status = await self._unless_forbidden("/api/status/", _FullStatus)
        return SystemReport(
            statistics=StatisticsReport(
                documents=found.documents_total,
                inbox_documents=found.documents_inbox,
                inbox_tags=tuple(found.inbox_tags or ()),
                file_types=tuple(
                    FileTypeCount(mime_type=row.mime_type, documents=row.mime_type_count)
                    for row in found.document_file_type_counts
                ),
                characters=found.character_count,
                tags=found.tag_count,
                correspondents=found.correspondent_count,
                document_types=found.document_type_count,
                storage_paths=found.storage_path_count,
                highest_serial_number=found.current_asn,
            ),
            status=_status_report(status) if status else None,
        )

    async def check_for_update(self) -> UpdateCheck:
        """See ``SystemReportReader.check_for_update``.

        Paperless asks GitHub whatever its own setting says, so the setting is read first: true, or
        not set ("default": the web UI asks its user). 0.0.0 says that GitHub did not answer.
        """
        try:
            ui = await self._client.get(
                "/api/ui_settings/", _UpdateSettings, timeout=UPDATE_SECONDS, retry=False
            )
        except PaperlessMcpError:
            return UpdateCheck.unknown(NOT_READ)
        if ui.settings.update_checking.backend_setting not in UPDATE_ON:
            return UpdateCheck.unknown(SWITCHED_OFF)
        found = await self._client.get(
            "/api/remote_version/", _RemoteVersion, timeout=UPDATE_SECONDS, retry=False
        )
        if found.version == NO_VERSION:
            return UpdateCheck.unknown(NO_ANSWER)
        return UpdateCheck(
            newest_release=found.version, update_available=found.update_available, note=None
        )

    async def _unless_forbidden[M: BaseModel](self, path: str, model: type[M]) -> M | None:
        """GET ``path``, or None if the user may not see it (HTTP 403)."""
        try:
            return await self._client.get(path, model)
        except PermissionDenied:
            return None
