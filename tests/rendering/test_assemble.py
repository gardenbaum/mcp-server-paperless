"""Pages of one PDF inserted into another: the worker, its answer and the child."""

import io
import json
import struct
import sys
import zlib
from hashlib import shake_256
from typing import override

import pypdfium2
import pytest
from e2e.helpers import RESTRICTED_PDF

from mcp_server_paperless.domain.rendering import (
    Assembled,
    AssembleRequest,
    RenderFailed,
    RenderRequest,
)
from mcp_server_paperless.rendering import worker
from mcp_server_paperless.rendering.runner import ChildRenderer
from mcp_server_paperless.rendering.wire import Job, assembled_of, encoded
from mcp_server_paperless.rendering.worker import assemble, main, render
from rendering.files import ENCRYPTED_PDF, corners, filled_form, opened, pdf_of
from rendering.test_main import Guard, pdf_with_pages
from rendering.test_overview import A4, A5, asked, mixed
from rendering.test_runner import READ, WITHOUT_FONTS

pytestmark = [pytest.mark.decision("decision-42"), pytest.mark.decision("decision-68")]

MIB = 1024 * 1024
WINDOWS = sys.platform == "win32"

PDF_REQUEST = RenderRequest(mime_type="application/pdf", pages="1-", size=400)


def sized(*widths: int) -> bytes:
    """A PDF whose pages can be told apart by their width."""
    return mixed(*[f"/MediaBox [0 0 {width} 800]" for width in widths])


TARGET, SOURCE = sized(301, 302, 303), sized(501, 502, 503, 504)


def wanted(
    pages: str = "1-", after_page: int = 1, target_pages: int = 3, max_bytes: int = MIB
) -> AssembleRequest:
    return AssembleRequest(
        pages=pages, after_page=after_page, target_pages=target_pages, max_bytes=max_bytes
    )


def widths(content: bytes) -> list[int]:
    pdf = pypdfium2.PdfDocument(content)
    return [round(pdf.get_page_size(n)[0]) for n in range(len(pdf))]


@pytest.mark.parametrize(
    ("pages", "after_page", "expected"),
    [
        ("2", 0, [502, 301, 302, 303]),
        ("2-3", 1, [301, 502, 503, 302, 303]),
        ("4,1", 3, [301, 302, 303, 504, 501]),
        ("3-", 2, [301, 302, 503, 504, 303]),
    ],
    ids=["front", "middle", "end", "to the last"],
)
def test_the_pages_are_inserted_where_they_were_asked_for_in_their_order(
    pages: str, after_page: int, expected: list[int]
) -> None:
    found = assemble(TARGET, SOURCE, wanted(pages, after_page))

    assert widths(found.content) == expected
    assert found.content.startswith(b"%PDF-")
    assert found.source_pages == 4
    assert len(found.inserted) == len(expected) - 3


def test_the_content_of_the_pages_is_theirs() -> None:
    left = pdf_of(["1 0 0 rg 0 0 600 800 re f"])
    right = pdf_of(["0 1 0 rg 0 0 600 800 re f", "0 0 1 rg 0 0 600 800 re f"])

    made = assemble(left, right, wanted("2", 0, 1)).content

    shown = render(made, PDF_REQUEST).images
    assert [corners(opened(image.data))[0] for image in shown] == ["blue", "red"]


def test_sizes_and_rotation_of_inserted_pages_stay() -> None:
    source = mixed(A4, f"{A5} /Rotate 90", f"{A4} /Rotate 270 /CropBox [10 10 300 400]")
    before = worker.overview(source, asked(sheets=False), worker.label_font()).runs

    made = assemble(TARGET, source, wanted("1-", 3)).content

    runs = worker.overview(made, asked("4-", sheets=False), worker.label_font()).runs
    assert [run.shape for run in runs] == [run.shape for run in before]


INHERITING = sized(301, 302, 303).replace(
    b"/Type /Pages", b"/Type /Pages /Rotate 90 /CropBox [0 0 100 100]"
)


