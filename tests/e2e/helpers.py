"""Helpers of the E2E suite: wait for the compose stack, then seed it through the API.

The stack itself is started by `just e2e-up` (tests/e2e/compose.yaml). Seeding is idempotent: every
object is looked up by its name or title first, so rerunning on a seeded stack changes nothing.
"""

import base64
import os
import subprocess
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast

import httpx2
import pytest
from fakes.accounts import (
    ADMIN,
    BLIND,
    BLIND_PERMISSIONS,
    PRIVATE_ASN,
    PRIVATE_CONTENT,
    PUBLIC_ASN,
    PUBLIC_NOTE,
    READER,
    READER_PERMISSIONS,
)

if TYPE_CHECKING:
    from collections.abc import Callable

PORT = os.environ.get("PAPERLESS_E2E_PORT", "8010")
BASE_URL = f"http://127.0.0.1:{PORT}"
API_VERSION = 10
# The release the stack runs: the one of compose.yaml, or the one `just e2e-latest` started.
EXPECTED_VERSION = os.environ.get("PAPERLESS_E2E_VERSION", "3.5.1")


READY_TIMEOUT = 300.0
CONSUME_TIMEOUT = 180.0
# Every custom field data type of 3.5.1 except `select`, which needs options (see seed()).
DATA_TYPES = (
    *("string", "url", "date", "boolean", "integer", "float", "monetary"),
    *("documentlink", "longtext"),
)
# `just e2e-latest` runs its own compose project and overlay (and sets PAPERLESS_E2E_VERSION).
_LATEST_FILES = "-f tests/e2e/compose.yaml -f tests/e2e/compose.latest.yaml"
LOGS_HINT = (
    f"docker compose -p paperless-mcp-e2e-latest {_LATEST_FILES} logs paperless"
    if "PAPERLESS_E2E_VERSION" in os.environ
    else "docker compose -f tests/e2e/compose.yaml logs paperless"
)

type Json = dict[str, object]


def parse(response: httpx2.Response) -> object:
    """The response's JSON body; the one place untyped JSON enters, callers narrow it."""
    return response.json()  # pyright: ignore[reportAny] -- narrowed by obj/seq/num below


def obj(value: object) -> Json:
    """Narrow a JSON value to an object."""
    assert isinstance(value, dict)
    return cast("Json", value)


def seq(value: object) -> list[object]:
    """Narrow a JSON value to an array."""
    assert isinstance(value, list)
    return cast("list[object]", value)


def num(value: object) -> int:
    """Narrow a JSON value to an integer."""
    assert isinstance(value, int)
    return value


def results(response: httpx2.Response) -> list[Json]:
    """The `results` of a paged list response."""
    return [obj(item) for item in seq(obj(parse(response))["results"])]


@dataclass(frozen=True)
class Seeded:
    """Ids of the seeded objects, by role."""

    parent_tag: int
    child_tag: int
    inbox_tag: int
    private_tag: int
    correspondent: int
    document_type: int
    storage_path: int
    custom_fields: dict[str, int]  # data_type -> field id
    public_document: int  # visible to the restricted user
    private_document: int  # owned by admin, invisible to the restricted user


@dataclass(frozen=True)
class Stack:
    """The running, seeded Paperless instance."""

    base_url: str
    admin: httpx2.Client
    reader: httpx2.Client
    seeded: Seeded
    # Fetched once: Paperless throttles /api/token/.
    admin_token: str
    reader_token: str
    blind_token: str


def pdf(text: str) -> bytes:
    """Build a one-page PDF with `text` as its text layer (no binary fixtures in the repo)."""
    return pdf_pages([text])


