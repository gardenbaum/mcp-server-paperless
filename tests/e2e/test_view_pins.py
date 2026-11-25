"""pin_saved_view through the assembled server, over stdio and over HTTP, as users of their own."""

import contextlib
import json
import uuid
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport, StdioTransport, StreamableHttpTransport
from harness import paperless_settings, server_settings

from e2e.helpers import client as api_client
from e2e.helpers import num, obj, parse
from e2e.test_http import MCP_TOKEN, running
from e2e.test_stdio import console_script
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterator

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-92")]

type Json = dict[str, object]
type Connected = Client[FastMCPTransport] | Client[StdioTransport] | Client[StreamableHttpTransport]

PREFIX = "contract-view-e2e-"
RIGHTS = ("view_savedview", "add_savedview", "change_savedview", "delete_savedview")
UI = ("view_uisettings", "add_uisettings")
TRANSPORTS = ("memory", "stdio", "http")
# What a web UI could have stored before; every byte of it has to survive.
FOREIGN: Json = {
    "language": "de-de",
    "nested": {"a": [1, 2.5, None, {"b": True}], "Umlaut ä": "ü😀"},
    "update_checking": {"enabled": True},
    "saved_views": {
        "dashboard_views_visible_ids": [999999],
        "dashboard_views_sort_order": [999999],
        "warn_on_unsaved_change": False,
    },
}


@pytest.fixture
def user(stack: Stack) -> Iterator[Callable[..., str]]:
    """Makes users with just these global permissions and gives their tokens."""
    made: list[int] = []

    def new(*permissions: str) -> str:
        name, password = f"{PREFIX}{uuid.uuid4().hex[:12]}", f"pw-{uuid.uuid4().hex}"
        body = {"username": name, "password": password, "user_permissions": list(permissions)}
        created = stack.admin.post("/api/users/", json=body).raise_for_status()
        made.append(num(obj(parse(created))["id"]))
        with httpx2.Client(  # basic auth: /api/token/ is throttled
            base_url=stack.base_url,
            auth=(name, password),
            headers={"Accept": "application/json; version=10"},
        ) as signed_in:
            issued = signed_in.post("/api/profile/generate_auth_token/").raise_for_status()
        return str(parse(issued))

    yield new
    for view in cast("list[Json]", obj(parse(stack.admin.get(VIEWS)))["results"]):
        if str(view["name"]).startswith(PREFIX):
            _ = stack.admin.delete(f"/api/saved_views/{view['id']}/")
    for number in made:
        _ = stack.admin.delete(f"/api/users/{number}/")


VIEWS = "/api/saved_views/?page_size=100"


@contextlib.asynccontextmanager
async def connected(stack: Stack, token: str, transport: str) -> AsyncGenerator[Connected]:
    """A client of the server signed in to Paperless with `token`."""
    toolsets = "core,views,permissions"
    if transport == "memory":
        server = create_server(
            paperless_settings(stack.base_url, token),
            server_settings(toolsets=toolsets.split(",")),
        )
        async with Client(server) as made:
            yield made
    elif transport == "stdio":
        env = {
            "PAPERLESS_URL": stack.base_url,
            "PAPERLESS_TOKEN": token,
            "PAPERLESS_MCP_TOOLSETS": toolsets,
        }
        async with Client(StdioTransport(console_script(), [], env=env, keep_alive=False)) as made:
            yield made
    else:
        with running(stack, PAPERLESS_TOKEN=token, PAPERLESS_MCP_TOOLSETS=toolsets) as url:
            headers = {"Authorization": f"Bearer {MCP_TOKEN}"}
            async with Client(StreamableHttpTransport(url, headers=headers)) as made:
                yield made


def stored(token: str) -> Json:
    """The settings as Paperless answers them, what it computes included."""
    with api_client(token) as api:
        return obj(obj(parse(api.get("/api/ui_settings/").raise_for_status()))["settings"])


