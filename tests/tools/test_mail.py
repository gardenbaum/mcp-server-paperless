"""The mail toolset through the in-memory client on the seeded fake (PLMCP-29).

Every scenario that handles a password runs with `PASSWORD` and checks that neither it nor any run
of asterisks (the mask Paperless puts in its answers) appears in any tool result, error text,
structured content or log line.
"""

import logging
from typing import cast

import pytest
from fakes.mail import FakeMailAccount, FakeMailRule, FakeProcessedMail
from fakes.paperless import FakePaperless, FakeState, seeded
from fastmcp import Client
from harness import serve

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-4"),
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-15"),
    pytest.mark.decision("decision-18"),
]

type Json = dict[str, object]

TOOLS = {
    "list_mail_accounts",
    "get_mail_account",
    "create_mail_account",
    "update_mail_account",
    "delete_mail_account",
    "test_mail_account",
    "process_mail_account",
    "list_mail_rules",
    "get_mail_rule",
    "create_mail_rule",
    "update_mail_rule",
    "delete_mail_rule",
    "list_processed_mail",
}
READ_ONLY = {
    "list_mail_accounts",
    "get_mail_account",
    "list_mail_rules",
    "get_mail_rule",
    "list_processed_mail",
}
PASSWORD = "S3cret-Passw0rd!x"
HOST = "imap.example.com"


def fake(
    state: FakeState | None = None, *, writes: bool = True, user: str = "admin"
) -> FakePaperless:
    return FakePaperless(state or seeded(), user=user, allow_writes=writes)


def with_account(state: FakeState, name: str = "Invoices") -> int:
    number = max((a.id for a in state.mail_accounts), default=0) + 1
    state.mail_accounts.append(
        FakeMailAccount(
            id=number,
            name=name,
            imap_server=HOST,
            imap_port=993,
            username="me@example.com",
            password=PASSWORD,
            owner=2,
        )
    )
    return number


def with_rule(state: FakeState, account: int, name: str = "Bills", **fields: object) -> int:
    number = max((r.id for r in state.mail_rules), default=0) + 1
    state.mail_rules.append(
        FakeMailRule(id=number, name=name, account=account, owner=2, **fields)  # pyright: ignore[reportArgumentType] -- test data
    )
    return number


def server(source: FakePaperless):  # noqa: ANN201 -- the assembled FastMCP server
    return serve(source, toolsets=["core", "mail"])


async def call(source: FakePaperless, tool: str, **arguments: object) -> Json:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    assert PASSWORD not in repr(result)
    return result.structured_content


async def refusal(source: FakePaperless, tool: str, **arguments: object) -> str:
    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    assert PASSWORD not in repr(result)
    return str(result.content)


async def test_mail_is_not_in_the_default_profile() -> None:
    async with Client(serve(fake())) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names.isdisjoint(TOOLS)


async def test_the_mail_toolset_offers_thirteen_tools_and_read_only_mode_keeps_the_reads() -> None:
    async with Client(serve(fake(), toolsets=["mail"])) as client:
        names = {tool.name for tool in await client.list_tools()}
    async with Client(serve(fake(), toolsets=["mail"], read_only=True)) as client:
        read_only = {tool.name for tool in await client.list_tools()}

    assert names == TOOLS
    assert read_only == READ_ONLY


