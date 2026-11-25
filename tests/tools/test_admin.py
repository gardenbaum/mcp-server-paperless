"""The admin toolset through the in-memory client on the seeded fake (PLMCP-31).

Every scenario that handles a password or a key runs with `PASSWORD` and `KEY` and checks that
neither appears in any tool result, error text or structured content.
"""

import base64
import io
import zipfile
from dataclasses import replace
from typing import cast

import pytest
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve
from mcp.types import EmbeddedResource, TextContent

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-4"),
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-15"),
    pytest.mark.decision("decision-26"),
]

type Json = dict[str, object]

TOOLS = {
    "get_user",
    "create_user",
    "update_user",
    "delete_user",
    "get_group",
    "create_group",
    "update_group",
    "delete_group",
    "get_app_config",
    "update_app_config",
    "list_logs",
    "get_log",
    "bulk_download",
}
READ_ONLY = {
    "get_user",
    "get_group",
    "get_app_config",
    "list_logs",
    "get_log",
    "bulk_download",
}
PASSWORD = "S3cret-Passw0rd!x"
KEY = "test-token-not-a-secret"


def fake(
    state: FakeState | None = None, *, writes: bool = True, user: str = "admin"
) -> FakePaperless:
    return FakePaperless(state or seeded(), user=user, allow_writes=writes)


def server(source: FakePaperless, *, max_download_bytes: int | None = None):  # noqa: ANN201 -- the assembled FastMCP server
    return serve(source, toolsets=["core", "admin"], max_download_bytes=max_download_bytes)


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    assert PASSWORD not in repr(result)
    assert KEY not in repr(result)
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    assert PASSWORD not in repr(result)
    assert KEY not in repr(result)
    return str(result.content)


async def test_admin_is_not_in_the_default_profile() -> None:
    async with Client(serve(fake())) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint(TOOLS)


async def test_the_toolset_offers_thirteen_tools_and_read_only_mode_keeps_the_reads() -> None:
    async with Client(server(fake())) as client:
        offered = {tool.name for tool in await client.list_tools()}
    async with Client(serve(fake(), toolsets=["admin"], read_only=True)) as client:
        reads = {tool.name for tool in await client.list_tools()}

    assert offered >= TOOLS
    assert len(TOOLS) == 13
    assert reads == READ_ONLY


async def test_a_user_is_created_read_and_never_shows_a_password() -> None:
    source = fake()

    made = await call(
        source,
        "create_user",
        username="carol",
        password=PASSWORD,
        groups=["e2e-group"],
        confirm=True,
    )
    shown = await call(source, "get_user", user="carol")

    user = cast("Json", made["user"])
    assert user["username"] == "carol"
    assert shown == user
    assert made["changed"] is True


async def test_a_password_that_is_not_text_is_refused_without_echoing_it() -> None:
    text = await refusal(fake(), "create_user", username="carol", password=12345678)

    assert "The password must be text." in text
    assert "12345678" not in text


async def test_creating_a_superuser_is_refused_until_confirmed() -> None:
    source = fake(writes=False)

    text = await refusal(source, "create_user", username="carol", is_superuser=True)

    assert "Nothing was changed. Confirming would create user 'carol'" in text
    assert "confirm=true" in text


async def test_changing_access_is_refused_until_confirmed_then_written() -> None:
    state = seeded()
    text = await refusal(fake(state, writes=False), "update_user", user="e2e-reader", is_staff=True)
    assert "give 'e2e-reader' staff status" in text

    done = await call(fake(state), "update_user", user="e2e-reader", is_staff=True, confirm=True)

    assert cast("Json", done["user"])["is_staff"] is True


