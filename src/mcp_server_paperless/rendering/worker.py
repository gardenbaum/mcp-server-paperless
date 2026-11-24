"""Render pages of one file, or put pages of two together: the program of the child process.

The functions behind it are here, too. The files are untrusted: PDFium and Pillow read them with
native code. `runner` starts this module in a process of its own for each call and bounds it.
"""

import ctypes
import importlib.util
import io
import itertools
import math
import re
import struct
import sys
import time
from itertools import batched, chain, groupby, islice
from typing import TYPE_CHECKING, BinaryIO, Final, Protocol, cast, override

import pypdfium2
import pypdfium2.raw
from PIL import ExifTags, Image, ImageDraw, ImageFont, TiffImagePlugin, UnidentifiedImageError
from pydantic import ValidationError

from mcp_server_paperless.domain.errors import Validation
from mcp_server_paperless.domain.rendering import (
    DEFAULT_SIZE,
    MAX_PAGES,
    MAX_RUNS,
    TILES,
    Assembled,
    AssembleRequest,
    ContactSheet,
    Counted,
    CountRequest,
    Overview,
    OverviewRequest,
    PageImage,
    PageRun,
    PageShape,
    Rendered,
    RenderFailed,
)
from mcp_server_paperless.rendering import limits
from mcp_server_paperless.rendering.fields import draw_fields, has_fields, remove_fields
from mcp_server_paperless.rendering.limits import MEMORY_EXIT
from mcp_server_paperless.rendering.wire import DONE, READING, STARTED, Job, answer
from mcp_server_paperless.services.page_ranges import parse_pages

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from mcp_server_paperless.domain.rendering import Reason, Region, RenderRequest
    from mcp_server_paperless.rendering.wire import Answered

PDF = "application/pdf"
WHOLE: Region = (0.0, 0.0, 1.0, 1.0)
WHITE = (255, 255, 255, 255)
# FPDF_ERR_PASSWORD and FPDF_ERR_SECURITY (a scheme PDFium does not know).
PDF_ERRORS: Final[Mapping[int | None, Reason]] = {4: "encrypted", 5: "encrypted"}
# The longest side of a page that is put together, in points: 3.5 kilometres.
MAX_SIDE: Final = 10_000_000
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
# Pixels of an image on each side that are scaled at a time, and how many pixels of the result
# LANCZOS looks at around one (three, and one for rounding).
TILE: Final = 4_096
MARGIN: Final = 4
# Megapixels above which a progressive JPEG is left to PDFium.
LARGE_JPEG: Final = 25_000_000
# Markers of a JPEG: the frame headers (which hold the size), and where the data of a scan ends.
FRAME_HEADERS: Final = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
PROGRESSIVE: Final = 0xC2
START_OF_SCAN: Final = 0xDA
DEFINE_LINES: Final = 0xDC
END_OF_IMAGE: Final = 0xD9
SCAN_END: Final = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")
QUALITY = 80
# A page that is too large alone: this quality first, then three quarters of the pixels per side,
# again and again. Twelve steps take the largest image to about 100 pixels.
LOW_QUALITY = 50
SHRINK = 0.75
REDUCTIONS = 12
# Faster than the default method at the same size.
METHOD = 2
# A contact sheet: its columns, the side of a tile, the strip with the number, and the space
# around the page.
COLUMNS: Final = 4
CELL: Final = DEFAULT_SIZE // COLUMNS
STRIP: Final = 34
SPACE: Final = 6
NUMBER_SIZE: Final = 24
DESK: Final = (200, 200, 200)
INK: Final = (0, 0, 0)
MM_PER_POINT: Final = 25.4 / 72

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

    def __init__(
        self,
        content: bytes,
        open_pdf: Callable[[bytes], pypdfium2.PdfDocument] = pypdfium2.PdfDocument,
    ) -> None:
        try:
            self._pdf: pypdfium2.PdfDocument = open_pdf(content)
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
        return self._drawn(found, size, region)

    def _drawn(self, found: pypdfium2.PdfPage, size: int, region: Region) -> Image.Image:
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

    def _loaded(self, page: int) -> tuple[PageShape, pypdfium2.PdfPage] | None:
        """A page (from 1) and its shape; None if it is missing or has no usable size."""
        try:
            found = self._pdf[page - 1]
            width, height = (round(side * MM_PER_POINT, 1) for side in found.get_size())
            shape = PageShape.model_validate(
                {"width_mm": width, "height_mm": height, "rotation": found.get_rotation()}
            )
        except pypdfium2.PdfiumError, ValidationError:
            return None
        return shape, found

    def shape(self, page: int) -> PageShape | None:
        """The shape of a page."""
        loaded = self._loaded(page)
        return loaded and loaded[0]

    def tile(self, page: int) -> tuple[PageShape | None, Image.Image]:
        """The shape of a page, and the page as large as it fits above its number."""
        loaded = self._loaded(page)
        if loaded is None:
            return None, Image.new("RGB", (1, 1), DESK)
        shape, found = loaded
        width, height = CELL - 2 * SPACE, CELL - STRIP - SPACE
        longest = max(shape.width_mm, shape.height_mm)
        fits = min(width * longest / shape.width_mm, height * longest / shape.height_mm)
        return shape, self._drawn(found, max(1, int(fits)), WHOLE)


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
    """The image in a mode that can be scaled, with its transparency."""
    if image.has_transparency_data:
        return image if image.mode in {"LA", "RGBA"} else image.convert("RGBA")
    if image.mode in {"L", "RGB"}:
        return image  # convert() would copy it
    if image.mode.startswith("I"):
        return image.convert("I")
    return image.convert("L" if image.mode == "1" else "RGB")


