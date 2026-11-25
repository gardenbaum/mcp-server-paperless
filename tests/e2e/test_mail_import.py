"""Mail imports against a real mail server: what is fetched, when, and what the rule does then.

Every row of the table in the design of PLMCP-81 (2.2), which `refetch` of get_processed_mail says,
and what Paperless answers and logs (probes P1 to P4). The tests run on a stack of their own
(`just e2e-imap`, marker `imap_e2e`) and skip elsewhere.
"""

import base64
import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from fastmcp import Client
from harness import paperless_settings, server_settings
from interleaving import before
from ratelimit import LimitingTransport, RateLimit

from e2e.echo_server import FORGED, echo_server
from e2e.echo_server import HOST as ECHO
from e2e.helpers import API_VERSION, in_container, num, obj, parse, results
from e2e.mailbox import BROKEN, PASSWORD, Mailbox, note, wait
from e2e.uploading import Scope
from mcp_server_paperless.domain.urls import ANSWER_NOT_SHOWN
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy
from mcp_server_paperless.server import Ports, create_server, paperless_ports

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator

    from fastmcp.client.transports import FastMCPTransport

    from e2e.echo_server import How
    from e2e.helpers import Json, Stack

IMAP = os.environ.get("PAPERLESS_E2E_IMAP") == "1"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.imap_e2e,
    pytest.mark.anyio,
    pytest.mark.decision("decision-80"),
    pytest.mark.skipif(not IMAP, reason="No mail server; `just e2e-imap` runs this with one."),
]
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

    assert box.fetch() == 2
    [failed] = box.records(rule, 1)
    shown = await record(client, failed)

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
    assert {row["uid"] for row in rows} == {uid}
    shown = await record(client, rows[0])
    assert f"until they are deleted too: {rows[1]['id']}. " in str(shown["refetch"])
    box.unflag(uid, FLAGGED)
    box.forget(num(rows[0]["id"]))
    assert box.fetch() == 0
    box.forget(num(rows[1]["id"]))
    assert box.fetch() == 2
    assert len(box.records(rule, 2)) == 2


async def test_records_are_listed_in_the_order_asked_for(box: Mailbox, client: Connected) -> None:
    """Received and processed differ: the mail that was sent later is fetched first."""
    rule = box.rule()
    _ = box.put("New")
    _ = box.fetch()
    _ = box.records(rule, 1)
    _ = box.put("Old", age=3600)
    _ = box.fetch()
    _ = box.records(rule, 2)

    async def listed(ordering: str) -> list[str]:
        found = await call(client, "list_processed_mail", rule_id=rule, ordering=ordering)
        return [str(row["subject"])[-3:] for row in cast("list[Json]", found["mails"])]

    assert await listed("processed") == ["New", "Old"]
    assert await listed("-processed") == ["Old", "New"]
    assert await listed("received") == ["Old", "New"]
    assert await listed("-received") == ["New", "Old"]


async def test_a_mail_without_attachments_is_imported_itself_again(
    box: Mailbox, client: Connected
) -> None:
    """The mail is queued before its attachments are looked at: SUCCESS and "no consumption"."""
    rule = box.rule("flag", consumption_scope=3)
    uid = box.put("Alone")

    assert box.fetch() == 1
    rows = box.records(rule, 2)
    assert {row["status"] for row in rows} == {"SUCCESS", "PROCESSED_WO_CONSUMPTION"}
    assert FLAGGED in box.flags(uid)
    [empty] = [row for row in rows if row["status"] == "PROCESSED_WO_CONSUMPTION"]
    said = str((await record(client, empty))["refetch"])
    assert "the mail itself and the files the rule takes from it are imported" in said
    box.unflag(uid, FLAGGED)
    box.forget(*(num(row["id"]) for row in rows))
    assert box.fetch() == 1
    _ = box.records(rule, 2)
    assert FLAGGED in box.flags(uid)
    assert len(box.documents("Alone")) == 2


async def test_the_rule_acts_when_the_mail_is_imported_although_its_file_fails(
    box: Mailbox, client: Connected
) -> None:
    """The mail itself and its attachments are queued apart, each with the action behind it."""
    rule = box.rule("flag", consumption_scope=3)
    uid = box.put("Half", {"broken.pdf": BROKEN})

    assert box.fetch() == 2
    [row, *_] = box.records(rule, count=None)
    # The first record can be that of the file that fails; the mail itself is imported after it.
    _ = wait("the rule to act", lambda: FLAGGED in box.flags(uid) or None)
    said = str((await record(client, row))["refetch"])
    assert "as soon as the mail itself, or every one of the files, is consumed" in said
    assert len(box.documents("Half")) == 1


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


# --- why a mail was not imported


