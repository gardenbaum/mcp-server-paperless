"""The mail toolset through the assembled server against the seeded Paperless stack.

The stack has no IMAP server and compose.yaml is not given one on purpose (PLMCP-29): a mail server
would be one more container for every developer and CI run, to test what the fake, the adapter
tests (which script the wire) and the contract tests already cover. So the connection test is run
against a host that refuses connections, which is the failure Paperless answers with an HTTP 500;
a login that works and a server that never answers are covered by the fake and the adapter tests.
A fetch is queued for such an account and fails in the background without touching anything. Rows
of processed mail are written only by a real fetch, so that listing is checked empty.

Every account and rule a test makes is deleted afterwards (rules first), also when it failed; the
stack is shared, and rows of other runs are not touched.
"""

from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import server_settings

from e2e.helpers import obj, parse, results
from mcp_server_paperless.server import create_server
from mcp_server_paperless.settings import PaperlessSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-18")]
type Json = dict[str, object]
type Connected = Client[FastMCPTransport]

PASSWORD = "e2e-Mail-Passw0rd-4711"
NEW_PASSWORD = "e2e other mail password"
REFUSING = "127.0.0.1"
PREFIX = "e2e-mail-"
FINANCE, ACME, INVOICE = "e2e-finance", "e2e-acme", "e2e-invoice"


@pytest.fixture
async def client(stack: Stack) -> AsyncIterator[Connected]:
    paperless = PaperlessSettings.model_validate(
        {"url": stack.base_url, "token": stack.admin_token}
    )
    server = create_server(paperless, server_settings(toolsets=["core", "mail"]))
    async with Client(server) as connected:
        yield connected
    # Whatever a test left behind, also when it failed; rows of other runs are not touched.
    for row in results(stack.admin.get("/api/mail_rules/")):
        if str(row["name"]).startswith(PREFIX):
            _ = stack.admin.delete(f"/api/mail_rules/{row['id']}/")
    for row in results(stack.admin.get("/api/mail_accounts/")):
        if str(row["name"]).startswith(PREFIX):
            _ = stack.admin.delete(f"/api/mail_accounts/{row['id']}/")


def clean(text: object) -> None:
    """Neither a password nor the mask Paperless puts in its answers is in what a client saw."""
    shown = str(text)
    assert PASSWORD not in shown
    assert NEW_PASSWORD not in shown
    assert "****" not in shown


# What Paperless shows instead of a password: ten asterisks, whatever its length.
MASK = "*" * 10


