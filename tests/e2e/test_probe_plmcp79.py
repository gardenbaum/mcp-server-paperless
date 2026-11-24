"""Temporary probe of PLMCP-79 (design section 11): prints facts of 3.5.1, asserts nothing.

Deleted once its facts are contract tests. Every line it prints starts with `PROBE79`.
"""

import io
import json
import os
import time
import traceback
import uuid
from typing import TYPE_CHECKING

import httpx2
import pytest
from contract.conftest import delete_and_empty_trash, live_in_trash
from PIL import Image

from e2e.helpers import (
    ENCRYPTED_PDF,
    client,
    consume,
    consume_text,
    num,
    obj,
    parse,
    pdf_pages,
    results,
    seq,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from e2e.helpers import Json, Stack

pytestmark = [pytest.mark.e2e]
DUPLICATES = os.environ.get("PAPERLESS_E2E_DUPLICATES") == "1"
normal = pytest.mark.skipif(DUPLICATES, reason="probes the plain stack")

WAIT = 120.0
SHOWN = (
    *("id", "title", "owner", "page_count", "mime_type", "archive_serial_number", "created"),
    *("correspondent", "document_type", "storage_path", "tags", "custom_fields", "permissions"),
    *("archived_file_name", "original_file_name"),
)
EDITING = ("view_document", "change_document", "add_document")


class Probe:
    """One probe's documents and users, and the output of what it finds."""

    def __init__(self, stack: Stack, capfd: pytest.CaptureFixture[str]) -> None:
        self.stack: Stack = stack
        self.admin: httpx2.Client = stack.admin
        self.capfd: pytest.CaptureFixture[str] = capfd
        self.users: list[int] = []
        self.paths: list[str] = []
        self.known: set[int] = self.all_documents()
        self.me: int = num(obj(obj(parse(self.admin.get("/api/ui_settings/")))["user"])["id"])

    def all_documents(self) -> set[int]:
        found: set[int] = set()
        page = 1
        while True:
            answer = self.admin.get(
                "/api/documents/", params={"page": page, "page_size": 100, "fields": "id"}
            )
            found |= {num(row["id"]) for row in results(answer)}
            if not obj(parse(answer))["next"]:
                return found
            page += 1

    def say(self, what: str, value: object = "") -> None:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        with self.capfd.disabled():
            print(f"PROBE79 {what}: {text[:1500]}", flush=True)  # noqa: T201 -- the probe's output

    def clean(self) -> None:
        delete_and_empty_trash(self.stack, sorted(self.all_documents() - self.known))
        trash = {num(r["id"]) for r in results(self.admin.get("/api/trash/?page_size=100"))}
        mine = sorted(n for n in trash if n > max(self.known, default=0))
        if mine:
            _ = self.admin.post("/api/trash/", json={"action": "empty", "documents": mine})
        for user in self.users:
            _ = self.admin.delete(f"/api/users/{user}/")
        for path in self.paths:
            _ = self.admin.delete(path)

    def pdf(self, pages: int, mark: str = "") -> int:
        title = f"probe79-{mark}{uuid.uuid4().hex[:8]}"
        texts = [f"{title} page {n}" for n in range(1, pages + 1)]
        _, made = consume(self.admin, title, pdf_pages(texts))
        assert made is not None
        return made

    def version(self, document: int, pages: int) -> int:
        name = f"probe79-v-{uuid.uuid4().hex[:8]}"
        content = pdf_pages([f"{name} version page {n}" for n in range(1, pages + 1)])
        answer = self.admin.post(
            f"/api/documents/{document}/update_version/",
            files={"document": (f"{name}.pdf", content, "application/pdf")},
        ).raise_for_status()
        task = self.finish(str(parse(answer)))
        return num(seq(obj(task)["related_document_ids"])[0])

    def finish(self, task_id: str) -> Json:
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            rows = results(self.admin.get("/api/tasks/", params={"task_id": task_id}))
            if rows and rows[0]["status"] in {"success", "failure", "revoked"}:
                return rows[0]
            time.sleep(0.3)
        return {"status": "TIMEOUT", "related_document_ids": []}

    def newest(self) -> int:
        rows = results(
            self.admin.get("/api/tasks/", params={"ordering": "-date_created", "page_size": 1})
        )
        return num(rows[0]["id"]) if rows else 0

    def user(self, *permissions: str, superuser: bool = False) -> tuple[int, httpx2.Client]:
        name, password = f"probe79-{uuid.uuid4().hex[:8]}", f"pw-{uuid.uuid4().hex}"
        body = {
            "username": name,
            "password": password,
            "user_permissions": list(permissions),
            "is_superuser": superuser,
        }
        made = self.admin.post("/api/users/", json=body).raise_for_status()
        number = num(obj(parse(made))["id"])
        self.users.append(number)
        with httpx2.Client(
            base_url=self.stack.base_url,
            auth=(name, password),
            headers={"Accept": "application/json; version=10"},
        ) as signed_in:
            issued = signed_in.post("/api/profile/generate_auth_token/").raise_for_status()
        return number, client(str(parse(issued)))

    def patch(self, document: int, **body: object) -> httpx2.Response:
        return self.admin.patch(f"/api/documents/{document}/", json=body)

    def show(self, what: str, document: int, **params: str | int) -> Json:
        answer = self.admin.get(
            f"/api/documents/{document}/", params={"full_perms": "true", **params}
        )
        if not answer.is_success:
            self.say(what, f"document {document}: {answer.status_code}")
            return {}
        row = obj(parse(answer))
        shown: Json = {key: row.get(key) for key in SHOWN}
        shown["text"] = str(row.get("content"))[:60]
        shown["notes"] = len(seq(row.get("notes") or []))
        shown["versions"] = [
            (obj(v)["id"], obj(v)["is_root"]) for v in seq(row.get("versions") or [])
        ]
        self.say(what, shown)
        return row

    def run(
        self, what: str, endpoint: str, api: httpx2.Client | None = None, **body: object
    ) -> list[int]:
        """Post an operation, wait until everything it started ended; the ids its tasks name."""
        mark = self.newest()
        answer = (api or self.admin).post(f"/api/documents/{endpoint}/", json=body)
        self.say(
            f"{what} POST {endpoint} {json.dumps(body)[:300]}",
            f"{answer.status_code} {answer.text[:300]}",
        )
        if not answer.is_success:
            return []
        _ = self.finish(str(obj(parse(answer))["task_id"]))
        quiet = 0
        rows: list[Json] = []
        deadline = time.monotonic() + WAIT
        while quiet < 4 and time.monotonic() < deadline:
            listed = results(
                self.admin.get("/api/tasks/", params={"ordering": "-date_created", "page_size": 50})
            )
            rows = [r for r in listed if num(r["id"]) > mark]
            running = any(r["status"] not in {"success", "failure", "revoked"} for r in rows)
            quiet = 0 if running else quiet + 1
            time.sleep(0.5)
        made: list[int] = []
        for row in sorted(rows, key=lambda r: num(r["id"])):
            keys = (
                *("id", "task_type", "status", "owner", "input_data", "result_data"),
                *("related_document_ids", "trigger_source"),
            )
            self.say(f"{what} task", {k: row.get(k) for k in keys})
            if row["task_type"] == "consume_file":
                made += [num(n) for n in seq(row.get("related_document_ids") or [])]
        return made


@pytest.fixture
def probe(stack: Stack, capfd: pytest.CaptureFixture[str]) -> Iterator[Probe]:
    made = Probe(stack, capfd)
    made.say("start")
    yield made
    try:
        made.clean()
    except Exception:  # noqa: BLE001 -- a probe never fails
        made.say("CLEANUP FAILED", traceback.format_exc()[-1200:])


def attempt(probe: Probe, what: str, step: Callable[[], object]) -> None:
    """Run one step; a surprise is a fact too."""
    try:
        _ = step()
    except Exception:  # noqa: BLE001 -- a probe never fails
        probe.say(f"{what} RAISED", traceback.format_exc()[-1200:])


def new(page: int, doc: int = 0, rotate: int = 0) -> Json:
    return {"page": page, "doc": doc, "rotate": rotate}


@normal
def test_p6_metadata_of_products(probe: Probe) -> None:
    def step() -> None:
        seeded = probe.stack.seeded
        source = probe.pdf(2)
        target = consume_text(probe.admin, f"probe79-target-{uuid.uuid4().hex[:8]}")
        options = obj(
            parse(probe.admin.get(f"/api/custom_fields/{seeded.custom_fields['select']}/"))
        )
        option = obj(seq(obj(options["extra_data"])["select_options"])[0])["id"]
        values: Json = {
            "string": "hello",
            "url": "https://example.com/e2e",
            "date": "2026-01-15",
            "boolean": True,
            "integer": 42,
            "float": 3.25,
            "monetary": "EUR12.50",
            "documentlink": [target],
            "select": option,
            "longtext": "one\ntwo",
        }
        user, _api = probe.user(*EDITING)
        answer = probe.patch(
            source,
            created="2020-02-03",
            tags=[seeded.parent_tag, seeded.child_tag, seeded.inbox_tag],
            correspondent=seeded.correspondent,
            document_type=seeded.document_type,
            storage_path=seeded.storage_path,
            content="corrected by hand",
            custom_fields=[
                {"field": seeded.custom_fields[k], "value": v} for k, v in values.items()
            ],
            set_permissions={
                "view": {"users": [user], "groups": []},
                "change": {"users": [user], "groups": []},
            },
        )
        probe.say(
            "P6 prepared",
            f"{answer.status_code} {answer.text[:300] if not answer.is_success else ''}",
        )
        _ = probe.admin.post(f"/api/documents/{source}/notes/", json={"note": "probe79"})
        _ = probe.admin.post(
            "/api/share_links/", json={"document": source, "file_version": "original"}
        )
        _ = probe.show("P6 source", source)
        _ = probe.show("P6 link target before", target)
        before = probe.all_documents()
        _ = probe.run("P6 include_metadata", "edit_pdf", documents=[source], operations=[new(1)])
        time.sleep(10)
        for made in sorted(probe.all_documents() - before):
            _ = probe.show("P6 product with metadata", made)
            links = probe.admin.get(f"/api/documents/{made}/share_links/")
            probe.say("P6 share links of product", links.text[:200])
        _ = probe.show("P6 link target after", target)
        for kind, value in values.items():
            plain = probe.pdf(1)
            answer = probe.patch(
                plain, custom_fields=[{"field": seeded.custom_fields[kind], "value": value}]
            )
            probe.say(f"P6 {kind} set", str(answer.status_code))
            before = probe.all_documents()
            found = probe.run(f"P6 {kind}", "edit_pdf", documents=[plain], operations=[new(1)])
            made_now: list[int] = []
            for _ in range(20):
                made_now = sorted(probe.all_documents() - before)
                if made_now:
                    break
                time.sleep(1)
            probe.say(f"P6 {kind} task names / documents made", [found, made_now])
        rotated = probe.run("P6 rotate all fields", "rotate", documents=[source], degrees=90)
        time.sleep(10)
        _ = probe.show("P6 after rotate", source)
        probe.say("P6 rotate task names", rotated)

    attempt(probe, "P6", step)


def picture() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (300, 400), (uuid.uuid4().int % 255, 10, 20)).save(
        out, format="PNG", dpi=(100, 100)
    )
    return out.getvalue()


