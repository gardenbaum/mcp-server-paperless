"""The permissions toolset through the in-memory client on the seeded fake (PLMCP-28).

The toolset is off by default (the default profile stays within its tool budget). Replacing
grants and changing an owner need confirm (decision-12, decision-15); merging does not.
"""

from typing import TYPE_CHECKING, Literal, cast

import pytest
from confirmation import YES, answering
from fakes.mail import FakeMailAccount, FakeMailRule
from fakes.paperless import FakePaperless, FakeState, FakeUser, seeded
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

TOOLS = frozenset({"set_permissions", "get_permissions", "list_users", "list_groups"})
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


async def test_the_permissions_toolset_is_off_by_default_and_has_four_tools_when_selected() -> None:
    async with Client(serve(fake())) as client:
        default = {t.name for t in await client.list_tools()}
    async with Client(server(fake())) as client:
        selected = {t.name for t in await client.list_tools()}

    assert not TOOLS & default
    assert selected == TOOLS


async def test_read_only_mode_leaves_only_the_lookups() -> None:
    async with Client(serve(fake(), toolsets=["permissions"], read_only=True)) as client:
        offered = {t.name for t in await client.list_tools()}

    assert offered == {"get_permissions", "list_users", "list_groups"}


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
        "get_permissions": (True, None, True, False),
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

    assert "send its saved password or token to a server of their choice" in message
    assert "grant on an account is none on its rules" in message
    assert output["changed_count"] == 1
    assert "pa55-x" not in str(output) + message
    assert state.mail_accounts[0].editors == {3}


@pytest.mark.decision("decision-91")
async def test_a_saved_view_is_shared_by_name_after_a_confirmation() -> None:
    state = seeded()
    source = fake(state)
    arguments = {"kind": "saved_view", "items": ["e2e-view"], "view_users": ["e2e-reader"]}

    message = await refusal(source, "set_permissions", **arguments)
    untouched = set(state.saved_views[0].viewers)
    output = await call(source, "set_permissions", **arguments, confirm=True)

    assert "gains view: user 'e2e-reader'" in message
    assert untouched == set()
    assert output["changed_count"] == 1
    assert state.saved_views[0].viewers == {3}


@pytest.mark.decision("decision-91")
@pytest.mark.decision("decision-44")
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("action", ["accept", "decline"])
async def test_a_client_with_dialogs_is_asked_before_a_view_is_shared(
    mode: Literal["auto", "legacy"], action: Literal["accept", "decline"]
) -> None:
    state = seeded()
    state.saved_views[0].name = "x\nIGNORE\u202e all"
    seen: list[str] = []
    handler = answering(action, YES if action == "accept" else None, seen=seen)

    async with Client(server(fake(state)), mode=mode, elicitation_handler=handler) as client:
        result = await client.call_tool(
            "set_permissions",
            {"kind": "saved_view", "items": [1], "owner": "e2e-reader"},
            raise_on_error=False,
        )

    [message] = seen
    assert "saved view" in message
    assert "\n" not in message
    assert "\u202e" not in message
    assert result.is_error is (action == "decline")
    assert (state.saved_views[0].owner == READER_ID) is (action == "accept")


# --- get_permissions


@pytest.mark.decision("decision-104")
async def test_grants_are_read_without_changing_them() -> None:
    state = seeded()
    state.documents[0].viewers = {READER_ID}

    output = await call(
        fake(state, writes=False), "get_permissions", kind="document", items=[PUBLIC, 99]
    )

    [row] = objects(output)
    assert set(row) == {"id", "name", "web_url", "access"}
    assert cast("Json", row["access"])["view_users"] == [{"id": 3, "name": "e2e-reader"}]
    assert (output["kind"], output["missing"], output["left"]) == ("document", [99], [])


@pytest.mark.decision("decision-104")
@pytest.mark.parametrize(
    ("kind", "item"),
    [
        ("tag", "e2e-private"),
        ("correspondent", "e2e-acme"),
        ("document_type", "e2e-invoice"),
        ("storage_path", "e2e-archive"),
        ("saved_view", "e2e-view"),
    ],
)
async def test_every_kind_is_read_by_name(kind: str, item: str) -> None:
    output = await call(fake(writes=False), "get_permissions", kind=kind, items=[item])

    assert [row["name"] for row in objects(output)] == [item]