def test_an_inserted_page_inherits_nothing_of_the_document_it_goes_into() -> None:
    made = assemble(INHERITING, SOURCE, wanted("1", 1)).content

    pdf = pypdfium2.PdfDocument(made)
    shown = [(pdf[n].get_size(), pdf[n].get_rotation()) for n in range(4)]
    assert shown == [
        ((100.0, 100.0), 90),
        ((501.0, 800.0), 0),
        ((100.0, 100.0), 90),
        ((100.0, 100.0), 90),
    ]


def test_an_inserted_page_keeps_what_it_inherits() -> None:
    made = assemble(SOURCE, INHERITING, wanted("2", 0, 4)).content

    page = pypdfium2.PdfDocument(made)[0]
    assert (page.get_size(), page.get_rotation()) == ((100.0, 100.0), 90)


def rich(attachment: str) -> bytes:
    """A PDF with a bookmark, an attachment and a form field that names it."""
    return pdf_of(
        [""],
        page="/MediaBox [0 0 600 800] /Annots [9 0 R]",
        catalog=(
            "/Outlines 5 0 R /Names << /EmbeddedFiles << /Names [(a) 7 0 R] >> >> "
            "/AcroForm << /Fields [9 0 R] >>"
        ),
        extra=[
            "<< /Type /Outlines /First 6 0 R /Last 6 0 R /Count 1 >>",
            "<< /Title (Chapter) /Parent 5 0 R /Dest [3 0 R /Fit] >>",
            "<< /Type /Filespec /F (a.txt) /EF << /F 8 0 R >> >>",
            f"<< /Type /EmbeddedFile /Length {len(attachment)} >>\nstream\n{attachment}\nendstream",
            (
                "<< /Type /Annot /Subtype /Widget /FT /Tx /T (name) /V (Ada) /Rect [1 1 99 20] "
                "/P 3 0 R >>"
            ),
        ],
    )


def structure(content: bytes) -> tuple[int, int, int]:
    """Bookmarks, attachments, and the kind of the form (0: none)."""
    pdf = pypdfium2.PdfDocument(content)
    attachments = pypdfium2.raw.FPDFDoc_GetAttachmentCount(pdf)
    return len(list(pdf.get_toc())), attachments, pdf.get_formtype()


def test_the_document_keeps_its_structure_and_takes_none_of_the_source() -> None:
    kept = assemble(rich("mine"), SOURCE, wanted("1", 1, 1)).content
    taken = assemble(TARGET, rich("theirs"), wanted("1")).content

    assert structure(rich("x")) == (1, 1, 1)
    assert structure(kept) == (1, 1, 1)
    assert structure(taken) == (0, 0, 0)


def test_the_document_keeps_the_values_of_its_form() -> None:
    made = assemble(filled_form(), SOURCE, wanted("1", 1, 1)).content
    request = PDF_REQUEST.model_copy(update={"pages": "1"})

    assert render(made, request).images == render(filled_form(), request).images


def test_links_and_annotations_come_with_their_page() -> None:
    link = (
        "/Annots [<< /Type /Annot /Subtype /Link /Rect [0 0 9 9] "
        "/A << /S /URI /URI (https://example.org/) >> >>]"
    )
    source = pdf_of([""], page=f"/MediaBox [0 0 600 800] {link}")

    assert b"https://example.org/" in assemble(TARGET, source, wanted()).content


def test_other_pages_of_the_source_are_not_taken_along() -> None:
    link = "/Annots [<< /Type /Annot /Subtype /Link /Rect [0 0 9 9] /Dest [5 0 R /Fit] >>]"
    noise = "% " + shake_256(b"other").hexdigest(50_000)
    source = pdf_of(["", noise], page=f"/MediaBox [0 0 600 800] {link}")

    assert len(assemble(TARGET, source, wanted("1")).content) < 10_000
    assert len(assemble(TARGET, source, wanted("1-2")).content) > 25_000


