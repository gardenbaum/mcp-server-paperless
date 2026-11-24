"""Mail imports against a real mail server: what is fetched, when, and what the rule does then.

Every row of the table in the design of PLMCP-81 (2.2), which `refetch` of get_processed_mail says,
and what Paperless answers and logs (probes P1 to P4). The tests run on a stack of their own
(`just e2e-imap`, marker `imap_e2e`) and skip elsewhere.
"""

import json
import logging
import os
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import API_VERSION, num, obj, parse, results
from e2e.mailbox import BROKEN, PASSWORD, Mailbox, note
from mcp_server_paperless.domain.urls import ANSWER_NOT_SHOWN
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from fastmcp.client.transports import FastMCPTransport

    from e2e.helpers import Json, Stack

IMAP = os.environ.get("PAPERLESS_E2E_IMAP") == "1"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.imap_e2e,
    pytest.mark.anyio,
    pytest.mark.decision("decision-80"),
    pytest.mark.skipif(not IMAP, reason="No mail server; `just e2e-imap` runs this with one."),
]
LOG = logging.getLogger("mail.import")
type Connected = Client[FastMCPTransport]
SEEN, FLAGGED = "\\Seen", "\\Flagged"


@pytest.fixture
def box(stack: Stack) -> Iterator[Mailbox]:
    made = Mailbox(stack)
    yield made
    made.close()


@pytest.fixture
async def client(stack: Stack) -> AsyncIterator[Connected]:
    server = create_server(
        paperless_settings(stack.base_url, stack.admin_token),
        server_settings(toolsets=["core", "mail", "admin", "raw"]),
    )
    async with Client(server) as connected:
        yield connected


def seen(*said: object) -> None:
    """What Paperless said, in the report of the test (`pytest -rP` shows it)."""
    LOG.warning("%s", " ".join(map(str, said)))


async def call(client: Connected, tool: str, **arguments: object) -> Json:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    assert PASSWORD not in str(result)
    return result.structured_content


async def record(client: Connected, row: Json) -> Json:
    """The record as get_processed_mail reads it."""
    return await call(client, "get_processed_mail", mail_id=row["id"])


# --- what is fetched again without the record


@pytest.mark.parametrize(
    ("action", "parameter", "flag", "said"),
    [
        ("mark_read", None, SEEN, "while it is read on the mail server"),
        ("flag", None, FLAGGED, "while it is flagged on the mail server"),
        ("tag", "paperless", "paperless", "while it is tagged 'paperless' on the mail server"),
        ("tag", "apple:red", FLAGGED, "while it is flagged on the mail server"),
    ],
)
async def test_a_marked_mail_is_only_taken_again_without_its_record_and_its_mark(  # noqa: PLR0913, PLR0917 -- the cases
    box: Mailbox, client: Connected, action: str, parameter: str | None, flag: str, said: str
) -> None:
    rule = box.rule(action, action_parameter=parameter)
    uid = box.put("Invoice", {"a.txt": note()})

    assert box.fetch() == 1
    [first] = box.records(rule, 1)
    assert (first["status"], first["uid"]) == ("SUCCESS", uid)
    assert flag in box.flags(uid)
    shown = await record(client, first)
    assert said in str(shown["refetch"])
    assert (shown["uid"], shown["error"]) == (uid, None)

    # The mark without the record, and the record without the mark, both keep it out.
    box.unflag(uid, flag)
    assert box.fetch() == 0
    box.forget(num(first["id"]))
    assert box.fetch() == 1
    [second] = box.records(rule, 1)
    assert flag in box.flags(uid)
    box.forget(num(second["id"]))
    assert box.fetch() == 0
    assert box.records(rule, 0) == []
    assert len(box.documents("Invoice")) == 2


async def test_a_deleted_mail_is_gone_and_a_moved_one_has_a_new_uid_when_it_is_back(
    box: Mailbox, client: Connected
) -> None:
    rule = box.rule("delete")
    _ = box.put("Deleted", {"a.txt": note()})
    assert box.fetch() == 1
    [deleted] = box.records(rule, 1)
    assert box.uids() == []
    assert "The rule deletes the mail" in str((await record(client, deleted))["refetch"])
    box.change_rule(rule, enabled=False)

    _ = box.imap.create("Done")
    moving = box.rule("move", action_parameter="Done")
    uid = box.put("Moved", {"a.txt": note()})
    assert box.fetch() == 1
    [moved] = box.records(moving, 1)
    assert "The rule moves the mail to 'Done'" in str((await record(client, moved))["refetch"])
    assert (box.uids(), len(box.uids("Done"))) == ([], 1)
    box.move(box.uids("Done")[0], "Done", "INBOX")
    assert box.uids() != [uid]
    assert box.fetch() == 1
    assert len(box.records(moving, 2)) == 2
    assert len(box.documents("Moved")) == 2