async def call(client: Connected, tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_view_is_pinned_and_unpinned_and_other_settings_survive(
    stack: Stack, user: Callable[..., str], transport: str
) -> None:
    token = user(*RIGHTS, *UI)
    with api_client(token) as api:
        _ = api.post("/api/ui_settings/", json={"settings": FOREIGN}).raise_for_status()
    before = stored(token)
    name = f"{PREFIX}{uuid.uuid4().hex[:8]}"

    async with connected(stack, token, transport) as client:
        made = await call(client, "create_saved_view", name=name)
        pinned = await call(client, "pin_saved_view", view=name, dashboard=True, sidebar=True)
        after_pin = stored(token)
        shown = await call(client, "get_saved_view", view=name)
        again = await call(client, "pin_saved_view", view=name, dashboard=True)
        _ = await call(client, "pin_saved_view", view=name, dashboard=False, sidebar=False)

    assert (made["on_dashboard"], made["in_sidebar"]) == (False, False)
    assert (pinned["on_dashboard"], pinned["in_sidebar"], pinned["changed"]) == (True, True, True)
    assert (shown["on_dashboard"], shown["in_sidebar"]) == (True, True)
    assert again["changed"] is False
    places = obj(after_pin["saved_views"])
    assert places["dashboard_views_visible_ids"] == [999999, made["id"]]
    assert places["sidebar_views_visible_ids"] == [made["id"]]
    restored = stored(token)
    assert obj(restored["saved_views"]).pop("sidebar_views_visible_ids") == []
    assert json.dumps(restored, sort_keys=True) == json.dumps(before, sort_keys=True)


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_user_who_may_not_write_settings_is_told_and_nothing_changes(
    stack: Stack, user: Callable[..., str], transport: str
) -> None:
    token = user(*RIGHTS, "view_uisettings")
    name = f"{PREFIX}{uuid.uuid4().hex[:8]}"

    async with connected(stack, token, transport) as client:
        _ = await call(client, "create_saved_view", name=name)
        refused = await client.call_tool(
            "pin_saved_view", {"view": name, "dashboard": True}, raise_on_error=False
        )

    assert refused.is_error
    assert "add_uisettings" in str(refused.content)
    assert "saved_views" not in stored(token)


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_user_who_may_not_view_settings_still_sees_views_but_not_where(
    stack: Stack, user: Callable[..., str], transport: str
) -> None:
    name = f"{PREFIX}{uuid.uuid4().hex[:8]}"

    async with connected(stack, user(*RIGHTS, "add_uisettings"), transport) as client:
        made = await call(client, "create_saved_view", name=name)
        refused = await client.call_tool(
            "pin_saved_view", {"view": name, "dashboard": True}, raise_on_error=False
        )

    assert (made["on_dashboard"], made["in_sidebar"]) == (None, None)
    assert refused.is_error
    assert "view_uisettings" in str(refused.content)


async def test_a_view_of_another_owner_can_be_pinned_by_who_may_only_see_it(
    stack: Stack, user: Callable[..., str]
) -> None:
    token = user("view_savedview", *UI)
    number = num(obj(obj(parse(_settings(token)))["user"])["id"])
    body: Json = {
        "name": f"{PREFIX}{uuid.uuid4().hex[:8]}",
        "filter_rules": [],
        "set_permissions": {
            "view": {"users": [number], "groups": []},
            "change": {"users": [], "groups": []},
        },
    }
    view = num(
        obj(parse(stack.admin.post("/api/saved_views/", json=body).raise_for_status()))["id"]
    )

    async with connected(stack, token, "memory") as client:
        done = await call(client, "pin_saved_view", view=view, sidebar=True)
        shown = await call(client, "get_saved_view", view=view)

    assert (done["on_dashboard"], done["in_sidebar"]) == (False, True)
    assert (shown["can_change"], shown["in_sidebar"]) == (False, True)


SHARING = (*RIGHTS, "view_uisettings", "view_user", "view_group")


def access(stack: Stack, view: int) -> tuple[object, list[int], list[int]]:
    """The owner, who may see and who may change the view, as Paperless stores them."""
    found = stack.admin.get(f"/api/saved_views/{view}/", params={"full_perms": "true"})
    row = obj(parse(found.raise_for_status()))
    users = [obj(obj(row["permissions"])[what])["users"] for what in ("view", "change")]
    seen, changed = cast("list[list[int]]", users)
    return row["owner"], seen, changed


@pytest.mark.decision("decision-91")
async def test_who_may_share_a_view_with_and_without_confirm(
    stack: Stack, user: Callable[..., str]
) -> None:
    owner, other = user(*SHARING), user(*SHARING)
    owns, grantee = (num(obj(obj(parse(_settings(t)))["user"])["id"]) for t in (owner, other))
    name = f"{PREFIX}{uuid.uuid4().hex[:8]}"
    async with connected(stack, owner, "memory") as client:
        view = num((await call(client, "create_saved_view", name=name))["id"])
    wanted: Json = {"kind": "saved_view", "items": [view], "view_users": ["e2e-reader"]}
    reader = num(obj(obj(parse(_settings(stack.reader_token)))["user"])["id"])

    def give(number: int | None, *editors: int) -> None:
        change = {"users": list(editors), "groups": list[int]()}
        body: Json = {"owner": number, "set_permissions": {"view": change, "change": change}}
        _ = stack.admin.patch(f"/api/saved_views/{view}/", json=body).raise_for_status()

    async def share(token: str, *, confirm: bool) -> tuple[bool, str]:
        async with connected(stack, token, "memory") as client:
            result = await client.call_tool(
                "set_permissions", {**wanted, "confirm": confirm}, raise_on_error=False
            )
        return result.is_error, str(result.content)

    # Nobody but the owner sees it: sharing it finds nothing, with and without confirm.
    for confirm in (False, True):
        refused, hidden = await share(other, confirm=confirm)
        assert refused
        assert "Confirming would" not in hidden
    # A change grant is asked about, but not enough.
    give(owns, grantee)
    asked, question = await share(other, confirm=False)
    denied, told = await share(other, confirm=True)
    assert (asked, denied) == (True, True)
    assert "Nothing was changed. Confirming would" in question
    assert "may not" in told
    assert access(stack, view) == (owns, [grantee], [grantee])
    # The owner, who is no superuser, and then anyone on a view without an owner.
    for token, number in ((owner, owns), (other, None)):
        give(number)
        asked, question = await share(token, confirm=False)
        assert asked
        assert "gains view: user 'e2e-reader'" in question
        assert access(stack, view) == (number, [], [])
        failed, said = await share(token, confirm=True)
        assert not failed, said
        assert access(stack, view) == (number, [reader], [])


def _settings(token: str) -> httpx2.Response:
    with api_client(token) as api:
        return api.get("/api/ui_settings/").raise_for_status()