@pytest.mark.parametrize(
    ("target", "source", "request_", "reason", "of_source", "page_count"),
    [
        (ENCRYPTED_PDF, SOURCE, wanted(), "encrypted", False, 0),
        (TARGET, ENCRYPTED_PDF, wanted(), "encrypted", True, 0),
        (RESTRICTED_PDF, SOURCE, wanted(target_pages=2), "restricted", False, 0),
        (TARGET, RESTRICTED_PDF, wanted(), "restricted", True, 0),
        (b"nothing", SOURCE, wanted(), "damaged", False, 0),
        (TARGET, b"nothing", wanted(), "damaged", True, 0),
        (TARGET, SOURCE, wanted("5"), "pages", True, 4),
        (TARGET, SOURCE, wanted(""), "pages", True, 4),
        (TARGET, SOURCE, wanted(target_pages=4), "differs", False, 0),
        (TARGET, SOURCE, wanted(target_pages=2), "differs", False, 0),
        (TARGET, pdf_with_pages(200_001), wanted("1"), "count", True, 0),
        (TARGET, SOURCE, wanted(max_bytes=500), "large", False, 0),
    ],
)
def test_files_that_cannot_be_put_together_say_why(  # noqa: PLR0913, PLR0917 -- one case each
    target: bytes,
    source: bytes,
    request_: AssembleRequest,
    reason: str,
    of_source: bool,  # noqa: FBT001 -- a parameter of the case
    page_count: int,
) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = assemble(target, source, request_)

    found = raised.value
    assert (found.reason, found.of_source, found.page_count) == (reason, of_source, page_count)


def test_a_result_with_too_many_pages_is_refused() -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = assemble(pdf_with_pages(150_000), pdf_with_pages(50_001), wanted(target_pages=150_000))

    assert (raised.value.reason, raised.value.of_source) == ("count", False)


def test_a_result_of_exactly_the_most_bytes_is_taken() -> None:
    size = len(assemble(TARGET, SOURCE, wanted("1")).content)

    assert len(assemble(TARGET, SOURCE, wanted("1", max_bytes=size)).content) <= size
    with pytest.raises(RenderFailed, match="large"):
        _ = assemble(TARGET, SOURCE, wanted("1", max_bytes=size - 20))


MISSING = TARGET.replace(b"/Kids [3 0 R 5 0 R", b"/Kids [3 0 R 99 0 R")
CYCLE = TARGET.replace(b"/Kids [3 0 R 5 0 R", b"/Kids [3 0 R 2 0 R")


@pytest.mark.parametrize("broken", [MISSING, CYCLE], ids=["missing", "cycle"])
def test_a_page_tree_that_is_broken_is_damage_and_not_a_crash(broken: bytes) -> None:
    for files, of_source in (((broken, SOURCE), False), ((TARGET, broken), True)):
        with pytest.raises(RenderFailed) as raised:
            _ = assemble(*files, wanted("2-3" if of_source else "1"))
        assert (raised.value.reason, raised.value.of_source) == ("damaged", of_source)


def test_a_broken_page_of_the_source_that_is_not_wanted_does_not_matter() -> None:
    assert widths(assemble(TARGET, MISSING, wanted("1,3", 3)).content) == [301, 302, 303, 301, 303]


class Unwilling(pypdfium2.PdfDocument):
    """Cannot import pages."""

    @override
    def import_pages(self, pdf: pypdfium2.PdfDocument, pages: list[int], index: int) -> None:
        message = "Failed to import pages."
        raise pypdfium2.PdfiumError(message)


class Silent(pypdfium2.PdfDocument):
    """Says it imported pages, and did not."""

    @override
    def import_pages(self, pdf: pypdfium2.PdfDocument, pages: list[int], index: int) -> None:
        _ = (pdf, pages, index)


class Wrong(pypdfium2.PdfDocument):
    """Imports the first page in place of every other."""

    @override
    def import_pages(self, pdf: pypdfium2.PdfDocument, pages: list[int], index: int) -> None:
        super().import_pages(pdf, [0] * len(pages), index)


