"""Pinning and sharing saved views as operations, on the fake and on a live Paperless.

The tools run as they are on the contract backends (contract/serving.py): pins are two lists among
the UI settings of a user, and a confirmation is no port method.
"""

import json
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from fakes.accounts import READER

from contract.conftest import SCRATCH_VIEW
from contract.serving import output, refusal
from contract.test_bulk import user_id
from contract.test_ui_settings import RICH
from mcp_server_paperless.domain.bulk import ObjectPermissions
from mcp_server_paperless.domain.permissions import AccessChange
from mcp_server_paperless.domain.views import ViewValues

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from contract.conftest import Backend, Ports
    from contract.serving import Json

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-91")]

VIEWING = frozenset({"view_savedview", "change_savedview", "add_savedview"})
UI = frozenset({"view_uisettings", "add_uisettings"})
SHARING = VIEWING | {"view_uisettings", "view_user", "view_group"}
DASHBOARD, SIDEBAR = "dashboard_views_visible_ids", "sidebar_views_visible_ids"


async def make_view(ports: Ports) -> int:
    name = f"{SCRATCH_VIEW}{uuid.uuid4().hex}"
    return (await ports.view_writes.create_saved_view(name, ViewValues())).id


# --- pinning


def places(settings: Mapping[str, JsonValue], *, without: tuple[str, ...] = ()) -> str:
    """The settings as text, without some lists of the saved views."""
    views = cast("dict[str, JsonValue]", settings.get("saved_views", {}))
    kept = {key: value for key, value in views.items() if key not in without}
    return json.dumps({**settings, "saved_views": kept}, sort_keys=True)


@pytest.mark.decision("decision-92")
async def test_a_view_is_pinned_and_unpinned_and_every_other_setting_stays(
    backend: Backend,
) -> None:
    user = backend.restricted(VIEWING | UI)
    view = await make_view(user)
    await user.ui_setting_writes.replace_ui_settings(RICH)

    both = await output(user, "pin_saved_view", view=view, dashboard=True, sidebar=True)
    pinned = await user.ui_settings.get_ui_settings()
    shown = await output(user, "get_saved_view", view=view)
    again = await output(user, "pin_saved_view", view=view, dashboard=True)
    one = await output(user, "pin_saved_view", view=view, dashboard=False)
    half = await user.ui_settings.get_ui_settings()
    none = await output(user, "pin_saved_view", view=view, sidebar=False)
    unpinned = await user.ui_settings.get_ui_settings()

    def state(answer: Json) -> tuple[object, object, object]:
        return answer["on_dashboard"], answer["in_sidebar"], answer["changed"]

    assert state(both) == (True, True, True)
    assert (shown["on_dashboard"], shown["in_sidebar"]) == (True, True)
    assert state(again) == (True, True, False)
    assert state(one) == (False, True, True)
    assert state(none) == (False, False, True)
    stored = cast("dict[str, list[JsonValue]]", pinned["saved_views"])
    assert stored[DASHBOARD] == [3, "4", 999999, view]
    assert stored[SIDEBAR] == [view]
    kept = cast("dict[str, list[JsonValue]]", half["saved_views"])
    assert (kept[DASHBOARD], kept[SIDEBAR]) == ([3, "4", 999999], [view])
    assert places(unpinned) == places(RICH)
    for settings in (pinned, half):
        lists = (DASHBOARD, SIDEBAR)
        assert places(settings, without=lists) == places(RICH, without=lists)


@pytest.mark.decision("decision-92")
async def test_a_view_of_somebody_else_is_pinned_for_the_current_user_only(
    backend: Backend,
) -> None:
    owner, user = backend.restricted(VIEWING | UI), backend.restricted(VIEWING | UI)
    view = await make_view(owner)
    viewer = await user.management.get_current_user_id()
    _ = await owner.permission_writes.set_permissions(
        "saved_view",
        view,
        AccessChange(permissions=ObjectPermissions(view_users=(viewer,))),
    )

    done = await output(user, "pin_saved_view", view=view, sidebar=True)
    theirs = await output(owner, "get_saved_view", view=view)
    gone = await output(user, "pin_saved_view", view=view, sidebar=False)

    assert (done["on_dashboard"], done["in_sidebar"]) == (False, True)
    assert (theirs["on_dashboard"], theirs["in_sidebar"]) == (False, False)
    assert (gone["in_sidebar"], gone["changed"]) == (False, True)
    assert await owner.ui_settings.get_ui_settings() == {}