@pytest.mark.decision("decision-90")
async def test_an_import_that_did_not_happen_is_explained(box: Mailbox, client: Connected) -> None:
    rule = box.rule(filter_attachment_filename_include="*.pdf")
    _ = box.put("Excluded", {"a.txt": note()})
    assert box.fetch() == 0
    _ = box.records(rule, 1)

    found = await call(
        client,
        "explain_mail_import",
        account_id=box.account,
        subject=f"{box.name} Excluded",
        folder="inbox",
        attachment_names=["a.txt"],
        is_read=False,
    )

    [checked] = cast("list[Json]", found["rules"])
    results = {c["what"]: c["result"] for c in cast("list[Json]", checked["checks"])}
    assert (results["folder"], results["attachments"], results["mark"]) == ("ok", "no", "ok")
    assert results["record"] == "unknown"
    assert checked["result"] == "no"
    [row] = cast("list[Json]", found["processed"])
    assert row["status"] == "PROCESSED_WO_CONSUMPTION"
    logs = "\n".join(cast("list[str]", found["logs"]))
    assert "does not match pattern *.pdf" in logs
    assert found["logs_from"]
    assert "mail_fetch" in {t["task_type"] for t in cast("list[Json]", found["tasks"])}


@pytest.mark.decision("decision-90")
async def test_a_skipped_fetch_and_a_failed_login_are_in_the_explanation(
    box: Mailbox, client: Connected
) -> None:
    _ = box.rule()
    box.change_account(password="wr0ng-Passw0rd")
    assert box.fetch() == 0
    lock("add('paperless_mail_fetch_lock', 'e2e', timeout=300)")
    try:
        assert box.fetch() == 0
    finally:
        lock("delete('paperless_mail_fetch_lock')")

    found = await call(client, "explain_mail_import", account_id=box.account, subject="x")

    logs = "\n".join(cast("list[str]", found["logs"]))
    assert "Mail account processing is already running; skipping this run." in logs
    assert f"Error while authenticating account {box.name}: {ANSWER_NOT_SHOWN}" in logs
    assert "AUTHENTICATIONFAILED" not in str(found)


@pytest.mark.decision("decision-90")
async def test_a_user_who_is_no_staff_is_told_what_is_not_known(box: Mailbox, stack: Stack) -> None:
    settings = obj(parse(stack.admin.get("/api/ui_settings/")))
    # A rule of somebody else, which the user does not see.
    hidden = box.rule(owner=num(obj(settings["user"])["id"]))
    body = {
        "username": f"{box.name}-user",
        "password": "pw-e2e-imap-4711",
        "user_permissions": ["view_mailaccount", "view_mailrule"],
    }
    user = num(obj(parse(stack.admin.post("/api/users/", json=body).raise_for_status()))["id"])
    box.change_account(owner=user)
    try:
        with httpx2.Client(
            base_url=stack.base_url,
            auth=(f"{box.name}-user", "pw-e2e-imap-4711"),
            headers={"Accept": f"application/json; version={API_VERSION}"},
        ) as other:
            token = str(parse(other.post("/api/profile/generate_auth_token/")))
        server = create_server(
            paperless_settings(stack.base_url, token), server_settings(toolsets=["core", "mail"])
        )
        async with Client(server) as limited:
            found = await call(limited, "explain_mail_import", account_id=box.account)
            visible = box.rule(owner=user, order=9)
            seen = await call(limited, "explain_mail_import", account_id=box.account)
    finally:
        box.change_account(owner=None)
        _ = stack.admin.delete(f"/api/users/{user}/")

    assert "You see no rule of this account" in str(found["message"])
    assert "Rules of other users that you may not see" in str(found["message"])
    gaps = cast("list[str]", found["not_checked"])
    assert "whether a fetch was skipped is not known" in gaps
    assert found["complete"] is False
    assert found["logs"] == []
    assert [rule["rule_id"] for rule in cast("list[Json]", seen["rules"])] == [visible]
    assert f"(id {hidden})" not in str(seen)


DAY = 86_400


async def explained(box: Mailbox, client: Connected, rule: int, **facts: object) -> Json:
    """What the tool says of one rule; the answer says that it is no dry run."""
    found = await call(client, "explain_mail_import", account_id=box.account, rule_id=rule, **facts)
    assert str(found["message"]).startswith("This is not a dry run")
    [checked] = cast("list[Json]", found["rules"])
    assert checked["rule_id"] == rule
    return found


async def said(box: Mailbox, client: Connected, rule: int, what: str, **facts: object) -> list[str]:
    """The results of one check of a rule."""
    [checked] = cast("list[Json]", (await explained(box, client, rule, **facts))["rules"])
    checks = cast("list[Json]", checked["checks"])
    return [str(check["result"]) for check in checks if check["what"] == what]


