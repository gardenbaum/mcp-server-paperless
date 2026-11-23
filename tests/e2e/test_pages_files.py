"""get_document_pages on Paperless 3.5.1: every file type, broken files and each limit.

Office documents and e-mails need Tika and Gotenberg: the tests marked `office_e2e` run with
`just e2e-office` and skip elsewhere.
"""

import asyncio
import io
import os
import uuid
import zipfile
from email.message import EmailMessage
from hashlib import shake_256
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash
from PIL import Image
from rendering.files import QUADRANTS, corners, pdf_of, quadrants, saved

from e2e.helpers import ENCRYPTED_PDF
from e2e.pages import (
    PDF,
    UPRIGHT,
    Json,
    Proxy,
    Scope,
    dark,
    images,
    page_service,
    refused,
    report,
    show,
    unique,
)
from mcp_server_paperless.domain.errors import PaperlessMcpError
from mcp_server_paperless.domain.rendering import MAX_IMAGE_BYTES
from mcp_server_paperless.services.pages import MAX_SOURCE_BYTES

if TYPE_CHECKING:
    from collections.abc import Iterator

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

OFFICE = os.environ.get("PAPERLESS_E2E_OFFICE") == "1"
NO_OFFICE = "This Paperless has no Tika and Gotenberg; `just e2e-office` runs this against one."


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


@pytest.mark.parametrize("kind", ["JPEG", "PNG", "TIFF", "WEBP"])
async def test_image_originals_are_shown(stack: Stack, scope: Scope, kind: str) -> None:
    picture = quadrants(612, 792)
    # A dot of a random colour makes the file unique.
    picture.putpixel((5, 5), tuple(uuid.uuid4().bytes[:3]))
    mime_type = f"image/{kind.lower()}"
    # Paperless needs a plausible resolution to consume a TIFF.
    content = saved(picture, kind, dpi=(100, 100))
    document = scope.document(content, f"quadrants.{kind.lower()}", mime_type)

    original = await show(stack, document, size=512)
    archive = await show(stack, document, size=512, source="archive")

    assert (report(original)["source"], report(original)["mime_type"]) == ("original", mime_type)
    assert corners(images(original)[0]) == UPRIGHT
    assert corners(images(archive)[0]) == UPRIGHT


async def test_the_pages_of_a_tiff_are_shown(stack: Stack, scope: Scope) -> None:
    frames = [quadrants(300, 400), Image.new("RGB", (300, 400), (0, 0, 255))]
    frames[0].putpixel((5, 5), tuple(uuid.uuid4().bytes[:3]))
    content = saved(frames[0], "TIFF", save_all=True, append_images=frames[1:], dpi=(100, 100))
    document = scope.document(content, "frames.tiff", "image/tiff")

    result = await show(stack, document, pages="2,1", size=512)

    second, first = images(result)
    assert (corners(second), corners(first)) == (["blue"] * 4, UPRIGHT)


async def test_an_encrypted_pdf_is_refused_with_the_reason(stack: Stack, scope: Scope) -> None:
    document = scope.document(unique(ENCRYPTED_PDF), "locked.pdf")

    said = await refused(stack, document, source="original")

    assert "is encrypted: it needs a password" in said
    assert "remove_document_password (toolset pdf)" in said


