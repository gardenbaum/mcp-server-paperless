"""Render pages of one file: the program of the child process, and the function behind it.

The file is untrusted: PDFium and Pillow read it with native code. `runner` starts this module in
a process of its own for each call and bounds it.
"""

import io
import math
import struct
import sys
from typing import TYPE_CHECKING, BinaryIO, Final, Protocol, override

import pypdfium2
import pypdfium2.raw
from PIL import ExifTags, Image, TiffImagePlugin, UnidentifiedImageError

from mcp_server_paperless.domain.errors import Validation
from mcp_server_paperless.domain.rendering import PageImage, Rendered, RenderFailed
from mcp_server_paperless.rendering import limits
from mcp_server_paperless.rendering.limits import MEMORY_EXIT
from mcp_server_paperless.rendering.wire import Job, answer
from mcp_server_paperless.services.page_ranges import parse_pages

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from mcp_server_paperless.domain.rendering import Reason, Region, RenderRequest

PDF = "application/pdf"
WHOLE: Region = (0.0, 0.0, 1.0, 1.0)
WHITE = (255, 255, 255, 255)
# FPDF_ERR_PASSWORD and FPDF_ERR_SECURITY (a scheme PDFium does not know).
PDF_ERRORS: Final[Mapping[int | None, Reason]] = {4: "encrypted", 5: "encrypted"}
# The only formats Pillow may open, and what its decoders raise for a broken file.
FORMATS: Final = ("JPEG", "PNG", "TIFF", "WEBP")
BROKEN: Final = (
    UnidentifiedImageError,
    OSError,
    SyntaxError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    EOFError,
    ZeroDivisionError,
    OverflowError,
    struct.error,
)
# EXIF orientation -> how the stored image is turned to be shown.
TURNS: Final[Mapping[int, Image.Transpose]] = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
QUALITY = 80
# A page that is too large alone: this quality first, then three quarters of the pixels per side,
# again and again. Twelve steps take the largest image to about 100 pixels.
LOW_QUALITY = 50
SHRINK = 0.75
REDUCTIONS = 12
# Faster than the default method at the same size.
METHOD = 2

# Pillow's own pixel limit only warns up to twice its value; every frame is checked here instead.
Image.MAX_IMAGE_PIXELS = None


class _Source(Protocol):
    """An opened file."""

    page_count: int

    def draw(self, page: int, size: int, region: Region) -> Image.Image:
        """The region of a page (from 1) with ``size`` pixels on its long side."""
        ...


def _scaled(width: float, height: float, region: Region, size: int) -> tuple[float, int, int]:
    """The scale that gives ``region`` ``size`` pixels on its long side, and its pixels."""
    left, top, right, bottom = region
    shown = ((right - left) * width, (bottom - top) * height)
    scale = size / max(shown)
    return scale, max(1, round(shown[0] * scale)), max(1, round(shown[1] * scale))


class _Pdf(_Source):
    """A PDF, drawn at the final size with annotations and form fields."""

    def __init__(self, content: bytes) -> None:
        try:
            self._pdf: pypdfium2.PdfDocument = pypdfium2.PdfDocument(content)
            _ = self._pdf.init_forms()
        except pypdfium2.PdfiumError as error:
            raise RenderFailed(reason=PDF_ERRORS.get(error.err_code, "damaged")) from error
        self.page_count: int = len(self._pdf)

    @override
    def draw(self, page: int, size: int, region: Region) -> Image.Image:
        try:
            found = self._pdf[page - 1]
        except pypdfium2.PdfiumError as error:
            raise RenderFailed(reason="damaged") from error
        page_width, page_height = found.get_size()
        scale, width, height = _scaled(page_width, page_height, region, size)
        bitmap = pypdfium2.PdfBitmap.new_native(width, height, pypdfium2.raw.FPDFBitmap_BGR)
        bitmap.fill_rect(WHITE, 0, 0, width, height)
        place = (
            -round(region[0] * page_width * scale),
            -round(region[1] * page_height * scale),
            max(1, round(page_width * scale)),
            max(1, round(page_height * scale)),
            0,
            pypdfium2.raw.FPDF_ANNOT,
        )
        pypdfium2.raw.FPDF_RenderPageBitmap(bitmap, found, *place)
        if found.formenv:
            pypdfium2.raw.FPDF_FFLDraw(found.formenv, bitmap, found, *place)
        return bitmap.to_pil()


def _stored(region: Region, orientation: int) -> Region:
    """``region`` of the image as shown, on the image as stored with this EXIF orientation."""
    left, top, right, bottom = region
    if orientation >= 5:  # noqa: PLR2004 -- 5 to 8 swap the sides
        left, top, right, bottom = top, left, bottom, right
    if orientation in {2, 3, 7, 8}:
        left, right = 1 - right, 1 - left
    if orientation in {3, 4, 6, 7}:
        top, bottom = 1 - bottom, 1 - top
    return left, top, right, bottom


def _flat(image: Image.Image) -> Image.Image:
    """The image in a mode that can be scaled, and transparency kept."""
    if image.mode in {"I", "I;16", "L", "RGB", "RGBA"} and "transparency" not in image.info:
        return image  # convert() would copy it: 400 MB at the pixel limit
    if image.has_transparency_data:
        return image.convert("RGBA")
    return image.convert("L" if image.mode == "1" else "RGB")


