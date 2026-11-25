"""The permissions toolset through the in-memory client on the seeded fake (PLMCP-28).

The toolset is off by default (the default profile stays within its tool budget). Replacing
grants and changing an owner need confirm (decision-12, decision-15); merging does not.
"""

from typing import TYPE_CHECKING, cast

import pytest
from fakes.mail import FakeMailAccount
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

if TYPE_CHECKING:
    from fastmcp import FastMCP

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-12"),
    pytest.mark.decision("decision-19"),
    pytest.mark.decision("decision-15"),
]

type Json = dict[str, object]

TOOLS = frozenset({"set_permissions", "list_users", "list_groups"})
CONFIRM_DESCRIPTION = (
    "Must be true to go ahead at once. Without it a client that can show dialogs asks the "
    "user, and the tool runs only if they accept; with any other nothing changes and the "
    "error says what would happen. Describe that to the user and pass true only once they "
    "agree, or if they already asked for exactly this."
)
PUBLIC, PRIVATE, READER_ID = 1, 2, 3


def fake(state: FakeState | None = None, *, writes: bool = True) -> FakePaperless:
    return FakePaperless(state or seeded(), user="admin", allow_writes=writes)


def server(source: FakePaperless, **settings: object) -> FastMCP:
    return serve(source, toolsets=["permissions"], **settings)  # pyright: ignore[reportArgumentType] -- settings


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return str(result.content)


def objects(output: Json) -> list[Json]:
    return cast("list[Json]", output["objects"])


# --- the toolset -------------------------------------------------------------------------------


async def test_the_permissions_toolset_is_off_by_default_and_has_three_tools_when_selected() -> (
    None
):
    async with Client(serve(fake())) as client:
        default = {t.name for t in await client.list_tools()}
    async with Client(server(fake())) as client:
        selected = {t.name for t in await client.list_tools()}

    assert not TOOLS & default
    assert selected == TOOLS


async def test_read_only_mode_leaves_only_the_lookups() -> None:
    async with Client(serve(fake(), toolsets=["permissions"], read_only=True)) as client:
        offered = {t.name for t in await client.list_tools()}

    assert offered == {"list_users", "list_groups"}


async def test_the_annotations_tell_which_tool_changes_things_and_which_can_repeat() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t.annotations for t in await client.list_tools()}

    hints = {
        name: (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint)
        for name, a in tools.items()
        if a
    }
    assert hints == {
        "set_permissions": (False, True, True, False),
        "list_users": (True, None, True, False),
        "list_groups": (True, None, True, False),
    }


async def test_set_permissions_takes_a_described_confirm_and_a_mode_that_defaults_to_merge() -> (
    None
):
    async with Client(server(fake())) as client:
        tools = {t.name: t for t in await client.list_tools()}

    schema = tools["set_permissions"].input_schema
    properties = cast("dict[str, Json]", schema["properties"])
    assert properties["confirm"]["default"] is False
    assert properties["confirm"]["description"] == CONFIRM_DESCRIPTION
    assert properties["mode"]["default"] == "merge"
    assert sorted(cast("list[str]", schema["required"])) == ["items", "kind"]
    items = properties["items"]
    assert (items["minItems"], items["maxItems"]) == (1, 100)
    for name in ("view_users", "view_groups", "change_users", "change_groups", "owner"):
        assert properties[name]["default"] is None
    assert properties["clear_owner"]["default"] is False


async def test_the_description_says_how_it_differs_from_the_other_tools_that_set_permissions() -> (
    None
):
    async with Client(server(fake())) as client:
        tools = {t.name: t for t in await client.list_tools()}

    description = " ".join((tools["set_permissions"].description or "").split())
    assert "merge" in description
    assert "replace" in description
    assert "bulk_edit_taxonomy" in description
    assert "update_document" in description
    assert "list_users" in description
    assert "never wipes" in description or "never removes" in description
    assert "every call that changes anything needs confirm" in description


async def test_the_lookups_say_how_they_differ_from_list_taxonomy() -> None:
    async with Client(server(fake())) as client:
        tools = {t.name: t for t in await client.list_tools()}

    for name in ("list_users", "list_groups"):
        assert "list_taxonomy" in " ".join((tools[name].description or "").split())


# --- set_permissions ---------------------------------------------------------------------------


async def test_a_merge_adds_a_grant_and_returns_the_access_by_name() -> None:
    source = fake()

    output = await call(
        source,
        "set_permissions",
        kind="document",
        items=[PUBLIC],
        view_users=["e2e-reader"],
        change_groups=["e2e-group"],
        confirm=True,
    )

    [row] = objects(output)
    assert row["web_url"] == "http://paperless.test/documents/1/details"
    access = cast("Json", row["access"])
    assert cast("list[Json]", access["view_users"]) == [{"id": READER_ID, "name": "e2e-reader"}]
    assert cast("list[Json]", access["change_groups"]) == [{"id": 1, "name": "e2e-group"}]
    assert (output["kind"], output["mode"], output["changed_count"]) == ("document", "merge", 1)