def resized(
    image: Image.Image, box: Region, size: tuple[int, int], *, tile: int = TILE
) -> Image.Image:
    """The box of an image, scaled to ``size``: nearly what ``resize`` with this box gives.

    A tile at a time, of about ``tile`` pixels of the image on each side, because converting and
    scaling a large image as a whole takes several copies of it. Each tile is cut with the pixels
    around it that the filter looks at.
    """
    columns = _spans(box[0], box[2], size[0], image.width, tile)
    rows = _spans(box[1], box[3], size[1], image.height, tile)
    shown: Image.Image | None = None
    for x, width, (from_x, to_x), (left, right) in columns:
        for y, height, (from_y, to_y), (top, bottom) in rows:
            part = _flat(image.crop((from_x, from_y, to_x, to_y)))
            place = (left - from_x, top - from_y, right - from_x, bottom - from_y)
            piece = part.resize((width, height), Image.Resampling.LANCZOS, box=place)
            if shown is None:
                shown = Image.new(piece.mode, size)
            shown.paste(piece, (x, y))
    return shown or image


def _spans(
    start: float, end: float, pixels: int, limit: int, tile: int
) -> list[tuple[int, int, tuple[int, int], tuple[float, float]]]:
    """One direction, cut into tiles.

    For each its place and length in the result, the pixels of the image it needs, and the part of
    them it shows.
    """
    step = (end - start) / pixels
    reach = MARGIN * max(1.0, step)
    length = max(1, int(tile / max(1.0, step)) - 2 * MARGIN)
    spans: list[tuple[int, int, tuple[int, int], tuple[float, float]]] = []
    for first in range(0, pixels, length):
        count = min(length, pixels - first)
        low, high = start + first * step, min(start + (first + count) * step, float(limit))
        needed = (max(0, int(low - reach)), min(limit, math.ceil(high + reach)))
        spans.append((first, count, needed, (low, high)))
    return spans


class DecodeInto(Protocol):
    """libwebp's ``WebPDecodeRGBAInto``."""

    def __call__(
        self,
        data: bytes,
        data_size: ctypes.c_size_t,
        output: ctypes.Array[ctypes.c_ubyte],
        output_size: ctypes.c_size_t,
        stride: int,
        /,
    ) -> int | None:
        """Decode into ``output``; None if it cannot."""
        ...


def libwebp(module: str = "PIL._webp") -> DecodeInto | None:
    """From the libwebp that Pillow's own module is linked to; None if built in (Windows)."""
    spec = importlib.util.find_spec(module)
    try:
        function = ctypes.CDLL(str(spec and spec.origin))["WebPDecodeRGBAInto"]
    except OSError, AttributeError:
        return None
    function.restype = ctypes.c_void_p
    return cast("DecodeInto", function)