class Twice(pypdfium2.PdfDocument):
    """Imports every page twice."""

    @override
    def import_pages(self, pdf: pypdfium2.PdfDocument, pages: list[int], index: int) -> None:
        super().import_pages(pdf, pages, index)
        super().import_pages(pdf, pages, index)


@pytest.mark.parametrize("kind", [Unwilling, Silent, Wrong, Twice])
def test_an_import_that_did_not_do_its_work_is_damage(
    kind: type[pypdfium2.PdfDocument],
) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = assemble(TARGET, SOURCE, wanted("2-3"), open_pdf=kind)

    assert (raised.value.reason, raised.value.of_source) == ("damaged", True)


# --- the answer


def run(target: bytes, source: bytes, request: AssembleRequest) -> tuple[int, bytes, Guard]:
    content = target + source
    head = Job(
        request=request,
        content_bytes=len(content),
        first_bytes=len(target),
        memory_bytes=1000,
        seconds=5.0,
    )
    out, guard = io.BytesIO(), Guard()
    return main(io.BytesIO(encoded(head) + content), out, guard), out.getvalue(), guard


def test_the_answer_is_the_file() -> None:
    code, out, guard = run(TARGET, SOURCE, wanted("2-3"))

    found = assembled_of(out, wanted("2-3"))
    assert (code, guard.calls) == (0, [(1000, 5.0)])
    assert (found.inserted, found.source_pages) == ((2, 3), 4)
    assert widths(found.content) == [301, 502, 503, 302, 303]


@pytest.mark.parametrize(
    ("target", "source", "request_", "reason", "of_source"),
    [
        (ENCRYPTED_PDF, SOURCE, wanted(), "encrypted", False),
        (TARGET, ENCRYPTED_PDF, wanted(), "encrypted", True),
        (TARGET, SOURCE, wanted("5"), "pages", True),
    ],
)
def test_a_failure_is_an_answer_that_names_the_file(
    target: bytes,
    source: bytes,
    request_: AssembleRequest,
    reason: str,
    of_source: bool,  # noqa: FBT001 -- a parameter of the case
) -> None:
    code, out, _ = run(target, source, request_)

    with pytest.raises(RenderFailed) as raised:
        _ = assembled_of(out, request_)
    assert (code, raised.value.reason, raised.value.of_source) == (0, reason, of_source)


def said(count: int = 4, data: bytes = b"%PDF-1.7", **head: object) -> bytes:
    return json.dumps({"page_count": count, **head}).encode() + b"\n" + data


def test_an_answer_that_adds_up_is_taken_and_the_pages_follow_from_the_request() -> None:
    assert assembled_of(said(), wanted("3,1")) == Assembled(
        content=b"%PDF-1.7", source_pages=4, inserted=(3, 1)
    )
    assert assembled_of(said(), wanted(max_bytes=8)).content == b"%PDF-1.7"
    assert assembled_of(said(data=b"%PDF-1\n\n{"), wanted()).content == b"%PDF-1\n\n{"


@pytest.mark.parametrize(
    "output",
    [
        b"",
        b"not json\n%PDF-1.7",
        said(data=b""),
        said(data=b"PK..%PDF-"),
        said(count=0),
        said(count=200_001),
        b'{"failure": "pages", "page_count": 4}\n',  # the pages are right
        b'{"failure": "invented"}\n',
    ],
)
def test_an_answer_that_does_not_add_up_is_a_crash(output: bytes) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = assembled_of(output, wanted("1-3"))

    assert raised.value.reason == "crashed"


def test_more_than_the_most_bytes_is_a_crash() -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = assembled_of(said(), wanted(max_bytes=7))

    assert raised.value.reason == "crashed"


# --- the child


