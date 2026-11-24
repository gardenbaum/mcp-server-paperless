"""Temporary probe of PLMCP-79 (design section 11): prints facts of 3.5.1, asserts nothing.

Deleted once its facts are contract tests. Every line it prints starts with `PROBE79`.
"""

import importlib
import io
import json
import os
import time
import traceback
import uuid
from typing import TYPE_CHECKING, Any, cast

import httpx2
import pytest
from contract.conftest import delete_and_empty_trash, live_in_trash
from PIL import Image
from rendering.files import pdf_of

from e2e.helpers import (
    ENCRYPTED_PASSWORD,
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
# Not the narrow stub of typings/: the probe counts what the renderer never reads.
pdfium = cast("Any", importlib.import_module("pypdfium2"))
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
def test_p1_page_bound(probe: Probe) -> None:
    def step() -> None:
        root = probe.pdf(2)
        version = probe.version(root, 4)
        _ = probe.show("P1 document", root)
        _ = probe.show("P1 document ?version=root", root, version=root)
        _ = probe.show("P1 document ?version=newest", root, version=version)
        meta = probe.admin.get(f"/api/documents/{root}/metadata/", params={"version": root})
        probe.say("P1 metadata of root version", meta.text[:600])
        _ = probe.run("P1 root id page 4", "edit_pdf", documents=[root], operations=[new(4)])
        for made in probe.run(
            "P1 version id page 4", "edit_pdf", documents=[version], operations=[new(4)]
        ):
            _ = probe.show("P1 product of version id", made)
        for made in probe.run(
            "P1 version id explicit page 3",
            "edit_pdf",
            documents=[version],
            operations=[new(3)],
            source_mode="explicit_selection",
        ):
            _ = probe.show("P1 product of version id explicit", made)
        owner, api = probe.user(*EDITING, "view_paperlesstask")
        _ = probe.patch(root, owner=owner)
        _ = probe.run(
            "P1 owner, version id, new version",
            "edit_pdf",
            api,
            documents=[version],
            operations=[new(1), new(2), new(4)],
            update_document=True,
            source_mode="explicit_selection",
        )
        _ = probe.show("P1 after new version by owner", root)
        seen = api.get(f"/api/documents/{root}/")
        probe.say("P1 owner sees", str(seen.status_code))
        _ = probe.patch(root, owner=None)
        _ = probe.run(
            "P1 unowned, version id", "edit_pdf", api, documents=[version], operations=[new(4)]
        )
        _ = probe.patch(
            root,
            owner=probe.me,
            set_permissions={
                "view": {"users": [owner], "groups": []},
                "change": {"users": [owner], "groups": []},
            },
        )
        _ = probe.run(
            "P1 grant only, version id", "edit_pdf", api, documents=[version], operations=[new(4)]
        )
        api.close()

    attempt(probe, "P1", step)


@normal
def test_p2_explicit_selection(probe: Probe) -> None:
    def step() -> None:
        root = probe.pdf(4)
        version = probe.version(root, 2)
        _ = probe.run(
            "P2 root id latest page 4 (newest has 2)",
            "edit_pdf",
            documents=[root],
            operations=[new(4)],
        )
        for made in probe.run(
            "P2 root id explicit page 4",
            "edit_pdf",
            documents=[root],
            operations=[new(4)],
            source_mode="explicit_selection",
        ):
            _ = probe.show("P2 product of first file", made)
        _ = probe.run(
            "P2 rotate explicit",
            "rotate",
            documents=[root],
            degrees=90,
            source_mode="explicit_selection",
        )
        _ = probe.show("P2 after rotate explicit", root)
        other = probe.pdf(1)
        for made in probe.run(
            "P2 merge explicit",
            "merge",
            documents=[root, other],
            metadata_document_id=root,
            source_mode="explicit_selection",
        ):
            _ = probe.show("P2 merged explicit", made)
        _ = probe.run(
            "P2 new version from first",
            "edit_pdf",
            documents=[root],
            operations=[new(1), new(2), new(3)],
            update_document=True,
            source_mode="explicit_selection",
        )
        _ = probe.show("P2 after new version from first", root)
        _ = probe.run(
            "P3 delete_original with version id",
            "edit_pdf",
            documents=[version],
            operations=[new(1)],
            delete_original=True,
        )
        _ = probe.show("P3 after delete_original with version id", root)
        probe.say("P3 root in trash", str(live_in_trash(probe.stack, root)))
        probe.say("P3 version in trash", str(live_in_trash(probe.stack, version)))

    attempt(probe, "P2", step)


@normal
def test_p5_archive_serial_numbers(probe: Probe) -> None:
    def numbered(pages: int, number: int) -> int:
        made = probe.pdf(pages)
        answer = probe.patch(made, archive_serial_number=number)
        probe.say(f"P5 set ASN {number} on {made}", str(answer.status_code))
        return made

    def step() -> None:
        base = int(time.time()) % 1_000_000 + 7_000_000
        plain = numbered(2, base)
        for made in probe.run(
            "P5 one output, kept", "edit_pdf", documents=[plain], operations=[new(1)]
        ):
            _ = probe.show("P5 product (kept)", made)
        for made in probe.run(
            "P5 two outputs, delete_original",
            "edit_pdf",
            documents=[plain],
            operations=[new(1), new(2, 1)],
            delete_original=True,
        ):
            _ = probe.show("P5 product of two", made)
        trash = [
            r for r in results(probe.admin.get("/api/trash/?page_size=100")) if r["id"] == plain
        ]
        probe.say("P5 trashed original", [r.get("archive_serial_number") for r in trash])
        one = numbered(2, base + 1)
        for made in probe.run(
            "P5 one output, delete_original",
            "edit_pdf",
            documents=[one],
            operations=[new(1)],
            delete_original=True,
        ):
            _ = probe.show("P5 product of one", made)
        first, second = numbered(1, base + 2), numbered(1, base + 3)
        for made in probe.run(
            "P5 merge kept", "merge", documents=[first, second], metadata_document_id=second
        ):
            _ = probe.show("P5 merged, kept", made)
        for made in probe.run(
            "P5 merge delete",
            "merge",
            documents=[first, second],
            metadata_document_id=second,
            delete_originals=True,
        ):
            _ = probe.show("P5 merged, deleted", made)
        title = f"probe79-{uuid.uuid4().hex[:8]}"
        _, locked = consume(probe.admin, title, ENCRYPTED_PDF)
        assert locked is not None
        _ = probe.patch(locked, archive_serial_number=base + 4)
        for made in probe.run(
            "P5 unlock delete",
            "remove_password",
            documents=[locked],
            password=ENCRYPTED_PASSWORD,
            delete_original=True,
        ):
            _ = probe.show("P5 unlocked", made)
            answer = probe.patch(made, archive_serial_number=base + 4)
            probe.say(
                "P5 number of a trashed document", f"{answer.status_code} {answer.text[:200]}"
            )
        restored = probe.admin.post(
            "/api/trash/", json={"action": "restore", "documents": [locked]}
        )
        probe.say("P5 restore", f"{restored.status_code} {restored.text[:200]}")
        _ = probe.show("P5 restored", locked)

    attempt(probe, "P5", step)


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
        for made in probe.run(
            "P6 include_metadata", "edit_pdf", documents=[source], operations=[new(1)]
        ):
            _ = probe.show("P6 product with metadata", made)
            links = probe.admin.get(f"/api/documents/{made}/share_links/")
            probe.say("P6 share links of product", links.text[:200])
        _ = probe.show("P6 link target after", target)
        for made in probe.run(
            "P6 no metadata",
            "edit_pdf",
            documents=[source],
            operations=[new(2)],
            include_metadata=False,
        ):
            _ = probe.show("P6 product without metadata", made)
        _ = probe.run(
            "P6 version without metadata",
            "edit_pdf",
            documents=[source],
            operations=[new(2)],
            include_metadata=False,
            update_document=True,
        )
        _ = probe.show("P6 source after version", source)
        for made in probe.run(
            "P6 merge without metadata document", "merge", documents=[source, target]
        ):
            _ = probe.show("P6 merged without metadata document", made)
        unowned = probe.pdf(1)
        _ = probe.patch(unowned, owner=None)
        other, api = probe.user(*EDITING)
        for made in probe.run(
            "P6 unowned by another user",
            "edit_pdf",
            api,
            documents=[unowned],
            operations=[new(1)],
        ):
            _ = probe.show(f"P6 product of user {other}", made)
        _ = probe.run(
            "P6 unowned rotate by another user", "rotate", api, documents=[unowned], degrees=90
        )
        _ = probe.run(
            "P6 unowned new version by another user",
            "edit_pdf",
            api,
            documents=[unowned],
            operations=[new(1)],
            update_document=True,
        )
        _ = probe.show("P6 unowned document after", unowned)

    attempt(probe, "P6", step)


@normal
def test_p7_tasks_of_parts(probe: Probe) -> None:
    def step() -> None:
        source = probe.pdf(3)
        made = probe.run(
            "P7 three parts",
            "edit_pdf",
            documents=[source],
            operations=[new(3), new(1, 1), new(2, 1), new(2, 2)],
        )
        for number in made:
            _ = probe.show("P7 part", number)
        for permissions in (EDITING, (*EDITING, "view_paperlesstask")):
            owner, api = probe.user(*permissions)
            _ = probe.patch(source, owner=owner)
            _ = probe.run(
                f"P7 as {permissions[-1]}", "edit_pdf", api, documents=[source], operations=[new(1)]
            )
            seen = api.get("/api/tasks/", params={"page_size": 5, "ordering": "-date_created"})
            probe.say(
                f"P7 tasks seen by {permissions[-1]}", f"{seen.status_code} {seen.text[:900]}"
            )
            api.close()

    attempt(probe, "P7", step)


def picture() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (300, 400), (uuid.uuid4().int % 255, 10, 20)).save(out, format="PNG")
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