LIBWEBP: Final = libwebp()


def decoded_webp(
    content: bytes, size: tuple[int, int], decode: DecodeInto | None = LIBWEBP
) -> Image.Image | None:
    """A still WebP image, decoded into the one buffer that is kept.

    Pillow decodes WebP through the animation decoder, which holds three to four copies of the
    image (1.6 GB for 100 megapixels). None if this way does not work: animations, broken files,
    and where libwebp cannot be reached.
    """
    if decode is None:
        return None
    width, height = size
    buffer = bytearray(width * height * 4)
    output = (ctypes.c_ubyte * len(buffer)).from_buffer(buffer)
    length, space = ctypes.c_size_t(len(content)), ctypes.c_size_t(len(buffer))
    if not decode(content, length, output, space, width * 4):
        return None
    return Image.frombuffer("RGBA", size, buffer, "raw", "RGBA", 0, 1)


def single_jpeg(content: bytes, image: Image.Image) -> bytes | None:
    """The one progressive JPEG picture that Pillow opened as ``image``, without what follows it.

    None if the file is something else: another format, no end, more than one frame header or one
    of another size (the pixel limit was checked on what Pillow measured).
    """
    if image.format not in {"JPEG", "MPO"} or content[:2] != b"\xff\xd8":
        return None
    position, frames = 2, 0
    while content[position : position + 1] == b"\xff" and position + 4 <= len(content):
        marker = content[position + 1]
        if marker in FRAME_HEADERS:
            frames += 1
            shape = struct.unpack_from(">HH", content[position + 5 : position + 9].ljust(4, b"\0"))
            if frames > 1 or marker != PROGRESSIVE or shape != (image.height, image.width):
                return None
        if marker == DEFINE_LINES:  # it could change the height
            return None
        position += 2 + int.from_bytes(content[position + 2 : position + 4])
        if marker == START_OF_SCAN:
            found = SCAN_END.search(content, position)
            if found is None:
                return None
            position = found.start()
            if content[position + 1] == END_OF_IMAGE:
                return content[: position + 2] if frames else None
    return None


def _as_pdf(content: bytes, image: Image.Image) -> bytes:
    """A JPEG as a PDF of one page. Pillow holds a large progressive one twice, PDFium once."""
    side = f"{image.width} 0 0 {image.height} 0 0 cm /I Do".encode()
    colours = b"/DeviceRGB"
    if image.mode == "CMYK":
        # Pillow inverts the values of a JPEG with an Adobe marker, as its writers mean them.
        colours = b"/DeviceCMYK" + b" /Decode [1 0 1 0 1 0 1 0]" * ("adobe" in image.info)
    size = (image.width, image.height)
    page = b"/MediaBox [0 0 %d %d] /Resources << /XObject << /I 5 0 R >> >>" % size
    look = b"/Width %d /Height %d /BitsPerComponent 8 /ColorSpace %b" % (*size, colours)
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R %b >>" % page,
        b"<< /Length %d >>\nstream\n%b\nendstream" % (len(side), side),
        b"<< /Type /XObject /Subtype /Image /Filter /DCTDecode %b /Length %d >>\n"
        % (look, len(content))
        + b"stream\n%b\nendstream" % content,
    )
    body = b"".join(b"%d 0 obj\n%b\nendobj\n" % one for one in enumerate(objects, start=1))
    return b"%PDF-1.4\n" + body + b"trailer << /Size 6 /Root 1 0 R >>\n"