async def test_a_damaged_original_is_refused_with_the_reason_and_its_archive_is_shown(
    stack: Stack, scope: Scope
) -> None:
    # Paperless refuses most damaged files; it takes a JPEG whose end is missing.
    picture = quadrants(612, 792)
    picture.putpixel((5, 5), tuple(uuid.uuid4().bytes[:3]))
    whole = saved(picture, "JPEG", dpi=(100, 100))
    document = scope.document(whole[: len(whole) * 4 // 5], "cut.jpg", "image/jpeg")

    said = await refused(stack, document)
    archive = await show(stack, document, source="archive", size=256)

    assert f"The original of document {document} is damaged and cannot be read." in said
    assert len(images(archive)) == 1


async def test_a_file_over_the_source_limit_is_refused_before_it_is_read(
    stack: Stack, scope: Scope
) -> None:
    padding = b"\n%" + b"x" * MAX_SOURCE_BYTES
    document = scope.document(unique(pdf_of([QUADRANTS])) + padding, "large.pdf")

    async with Proxy().listening() as proxy, page_service(stack, url=proxy.url) as service:
        with pytest.raises(PaperlessMcpError, match="is over 100 MiB, the most that is rendered"):
            _ = await service.show(document, "1", source="original")

    assert any(path.startswith(f"/api/documents/{document}/metadata/") for path in proxy.requests)
    assert not any("/download/" in path for path in proxy.requests)


async def test_a_file_that_takes_too_long_to_come_is_given_up(stack: Stack, scope: Scope) -> None:
    # Not compressible: Paperless compresses what it sends.
    padding = b"\n%" + os.urandom(1_000_000).hex().encode()
    document = scope.document(unique(pdf_of([QUADRANTS])) + padding, "late.pdf")

    async with (
        Proxy(slow=True).listening() as proxy,
        page_service(stack, url=proxy.url, download_seconds=2.0) as service,
    ):
        with pytest.raises(
            PaperlessMcpError,
            match=r"did not send the file of document .* within 2 seconds",
        ):
            _ = await service.show(document, "1", source="original")
        assert any("/download/" in path for path in proxy.requests)
        # The only slot is free again.
        proxy.slow = False
        shown = await service.show(document, "1", source="original", size=256)

    assert len(shown.images) == 1


async def test_a_call_waits_for_the_one_before_it_and_then_gives_up(
    stack: Stack, scope: Scope
) -> None:
    document = scope.document(unique(slow_pdf()), "slow.pdf")

    async with page_service(stack, wait_seconds=1.0, render_seconds=8.0) as service:
        first = asyncio.create_task(service.show(document, "2", source="original"))
        await asyncio.sleep(2.0)
        with pytest.raises(PaperlessMcpError, match="Another document is being rendered"):
            _ = await service.show(document, "1", source="original")
        with pytest.raises(PaperlessMcpError, match="was stopped"):
            _ = await first
        shown = await service.show(document, "1", source="original", size=256)

    assert len(shown.images) == 1


async def test_a_page_too_large_for_a_call_is_shown_reduced(stack: Stack, scope: Scope) -> None:
    noise = Image.frombytes("RGB", (1500, 1500), shake_256(uuid.uuid4().bytes).digest(6_750_000))
    document = scope.document(saved(noise, "PNG", dpi=(100, 100)), "noise.png", "image/png")

    result = await show(stack, document, size=2576, source="original")

    [image] = cast("list[Json]", report(result)["images"])
    assert image["reduced"] is True
    assert cast("int", image["size_bytes"]) <= MAX_IMAGE_BYTES
    assert "Page 1 was too large as asked and is shown reduced" in str(report(result)["note"])


async def test_an_image_over_the_pixel_limit_is_refused(stack: Stack, scope: Scope) -> None:
    image = Image.new("1", (10_001, 10_000), 1)
    image.putpixel((int(uuid.uuid4()) % 10_000, 5), 0)
    document = scope.document(saved(image, "PNG", dpi=(1200, 1200)), "large.png", "image/png")

    assert "has 100.0 megapixels, more than the 100 that are decoded" in await refused(
        stack, document, source="original"
    )


async def test_pages_over_the_bytes_of_a_call_come_with_the_next_calls(
    stack: Stack, scope: Scope
) -> None:
    frames = [
        Image.frombytes("RGB", (700, 700), shake_256(uuid.uuid4().bytes).digest(1_470_000))
        for _ in range(3)
    ]
    content = saved(frames[0], "TIFF", save_all=True, append_images=frames[1:], dpi=(100, 100))
    document = scope.document(content, "noise.tiff", "image/tiff")

    seen: list[int] = []
    wanted: object = "1-"
    while wanted is not None:
        result = await show(stack, document, pages=wanted, size=1000, source="original")
        shown = cast("list[Json]", report(result)["images"])
        assert sum(cast("int", image["size_bytes"]) for image in shown) <= MAX_IMAGE_BYTES
        if not seen:
            first = report(result)
            assert (first["next_pages"], first["not_shown"]) == ("2-3", "pages 2-3")
        seen += [cast("int", image["page"]) for image in shown]
        wanted = report(result)["next_pages"]

    assert seen == [1, 2, 3]


def two_pages(second: str, resources: str, objects: list[bytes]) -> bytes:
    """A PDF whose first page is plain and whose second one is the problem.

    Both pages have a text layer, so Paperless does not OCR them, and it draws only the first one
    for the thumbnail. `objects` are numbered from 8.
    """
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    text = "BT /F1 18 Tf 72 720 Td (plmcp74 a page with enough text that nobody has to read) Tj ET"
    parts: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
    ]
    for number, stream, extra in ((4, text, ""), (6, f"{text} {second}", resources)):
        page = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {number} 0 R "
            f"/Resources << /Font << /F1 7 0 R >> {extra} >> >>"
        )
        parts += [
            page.encode(),
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode(),
        ]
    out = b"%PDF-1.5\n"
    offsets: list[int] = []
    for number, body in enumerate([*parts, font, *objects], start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(offsets) + 1,
        xref,
    )