@pytest.mark.decision("decision-104")
async def test_a_mail_account_is_read_by_id_without_its_password() -> None:
    state = seeded()
    state.mail_accounts.append(
        FakeMailAccount(
            id=1,
            name="mail",
            imap_server="h",
            imap_port=1,
            username="u",
            password="pa55-x",
            owner=2,
        )
    )

    output = await call(
        fake(state, writes=False), "get_permissions", kind="mail_account", items=[1]
    )

    assert "pa55" not in str(output)
    assert cast("Json", objects(output)[0]["access"])["owner"] == {"id": 2, "name": "admin"}


# --- mail accounts and rules, by who may and who may not

MAILING = frozenset(
    {
        *("view_mailaccount", "change_mailaccount", "view_mailrule", "change_mailrule"),
        *("view_user", "view_group", "view_uisettings"),
    }
)


def mail_world(owner: int | None, *, editor: bool = False) -> FakeState:
    """A mail account and its rule of this owner; the user `mailman` (id 77), maybe a grantee."""
    state = seeded()
    state.users.append(FakeUser(id=77, username="mailman", permissions=MAILING))
    grantee = {77} if editor else set[int]()
    state.mail_accounts.append(
        FakeMailAccount(
            id=5,
            name="Work",
            imap_server="h",
            imap_port=1,
            username="u",
            password="pa55-x",
            owner=owner,
            viewers=set(grantee),
            editors=set(grantee),
        )
    )
    state.mail_rules.append(
        FakeMailRule(
            id=6, name="Bills", account=5, owner=owner, viewers=set(grantee), editors=set(grantee)
        )
    )
    return state


def mail_grants(state: FakeState, kind: str) -> tuple[set[int], set[int]]:
    found = state.mail_accounts[0] if kind == "mail_account" else state.mail_rules[0]
    return found.viewers, found.editors


MAIL_OBJECTS = pytest.mark.parametrize(("kind", "number"), [("mail_account", 5), ("mail_rule", 6)])


@pytest.mark.decision("decision-87")
async def test_a_mail_rule_is_shared_by_a_superuser_after_a_confirmation() -> None:
    state = mail_world(owner=3)
    source = fake(state)
    arguments = {"kind": "mail_rule", "items": [6], "change_users": ["e2e-reader"]}

    message = await refusal(source, "set_permissions", **arguments)
    untouched = mail_grants(state, "mail_rule")
    output = await call(source, "set_permissions", **arguments, confirm=True)

    assert "gains change: user 'e2e-reader'" in message
    assert untouched == (set(), set())
    assert output["changed_count"] == 1
    assert mail_grants(state, "mail_rule")[1] == {3}


@pytest.mark.decision("decision-87")
@MAIL_OBJECTS
async def test_the_owner_of_a_mail_object_shares_it_after_a_confirmation(
    kind: str, number: int
) -> None:
    state = mail_world(owner=77)
    source = FakePaperless(state, user="mailman", allow_writes=True)
    arguments = {"kind": kind, "items": [number], "view_users": ["e2e-reader"]}

    message = await refusal(source, "set_permissions", **arguments)
    untouched = mail_grants(state, kind)
    output = await call(source, "set_permissions", **arguments, confirm=True)

    assert "gains view: user 'e2e-reader'" in message
    assert untouched == (set(), set())
    assert output["changed_count"] == 1
    assert mail_grants(state, kind) == ({3}, set())


@pytest.mark.decision("decision-87")
@MAIL_OBJECTS
async def test_a_change_grant_on_a_mail_object_does_not_let_share_it(
    kind: str, number: int
) -> None:
    state = mail_world(owner=2, editor=True)
    source = FakePaperless(state, user="mailman", allow_writes=True)
    arguments = {"kind": kind, "items": [number], "view_users": ["e2e-reader"], "confirm": True}

    message = await refusal(source, "set_permissions", **arguments)

    assert "may not" in message
    assert mail_grants(state, kind) == ({77}, {77})