def rich() -> bytes:
    """A PDF with a title, a bookmark, a form field, an attachment, a link and a signature field."""
    return pdf_of(
        ["BT /F1 18 Tf 72 720 Td (rich one) Tj ET", "BT /F1 18 Tf 72 720 Td (rich two) Tj ET"],
        page="/MediaBox [0 0 600 800] /Annots [7 0 R 8 0 R 13 0 R] "
        "/Resources << /Font << /F1 9 0 R >> >>",
        catalog=(
            "/Outlines 10 0 R /AcroForm << /Fields [7 0 R 13 0 R] /SigFlags 3 "
            "/DA (/Helv 12 Tf 0 g) /DR << /Font << /Helv 9 0 R >> >> >> "
            "/Names << /EmbeddedFiles << /Names [(a.txt) 12 0 R] >> >>"
        ),
        extra=[
            (
                "<< /Type /Annot /Subtype /Widget /FT /Tx /T (name) /V (value) /F 4 "
                "/DA (/Helv 12 Tf 0 g) /Rect [100 600 400 650] /P 3 0 R >>"
            ),
            (
                "<< /Type /Annot /Subtype /Link /Rect [10 10 50 50] "
                "/A << /S /URI /URI (https://example.com) >> >>"
            ),
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            "<< /Type /Outlines /First 11 0 R /Last 11 0 R /Count 1 >>",
            "<< /Title (mark) /Parent 10 0 R /Dest [5 0 R /Fit] >>",
            "<< /Type /Filespec /F (a.txt) /EF << /F 14 0 R >> >>",
            (
                "<< /Type /Annot /Subtype /Widget /FT /Sig /T (sig) /F 4 /Rect [0 0 0 0] /P 3 0 R "
                "/V << /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
                "/ByteRange [0 10 20 10] /Contents <00> >> >>"
            ),
            "<< /Type /EmbeddedFile /Length 5 >>\nstream\nhello\nendstream",
        ],
    )