@pytest.mark.decision("decision-90")
@pytest.mark.parametrize(
    ("field", "what", "given", "other", "fitting"),
    [
        ("filter_from", "sender", "Sender <sender@example.org>", "nobody@", "SENDER@example.org"),
        ("filter_to", "recipient", "me@example.org", "you@example.org", "ME@example.org"),
        ("filter_subject", "subject", "Invoice", "Reminder", "INVOICE"),
        ("filter_body", "body", None, "nowhere in it", "body of the mail"),
    ],
)
async def test_a_filter_for_text_is_explained_as_the_server_then_searches(  # noqa: PLR0913, PLR0917 -- the cases
    box: Mailbox,
    client: Connected,
    field: str,
    what: str,
    given: str | None,
    other: str,
    fitting: str,
) -> None:
    rule = box.rule(**{field: other})
    _ = box.put("Invoice", {"a.txt": note()})
    facts = {what: given} if given else {}

    assert await said(box, client, rule, what, **facts) == ["no" if given else "unknown"]
    assert await said(box, client, rule, what) == ["unknown"]
    assert box.fetch() == 0
    assert box.records(rule, 0) == []

    box.change_rule(rule, **{field: fitting})
    assert await said(box, client, rule, what, **facts) == ["ok" if given else "unknown"]
    assert box.fetch() == 1
    _ = box.records(rule, 1)


@pytest.mark.decision("decision-90")
async def test_the_age_is_explained_as_the_server_then_searches(
    box: Mailbox, client: Connected
) -> None:
    rule = box.rule(maximum_age=5)
    _ = box.put("Old", {"a.txt": note()}, arrived=10 * DAY)
    received = datetime.now(UTC) - timedelta(days=10)
    facts = {"received": received.isoformat()}

    assert await said(box, client, rule, "age", **facts) == ["no"]
    assert await said(box, client, rule, "age") == ["unknown"]
    assert box.fetch() == 0
    assert box.records(rule, 0) == []
    box.change_rule(rule, maximum_age=10)
    assert await said(box, client, rule, "age", **facts) == ["unknown"]

    box.change_rule(rule, maximum_age=20)
    assert await said(box, client, rule, "age", **facts) == ["ok"]
    assert box.fetch() == 1
    _ = box.records(rule, 1)
    box.change_rule(rule, maximum_age=0)
    assert await said(box, client, rule, "age") == ["ok"]


@pytest.mark.decision("decision-90")
async def test_another_folder_is_explained(box: Mailbox, client: Connected) -> None:
    _ = box.imap.create("Elsewhere")
    rule = box.rule(folder="INBOX")
    _ = box.put("Filed", {"a.txt": note()}, folder="Elsewhere")

    assert await said(box, client, rule, "folder", folder="Elsewhere") == ["no"]
    assert await said(box, client, rule, "folder") == ["unknown"]
    assert box.fetch() == 0
    assert box.records(rule, 0) == []

    box.change_rule(rule, folder="Elsewhere")
    assert await said(box, client, rule, "folder", folder="Elsewhere") == ["ok"]
    assert box.fetch() == 1
    _ = box.records(rule, 1)


@pytest.mark.decision("decision-90")
async def test_a_disabled_rule_is_explained(box: Mailbox, client: Connected) -> None:
    rule = box.rule(enabled=False)
    _ = box.put("Off", {"a.txt": note()})

    assert await said(box, client, rule, "enabled") == ["no"]
    found = await explained(box, client, rule, subject=f"{box.name} Off")
    assert "None of these rules is enabled" in str(found["message"])
    assert box.fetch() == 0
    found = await explained(box, client, rule, subject=f"{box.name} Off")
    assert f"No rules enabled for account {box.name}. Skipping." in str(found["logs"])

    box.change_rule(rule, enabled=True)
    assert await said(box, client, rule, "enabled") == []
    assert box.fetch() == 1
    _ = box.records(rule, 1)


@pytest.mark.decision("decision-90")
@pytest.mark.parametrize(
    ("files", "fields", "before", "after"),
    [
        (["a.txt"], {"filter_attachment_filename_include": "*.pdf"}, "no", "unknown"),
        (["a.txt"], {"filter_attachment_filename_exclude": "*.TXT"}, "no", "unknown"),
        ([], {}, "no", "ok"),
    ],
)
async def test_attachments_are_explained(  # noqa: PLR0913, PLR0917 -- the cases
    box: Mailbox,
    client: Connected,
    files: list[str],
    fields: dict[str, object],
    before: str,
    after: str,
) -> None:
    rule = box.rule("mark_read", **fields)
    _ = box.put("Files", {name: note() for name in files})

    assert await said(box, client, rule, "attachments", attachment_names=files) == [before]
    assert await said(box, client, rule, "attachments") == ["unknown"]
    assert box.fetch() == 0
    [row] = box.records(rule, 1)
    assert row["status"] == "PROCESSED_WO_CONSUMPTION"

    # Without its filter, or for the mail itself, the rule takes it.
    if files:
        box.change_rule(rule, **dict.fromkeys(fields, None))
    else:
        box.change_rule(rule, consumption_scope=2)
    assert await said(box, client, rule, "attachments", attachment_names=files) == [after]
    box.forget(num(row["id"]))
    assert box.fetch() == 1
    _ = box.records(rule, 1)


