"""What the instance overview reports: versions, statistics, system status and the current user.

What a restricted user may not see is None, never an error: Paperless shows the system status only
to superusers, staff and holders of ``view_system_monitoring``, and the current user and the AI
flag (UI settings) only to holders of ``view_uisettings``.
"""

import re
from datetime import datetime
from typing import Annotated, ClassVar, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from mcp_server_paperless.domain.urls import text_without_credentials

# Output models are tool output schemas: a one-line class docstring each, and field descriptions
# (``Field(description=...)``) only where the name does not say enough. Docstrings of these
# classes reach the LLM in every tools/list, once per use of the class.


# The oldest supported paperless-ngx (decision-43), compared with what Paperless reports.
MINIMUM_RELEASE: Final = (3, 5, 1)
MINIMUM: Final = ".".join(map(str, MINIMUM_RELEASE))
_RELEASE = re.compile(r"(\d{1,9})\.(\d{1,9})\.(\d{1,9})", re.ASCII)


def update_notice(version: str) -> str | None:
    """That ``version`` is older than the minimum; None if it is not, or is no plain release."""
    found = _RELEASE.fullmatch(version)
    if found is None or tuple(map(int, found.groups())) >= MINIMUM_RELEASE:
        return None
    return (
        f"{version} is older than the minimum release {MINIMUM}: Paperless must be updated; until "
        "then some tools fail or answer differently"
    )


class _Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class InstanceStatistics(_Frozen):
    """Object counts, of the objects the user may see unless they may view global statistics."""

    documents: int
    inbox_documents: Annotated[
        int | None,
        Field(description="Documents with an inbox tag; null if no inbox tag is visible."),
    ]
    tags: int
    correspondents: int
    document_types: int
    storage_paths: int


class ComponentStatus(_Frozen):
    """Health of one Paperless component."""

    status: Annotated[str, Field(description="OK, WARNING, ERROR or DISABLED.")]
    error: str | None


class SystemStatus(_Frozen):
    """Storage and component health of the instance."""

    install_type: str
    storage_total_bytes: int
    storage_available_bytes: int
    database: ComponentStatus
    redis: ComponentStatus
    celery: ComponentStatus
    search_index: ComponentStatus
    classifier: ComponentStatus
    sanity_check: ComponentStatus
    llm_index: ComponentStatus


class CurrentUser(_Frozen):
    """The Paperless user the credential belongs to."""

    id: int
    username: str
    is_superuser: bool


class InstanceOverview(_Frozen):
    """The instance as the current user sees it."""

    paperless_version: str
    api_version: int
    statistics: InstanceStatistics
    system_status: Annotated[
        SystemStatus | None,
        Field(description="Null if the user may not view the system status."),
    ]
    current_user: Annotated[
        CurrentUser | None, Field(description="Null if the user may not view the UI settings.")
    ]
    ai_enabled: Annotated[
        bool | None,
        Field(
            description=(
                "Whether Paperless AI is enabled; null means unknown (the user may not view the "
                "UI settings), not disabled."
            )
        ),
    ]
    next_asn: Annotated[
        int | None,
        Field(description="Highest archive serial number + 1 (no trash)."),
    ]


class FileTypeCount(_Frozen):
    """How many documents have files of this type."""

    mime_type: str
    documents: int


class StatisticsReport(_Frozen):
    """Counts of the objects the user may see, or of all with the right to global statistics."""

    documents: int
    inbox_documents: Annotated[int | None, Field(description="Null: no inbox tag is visible.")]
    inbox_tags: Annotated[tuple[int, ...], Field(description="Ids of the inbox tags.")]
    file_types: tuple[FileTypeCount, ...]
    characters: Annotated[int, Field(description="Of the text of all documents.")]
    tags: int
    correspondents: int
    document_types: int
    storage_paths: int
    highest_serial_number: Annotated[
        int, Field(description="Highest archive serial number outside the trash; 0: none.")
    ]