@normal
def test_p8_what_a_merge_leaves_out(probe: Probe) -> None:
    def sources() -> dict[str, int]:
        name = f"probe79-{uuid.uuid4().hex[:8]}"
        _, image = consume(
            probe.admin, f"{name}-image", picture(), filename="i.png", mime_type="image/png"
        )
        _, locked = consume(probe.admin, f"{name}-locked", ENCRYPTED_PDF)
        text = consume_text(probe.admin, f"{name}-text")
        assert image is not None
        assert locked is not None
        made = {"pdf": probe.pdf(2), "text": text, "image": image, "locked": locked}
        for what, number in made.items():
            _ = probe.show(f"P8 source {what}", number)
            meta = obj(parse(probe.admin.get(f"/api/documents/{number}/metadata/")))
            keys = ("original_mime_type", "has_archive_version", "archive_checksum")
            probe.say(f"P8 metadata {what}", {k: meta.get(k) for k in keys})
        return made

    def step() -> None:
        made = sources()
        everything = list(made.values())
        for fallback in (False, True):
            for merged in probe.run(
                f"P8 all four, fallback {fallback}",
                "merge",
                documents=everything,
                archive_fallback=fallback,
            ):
                _ = probe.show(f"P8 merged, fallback {fallback}", merged)
            for what in ("text", "image", "locked"):
                for merged in probe.run(
                    f"P8 pdf and {what}, fallback {fallback}",
                    "merge",
                    documents=[made["pdf"], made[what]],
                    archive_fallback=fallback,
                ):
                    _ = probe.show(f"P8 merged pdf and {what}, fallback {fallback}", merged)
        _ = probe.run("P8 only locked", "merge", documents=[made["locked"]])
        _ = probe.run(
            "P8 omission with delete_originals",
            "merge",
            documents=[made["pdf"], made["locked"]],
            delete_originals=True,
        )
        for what in ("pdf", "locked"):
            probe.say(f"P8 {what} in trash", str(live_in_trash(probe.stack, made[what])))

    attempt(probe, "P8", step)