@pytest.mark.decision("decision-90")
@pytest.mark.parametrize(
    ("action", "parameter", "flag", "fact"),
    [
        ("mark_read", None, SEEN, "is_read"),
        ("flag", None, FLAGGED, "is_flagged"),
        ("tag", "apple:red", FLAGGED, "is_flagged"),
        ("tag", "done", "done", None),
    ],
)
async def test_a_mark_is_explained(  # noqa: PLR0913, PLR0917 -- the cases
    box: Mailbox,
    client: Connected,
    action: str,
    parameter: str | None,
    flag: str,
    fact: str | None,
) -> None:
    rule = box.rule(action, action_parameter=parameter)
    uid = box.put("Marked", {"a.txt": note()}, flag=flag)

    def given(*, marked: bool) -> dict[str, bool]:
        return {fact: marked} if fact else {}

    marked = await said(box, client, rule, "mark", **given(marked=True))
    assert marked == ["no" if fact else "unknown"]
    assert await said(box, client, rule, "mark") == ["unknown"]
    assert box.fetch() == 0
    assert box.records(rule, 0) == []

    box.unflag(uid, flag)
    unmarked = await said(box, client, rule, "mark", **given(marked=False))
    assert unmarked == ["ok" if fact else "unknown"]
    assert box.fetch() == 1
    _ = box.records(rule, 1)


@pytest.mark.decision("decision-90")
@pytest.mark.parametrize("colour", ["apple:pink", "apple:red:blue"])
async def test_a_colour_paperless_does_not_know_is_explained(
    box: Mailbox, client: Connected, colour: str
) -> None:
    rule = box.rule("tag", action_parameter=colour)
    _ = box.put("Coloured", {"a.txt": note()})

    assert await said(box, client, rule, "mark", is_flagged=False) == ["no"]
    assert box.fetch() == 0
    assert box.records(rule, 0) == []
    found = await explained(box, client, rule, subject=f"{box.name} Coloured")
    assert f"Rule {box.name}.{box.name}-0: Error while processing rule" in str(found["logs"])


@pytest.mark.decision("decision-90")
async def test_a_folder_to_move_to_that_is_missing_is_explained(
    box: Mailbox, client: Connected
) -> None:
    rule = box.rule("move", action_parameter="Missing")
    uid = box.put("Moving", {"a.txt": note()})

    assert await said(box, client, rule, "target") == ["unknown"]
    assert box.fetch() == 0
    assert (box.records(rule, 0), box.uids()) == ([], [uid])

    _ = box.imap.create("Missing")
    assert await said(box, client, rule, "target") == ["unknown"]
    assert box.fetch() == 1
    _ = box.records(rule, 1)
    assert box.uids() == []


@pytest.mark.decision("decision-90")
async def test_a_record_and_a_failed_file_are_explained(box: Mailbox, client: Connected) -> None:
    rule = box.rule("flag")
    _ = box.put("Broken", {"broken.pdf": BROKEN})
    assert await said(box, client, rule, "record", subject=f"{box.name} Broken") == []
    assert box.fetch() == 1
    [row] = box.records(rule, 1)

    found = await explained(box, client, rule, subject=f"{box.name} BROKEN")

    [processed] = cast("list[Json]", found["processed"])
    assert (processed["id"], processed["status"]) == (row["id"], "FAILED")
    assert processed["error"]
    [checked] = cast("list[Json]", found["rules"])
    [check] = [c for c in cast("list[Json]", checked["checks"]) if c["what"] == "record"]
    assert check["result"] == "unknown"
    assert str(check["why"]).startswith(f"records {row['id']} have this subject")
    tasks = [(t["task_type"], t["state"]) for t in cast("list[Json]", found["tasks"])]
    assert ("consume_file", "failure") in tasks
    assert box.fetch() == 0
    box.forget(num(row["id"]))
    assert await said(box, client, rule, "record", subject=f"{box.name} Broken") == []


@pytest.mark.decision("decision-90")
async def test_a_rule_that_ends_the_run_is_explained(box: Mailbox, client: Connected) -> None:
    _ = box.imap.create("First")
    first = box.rule("flag", folder="First", order=1, stop_processing=True)
    second = box.rule("flag", order=2)
    _ = box.put("Stops", {"a.txt": note()}, folder="First")
    _ = box.put("Waits", {"b.txt": note()})

    found = await explained(box, client, second)
    [checked] = cast("list[Json]", found["rules"])
    [check] = [c for c in cast("list[Json]", checked["checks"]) if c["what"] == "earlier rules"]
    assert check["result"] == "unknown"
    assert f"(id {first}) ends the run" in str(check["why"])
    assert await said(box, client, first, "earlier rules") == []

    assert box.fetch() == 1
    _ = box.records(first, 1)
    assert box.records(second, 0) == []
    assert box.fetch() == 1
    _ = box.records(second, 1)


@pytest.mark.decision("decision-90")
async def test_a_rule_that_takes_the_mail_first_is_explained(
    box: Mailbox, client: Connected
) -> None:
    first, second = box.rule("flag", order=1), box.rule("flag", order=2)
    _ = box.put("Once", {"a.txt": note()})

    found = await explained(box, client, second)
    [checked] = cast("list[Json]", found["rules"])
    [check] = [c for c in cast("list[Json]", checked["checks"]) if c["what"] == "earlier rules"]
    assert check["result"] == "unknown"
    assert f"(id {first}) reads the same folder first" in str(check["why"])

    assert box.fetch() == 1
    _ = box.records(first, 1)
    assert box.records(second, 0) == []
    told = await explained(box, client, second, subject=f"{box.name} Once")
    assert "already queued by a previous rule in this run" in str(told["logs"])