class _Picture(_Source):
    """An image; the frames of a TIFF are its pages."""

    def __init__(self, content: bytes, max_pixels: int) -> None:
        self._max_pixels: int = max_pixels
        try:
            image = Image.open(io.BytesIO(content), formats=FORMATS)
            tiff = isinstance(image, TiffImagePlugin.TiffImageFile)
            self.page_count: int = image.n_frames if tiff else 1
        except BROKEN as error:
            raise RenderFailed(reason="damaged") from error
        self._image: Image.Image = image
        self._tiff: bool = tiff

    @override
    def draw(self, page: int, size: int, region: Region) -> Image.Image:
        try:
            return self._draw(page, size, region)
        except BROKEN as error:
            raise RenderFailed(reason="damaged") from error

    def _draw(self, page: int, size: int, region: Region) -> Image.Image:
        image = self._image
        if self._tiff:
            image.seek(page - 1)
        pixels = image.width * image.height
        if pixels > self._max_pixels:
            raise RenderFailed(reason="pixels", megapixels=pixels / 1e6)
        # Pillow turns a TIFF itself when it loads it, and gives its size as shown.
        orientation = 1 if self._tiff else image.getexif().get(ExifTags.Base.Orientation, 1)
        turn = TURNS.get(orientation)
        left, top, right, bottom = _stored(region, orientation if turn is not None else 1)
        box_region = (left, top, right, bottom)
        scale, width, height = _scaled(image.width, image.height, box_region, size)
        # A JPEG can be decoded at 1/2, 1/4 or 1/8 of its size; never below what is shown.
        _ = image.draft(None, (math.ceil(image.width * scale), math.ceil(image.height * scale)))
        _ = image.load()
        box = (left * image.width, top * image.height, right * image.width, bottom * image.height)
        shown = _flat(image).resize((width, height), Image.Resampling.LANCZOS, box=box)
        if shown.mode.startswith("I"):
            # 16 bits per pixel (and Pillow's 32 bit form of them) to 8.
            shown = shown.convert("I").point(lambda value: value / 256).convert("L")
        if shown.mode == "RGBA":
            paper = Image.new("RGBA", shown.size, WHITE)
            paper.alpha_composite(shown)
            shown = paper.convert("RGB")
        return shown if turn is None else shown.transpose(turn)


def _webp(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=quality, method=METHOD)
    return buffer.getvalue()


def _page_image(page: int, image: Image.Image, *, max_bytes: int, whole: bool) -> PageImage | None:
    """The page as an image of at most ``max_bytes`` bytes.

    A page that does not fit is None with ``whole``; else it gets a lower quality and then fewer
    pixels, as many times as it takes.
    """
    data = _webp(image, QUALITY)
    shown = image
    reduced = len(data) > max_bytes
    if reduced and whole:
        return None
    for step in range(REDUCTIONS):
        if len(data) <= max_bytes:
            break
        factor = SHRINK**step
        size = (max(1, int(image.width * factor)), max(1, int(image.height * factor)))
        shown = image if step == 0 else image.resize(size, Image.Resampling.LANCZOS)
        data = _webp(shown, LOW_QUALITY)
    return PageImage(page=page, width=shown.width, height=shown.height, data=data, reduced=reduced)


def render(content: bytes, request: RenderRequest) -> Rendered:
    """The pages ``request`` asks for, as many as fit its bytes and its number of images.

    Raises:
        RenderFailed: The file cannot be read, or it has no such page.
    """
    source: _Source = (
        _Pdf(content) if request.mime_type == PDF else _Picture(content, request.max_pixels)
    )
    try:
        pages = parse_pages(request.pages, page_count=source.page_count)
    except Validation as error:
        raise RenderFailed(reason="pages", page_count=source.page_count) from error
    images: list[PageImage] = []
    left = request.max_bytes
    for number in pages[: request.max_images]:
        drawn = source.draw(number, request.size, request.region or WHOLE)
        image = _page_image(number, drawn, max_bytes=left, whole=bool(images))
        if image is None:
            break
        images.append(image)
        left -= len(image.data)
    return Rendered(
        page_count=source.page_count, images=tuple(images), rest=tuple(pages[len(images) :])
    )


def main(stdin: BinaryIO, stdout: BinaryIO, guard: Callable[[int, float], None]) -> int:
    """Render the job on ``stdin`` and write the answer to ``stdout``; returns the exit code.

    ``guard`` bounds the process by the memory and the seconds of the job.
    """
    # All plugins now: the process may not be able to open their modules later.
    _ = Image.init()
    job = Job.model_validate_json(stdin.readline())
    try:
        guard(job.memory_bytes, job.seconds)
        content = stdin.read(job.content_bytes)
        if len(content) < job.content_bytes:
            return 1  # the server went away
        try:
            found: Rendered | RenderFailed = render(content, job.request)
        except RenderFailed as failure:
            found = failure
        _ = stdout.write(answer(found))
        stdout.flush()
    except MemoryError:
        return MEMORY_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.stdin.buffer, sys.stdout.buffer, limits.guard))
