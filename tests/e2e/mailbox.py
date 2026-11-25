"""A mail server for the live mail tests: mailboxes, Paperless accounts and fetches, cleaned up."""

import imaplib
import os
import time
import uuid
from email.message import EmailMessage
from typing import TYPE_CHECKING, Final, cast, final

import pytest
from contract.conftest import delete_and_empty_trash

from e2e.helpers import num, obj, parse, results

if TYPE_CHECKING:
    from collections.abc import Callable

    from e2e.helpers import Json, Stack

PASSWORD: Final = "e2e-imap-Passw0rd-4711"
PORT: Final = int(os.environ.get("PAPERLESS_E2E_IMAP_PORT", "8018"))
# Where Paperless reaches the same server.
HOST, HOST_PORT = "imap", 31143
# The actions of rules on the wire.
ACTIONS: Final = {"delete": 1, "move": 2, "mark_read": 3, "flag": 4, "tag": 5}
TIMEOUT: Final = 120.0
ENDED: Final = frozenset({"success", "failure", "revoked"})
PREFIX: Final = "e2e-imap-"
BROKEN: Final = b"%PDF-1.4\nthis is not a PDF\n"


def wait[T](what: str, look: Callable[[], T | None]) -> T:
    """The first result of `look` that is not None."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        found = look()
        if found is not None:
            return found
        time.sleep(0.5)
    pytest.fail(f"Timed out waiting for {what}")


def mail(subject: str, files: dict[str, bytes] | None = None, *, age: int = 0) -> bytes:
    message = EmailMessage()
    message["From"] = "Sender <sender@example.org>"
    message["To"] = "me@example.org"
    message["Subject"] = subject
    message["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(time.time() - age))
    message["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
    message.set_content("The body of the mail.")
    for name, content in (files or {}).items():
        message.add_attachment(
            content, maintype="application", subtype="octet-stream", filename=name
        )
    return message.as_bytes()


def note() -> bytes:
    """A text file no other test has."""
    return f"Mail attachment {uuid.uuid4().hex}\n".encode()


@final
class Mailbox:
    """A mailbox of its own with an account of the admin in Paperless that reads it."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.name: str = f"{PREFIX}{uuid.uuid4().hex[:12]}"
        self.imap: imaplib.IMAP4 = wait("the mail server", self._login)
        body = {
            "name": self.name,
            "imap_server": HOST,
            "imap_port": HOST_PORT,
            "imap_security": 1,
            "username": self.name,
            "password": PASSWORD,
            "character_set": "UTF-8",
        }
        made = stack.admin.post("/api/mail_accounts/", json=body).raise_for_status()
        self.account: int = num(obj(parse(made))["id"])
        self.rules: list[int] = []

    def _login(self) -> imaplib.IMAP4 | None:
        try:
            client = imaplib.IMAP4("127.0.0.1", PORT, timeout=10)
            _ = client.login(self.name, PASSWORD)
        except OSError, imaplib.IMAP4.error:
            return None
        return client

    def close(self) -> None:
        """Delete the rules, the account and the documents made of the mails."""
        for rule in self.rules:
            _ = self.stack.admin.delete(f"/api/mail_rules/{rule}/")
        _ = self.stack.admin.delete(f"/api/mail_accounts/{self.account}/")
        delete_and_empty_trash(self.stack, self.documents(""))
        _ = self.imap.logout()

    # --- the mail server

    def put(self, subject: str, files: dict[str, bytes] | None = None, *, age: int = 0) -> str:
        """Append an unread mail, sent `age` seconds ago, to the inbox; its uid."""
        content = mail(f"{self.name} {subject}", files, age=age)
        status, said = self.imap.append("INBOX", "", "", content)
        assert status == "OK", said
        return self.uids()[-1]

    def _select(self, folder: str) -> None:
        status, said = self.imap.select(folder)
        assert status == "OK", said

    def uids(self, folder: str = "INBOX") -> list[str]:
        self._select(folder)
        _, found = self.imap.uid("SEARCH", "ALL")
        return cast("bytes", found[0] or b"").decode().split()

    def flags(self, uid: str, folder: str = "INBOX") -> str:
        self._select(folder)
        _, found = self.imap.uid("FETCH", uid, "(FLAGS)")
        return repr(cast("object", found[0]))

    def unflag(self, uid: str, flag: str) -> None:
        self._select("INBOX")
        status, said = self.imap.uid("STORE", uid, "-FLAGS", f"({flag})")
        assert status == "OK", said

    def move(self, uid: str, source: str, target: str) -> None:
        self._select(source)
        status, said = self.imap.uid("MOVE", uid, target)
        assert status == "OK", said

    # --- Paperless

    def rule(self, action: str = "mark_read", **fields: object) -> int:
        body: Json = {
            "name": f"{self.name}-{len(self.rules)}",
            "account": self.account,
            "enabled": True,
            "action": ACTIONS[action],
            **fields,
        }
        made = self.stack.admin.post("/api/mail_rules/", json=body)
        assert made.is_success, made.text
        self.rules.append(num(obj(parse(made))["id"]))
        return self.rules[-1]

    def change_account(self, **fields: object) -> None:
        changed = self.stack.admin.patch(f"/api/mail_accounts/{self.account}/", json=fields)
        assert changed.is_success, changed.text

    def change_rule(self, rule: int, **fields: object) -> None:
        changed = self.stack.admin.patch(f"/api/mail_rules/{rule}/", json=fields)
        assert changed.is_success, changed.text

    def tasks(self, task_type: str) -> dict[int, Json]:
        params = {"task_type": task_type, "page_size": 100}
        rows = results(self.stack.admin.get("/api/tasks/", params=params).raise_for_status())
        return {num(row["id"]): row for row in rows}

    def fetch(self) -> int:
        """Fetch the mail of the account now and wait for the end; how many files it queued.

        The task itself says nothing: 3.5.1 gives `result_data` null for mail_fetch.
        """
        fetches, files = set(self.tasks("mail_fetch")), set(self.tasks("consume_file"))
        reply = self.stack.admin.post(f"/api/mail_accounts/{self.account}/process/")
        assert reply.is_success, reply.text

        def look() -> bool | None:
            rows = self.tasks("mail_fetch").items()
            return any(n not in fetches and row["status"] in ENDED for n, row in rows) or None

        assert wait("the fetch", look)
        return len(set(self.tasks("consume_file")) - files)

    def log(self, name: str) -> list[str]:
        return cast("list[str]", parse(self.stack.admin.get(f"/api/logs/{name}/")))

    def records(self, rule: int, count: int | None) -> list[Json]:
        """The records of the rule, newest first, once there are `count` and nothing is running.

        Without a count: at least one, once nothing was running in five looks in a row.
        """
        quiet = 0

        def look() -> list[Json] | None:
            params = {"rule": rule, "ordering": "-processed", "page_size": 100}
            rows = results(self.stack.admin.get("/api/processed_mail/", params=params))
            active = parse(self.stack.admin.get("/api/tasks/active/"))
            running = obj(cast("object", active))["results"] if isinstance(active, dict) else active
            nonlocal quiet
            quiet = 0 if running else quiet + 1
            enough = len(rows) == count if count is not None else bool(rows) and quiet > 4
            return rows if enough and not running else None

        return wait(f"{count} records", look)

    def forget(self, *records: int) -> None:
        """Delete records past the server, as the web UI does."""
        reply = self.stack.admin.post(
            "/api/processed_mail/bulk_delete/", json={"mail_ids": list(records)}
        )
        assert reply.is_success, reply.text

    def documents(self, subject: str) -> list[int]:
        """The documents made of the mails with this subject (an empty one: of all)."""
        params = {"title__icontains": f"{self.name} {subject}".strip(), "page_size": 100}
        found = results(self.stack.admin.get("/api/documents/", params=params))
        return [num(row["id"]) for row in found]