@pytest.mark.decision("decision-90")
async def test_what_the_account_lacks_is_explained(box: Mailbox, client: Connected) -> None:
    rule = box.rule()
    assert (await explained(box, client, rule))["account"] == []

    box.change_account(account_type=2, is_token=True, expiration="2020-01-01T00:00:00Z")
    code = (
        "from paperless_mail.models import MailAccount; "
        f"MailAccount.objects.filter(pk={box.account}).update(username='')"
    )
    in_container("python", "manage.py", "shell", "-c", code)

    notes = "\n".join(cast("list[str]", (await explained(box, client, rule))["account"]))
    assert "The access token has expired" in notes
    assert "The account has no username" in notes


# --- logins that fail


async def test_the_answer_to_a_failed_login_is_in_no_result_and_no_log(
    box: Mailbox, client: Connected
) -> None:
    """The texts the redaction of decision-80 knows are those Paperless 3.5.1 writes."""
    _ = box.rule()
    box.change_account(password="wr0ng-Passw0rd")
    assert box.fetch() == 0

    raw = {name: box.log(name) for name in ("mail", "paperless", "celery")}
    logged = [line for line in raw["mail"] if f"authenticating account {box.name}: " in line]
    assert logged, "Paperless no longer logs the answer like this"
    answer = logged[0].split(f"{box.name}: ", 1)[1]
    assert "AUTHENTICATIONFAILED" in answer
    for name, lines in raw.items():
        shown = await call(client, "get_log", name=name, lines=1000)
        text = "\n".join(cast("list[str]", shown["lines"]))
        assert "AUTHENTICATIONFAILED" not in text
        assert "wr0ng" not in text
        if name != "celery":
            assert any("AUTHENTICATIONFAILED" in line for line in lines[-50:])
            assert ANSWER_NOT_SHOWN in text
    for path in ("/api/logs/mail/", "/api/logs/paperless/"):
        tail = {"limit": 60}
        assert "AUTHENTICATIONFAILED" in box.stack.admin.get(path, params=tail).text
        read = await call(client, "paperless_api_get", path=path, params=tail)
        assert "AUTHENTICATIONFAILED" not in json.dumps(read)
        assert ANSWER_NOT_SHOWN in json.dumps(read)


SENT = "wr0ng-Passw0rd-0815"
TOKEN = {"is_token": True, "account_type": 2, "expiration": "2099-01-01T00:00:00Z"}


def credentials(box: Mailbox, password: str) -> dict[str, str]:
    """What Paperless sends for each kind of login, as a server can repeat it."""
    plain = f"\0{box.name}\0{password}"
    xoauth2 = f"user={box.name}\1auth=Bearer {password}\1\1"
    return {
        "login": f'"{box.name}" "{password}"',
        "plain": base64.b64encode(plain.encode()).decode(),
        "xoauth2": base64.b64encode(xoauth2.encode()).decode(),
    }


@pytest.mark.parametrize("how", ["no", "bye", "logout"])
@pytest.mark.parametrize("login", ["login", "plain", "xoauth2"])
async def test_a_server_that_repeats_the_credentials_leaks_none(
    box: Mailbox, client: Connected, how: How, login: str
) -> None:
    """LOGIN for an ASCII password, AUTHENTICATE PLAIN for another one, XOAUTH2 for a token."""
    _ = box.rule()
    password = f"{SENT}ü" if login == "plain" else SENT
    sent = credentials(box, password)
    forms = [SENT, *sent.values()]
    raw: dict[str, list[str]] = {}
    with echo_server(how) as port:
        box.change_account(
            imap_server=ECHO,
            imap_port=port,
            password=password,
            **(TOKEN if login == "xoauth2" else {}),
        )
        assert box.fetch() == 0
        raw = {name: box.log(name) for name in ("mail", "paperless")}
    written = "\n".join("\n".join(lines[-60:]) for lines in raw.values())
    assert sent[login] in written, "the server's answer is not in the logs"

    for name in raw:
        shown = await call(client, "get_log", name=name, lines=1000)
        read = await call(
            client, "paperless_api_get", path=f"/api/logs/{name}/", params={"limit": 60}
        )
        for form in forms:
            assert form not in str(shown)
            assert form not in str(read)
    told = await call(client, "explain_mail_import", account_id=box.account)
    assert told["logs"], "the failed login is not among the lines"
    for form in forms:
        assert form not in str(told)