def pdf_pages(texts: list[str]) -> bytes:
    """Build a PDF with one page per text, each text being the page's text layer."""
    font = 3 + 2 * len(texts)
    kids = " ".join(f"{3 + 2 * n} 0 R" for n in range(len(texts)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(texts)} >>".encode(),
    ]
    for number, text in enumerate(texts):
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        page = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * number} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>"
        )
        objects.append(page.encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return out


# A one-page PDF encrypted with the user password below (RC4, made once with pikepdf): Paperless
# consumes it without text and without a page count, and remove_password unlocks it.
ENCRYPTED_PASSWORD = "s3cret"
_ENCRYPTED_BASE64 = (
    "JVBERi0xLjUKJb/3ov4KMSAwIG9iago8PCAvUGFnZXMgMiAwIFIgL1R5cGUgL0NhdGFsb2cgPj4KZW5kb2Jq"
    "CjIgMCBvYmoKPDwgL0NvdW50IDEgL0tpZHMgWyAzIDAgUiBdIC9UeXBlIC9QYWdlcyA+PgplbmRvYmoKMyAw"
    "IG9iago8PCAvQ29udGVudHMgNCAwIFIgL01lZGlhQm94IFsgMCAwIDYxMiA3OTIgXSAvUGFyZW50IDIgMCBS"
    "IC9SZXNvdXJjZXMgPDwgL0ZvbnQgPDwgL0YxIDUgMCBSID4+ID4+IC9UeXBlIC9QYWdlID4+CmVuZG9iago0"
    "IDAgb2JqCjw8IC9MZW5ndGggNTIgL0ZpbHRlciAvRmxhdGVEZWNvZGUgPj4Kc3RyZWFtCpPKZdOxUKR8MGzb"
    "q86EM2b+cpBF9ExMMDss0C3NUr3nIhfACHZyPJIRxsEITBF0DJYTtJYKZW5kc3RyZWFtCmVuZG9iago1IDAg"
    "b2JqCjw8IC9CYXNlRm9udCAvSGVsdmV0aWNhIC9TdWJ0eXBlIC9UeXBlMSAvVHlwZSAvRm9udCA+PgplbmRv"
    "YmoKNiAwIG9iago8PCAvQ0YgPDwgL1N0ZENGIDw8IC9BdXRoRXZlbnQgL0RvY09wZW4gL0NGTSAvVjIgL0xl"
    "bmd0aCAxNiA+PiA+PiAvRW5jcnlwdE1ldGFkYXRhIGZhbHNlIC9GaWx0ZXIgL1N0YW5kYXJkIC9MZW5ndGgg"
    "MTI4IC9PIDwwZGUzODU1ZmM1MzI2NTY5ZTc2NTkwNmNhZjY0ZTQ0MjlhNGMyMGQ2ZTk5NmZkZWY5NjNlOWI1"
    "MDgwZjllMDgzPiAvT0UgPD4gL1AgLTEwMjggL1IgNCAvU3RtRiAvU3RkQ0YgL1N0ckYgL1N0ZENGIC9VIDw3"
    "ZDJhMGM2MGZjODEwYzBkZTY1ZDVhZTRlZDUzNmQwNjAwMjE0NDY5OTBiOWU0MTE0MDcxYTRkOTEwNDk4NGMx"
    "PiAvVUUgPD4gL1YgNCA+PgplbmRvYmoKeHJlZgowIDcKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDE1"
    "IDAwMDAwIG4gCjAwMDAwMDAwNjQgMDAwMDAgbiAKMDAwMDAwMDEyMyAwMDAwMCBuIAowMDAwMDAwMjUxIDAw"
    "MDAwIG4gCjAwMDAwMDAzNzQgMDAwMDAgbiAKMDAwMDAwMDQ0NCAwMDAwMCBuIAp0cmFpbGVyIDw8IC9Sb290"
    "IDEgMCBSIC9TaXplIDcgL0lEIFs8MGVjMGQ0NjQ2MTdkZWIxYmUyYTI1YmU2MGY4ZjAyOTU+PDBlYzBkNDY0"
    "NjE3ZGViMWJlMmEyNWJlNjBmOGYwMjk1Pl0gL0VuY3J5cHQgNiAwIFIgPj4Kc3RhcnR4cmVmCjc4MAolJUVP"
    "Rgo="
)
ENCRYPTED_PDF = base64.b64decode(_ENCRYPTED_BASE64)

# Two pages that open without a password, encrypted with an owner password (AES-256, no printing;
# made once with qpdf and compressed with zlib).
_RESTRICTED_BASE64 = (
    "eNq9VEtoXFUY1oUgQ2npRhduzsIUEdI574cMoU0zQWlL4sREShE5j/+kEyYz4c5NmpaKpUIRi1hooFRxU1Tw1YUb"
    "60IrKO600AqlWkoFES0WdNMS0eK5N6+RKQguvPdyzzn/4/v/7z//OQPjI6ODZLuqDFy4c/ZuhSCMOm6mUquhan0x"
    "h3a32Wl3UbHcOTJcLyfDtgtTkBUaVE2uPZZ7YAFaSKOhoeKrjttp6CKaMBuo+syhOUDVXTa3rc50UlegHYpQtCfk"
    "rs58O08O1d3N0EX7EStdefl/bg1iBXUDgP0DoJ1SybtIrATdC6FphzuLCQunVxKKlKEF1rjNoIxV2jWg25nPPKxQ"
    "HU0oKxOCZGmwSmgjgZ74/F7x1f8XX/TE3wPt6fwA0jj5Nls5ZGls2RxGwHdC6dTNM7CzlcHj2879/PLVxhfXvzpx"
    "+ZEjcz99t3WmvS17/we8fcux1+tXZr69yXa8++vXX356emH5o+OvPP/Jlu8/PPPYg5effm9satOJ2quP7jj18c3B"
    "ly6dunLrHD/bSLmsIq9mJXuyKjqm5FR9EloLkDe9RdWJeZeXdApSZI1babbBTf0Xbvy1hy4unf/94L4bd8+YN/6g"
    "S0eP7r76QOW+g/TtGo7v3Pn8idvnb29efvHw+MVnP5i6/60Lx3584eFNvyyf/OsbfOvxa39OLm02+s2TN478xrd+"
    "Rk/v6+Ome3d8tNypiTysznbO5wfqC8XeVkc6fmwO2oXR3qSoT0yxdRqMrm3qGpmJ3LaDzcK6CRUSVcdQjQTrpPOA"
    "MQEFUdCoAhXKKcUlcEJFNMph7iwHIcHIgBUED5Sp9DkuXfQqOoFBBau84ZRHQ72QItggTBQph7F0tB2YqIGQII2R"
    "OhosOWXUO8CCugAKc2UkgxioVN5rYcDoKH0JSoXRxYFHgxQXWY9DNpt6OToeozZBiGQvpYDgFFaRUAYGrA/JpZE6"
    "PFGfHV2rYRqyjcUkqnFvMMcOXBScKkMCNppGVuQkbFI6rUgkjhCLlbeYBpM4eUMcS4w0pzEoicEzzlRw1BjmYrRR"
    "M61DoET7lMRkoi8l1UITbGgqFE+15kRYGsE7ncrqwWlHmKNWUUGZCdgba6iBaAKTNHKcYKbStbPRu4sZxApGpoLX"
    "HySFYAJFtC4j6fSWmva6jDDcLzP9doyyPhkXpE8m8T1kql+maY8sz2yzlbqy6OhGp5MjsnJRTTQPAzKo+tQI2l+L"
    "DBOplHdWa+KdV4IQZxVmUYkocRj6d4t0E9bbPjs0lyO9etOlY2yzvKweYYpUBgbqY6OVvwHu9PXb"
)
RESTRICTED_PDF = zlib.decompress(base64.b64decode(_RESTRICTED_BASE64))


def client(token: str) -> httpx2.Client:
    """An API client pinned to API version 10, authenticated with `token`."""
    return httpx2.Client(
        base_url=BASE_URL,
        headers={
            "Accept": f"application/json; version={API_VERSION}",
            "Authorization": f"Token {token}",
        },
        timeout=30.0,
    )


def token_for(username: str, password: str) -> str:
    """Return the user's API token (Paperless returns the same one on every call)."""
    response = httpx2.post(
        f"{BASE_URL}/api/token/",
        headers={"Accept": f"application/json; version={API_VERSION}"},
        data={"username": username, "password": password},
        timeout=30.0,
    )
    _ = response.raise_for_status()
    return str(obj(parse(response))["token"])


def wait_ready() -> str:
    """Poll until the API answers authenticated requests; return the admin token."""
    deadline = time.monotonic() + READY_TIMEOUT
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with client(token_for(*ADMIN)) as probe:
                response = probe.get("/api/tags/")
            if response.is_success:
                return str(response.request.headers["Authorization"]).removeprefix("Token ")
            last = f"HTTP {response.status_code}"
        except (httpx2.HTTPError, KeyError) as error:
            last = repr(error)
        time.sleep(2)
    message = f"Paperless not ready at {BASE_URL} after {READY_TIMEOUT:.0f}s ({last})."
    pytest.fail(f"{message} Is the stack up (`just e2e-up`)? Container logs: `{LOGS_HINT}`")


def get_or_create(
    api: httpx2.Client, path: str, key: str, body: Json, *, reconcile: Json | None = None
) -> int:
    """Return the id of the object whose `key` equals body[key], creating it if absent.

    `reconcile` fields are also PATCHed onto an existing object, so a reused stack seeded by
    older code converges to the current seed.
    """
    found = results(api.get(path, params={f"{key}__iexact": str(body[key])}).raise_for_status())
    if not found:
        return num(obj(parse(api.post(path, json=body).raise_for_status()))["id"])
    object_id = num(found[0]["id"])
    if reconcile:
        _ = api.patch(f"{path}{object_id}/", json=reconcile).raise_for_status()
    return object_id


def ensure_document(api: httpx2.Client, title: str, text: str) -> int:
    """Upload a generated PDF titled `title` unless it exists; wait for its consumption."""
    found = results(api.get("/api/documents/", params={"title__iexact": title}).raise_for_status())
    if found:
        return num(found[0]["id"])
    uploaded = api.post(
        "/api/documents/post_document/",
        data={"title": title},
        files={"document": (f"{title}.pdf", pdf(text), "application/pdf")},
    ).raise_for_status()
    task_id = str(parse(uploaded))
    deadline = time.monotonic() + CONSUME_TIMEOUT
    while time.monotonic() < deadline:
        tasks = results(api.get("/api/tasks/", params={"task_id": task_id}).raise_for_status())
        task = tasks[0] if tasks else None
        if task and task["status"] == "success":
            return num(seq(task["related_document_ids"])[0])
        if task and task["status"] in {"failure", "revoked"}:
            pytest.fail(f"Consuming {title!r} failed: {task}. Container logs: `{LOGS_HINT}`")
        time.sleep(2)
    pytest.fail(f"Consuming {title!r} timed out after {CONSUME_TIMEOUT:.0f}s. Logs: `{LOGS_HINT}`")


def consume(
    api: httpx2.Client,
    title: str,
    content: bytes,
    *,
    filename: str | None = None,
    mime_type: str = "application/pdf",
) -> tuple[str, int | None]:
    """Upload `content` as `title`, wait until its task ends, and return the task id.

    The file is named `filename` (default: the title with ".pdf") and sent as `mime_type`.

    Also returns the id of the document the task created, None if it failed. The caller deletes
    that document again (`api.delete`), also when its test fails.
    """
    uploaded = api.post(
        "/api/documents/post_document/",
        data={"title": title},
        files={"document": (filename or f"{title}.pdf", content, mime_type)},
    ).raise_for_status()
    task_id = str(parse(uploaded))
    return task_id, settled(api, task_id, title)


def settled(api: httpx2.Client, task_id: str, title: str) -> int | None:
    """Wait until the task that consumes `title` ended; what it made, None if it failed."""
    deadline = time.monotonic() + CONSUME_TIMEOUT
    while time.monotonic() < deadline:
        tasks = results(api.get("/api/tasks/", params={"task_id": task_id}).raise_for_status())
        if tasks and tasks[0]["status"] in {"success", "failure", "revoked"}:
            related = seq(tasks[0]["related_document_ids"])
            return num(related[0]) if related else None
        time.sleep(1)
    pytest.fail(f"Consuming {title!r} timed out after {CONSUME_TIMEOUT:.0f}s. Logs: `{LOGS_HINT}`")


# The seed's manifest per instance statistic: (objects every user sees, objects only the admin
# sees). `fakes.paperless.seeded` matches it exactly; on the persistent live stack other tests may
# add objects, so the contract suite only relies on these as lower bounds.
SEEDED_OBJECTS: dict[str, tuple[int, int]] = {
    "documents": (1, 1),
    "inbox_documents": (1, 0),
    "tags": (3, 1),
    "correspondents": (1, 0),
    "document_types": (1, 0),
    "storage_paths": (1, 0),
}


def seed(admin: httpx2.Client) -> Seeded:
    """Create the fixture data; safe to run any number of times.

    Paperless makes the creator the owner of a new object, which hides it from other users. The
    shared taxonomy is created with `owner: None` so the restricted user sees it; `e2e-private`
    keeps the admin as owner and stays hidden from that user.
    """
    shared: Json = {"owner": None}
    parent = get_or_create(
        admin, "/api/tags/", "name", {"name": "e2e-finance", **shared}, reconcile=shared
    )
    child = get_or_create(
        admin,
        "/api/tags/",
        "name",
        {"name": "e2e-invoices", "parent": parent, **shared},
        reconcile=shared,
    )
    inbox = get_or_create(
        admin,
        "/api/tags/",
        "name",
        {"name": "e2e-inbox", "is_inbox_tag": True, **shared},
        reconcile={"is_inbox_tag": True, **shared},
    )
    private_tag = get_or_create(admin, "/api/tags/", "name", {"name": "e2e-private"})
    correspondent = get_or_create(
        admin, "/api/correspondents/", "name", {"name": "e2e-acme", **shared}, reconcile=shared
    )
    document_type = get_or_create(
        admin, "/api/document_types/", "name", {"name": "e2e-invoice", **shared}, reconcile=shared
    )
    storage_path = get_or_create(
        admin,
        "/api/storage_paths/",
        "name",
        {"name": "e2e-archive", "path": "e2e/{{ created_year }}/{{ title }}", **shared},
        reconcile=shared,
    )
    options = [{"label": "alpha"}, {"label": "beta"}]
    field_bodies: dict[str, Json] = {
        kind: {"name": f"e2e-{kind}", "data_type": kind}
        for kind in (
            "string",
            "url",
            "date",
            "boolean",
            "integer",
            "float",
            "monetary",
            "documentlink",
            "longtext",
        )
    }
    field_bodies["select"] = {
        "name": "e2e-select",
        "data_type": "select",
        "extra_data": {"select_options": options},
    }
    fields = {
        kind: get_or_create(admin, "/api/custom_fields/", "name", body)
        for kind, body in field_bodies.items()
    }

    public = ensure_document(admin, "e2e-public-invoice", "E2E public invoice ACME 2026")
    private = ensure_document(admin, "e2e-private-note", PRIVATE_CONTENT)
    select_field = obj(parse(admin.get(f"/api/custom_fields/{fields['select']}/")))
    option_id = obj(seq(obj(select_field["extra_data"])["select_options"])[0])["id"]
    values: Json = {
        "string": "hello",
        "url": "https://example.com/e2e",
        "date": "2026-01-15",
        "boolean": True,
        "integer": 42,
        "float": 3.25,
        "monetary": "EUR12.50",
        "documentlink": [private],
        "select": option_id,
        "longtext": "line one\nline two",
    }
    _ = admin.patch(
        f"/api/documents/{public}/",
        json={
            "owner": None,  # unowned: visible to every user with the model permission
            "tags": [parent, child, inbox],
            "correspondent": correspondent,
            "document_type": document_type,
            "storage_path": storage_path,
            "archive_serial_number": PUBLIC_ASN,
            "custom_fields": [{"field": fields[kind], "value": v} for kind, v in values.items()],
        },
    ).raise_for_status()
    notes = seq(parse(admin.get(f"/api/documents/{public}/notes/").raise_for_status()))
    if not any(obj(note)["note"] == PUBLIC_NOTE for note in notes):
        _ = admin.post(
            f"/api/documents/{public}/notes/", json={"note": PUBLIC_NOTE}
        ).raise_for_status()
    admin_id = num(
        results(admin.get("/api/users/", params={"username__iexact": ADMIN[0]}))[0]["id"]
    )
    _ = admin.patch(
        f"/api/documents/{private}/",
        json={
            "owner": admin_id,
            "content": PRIVATE_CONTENT,  # reconciles a stack seeded with the old text
            "archive_serial_number": PRIVATE_ASN,
            "tags": [private_tag],
            # The mirror of the public document's link; reconciles back-links to documents
            # that tests linked it to and that are deleted since.
            "custom_fields": [{"field": fields["documentlink"], "value": [public]}],
            "set_permissions": {
                "view": {"users": [], "groups": []},
                "change": {"users": [], "groups": []},
            },
        },
    ).raise_for_status()
    _ = get_or_create(admin, "/api/groups/", "name", {"name": "e2e-group", "permissions": []})
    # /api/saved_views/ has no name filter, so the lookup is by hand. Owned by the admin.
    views = results(admin.get("/api/saved_views/", params={"page_size": 100}).raise_for_status())
    if not any(view["name"] == "e2e-view" for view in views):
        rules = [{"rule_type": 6, "value": str(parent)}]  # 6: has tag
        _ = admin.post(
            "/api/saved_views/", json={"name": "e2e-view", "filter_rules": rules}
        ).raise_for_status()
    _ = get_or_create(
        admin,
        "/api/users/",
        "username",
        {
            "username": READER[0],
            "password": READER[1],
            "user_permissions": READER_PERMISSIONS,
        },
        reconcile={"user_permissions": READER_PERMISSIONS},
    )
    _ = get_or_create(
        admin,
        "/api/users/",
        "username",
        {"username": BLIND[0], "password": BLIND[1], "user_permissions": BLIND_PERMISSIONS},
        reconcile={"user_permissions": BLIND_PERMISSIONS},
    )
    return Seeded(
        parent_tag=parent,
        child_tag=child,
        inbox_tag=inbox,
        private_tag=private_tag,
        correspondent=correspondent,
        document_type=document_type,
        storage_path=storage_path,
        custom_fields=fields,
        public_document=public,
        private_document=private,
    )


def consume_text(api: httpx2.Client, title: str) -> int:
    """Upload a plain text document titled `title` and return its id once it is consumed.

    A text file skips OCR: about 1 s on the E2E stack where a PDF takes about 6 s (and several
    PDFs at once are slower than one after the other). For tests that need a document, not a file.
    """
    body = f"{title} plain text for a test".encode()
    _, document = consume(api, title, body, filename=f"{title}.txt", mime_type="text/plain")
    assert document is not None, f"Consuming {title!r} failed"
    return document


# Tasks that write the full-text index in the background after the request that started them.
INDEX_WRITING_TASKS = (
    "bulk_update",
    "bulk_delete",
    "check_workflows",
    "consume_file",
    "reprocess_document",
)
# A reindex of the E2E corpus takes seconds; fail fast instead of stalling the job.
REINDEX_TIMEOUT = 300.0
INDEX_SETTLE_TIMEOUT = 120.0
# Only recent tasks count: a long-lived stack can hold one that was killed and never ended.
RECENT_TASKS = timedelta(minutes=15)


def wait_for_index_writers(api: httpx2.Client) -> None:
    """Wait until no recent task that writes the search index is still queued or running."""
    since = (datetime.now(UTC) - RECENT_TASKS).isoformat()
    deadline = time.monotonic() + INDEX_SETTLE_TIMEOUT
    while True:
        running = [
            task
            for kind in INDEX_WRITING_TASKS
            for task in results(
                api.get(
                    "/api/tasks/",
                    params={"task_type": kind, "is_complete": "false", "date_created_after": since},
                ).raise_for_status()
            )
        ]
        if not running:
            return
        if time.monotonic() > deadline:
            pytest.fail(f"Index writing tasks still running after {INDEX_SETTLE_TIMEOUT:.0f}s.")
        time.sleep(1)


# Tantivy's reader polls the index every 0.5 s for a commit of another process.
INDEX_RELOAD = 1.0
# A word of the seeded public document, so a search for it answers as soon as it is indexed.
INDEXED_WORD = "ACME"


def wait_for_index_reload(
    api: httpx2.Client,
    *,
    pause: Callable[[float], None] = time.sleep,
    timeout: float = INDEX_SETTLE_TIMEOUT,
) -> None:
    """Wait until the web process searches the index another process has just rebuilt.

    Why: `document_index reindex` wipes the index before it fills it, and the web process notices
    both only when its reader polls. Until then it searches an empty index, in which a
    more-like-this query does not find its own document and answers an empty list.
    """
    deadline = time.monotonic() + timeout
    while True:
        pause(INDEX_RELOAD)
        found = obj(
            parse(api.get("/api/documents/", params={"query": INDEXED_WORD}).raise_for_status())
        )
        if found["count"]:
            return
        if time.monotonic() > deadline:
            pytest.fail(f"The rebuilt index is still empty after {timeout:.0f}s.")


def rebuild_search_index(api: httpx2.Client) -> None:
    """Rebuild Paperless's full-text index from the database, as `document_index reindex` does.

    Why: Tantivy keeps replaced and deleted documents in their segments until a merge, and counts
    them in a term's document frequency. A more-like-this query whose terms occur more often than
    there are live documents makes Tantivy panic, and Paperless 3.5.1 then answers an empty list
    (`more_like_this_ids` logs "More Like This scoring panicked"). Every update or deletion of a
    document leaves such an entry, so any test that runs after other writes (a contract suite, a
    long-lived stack) can hit it; a rebuild leaves exactly one entry per document. Paperless has no
    API for it, so this runs the management command in the container that publishes `PORT`.
    Drop this rebuild (and its callers) once the pinned Paperless ships a Tantivy with the fix
    (upstream tantivy PR 2964); until then the limitation stays documented in the README and in
    the `more_like_id` description.
    Wait for the index-writing tasks first: one that ends after the rebuild leaves new entries.
    """
    wait_for_index_writers(api)
    in_container("python", "manage.py", "document_index", "reindex", "--no-progress-bar")
    wait_for_index_reload(api)


type FilePart = Literal["original", "archive", "thumbnail", "names"]

MEDIA = "/usr/src/paperless/media/documents"


def lose_file(api: httpx2.Client, document: int, part: FilePart) -> None:
    """Take a file of a document with one version away from Paperless, or its file names.

    Without its name the file stays where it is and is found: the stack sets no file name format,
    so the name Paperless falls back to is the one the file has.

    Paperless has no API for it: files get lost on the disk, names are null in old databases.
    """
    if part == "names":
        update = f"Document.objects.filter(pk={document}).update"
        code = f"from documents.models import Document; {update}(filename=None"
        in_container("python", "manage.py", "shell", "-c", f"{code}, original_filename=None)")
        return
    reply = api.get(f"/api/documents/{document}/metadata/").raise_for_status()
    facts = cast("dict[str, object]", reply.json())
    path = {
        "original": f"originals/{facts['media_filename']}",
        "archive": f"archive/{facts['archive_media_filename']}",
        "thumbnail": f"thumbnails/{document:07}.webp",
    }[part]
    in_container("rm", "--", f"{MEDIA}/{path}")


def in_container(*command: str) -> None:
    """Run a command in the Paperless container that publishes `PORT`."""
    container = subprocess.run(  # noqa: S603 -- fixed docker arguments, no user input
        ["docker", "ps", "--quiet", "--filter", f"publish={PORT}"],  # noqa: S607 -- fixed argument list
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert container.returncode == 0, f"docker ps failed (is docker reachable?): {container.stderr}"
    ids = container.stdout.split()
    assert len(ids) == 1, f"Expected one container publishing port {PORT}, got {ids}"
    ran = subprocess.run(  # noqa: S603 -- arguments of the tests
        [
            *("docker", "exec", "--user", "paperless", "--workdir", "/usr/src/paperless/src"),
            ids[0],
            *command,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=REINDEX_TIMEOUT,
    )
    assert ran.returncode == 0, f"{command} failed: {ran.stderr[-500:]}"
