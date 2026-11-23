"""The renderer itself, in the process of the test: pages of PDFs and images."""

import itertools
from hashlib import shake_256

import pytest
from PIL import ExifTags, Image

from mcp_server_paperless.domain.rendering import RenderFailed, RenderRequest
from mcp_server_paperless.rendering.worker import render
from rendering.files import (
    BLUE,
    ENCRYPTED_PDF,
    GREEN,
    QUADRANTS,
    RED,
    YELLOW,
    corners,
    filled_form,
    near,
    opened,
    pdf_of,
    quadrants,
    saved,
)

PDF = "application/pdf"


def request(mime_type: str = PDF, pages: str = "1", **values: object) -> RenderRequest:
    given = {"size": 400, **values}
    return RenderRequest.model_validate({"mime_type": mime_type, "pages": pages, **given})


def test_a_page_is_a_webp_image_whose_long_side_is_the_size() -> None:
    found = render(pdf_of([QUADRANTS]), request())

    [image] = found.images
    assert (found.page_count, found.rest) == (1, ())
    assert (image.page, image.width, image.height) == (1, 300, 400)
    shown = opened(image.data)
    assert shown.size == (300, 400)
    assert corners(shown) == ["red", "green", "blue", "yellow"]


@pytest.mark.parametrize("size", [256, 1568, 2576])
def test_the_long_side_is_exactly_the_size_for_any_page_shape(size: int) -> None:
    for box in ("0 0 595.276 841.89", "0 0 841.89 595.276", "0 0 14400000 14400000", "0 0 9999 1"):
        [image] = render(
            pdf_of([""], page=f"/MediaBox [{box}]"), request(size=size, region=None)
        ).images

        assert max(image.width, image.height) == size
        assert min(image.width, image.height) >= 1


def test_the_region_is_enlarged_to_the_size() -> None:
    found = render(pdf_of([QUADRANTS]), request(region=(0.25, 0.5, 0.75, 1.0)))

    [image] = found.images
    shown = opened(image.data)
    assert (image.width, image.height) == shown.size == (300, 400)
    assert near(shown, (0.25, 0.5), BLUE)
    assert near(shown, (0.75, 0.5), YELLOW)


def edges(data: bytes) -> int:
    """How often the middle row of an image changes between dark and light."""
    shown = opened(data).convert("L")
    row = shown.crop((0, shown.height // 2, shown.width, shown.height // 2 + 1)).tobytes()
    dark = [value < 128 for value in row]
    return sum(a != b for a, b in itertools.pairwise(dark))


def test_a_region_shows_detail_the_whole_page_does_not_have_at_the_same_size() -> None:
    # Thirty lines of 0.5 pt, 1 pt apart: half a pixel each on the whole page.
    lines = " ".join(f"{300 + n} 0 0.5 800 re" for n in range(30))
    page = pdf_of([f"0 0 0 rg {lines} f"])

    [whole] = render(page, request()).images
    [part] = render(page, request(region=(0.5, 0.48, 0.55, 0.52))).images

    assert edges(whole.data) < 10
    assert edges(part.data) >= 55


def test_a_rotated_page_and_its_region_are_as_shown() -> None:
    page = pdf_of([QUADRANTS], page="/MediaBox [0 0 600 800] /Rotate 90")

    [whole] = render(page, request()).images
    [part] = render(page, request(region=(0.5, 0.0, 1.0, 0.5))).images

    assert (whole.width, whole.height) == (400, 300)
    assert corners(opened(whole.data)) == ["blue", "red", "yellow", "green"]
    assert corners(opened(part.data)) == ["red"] * 4


def test_the_value_of_a_form_field_is_drawn() -> None:
    [image] = render(filled_form(), request(size=800)).images

    dark = opened(image.data).convert("L").histogram()[:100]
    assert sum(dark) > 100


def test_pages_come_in_the_order_written() -> None:
    document = pdf_of([QUADRANTS, "", QUADRANTS])

    found = render(document, request(pages="3,1-2"))

    assert found.page_count == 3
    assert [image.page for image in found.images] == [3, 1, 2]
    assert corners(opened(found.images[0].data)) == ["red", "green", "blue", "yellow"]


def test_a_page_past_the_end_is_refused_with_the_page_count() -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = render(pdf_of(["", ""]), request(pages="2-3"))

    assert (raised.value.reason, raised.value.page_count) == ("pages", 2)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (ENCRYPTED_PDF, "encrypted"),
        (pdf_of([QUADRANTS])[:40], "damaged"),
        (b"", "damaged"),
        (bytes(range(256)) * 40, "damaged"),
        (b"%PDF-1.7\ntrailer << /Root 1 0 R >>\n%%EOF", "damaged"),
        (pdf_of([]), "damaged"),
    ],
    ids=["encrypted", "cut", "empty", "noise", "no objects", "no pages"],
)
def test_a_pdf_that_cannot_be_opened_says_why(content: bytes, reason: str) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = render(content, request())

    assert raised.value.reason == reason