@pytest.mark.anyio
async def test_the_file_is_made_in_a_child_process() -> None:
    found = await ChildRenderer().assemble(TARGET, SOURCE, wanted("2-3", 3))

    assert (found.inserted, found.source_pages) == ((2, 3), 4)
    assert widths(found.content) == [301, 302, 303, 502, 503]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target", "source", "reason", "of_source"),
    [
        (RESTRICTED_PDF, SOURCE, "restricted", False),
        (TARGET, ENCRYPTED_PDF, "encrypted", True),
        (TARGET, b"nothing", "damaged", True),
    ],
)
async def test_the_child_says_why_it_cannot(
    target: bytes,
    source: bytes,
    reason: str,
    of_source: bool,  # noqa: FBT001 -- a parameter of the case
) -> None:
    request = wanted(target_pages=2 if target is RESTRICTED_PDF else 3)

    with pytest.raises(RenderFailed) as raised:
        _ = await ChildRenderer().assemble(target, source, request)

    assert (raised.value.reason, raised.value.of_source) == (reason, of_source)


@pytest.mark.anyio
@pytest.mark.skipif(WINDOWS, reason="there are no resource limits on Windows")
async def test_a_child_that_may_open_nothing_puts_files_together() -> None:
    renderer = ChildRenderer(command=(sys.executable, "-I", "-c", WITHOUT_FONTS))

    found = await renderer.assemble(TARGET, SOURCE, wanted("1"))

    assert widths(found.content) == [301, 501, 302, 303]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("import time\ntime.sleep(60)", "timeout"),
        ("while True: sys.stdout.buffer.write(b'x' * 65536)", "crashed"),
        ("raise SystemExit(73)", "memory"),
        ("raise SystemExit(74)", "timeout"),
        ("raise SystemExit(1)", "crashed"),
    ],
)
async def test_the_limits_of_the_child_hold_for_a_file(code: str, reason: str) -> None:
    renderer = ChildRenderer(command=(sys.executable, "-I", "-c", READ + code), seconds=2.0)

    with pytest.raises(RenderFailed) as raised:
        _ = await renderer.assemble(b"x", b"y", wanted())

    assert (raised.value.reason, raised.value.slow) == (reason, None)


# Answers a file for `assemble`, and for the count what the code says.
TWICE = """
kind = head["request"]["kind"]
if kind == "assemble":
    sys.stdout.buffer.write(b'{"page_count": 4}\\n%PDF-made')
else:
    _ = sys.stdin.buffer.read(head["content_bytes"])
"""


def counted(answer: str, seconds: float = 20.0) -> ChildRenderer:
    code = READ + TWICE + "    " + answer
    return ChildRenderer(command=(sys.executable, "-I", "-c", code), seconds=seconds)


OVERVIEW = (
    """sys.stdout.buffer.write(json.dumps({"page_count": %d, "runs": [{"count": 1, "shape": """
    """None}]}).encode() + b"\\n")"""
)


@pytest.mark.anyio
async def test_the_pages_of_the_file_are_counted_by_a_second_child() -> None:
    found = await counted(OVERVIEW % 4).assemble(b"x", b"y", wanted("2"))

    assert found.content == b"%PDF-made"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (OVERVIEW % 3, "crashed"),
        (OVERVIEW % 5, "crashed"),
        ("""sys.stdout.buffer.write(b'{"failure": "damaged"}\\n')""", "crashed"),
        ("""sys.stdout.buffer.write(b'{"failure": "encrypted"}\\n')""", "crashed"),
        ("raise SystemExit(73)", "memory"),
        ("import time; time.sleep(60)", "timeout"),
    ],
)
async def test_a_file_that_does_not_hold_its_pages_is_not_given_out(
    answer: str, reason: str
) -> None:
    renderer = counted(answer, 3.0 if reason == "timeout" else 20.0)
    with pytest.raises(RenderFailed) as raised:
        _ = await renderer.assemble(b"x", b"y", wanted("2"))

    assert (raised.value.reason, raised.value.of_source, raised.value.slow) == (reason, False, None)


@pytest.mark.anyio
async def test_both_children_share_the_seconds_of_a_call() -> None:
    slow = "import time; time.sleep(1.2)\n"
    code = READ + slow + TWICE + "    " + OVERVIEW % 4
    renderer = ChildRenderer(command=(sys.executable, "-I", "-c", code), seconds=2.0)

    with pytest.raises(RenderFailed) as raised:
        _ = await renderer.assemble(b"x", b"y", wanted("2"))

    assert raised.value.reason == "timeout"


