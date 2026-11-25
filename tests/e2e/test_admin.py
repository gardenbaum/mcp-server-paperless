"""The admin toolset through the assembled server on the E2E Paperless, on objects of its own.

Users and groups are created by the tests and removed again; the only configuration setting
touched is `app_title`, which the `title` fixture puts back. Names start with `plmcp31-`.
"""

import base64
import io
import uuid
import zipfile
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from contract.conftest import delete_and_empty_trash
from fastmcp import Client
from harness import paperless_settings, server_settings
from mcp.types import EmbeddedResource

from e2e.helpers import add_second_factor, consume_text, num, parse, results
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP

    from e2e.helpers import Json, Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-26")]

PREFIX = "plmcp31-"
# Not a secret: the password of throwaway users on the throwaway E2E stack.
PASSWORD = "plmcp31-Passw0rd!x"


def server(stack: Stack, token: str | None = None) -> FastMCP:
    settings = server_settings(toolsets=["core", "admin"])
    return create_server(paperless_settings(stack.base_url, token or stack.admin_token), settings)


async def run(stack: Stack, tool: str, token: str | None = None, **arguments: object) -> Json:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    assert PASSWORD not in repr(result)
    return result.structured_content


async def refused(stack: Stack, tool: str, token: str | None = None, **arguments: object) -> str:
    async with Client(server(stack, token)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    assert PASSWORD not in repr(result)
    return str(result.content)


def signs_in(stack: Stack, username: str) -> int:
    """The HTTP status of a request signed in with the user's password (documents need view)."""
    with httpx2.Client(
        base_url=stack.base_url,
        auth=(username, PASSWORD),
        headers={"Accept": "application/json; version=10"},
    ) as api:
        return api.get("/api/documents/", params={"page_size": 1}).status_code


@pytest.fixture
def tag() -> str:
    return f"{PREFIX}{uuid.uuid4().hex[:8]}"


@pytest.fixture
def cleanup(stack: Stack) -> Iterator[None]:
    """Delete every user and group of the tests, also when one failed."""
    yield
    for path, field in (("/api/users/", "username"), ("/api/groups/", "name")):
        found = stack.admin.get(path, params={f"{field}__istartswith": PREFIX})
        for row in results(found):
            _ = stack.admin.delete(f"{path}{row['id']}/")


@pytest.fixture
def title(stack: Stack) -> Iterator[None]:
    rows = cast("list[Json]", parse(stack.admin.get("/api/config/")))
    original = rows[0]["app_title"]
    yield
    _ = stack.admin.patch("/api/config/1/", json={"app_title": original}).raise_for_status()


@pytest.mark.usefixtures("cleanup")
async def test_a_user_is_created_changed_and_deleted_with_confirmations(
    stack: Stack, tag: str
) -> None:
    group = await run(stack, "create_group", name=f"{tag}-team")
    made = await run(
        stack,
        "create_user",
        username=tag,
        password=PASSWORD,
        permissions=["view_document"],
        groups=[f"{tag}-team"],
        confirm=True,
    )
    assert cast("Json", made["user"])["username"] == tag
    assert signs_in(stack, tag) == 200

    shown = await refused(stack, "update_user", user=tag, is_active=False)
    assert f"deactivate '{tag}'" in shown
    assert signs_in(stack, tag) == 200
    off = await run(stack, "update_user", user=tag, is_active=False, confirm=True)
    assert cast("Json", off["user"])["is_active"] is False
    assert signs_in(stack, tag) in {401, 403}

    gone = await refused(stack, "delete_user", user=tag)
    assert f"delete user '{tag}' for good" in gone
    deleted = await run(stack, "delete_user", user=tag, confirm=True)
    assert deleted["name"] == tag
    assert "No user" in await refused(stack, "get_user", user=tag)
    assert num(cast("Json", group["group"])["id"]) >= 1


@pytest.mark.usefixtures("cleanup")
async def test_a_superuser_needs_confirm_and_a_password_never_comes_back(
    stack: Stack, tag: str
) -> None:
    text = await refused(stack, "create_user", username=tag, is_superuser=True)
    assert "as a superuser" in text

    made = await run(
        stack, "create_user", username=tag, password=PASSWORD, is_superuser=True, confirm=True
    )

    assert cast("Json", made["user"])["is_superuser"] is True
    shown = await run(stack, "get_user", user=tag)
    assert "password" not in shown
    assert "*" not in repr(shown)


@pytest.mark.usefixtures("cleanup")
async def test_a_group_is_created_changed_and_deleted(stack: Stack, tag: str) -> None:
    assert "grants its members the permissions 'view_tag'" in await refused(
        stack, "create_group", name=tag, permissions=["view_tag"]
    )
    _ = await run(stack, "create_group", name=tag, permissions=["view_tag"], confirm=True)

    assert "add_tag" in await refused(stack, "update_group", group=tag, permissions=["add_tag"])
    changed = await run(stack, "update_group", group=tag, permissions=["add_tag"], confirm=True)
    assert cast("Json", changed["group"])["permissions"] == ["add_tag"]
    assert "delete group" in await refused(stack, "delete_group", group=tag)
    _ = await run(stack, "delete_group", group=tag, confirm=True)
    assert "No group" in await refused(stack, "get_group", group=tag)


@pytest.mark.usefixtures("title")
async def test_the_configuration_is_changed_only_with_confirm_and_restored(stack: Stack) -> None:
    name = f"{PREFIX}title"

    text = await refused(stack, "update_app_config", settings={"app_title": name})
    assert "app_title" in text
    assert cast("Json", (await run(stack, "get_app_config"))["settings"]).get("app_title") != name
    done = await run(stack, "update_app_config", settings={"app_title": name}, confirm=True)

    assert done["changed"] == ["app_title"]
    assert cast("Json", (await run(stack, "get_app_config"))["settings"])["app_title"] == name


async def test_logs_are_listed_and_read(stack: Stack) -> None:
    listed = await run(stack, "list_logs")
    assert "paperless" in cast("list[str]", listed["logs"])

    read = await run(stack, "get_log", name="paperless", lines=5)

    assert 1 <= num(read["returned_lines"]) <= 5
    assert "No log" in await refused(stack, "get_log", name="nope")


async def test_documents_come_as_one_archive(stack: Stack) -> None:
    ids = [consume_text(stack.admin, f"{PREFIX}zip-{n}-{uuid.uuid4().hex}") for n in range(2)]
    try:
        async with Client(server(stack)) as client:
            result = await client.call_tool(
                "bulk_download", {"document_ids": ids, "content": "originals"}
            )
        _, resource = result.content
        assert isinstance(resource, EmbeddedResource)
        blob = cast("str", resource.resource.model_dump()["blob"])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(blob))) as archive:
            assert len(archive.namelist()) == 2
    finally:
        delete_and_empty_trash(stack, ids)