@pytest.mark.parametrize("action", ["mark_read", "delete"])
async def test_a_failed_mail_is_left_alone_until_its_record_is_gone_and_then_taken_whole(
    box: Mailbox, client: Connected, action: str
) -> None:
    """T4: what was imported before the failure is imported again, and then the rule acts."""
    rule = box.rule(action)
    uid = box.put("Half", {"good.txt": note(), "broken.pdf": BROKEN})

    started = box.fetch()
    [failed] = box.records(rule, 1)
    shown = await record(client, failed)
    seen("P1/P3 failed:", started, failed["status"], repr(failed["error"]))

    assert failed["status"] == "FAILED"
    assert SEEN not in box.flags(uid)
    assert len(box.documents("Half")) == 1
    assert "takes the mail again" in str(shown["refetch"])
    assert box.fetch() == 0

    box.change_rule(rule, filter_attachment_filename_exclude="broken*")
    box.forget(num(failed["id"]))
    assert box.fetch() == 1
    [taken] = box.records(rule, 1)
    assert taken["status"] == "SUCCESS"
    assert len(box.documents("Half")) == 2
    if action == "delete":
        assert box.uids() == []
    else:
        assert SEEN in box.flags(uid)


async def test_a_mail_without_a_file_is_looked_at_again_and_taken_once_the_rule_gives_one(
    box: Mailbox, client: Connected
) -> None:
    rule = box.rule(filter_attachment_filename_include="*.pdf")
    uid = box.put("Excluded", {"a.txt": note()})
    _ = box.put("Bare")

    assert box.fetch() == 0
    rows = box.records(rule, 2)
    assert {row["status"] for row in rows} == {"PROCESSED_WO_CONSUMPTION"}
    assert SEEN not in box.flags(uid)
    listed = await call(
        client, "list_processed_mail", rule_id=rule, status="PROCESSED_WO_CONSUMPTION"
    )
    assert listed["count"] == 2
    shown = await record(client, rows[0])
    assert "looks at the mail again" in str(shown["refetch"])
    assert shown["error"] is None

    box.change_rule(rule, filter_attachment_filename_include="*")
    assert box.fetch() == 0
    box.forget(*(num(row["id"]) for row in rows))
    assert box.fetch() == 1
    statuses = {str(row["subject"])[-8:]: row["status"] for row in box.records(rule, 2)}
    assert statuses["Excluded"] == "SUCCESS"
    assert SEEN in box.flags(uid)


async def test_a_disabled_rule_fetches_nothing_also_without_the_record(
    box: Mailbox, client: Connected
) -> None:
    rule = box.rule("flag")
    uid = box.put("Off", {"a.txt": note()})
    assert box.fetch() == 1
    [taken] = box.records(rule, 1)
    box.change_rule(rule, enabled=False)
    box.unflag(uid, FLAGGED)
    assert str((await record(client, taken))["refetch"]).startswith("The rule is disabled")
    box.forget(num(taken["id"]))

    assert box.fetch() == 0
    assert box.records(rule, 0) == []


async def test_a_mail_with_two_records_is_blocked_by_either(
    box: Mailbox, client: Connected
) -> None:
    """T2: the mail and its attachments are two queues with a record each."""
    rule = box.rule("flag", consumption_scope=3)
    uid = box.put("Both", {"a.txt": note()})

    assert box.fetch() == 2
    rows = box.records(rule, 2)
    seen("P1 siblings:", [(row["status"], row["error"]) for row in rows])
    assert {row["uid"] for row in rows} == {uid}
    shown = await record(client, rows[0])
    assert f"until they are deleted too: {rows[1]['id']}." in str(shown["refetch"])
    box.unflag(uid, FLAGGED)
    box.forget(num(rows[0]["id"]))
    assert box.fetch() == 0
    box.forget(num(rows[1]["id"]))
    assert box.fetch() == 2
    assert len(box.records(rule, 2)) == 2


