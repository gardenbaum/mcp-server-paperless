"""PaperlessMailReader and PaperlessMailWriter on the Paperless 3.4.2 wire format."""

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from pydantic import SecretStr

from adapter.conftest import streaming
from mcp_server_paperless.domain.errors import (
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    UnexpectedAnswer,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.domain.mail import (
    MailAccount,
    MailAccountValues,
    MailRule,
    MailRulePage,
    MailRuleValues,
    ProcessedMail,
)
from mcp_server_paperless.paperless.mail import (
    TEST_TIMEOUT,
    PaperlessMailReader,
    PaperlessMailWriter,
    connected,
    failed_login,
    silent_server,
    unreachable_server,
)
from mcp_server_paperless.paperless.pagination import MAX_ITEMS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from adapter.conftest import ClientFactory

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-18")]
type Json = dict[str, object]

PASSWORD = "hunter2-Tr0ub4dor&3"
MASK = "*" * len(PASSWORD)

ACCOUNT_WIRE: Json = {
    "id": 1,
    "name": "Invoices",
    "imap_server": "imap.example.com",
    "imap_port": 993,
    "imap_security": 2,
    "username": "me@example.com",
    "password": MASK,
    "character_set": "UTF-8",
    "is_token": False,
    "owner": 2,
    "user_can_change": True,
    "account_type": 1,
    "expiration": None,
}
ACCOUNT = MailAccount(
    id=1,
    name="Invoices",
    imap_server="imap.example.com",
    imap_port=993,
    imap_security="ssl",
    username="me@example.com",
    character_set="UTF-8",
    account_type="imap",
    is_token=False,
    owner=2,
    can_change=True,
    expiration=None,
)
RULE_WIRE: Json = {
    "id": 7,
    "name": "Invoices",
    "account": 1,
    "enabled": True,
    "folder": "INBOX",
    "filter_from": "billing@example.com",
    "filter_to": None,
    "filter_subject": "",
    "filter_body": None,
    "filter_attachment_filename_include": "*.pdf",
    "filter_attachment_filename_exclude": None,
    "maximum_age": 30,
    "action": 2,
    "action_parameter": "Archive",
    "assign_title_from": 2,
    "assign_tags": [3, 4],
    "assign_correspondent_from": 4,
    "assign_correspondent": 5,
    "assign_document_type": 6,
    "assign_owner_from_rule": True,
    "order": 1,
    "attachment_type": 2,
    "consumption_scope": 3,
    "pdf_layout": 3,
    "owner": 2,
    "user_can_change": False,
    "stop_processing": True,
}
RULE = MailRule(
    id=7,
    name="Invoices",
    account_id=1,
    enabled=True,
    order=1,
    folder="INBOX",
    filter_from="billing@example.com",
    filter_to=None,
    filter_subject=None,
    filter_body=None,
    filter_attachment_filename_include="*.pdf",
    filter_attachment_filename_exclude=None,
    maximum_age=30,
    attachment_type="all_files",
    consumption_scope="eml_and_attachments",
    pdf_layout="html",
    action="move",
    action_parameter="Archive",
    assign_title_from="filename",
    assign_tags=(3, 4),
    assign_correspondent_from="selected",
    assign_correspondent=5,
    assign_document_type=6,
    assign_owner_from_rule=True,
    stop_processing=True,
    owner=2,
    can_change=False,
)
PROCESSED_WIRE: Json = {
    "id": 9,
    "owner": 2,
    "rule": 7,
    "folder": "INBOX",
    "uid": "1234",
    "subject": "Your invoice",
    "received": "2026-11-10T08:00:00Z",
    "processed": "2026-11-10T08:10:00.123456Z",
    "status": "FAILED",
    "error": "boom",
}

PROCESSED = ProcessedMail(
    id=9,
    owner=2,
    rule_id=7,
    folder="INBOX",
    uid="1234",
    subject="Your invoice",
    received=datetime(2026, 11, 10, 8, tzinfo=UTC),
    processed=datetime(2026, 11, 10, 8, 10, 0, 123456, tzinfo=UTC),
    status="FAILED",
    error="boom",
)


class Recording:
    """Answers each request with the next scripted response and records method, path, body."""

    def __init__(self, *answers: httpx2.Response) -> None:
        self.answers: list[httpx2.Response] = list(answers)
        self.sent: list[tuple[str, str, Json | None, dict[str, str]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = cast("Json", json.loads(request.content)) if request.content else None
        self.sent.append((request.method, request.url.path, body, dict(request.url.params)))
        return self.answers.pop(0)


def page(*rows: Json, next_link: str | None = None, count: int | None = None) -> httpx2.Response:
    body = {
        "count": len(rows) if count is None else count,
        "next": next_link,
        "previous": None,
        "results": list(rows),
    }
    return httpx2.Response(200, json=body)


def ok(body: Json) -> httpx2.Response:
    return httpx2.Response(200, json=body)


# --- reading


async def test_the_accounts_are_listed_without_any_trace_of_the_password(
    make_client: ClientFactory,
) -> None:
    server = Recording(page(ACCOUNT_WIRE))

    found = await PaperlessMailReader(make_client(server)).list_mail_accounts()

    assert found == (ACCOUNT,)
    assert server.sent == [("GET", "/api/mail_accounts/", None, {"page": "1", "page_size": "100"})]
    assert MASK not in repr(found)
    assert "password" not in found[0].model_dump_json()


async def test_every_page_of_accounts_is_read(make_client: ClientFactory) -> None:
    link = "http://paperless.test/api/mail_accounts/?page=2&page_size=100"
    second = {**ACCOUNT_WIRE, "id": 2}
    server = Recording(page(ACCOUNT_WIRE, next_link=link, count=2), page(second, count=2))

    found = await PaperlessMailReader(make_client(server)).list_mail_accounts()

    assert [item.id for item in found] == [1, 2]


@pytest.mark.parametrize("kind", ["accounts", "rules"])
async def test_more_rows_than_the_cap_fail_instead_of_showing_a_part(
    make_client: ClientFactory, kind: str
) -> None:
    wire = ACCOUNT_WIRE if kind == "accounts" else RULE_WIRE
    rows = [{**wire, "id": n} for n in range(100)]
    pages = [page(*rows, next_link="http://paperless.test/?page=2", count=MAX_ITEMS + 1)] * 20
    reader = PaperlessMailReader(make_client(Recording(*pages)))

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await (reader.list_mail_accounts() if kind == "accounts" else reader.list_mail_rules())

    assert f"more than {MAX_ITEMS} mail {kind}" in raised.value.message


async def test_an_account_is_read_by_id(make_client: ClientFactory) -> None:
    server = Recording(ok(ACCOUNT_WIRE))

    found = await PaperlessMailReader(make_client(server)).get_mail_account(1)

    assert found == ACCOUNT
    assert server.sent == [("GET", "/api/mail_accounts/1/", None, {})]


async def test_an_oauth_account_carries_its_token_expiry_but_no_token(
    make_client: ClientFactory,
) -> None:
    wire = {
        **ACCOUNT_WIRE,
        "account_type": 2,
        "is_token": True,
        "expiration": "2026-12-01T10:00:00Z",
    }
    server = Recording(ok(wire))

    found = await PaperlessMailReader(make_client(server)).get_mail_account(1)

    assert found.account_type == "gmail_oauth"
    assert found.expiration == datetime(2026, 12, 1, 10, tzinfo=UTC)
    assert MASK not in found.model_dump_json()


async def test_an_account_that_does_not_exist_is_not_found(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No MailAccount matches."}))

    with pytest.raises(NotFound) as raised:
        _ = await PaperlessMailReader(make_client(server)).get_mail_account(99)

    assert raised.value.describe() == (
        "No mail account 99 exists that this user may see. "
        "Hint: Call list_mail_accounts to see the accounts you can use."
    )


async def test_the_rules_are_listed_with_blank_filters_as_none(make_client: ClientFactory) -> None:
    server = Recording(page(RULE_WIRE))

    found = await PaperlessMailReader(make_client(server)).list_mail_rules()

    assert found == (RULE,)
    assert server.sent == [("GET", "/api/mail_rules/", None, {"page": "1", "page_size": "100"})]


async def test_one_page_of_rules_is_one_request(make_client: ClientFactory) -> None:
    body = {"count": 5, "next": "http://h/api/mail_rules/?page=3", "previous": None}
    server = Recording(ok({**body, "results": [RULE_WIRE]}))

    found = await PaperlessMailReader(make_client(server)).list_mail_rule_page(page=2, page_size=2)

    assert found == MailRulePage(count=5, next_page=3, rules=(RULE,))
    assert server.sent == [("GET", "/api/mail_rules/", None, {"page": "2", "page_size": "2"})]


async def test_a_page_of_rules_beyond_the_last_is_not_found(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "Invalid page."}))

    with pytest.raises(NotFound, match="Page 9 of the mail rules does not exist"):
        _ = await PaperlessMailReader(make_client(server)).list_mail_rule_page(page=9, page_size=2)


async def test_a_rule_with_a_default_action_has_no_parameter_and_no_tags(
    make_client: ClientFactory,
) -> None:
    wire: Json = {
        **RULE_WIRE,
        "action": 3,
        "action_parameter": "",
        "assign_tags": [],
        "pdf_layout": 0,
    }
    server = Recording(ok(wire))

    found = await PaperlessMailReader(make_client(server)).get_mail_rule(7)

    assert (found.action, found.action_parameter, found.assign_tags) == ("mark_read", None, ())
    assert found.pdf_layout == "default"
    assert server.sent == [("GET", "/api/mail_rules/7/", None, {})]


async def test_a_rule_that_does_not_exist_is_not_found(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No MailRule matches."}))

    with pytest.raises(NotFound) as raised:
        _ = await PaperlessMailReader(make_client(server)).get_mail_rule(99)

    assert raised.value.describe() == (
        "No mail rule 99 exists that this user may see. "
        "Hint: Call list_mail_rules to see the rules you can use."
    )


async def test_processed_mail_is_listed_newest_first_with_its_filters(
    make_client: ClientFactory,
) -> None:
    server = Recording(page(PROCESSED_WIRE, count=31, next_link="http://paperless.test/?page=3"))

    found = await PaperlessMailReader(make_client(server)).list_processed_mail(
        rule_id=7, status="FAILED", ordering="received", page=2, page_size=10
    )

    assert (found.count, found.next_page) == (31, 3)
    [mail] = found.mails
    assert mail == PROCESSED
    assert server.sent == [
        (
            "GET",
            "/api/processed_mail/",
            None,
            {
                "page": "2",
                "page_size": "10",
                "ordering": "received",
                "rule": "7",
                "status": "FAILED",
            },
        )
    ]


async def test_processed_mail_without_filters_sends_none(make_client: ClientFactory) -> None:
    server = Recording(page({**PROCESSED_WIRE, "error": ""}))

    found = await PaperlessMailReader(make_client(server)).list_processed_mail(
        rule_id=None, status=None, ordering="-processed", page=1, page_size=25
    )

    assert found.mails[0].error is None
    assert server.sent[0][3] == {"page": "1", "page_size": "25", "ordering": "-processed"}


@pytest.mark.decision("decision-66")
async def test_the_error_of_a_processed_mail_holds_no_credentials_of_a_url(
    make_client: ClientFactory,
) -> None:
    server = Recording(page({**PROCESSED_WIRE, "error": "to https://user:pw@h.example/x"}))

    found = await PaperlessMailReader(make_client(server)).list_processed_mail(
        rule_id=None, status=None, ordering="-processed", page=1, page_size=25
    )

    assert found.mails[0].error == "to https://h.example/x"


@pytest.mark.decision("decision-80")
async def test_the_error_of_a_processed_mail_holds_no_answer_to_a_login(
    make_client: ClientFactory,
) -> None:
    error = (
        'File "mail.py", line 1, in mailbox_login\n'
        f"imaplib.IMAP4.error: NO {PASSWORD} https://u:{PASSWORD}@h/x"
    )
    wire = {**PROCESSED_WIRE, "error": error}
    reader = PaperlessMailReader(make_client(Recording(ok(wire), page(wire))))

    one = await reader.get_processed_mail(9)
    listed = await reader.list_processed_mail(
        rule_id=None, status=None, ordering="-processed", page=1, page_size=25
    )

    assert one.error == (
        'File "mail.py", line 1, in mailbox_login\n'
        "imaplib.IMAP4.error: (answer of the mail server not shown)"
    )
    assert listed.mails == (one,)


async def test_one_processed_mail_is_read_by_id(make_client: ClientFactory) -> None:
    server = Recording(ok(PROCESSED_WIRE))

    assert await PaperlessMailReader(make_client(server)).get_processed_mail(9) == PROCESSED
    assert server.sent == [("GET", "/api/processed_mail/9/", None, {})]


async def test_a_processed_mail_that_does_not_exist_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No ProcessedMail matches."}))

    with pytest.raises(NotFound) as raised:
        _ = await PaperlessMailReader(make_client(server)).get_processed_mail(99)

    assert raised.value.describe() == (
        "No processed mail 99 exists that this user may see. "
        "Hint: Call list_processed_mail to see the records you can use."
    )


async def test_the_password_of_a_token_account_is_never_sent(make_client: ClientFactory) -> None:
    server = Recording(ok({**ACCOUNT_WIRE, "is_token": True}))
    values = MailAccountValues(name="New", password=SecretStr(PASSWORD))

    with pytest.raises(Validation) as raised:
        _ = await PaperlessMailWriter(make_client(server)).update_mail_account(1, values)

    assert [sent[0] for sent in server.sent] == ["GET"]
    assert PASSWORD not in raised.value.describe()
    assert raised.value.describe() == (
        "The mail account signs in with an OAuth access token, which is not replaced here. "
        "Nothing was changed. "
        "Hint: Set the account up again in the Paperless web UI; its other settings can be "
        "changed here."
    )


async def test_other_settings_of_a_token_account_are_sent_without_the_token(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok({**ACCOUNT_WIRE, "is_token": True}))
    values = MailAccountValues(username="me@example.com")

    _ = await PaperlessMailWriter(make_client(server)).update_mail_account(1, values)

    assert server.sent == [("PATCH", "/api/mail_accounts/1/", {"username": "me@example.com"}, {})]


@pytest.mark.decision("decision-83")
async def test_records_are_deleted_by_one_request_whose_echo_is_not_read(
    make_client: ClientFactory,
) -> None:
    """Paperless lists every id it was sent as deleted, also one that does not exist."""
    server = Recording(ok({"result": "OK", "deleted_mail_ids": [9, 10]}))

    assert await PaperlessMailWriter(make_client(server)).delete_processed_mail([9, 10]) is None
    assert server.sent == [("POST", "/api/processed_mail/bulk_delete/", {"mail_ids": [9, 10]}, {})]


@pytest.mark.decision("decision-107")
@pytest.mark.parametrize(
    "answer",
    [
        httpx2.Response(200, text="<html>Sign in</html>"),
        httpx2.Response(200),
        ok({"deleted_mail_ids": [9]}),
        ok({"result": "PARTIAL", "deleted_mail_ids": [9]}),
    ],
)
async def test_only_the_answer_paperless_gives_confirms_a_deletion(
    answer: httpx2.Response,
) -> None:
    server = Recording(answer)
    client, _ = streaming(server)  # these answers are not those of the schema

    with pytest.raises(UnexpectedAnswer):
        await PaperlessMailWriter(client).delete_processed_mail([9])

    assert len(server.sent) == 1


@pytest.mark.decision("decision-83")
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_deleting_records_is_never_repeated(make_client: ClientFactory, status: int) -> None:
    server = Recording(httpx2.Response(status, json={"detail": "no"}))

    with pytest.raises(UpstreamUnavailable):
        await PaperlessMailWriter(make_client(server)).delete_processed_mail([9])

    assert len(server.sent) == 1


@pytest.mark.decision("decision-83")
async def test_a_refused_deletion_says_that_nothing_was_deleted(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(403, text="Insufficient permissions"))

    with pytest.raises(PermissionDenied) as raised:
        await PaperlessMailWriter(make_client(server)).delete_processed_mail([9])

    assert raised.value.describe() == (
        "Paperless does not let you delete these records. Nothing was deleted. "
        "Hint: It takes the permission delete_processedmail, and records that you own or that "
        "have no owner; one other record refuses all of them."
    )


async def test_a_page_beyond_the_last_one_is_not_found(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "Invalid page."}))

    with pytest.raises(NotFound) as raised:
        _ = await PaperlessMailReader(make_client(server)).list_processed_mail(
            rule_id=None, status=None, ordering="-processed", page=9, page_size=25
        )

    assert raised.value.message == "Page 9 of the processed mail does not exist."


# --- accounts


async def test_an_account_is_created_with_the_password_in_the_body_only(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(201, json=ACCOUNT_WIRE))
    values = MailAccountValues(
        name="Invoices",
        imap_server="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        username="me@example.com",
        password=SecretStr(PASSWORD),
    )

    made = await PaperlessMailWriter(make_client(server)).create_mail_account(values)

    assert made == ACCOUNT
    assert server.sent == [
        (
            "POST",
            "/api/mail_accounts/",
            {
                "name": "Invoices",
                "imap_server": "imap.example.com",
                "imap_port": 993,
                "imap_security": 2,
                "username": "me@example.com",
                "password": PASSWORD,
            },
            {},
        )
    ]
    assert PASSWORD not in repr(values)
    assert PASSWORD not in repr(made)


async def test_an_update_sends_only_what_is_set_and_keeps_the_password(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok({**ACCOUNT_WIRE, "name": "Bills", "imap_security": 3}))

    changed = await PaperlessMailWriter(make_client(server)).update_mail_account(
        1, MailAccountValues(name="Bills", imap_security="starttls", character_set="ISO-8859-1")
    )

    assert changed.name == "Bills"
    assert server.sent == [
        (
            "PATCH",
            "/api/mail_accounts/1/",
            {"name": "Bills", "imap_security": 3, "character_set": "ISO-8859-1"},
            {},
        )
    ]


async def test_a_new_password_is_sent_on_an_update(make_client: ClientFactory) -> None:
    server = Recording(ok(ACCOUNT_WIRE), ok(ACCOUNT_WIRE))

    _ = await PaperlessMailWriter(make_client(server)).update_mail_account(
        1, MailAccountValues(password=SecretStr(PASSWORD))
    )

    assert [sent[0] for sent in server.sent] == ["GET", "PATCH"]
    assert server.sent[1][2] == {"password": PASSWORD}


@pytest.mark.parametrize("password", ["*", "*****"])
async def test_a_password_of_asterisks_is_refused_because_paperless_would_ignore_it(
    make_client: ClientFactory, password: str
) -> None:
    server = Recording()
    writer = PaperlessMailWriter(make_client(server))

    with pytest.raises(Validation) as raised:
        _ = await writer.update_mail_account(1, MailAccountValues(password=SecretStr(password)))
    with pytest.raises(Validation):
        _ = await writer.create_mail_account(MailAccountValues(password=SecretStr(password)))

    assert "only of asterisks" in raised.value.message
    assert server.sent == []


async def test_a_password_with_an_asterisk_among_other_characters_is_sent(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok(ACCOUNT_WIRE), ok(ACCOUNT_WIRE))

    _ = await PaperlessMailWriter(make_client(server)).update_mail_account(
        1, MailAccountValues(password=SecretStr("a*"))
    )

    assert [sent[0] for sent in server.sent] == ["GET", "PATCH"]
    assert server.sent[1][2] == {"password": "a*"}


async def test_a_rejected_account_names_the_field_but_never_the_value(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    answer = httpx2.Response(400, json={"imap_port": ["A valid integer is required."]})
    server = Recording(answer)

    with pytest.raises(Validation) as raised:
        _ = await PaperlessMailWriter(make_client(server)).create_mail_account(
            MailAccountValues(
                name="x",
                imap_server="h",
                imap_port=0,
                username="u",
                password=SecretStr(PASSWORD),
            )
        )

    assert (
        raised.value.message
        == "Paperless rejected the request: imap_port: A valid integer is required."
    )
    assert PASSWORD not in raised.value.describe()
    assert PASSWORD not in caplog.text


async def test_an_account_the_user_may_not_change_is_a_permission_error(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(403, json={"detail": "no"}))

    with pytest.raises(PermissionDenied):
        _ = await PaperlessMailWriter(make_client(server)).update_mail_account(
            1, MailAccountValues(name="x")
        )


async def test_updating_an_account_that_does_not_exist_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No MailAccount matches."}))

    with pytest.raises(NotFound):
        _ = await PaperlessMailWriter(make_client(server)).update_mail_account(
            9, MailAccountValues(name="x")
        )


async def test_an_account_is_deleted_without_retry(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(503, text="busy"), httpx2.Response(204))

    with pytest.raises(UpstreamUnavailable):
        await PaperlessMailWriter(make_client(server)).delete_mail_account(1)

    assert server.sent == [("DELETE", "/api/mail_accounts/1/", None, {})]


async def test_deleting_an_account_that_does_not_exist_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No MailAccount matches."}))

    with pytest.raises(NotFound):
        await PaperlessMailWriter(make_client(server)).delete_mail_account(9)


# --- test and process


def saved_body() -> Json:
    """What the connection test sends: the saved account, the id and a masked password."""
    return {
        "name": "Invoices",
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "imap_security": 2,
        "username": "me@example.com",
        "character_set": "UTF-8",
        "is_token": False,
        "password": "*",
        "id": 1,
    }


async def test_a_connection_test_that_works_says_so(make_client: ClientFactory) -> None:
    server = Recording(ok(ACCOUNT_WIRE), ok({"success": True}))

    result = await PaperlessMailWriter(make_client(server)).test_mail_account(1)

    assert result == connected()
    assert server.sent == [
        ("GET", "/api/mail_accounts/1/", None, {}),
        ("POST", "/api/mail_accounts/test/", saved_body(), {}),
    ]
    assert PASSWORD not in json.dumps(server.sent)
    assert MASK not in json.dumps(server.sent[1])


async def test_a_failed_login_is_a_failed_result(make_client: ClientFactory) -> None:
    server = Recording(ok(ACCOUNT_WIRE), httpx2.Response(400, text="Unable to connect to server"))

    result = await PaperlessMailWriter(make_client(server)).test_mail_account(1)

    assert result == failed_login()
    assert not result.success


async def test_a_refused_connection_is_a_failed_result(make_client: ClientFactory) -> None:
    server = Recording(ok(ACCOUNT_WIRE), httpx2.Response(500, text="Server Error (500)"))

    result = await PaperlessMailWriter(make_client(server)).test_mail_account(1)

    assert result == unreachable_server()
    assert "refuses the connection" in result.message


async def test_a_mail_server_that_never_answers_is_a_failed_result(
    make_client: ClientFactory,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return ok(ACCOUNT_WIRE)
        message = "no answer"
        raise httpx2.ReadTimeout(message, request=request)

    result = await PaperlessMailWriter(make_client(handler)).test_mail_account(1)

    assert result == silent_server()
    assert f"{TEST_TIMEOUT:.0f} seconds" in result.message


async def test_the_test_gets_its_own_timeout_and_is_not_retried(
    make_client: ClientFactory,
) -> None:
    seen: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return ok(ACCOUNT_WIRE)
        seen.append(cast("float", request.extensions["timeout"]["read"]))
        return httpx2.Response(503, text="busy")

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessMailWriter(make_client(handler)).test_mail_account(1)

    assert seen == [TEST_TIMEOUT]


async def test_paperless_being_down_is_an_error_not_a_failed_test(
    make_client: ClientFactory,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return ok(ACCOUNT_WIRE)
        message = "refused"
        raise httpx2.ConnectError(message, request=request)

    with pytest.raises(UpstreamUnavailable):
        _ = await PaperlessMailWriter(make_client(handler)).test_mail_account(1)


async def test_a_user_who_may_not_change_the_account_cannot_test_it(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok(ACCOUNT_WIRE), httpx2.Response(403, text="Insufficient permissions"))

    with pytest.raises(PermissionDenied):
        _ = await PaperlessMailWriter(make_client(server)).test_mail_account(1)


async def test_testing_an_account_that_does_not_exist_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No MailAccount matches."}))

    with pytest.raises(NotFound):
        _ = await PaperlessMailWriter(make_client(server)).test_mail_account(9)

    assert len(server.sent) == 1


async def test_a_fetch_is_queued_with_the_saved_account_as_the_body(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok(ACCOUNT_WIRE), ok({"result": "OK"}))

    await PaperlessMailWriter(make_client(server)).process_mail_account(1)

    body = saved_body()
    del body["id"]
    assert server.sent == [
        ("GET", "/api/mail_accounts/1/", None, {}),
        ("POST", "/api/mail_accounts/1/process/", body, {}),
    ]


async def test_a_fetch_is_never_queued_twice(make_client: ClientFactory) -> None:
    server = Recording(ok(ACCOUNT_WIRE), httpx2.Response(502, text="bad"), ok({"result": "OK"}))

    with pytest.raises(UpstreamUnavailable):
        await PaperlessMailWriter(make_client(server)).process_mail_account(1)

    assert [entry[0] for entry in server.sent] == ["GET", "POST"]


async def test_a_fetch_for_an_account_that_does_not_exist_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No MailAccount matches."}))

    with pytest.raises(NotFound):
        await PaperlessMailWriter(make_client(server)).process_mail_account(9)


async def test_a_fetch_that_vanishes_between_the_read_and_the_post_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok(ACCOUNT_WIRE), httpx2.Response(404, json={"detail": "gone"}))

    with pytest.raises(NotFound):
        await PaperlessMailWriter(make_client(server)).process_mail_account(1)


# --- rules


async def test_a_rule_is_created_with_every_choice_as_its_number(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(201, json=RULE_WIRE))
    values = MailRuleValues(
        name="Invoices",
        account_id=1,
        enabled=True,
        order=1,
        folder="INBOX",
        filter_from="billing@example.com",
        filter_attachment_filename_include="*.pdf",
        maximum_age=30,
        attachment_type="all_files",
        consumption_scope="eml_and_attachments",
        pdf_layout="html",
        action="move",
        action_parameter="Archive",
        assign_title_from="filename",
        assign_tags=(3, 4),
        assign_correspondent_from="selected",
        assign_correspondent=5,
        assign_document_type=6,
        assign_owner_from_rule=True,
        stop_processing=True,
        owner=2,
    )

    made = await PaperlessMailWriter(make_client(server)).create_mail_rule(values)

    assert made == RULE
    assert server.sent == [
        (
            "POST",
            "/api/mail_rules/",
            {
                "name": "Invoices",
                "account": 1,
                "enabled": True,
                "order": 1,
                "folder": "INBOX",
                "filter_from": "billing@example.com",
                "filter_attachment_filename_include": "*.pdf",
                "maximum_age": 30,
                "attachment_type": 2,
                "consumption_scope": 3,
                "pdf_layout": 3,
                "action": 2,
                "action_parameter": "Archive",
                "assign_title_from": 2,
                "assign_tags": [3, 4],
                "assign_correspondent_from": 4,
                "assign_correspondent": 5,
                "assign_document_type": 6,
                "assign_owner_from_rule": True,
                "stop_processing": True,
                "owner": 2,
            },
            {},
        )
    ]


@pytest.mark.parametrize(
    ("field", "value", "wire"),
    [
        pytest.param("action", "delete", 1, id="delete"),
        pytest.param("action", "flag", 4, id="flag"),
        pytest.param("action", "tag", 5, id="tag"),
        pytest.param("action", "mark_read", 3, id="mark-read"),
        pytest.param("assign_title_from", "subject", 1, id="title-subject"),
        pytest.param("assign_title_from", "none", 3, id="title-none"),
        pytest.param("assign_correspondent_from", "none", 1, id="correspondent-none"),
        pytest.param("assign_correspondent_from", "email_address", 2, id="correspondent-address"),
        pytest.param("assign_correspondent_from", "name", 3, id="correspondent-name"),
        pytest.param("attachment_type", "attachments_only", 1, id="attachments"),
        pytest.param("consumption_scope", "attachments", 1, id="scope-attachments"),
        pytest.param("consumption_scope", "eml", 2, id="scope-eml"),
        pytest.param("pdf_layout", "default", 0, id="layout-default"),
        pytest.param("pdf_layout", "text_html", 1, id="layout-text-html"),
        pytest.param("pdf_layout", "html_text", 2, id="layout-html-text"),
        pytest.param("pdf_layout", "text", 4, id="layout-text"),
    ],
)
async def test_each_choice_has_its_wire_number(
    make_client: ClientFactory, field: str, value: str, wire: int
) -> None:
    server = Recording(ok(RULE_WIRE))

    _ = await PaperlessMailWriter(make_client(server)).update_mail_rule(
        7, MailRuleValues.model_validate({field: value})
    )

    assert server.sent[0][2] == {field: wire}


async def test_an_update_clears_fields_by_sending_null(make_client: ClientFactory) -> None:
    server = Recording(ok(RULE_WIRE))

    _ = await PaperlessMailWriter(make_client(server)).update_mail_rule(
        7,
        MailRuleValues(
            filter_from=None,
            filter_to=None,
            filter_subject=None,
            filter_body=None,
            filter_attachment_filename_exclude=None,
            action_parameter=None,
            assign_correspondent=None,
            assign_document_type=None,
            assign_tags=(),
            owner=None,
            account_id=2,
        ),
    )

    assert server.sent == [
        (
            "PATCH",
            "/api/mail_rules/7/",
            {
                "filter_from": None,
                "filter_to": None,
                "filter_subject": None,
                "filter_body": None,
                "filter_attachment_filename_exclude": None,
                "action_parameter": None,
                "assign_correspondent": None,
                "assign_document_type": None,
                "assign_tags": [],
                "owner": None,
                "account": 2,
            },
            {},
        )
    ]


async def test_an_empty_update_sends_an_empty_body(make_client: ClientFactory) -> None:
    server = Recording(ok(RULE_WIRE))

    _ = await PaperlessMailWriter(make_client(server)).update_mail_rule(7, MailRuleValues())

    assert server.sent[0][2] == {}


async def test_a_rule_that_needs_a_parameter_names_the_problem(make_client: ClientFactory) -> None:
    answer = httpx2.Response(400, json={"non_field_errors": ["An action parameter is required."]})
    server = Recording(answer)

    with pytest.raises(Validation) as raised:
        _ = await PaperlessMailWriter(make_client(server)).update_mail_rule(
            7, MailRuleValues(action="move")
        )

    assert (
        raised.value.message == "Paperless rejected the request: An action parameter is required."
    )


async def test_updating_a_rule_that_does_not_exist_is_not_found(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No MailRule matches."}))

    with pytest.raises(NotFound):
        _ = await PaperlessMailWriter(make_client(server)).update_mail_rule(
            9, MailRuleValues(name="x")
        )


async def test_a_rule_is_deleted_without_retry(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(503, text="busy"), httpx2.Response(204))

    with pytest.raises(UpstreamUnavailable):
        await PaperlessMailWriter(make_client(server)).delete_mail_rule(7)

    assert server.sent == [("DELETE", "/api/mail_rules/7/", None, {})]


async def test_deleting_a_rule_that_does_not_exist_is_not_found(make_client: ClientFactory) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "No MailRule matches."}))

    with pytest.raises(NotFound):
        await PaperlessMailWriter(make_client(server)).delete_mail_rule(9)


async def test_the_failed_results_say_what_to_check() -> None:
    assert failed_login().message == (
        "Paperless could not connect to the mail server or log in. Check the server name, "
        "port, security setting, username and password."
    )
    assert unreachable_server().message == (
        "Paperless could not connect to the mail server, or failed while trying (the host is "
        "unknown or refuses the connection, or the server broke off). Check the server name, "
        "port and security setting."
    )
    assert silent_server().message == (
        "The mail server did not answer within 45 seconds. Check the port and the security "
        "setting: a port that expects TLS never answers a plain connection. The Paperless web "
        "worker may stay busy for several minutes, so do not try again at once."
    )
    assert connected().message == "Paperless connected and logged in."


async def test_an_oauth_account_is_tested_and_fetched_as_a_token_account(
    make_client: ClientFactory,
) -> None:
    token_account = {**ACCOUNT_WIRE, "account_type": 2, "is_token": True}
    server = Recording(
        ok(token_account), ok({"success": True}), ok(token_account), ok({"result": "OK"})
    )
    writer = PaperlessMailWriter(make_client(server))

    _ = await writer.test_mail_account(1)
    await writer.process_mail_account(1)

    assert server.sent[1][2] is not None
    assert server.sent[1][2]["is_token"] is True
    assert server.sent[3][2] is not None
    assert server.sent[3][2]["is_token"] is True


async def test_every_filter_of_a_rule_is_read_back_and_blank_ones_are_none(
    make_client: ClientFactory,
) -> None:
    names = (
        *("filter_from", "filter_to", "filter_subject", "filter_body"),
        *("filter_attachment_filename_include", "filter_attachment_filename_exclude"),
    )
    full = {**RULE_WIRE, **{name: f"value-{name}" for name in names}}
    blank = {**RULE_WIRE, **dict.fromkeys(names, "")}
    server = Recording(ok(full), ok(blank))
    reader = PaperlessMailReader(make_client(server))

    shown = await reader.get_mail_rule(7)
    emptied = await reader.get_mail_rule(7)

    assert {name: getattr(shown, name) for name in names} == {n: f"value-{n}" for n in names}
    assert {name: getattr(emptied, name) for name in names} == dict.fromkeys(names)


async def test_an_account_read_back_carries_owner_rights_and_expiry(
    make_client: ClientFactory,
) -> None:
    wire = {
        **ACCOUNT_WIRE,
        "owner": None,
        "user_can_change": False,
        "character_set": "ISO-8859-1",
        "imap_security": 1,
        "expiration": "2026-12-01T10:00:00Z",
    }
    server = Recording(ok(wire))

    found = await PaperlessMailReader(make_client(server)).get_mail_account(1)

    assert found.model_dump() == {
        **ACCOUNT.model_dump(),
        "owner": None,
        "can_change": False,
        "character_set": "ISO-8859-1",
        "imap_security": "none",
        "expiration": datetime(2026, 12, 1, 10, tzinfo=UTC),
    }


async def test_every_not_found_names_the_missing_object_and_the_next_step(
    make_client: ClientFactory,
) -> None:
    gone = httpx2.Response(404, json={"detail": "No such object."})
    account_hint = "Hint: Call list_mail_accounts to see the accounts you can use."
    rule_hint = "Hint: Call list_mail_rules to see the rules you can use."

    async def failing(operation: str, *args: object) -> str:
        writer = PaperlessMailWriter(make_client(Recording(gone, gone)))
        call = cast("Callable[..., Awaitable[object]]", getattr(writer, operation))
        with pytest.raises(NotFound) as raised:
            _ = await call(*args)
        return raised.value.describe()

    assert await failing("update_mail_account", 9, MailAccountValues(name="x")) == (
        f"No mail account 9 exists that this user may see. {account_hint}"
    )
    assert await failing("delete_mail_account", 9) == (
        f"No mail account 9 exists that this user may see. {account_hint}"
    )
    assert await failing("test_mail_account", 9) == (
        f"No mail account 9 exists that this user may see. {account_hint}"
    )
    assert await failing("process_mail_account", 9) == (
        f"No mail account 9 exists that this user may see. {account_hint}"
    )
    assert await failing("update_mail_rule", 8, MailRuleValues(name="x")) == (
        f"No mail rule 8 exists that this user may see. {rule_hint}"
    )
    assert await failing("delete_mail_rule", 8) == (
        f"No mail rule 8 exists that this user may see. {rule_hint}"
    )


async def test_a_page_past_the_end_of_the_processed_mail_has_its_exact_texts(
    make_client: ClientFactory,
) -> None:
    server = Recording(httpx2.Response(404, json={"detail": "Invalid page."}))

    with pytest.raises(NotFound) as raised:
        _ = await PaperlessMailReader(make_client(server)).list_processed_mail(
            rule_id=None, status=None, ordering="-processed", page=9, page_size=25
        )

    assert raised.value.describe() == (
        "Page 9 of the processed mail does not exist. "
        "Hint: List it with page=1; count and next_page tell how far it goes."
    )


async def test_values_that_are_set_to_none_are_not_sent(make_client: ClientFactory) -> None:
    server = Recording(ok(ACCOUNT_WIRE), ok(RULE_WIRE))
    nothing = MailAccountValues(
        name=None,
        imap_server=None,
        imap_port=None,
        imap_security=None,
        username=None,
        password=None,
        character_set=None,
    )
    none_rule = MailRuleValues(
        attachment_type=None,
        consumption_scope=None,
        pdf_layout=None,
        action=None,
        assign_title_from=None,
        assign_correspondent_from=None,
        assign_tags=None,
    )

    _ = await PaperlessMailWriter(make_client(server)).update_mail_account(1, nothing)
    _ = await PaperlessMailWriter(make_client(server)).update_mail_rule(7, none_rule)

    assert [entry[2] for entry in server.sent] == [{}, {}]


async def test_a_fetch_for_an_account_deleted_in_between_names_it(
    make_client: ClientFactory,
) -> None:
    server = Recording(ok(ACCOUNT_WIRE), httpx2.Response(404, json={"detail": "gone"}))

    with pytest.raises(NotFound) as raised:
        await PaperlessMailWriter(make_client(server)).process_mail_account(1)

    assert raised.value.message == "No mail account 1 exists that this user may see."