class _Picture(_Source):
    """An image; the frames of a TIFF are its pages."""

    def __init__(self, content: bytes, max_pixels: int) -> None:
        self._content: bytes = content
        self._max_pixels: int = max_pixels
        try:
            image = self._opened()
            tiff = isinstance(image, TiffImagePlugin.TiffImageFile)
            self.page_count: int = image.n_frames if tiff else 1
        except BROKEN as error:
            raise RenderFailed(reason="damaged") from error
        self._image: Image.Image = image
        self._tiff: bool = tiff

    def _opened(self) -> Image.Image:
        return Image.open(io.BytesIO(self._content), formats=FORMATS)

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
        if image.format == "WEBP":
            # The opened image holds copies of the file: it goes before this takes its own memory.
            shape = image.size
            image = self._image = Image.new("1", (1, 1))
            image = decoded_webp(self._content, shape) or self._opened()
        elif (
            pixels > LARGE_JPEG
            and image.mode != "L"
            and (picture := single_jpeg(self._content, image)) is not None
        ):
            drawn = _Pdf(_as_pdf(picture, image)).draw(1, size, box_region)
            return drawn if turn is None else drawn.transpose(turn)
        # A JPEG can be decoded at 1/2, 1/4 or 1/8 of its size; never below what is shown.
        _ = image.draft(None, (math.ceil(image.width * scale), math.ceil(image.height * scale)))
        _ = image.load()
        box = (left * image.width, top * image.height, right * image.width, bottom * image.height)
        shown = resized(image, box, (width, height))
        if shown.mode.startswith("I"):
            # 16 bits per pixel (and Pillow's 32 bit form of them) to 8.
            shown = shown.point(lambda value: value / 256).convert("L")
        if shown.mode in {"LA", "RGBA"}:
            paper = Image.new("RGBA", shown.size, WHITE)
            paper.alpha_composite(shown.convert("RGBA"))
            shown = paper.convert("RGB")
        return shown if turn is None else shown.transpose(turn)


def _webp(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=quality, method=METHOD)
    return buffer.getvalue()


def _fitted(image: Image.Image, data: bytes, max_bytes: int) -> tuple[Image.Image, bytes]:
    """The image and its file ``data``, made smaller until it has at most ``max_bytes`` bytes.

    It gets a lower quality and then fewer pixels, as many times as it takes.
    """
    shown = image
    for step in range(REDUCTIONS):
        if len(data) <= max_bytes:
            break
        factor = SHRINK**step
        size = (max(1, int(image.width * factor)), max(1, int(image.height * factor)))
        shown = image if step == 0 else image.resize(size, Image.Resampling.LANCZOS)
        data = _webp(shown, LOW_QUALITY)
    return shown, data


def _page_image(page: int, image: Image.Image, *, max_bytes: int, whole: bool) -> PageImage | None:
    """The page as an image of at most ``max_bytes`` bytes.

    A page that does not fit is None with ``whole``; else it is reduced.
    """
    data = _webp(image, QUALITY)
    reduced = len(data) > max_bytes
    if reduced and whole:
        return None
    shown, data = _fitted(image, data, max_bytes)
    return PageImage(page=page, width=shown.width, height=shown.height, data=data, reduced=reduced)


def _pages(page_count: int, wanted: str) -> list[int]:
    """The pages of a file that were asked for."""
    if page_count > MAX_PAGES:
        raise RenderFailed(reason="count")
    try:
        return parse_pages(wanted, page_count=page_count)
    except Validation as error:
        raise RenderFailed(reason="pages", page_count=page_count) from error


def render(content: bytes, request: RenderRequest) -> Rendered:
    """The pages ``request`` asks for, as many as fit its bytes and its number of images.

    Raises:
        RenderFailed: The file cannot be read, or it has no such page.
    """
    source: _Source = (
        _Pdf(content) if request.mime_type == PDF else _Picture(content, request.max_pixels)
    )
    pages = _pages(source.page_count, request.pages)
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


def label_font() -> ImageFont.FreeTypeFont:
    """What numbers are written with. Comes with Pillow; no module or file is opened later."""
    font = cast("ImageFont.FreeTypeFont", ImageFont.load_default(NUMBER_SIZE))
    ImageDraw.Draw(Image.new("RGB", (1, 1))).text((0, 0), "0", font=font, anchor="ma")
    return font


def _runs(shapes: Iterable[tuple[int, PageShape | None]]) -> list[PageRun]:
    """Pages and their shapes as runs, until there are too many."""
    runs: list[PageRun] = []
    for shape, group in islice(groupby(shapes, key=lambda found: found[1]), MAX_RUNS):
        runs.append(PageRun(pages=tuple(page for page, _ in group), shape=shape))
    return runs


