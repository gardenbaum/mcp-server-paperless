"""Deleting records of processed mail and explaining an import, on the fake and a live Paperless.

The tools run as they are on the contract backends (contract/serving.py). Neither backend has a mail
server: the records are made in Paperless itself, and what is fetched again is what the tools say;
that the mail server then gives it is proven in tests/e2e/test_mail_import.py.
"""

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest
from interleaving import before

from contract.conftest import TOKEN
from contract.serving import output, refusal
from contract.test_mail import PASSWORD, made, mail
from mcp_server_paperless.domain.bulk import ObjectPermissions
from mcp_server_paperless.domain.errors import NotFound
from mcp_server_paperless.domain.mail import MailAccountValues
from mcp_server_paperless.domain.permissions import AccessChange

if TYPE_CHECKING:
    from contract.conftest import Backend
    from contract.serving import Json
    from contract.test_mail import Mail

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-83")]

__all__ = ["mail"]  # the fixture

DELETE = "delete_processed_mail"
# The request of the adapter and the port method of the fake.
DELETION = frozenset({"POST /api/processed_mail/bulk_delete/", DELETE})
NOT_REPEATED = "It is not repeated: check with list_processed_mail before you call this again."


def outcomes(answer: Json) -> list[object]:
    return [row["outcome"] for row in cast("list[Json]", answer["mails"])]


async def gone(backend: Backend, number: int) -> bool:
    try:
        _ = await backend.admin.mail.get_processed_mail(number)
    except NotFound:
        return True
    return False


# --- deleting records


@pytest.mark.parametrize(
    ("action", "status", "said"),
    [
        ("delete", "FAILED", "the rule then DELETES the mail on the mail server"),
        ("mark_read", "FAILED", "takes the mail again if it still matches the rule's folder"),
        ("mark_read", "SUCCESS", "is not fetched while it is read on the mail server"),
        ("mark_read", "PROCESSED_WO_CONSUMPTION", "looks at the mail again"),
    ],
)
async def test_what_is_fetched_again_is_said_before_a_record_goes(
    backend: Backend, mail: Mail, action: str, status: str, said: str
) -> None:
    rule = await mail.rule((await mail.account()).id, action=action)
    backend.new_processed(rule.id, [("Invoice", "41", status, None)])
    found = await backend.admin.mail.list_processed_mail(
        rule_id=rule.id, status=None, ordering="processed", page=1, page_size=25
    )
    [row] = found.mails
    ports = backend.admin

    read = await output(ports, "get_processed_mail", mail_id=row.id)
    preview = await output(ports, DELETE, mail_ids=[row.id], dry_run=True)
    asked = await refusal(ports, DELETE, mail_ids=[row.id])
    assert not await gone(backend, row.id)
    done = await output(ports, DELETE, mail_ids=[row.id], confirm=True)

    for text in (str(read["refetch"]), str(preview["message"]), asked):
        assert said in text
    assert outcomes(preview) == ["would_delete"]
    assert outcomes(done) == ["deleted"]
    assert await gone(backend, row.id)


async def test_the_other_record_of_a_mail_that_keeps_it_out_is_named(
    backend: Backend, mail: Mail
) -> None:
    first, second = await made(
        backend, mail, ("Pair", "7", "SUCCESS", None), ("Pair", "7", "FAILED", None)
    )

    read = await output(backend.admin, "get_processed_mail", mail_id=first.id)
    preview = await output(backend.admin, DELETE, mail_ids=[first.id], dry_run=True)

    assert f"keep it out until they are deleted too: {second.id}." in str(read["refetch"])
    assert f"kept out by the records {second.id} with its uid" in str(preview["message"])


async def test_a_deletion_whose_answer_is_lost_is_read_back_and_not_repeated(
    backend: Backend, mail: Mail
) -> None:
    rows = await made(backend, mail, *((subject, "1", "FAILED", None) for subject in "ab"))
    backend.rate_limit.lost = DELETION

    done = await output(backend.limited, DELETE, mail_ids=[r.id for r in rows], confirm=True)

    assert outcomes(done) == ["deleted", "deleted"]
    assert "Paperless did not confirm the deletion: " in str(done["message"])
    assert str(done["message"]).endswith(NOT_REPEATED)
    assert [await gone(backend, r.id) for r in rows] == [True, True]


