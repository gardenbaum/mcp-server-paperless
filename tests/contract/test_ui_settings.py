"""Contract of UiSettingsReader and -Writer: the fake and a live Paperless, with users of their own.

No test writes the settings of a seeded account: POST replaces all of them.
"""

import json
from typing import TYPE_CHECKING

import pytest

from mcp_server_paperless.domain.errors import PermissionDenied

if TYPE_CHECKING:
    from pydantic import JsonValue

    from contract.conftest import Backend

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-92")]

GET, REPLACE = "UiSettingsReader.get_ui_settings", "UiSettingsWriter.replace_ui_settings"
READS, WRITES = "view_uisettings", "add_uisettings"
RICH: dict[str, JsonValue] = {
    "language": "de-de",
    "nested": {"a": [1, 2.5, None, "x", {"b": True}], "Umlaut ä": "ü😀", "n": {}},
    "saved_views": {
        "dashboard_views_visible_ids": [3, "4", 999999],
        "sidebar_views_visible_ids": [],
        "dashboard_views_sort_order": [4, 3],
    },
    "update_checking": {"enabled": True},
    "big": 12345678901234567890123,
    "float": 1e-07,
    "empty": None,
}


@pytest.mark.contract(GET)
async def test_a_user_who_stored_nothing_has_no_settings(backend: Backend) -> None:
    assert await backend.restricted(frozenset({READS})).ui_settings.get_ui_settings() == {}


@pytest.mark.contract(GET)
@pytest.mark.contract(REPLACE)
async def test_what_is_written_is_read_back_and_survives_being_written_back(
    backend: Backend,
) -> None:
    user = backend.restricted(frozenset({READS, WRITES}))

    await user.ui_setting_writes.replace_ui_settings(RICH)
    first = await user.ui_settings.get_ui_settings()
    await user.ui_setting_writes.replace_ui_settings(first)

    assert json.dumps(first, sort_keys=True) == json.dumps(RICH, sort_keys=True)
    assert await user.ui_settings.get_ui_settings() == first


@pytest.mark.contract(REPLACE)
async def test_a_write_replaces_everything_and_only_for_its_user(backend: Backend) -> None:
    one = backend.restricted(frozenset({READS, WRITES}))
    other = backend.restricted(frozenset({READS, WRITES}))
    await other.ui_setting_writes.replace_ui_settings({"theme": 1})

    await one.ui_setting_writes.replace_ui_settings({"language": "fr-fr"})
    await one.ui_setting_writes.replace_ui_settings({"theme": 2})

    assert await one.ui_settings.get_ui_settings() == {"theme": 2}
    assert await other.ui_settings.get_ui_settings() == {"theme": 1}


@pytest.mark.contract(GET)
@pytest.mark.contract(REPLACE)
async def test_reading_and_writing_need_their_own_permissions(backend: Backend) -> None:
    reads = backend.restricted(frozenset({READS, "change_uisettings"}))
    writes = backend.restricted(frozenset({WRITES}))

    with pytest.raises(PermissionDenied):
        await reads.ui_setting_writes.replace_ui_settings({"a": 1})
    assert await reads.ui_settings.get_ui_settings() == {}
    await writes.ui_setting_writes.replace_ui_settings({})
    with pytest.raises(PermissionDenied):
        _ = await writes.ui_settings.get_ui_settings()