async def test_the_tools_that_contact_the_mail_server_are_marked_open_world() -> None:
    async with Client(serve(fake(), toolsets=["mail"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    open_world = {
        name
        for name, tool in tools.items()
        if tool.annotations is not None and tool.annotations.open_world_hint is True
    }
    assert open_world == {"test_mail_account", "process_mail_account"}
    fetch = tools["process_mail_account"].annotations
    assert fetch is not None
    assert (fetch.read_only_hint, fetch.destructive_hint, fetch.idempotent_hint) == (
        False,
        True,
        False,
    )
    check = tools["test_mail_account"].annotations
    assert check is not None
    assert (check.read_only_hint, check.destructive_hint) == (False, False)


async def test_the_password_is_write_only_and_the_confirming_tools_default_it_to_false() -> None:
    async with Client(serve(fake(), toolsets=["mail"])) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    for name in ("create_mail_account", "update_mail_account"):
        properties = cast("dict[str, Json]", tools[name].input_schema["properties"])
        password = properties["password"]
        shown = cast("list[Json]", password["anyOf"])[0] if "anyOf" in password else password
        assert (shown["type"], shown["format"], shown["writeOnly"]) == ("string", "password", True)
    for name in (
        *("update_mail_account", "delete_mail_account", "process_mail_account"),
        *("create_mail_rule", "update_mail_rule", "delete_mail_rule"),
    ):
        properties = cast("dict[str, Json]", tools[name].input_schema["properties"])
        assert properties["confirm"]["default"] is False
        assert "required" not in tools[name].input_schema or "confirm" not in cast(
            "list[str]", tools[name].input_schema["required"]
        )


async def test_an_account_is_created_listed_read_updated_and_never_shows_the_password(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    source = fake()

    made = await call(
        source,
        "create_mail_account",
        name="Invoices",
        imap_server=HOST,
        imap_port=993,
        username="me@example.com",
        password=PASSWORD,
        imap_security="starttls",
    )
    listed = await call(source, "list_mail_accounts")
    read = await call(source, "get_mail_account", account_id=made["id"])
    changed = await call(source, "update_mail_account", account_id=made["id"], name="Bills")
    keyed = await call(
        source, "update_mail_account", account_id=made["id"], password="another new password"
    )

    assert (made["name"], made["imap_security"], made["account_type"]) == (
        "Invoices",
        "starttls",
        "imap",
    )
    assert cast("list[Json]", listed["accounts"])[0]["name"] == "Invoices"
    assert read["username"] == "me@example.com"
    assert changed["name"] == "Bills"
    assert source.state.mail_accounts[0].password == "another new password"
    everything = repr([made, listed, read, changed, keyed]) + caplog.text
    assert PASSWORD not in everything
    assert "another new password" not in everything
    assert "password" not in everything.lower().replace("passwords", "")
    assert "***" not in everything


@pytest.mark.parametrize(
    ("password", "echo"),
    [
        pytest.param(1234.5678, "1234.5678", id="float"),
        pytest.param(987654321, "987654321", id="int"),
        pytest.param(["hunter2-x", "y"], "hunter2-x", id="list"),
        pytest.param({"p": "hunter2-x"}, "hunter2-x", id="object"),
        pytest.param(True, "True", id="bool"),
        pytest.param(None, "\0", id="null"),
    ],
)
@pytest.mark.parametrize("tool", ["create_mail_account", "update_mail_account"])
async def test_a_password_that_is_not_text_is_refused_without_quoting_it(
    tool: str, password: object, echo: str
) -> None:
    source = fake()
    number = with_account(source.state)
    arguments: Json = {"password": password}
    if tool == "create_mail_account":
        arguments |= {"name": "x", "imap_server": HOST, "imap_port": 993, "username": "u"}
    else:
        arguments |= {"account_id": number, "name": "renamed"}

    async with Client(server(source)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)

    shown = str(result.content)
    assert echo not in shown
    assert "input_value" not in shown
    if password is None and tool == "update_mail_account":
        assert not result.is_error  # an explicit null is "not given"
    else:
        assert result.is_error
        assert "The password must be text" in shown
    assert source.state.mail_accounts[-1].password == PASSWORD or tool == "create_mail_account"


@pytest.mark.parametrize("password", ["", "   ", "*", "*****"])
async def test_a_blank_or_asterisk_password_is_an_error_that_does_not_echo_it(
    password: str,
) -> None:
    source = fake()

    text = await refusal(
        source,
        "create_mail_account",
        name="x",
        imap_server=HOST,
        imap_port=993,
        username="u",
        password=password,
    )

    assert "password" in text
    assert "**" not in text
    assert source.state.mail_accounts == []


async def test_a_server_change_without_a_new_password_is_refused_without_the_password() -> None:
    source = fake()
    number = with_account(source.state)

    text = await refusal(source, "update_mail_account", account_id=number, imap_server="evil.test")

    assert "Nothing was changed. Confirming would point mail account 'Invoices'" in text
    assert "evil.test" in text
    assert source.state.mail_accounts[0].imap_server == HOST


async def test_a_confirmed_server_change_goes_through() -> None:
    source = fake()
    number = with_account(source.state)

    changed = await call(
        source, "update_mail_account", account_id=number, imap_server="new.example", confirm=True
    )

    assert changed["imap_server"] == "new.example"


async def test_deleting_an_account_is_a_two_step_flow() -> None:
    source = fake()
    number = with_account(source.state)
    _ = with_rule(source.state, number)

    text = await refusal(source, "delete_mail_account", account_id=number)
    gone = await call(source, "delete_mail_account", account_id=number, confirm=True)

    assert "delete mail account 'Invoices' (id 1) with its 1 rule ('Bills')" in text
    assert gone["id"] == number
    assert source.state.mail_accounts == []


async def test_a_connection_test_reports_the_outcome_and_never_the_password() -> None:
    source = fake()
    number = with_account(source.state)
    source.state.mail_hosts.add(HOST)
    source.state.mail_logins.add((HOST, 993, "me@example.com", PASSWORD))
    wrong = with_account(source.state, "Wrong")
    source.state.mail_accounts[1].password = "not-the-password"

    good = await call(source, "test_mail_account", account_id=number)
    bad = await call(source, "test_mail_account", account_id=wrong)

    assert good["success"] is True
    assert bad["success"] is False
    assert "or log in" in str(bad["message"])
    assert "not-the-password" not in repr(bad)


async def test_a_fetch_needs_confirm_and_is_then_queued() -> None:
    source = fake()
    number = with_account(source.state)
    _ = with_rule(source.state, number, action="delete")

    text = await refusal(source, "process_mail_account", account_id=number)
    started = await call(source, "process_mail_account", account_id=number, confirm=True)

    assert "Rule 'Bills' (id 1) deletes the mail" in text
    assert source.state.mail_fetches == [number]
    assert "queued a fetch" in str(started["message"])


async def test_a_rule_is_created_with_taxonomy_by_name_listed_updated_and_deleted() -> None:
    source = fake()
    account = with_account(source.state)

    made = await call(
        source,
        "create_mail_rule",
        name="Invoices",
        account_id=account,
        assign_tags=["e2e-finance", "e2e-invoices"],
        assign_correspondent="e2e-acme",
        assign_document_type="e2e-invoice",
        filter_from="billing@example.com",
        maximum_age_days=14,
    )
    listed = await call(source, "list_mail_rules", account_id=account)
    read = await call(source, "get_mail_rule", rule_id=made["id"])
    changed = await call(
        source,
        "update_mail_rule",
        rule_id=made["id"],
        filter_from="",
        clear=["assign_correspondent"],
        assign_tags=[],
    )
    text = await refusal(source, "delete_mail_rule", rule_id=made["id"])
    gone = await call(source, "delete_mail_rule", rule_id=made["id"], confirm=True)

    assert [t["name"] for t in cast("list[Json]", made["assign_tags"])] == [
        "e2e-finance",
        "e2e-invoices",
    ]
    assert cast("Json", made["assign_correspondent"])["name"] == "e2e-acme"
    assert made["assign_correspondent_from"] == "selected"
    assert (made["maximum_age_days"], made["action"]) == (14, "mark_read")
    assert cast("list[Json]", listed["rules"])[0]["id"] == made["id"]
    assert read == made
    assert (changed["filter_from"], changed["assign_correspondent"], changed["assign_tags"]) == (
        None,
        None,
        [],
    )
    assert "delete the mail rule 'Invoices' (id 1) of account 'Invoices'" in text
    assert gone["id"] == made["id"]
    assert source.state.mail_rules == []


async def test_a_rule_that_deletes_mail_is_refused_without_confirm_and_nothing_is_stored() -> None:
    source = fake()
    account = with_account(source.state)

    text = await refusal(
        source, "create_mail_rule", name="Purge", account_id=account, action="delete"
    )

    assert "deletes each mail from the mail server" in text
    assert source.state.mail_rules == []


async def test_a_name_that_matches_nothing_is_an_error_naming_the_closest() -> None:
    source = fake()
    account = with_account(source.state)

    text = await refusal(
        source, "create_mail_rule", name="R", account_id=account, assign_tags=["e2e-financ"]
    )

    assert "No tag is named 'e2e-financ'" in text
    assert "e2e-finance" in text


async def test_processed_mail_is_listed_as_compact_rows() -> None:
    source = fake()
    rule = with_rule(source.state, with_account(source.state))
    source.state.processed_mail.append(
        FakeProcessedMail(id=1, rule=rule, subject="Your invoice", status="FAILED", error="boom")
    )

    listed = await call(source, "list_processed_mail", rule_id=rule, status="FAILED")

    assert listed["count"] == 1
    [row] = cast("list[Json]", listed["mails"])
    assert (row["subject"], row["status"], row["error"], row["rule_id"]) == (
        "Your invoice",
        "FAILED",
        "boom",
        rule,
    )
    assert "uid" not in row


async def test_the_page_size_is_bounded() -> None:
    source = fake()
    async with Client(server(source)) as client:
        result = await client.call_tool(
            "list_processed_mail", {"page_size": 101}, raise_on_error=False
        )

    assert result.is_error


async def test_a_user_without_the_mail_permissions_gets_a_permission_error() -> None:
    source = fake(user="e2e-blind")

    text = await refusal(source, "list_mail_accounts")

    assert "may not perform this action" in text
