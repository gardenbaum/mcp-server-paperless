"""What the instance overview reports: versions, statistics, system status and the current user.

What a restricted user may not see is None, never an error: Paperless shows the system status only
to superusers, staff and holders of ``view_system_monitoring``, and the current user and the AI
flag (UI settings) only to holders of ``view_uisettings``.
"""

import re
from typing import Annotated, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

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
            too_old=update_notice(overview.paperless_version),
            toolsets=toolsets,
        )