async def test_a_merge_without_confirm_is_an_error_naming_who_gains_access() -> None:
    state = seeded()
    source = fake(state, writes=False)

    message = await refusal(
        source, "set_permissions", kind="document", items=[PUBLIC], view_users=["e2e-reader"]
    )

    assert "Confirming would change the permissions of 1 document" in message
    assert "gains view: user 'e2e-reader'" in message
    assert state.documents[0].viewers == set()


async def test_a_replace_without_confirm_is_an_error_naming_who_loses_access() -> None:
    state = seeded()
    state.documents[0].viewers = {READER_ID}
    source = fake(state, writes=False)

    message = await refusal(
        source, "set_permissions", kind="document", items=[PUBLIC], mode="replace", view_users=[]
    )

    assert "Nothing was changed. Confirming would replace the permissions of 1 document" in message
    assert "loses view: user 'e2e-reader'" in message
    assert "confirm=true" in message


async def test_a_confirmed_replace_replaces_the_given_list() -> None:
    state = seeded()
    state.documents[0].viewers = {READER_ID}
    source = fake(state)

    output = await call(
        source,
        "set_permissions",
        kind="document",
        items=[PUBLIC],
        mode="replace",
        view_users=[],
        confirm=True,
    )

    assert state.documents[0].viewers == set()
    assert output["changed_count"] == 1


async def test_an_owner_change_is_an_error_until_confirmed() -> None:
    state = seeded()
    source = fake(state, writes=False)

    message = await refusal(source, "set_permissions", kind="document", items=[PRIVATE], owner=3)

    assert "the owner changes from 'admin' to 'e2e-reader'" in message
    assert state.documents[1].owner == 1 + 1

    confirmed = fake(state)
    _ = await call(
        confirmed, "set_permissions", kind="document", items=[PRIVATE], owner=3, confirm=True
    )
    assert state.documents[1].owner == READER_ID


async def test_taxonomy_objects_are_named_and_unknown_names_are_errors() -> None:
    source = fake()

    output = await call(
        source,
        "set_permissions",
        kind="tag",
        items=["e2e-finance"],
        view_users=["e2e-reader"],
        confirm=True,
    )
    message = await refusal(
        source, "set_permissions", kind="tag", items=["e2e-finance"], view_users=["ghost"]
    )

    assert objects(output)[0]["web_url"] is None
    assert "No user is named 'ghost'" in message


async def test_a_selection_outside_the_limits_is_refused_by_the_schema() -> None:
    async with Client(server(fake())) as client:
        for items in ([], list(range(1, 102))):
            result = await client.call_tool(
                "set_permissions",
                {"kind": "document", "items": items, "view_users": [3]},
                raise_on_error=False,
            )
            assert result.is_error


async def test_nothing_to_change_is_an_error() -> None:
    message = await refusal(fake(), "set_permissions", kind="document", items=[PUBLIC])

    assert "Nothing to change" in message


# --- lookups -----------------------------------------------------------------------------------


async def test_list_users_returns_compact_rows_without_e_mail_or_permissions() -> None:
    output = await call(fake(), "list_users", name_contains="e2e")

    assert output["count"] == 2
    users = cast("list[Json]", output["users"])
    assert [u["username"] for u in users] == ["e2e-blind", "e2e-reader"]
    assert set(users[0]) == {"id", "username", "name", "is_active", "is_superuser", "group_ids"}
    assert (output["page"], output["next_page"]) == (1, None)


async def test_list_users_pages() -> None:
    output = await call(fake(), "list_users", page=2, page_size=2)

    assert [u["username"] for u in cast("list[Json]", output["users"])] == ["e2e-reader"]
    assert output["next_page"] is None


async def test_list_groups_returns_compact_rows() -> None:
    output = await call(fake(), "list_groups")

    assert cast("list[Json]", output["groups"]) == [{"id": 1, "name": "e2e-group"}]
    assert output["count"] == 1


async def test_a_user_who_may_not_view_users_gets_an_error_naming_the_permission() -> None:
    reader = FakePaperless(seeded(), user="e2e-reader")

    message = await refusal(reader, "list_users")

    assert "permission" in message


@pytest.mark.decision("decision-87")
async def test_a_mail_account_is_shared_after_a_confirmation_that_says_what_it_gives() -> None:
    state = seeded()
    state.mail_accounts.append(
        FakeMailAccount(
            id=5,
            name="Work",
            imap_server="h",
            imap_port=1,
            username="u",
            password="pa55-x",
            owner=2,
        )
    )
    source = fake(state)
    arguments = {"kind": "mail_account", "items": [5], "change_users": ["e2e-reader"]}

    message = await refusal(source, "set_permissions", **arguments)
    output = await call(source, "set_permissions", **arguments, confirm=True)

    assert "point it at another server" in message
    assert "grant on an account is none on its rules" in message
    assert output["changed_count"] == 1
    assert "pa55-x" not in str(output) + message
    assert state.mail_accounts[0].editors == {3}
