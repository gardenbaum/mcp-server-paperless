"""get_document_pages through the assembled server on Paperless 3.4.2, with real files.

Office documents and e-mails need Tika and Gotenberg: the tests marked `office_e2e` run with
`just e2e-office` and skip elsewhere.
"""

import base64
import io
import json
import os
import uuid
import zipfile
from email.message import EmailMessage
from hashlib import shake_256
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from harness import paperless_settings, server_settings
from mcp.types import ImageContent, TextContent
from PIL import Image
from rendering.files import QUADRANTS, corners, opened, pdf_of, quadrants, saved
from test_http_uploads import DEFAULT, http_settings, listening

from e2e.helpers import ENCRYPTED_PDF, consume, pdf_pages
from e2e.test_stdio import console_script
from mcp_server_paperless.app import build_app
from mcp_server_paperless.domain.rendering import MAX_IMAGE_BYTES
from mcp_server_paperless.server import create_server
from mcp_server_paperless.services.pages import MAX_SOURCE_BYTES

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastmcp import FastMCP
    from fastmcp.client.client import CallToolResult

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

type Json = dict[str, object]

PDF = "application/pdf"
UPRIGHT = ["red", "green", "blue", "yellow"]
OFFICE = os.environ.get("PAPERLESS_E2E_OFFICE") == "1"
NO_OFFICE = "This Paperless has no Tika and Gotenberg; `just e2e-office` runs this against one."