@pytest.mark.anyio
async def test_the_real_child_stops_at_its_memory() -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = await ChildRenderer(memory_bytes=10_000_000).assemble(TARGET, SOURCE, wanted())

    assert raised.value.reason == "memory"


def filled(megabytes: int) -> bytes:
    """A page with an image of that many bytes that do not compress."""
    block = shake_256(b"image").hexdigest(MIB // 2)
    data = block * megabytes
    image = (
        "<< /Type /XObject /Subtype /Image /Width 1000 /Height 1000 /BitsPerComponent 8 "
        f"/ColorSpace /DeviceGray /Filter /ASCIIHexDecode /Length {len(data)} >>\n"
        f"stream\n{data}\nendstream"
    )
    return pdf_of(
        ["600 0 0 800 0 0 cm /I Do"],
        page="/MediaBox [0 0 600 800] /Resources << /XObject << /I 5 0 R >> >>",
        extra=[image],
    )


@pytest.mark.anyio
async def test_two_files_of_the_most_bytes_fit_the_limits() -> None:
    request = wanted("1", 1, 1, max_bytes=100 * MIB)
    target, source = filled(49), filled(50)

    found = await ChildRenderer().assemble(target, source, request)

    assert 99 * MIB < len(found.content) <= 100 * MIB


def bomb(unpacked: int) -> bytes:
    """A small PDF whose page holds an object that is ``unpacked`` bytes long."""
    inner = b"5 0 (" + b"a" * unpacked + b")"
    packed = zlib.compress(inner, 9)
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Big 5 0 R >>",
        b"<< /Type /ObjStm /N 1 /First 4 /Filter /FlateDecode /Length %d >>\nstream\n" % len(packed)
        + packed
        + b"\nendstream",
    ]
    out, rows = b"%PDF-1.7\n", [struct.pack(">BIB", 0, 0, 255)]
    for number, body in enumerate(bodies, start=1):
        rows.append(struct.pack(">BIB", 1, len(out), 0))
        out += b"%d 0 obj\n%b\nendobj\n" % (number, body)
    rows += [struct.pack(">BIB", 2, 4, 0), struct.pack(">BIB", 1, len(out), 0)]
    table = b"".join(rows)
    return out + (
        b"6 0 obj\n<< /Type /XRef /Size 7 /Root 1 0 R /W [1 4 1] /Length %d >>\n" % len(table)
        + b"stream\n%b\nendstream\nendobj\nstartxref\n%d\n%%%%EOF\n" % (table, len(out))
    )


@pytest.mark.anyio
@pytest.mark.parametrize("of_source", [True, False])
async def test_a_small_file_that_unpacks_to_a_large_one_is_refused(
    of_source: bool,  # noqa: FBT001 -- a parameter of the case
) -> None:
    packed = bomb(3 * MIB)
    files = (TARGET, packed) if of_source else (packed, SOURCE)
    request = wanted("1", 1, 3 if of_source else 1, max_bytes=2 * MIB)

    assert len(packed) < 10_000
    with pytest.raises(RenderFailed) as raised:
        _ = await ChildRenderer().assemble(*files, request)
    assert raised.value.reason == "large"
    made = await ChildRenderer().assemble(*files, request.model_copy(update={"max_bytes": 4 * MIB}))
    assert len(made.content) > 3 * MIB


@pytest.mark.anyio
async def test_a_file_that_unpacks_to_more_than_the_memory_ends_the_child() -> None:
    request = wanted("1", max_bytes=100 * MIB)

    with pytest.raises(RenderFailed) as raised:
        _ = await ChildRenderer(memory_bytes=200 * MIB).assemble(TARGET, bomb(150 * MIB), request)

    assert raised.value.reason in {"memory", "crashed"}  # Linux refuses the allocation at once