async def call(client: Connected, tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    clean(result)
    return result.structured_content


async def error(client: Connected, tool: str, **arguments: object) -> str:
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    clean(result)
    return str(result.content)


async def account(client: Connected, name: str, **more: object) -> Json:
    return await call(
        client,
        "create_mail_account",
        name=f"{PREFIX}{name}",
        imap_server=REFUSING,
        imap_port=1,
        username="me@example.com",
        password=PASSWORD,
        **more,
    )


async def test_an_account_is_created_read_listed_changed_and_never_shows_a_password(
    client: Connected, stack: Stack
) -> None:
    made = await account(client, "main", imap_security="starttls")

    assert (made["imap_server"], made["imap_port"], made["imap_security"]) == (
        REFUSING,
        1,
        "starttls",
    )
    assert made["account_type"] == "imap"
    # Paperless itself masks the password (ten asterisks whatever its length); the tools drop
    # even that.
    raw = obj(parse(stack.admin.get(f"/api/mail_accounts/{made['id']}/")))
    assert raw["password"] == MASK
    read = await call(client, "get_mail_account", account_id=made["id"])
    listed = await call(client, "list_mail_accounts", page_size=100)
    assert read == made
    assert made["id"] in {row["id"] for row in cast("list[Json]", listed["accounts"])}
    renamed = await call(
        client, "update_mail_account", account_id=made["id"], name=f"{PREFIX}renamed"
    )
    assert renamed["name"] == f"{PREFIX}renamed"
    again = await call(client, "update_mail_account", account_id=made["id"], password=NEW_PASSWORD)
    assert again["id"] == made["id"]
    raw = obj(parse(stack.admin.get(f"/api/mail_accounts/{made['id']}/")))
    assert raw["password"] == MASK


async def test_a_bad_password_is_refused_without_echoing_it(client: Connected) -> None:
    for password in ("", "***"):
        text = await error(
            client,
            "create_mail_account",
            name=f"{PREFIX}bad",
            imap_server=REFUSING,
            imap_port=1,
            username="u",
            password=password,
        )

        assert "password" in text


async def test_pointing_an_account_elsewhere_needs_confirm_unless_the_password_is_replaced(
    client: Connected, stack: Stack
) -> None:
    made = await account(client, "redirect")

    text = await error(
        client, "update_mail_account", account_id=made["id"], imap_server="mail.other.example"
    )

    assert f"point mail account '{PREFIX}redirect' (id {made['id']}) at mail.other.example" in text
    assert "sends it to the new server" in text
    assert obj(parse(stack.admin.get(f"/api/mail_accounts/{made['id']}/")))["imap_server"] == (
        REFUSING
    )
    moved = await call(
        client,
        "update_mail_account",
        account_id=made["id"],
        imap_server="mail.other.example",
        password=NEW_PASSWORD,
    )
    assert moved["imap_server"] == "mail.other.example"


async def test_a_connection_to_a_host_that_refuses_is_a_failed_result(client: Connected) -> None:
    made = await account(client, "refused")

    tested = await call(client, "test_mail_account", account_id=made["id"])

    assert tested["success"] is False
    assert f"Mail account '{PREFIX}refused': Paperless could not connect to the mail server" in str(
        tested["message"]
    )


async def test_testing_an_account_that_does_not_exist_is_an_error(client: Connected) -> None:
    text = await error(client, "test_mail_account", account_id=999_999)

    assert "No mail account 999999" in text


async def test_rules_take_taxonomy_by_name_and_list_it_back_with_names(client: Connected) -> None:
    made = await account(client, "rules")

    rule = await call(
        client,
        "create_mail_rule",
        name=f"{PREFIX}invoices",
        account_id=made["id"],
        assign_tags=[FINANCE],
        assign_correspondent=ACME,
        assign_document_type=INVOICE,
        owner="admin",
        filter_from="billing@example.com",
        folder="Billing",
        maximum_age_days=14,
        action="flag",
    )

    assert [t["name"] for t in cast("list[Json]", rule["assign_tags"])] == [FINANCE]
    assert cast("Json", rule["assign_correspondent"])["name"] == ACME
    assert cast("Json", rule["assign_document_type"])["name"] == INVOICE
    assert cast("Json", rule["owner"])["name"] == "admin"
    assert rule["assign_correspondent_from"] == "selected"
    assert (rule["action"], rule["folder"], rule["maximum_age_days"]) == ("flag", "Billing", 14)
    listed = await call(client, "list_mail_rules", account_id=made["id"])
    assert cast("list[Json]", listed["rules"]) == [rule]
    assert await call(client, "get_mail_rule", rule_id=rule["id"]) == rule
    changed = await call(
        client,
        "update_mail_rule",
        rule_id=rule["id"],
        filter_from="",
        assign_tags=[],
        clear=["assign_correspondent"],
        action="mark_read",
    )
    assert (changed["filter_from"], changed["assign_tags"]) == (None, [])
    assert (changed["assign_correspondent"], changed["action"]) == (None, "mark_read")


async def test_a_name_that_matches_nothing_is_an_error(client: Connected) -> None:
    made = await account(client, "unknown")

    text = await error(
        client,
        "create_mail_rule",
        name=f"{PREFIX}x",
        account_id=made["id"],
        assign_tags=["no-such-tag"],
    )

    assert "No tag is named 'no-such-tag'" in text


async def test_a_rule_that_deletes_mail_is_refused_until_confirmed(
    client: Connected, stack: Stack
) -> None:
    made = await account(client, "purge")
    before = len(results(stack.admin.get("/api/mail_rules/")))

    text = await error(
        client,
        "create_mail_rule",
        name=f"{PREFIX}purge",
        account_id=made["id"],
        action="delete",
        filter_from="spam@example.com",
    )

    assert f"create the enabled mail rule '{PREFIX}purge' for account '{PREFIX}purge'" in text
    assert "deletes each mail from the mail server" in text
    assert "from 'spam@example.com'" in text
    assert len(results(stack.admin.get("/api/mail_rules/"))) == before
    rule = await call(
        client,
        "create_mail_rule",
        name=f"{PREFIX}purge",
        account_id=made["id"],
        action="delete",
        confirm=True,
    )
    widened = await error(client, "update_mail_rule", rule_id=rule["id"], folder="Other")
    assert "then deletes each mail from the mail server" in widened
    renamed = await call(
        client, "update_mail_rule", rule_id=rule["id"], name=f"{PREFIX}purge-renamed"
    )
    assert renamed["name"] == f"{PREFIX}purge-renamed"


async def test_a_fetch_names_what_the_rules_do_and_is_queued_once_confirmed(
    client: Connected,
) -> None:
    made = await account(client, "fetch")
    rule = await call(
        client,
        "create_mail_rule",
        name=f"{PREFIX}moves",
        account_id=made["id"],
        action="move",
        action_parameter="Archive",
        confirm=True,
    )

    text = await error(client, "process_mail_account", account_id=made["id"])
    started = await call(client, "process_mail_account", account_id=made["id"], confirm=True)

    assert f"rule '{PREFIX}moves' (id {rule['id']}) moves the mail to 'Archive'" in text.replace(
        "Rule", "rule"
    )
    assert started["id"] == made["id"]
    assert "queued a fetch" in str(started["message"])


async def test_processed_mail_of_a_rule_that_never_ran_is_empty(client: Connected) -> None:
    made = await account(client, "empty")
    rule = await call(client, "create_mail_rule", name=f"{PREFIX}idle", account_id=made["id"])

    listed = await call(client, "list_processed_mail", rule_id=rule["id"], status="FAILED")

    assert (listed["count"], listed["next_page"], listed["mails"]) == (0, None, [])


async def test_deleting_a_rule_and_an_account_needs_confirm_and_takes_the_rules_along(
    client: Connected, stack: Stack
) -> None:
    made = await account(client, "gone")
    rule = await call(client, "create_mail_rule", name=f"{PREFIX}gone", account_id=made["id"])

    ruled = await error(client, "delete_mail_rule", rule_id=rule["id"])
    accounted = await error(client, "delete_mail_account", account_id=made["id"])

    assert (
        f"delete the mail rule '{PREFIX}gone' (id {rule['id']}) of account '{PREFIX}gone'" in ruled
    )
    assert f"with its 1 rule ('{PREFIX}gone')" in accounted
    assert stack.admin.get(f"/api/mail_accounts/{made['id']}/").is_success
    gone = await call(client, "delete_mail_account", account_id=made["id"], confirm=True)
    assert gone["id"] == made["id"]
    assert stack.admin.get(f"/api/mail_rules/{rule['id']}/").status_code == 404
    again = await error(client, "delete_mail_rule", rule_id=rule["id"], confirm=True)
    assert f"No mail rule {rule['id']}" in again