@pytest.mark.decision("decision-92")
@pytest.mark.parametrize("missing", sorted(UI))
async def test_pinning_needs_both_permissions(backend: Backend, missing: str) -> None:
    user = backend.restricted(VIEWING | UI - {missing})
    view = await make_view(user)

    told = await refusal(user, "pin_saved_view", view=view, dashboard=True)

    assert "may not" in told
    if missing == "add_uisettings":
        assert await user.ui_settings.get_ui_settings() == {}


# --- sharing, with and without confirm


async def grants(backend: Backend, view: int) -> tuple[int | None, ObjectPermissions]:
    [found] = await backend.admin.permissions.get_permissions("saved_view", [view])
    return found.owner, found.permissions


async def share(backend: Backend, view: int, *, owner: int | None, editor: int | None) -> None:
    """Give the view to an owner or nobody, with a change grant or without any."""
    given = () if editor is None else (editor,)
    change = AccessChange(owner=owner, permissions=ObjectPermissions(change_users=given))
    _ = await backend.admin.permission_writes.set_permissions("saved_view", view, change)


WANTED: Json = {"kind": "saved_view", "view_users": [READER[0]]}


@pytest.mark.parametrize("who", ["owner", "superuser", "anyone on an unowned view"])
async def test_a_view_is_shared_after_a_confirmation_only(backend: Backend, who: str) -> None:
    user = backend.restricted(SHARING)
    view = await make_view(user)
    number = await user.management.get_current_user_id()
    reader = await user_id(backend.admin, READER[0])
    if who == "anyone on an unowned view":
        await share(backend, view, owner=None, editor=None)
    acting = backend.admin if who == "superuser" else user
    owner = None if who == "anyone on an unowned view" else number

    asked = await refusal(acting, "set_permissions", WANTED, items=[view])
    untouched = await grants(backend, view)
    done = await output(acting, "set_permissions", WANTED, items=[view], confirm=True)

    assert "Nothing was changed. Confirming would" in asked
    assert f"gains view: user '{READER[0]}'" in asked
    assert untouched == (owner, ObjectPermissions())
    assert done["changed_count"] == 1
    assert await grants(backend, view) == (owner, ObjectPermissions(view_users=(reader,)))


@pytest.mark.parametrize("confirm", [False, True])
async def test_a_change_grant_on_a_view_is_not_enough_to_share_it(
    backend: Backend, *, confirm: bool
) -> None:
    user = backend.restricted(SHARING)
    view = await make_view(backend.admin)
    number = await user.management.get_current_user_id()
    admin = await backend.admin.management.get_current_user_id()

    hidden = await refusal(user, "set_permissions", WANTED, items=[view], confirm=confirm)
    await share(backend, view, owner=admin, editor=number)
    before = await grants(backend, view)
    told = await refusal(user, "set_permissions", WANTED, items=[view], confirm=confirm)

    assert str(view) in hidden
    # Without confirm the refusal is the question; with it Paperless refuses.
    assert ("may not" in told) is confirm
    assert ("Nothing was changed. Confirming would" in told) is not confirm
    assert await grants(backend, view) == before


async def test_an_owner_hands_a_view_over_after_a_confirmation_only(backend: Backend) -> None:
    user = backend.restricted(SHARING)
    view = await make_view(user)
    number = await user.management.get_current_user_id()
    reader = await user_id(backend.admin, READER[0])
    wanted: Json = {"kind": "saved_view", "items": [view], "owner": READER[0]}

    asked = await refusal(user, "set_permissions", wanted)
    assert await grants(backend, view) == (number, ObjectPermissions())
    _ = await output(user, "set_permissions", wanted, confirm=True)

    assert "Nothing was changed. Confirming would" in asked
    assert await grants(backend, view) == (reader, ObjectPermissions())