async def test_every_rule_has_a_record_of_its_own_and_skips_what_another_took_in_the_run(
    box: Mailbox,
) -> None:
    """T7: within one run a mail queued by one rule is skipped by later ones."""
    first, second = box.rule("flag", order=1), box.rule("flag", order=2)
    uid = box.put("Once", {"a.txt": note()})

    assert box.fetch() == 1
    _ = box.records(first, 1)
    assert box.records(second, 0) == []
    box.unflag(uid, FLAGGED)
    assert box.fetch() == 1
    assert len(box.records(second, 1)) == 1


# --- logins that fail


async def test_the_answer_to_a_failed_login_is_in_no_result_and_no_log(
    box: Mailbox, client: Connected
) -> None:
    """The texts the redaction of decision-80 knows are those Paperless 3.5.1 writes."""
    _ = box.rule()
    wrong = box.stack.admin.patch(
        f"/api/mail_accounts/{box.account}/", json={"password": "wr0ng-Passw0rd"}
    )
    assert wrong.is_success
    said = box.fetch()
    seen("P3 task of a failed login:", said)

    raw = {name: box.log(name) for name in ("mail", "paperless", "celery")}
    for name, lines in raw.items():
        for number, line in enumerate(lines):
            if box.name in line or "mailbox_login" in line:
                seen(f"P3 {name}.log:", lines[number : number + 2])
    logged = [line for line in raw["mail"] if f"authenticating account {box.name}: " in line]
    assert logged, "Paperless no longer logs the answer like this"
    answer = logged[0].split(f"{box.name}: ", 1)[1]
    assert "AUTHENTICATIONFAILED" in answer
    for name, lines in raw.items():
        shown = await call(client, "get_log", name=name, lines=1000)
        text = "\n".join(cast("list[str]", shown["lines"]))
        assert "AUTHENTICATIONFAILED" not in text
        assert "wr0ng" not in text
        if any("AUTHENTICATIONFAILED" in line for line in lines[-200:]):
            assert ANSWER_NOT_SHOWN in text
    for path in ("/api/logs/mail/", "/api/logs/paperless/"):
        read = await call(client, "paperless_api_get", path=path, params={"limit": 60})
        assert "AUTHENTICATIONFAILED" not in json.dumps(read)


async def test_a_login_that_fails_when_the_rule_acts_shows_no_answer(
    box: Mailbox, client: Connected
) -> None:
    """The password changes while the attachments are imported: the action cannot sign in."""
    rule = box.rule("flag")
    uid = box.put("Late", {f"{n}.txt": note() for n in range(8)})
    assert box.fetch() == 8
    wrong = box.stack.admin.patch(
        f"/api/mail_accounts/{box.account}/", json={"password": "wr0ng-Passw0rd"}
    )
    assert wrong.is_success

    [failed] = box.records(rule, 1)
    seen("P3 failed action:", failed["error"])
    shown = await record(client, failed)

    assert failed["status"] == "FAILED"
    assert "AUTHENTICATIONFAILED" in str(failed["error"])
    assert "AUTHENTICATIONFAILED" not in json.dumps(shown)
    assert ANSWER_NOT_SHOWN in str(shown["error"])
    assert "in mailbox_login" in str(shown["error"])
    listed = await call(client, "list_processed_mail", rule_id=rule)
    assert "AUTHENTICATIONFAILED" not in json.dumps(listed)
    raw = await call(
        client, "paperless_api_get", path="/api/processed_mail/", params={"rule": rule}
    )
    assert "AUTHENTICATIONFAILED" not in json.dumps(raw)
    assert FLAGGED not in box.flags(uid)
    assert len(box.documents("Late")) == 8


async def test_an_action_that_fails_leaves_a_traceback(box: Mailbox, client: Connected) -> None:
    """The folder a rule moves to disappears while the attachments are imported."""
    _ = box.imap.create("Gone")
    rule = box.rule("move", action_parameter="Gone")
    _ = box.put("Late", {f"{n}.txt": note() for n in range(8)})
    assert box.fetch() == 8
    _ = box.imap.delete("Gone")

    [failed] = box.records(rule, 1)
    seen("P3 failed move:", failed["error"])
    shown = await record(client, failed)

    assert failed["status"] == "FAILED"
    assert shown["error"] == failed["error"]
    assert "Traceback" in str(shown["error"])