@pytest.mark.decision("decision-106")
@pytest.mark.parametrize("how", ["dated", "record"])
@pytest.mark.parametrize("login", ["login", "plain", "xoauth2"])
async def test_a_server_that_forges_the_start_of_a_log_record(
    box: Mailbox, client: Connected, how: How, login: str
) -> None:
    """A part of a start hides nothing from the redaction; a whole one is the known residual.

    The server sends a BYE to LOGIN, and a NO to AUTHENTICATE.
    """
    _ = box.rule()
    # Of its own: what the last case shows must not be taken for that of another test.
    password = f"f0rged-{box.name}{'ü' if login == 'plain' else ''}"
    secret = password if login == "login" else credentials(box, password)[login]
    with echo_server(how) as port:
        box.change_account(
            imap_server=ECHO,
            imap_port=port,
            password=password,
            **(TOKEN if login == "xoauth2" else {}),
        )
        assert box.fetch() == 0
    forged = [line for line in box.log("mail") if line.startswith(FORGED[how]) and secret in line]
    assert forged, "the carriage return no longer makes a line of its own"

    for name in ("mail", "paperless"):
        shown = await call(client, "get_log", name=name, lines=1000)
        read = await call(
            client, "paperless_api_get", path=f"/api/logs/{name}/", params={"limit": 60}
        )
        for result in (shown, read):
            # A real server could do the same, so this is named in decision-106, not protected.
            assert (secret in json.dumps(result)) is (how == "record")
    told = await call(client, "explain_mail_import", account_id=box.account)
    assert secret not in json.dumps(told)


@pytest.mark.parametrize("how", ["no", "bye"])
async def test_the_record_of_a_login_that_repeats_the_credentials_shows_none(
    box: Mailbox, client: Connected, how: How
) -> None:
    rule = box.rule("flag")
    _ = box.put("Echo", {f"{n}.txt": note() for n in range(8)})
    with echo_server(how) as port:
        assert box.fetch() == 8
        box.change_account(imap_server=ECHO, imap_port=port, password=SENT)
        [failed] = box.records(rule, 1)

    assert SENT in str(failed["error"])
    shown = await record(client, failed)
    listed = await call(client, "list_processed_mail", rule_id=rule)
    raw = await call(
        client, "paperless_api_get", path="/api/processed_mail/", params={"rule": rule}
    )
    for result in (shown, listed, raw):
        assert SENT not in str(result)


@pytest.mark.decision("decision-87")
async def test_a_change_grant_sends_the_saved_password_anywhere_without_storing_anything(
    box: Mailbox, stack: Stack
) -> None:
    """What the confirmation of set_permissions says: the account is only tested, not changed."""
    name, password = f"{box.name}-user", "pw-e2e-imap-4711"
    body = {
        "username": name,
        "password": password,
        "user_permissions": ["view_mailaccount", "change_mailaccount"],
    }
    user = num(obj(parse(stack.admin.post("/api/users/", json=body).raise_for_status()))["id"])
    box.change_account(
        set_permissions={
            "view": {"users": [user], "groups": []},
            "change": {"users": [user], "groups": []},
        }
    )
    heard: list[str] = []
    try:
        with (
            echo_server("no", heard) as port,
            httpx2.Client(
                base_url=stack.base_url,
                auth=(name, password),
                headers={"Accept": f"application/json; version={API_VERSION}"},
                timeout=60.0,
            ) as grantee,
        ):
            body = {
                "id": box.account,
                "name": "x",
                "imap_server": ECHO,
                "imap_port": port,
                "imap_security": 1,
                "username": "me",
                "password": "*",
            }
            tested = grantee.post("/api/mail_accounts/test/", json=body)
    finally:
        _ = stack.admin.delete(f"/api/users/{user}/")

    assert tested.status_code == 400
    assert any(PASSWORD in line for line in heard)
    stored = obj(parse(stack.admin.get(f"/api/mail_accounts/{box.account}/")))
    assert stored["imap_server"] == "imap"


async def test_a_login_that_fails_when_the_rule_acts_shows_no_answer(
    box: Mailbox, client: Connected
) -> None:
    """The password changes while the attachments are imported: the action cannot sign in."""
    rule = box.rule("flag")
    uid = box.put("Late", {f"{n}.txt": note() for n in range(8)})
    assert box.fetch() == 8
    box.change_account(password="wr0ng-Passw0rd")

    [failed] = box.records(rule, 1)
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
    shown = await record(client, failed)

    assert failed["status"] == "FAILED"
    assert shown["error"] == failed["error"]
    assert "Traceback" in str(shown["error"])


def lock(command: str) -> None:
    """Take or give back the lock a running fetch holds in Paperless's cache."""
    code = f"from django.core.cache import cache; assert cache.{command}"
    in_container("python", "manage.py", "shell", "-c", code)