# What the host says of itself; a URL in it is shown without its credentials.
type _Told = Annotated[str, AfterValidator(text_without_credentials)]


class _Status(_Frozen):
    status: Annotated[_Told, Field(description="OK, WARNING, ERROR or DISABLED.")]
    error: _Told | None


class DatabaseStatus(_Status):
    """The database."""

    type: _Told
    name: _Told
    latest_migration: _Told | None
    unapplied_migrations: tuple[_Told, ...]


class ServiceStatus(_Status):
    """A service Paperless talks to."""

    address: Annotated[_Told | None, Field(description="Without credentials.")]


class TimedStatus(_Status):
    """A component and when it last changed, was trained or ran."""

    last: datetime | None


class TaskTotals(_Frozen):
    """Every user's tasks of the last days."""

    days: int
    total: int
    pending: int
    success: int
    failure: int


class StatusReport(_Frozen):
    """Version, host, storage and every component of the instance."""

    paperless_version: _Told
    server_os: _Told
    install_type: _Told
    storage_total_bytes: int
    storage_available_bytes: int
    database: DatabaseStatus
    redis: ServiceStatus
    celery: Annotated[ServiceStatus, Field(description="The task workers.")]
    search_index: Annotated[TimedStatus, Field(description="last: when the index was written.")]
    classifier: Annotated[TimedStatus, Field(description="last: when it was trained.")]
    sanity_check: Annotated[TimedStatus, Field(description="last: when it ran.")]
    llm_index: Annotated[TimedStatus, Field(description="last: when it was updated.")]
    tasks: TaskTotals


# Why the newest release is not known.
SWITCHED_OFF: Final = "unknown: switched off (PAPERLESS_ENABLE_UPDATE_CHECK)"
NOT_READ: Final = "unknown: whether the check is switched on could not be read; GitHub is not asked"
NO_ANSWER: Final = "unknown: GitHub did not answer Paperless"
TOO_SLOW: Final = "unknown: the check took too long"


class UpdateCheck(_Frozen):
    """The newest release of Paperless, as Paperless learns it from GitHub; null: unknown."""

    newest_release: str | None
    update_available: bool | None
    note: str | None

    @classmethod
    def unknown(cls, note: str) -> UpdateCheck:
        """The newest release is not known, and why."""
        return cls(newest_release=None, update_available=None, note=note)


class SystemReport(_Frozen):
    """All that Paperless's statistics and system status tell this user."""

    statistics: StatisticsReport
    status: Annotated[
        StatusReport | None, Field(description="Null if the user may not view the system status.")
    ]
    update: Annotated[UpdateCheck | None, Field(description="Null: not asked for.")] = None


class OffToolset(_Frozen):
    """A toolset that is off."""

    toolset: str
    what: str
    tools: tuple[str, ...]


class ToolsetGuide(_Frozen):
    """The toolsets on and off."""

    on: tuple[str, ...]
    offered_after_restart: tuple[str, ...]
    off: tuple[OffToolset, ...]
    switch_on: str | None
    unchecked: dict[str, str]


class ServerOverview(InstanceOverview):
    """The instance as the current user sees it, and the toolsets."""

    too_old: Annotated[
        str | None, Field(description="Notice that Paperless must be updated; else null.")
    ]
    toolsets: ToolsetGuide

    @classmethod
    def of(cls, overview: InstanceOverview, toolsets: ToolsetGuide) -> ServerOverview:
        """The overview of the instance, with the toolsets of this server."""
        return cls(
            paperless_version=overview.paperless_version,
            api_version=overview.api_version,
            statistics=overview.statistics,
            system_status=overview.system_status,
            current_user=overview.current_user,
            ai_enabled=overview.ai_enabled,
            next_asn=overview.next_asn,
            too_old=update_notice(overview.paperless_version),
            toolsets=toolsets,
        )
