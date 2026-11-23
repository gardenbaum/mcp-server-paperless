"""The permissions toolset through the assembled server on Paperless 3.4.2, on objects of its own.

Users, groups, documents and tags are created by the tests and removed again (documents with
their trash entries), so nothing is left behind and no seeded object is changed. Names start with
`plmcp28-`.
"""

import uuid
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume_text, num, obj, parse, results, seq
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Json, Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-19")]

PREFIX = "plmcp28-"
# Not a secret: the password of throwaway users on the throwaway E2E stack.
PASSWORD = "plmcp28-Passw0rd!x"


class Accounts:
    """Two users and a group of the test, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        tag = uuid.uuid4().hex[:8]
        self.alice: str = f"{PREFIX}alice-{tag}"
        self.bob: str = f"{PREFIX}bob-{tag}"
        self.team: str = f"{PREFIX}team-{tag}"
        self.alice_id: int = self.user(self.alice)
        self.bob_id: int = self.user(self.bob)
        created = stack.admin.post("/api/groups/", json={"name": self.team, "permissions": []})
        self.team_id: int = num(obj(parse(created.raise_for_status()))["id"])

    def user(self, name: str) -> int:
        body = {"username": name, "password": PASSWORD, "first_name": "Plmcp", "last_name": "Test"}
        created = self.stack.admin.post("/api/users/", json=body).raise_for_status()
        return num(obj(parse(created))["id"])

    def remove(self) -> None:
        for number in (self.alice_id, self.bob_id):
            _ = self.stack.admin.delete(f"/api/users/{number}/")
        _ = self.stack.admin.delete(f"/api/groups/{self.team_id}/")


@pytest.fixture
def accounts(stack: Stack) -> Iterator[Accounts]:
    made = Accounts(stack)
    yield made
    made.remove()


@pytest.fixture
def documents(stack: Stack) -> Iterator[list[int]]:
    """Two documents of their own, deleted and removed from the trash again."""
    created = [consume_text(stack.admin, f"{PREFIX}{n}-{uuid.uuid4().hex}") for n in range(2)]
    yield created
    delete_and_empty_trash(stack, created)


@pytest.fixture
def tag(stack: Stack) -> Iterator[tuple[int, str]]:
    name = f"{PREFIX}tag-{uuid.uuid4().hex[:8]}"
    created = stack.admin.post("/api/tags/", json={"name": name}).raise_for_status()
    number = num(obj(parse(created))["id"])
    yield number, name
    _ = stack.admin.delete(f"/api/tags/{number}/")


def server(stack: Stack, token: str, *, read_only: bool = False) -> FastMCP:
    settings = server_settings(toolsets=["core", "permissions"], read_only=read_only)
    return create_server(paperless_settings(stack.base_url, token), settings)


async def run(stack: Stack, token: str, tool: str, **arguments: object) -> Json:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused(stack: Stack, token: str, tool: str, **arguments: object) -> str:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def full(stack: Stack, path: str, number: int) -> Json:
    """The object with its grants, as Paperless stores them."""
    found = stack.admin.get(f"{path}{number}/", params={"full_perms": "true"}).raise_for_status()
    return obj(parse(found))


def grants(stack: Stack, path: str, number: int) -> dict[str, list[int]]:
    given = obj(full(stack, path, number)["permissions"])
    return {
        f"{action}_{kind}": sorted(num(n) for n in seq(obj(given[action])[kind]))
        for action in ("view", "change")
        for kind in ("users", "groups")
    }


async def test_a_merge_is_refused_without_confirm_and_names_who_would_gain_access(
    stack: Stack, accounts: Accounts, documents: list[int]
) -> None:
    [document, _] = documents

    message = await refused(
        stack,
        stack.admin_token,
        "set_permissions",
        kind="document",
        items=[document],
        view_users=[accounts.alice],
    )

    assert f"gains view: user '{accounts.alice}'" in message
    assert "confirm=true" in message
    assert grants(stack, "/api/documents/", document)["view_users"] == []


async def test_a_merge_adds_to_the_grants_by_name_and_a_replace_asks_too(
    stack: Stack, accounts: Accounts, documents: list[int]
) -> None:
    first, second = documents
    token = stack.admin_token

    merged = await run(
        stack,
        token,
        "set_permissions",
        kind="document",
        items=[first, second],
        view_users=[accounts.alice],
        change_groups=[accounts.team],
        confirm=True,
    )
    again = await run(
        stack,
        token,
        "set_permissions",
        kind="document",
        items=[first],
        view_users=[accounts.bob],
        confirm=True,
    )

    assert (merged["changed_count"], merged["unchanged_count"]) == (2, 0)
    assert grants(stack, "/api/documents/", first) == {
        "view_users": sorted([accounts.alice_id, accounts.bob_id]),
        "view_groups": [accounts.team_id],
        "change_users": [],
        "change_groups": [accounts.team_id],
    }
    assert grants(stack, "/api/documents/", second)["view_users"] == [accounts.alice_id]
    assert again["changed_count"] == 1

    message = await refused(
        stack,
        token,
        "set_permissions",
        kind="document",
        items=[first],
        mode="replace",
        view_users=[accounts.bob],
        change_groups=[],
    )

    assert f"gains view: user '{accounts.bob}'" not in message  # bob already may view
    assert f"loses view: user '{accounts.alice}'" in message
    assert f"loses change (keeps view): group '{accounts.team}'" in message
    assert "confirm=true" in message
    assert grants(stack, "/api/documents/", first)["view_users"] == sorted(
        [accounts.alice_id, accounts.bob_id]
    )  # nothing changed

    done = await run(
        stack,
        token,
        "set_permissions",
        kind="document",
        items=[first],
        mode="replace",
        view_users=[accounts.bob],
        change_groups=[],
        confirm=True,
    )

    assert done["changed_count"] == 1
    assert grants(stack, "/api/documents/", first) == {
        "view_users": [accounts.bob_id],
        "view_groups": [accounts.team_id],  # a list that was not given is kept
        "change_users": [],
        "change_groups": [],
    }
    row = cast("list[Json]", done["objects"])[0]
    assert str(row["web_url"]).endswith(f"/documents/{first}/details")


async def test_an_owner_is_handed_over_and_cleared_only_with_confirm(
    stack: Stack, accounts: Accounts, documents: list[int]
) -> None:
    [document, _] = documents
    token = stack.admin_token
    before = obj(full(stack, "/api/documents/", document))["owner"]

    handing = await refused(
        stack, token, "set_permissions", kind="document", items=[document], owner=accounts.alice
    )
    clearing = await refused(
        stack, token, "set_permissions", kind="document", items=[document], clear_owner=True
    )

    assert f"the owner changes from 'admin' to '{accounts.alice}'" in handing
    assert "the owner 'admin' is removed ('admin' loses the control ownership gave" in clearing
    assert "every user with the global permission to view documents" in clearing
    assert obj(full(stack, "/api/documents/", document))["owner"] == before

    _ = await run(
        stack,
        token,
        "set_permissions",
        kind="document",
        items=[document],
        owner=accounts.alice,
        confirm=True,
    )
    assert obj(full(stack, "/api/documents/", document))["owner"] == accounts.alice_id
    _ = await run(
        stack, token, "set_permissions", kind="document", items=[document], clear_owner=True,
        confirm=True,
    )  # fmt: skip
    assert obj(full(stack, "/api/documents/", document))["owner"] is None


async def test_taxonomy_objects_are_shared_by_name_and_unknown_names_send_nothing(
    stack: Stack, accounts: Accounts, tag: tuple[int, str]
) -> None:
    number, name = tag
    token = stack.admin_token

    unknown = await refused(
        stack,
        token,
        "set_permissions",
        kind="tag",
        items=[name],
        view_users=[accounts.alice, "ghost"],
    )
    done = await run(
        stack,
        token,
        "set_permissions",
        kind="tag",
        items=[name],
        change_users=[accounts.alice],
        view_groups=[accounts.team],
        confirm=True,
    )

    assert "No user is named 'ghost'" in unknown
    row = cast("list[Json]", done["objects"])[0]
    assert row["web_url"] is None
    assert grants(stack, "/api/tags/", number) == {
        "view_users": [accounts.alice_id],
        "view_groups": [accounts.team_id],
        "change_users": [accounts.alice_id],
        "change_groups": [],
    }


async def test_users_and_groups_are_listed_compactly(stack: Stack, accounts: Accounts) -> None:
    token = stack.admin_token

    users = await run(stack, token, "list_users", name_contains=PREFIX)
    groups = await run(stack, token, "list_groups", name_contains=accounts.team)

    rows = {str(u["username"]): u for u in cast("list[Json]", users["users"])}
    assert {accounts.alice, accounts.bob} <= rows.keys()
    alice = rows[accounts.alice]
    assert set(alice) == {"id", "username", "name", "is_active", "is_superuser", "group_ids"}
    assert (alice["name"], alice["is_active"], alice["is_superuser"]) == ("Plmcp Test", True, False)
    assert cast("list[Json]", groups["groups"]) == [{"id": accounts.team_id, "name": accounts.team}]


async def test_a_user_without_the_permissions_gets_errors_that_say_so(
    stack: Stack, documents: list[int]
) -> None:
    [document, _] = documents
    nobody: dict[str, list[int]] = {"users": [], "groups": []}
    shared = {"view": {"users": [_reader_id(stack)], "groups": []}, "change": nobody}
    _ = stack.admin.patch(
        f"/api/documents/{document}/", json={"set_permissions": shared}
    ).raise_for_status()

    listing = await refused(stack, stack.reader_token, "list_users")
    changing = await refused(
        stack,
        stack.reader_token,
        "set_permissions",
        kind="document",
        items=[document],
        view_groups=[1],
        confirm=True,
    )
    hidden = await refused(
        stack,
        stack.blind_token,
        "set_permissions",
        kind="tag",
        items=[1],
        view_users=[3],
        confirm=True,
    )

    assert "Listing users needs the view permission" in listing
    assert "may not change the permissions of this document" in changing
    assert "may not" in hidden


def _reader_id(stack: Stack) -> int:
    found = results(
        stack.admin.get("/api/users/", params={"username__iexact": "e2e-reader"}).raise_for_status()
    )
    return num(found[0]["id"])


async def test_read_only_mode_leaves_only_the_lookups(stack: Stack) -> None:
    async with Client(server(stack, stack.admin_token, read_only=True)) as client:
        offered = {t.name for t in await client.list_tools()}

    assert {"list_users", "list_groups"} <= offered
    assert "set_permissions" not in offered