def contents(probe: Probe, what: str, document: int) -> None:
    answer = probe.admin.get(f"/api/documents/{document}/download/", params={"original": "true"})
    pdf = pdfium.PdfDocument(answer.content)
    facts: Json = {
        "pages": len(pdf),
        "bookmarks": len(list(pdf.get_toc())),
        "attachments": pdf.count_attachments(),
        "form": pdf.get_formtype(),
        "signature dictionary": b"/ByteRange" in answer.content,
        "info": pdf.get_metadata_dict(),
        "size": len(answer.content),
    }
    pages = [pdf[n] for n in range(len(pdf))]  # kept: a page that is collected is closed
    facts["annotations"] = [pdfium.raw.FPDFPage_GetAnnotCount(page.raw) for page in pages]
    facts["signatures"] = cast("object", pdfium.raw.FPDF_GetSignatureCount(pdf.raw))
    for page in pages:
        page.close()
    probe.say(f"P11 {what}", facts)
    pdf.close()


@normal
def test_p11_what_paperless_keeps(probe: Probe) -> None:
    def step() -> None:
        title = f"probe79-{uuid.uuid4().hex[:8]}"
        task, source = consume(probe.admin, title, rich())
        probe.say("P11 consumed", self_row(probe, task))
        assert source is not None
        contents(probe, "source", source)
        for made in probe.run(
            "P11 edit all pages", "edit_pdf", documents=[source], operations=[new(1), new(2)]
        ):
            contents(probe, "edit, all pages", made)
        for made in probe.run(
            "P11 edit one page", "edit_pdf", documents=[source], operations=[new(2)]
        ):
            contents(probe, "edit, one page", made)
        other = probe.pdf(1)
        for made in probe.run("P11 merge", "merge", documents=[other, source]):
            contents(probe, "merge, rich second", made)
        for made in probe.run("P11 merge first", "merge", documents=[source, other]):
            contents(probe, "merge, rich first", made)
        _ = probe.run("P11 rotate", "rotate", documents=[source], degrees=90)
        contents(probe, "rotated", source)

    attempt(probe, "P11", step)