def _tiles(  # noqa: PLR0913 -- the pages, the limits, the clock, and whom to tell
    source: _Pdf,
    pages: list[int],
    request: OverviewRequest,
    *,
    clock: Callable[[], float],
    started: float,
    announce: Callable[[bytes], None],
) -> Iterator[tuple[int, PageShape | None, Image.Image]]:
    """Pages, their shapes and tiles while another as slow as the slowest ends in time.

    At least the first.
    """
    slowest: float | None = None
    for page in pages:
        begun = clock()
        elapsed = begun - started
        if slowest is not None and (
            elapsed >= request.seconds or elapsed + slowest > request.drawn_seconds
        ):
            break
        announce(STARTED)
        shape, tile = source.tile(page)
        announce(DONE)
        slowest = max(slowest or 0.0, clock() - begun)
        yield page, shape, tile


def _sheet(tiles: list[tuple[int, Image.Image]], font: ImageFont.FreeTypeFont) -> Image.Image:
    """The tiles row by row, each with its page number below."""
    rows = math.ceil(len(tiles) / COLUMNS)
    sheet = Image.new("RGB", (min(len(tiles), COLUMNS) * CELL, rows * CELL), DESK)
    draw = ImageDraw.Draw(sheet)
    for place, (page, tile) in enumerate(tiles):
        row, column = divmod(place, COLUMNS)
        x, y = column * CELL, row * CELL
        sheet.paste(
            tile,
            (x + (CELL - tile.width) // 2, y + SPACE + (CELL - STRIP - SPACE - tile.height) // 2),
        )
        at = (x + CELL // 2, y + CELL - STRIP + SPACE // 2)
        draw.text(at, str(page), fill=INK, font=font, anchor="ma")
    return sheet


def overview(  # noqa: PLR0913 -- the file, the job, and one keyword per seam
    content: bytes,
    request: OverviewRequest,
    font: ImageFont.FreeTypeFont,
    *,
    clock: Callable[[], float] = time.monotonic,
    started: float | None = None,
    announce: Callable[[bytes], None] = lambda _: None,
    open_pdf: Callable[[bytes], pypdfium2.PdfDocument] = pypdfium2.PdfDocument,
) -> Overview:
    """Sizes, rotation and contact sheets of the pages ``request`` asks for, until it is late.

    At least one page is described and shown. ``announce`` is told how many pages there are, and of
    each page before it is loaded, because one that takes too long ends the process (see
    ``wire.started_page``). The layout goes on after the sheets, in the time left. The sheets share
    the bytes of a call: all of them are shown, at a lower quality if it must be.

    Raises:
        RenderFailed: The file cannot be read, or it has no such page.
    """
    begun = clock() if started is None else started
    source = _Pdf(content, open_pdf)
    pages = _pages(source.page_count, request.pages)
    announce(b"%d:" % source.page_count)
    most = min(request.max_sheets, math.ceil(len(pages) / TILES)) if request.sheets else 0
    shapes: list[tuple[int, PageShape | None]] = []
    sheets: list[ContactSheet] = []
    left = request.max_bytes
    tiles = _tiles(
        source, pages[: most * TILES], request, clock=clock, started=begun, announce=announce
    )
    for group in batched(tiles, TILES, strict=False):
        shapes += [(page, shape) for page, shape, _ in group]
        image = _sheet([(page, tile) for page, _, tile in group], font)
        budget = left // (most - len(sheets))
        whole = _webp(image, QUALITY)
        smaller, data = _fitted(image, whole, budget)
        sheets.append(
            ContactSheet(
                pages=tuple(page for page, _, _ in group),
                width=smaller.width,
                height=smaller.height,
                data=data,
                reduced=len(whole) > budget,
            )
        )
        left -= len(data)
    shown = len(shapes)

    def remaining() -> Iterator[tuple[int, PageShape | None]]:
        for place, page in enumerate(pages[shown:], start=shown):
            if place and clock() - begun >= request.seconds:
                break
            announce(READING)
            yield page, source.shape(page)

    runs = _runs(chain(shapes, remaining()))
    announce(DONE + b"\n")
    return Overview(
        page_count=source.page_count,
        runs=tuple(runs),
        layout_rest=tuple(pages[sum(len(run.pages) for run in runs) :]),
        sheets=tuple(sheets),
        rest=tuple(pages[shown:]) if request.sheets else (),
    )


def _plain(
    content: bytes, open_pdf: Callable[[bytes], pypdfium2.PdfDocument], *, of_source: bool
) -> pypdfium2.PdfDocument:
    """A PDF to take apart, which must not be encrypted.

    Without its form: that would change it. One that opens without a password but restricts what
    may be done with it counts as encrypted, too: it would lose that, or pass it on.
    """
    try:
        pdf = open_pdf(content)
        encrypted = pypdfium2.raw.FPDF_GetSecurityHandlerRevision(pdf) >= 0
    except pypdfium2.PdfiumError as error:
        reason: Reason = PDF_ERRORS.get(error.err_code, "damaged")
        raise RenderFailed(reason=reason, of_source=of_source) from error
    if encrypted:
        raise RenderFailed(reason="restricted", of_source=of_source)
    return pdf


def assemble(
    target: bytes,
    source: bytes,
    request: AssembleRequest,
    open_pdf: Callable[[bytes], pypdfium2.PdfDocument] = pypdfium2.PdfDocument,
) -> Assembled:
    """``target`` with the pages ``request`` asks for from ``source``.

    Raises:
        RenderFailed: A file cannot be read, has no such page, or the result is too large.
    """
    into = _plain(target, open_pdf, of_source=False)
    if len(into) != request.target_pages:
        raise RenderFailed(reason="differs")
    origin = _plain(source, open_pdf, of_source=True)
    source_pages = len(origin)
    try:
        pages = _pages(source_pages, request.pages)
    except RenderFailed as failure:
        failure.of_source = True
        raise
    if len(into) + len(pages) > MAX_PAGES:
        raise RenderFailed(reason="count")
    of_source = False
    try:
        # Importing next to a page that is not there crashes PDFium.
        sizes = _sizes(into)
        _require_sizes(sizes, of_source=False)
        _require_places(target, sizes, request.after_page, len(pages))
        of_source = True
        inserted = _sizes(origin, pages)
        _require_sizes(inserted, of_source=True)
        rotations, fields = _prepared(origin, pages)
        into.import_pages(origin, [page - 1 for page in pages], request.after_page)
        first = request.after_page
        draw_fields(source, into, [(pages[n] - 1, first + n) for n in fields])
        for place, rotation in enumerate(rotations, request.after_page):
            made = into[place]
            # What the page does not say itself it would take from the pages around it.
            made.set_rotation(rotation)
            made.set_cropbox(*made.get_cropbox())
            made.close()
        content = _saved(into)
        origin.close()
        sizes[request.after_page : request.after_page] = inserted
        if not _alike(content, sizes):
            raise RenderFailed(reason="damaged", of_source=True)
    except pypdfium2.PdfiumError as error:
        raise RenderFailed(reason="damaged", of_source=of_source) from error
    if len(content) > request.max_bytes:
        raise RenderFailed(reason="large")
    return Assembled(content=content, source_pages=source_pages, inserted=tuple(pages))


def _prepared(origin: pypdfium2.PdfDocument, pages: list[int]) -> tuple[list[int], list[int]]:
    """The rotations of pages whose fields are taken off, and the places of those that had some."""
    rotations, fields = list[int](), list[int]()
    for place, page in enumerate(pages):
        original = origin[page - 1]
        rotations.append(original.get_rotation())
        if has_fields(original):
            fields.append(place)
        original.close()
    # Afterwards: two pages can be one object.
    for place in fields:
        original = origin[pages[place] - 1]
        remove_fields(original)
        original.close()
    return rotations, fields


def _saved(pdf: pypdfium2.PdfDocument) -> bytes:
    """The file of a document, which is closed."""
    buffer = io.BytesIO()
    pdf.save(buffer)
    pdf.close()
    return buffer.getvalue()


def _alike(content: bytes, sizes: Sequence[tuple[float, float]]) -> bool:
    """Whether a file has pages of just these sizes; numbers are rounded when they are written."""
    pdf = pypdfium2.PdfDocument(content)
    found = _sizes(pdf)
    pdf.close()
    return len(found) == len(sizes) and all(
        math.isclose(a, b, rel_tol=1e-6, abs_tol=0.01)
        for one, other in zip(found, sizes, strict=True)
        for a, b in zip(one, other, strict=True)
    )


def _require_sizes(sizes: list[tuple[float, float]], *, of_source: bool) -> None:
    """Refuse pages so large that their size cannot be written down again."""
    if any(side > MAX_SIDE for size in sizes for side in size):
        raise RenderFailed(reason="size", of_source=of_source)


def _require_places(
    target: bytes, sizes: list[tuple[float, float]], after_page: int, pages: int
) -> None:
    """Insert marked pages into a copy, and look for each where it has to be in the saved file.

    In a page tree whose counts are wrong PDFium puts a page elsewhere or where no reader finds it,
    and the sizes of real pages cannot tell them apart.

    Raises:
        RenderFailed: A page is not where it has to be.
    """
    taken = {side for _, height in sizes for side in (math.floor(height), math.ceil(height))}
    height = next(side for side in itertools.count(100) if not {side - 1, side, side + 1} & taken)
    markers = [(100.0 + number, float(height)) for number in range(pages)]
    probe = pypdfium2.PdfDocument(target)
    for place, (width, _) in enumerate(markers, after_page):
        page = probe.new_page(width, height, place)
        page.set_rotation(0)
        page.set_cropbox(0, 0, width, height)
        page.close()
    if not _alike(_saved(probe), [*sizes[:after_page], *markers, *sizes[after_page:]]):
        raise RenderFailed(reason="damaged")


def count(content: bytes) -> Counted:
    """The pages of a PDF this worker made.

    Raises:
        RenderFailed: It cannot be read, or a page of it.
    """
    try:
        return Counted(len(_sizes(pypdfium2.PdfDocument(content))))
    except pypdfium2.PdfiumError as error:
        raise RenderFailed(reason="damaged") from error


def _sizes(
    pdf: pypdfium2.PdfDocument, pages: Iterable[int] | None = None
) -> list[tuple[float, float]]:
    """The sizes of pages (from 1); None: of all."""
    wanted = range(1, len(pdf) + 1) if pages is None else pages
    return [pdf.get_page_size(page - 1) for page in wanted]


def _work(
    job: Job, started: float, announce: Callable[[bytes], None]
) -> Callable[[bytes, bytes], Answered]:
    """What makes the answer of a job out of what follows it."""
    request = job.request
    if isinstance(request, OverviewRequest):
        font = label_font()
        return lambda first, rest: overview(
            first + rest, request, font, started=started, announce=announce
        )
    if isinstance(request, AssembleRequest):
        return lambda first, rest: assemble(first, rest, request)
    if isinstance(request, CountRequest):
        return lambda first, rest: count(first + rest)
    return lambda first, rest: render(first + rest, request)


def main(stdin: BinaryIO, stdout: BinaryIO, guard: Callable[[int, float], None]) -> int:
    """Render the job on ``stdin`` and write the answer to ``stdout``; returns the exit code.

    ``guard`` bounds the process by the memory and the seconds of the job.
    """
    started = time.monotonic()

    def announce(marks: bytes) -> None:
        _ = stdout.write(marks)
        stdout.flush()

    # All plugins now, and what LAB images are converted with: the process may not be able to open
    # their modules later.
    _ = Image.init()
    _ = Image.new("LAB", (1, 1)).convert("RGB")
    job = Job.model_validate_json(stdin.readline())
    work = _work(job, started, announce)
    try:
        guard(job.memory_bytes, job.seconds)
        # One of the two is empty where there is one file: adding them copies nothing.
        first = stdin.read(job.first_bytes)
        rest = stdin.read(job.content_bytes - len(first))
        if len(first) + len(rest) < job.content_bytes:
            return 1  # the server went away
        try:
            found: Answered | RenderFailed = work(first, rest)
        except RenderFailed as failure:
            found = failure
        _ = stdout.write(answer(found))
        stdout.flush()
    except MemoryError:
        return MEMORY_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.stdin.buffer, sys.stdout.buffer, limits.guard))
