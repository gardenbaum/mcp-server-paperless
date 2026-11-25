"""PaperlessUiSettingsReader and -Writer on the Paperless 3.5.1 wire format."""

import gzip
import json
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from adapter.conftest import streaming
from adapter.test_views import Recording
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    PermissionDenied,
    UnexpectedAnswer,
    UpstreamUnavailable,
)
from mcp_server_paperless.paperless.ui_settings import (
    MAX_BYTES,
    PaperlessUiSettingsReader,
    PaperlessUiSettingsWriter,
    stored,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from adapter.conftest import ClientFactory

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-92")]

type Json = dict[str, object]

# What 3.5.1 puts into every answer, whatever is stored (probe P1 of PLMCP-88.1).
COMPUTED: Json = {
    "trash_delay": 30,
    "version": "3.5.1",
    "app_title": None,
    "app_logo": None,
    "auditlog_enabled": True,
    "remote_ocr": {"configured": False, "mode": "always"},
    "email_enabled": False,
    "ai_enabled": False,
    "gmail_oauth_url": "https://accounts.example/auth?state=xyz",
    "outlook_oauth_url": "https://login.example/auth?state=xyz",
}
STORED: Json = {
    "language": "de-de",
    "saved_views": {
        "dashboard_views_visible_ids": [3, "4", 999999],
        "dashboard_views_sort_order": [4, 3],
    },
    "update_checking": {"enabled": True},
    "big": 12345678901234567890123,
    "float": 1e-07,
    "empty": None,
    "nested": {"a": [1, 2.5, None, "ü😀", {"b": True}], "version": 1},
}


def answer(settings: object) -> httpx2.Response:
    body: Json = {"user": {"id": 2, "username": "admin"}, "settings": settings, "permissions": []}
    return httpx2.Response(200, json=body)


async def test_the_settings_are_read_without_what_paperless_computes(
    make_client: ClientFactory,
) -> None:
    checking = {"update_checking": {"enabled": True, "backend_setting": "default"}}
    server = Recording(answer({**STORED, **checking, **COMPUTED}))

    found = await PaperlessUiSettingsReader(make_client(server)).get_ui_settings()

    assert json.dumps(found) == json.dumps(STORED)
    assert server.sent == [("GET", "/api/ui_settings/", None, {})]


async def test_nothing_stored_is_an_empty_object(make_client: ClientFactory) -> None:
    server = Recording(answer({"update_checking": {"backend_setting": "default"}, **COMPUTED}))

    assert await PaperlessUiSettingsReader(make_client(server)).get_ui_settings() == {}


async def test_a_compressed_answer_is_taken(make_client: ClientFactory) -> None:
    plain = answer(STORED)
    packed = httpx2.Response(
        200,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        content=gzip.compress(plain.content),
    )
    server = Recording(packed)

    assert await PaperlessUiSettingsReader(make_client(server)).get_ui_settings() == STORED


async def test_settings_over_the_limit_are_refused(make_client: ClientFactory) -> None:
    # As Paperless sends it; the largest answer is the one the coverage table replays.
    computed = {"update_checking": {"backend_setting": "default"}}
    server = Recording(answer({"x": "a" * MAX_BYTES, **computed}))

    with pytest.raises(AnswerTooLarge):
        _ = await PaperlessUiSettingsReader(make_client(server)).get_ui_settings()


async def test_settings_that_are_no_object_are_refused(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(200, json={"settings": []}))

    with pytest.raises(UnexpectedAnswer):
        _ = await PaperlessUiSettingsReader(make_client(server)).get_ui_settings()


async def test_a_user_without_the_permissions_is_told_which_one_is_missing(
    make_client: ClientFactory,
) -> None:
    server = Recording(*[httpx2.Response(403, json={"detail": "no"})] * 2)
    client = make_client(server)

    with pytest.raises(PermissionDenied) as reading:
        _ = await PaperlessUiSettingsReader(client).get_ui_settings()
    with pytest.raises(PermissionDenied) as writing:
        await PaperlessUiSettingsWriter(client).replace_ui_settings({})

    assert reading.value.hint is not None
    assert "view_uisettings" in reading.value.hint
    assert writing.value.hint is not None
    assert "add_uisettings" in writing.value.hint
    assert reading.value.describe() == (
        "The Paperless user may not view UI settings. Nothing was changed. "
        "Hint: The user needs the permission view_uisettings."
    )
    assert writing.value.describe() == (
        "The Paperless user may not change UI settings. Nothing was changed. "
        "Hint: The user needs the permission add_uisettings."
    )


async def test_a_write_sends_all_settings_under_their_key_and_is_never_repeated(
    make_client: ClientFactory,
) -> None:
    server = Recording(
        httpx2.Response(200, json={"success": True}), httpx2.Response(503), httpx2.Response(200)
    )
    writer = PaperlessUiSettingsWriter(make_client(server))

    await writer.replace_ui_settings(cast("dict[str, JsonValue]", STORED))
    with pytest.raises(UpstreamUnavailable):
        await writer.replace_ui_settings({})

    assert server.sent == [
        ("POST", "/api/ui_settings/", {"settings": STORED}, {}),
        ("POST", "/api/ui_settings/", {"settings": {}}, {}),
    ]


@pytest.mark.parametrize(
    "page",
    [(302, b""), (200, b"<html>Sign in"), (200, b'{"success": false}')],
    ids=["redirect", "login page", "no success"],
)
async def test_what_is_no_answer_of_paperless_is_no_success(
    page: tuple[int, bytes],
) -> None:
    status, content = page
    client, _ = streaming(lambda _request: httpx2.Response(status, content=content))

    with pytest.raises(UnexpectedAnswer, match="unexpected response for POST"):
        await PaperlessUiSettingsWriter(client).replace_ui_settings({})
    with pytest.raises(UnexpectedAnswer, match="unexpected response for GET"):
        _ = await PaperlessUiSettingsReader(client).get_ui_settings()


VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(), inner, max_size=3),
    max_leaves=8,
)
KEYS = st.sampled_from([*COMPUTED, "update_checking"]) | st.text()


@given(st.dictionaries(KEYS, VALUES, max_size=8))
def test_computed_settings_are_left_out_and_everything_else_stays(
    settings: dict[str, JsonValue],
) -> None:
    kept = stored(settings)

    assert not set(kept) & set(COMPUTED)
    checking = kept.get("update_checking")
    assert not (isinstance(checking, dict) and "backend_setting" in checking)
    for name in set(settings) - set(COMPUTED) - {"update_checking"}:
        assert kept[name] == settings[name]
    assert set(kept) <= set(settings)
    assert stored(kept) == kept