class Scope:
    """The documents of one test: every id it made, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.ids: list[int] = []

    def document(self, content: bytes, filename: str, mime_type: str = PDF) -> int:
        """A document of `content`, which no other test has: a comment at its end is unique."""
        title = f"plmcp74-{uuid.uuid4().hex}"
        _, made = consume(self.stack.admin, title, content, filename=filename, mime_type=mime_type)
        assert made is not None, f"Paperless did not consume {filename}"
        self.ids.append(made)
        return made


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


def unique(content: bytes) -> bytes:
    """A PDF that is not a duplicate of an earlier one."""
    return content + f"\n%{uuid.uuid4().hex}\n".encode()


def server(stack: Stack, token: str | None = None) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, token or stack.admin_token),
        server_settings(toolsets=["core", "versions"]),
    )


def report(result: CallToolResult) -> Json:
    first = result.content[0]
    assert isinstance(first, TextContent)
    return cast("Json", json.loads(first.text))


def images(result: CallToolResult) -> list[Image.Image]:
    return [
        opened(base64.b64decode(block.data))
        for block in result.content
        if isinstance(block, ImageContent)
    ]


async def show(stack: Stack, document: int, **arguments: object) -> CallToolResult:
    async with Client(server(stack)) as client:
        return await client.call_tool("get_document_pages", {"document_id": document, **arguments})


async def refused(stack: Stack, document: int, **arguments: object) -> str:
    async with Client(server(stack)) as client:
        result = await client.call_tool(
            "get_document_pages", {"document_id": document, **arguments}, raise_on_error=False
        )
    assert result.is_error
    return str(result.content)


def dark(image: Image.Image) -> int:
    """How many pixels are dark: whether something is written there."""
    return sum(image.convert("L").histogram()[:100])


async def test_the_original_and_the_archive_of_a_pdf_are_shown(stack: Stack) -> None:
    number = stack.seeded.public_document

    original = await show(stack, number)
    archive = await show(stack, number, source="archive")

    for result, source in ((original, "original"), (archive, "archive")):
        [page] = images(result)
        assert max(page.size) == 1568
        assert dark(page) > 100
        assert (report(result)["source"], report(result)["page_count"]) == (source, 1)


async def test_pages_and_a_region_of_a_pdf_are_shown(stack: Stack, scope: Scope) -> None:
    texts = [f"page {n} {uuid.uuid4().hex[:6]}" for n in range(1, 8)]
    document = scope.document(unique(pdf_pages(texts)), "seven.pdf")

    some = await show(stack, document, pages="6-7,2", size=512)
    part = await show(stack, document, region=[0.05, 0.05, 0.55, 0.15], size=1000)
    first = await show(stack, document, pages="1-", size=256)

    assert [i["page"] for i in cast("list[Json]", report(some)["images"])] == [6, 7, 2]
    assert len(images(some)) == 3
    [enlarged] = images(part)
    assert enlarged.width == 1000
    assert dark(enlarged) > 500
    assert len(images(first)) == 5
    assert (report(first)["not_shown"], report(first)["next_pages"]) == ("pages 6-7", "6-7")
    rest = await show(stack, document, pages=str(report(first)["next_pages"]), size=256)
    assert len(images(rest)) == 2
    assert report(rest)["next_pages"] is None


@pytest.mark.parametrize("kind", ["JPEG", "PNG", "TIFF", "WEBP"])
async def test_image_originals_are_shown(stack: Stack, scope: Scope, kind: str) -> None:
    picture = quadrants(612, 792)
    # A dot of a random colour makes the file unique.
    picture.putpixel((5, 5), tuple(uuid.uuid4().bytes[:3]))
    mime_type = f"image/{kind.lower()}"
    document = scope.document(saved(picture, kind), f"quadrants.{kind.lower()}", mime_type)

    original = await show(stack, document, size=512)
    archive = await show(stack, document, size=512, source="archive")

    assert (report(original)["source"], report(original)["mime_type"]) == ("original", mime_type)
    assert corners(images(original)[0]) == UPRIGHT
    assert corners(images(archive)[0]) == UPRIGHT


async def test_the_pages_of_a_tiff_are_shown(stack: Stack, scope: Scope) -> None:
    frames = [quadrants(300, 400), Image.new("RGB", (300, 400), (0, 0, 255))]
    frames[0].putpixel((5, 5), tuple(uuid.uuid4().bytes[:3]))
    content = saved(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    document = scope.document(content, "frames.tiff", "image/tiff")

    result = await show(stack, document, pages="2,1", size=512)

    second, first = images(result)
    assert (corners(second), corners(first)) == (["blue"] * 4, UPRIGHT)


async def test_every_version_is_shown_and_a_deleted_one_is_not(stack: Stack, scope: Scope) -> None:
    document = scope.document(unique(pdf_of([QUADRANTS])), "first.pdf")
    blue = unique(pdf_of(["0 0 1 rg 0 0 600 800 re f"]))
    async with Client(server(stack)) as client:
        uploaded = await client.call_tool(
            "upload_document_version",
            {
                "document_id": document,
                "content_base64": base64.b64encode(blue).decode(),
                "filename": "second.pdf",
            },
        )
        output = uploaded.structured_content
        assert output is not None
        listed = await client.call_tool("list_document_versions", {"document_id": document})
        versions = cast("list[Json]", (listed.structured_content or {})["versions"])
        newest = max(cast("int", version["id"]) for version in versions)

        latest = await show(stack, document, size=256)
        root = await show(stack, document, size=256, version_id=document)
        archive = await show(stack, document, size=256, version_id=document, source="archive")
        _ = await client.call_tool(
            "delete_document_version",
            {"document_id": document, "version_id": newest, "confirm": True},
        )

    assert (report(latest)["version_id"], corners(images(latest)[0])) == (newest, ["blue"] * 4)
    assert (report(root)["version_id"], corners(images(root)[0])) == (document, UPRIGHT)
    assert corners(images(archive)[0]) == UPRIGHT
    assert f"Document {document} has no version {newest}." in await refused(
        stack, document, version_id=newest
    )


async def test_a_text_original_is_refused_with_the_reason(stack: Stack, scope: Scope) -> None:
    document = scope.document(uuid.uuid4().hex.encode(), "note.txt", "text/plain")

    said = await refused(stack, document)

    assert "is text/plain, which cannot be shown as pages" in said
    assert "Read its text with get_document_content." in said


async def test_an_encrypted_pdf_is_refused_with_the_reason(stack: Stack, scope: Scope) -> None:
    document = scope.document(unique(ENCRYPTED_PDF), "locked.pdf")

    said = await refused(stack, document, source="original")

    assert "is encrypted: it needs a password" in said
    assert "remove_document_password (toolset pdf)" in said


async def test_a_damaged_page_is_shown_or_refused_but_never_a_crash_of_the_server(
    stack: Stack, scope: Scope
) -> None:
    # Paperless refuses most damaged files; it takes a PDF whose image data is cut off.
    whole = unique(pdf_of([QUADRANTS, "q 100 0 0 100 0 0 cm /Missing Do Q ((("]))
    document = scope.document(whole, "damaged.pdf")

    async with Client(server(stack)) as client:
        _ = await client.call_tool(
            "get_document_pages", {"document_id": document, "pages": "2"}, raise_on_error=False
        )
        first = await client.call_tool("get_document_pages", {"document_id": document, "size": 256})

    assert corners(images(first)[0]) == UPRIGHT


async def test_a_page_past_the_end_and_a_missing_document_are_refused(stack: Stack) -> None:
    number = stack.seeded.public_document

    assert "Page 2 does not exist: the document has 1 page." in await refused(
        stack, number, pages="2"
    )
    assert "No document 999999 exists" in await refused(stack, 999_999)


async def test_a_restricted_user_sees_the_pages_of_their_documents_only(stack: Stack) -> None:
    async with Client(server(stack, stack.reader_token)) as client:
        seen = await client.call_tool(
            "get_document_pages", {"document_id": stack.seeded.public_document, "size": 256}
        )
        hidden = await client.call_tool(
            "get_document_pages",
            {"document_id": stack.seeded.private_document},
            raise_on_error=False,
        )

    assert len(images(seen)) == 1
    assert hidden.is_error
    assert not [block for block in hidden.content if isinstance(block, ImageContent)]


async def test_a_file_over_the_source_limit_is_refused_before_it_is_read(
    stack: Stack, scope: Scope
) -> None:
    padding = b"\n%" + b"x" * MAX_SOURCE_BYTES
    document = scope.document(unique(pdf_of([QUADRANTS])) + padding, "large.pdf")

    assert "is over 100 MiB, the most that is rendered" in await refused(
        stack, document, source="original"
    )


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
    content = saved(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    document = scope.document(content, "noise.tiff", "image/tiff")

    seen: list[int] = []
    wanted: object = "1-"
    while wanted is not None:
        result = await show(stack, document, pages=wanted, size=1000, source="original")
        shown = cast("list[Json]", report(result)["images"])
        assert sum(cast("int", image["size_bytes"]) for image in shown) <= MAX_IMAGE_BYTES
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


async def test_pages_are_shown_by_the_console_script_over_stdio(stack: Stack) -> None:
    transport = StdioTransport(
        console_script(),
        [],
        env={"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token},
        keep_alive=False,
    )

    async with Client(transport) as client:
        result = await client.call_tool(
            "get_document_pages", {"document_id": stack.seeded.public_document}
        )

    assert dark(images(result)[0]) > 100


async def test_pages_are_shown_over_http(stack: Stack) -> None:
    app = build_app(server(stack), http_settings(with_token=False), max_upload_bytes=DEFAULT)
    with listening(app) as url:
        async with Client(StreamableHttpTransport(url), mode="legacy") as client:
            result = await client.call_tool(
                "get_document_pages", {"document_id": stack.seeded.public_document}
            )

    assert dark(images(result)[0]) > 100


# --- With Tika and Gotenberg (`just e2e-office`) ------------------------------------------------


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
