"""Small PDFs and images the rendering tests make themselves (no binary fixtures)."""

import io
from typing import TYPE_CHECKING

from e2e.helpers import ENCRYPTED_PDF
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["ENCRYPTED_PDF"]

type Pixel = tuple[int, int, int]

RED, GREEN, BLUE, YELLOW = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)
# As shown: red and green on top, blue and yellow below.
QUADRANTS = (
    "1 0 0 rg 0 400 300 400 re f 0 1 0 rg 300 400 300 400 re f "
    "0 0 1 rg 0 0 300 400 re f 1 1 0 rg 300 0 300 400 re f"
)


def pdf_of(
    streams: Sequence[str],
    *,
    page: str = "/MediaBox [0 0 600 800]",
    catalog: str = "",
    extra: Sequence[str] = (),
) -> bytes:
    """A PDF with one page per content stream.

    `page` goes into every page dictionary, `catalog` into the catalog, and `extra` are more
    objects, numbered from 3 + 2 * pages.
    """
    kids = " ".join(f"{3 + 2 * n} 0 R" for n in range(len(streams)))
    objects = [
        f"<< /Type /Catalog /Pages 2 0 R {catalog} >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(streams)} >>",
    ]
    for number, stream in enumerate(streams):
        objects.append(f"<< /Type /Page /Parent 2 0 R /Contents {4 + 2 * number} 0 R {page} >>")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.extend(extra)
    out = b"%PDF-1.7\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return out + (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )


def filled_form() -> bytes:
    """A page whose only mark is the value of a form field without an appearance of its own."""
    look = "/DA (/Helv 24 Tf 0 g)"
    widget = (
        f"<< /Type /Annot /Subtype /Widget /FT /Tx /T (date) /V (23.11.2026) /F 4 {look} "
        "/Rect [100 600 400 650] /P 3 0 R >>"
    )
    return pdf_of(
        [""],
        page="/MediaBox [0 0 600 800] /Annots [5 0 R]",
        catalog=(
            "/AcroForm << /Fields [5 0 R] /NeedAppearances true /DA (/Helv 24 Tf 0 g) "
            "/DR << /Font << /Helv 6 0 R >> >> >>"
        ),
        extra=[
            widget,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        ],
    )


def quadrants(width: int = 600, height: int = 800, mode: str = "RGB") -> Image.Image:
    """The image of `QUADRANTS`."""
    image = Image.new("RGB", (width, height), RED)
    image.paste(GREEN, (width // 2, 0, width, height // 2))
    image.paste(BLUE, (0, height // 2, width // 2, height))
    image.paste(YELLOW, (width // 2, height // 2, width, height))
    return image.convert(mode)


def saved(image: Image.Image, kind: str, **options: object) -> bytes:
    """The file of `image` as `kind` (JPEG, PNG, TIFF, WEBP)."""
    buffer = io.BytesIO()
    image.save(buffer, kind, **options)
    return buffer.getvalue()


def opened(data: bytes) -> Image.Image:
    """A rendered page, decoded."""
    image = Image.open(io.BytesIO(data), formats=["WEBP"])
    _ = image.load()
    return image.convert("RGB")


def near(image: Image.Image, at: tuple[float, float], wanted: Pixel) -> bool:
    """Whether the pixel at the fraction `at` of the image is `wanted`, up to the lossy format."""
    x, y = int(at[0] * image.width), int(at[1] * image.height)
    found = image.getpixel((x, y))
    assert isinstance(found, tuple)
    return all(abs(a - b) < 60 for a, b in zip(found, wanted, strict=True))


def corners(image: Image.Image) -> list[str]:
    """The colours of the top left, top right, bottom left and bottom right quadrant."""
    names = {RED: "red", GREEN: "green", BLUE: "blue", YELLOW: "yellow"}
    places = [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)]
    return [
        next((name for pixel, name in names.items() if near(image, place, pixel)), "?")
        for place in places
    ]