IMAGES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/tiff": "TIFF", "image/webp": "WEBP"}
UPRIGHT = ["red", "green", "blue", "yellow"]


@pytest.mark.parametrize("mime_type", IMAGES)
def test_an_image_is_one_page(mime_type: str) -> None:
    found = render(saved(quadrants(), IMAGES[mime_type]), request(mime_type))

    [image] = found.images
    assert (found.page_count, image.page, image.width, image.height) == (1, 1, 300, 400)
    assert corners(opened(image.data)) == UPRIGHT


@pytest.mark.parametrize("mime_type", IMAGES)
def test_the_region_of_an_image_is_enlarged(mime_type: str) -> None:
    content = saved(quadrants(), IMAGES[mime_type])

    [image] = render(content, request(mime_type, region=(0.25, 0.5, 0.75, 1.0))).images

    shown = opened(image.data)
    assert (image.width, image.height) == (300, 400)
    assert near(shown, (0.25, 0.5), BLUE)
    assert near(shown, (0.75, 0.5), YELLOW)


@pytest.mark.parametrize("orientation", range(1, 9))
@pytest.mark.parametrize("mime_type", IMAGES)
def test_a_turned_image_and_its_region_are_as_shown(mime_type: str, orientation: int) -> None:
    upright = quadrants()
    # The file holds the image turned the other way, and says so.
    undo: dict[int, Image.Transpose] = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_90,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_270,
    }
    stored = upright if orientation == 1 else upright.transpose(undo[orientation])
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    content = saved(stored, IMAGES[mime_type], exif=exif)

    [whole] = render(content, request(mime_type)).images
    [part] = render(content, request(mime_type, region=(0.6, 0.1, 0.9, 0.2))).images

    assert (whole.width, whole.height) == (300, 400)
    assert corners(opened(whole.data)) == UPRIGHT
    assert (part.width, part.height) == (400, 178)
    assert corners(opened(part.data)) == ["green"] * 4


def test_the_frames_of_a_tiff_are_its_pages() -> None:
    first, second, third = (Image.new("RGB", (60, 80), colour) for colour in (RED, BLUE, GREEN))
    content = saved(first, "TIFF", save_all=True, append_images=[second, third])

    found = render(content, request("image/tiff", pages="3,1-2"))

    assert found.page_count == 3
    assert [image.page for image in found.images] == [3, 1, 2]
    assert [corners(opened(image.data))[0] for image in found.images] == ["green", "red", "blue"]


@pytest.mark.parametrize(
    ("mode", "white", "kind"),
    [
        ("1", 255, "TIFF"),
        ("L", 255, "PNG"),
        ("P", (255, 255, 255), "PNG"),
        ("CMYK", (0, 0, 0, 0), "JPEG"),
        ("I;16", 65535, "PNG"),
        ("I;16", 65535, "TIFF"),
        ("LA", (0, 0), "PNG"),
        ("RGBA", (0, 0, 0, 0), "PNG"),
        ("RGBA", (0, 0, 0, 0), "WEBP"),
    ],
)
def test_every_mode_becomes_a_picture_and_transparency_white(
    mode: str, white: int | tuple[int, ...], kind: str
) -> None:
    page = Image.new("RGB" if mode == "P" else mode, (60, 80), white).convert(mode)
    mime_type = next(name for name, value in IMAGES.items() if value == kind)

    [image] = render(saved(page, kind), request(mime_type)).images

    assert corners_are(opened(image.data), (255, 255, 255))


def test_a_grey_16_bit_image_keeps_its_greys() -> None:
    page = Image.new("I;16", (60, 80), 128 * 257)

    [image] = render(saved(page, "PNG"), request("image/png")).images

    assert corners_are(opened(image.data), (128, 128, 128))


def test_a_palette_image_with_a_transparent_colour_is_white_there() -> None:
    page = Image.new("P", (60, 80), 0)
    page.putpalette([0, 0, 0] + [255, 0, 0] * 255)

    [image] = render(saved(page, "PNG", transparency=0), request("image/png")).images

    assert corners_are(opened(image.data), (255, 255, 255))


