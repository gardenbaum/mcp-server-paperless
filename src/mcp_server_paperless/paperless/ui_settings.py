"""UiSettingsReader and UiSettingsWriter on ``/api/ui_settings/`` (decision-92).

The settings of the web UI of the signed-in user, one JSON object. A GET mixes what is stored with
what Paperless computes for the answer, OAuth addresses with their state among it; that never
leaves this module and is never sent back. A POST replaces the whole object, and one without
``settings`` makes every later GET fail (probe P1 of PLMCP-88.1).
"""

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, JsonValue

from mcp_server_paperless.domain.errors import PermissionDenied

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mcp_server_paperless.paperless.client import PaperlessClient

PATH: Final = "/api/ui_settings/"
MAX_BYTES: Final = 1024 * 1024
READING: Final = "view_uisettings"
WRITING: Final = "add_uisettings"  # change_uisettings does not count (probe P3)
# What 3.5.1 puts into every answer (UiSettingsView.get); a stored value of the name is hidden.
COMPUTED: Final = frozenset(
    {
        *("trash_delay", "version", "app_title", "app_logo", "auditlog_enabled", "remote_ocr"),
        *("email_enabled", "ai_enabled", "gmail_oauth_url", "outlook_oauth_url"),
    }
)


class _Answer(BaseModel):
    settings: dict[str, JsonValue]


def stored(settings: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """``settings`` without what Paperless computes for every answer."""
    kept = {name: value for name, value in settings.items() if name not in COMPUTED}
    checking = kept.get("update_checking")
    if isinstance(checking, dict):
        rest = {name: value for name, value in checking.items() if name != "backend_setting"}
        if rest:
            kept["update_checking"] = rest
        else:
            del kept["update_checking"]  # an answer always has it, stored or not
    return kept


def refused(permission: str) -> PermissionDenied:
    """The error for a 403, naming the permission that is missing."""
    what = "view" if permission == READING else "change"
    message = f"The Paperless user may not {what} UI settings. Nothing was changed."
    return PermissionDenied(message, hint=f"The user needs the permission {permission}.")


class PaperlessUiSettingsReader:
    """``UiSettingsReader`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Read through ``client``."""
        self._client: PaperlessClient = client

    async def get_ui_settings(self) -> dict[str, JsonValue]:
        """See ``UiSettingsReader.get_ui_settings``."""
        try:
            answer = await self._client.read_json(
                PATH, _Answer, max_bytes=MAX_BYTES, compressed=True
            )
        except PermissionDenied:
            raise refused(READING) from None
        return stored(answer.settings)


class PaperlessUiSettingsWriter:
    """``UiSettingsWriter`` over the Paperless REST API."""

    def __init__(self, client: PaperlessClient) -> None:
        """Write through ``client``."""
        self._client: PaperlessClient = client

    async def replace_ui_settings(self, settings: Mapping[str, JsonValue]) -> None:
        """See ``UiSettingsWriter.replace_ui_settings``; a POST is never retried."""
        try:
            _ = await self._client.request("POST", PATH, json={"settings": dict(settings)})
        except PermissionDenied:
            raise refused(WRITING) from None