def slow_pdf() -> bytes:
    """A shading whose colour is a long calculation for each pixel, drawn a hundred times."""
    steps = " ".join(["dup sin 2 mul exch"] * 60)
    program = f"{{ {steps} {' '.join(['add'] * 60)} exch pop dup dup }}".encode()
    function = (
        b"<< /FunctionType 4 /Domain [0 612 0 792] /Range [0 1 0 1 0 1] /Length %d >>\n"
        % len(program)
        + b"stream\n%b\nendstream" % program
    )
    shading = b"<< /ShadingType 1 /ColorSpace /DeviceRGB /Domain [0 612 0 792] /Function 8 0 R >>"
    return two_pages("/S sh " * 100, "/Shading << /S 9 0 R >>", [function, shading])


def heavy_pdf() -> bytes:
    """A progressive JPEG of 400 megapixels: its coefficients are decoded as a whole."""
    jpeg = saved(Image.new("RGB", (20_000, 20_000), "white"), "JPEG", progressive=True)
    image = (
        b"<< /Type /XObject /Subtype /Image /Width 20000 /Height 20000 /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n%b\nendstream"
    ) % (len(jpeg), jpeg)
    return two_pages("q 600 0 0 600 0 0 cm /I Do Q", "/XObject << /I 8 0 R >>", [image])


async def test_a_page_that_takes_too_long_is_stopped(stack: Stack, scope: Scope) -> None:
    document = scope.document(unique(slow_pdf()), "slow.pdf")

    first = await show(stack, document, source="original", size=256)
    said = await refused(stack, document, source="original", pages="2")

    assert len(images(first)) == 1
    assert f"Rendering document {document} took longer than 30 seconds and was stopped." in said


async def test_a_page_that_needs_too_much_memory_is_stopped(stack: Stack, scope: Scope) -> None:
    document = scope.document(unique(heavy_pdf()), "heavy.pdf")

    first = await show(stack, document, source="original", size=256)
    said = await refused(stack, document, source="original", pages="2")

    assert len(images(first)) == 1
    assert f"Rendering document {document} needed more than 1 GiB of memory" in said


def docx(text: str) -> bytes:
    """The smallest Word document."""
    types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    relations = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    body = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", relations)
        archive.writestr("word/document.xml", body)
    return buffer.getvalue()


def eml(text: str) -> bytes:
    """An e-mail."""
    message = EmailMessage()
    message["From"], message["To"] = "sender@example.org", "receiver@example.org"
    message["Subject"], message["Date"] = text, "Mon, 23 Nov 2026 10:00:00 +0000"
    message["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
    message.set_content(text)
    return message.as_bytes()


@pytest.mark.office_e2e
@pytest.mark.skipif(not OFFICE, reason=NO_OFFICE)
@pytest.mark.parametrize(
    ("filename", "mime_type"),
    [
        (
            "letter.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("mail.eml", "message/rfc822"),
    ],
)
async def test_office_documents_and_e_mails_are_shown_through_their_archive(
    stack: Stack, scope: Scope, filename: str, mime_type: str
) -> None:
    text = f"plmcp74 {uuid.uuid4().hex}"
    content = docx(text) if filename.endswith("docx") else eml(text)
    document = scope.document(content, filename, mime_type)

    result = await show(stack, document)

    assert (report(result)["source"], report(result)["mime_type"]) == ("archive", PDF)
    assert dark(images(result)[0]) > 100
    assert f"is {mime_type}, which cannot be shown as pages" in await refused(
        stack, document, source="original"
    )