CONFIRMED: list[tuple[str, Json]] = [
    ("a new e-mail address", {"email": "new@x.org"}),
    ("a rename", {"username": "rita"}),
    ("superuser or staff status", {"is_superuser": True}),
    ("superuser or staff status", {"is_staff": True}),
    ("deactivating", {"is_active": False}),
    ("reactivating", {"is_active": True}),
    ("a new password", {"password": PASSWORD}),
    ("groups", {"groups": ["e2e-group"]}),
    ("permissions", {"permissions": ["add_tag"]}),
]
UNCONFIRMED: list[tuple[str, Json]] = [
    ("First and last name", {"first_name": "Rita"}),
    ("First and last name", {"last_name": "Reed"}),
    ("clearing the e-mail address", {"email": ""}),
]


@pytest.mark.parametrize(
    ("words", "change", "address", "asks"),
    [
        *((words, change, "old@x.org", True) for words, change in CONFIRMED),
        ("a new e-mail address", {"email": "first@x.org"}, None, True),
        *((words, change, "old@x.org", False) for words, change in UNCONFIRMED),
    ],
)
async def test_update_user_asks_for_exactly_the_changes_its_description_names(
    words: str,
    change: Json,
    address: str | None,
    asks: bool,  # noqa: FBT001 -- a parametrised flag, not a switch
) -> None:
    async with Client(server(fake())) as client:
        tool = next(t for t in await client.list_tools() if t.name == "update_user")
    text = " ".join((tool.description or "").split())
    _, _, rest = text.partition("needs confirm: ")
    named, _, rest = rest.partition(". ")
    exempt = next(part for part in rest.split(". ") if "need none" in part)
    assert text.count("need confirm") + text.count("needs confirm") == 1
    assert text.count("need none") == 1
    assert (words in named, words in exempt) == (asks, not asks)

    state = seeded()
    position = next(i for i, u in enumerate(state.users) if u.username == "e2e-reader")
    active = not change.get("is_active", False)
    before = replace(state.users[position], email=address, is_active=active)
    state.users[position] = before

    if asks:
        assert "Nothing was changed. Confirming would" in await refusal(
            fake(state, writes=False), "update_user", user="e2e-reader", **change
        )
        assert state.users[position] == before
    else:
        done = await call(fake(state), "update_user", user="e2e-reader", **change)
        assert done["changed"] is True


async def test_deleting_a_user_needs_confirm_and_the_deleted_user_is_gone() -> None:
    state = seeded()
    assert "delete user 'e2e-blind' for good" in await refusal(
        fake(state, writes=False), "delete_user", user="e2e-blind"
    )

    done = await call(fake(state), "delete_user", user="e2e-blind", confirm=True)

    assert done["name"] == "e2e-blind"
    assert "No user has id 4" in await refusal(fake(state), "get_user", user=4)


async def test_groups_are_created_changed_and_deleted_with_confirmations() -> None:
    state = seeded()
    made = await call(
        fake(state), "create_group", name="accounting", permissions=["view_tag"], confirm=True
    )
    number = cast("Json", made["group"])["id"]
    assert "give the members of 'accounting'" in await refusal(
        fake(state, writes=False), "update_group", group="accounting", permissions=["add_tag"]
    )
    renamed = await call(fake(state), "update_group", group=number, name="books")
    assert cast("Json", renamed["group"])["name"] == "books"
    assert "delete group 'books' for good" in await refusal(
        fake(state, writes=False), "delete_group", group="books"
    )

    gone = await call(fake(state), "delete_group", group="books", confirm=True)

    assert gone["id"] == number
    assert await call(fake(state), "get_group", group="e2e-group") is not None


async def test_the_configuration_is_read_and_changed_only_with_confirm() -> None:
    state = seeded()
    state.config_keys = {"llm_api_key"}

    before = await call(fake(state), "get_app_config")
    text = await refusal(
        fake(state, writes=False),
        "update_app_config",
        settings={"app_title": "Vault"},
        llm_api_key=KEY,
    )
    done = await call(
        fake(state),
        "update_app_config",
        settings={"app_title": "Vault"},
        llm_api_key=KEY,
        confirm=True,
    )

    assert before["keys_configured"] == ["llm_api_key"]
    assert "set app_title to 'Vault'" in text
    assert "its value is never shown" in text
    assert cast("Json", done["config"])["settings"] == {"app_title": "Vault"}
    assert done["changed"] == ["app_title", "llm_api_key"]