def self_row(probe: Probe, task: str) -> Json:
    row = probe.finish(task)
    return {k: row.get(k) for k in ("status", "result_data", "related_document_ids")}


@pytest.mark.duplicates_e2e
@pytest.mark.skipif(not DUPLICATES, reason="probes the stack that rejects duplicates")
def test_p4_success_without_a_document(probe: Probe) -> None:
    def step() -> None:
        source = probe.pdf(2)
        [first] = probe.run("P4 part 1 alone", "edit_pdf", documents=[source], operations=[new(1)])
        _ = probe.show("P4 first product", first)
        _ = probe.run("P4 part 1 again", "edit_pdf", documents=[source], operations=[new(1)])
        _ = probe.run(
            "P4 split, part 1 a duplicate, delete_original",
            "edit_pdf",
            documents=[source],
            operations=[new(1), new(2, 1)],
            delete_original=True,
        )
        probe.say("P4 original in trash", str(live_in_trash(probe.stack, source)))
        # Products differ in bytes (dates, ids)? Then no duplicate arises: an upload of the very
        # product is what does.
        product = probe.admin.get(f"/api/documents/{first}/download/", params={"original": "true"})
        task = probe.admin.post(
            "/api/documents/post_document/",
            files={"document": ("again.pdf", product.content, "application/pdf")},
        )
        row = probe.finish(str(parse(task)))
        probe.say(
            "P4 the same file uploaded again", {k: row.get(k) for k in ("status", "result_data")}
        )
        second = probe.pdf(1)
        unlocked = probe.run("P4 rotate twice", "rotate", documents=[second], degrees=90)
        again = probe.run("P4 rotate twice", "rotate", documents=[second], degrees=180)
        again += probe.run("P4 rotate twice", "rotate", documents=[second], degrees=90)
        probe.say("P4 versions", [unlocked, again])
        left, right = probe.pdf(1), probe.pdf(1)
        _ = probe.run("P4 merge", "merge", documents=[left, right])
        _ = probe.run(
            "P4 merge again, delete_originals",
            "merge",
            documents=[left, right],
            delete_originals=True,
        )
        probe.say("P4 merged original in trash", str(live_in_trash(probe.stack, left)))

    attempt(probe, "P4", step)