@pytest.mark.decision("decision-107")
async def test_an_ordinary_user_is_not_told_that_a_record_is_gone_without_an_answer(
    backend: Backend, mail: Mail
) -> None:
    """It may be hidden: the same calls say deleted where Paperless answered."""
    rows = await made(backend, mail, *((subject, "1", "FAILED", None) for subject in "abc"))
    lost, answered = rows[:2], rows[2]
    user = backend.restricted(
        frozenset({"view_processedmail", "delete_processedmail", "view_uisettings"}), limited=True
    )

    told = await output(user, DELETE, mail_ids=[answered.id], confirm=True)
    backend.rate_limit.lost = DELETION
    done = await output(user, DELETE, mail_ids=[r.id for r in lost], confirm=True)

    assert outcomes(told) == ["deleted"]
    assert outcomes(done) == ["unknown", "unknown"]
    message = str(done["message"])
    numbers = f"{lost[0].id}, {lost[1].id}"
    assert message.startswith("0 of 2 records were seen to be deleted. ")
    assert f"Not visible to you any more, so deleted or hidden: {numbers}." in message
    assert "Paperless did not confirm the deletion: " in message
    assert message.endswith(NOT_REPEATED)
    assert [await gone(backend, r.id) for r in rows] == [True, True, True]


async def test_records_that_cannot_be_read_again_are_unknown_and_nothing_more_is_asked(
    backend: Backend, mail: Mail
) -> None:
    rows = await made(backend, mail, *((subject, "1", "FAILED", None) for subject in "abc"))
    ports, limit = backend.limited, backend.rate_limit
    # Every record is read once before the deletion, so the fifth read is the second afterwards.
    ports = replace(ports, mail=before(ports.mail, "get_processed_mail", limit.begin, at=5))

    done = await output(ports, DELETE, mail_ids=[r.id for r in rows], confirm=True)

    assert outcomes(done) == ["deleted", "unknown", "unknown"]
    message = str(done["message"])
    assert message.startswith("1 of 3 records were seen to be deleted. ")
    assert f"Not known: {rows[1].id}, {rows[2].id}." in message
    assert message.endswith(NOT_REPEATED)
    assert len(set(limit.asked)) == 1, limit.asked
    assert [await gone(backend, r.id) for r in rows] == [True, True, True]


# --- why a mail was not imported


@pytest.mark.decision("decision-90")
async def test_hidden_rules_and_logs_are_told_to_a_user_who_is_not_staff(
    backend: Backend, mail: Mail
) -> None:
    account = await mail.account()
    hidden = await mail.rule(account.id)
    shown = await mail.rule(account.id, order=1)
    user = backend.restricted(
        frozenset({"view_mailaccount", "view_mailrule", "view_processedmail", "view_uisettings"})
    )
    number = await user.management.get_current_user_id()
    grants = AccessChange(permissions=ObjectPermissions(view_users=(number,)))
    writes = backend.admin.permission_writes
    _ = await writes.set_permissions("mail_account", account.id, grants)

    nothing = await output(user, "explain_mail_import", account_id=account.id)
    _ = await writes.set_permissions("mail_rule", shown.id, grants)
    found = await output(user, "explain_mail_import", account_id=account.id)
    told = await refusal(user, "explain_mail_import", account_id=account.id, rule_id=hidden.id)

    assert "You see no rule of this account" in str(nothing["message"])
    assert nothing["rules"] == []
    assert [rule["rule_id"] for rule in cast("list[Json]", found["rules"])] == [shown.id]
    for answer in (nothing, found):
        assert hidden.name not in str(answer)
        assert "Rules of other users that you may not see" in str(answer["message"])
        assert "the log mail: Logs need a staff user." in cast("list[str]", answer["not_checked"])
        assert (answer["logs"], answer["complete"]) == ([], False)
    assert f"has no rule {hidden.id} that you may see" in told
    assert hidden.name not in told


@pytest.mark.decision("decision-80")
@pytest.mark.decision("decision-90")
async def test_no_credential_is_in_an_explanation(backend: Backend, mail: Mail) -> None:
    account = await mail.account()
    rule = await mail.rule(account.id)
    backend.new_processed(rule.id, [("Invoice", "1", "FAILED", "The file is broken.")])
    number = backend.new_token_account()
    mail.accounts.append(number)
    marker = uuid.uuid4().hex
    name = f"contract-{marker[:12]}"
    values = MailAccountValues(name=name, username="user@example.com")
    _ = await backend.admin.mail_writes.update_mail_account(number, values)
    for owner, secret in ((account.name, PASSWORD), (name, TOKEN)):
        backend.log_mail_error(f"Error while authenticating account {owner}: {secret} {marker}")

    plain = await output(
        backend.admin, "explain_mail_import", account_id=account.id, subject="Invoice"
    )
    token = await output(backend.admin, "explain_mail_import", account_id=number)

    for answer, owner in ((plain, account.name), (token, name)):
        # The line is there, without the answer of the server.
        assert any(owner in line for line in cast("list[str]", answer["logs"])), answer
        for secret in (PASSWORD, TOKEN, marker):
            assert secret not in repr(answer)
    assert len(cast("list[Json]", plain["processed"])) == 1