async def test_a_fetch_while_another_runs_is_skipped(box: Mailbox) -> None:
    """T5: the process call answers OK all the same, and only the log tells (no result_data)."""
    rule = box.rule()
    _ = box.put("Skipped", {"a.txt": note()})
    before = set(box.tasks("mail_fetch"))
    lock("add('paperless_mail_fetch_lock', 'e2e', timeout=300)")
    try:
        assert box.fetch() == 0
    finally:
        lock("delete('paperless_mail_fetch_lock')")

    [skipped] = [row for number, row in box.tasks("mail_fetch").items() if number not in before]
    assert (skipped["status"], skipped["result_data"]) == ("success", None)
    said = "Mail account processing is already running; skipping this run."
    assert any(said in line for line in box.log("paperless"))
    assert box.fetch() == 1
    _ = box.records(rule, 1)


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
    finally:
        _ = stack.admin.delete(f"/api/users/{user}/")

    assert both.status_code == 403
    assert len(kept) == 1
    assert (alone.status_code, read.status_code) == (200, 403)  # deleting needs no view permission
    assert box.records(free_rule, 0) == []
    assert len(results(stack.admin.get("/api/processed_mail/", params={"rule": owned_rule}))) == 1


async def error(client: Connected, tool: str, **arguments: object) -> str:
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    assert PASSWORD not in str(result)
    return str(result.content)


@pytest.mark.decision("decision-83")
async def test_a_failed_mail_is_taken_again_after_the_tool_deleted_its_record(
    box: Mailbox, client: Connected
) -> None:
    """The whole way: failed, deleted after a confirmation, fetched again, and the rule deletes."""
    rule = box.rule("delete")
    _ = box.put("Again", {"good.txt": note(), "broken.pdf": BROKEN})
    assert box.fetch() == 2
    [failed] = box.records(rule, 1)
    box.change_rule(rule, filter_attachment_filename_exclude="broken*")

    preview = await call(client, "delete_processed_mail", mail_ids=[failed["id"]], dry_run=True)
    asked = await error(client, "delete_processed_mail", mail_ids=[failed["id"]])
    missing = await error(
        client, "delete_processed_mail", mail_ids=[failed["id"], 999_999], confirm=True
    )
    assert box.records(rule, 1) == [failed]
    done = await call(client, "delete_processed_mail", mail_ids=[failed["id"]], confirm=True)

    for text in (str(preview["message"]), asked):
        assert f"{failed['id']} '{box.name} Again'" in text
        assert "takes the mail again" in text
        assert "the rule then DELETES the mail on the mail server" in text
    assert "No processed mail 999999 exists" in missing
    assert [row["outcome"] for row in cast("list[Json]", done["mails"])] == ["deleted"]
    assert box.records(rule, 0) == []
    assert box.fetch() == 1
    [taken] = box.records(rule, 1)
    assert taken["status"] == "SUCCESS"
    assert len(box.documents("Again")) == 2
    assert box.uids() == []


@pytest.mark.decision("decision-83")
async def test_a_record_of_a_pair_names_the_one_that_keeps_the_mail_out(
    box: Mailbox, client: Connected
) -> None:
    rule = box.rule("flag", consumption_scope=3)
    _ = box.put("Pair", {"a.txt": note()})
    assert box.fetch() == 2
    first, second = box.records(rule, 2)

    one = await call(client, "delete_processed_mail", mail_ids=[first["id"]], dry_run=True)
    both = await call(
        client, "delete_processed_mail", mail_ids=[first["id"], second["id"]], confirm=True
    )

    assert f"kept out by the records {second['id']} with its uid" in str(one["message"])
    assert both["message"] == "2 of 2 records were deleted."
    assert box.records(rule, 0) == []


@pytest.mark.decision("decision-83")
async def test_a_restricted_user_is_told_what_is_missing_and_deletes_unowned_records(
    box: Mailbox, stack: Stack
) -> None:
    rule, ids = recorded(box, 1, owner=None)
    name, password = f"{box.name}-user", "pw-e2e-imap-4711"
    body = {
        "username": name,
        "password": password,
        "user_permissions": ["view_processedmail", "view_uisettings"],
    }
    user = num(obj(parse(stack.admin.post("/api/users/", json=body).raise_for_status()))["id"])
    try:
        with httpx2.Client(
            base_url=stack.base_url,
            auth=(name, password),
            headers={"Accept": f"application/json; version={API_VERSION}"},
        ) as other:
            token = str(parse(other.post("/api/profile/generate_auth_token/")))
        server = create_server(
            paperless_settings(stack.base_url, token), server_settings(toolsets=["core", "mail"])
        )
        async with Client(server) as limited:
            lacking = await error(limited, "delete_processed_mail", mail_ids=ids, confirm=True)
            rights = [*body["user_permissions"], "delete_processedmail"]
            _ = stack.admin.patch(
                f"/api/users/{user}/", json={"user_permissions": rights}
            ).raise_for_status()
            done = await call(limited, "delete_processed_mail", mail_ids=ids, confirm=True)
    finally:
        _ = stack.admin.delete(f"/api/users/{user}/")

    assert "You lack the permission delete_processedmail. Nothing was deleted." in lacking
    assert done["message"] == "1 of 1 record was deleted."
    assert box.records(rule, 0) == []