async def test_a_fetch_while_another_runs_is_skipped(box: Mailbox) -> None:
    """T5: the process call answers OK all the same."""
    _ = box.rule()
    for number in range(60):
        _ = box.put(f"Many {number}")
    before = set(box.tasks("mail_fetch"))
    box.start()
    box.start()

    box.wait_for_fetches(before, count=2)
    new = [row for number, row in box.tasks("mail_fetch").items() if number not in before]
    seen("P3 two fetches:", [(row["status"], row["result_data"]) for row in new])
    assert {row["status"] for row in new} == {"success"}
    assert {row["result_data"] for row in new} == {None}  # so nothing but the log tells it
    skipped = "Mail account processing is already running; skipping this run."
    assert any(skipped in line for line in box.log("paperless"))


# --- deleting records (P2)


def recorded(box: Mailbox, count: int, **rule: object) -> tuple[int, list[int]]:
    number = box.rule(**rule)  # pyright: ignore[reportArgumentType] -- fields of the rule
    for index in range(count):
        _ = box.put(f"Record {index}")
    _ = box.fetch()
    return number, [num(row["id"]) for row in box.records(number, count)]


async def test_bulk_delete_names_what_it_was_asked_for_not_what_it_deleted(box: Mailbox) -> None:
    """T1: an id that does not exist is skipped and listed as deleted; an empty list is fine."""
    rule, ids = recorded(box, 2)

    reply = box.stack.admin.post(
        "/api/processed_mail/bulk_delete/", json={"mail_ids": [ids[0], 999_999]}
    )
    empty = box.stack.admin.post("/api/processed_mail/bulk_delete/", json={"mail_ids": []})
    many = box.stack.admin.post(
        "/api/processed_mail/bulk_delete/",
        json={"mail_ids": list(range(1_000_000, 1_001_001))},
    )
    seen("P2:", reply.text, empty.status_code, empty.text, many.status_code)

    assert obj(parse(reply))["deleted_mail_ids"] == [ids[0], 999_999]
    assert empty.status_code == many.status_code == 200
    assert [row["id"] for row in box.records(rule, 1)] == [ids[1]]


async def test_one_record_the_caller_may_not_delete_refuses_all(box: Mailbox, stack: Stack) -> None:
    """Records without an owner may be deleted by whoever has the permission, others' not."""
    settings = obj(parse(stack.admin.get("/api/ui_settings/")))
    admin = num(obj(settings["user"])["id"])
    owned_rule, owned = recorded(box, 1, owner=admin)
    box.change_rule(owned_rule, enabled=False)
    free_rule = box.rule(owner=None)
    box.change_rule(free_rule, owner=None)
    _ = box.fetch()
    [free] = box.records(free_rule, 1)
    name, password = f"{box.name}-user", "pw-e2e-imap-4711"
    body = {
        "username": name,
        "password": password,
        "user_permissions": ["delete_processedmail"],
    }
    user = num(obj(parse(stack.admin.post("/api/users/", json=body).raise_for_status()))["id"])
    try:
        with httpx2.Client(
            base_url=stack.base_url,
            auth=(name, password),
            headers={"Accept": f"application/json; version={API_VERSION}"},
        ) as other:
            path = "/api/processed_mail/bulk_delete/"
            both = other.post(path, json={"mail_ids": [free["id"], *owned]})
            kept = box.records(free_rule, 1)
            alone = other.post(path, json={"mail_ids": [free["id"]]})
            read = other.get("/api/processed_mail/")
            seen("P2 foreign:", both.status_code, both.text, alone.status_code, read.status_code)
    finally:
        _ = stack.admin.delete(f"/api/users/{user}/")

    assert both.status_code == 403
    assert len(kept) == 1
    assert alone.status_code == 200
    assert box.records(free_rule, 0) == []
    assert len(results(stack.admin.get("/api/processed_mail/", params={"rule": owned_rule}))) == 1


# --- the mail server itself (P4)


async def test_the_mail_server_keeps_uids_keywords_and_a_validity(box: Mailbox) -> None:
    uid = box.put("Plain")
    _, status = box.imap.status("INBOX", "(UIDVALIDITY UIDNEXT)")
    seen("P4:", box.imap.capabilities, status)

    assert b"UIDVALIDITY" in cast("bytes", status[0])
    assert box.uids() == [uid]
    assert SEEN not in box.flags(uid)