def corners_are(image: Image.Image, colour: tuple[int, int, int]) -> bool:
    return all(near(image, place, colour) for place in [(0.1, 0.1), (0.9, 0.1), (0.5, 0.9)])


def test_an_image_over_the_pixel_limit_is_refused_by_its_header() -> None:
    content = saved(Image.new("1", (1001, 1000)), "PNG")

    with pytest.raises(RenderFailed) as raised:
        _ = render(content, request("image/png", max_pixels=1_000_000))

    assert (raised.value.reason, raised.value.megapixels) == ("pixels", 1.001)


def test_an_image_at_the_pixel_limit_is_shown() -> None:
    content = saved(Image.new("1", (1000, 1000)), "PNG")

    assert render(content, request("image/png", max_pixels=1_000_000)).images


def test_the_pixel_limit_is_this_servers_also_above_pillows_own() -> None:
    # 400 megapixels, of which Pillow reads only the header.
    content = saved(Image.new("1", (20_000, 20_000)), "PNG")

    with pytest.raises(RenderFailed) as raised:
        _ = render(content, request("image/png"))

    assert (raised.value.reason, raised.value.megapixels) == ("pixels", 400.0)


@pytest.mark.parametrize(
    ("mime_type", "content"),
    [
        *((mime_type, saved(quadrants(), kind)[:400]) for mime_type, kind in IMAGES.items()),
        *((mime_type, b"") for mime_type in IMAGES),
        *((mime_type, bytes(range(256)) * 40) for mime_type in IMAGES),
        ("image/png", saved(quadrants(), "BMP")),
    ],
)
def test_a_broken_image_is_damaged(mime_type: str, content: bytes) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = render(content, request(mime_type))

    assert raised.value.reason == "damaged"


def test_a_large_jpeg_is_decoded_at_a_fraction_of_its_size() -> None:
    # draft() decodes 1/8 of each side: the pixels decoded stay far below those of the file.
    content = saved(quadrants(4000, 4000), "JPEG")

    [image] = render(content, request("image/jpeg")).images

    assert (image.width, image.height) == (400, 400)
    assert corners(opened(image.data)) == UPRIGHT


def noise(width: int = 400, height: int = 400) -> Image.Image:
    """An image that hardly compresses."""
    return Image.frombytes("RGB", (width, height), shake_256(b"noise").digest(width * height * 3))


def size_of(content: bytes, mime_type: str = "image/png") -> int:
    [image] = render(content, request(mime_type)).images
    return len(image.data)


def test_a_page_at_the_byte_budget_is_whole_and_one_byte_over_it_loses_quality() -> None:
    content = saved(noise(), "PNG")
    full = size_of(content)

    [whole] = render(content, request("image/png", max_bytes=full)).images
    [reduced] = render(content, request("image/png", max_bytes=full - 1)).images

    assert (whole.reduced, len(whole.data)) == (False, full)
    assert reduced.reduced
    assert len(reduced.data) < full
    assert (reduced.width, reduced.height) == (400, 400)


def test_a_page_that_does_not_fit_at_lower_quality_loses_pixels_until_it_fits() -> None:
    content = saved(noise(), "PNG")

    [image] = render(content, request("image/png", max_bytes=9_000)).images

    assert image.reduced
    assert len(image.data) <= 9_000
    assert (image.width, image.height) == opened(image.data).size
    assert image.width in {300, 225, 168, 126}


def test_pages_that_do_not_fit_into_the_bytes_are_the_rest() -> None:
    pages = [noise(), noise(), noise()]
    content = saved(pages[0], "TIFF", save_all=True, append_images=pages[1:])
    one = size_of(content, "image/tiff")

    two = render(content, request("image/tiff", pages="1-3", max_bytes=2 * one))
    less = render(content, request("image/tiff", pages="1-3", max_bytes=2 * one - 1))

    assert ([image.page for image in two.images], two.rest) == ([1, 2], (3,))
    assert ([image.page for image in less.images], less.rest) == ([1], (2, 3))
    assert not any(image.reduced for image in (*two.images, *less.images))


def test_pages_over_the_number_of_images_are_the_rest_and_are_not_drawn() -> None:
    document = pdf_of([""] * 7)

    found = render(document, request(pages="2-", max_images=5))

    assert [image.page for image in found.images] == [2, 3, 4, 5, 6]
    assert found.rest == (7,)
    assert render(document, request(pages="3-7", max_images=5)).rest == ()