@asynccontextmanager
async def acting(
    stack: Stack,
    limit: RateLimit,
    ports: Callable[[Ports], Ports] = lambda ports: ports,
    token: str = "",
) -> AsyncGenerator[Connected]:
    """A client of the server whose way to Paperless can lose answers or end."""
    async with httpx2.AsyncClient(
        base_url=stack.base_url, transport=LimitingTransport(limit), timeout=30
    ) as http:
        paperless = PaperlessClient(
            http,
            StaticTokenProvider(token or stack.admin_token),
            retry=RetryPolicy(),
            upload_timeout=30,
        )
        server = create_server(
            paperless_settings(stack.base_url, token or stack.admin_token),
            server_settings(toolsets=["core", "mail"]),
            ports=lambda _client: ports(paperless_ports(paperless)),
        )
        async with Client(server) as connected:
            yield connected


def outcomes(answer: Json) -> list[object]:
    return [row["outcome"] for row in cast("list[Json]", answer["mails"])]


NOT_REPEATED = "It is not repeated: check with list_processed_mail before you call this again."


@pytest.mark.decision("decision-83")
async def test_a_record_that_is_there_again_is_not_said_to_be_deleted(
    box: Mailbox, stack: Stack
) -> None:
    rule, ids = recorded(box, 3)
    uid = next(row["uid"] for row in box.records(rule, 3) if row["id"] == ids[1])

    async def returns(*_args: object) -> None:
        # Paperless stores the record again under its id before the answer is checked.
        code = (
            "from paperless_mail.models import ProcessedMail\n"
            "from django.utils.timezone import now\n"
            f"ProcessedMail.objects.create(pk={ids[1]}, rule_id={rule}, folder='INBOX', "
            f"subject='{box.name} again', uid='{uid}', status='FAILED', received=now())\n"
        )
        in_container("python", "manage.py", "shell", "-c", code)

    def ports(real: Ports) -> Ports:
        # Every record is read once before the deletion: the fourth read is the first afterwards.
        return replace(real, mail=before(real.mail, "get_processed_mail", returns, at=4))

    async with acting(stack, RateLimit(), ports) as client:
        done = await call(client, "delete_processed_mail", mail_ids=ids, confirm=True)

    assert outcomes(done) == ["deleted", "still_there", "deleted"]
    assert done["message"] == f"2 of 3 records were deleted. Still there: {ids[1]}."
    assert [row["id"] for row in box.records(rule, 1)] == [ids[1]]


@pytest.mark.decision("decision-83")
async def test_a_deletion_whose_answer_is_lost_is_read_back_and_not_repeated(
    box: Mailbox, stack: Stack
) -> None:
    rule, ids = recorded(box, 2)
    limit = RateLimit(lost=frozenset({"POST /api/processed_mail/bulk_delete/"}))

    async with acting(stack, limit) as client:
        done = await call(client, "delete_processed_mail", mail_ids=ids, confirm=True)

    assert outcomes(done) == ["deleted", "deleted"]
    assert "Paperless did not confirm the deletion: " in str(done["message"])
    assert str(done["message"]).endswith(NOT_REPEATED)
    assert box.records(rule, 0) == []


@pytest.mark.decision("decision-107")
async def test_an_ordinary_user_is_not_told_that_a_record_is_gone_without_an_answer(
    box: Mailbox, stack: Stack
) -> None:
    rule, ids = recorded(box, 2, owner=None)
    people = Scope(stack)
    limit = RateLimit(lost=frozenset({"POST /api/processed_mail/bulk_delete/"}))
    try:
        token = people.user("view_processedmail", "delete_processedmail", "view_uisettings")
        async with acting(stack, limit, token=token) as client:
            done = await call(client, "delete_processed_mail", mail_ids=ids, confirm=True)
    finally:
        people.clean_up()

    assert outcomes(done) == ["unknown", "unknown"]
    message = str(done["message"])
    assert f"Not visible to you any more, so deleted or hidden: {ids[0]}, {ids[1]}." in message
    assert message.endswith(NOT_REPEATED)
    assert box.records(rule, 0) == []


@pytest.mark.decision("decision-83")
async def test_records_that_cannot_be_read_again_are_unknown(box: Mailbox, stack: Stack) -> None:
    rule, ids = recorded(box, 3)
    limit = RateLimit()

    def ports(real: Ports) -> Ports:
        return replace(real, mail=before(real.mail, "get_processed_mail", limit.begin, at=5))

    async with acting(stack, limit, ports) as client:
        done = await call(client, "delete_processed_mail", mail_ids=ids, confirm=True)

    assert outcomes(done) == ["deleted", "unknown", "unknown"]
    message = str(done["message"])
    assert message.startswith("1 of 3 records were seen to be deleted. ")
    assert f"Not known: {ids[1]}, {ids[2]}." in message
    assert message.endswith(NOT_REPEATED)
    assert len(set(limit.asked)) == 1, limit.asked
    assert box.records(rule, 0) == []


# --- the mail server itself (P4)


async def test_the_mail_server_keeps_uids_keywords_and_a_validity(box: Mailbox) -> None:
    uid = box.put("Plain")
    _, status = box.imap.status("INBOX", "(UIDVALIDITY UIDNEXT)")

    assert b"UIDVALIDITY" in cast("bytes", status[0])
    assert box.uids() == [uid]
    assert SEEN not in box.flags(uid)