async def test_a_key_that_is_not_text_is_refused_without_echoing_it() -> None:
    text = await refusal(fake(), "update_app_config", llm_api_key=987654321, confirm=True)

    assert "must be text" in text
    assert "987654321" not in text


@pytest.mark.decision("decision-64")
async def test_the_configuration_tools_return_service_urls_without_their_credentials() -> None:
    state = seeded()
    state.config = {"llm_endpoint": "https://u:s3cret@old.example/v1"}

    read = await call(fake(state), "get_app_config")
    done = await call(
        fake(state),
        "update_app_config",
        settings={"llm_endpoint": "https://u:n3wer@new.example/v1"},
        confirm=True,
    )

    assert cast("Json", read["settings"])["llm_endpoint"] == "https://old.example/v1"
    assert read["shown_in_part"] == ["llm_endpoint"]
    config = cast("Json", done["config"])
    assert cast("Json", config["settings"])["llm_endpoint"] == "https://new.example/v1"
    assert config["shown_in_part"] == ["llm_endpoint"]
    assert "s3cret" not in str(read)
    assert "n3wer" not in str(done)


async def test_the_description_of_the_configuration_tool_lists_the_settings() -> None:
    async with Client(server(fake())) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    assert "app_title: Application title" in (tools["update_app_config"].description or "")


async def test_logs_are_listed_and_read() -> None:
    state = seeded()
    state.logs["mail"] = ["[2026-11-24 10:00:00,001] [INFO] [paperless] one", "two", "three"]

    listed = await call(fake(state), "list_logs")
    read = await call(fake(state), "get_log", name="mail", lines=2)

    assert listed["logs"] == ["paperless", "mail", "celery"]
    assert read["lines"] == ["two", "three"]
    assert read["truncated"] is False


async def test_documents_come_as_an_embedded_archive() -> None:
    async with Client(server(fake())) as client:
        result = await client.call_tool("bulk_download", {"document_ids": [1, 2]})

    report, resource = result.content
    assert isinstance(report, TextContent)
    assert isinstance(resource, EmbeddedResource)
    blob = cast("str", resource.resource.model_dump()["blob"])
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(blob))) as archive:
        assert len(archive.namelist()) == 2
    assert '"delivered":true' in report.text
    assert resource.resource.mime_type == "application/zip"


async def test_an_archive_over_the_limit_is_reported_and_not_attached() -> None:
    async with Client(server(fake(), max_download_bytes=10)) as client:
        result = await client.call_tool("bulk_download", {"document_ids": [1]})

    [report] = result.content
    assert isinstance(report, TextContent)
    assert '"delivered":false' in report.text
    assert "download_document" in report.text


async def test_a_user_without_the_permission_gets_a_hint() -> None:
    text = await refusal(fake(user="e2e-reader"), "list_logs")

    assert "Only staff users may read the logs." in text


async def test_a_password_user_and_a_group_with_permissions_are_refused_until_confirmed() -> None:
    source = fake(writes=False)

    user = await refusal(source, "create_user", username="mallory", password=PASSWORD)
    group = await refusal(source, "create_group", name="powers", permissions=["add_user"])

    assert "create user 'mallory' with a password" in user
    assert "create group 'powers' that grants its members the permissions 'add_user'" in group


async def test_every_archive_has_a_uri_of_its_own() -> None:
    uris: list[str] = []
    for ids in ([1], [1, 2]):
        async with Client(server(fake())) as client:
            result = await client.call_tool("bulk_download", {"document_ids": ids})
        resource = result.content[1]
        assert isinstance(resource, EmbeddedResource)
        uris.append(str(resource.resource.uri))

    assert uris[0] != uris[1]
    assert all(u.endswith("/paperless-documents.zip") for u in uris)