async def test_a_user_without_the_permissions_gets_hints(stack: Stack) -> None:
    logs = await refused(stack, "list_logs", token=stack.reader_token)
    users = await refused(stack, "get_user", token=stack.reader_token, user=2)

    assert "Only staff users may read the logs." in logs
    assert "view_user" in users


@pytest.mark.usefixtures("cleanup")
@pytest.mark.decision("decision-103")
async def test_a_second_factor_is_removed_only_with_confirm(stack: Stack, tag: str) -> None:
    # A user who may not view documents would get a 403 whether signed in or not.
    made = await run(
        stack,
        "create_user",
        username=tag,
        password=PASSWORD,
        permissions=["view_document"],
        confirm=True,
    )
    assert signs_in(stack, tag) == 200
    none = await run(stack, "update_user", user=tag, remove_second_factor=True)
    add_second_factor(num(cast("Json", made["user"])["id"]))
    assert signs_in(stack, tag) == 401

    shown = await refused(stack, "update_user", user=tag, remove_second_factor=True)
    still = await run(stack, "get_user", user=tag)
    assert signs_in(stack, tag) == 401
    done = await run(stack, "update_user", user=tag, remove_second_factor=True, confirm=True)
    own = await refused(stack, "update_user", user="admin", remove_second_factor=True, confirm=True)

    assert "has no second factor" in str(none["message"])
    assert f"remove the second factor of '{tag}'" in shown
    assert still["mfa_enabled"] is True
    assert cast("Json", done["user"])["mfa_enabled"] is False
    assert signs_in(stack, tag) == 200
    assert "account this server signs in with" in own
