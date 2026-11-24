"""Page sizes, rotation and numbered contact sheets: the worker, its answer and the child."""

import io
import json
import sys
import time
from hashlib import shake_256
from typing import TYPE_CHECKING

import pytest
from PIL import Image as PIL_Image
from PIL import ImageChops, ImageDraw

from mcp_server_paperless.domain.rendering import (
    MAX_IMAGE_BYTES,
    Overview,
    OverviewRequest,
    PageRun,
    PageShape,
    RenderFailed,
)
from mcp_server_paperless.rendering.runner import ChildRenderer
from mcp_server_paperless.rendering.wire import Job, answer, encoded, overview_of, started_page
from mcp_server_paperless.rendering.worker import label_font, main, overview
from rendering.files import ENCRYPTED_PDF, QUADRANTS, corners, opened, pdf_of
from rendering.test_main import Guard, pdf_with_pages
from rendering.test_runner import READ, WITHOUT_FONTS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

pytestmark = [pytest.mark.decision("decision-42"), pytest.mark.decision("decision-65")]

A4, A5 = "/MediaBox [0 0 595.276 841.89]", "/MediaBox [0 0 419.528 595.276]"
CELL = 392
FONT = label_font()
WINDOWS = sys.platform == "win32"


def asked(pages: str = "1-", **values: object) -> OverviewRequest:
    return OverviewRequest.model_validate({"pages": pages, **values})


def mixed(*pages: str) -> bytes:
    """A PDF whose pages have the given dictionaries."""
    content = pdf_of([""] * len(pages), page="/Each")
    for page in pages:
        content = content.replace(b"/Each", page.encode(), 1)
    return content


def cell(sheet: Image.Image, number: int) -> Image.Image:
    """The tile at `number` (from 0) of a full-size sheet."""
    x, y = number % 4 * CELL, number // 4 * CELL
    return sheet.crop((x, y, x + CELL, y + CELL))


def page_in(tile: Image.Image) -> Image.Image:
    """What is drawn above the number, without the grey around it."""
    white = tile.crop((0, 0, CELL, CELL - 40)).convert("L").point([0] * 241 + [255] * 15)
    return tile.crop(white.getbbox())


def ink(tile: Image.Image) -> int:
    """How many pixels of the strip with the number are dark."""
    return sum(tile.crop((0, CELL - 34, CELL, CELL)).convert("L").histogram()[:100])


def test_the_layout_is_runs_of_equal_pages_as_shown_with_the_stored_rotation() -> None:
    content = mixed(A4, A4, f"{A4} /Rotate 90", f"{A4} /Rotate 270", f"{A5} /Rotate 180", A5)

    found = overview(content, asked(sheets=False), FONT)

    portrait = PageShape(width_mm=210.0, height_mm=297.0, rotation=0)
    assert found.page_count == 6
    assert found.runs == (
        PageRun(pages=(1, 2), shape=portrait),
        PageRun(pages=(3,), shape=PageShape(width_mm=297.0, height_mm=210.0, rotation=90)),
        PageRun(pages=(4,), shape=PageShape(width_mm=297.0, height_mm=210.0, rotation=270)),
        PageRun(pages=(5,), shape=PageShape(width_mm=148.0, height_mm=210.0, rotation=180)),
        PageRun(pages=(6,), shape=PageShape(width_mm=148.0, height_mm=210.0, rotation=0)),
    )
    assert (found.layout_rest, found.sheets, found.rest) == ((), (), ())


def test_the_layout_follows_the_pages_asked_for() -> None:
    found = overview(mixed(A4, A5, A5, A4), asked("4,1-2", sheets=False), FONT)

    assert [run.pages for run in found.runs] == [(4, 1), (2,)]


def test_at_most_a_hundred_runs_and_the_rest_is_named() -> None:
    content = mixed(*[A4, A5] * 51)

    full = overview(content, asked("1-100", sheets=False), FONT)
    over = overview(content, asked(sheets=False), FONT)

    assert (len(full.runs), full.layout_rest) == (100, ())
    assert (len(over.runs), over.layout_rest) == (100, (101, 102))


@pytest.mark.parametrize(
    "page",
    [
        f"{A4} /CropBox [2000 2000 2100 2100]",  # nothing of the page is left
        "/MediaBox [0 0 0.01 0.01]",  # rounds to nothing
        "/MediaBox [0 0 100000000 10]",  # 35 km
        None,  # the object is missing
    ],
)
def test_a_page_that_cannot_be_read_is_a_run_without_shape_and_an_empty_tile(
    page: str | None,
) -> None:
    content = mixed(A4, page or A4, A4)
    if page is None:
        content = content.replace(b"/Kids [3 0 R 5 0 R", b"/Kids [3 0 R 99 0 R")

    found = overview(content, asked(), FONT)

    assert [run.pages for run in found.runs] == [(1,), (2,), (3,)]
    assert [run.shape is None for run in found.runs] == [False, True, False]
    sheet = opened(found.sheets[0].data)
    assert found.sheets[0].pages == (1, 2, 3)
    above = cell(sheet, 1).crop((0, 0, CELL, CELL - 40)).convert("L")
    assert sum(above.histogram()[240:]) == 0
    assert ink(cell(sheet, 1)) > 10


def test_a_sheet_shows_its_pages_whole_in_order_and_numbered() -> None:
    content = pdf_of([QUADRANTS, "", QUADRANTS])

    [sheet] = overview(content, asked("3,1-2"), FONT).sheets

    shown = opened(sheet.data)
    assert sheet.pages == (3, 1, 2)
    assert shown.size == (sheet.width, sheet.height) == (3 * CELL, CELL)
    assert not sheet.reduced
    first, second, third = (cell(shown, n) for n in range(3))
    for tile in (first, second):
        assert corners(page_in_colour(tile)) == ["red", "green", "blue", "yellow"]
    assert sum(page_in(third).convert("L").histogram()[:241]) == 0
    assert all(ink(tile) > 10 for tile in (first, second, third))


def page_in_colour(tile: Image.Image) -> Image.Image:
    """The page of `QUADRANTS`: what is not grey."""
    hue = tile.crop((0, 0, CELL, CELL - 40)).convert("HSV").getchannel("S")
    return tile.crop(hue.point([0] * 129 + [255] * 127).getbbox())


def test_every_page_has_a_number_of_its_own() -> None:
    numbered = overview(pdf_of([""] * 12), asked(), FONT).sheets[0]
    alone = [overview(pdf_of([""] * 12), asked(str(n)), FONT).sheets[0] for n in (7, 11)]

    sheet = opened(numbered.data)
    for place, single in zip((6, 10), alone, strict=True):
        assert abs(ink(cell(sheet, place)) - ink(cell(opened(single.data), 0))) < 40
    assert ink(cell(sheet, 11)) > ink(cell(sheet, 0))  # 12 and 1


def test_every_shape_of_page_is_shown_whole_in_its_tile() -> None:
    page = f"{A4} /Rotate 90"
    content = mixed(A4, page, "/MediaBox [0 0 9999 1]", "/MediaBox [0 0 1 9999]")

    sheet = opened(overview(content, asked(), FONT).sheets[0].data)

    portrait, landscape = page_in(cell(sheet, 0)), page_in(cell(sheet, 1))
    assert portrait.height == pytest.approx(portrait.width * 1.414, abs=10)
    assert landscape.width == pytest.approx(landscape.height * 1.414, abs=10)
    assert landscape.width > 370
    assert portrait.height > 340


@pytest.mark.parametrize(
    ("count", "sheets", "rest"),
    [
        (1, [(1, CELL, CELL)], ()),
        (5, [(5, 4 * CELL, 2 * CELL)], ()),
        (12, [(12, 4 * CELL, 3 * CELL)], ()),
        (13, [(12, 4 * CELL, 3 * CELL), (1, CELL, CELL)], ()),
        (60, [(12, 4 * CELL, 3 * CELL)] * 5, ()),
        (61, [(12, 4 * CELL, 3 * CELL)] * 5, (61,)),
    ],
)
def test_a_call_shows_sixty_pages_on_five_sheets(
    count: int, sheets: list[tuple[int, int, int]], rest: tuple[int, ...]
) -> None:
    found = overview(pdf_of([""] * count), asked(), FONT)

    assert [(len(s.pages), s.width, s.height) for s in found.sheets] == sheets
    assert [page for sheet in found.sheets for page in sheet.pages] == list(
        range(1, min(count, 60) + 1)
    )
    assert found.rest == rest
    assert max(4 * CELL, 3 * CELL) == 1568


def noise(pages: int) -> bytes:
    """Pages that hardly compress: an image of random bytes each."""
    data = shake_256(b"sheets").hexdigest(380 * 380 * 3)
    image = (
        "<< /Type /XObject /Subtype /Image /Width 380 /Height 380 /BitsPerComponent 8 "
        f"/ColorSpace /DeviceRGB /Filter /ASCIIHexDecode /Length {len(data)} >>\n"
        f"stream\n{data}\nendstream"
    )
    number = 3 + 2 * pages
    return pdf_of(
        ["600 0 0 800 0 0 cm /I Do"] * pages,
        page=f"/MediaBox [0 0 600 800] /Resources << /XObject << /I {number} 0 R >> >>",
        extra=[image],
    )


def test_sheets_that_hardly_compress_share_the_bytes_and_all_pages_are_shown() -> None:
    found = overview(noise(60), asked(), FONT)

    assert found.rest == ()
    assert sum(len(sheet.data) for sheet in found.sheets) <= MAX_IMAGE_BYTES
    assert all(sheet.reduced for sheet in found.sheets)
    assert [opened(s.data).size for s in found.sheets] == [
        (s.width, s.height) for s in found.sheets
    ]
    assert all(sheet.width >= 800 for sheet in found.sheets)


def test_a_single_sheet_may_have_all_the_bytes() -> None:
    content = noise(12)
    [alone] = overview(content, asked(), FONT).sheets
    size = len(alone.data)

    assert not alone.reduced
    [exact] = overview(content, asked(max_bytes=size), FONT).sheets
    [less] = overview(content, asked(max_bytes=size - 1), FONT).sheets
    assert (exact.reduced, less.reduced) == (False, True)
    assert len(less.data) < size


class Clock:
    """Moves on by `step` each time it is read, or by each step in turn (the last one stays)."""

    def __init__(self, *steps: float) -> None:
        self.now: float = 0.0
        self.steps: list[float] = list(steps)

    def __call__(self) -> float:
        self.now += self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        return self.now


def test_the_layout_alone_stops_after_its_time_and_says_what_is_left() -> None:
    found = overview(pdf_of([""] * 40), asked(sheets=False), FONT, clock=Clock(1.0))

    [run] = found.runs
    assert 19 <= len(run.pages) <= 21
    assert found.layout_rest == tuple(range(len(run.pages) + 1, 41))


def test_sheets_stop_after_their_time_between_two_tiles() -> None:
    # Two looks at the clock a tile: ten tiles in twenty seconds.
    found = overview(pdf_of([""] * 40), asked(seconds=30.0), FONT, clock=Clock(1.0))

    shown = [page for sheet in found.sheets for page in sheet.pages]
    assert 9 <= len(shown) <= 13
    assert found.rest == tuple(range(len(shown) + 1, 41))
    assert [page for run in found.runs for page in run.pages] == shown


def test_no_tile_is_started_that_as_slow_as_the_slowest_would_not_end_in_time() -> None:
    # (start, tile, look, tile, look, ...): tiles of 1, 9 and 1 seconds, then 16 have passed.
    clock = Clock(0.0, 0.0, 1.0, 0.0, 9.0, 0.0, 1.0, 5.0, 0.0)

    found = overview(pdf_of([""] * 10), asked(), FONT, clock=clock)

    # 16 + 9 is past the 24 that leave time for the encoding, though 16 is not past 20.
    assert [sheet.pages for sheet in found.sheets] == [(1, 2, 3)]
    assert found.rest == tuple(range(4, 11))


def test_a_first_tile_that_took_long_is_the_last_and_is_kept() -> None:
    found = overview(pdf_of([""] * 3), asked(), FONT, clock=Clock(0.0, 0.0, 13.0, 0.0))

    assert [sheet.pages for sheet in found.sheets] == [(1,)]
    assert found.rest == (2, 3)


def test_time_before_the_drawing_counts() -> None:
    found = overview(pdf_of([""] * 3), asked(), FONT, clock=Clock(0.0), started=-21.0)

    assert [sheet.pages for sheet in found.sheets] == [(1,)]


def test_a_file_slower_than_the_time_still_shows_one_page() -> None:
    found = overview(pdf_of([""] * 3), asked(), FONT, clock=Clock(100.0))
    layout = overview(pdf_of([""] * 3), asked(sheets=False), FONT, clock=Clock(100.0))

    assert [sheet.pages for sheet in found.sheets] == [(1,)]
    assert found.rest == (2, 3)
    assert ([run.pages for run in layout.runs], layout.layout_rest) == ([(1,)], (2, 3))


def test_every_tile_is_announced_before_it_is_drawn() -> None:
    announced: list[int] = []

    _ = overview(pdf_of([""] * 3), asked("3,1-2"), FONT, announce=announced.append)

    assert announced == [3, 1, 2]


def drawn_number(number: int) -> Image.Image:
    """The number as it should stand below its page, drawn here."""
    strip = PIL_Image.new("L", (CELL, 34), 200)
    ImageDraw.Draw(strip).text((CELL // 2, 3), str(number), fill=0, font=FONT, anchor="ma")
    return strip


def difference(tile: Image.Image, number: int) -> float:
    """How much the strip of a tile differs from the one of `number`: the mean per pixel."""
    strip = tile.crop((0, CELL - 34, CELL, CELL)).convert("L")
    found = ImageChops.difference(strip, drawn_number(number)).histogram()
    return sum(value * count for value, count in enumerate(found)) / sum(found)


@pytest.mark.parametrize("pages", ["1-12", "49-60", "61", "7,100-101"])
def test_the_number_below_a_page_is_its_own(pages: str) -> None:
    [sheet] = overview(pdf_of([""] * 101), asked(pages), FONT).sheets

    shown = opened(sheet.data)
    for place, page in enumerate(sheet.pages):
        tile = cell(shown, place)
        assert difference(tile, page) < 0.8
        for other in (page - 1, page + 1):
            assert difference(tile, other) > difference(tile, page) + 0.5


@pytest.mark.parametrize(
    ("content", "pages", "reason", "page_count"),
    [
        (ENCRYPTED_PDF, "1-", "encrypted", 0),
        (b"nothing", "1-", "damaged", 0),
        (pdf_of([""]), "2", "pages", 1),
        (pdf_of([""]), "", "pages", 1),
        (pdf_with_pages(200_001), "1-", "count", 0),
    ],
    ids=["encrypted", "damaged", "past the end", "no pages", "too many"],
)
def test_a_file_that_cannot_be_read_says_why(
    content: bytes, pages: str, reason: str, page_count: int
) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = overview(content, asked(pages), FONT)

    assert (raised.value.reason, raised.value.page_count) == (reason, page_count)


def test_a_file_with_very_many_pages_has_a_layout_in_time() -> None:
    begun = time.monotonic()
    found = overview(pdf_with_pages(20_000), asked(sheets=False), FONT)

    assert [len(run.pages) for run in found.runs] == [20_000]
    assert time.monotonic() - begun < 10


# --- the answer


def run(content: bytes, request: OverviewRequest) -> tuple[int, bytes, Guard]:
    head = Job(request=request, content_bytes=len(content), memory_bytes=1000, seconds=5.0)
    out, guard = io.BytesIO(), Guard()
    return main(io.BytesIO(encoded(head) + content), out, guard), out.getvalue(), guard


def test_the_answer_is_the_overview() -> None:
    content = mixed(*[A4] * 14, "/MediaBox [0 0 100000000 10]")

    code, out, guard = run(content, asked())

    found = overview_of(out, asked())
    assert (code, guard.calls) == (0, [(1000, 5.0)])
    assert found == overview(content, asked(), FONT)
    assert found.runs[-1] == PageRun(pages=(15,), shape=None)


def test_what_is_left_is_in_the_answer() -> None:
    sheets, layout = asked(max_sheets=1), asked(sheets=False)
    content = mixed(*[A4, A5] * 60)

    shown = overview_of(run(content, sheets)[1], sheets)
    described = overview_of(run(content, layout)[1], layout)

    assert (shown.layout_rest, shown.rest[0]) == ((), 13)
    assert [len(one.pages) for one in shown.runs] == [1] * 12
    assert described.layout_rest == tuple(range(101, 121))


def test_the_pages_are_announced_before_the_answer() -> None:
    out = run(pdf_of([""] * 3), asked("3,1"))[1]

    assert out.startswith(b'3\n1\n{"')
    assert started_page(b"3\n1\n") == 1
    assert started_page(b"3\n1") == 3  # the last line is not complete
    assert started_page(out) == 0  # it is not at a page any more


@pytest.mark.parametrize(
    "output", [b"", b"x\n", b"0\n", b"200001\n", b"-1\n", b"9" * 5000 + b"\n", b"1\nx"]
)
def test_an_announcement_that_is_none_names_no_page(output: bytes) -> None:
    assert started_page(output) == 0


def test_a_failure_is_an_answer() -> None:
    for content, reason in ((ENCRYPTED_PDF, "encrypted"), (b"", "damaged")):
        with pytest.raises(RenderFailed) as raised:
            _ = overview_of(run(content, asked())[1], asked())
        assert raised.value.reason == reason


WEBP = b"RIFF....WEBP"
SHAPE = {"width_mm": 210.0, "height_mm": 297.0, "rotation": 0}


def said(
    *,
    runs: Sequence[tuple[int, object]] = ((3, SHAPE),),
    sheets: Sequence[int] = (3,),
    count: int = 3,
    data: bytes = WEBP,
    **sheet: object,
) -> bytes:
    """An answer of the child, as far as it is true."""
    one = {"width": CELL, "height": CELL, "size_bytes": len(data), **sheet}
    head = {
        "page_count": count,
        "runs": [{"count": length, "shape": shape} for length, shape in runs],
        "sheets": [{"count": length, **one} for length in sheets],
    }
    return json.dumps(head).encode() + b"\n" + data * len(sheets)


def test_an_answer_that_adds_up_is_taken_and_the_pages_follow_from_the_request() -> None:
    found = overview_of(said(runs=[(1, SHAPE), (1, None)], sheets=[2]), asked("3,1-2"))
    layout = overview_of(said(runs=[(2, SHAPE)], sheets=[]), asked("3,1-2", sheets=False))

    assert [run.pages for run in found.runs] == [(3,), (1,)]
    assert (found.layout_rest, found.sheets[0].pages, found.rest) == ((), (3, 1), (2,))
    assert (layout.layout_rest, layout.rest) == ((2,), ())
    assert overview_of(said(sheets=[]), asked(sheets=False)) == Overview(
        page_count=3, runs=(PageRun(pages=(1, 2, 3), shape=PageShape.model_validate(SHAPE)),)
    )


@pytest.mark.parametrize(
    "answer",
    [
        b"",
        b"not json\n",
        said(runs=[]),
        said(runs=[(4, SHAPE)]),  # more pages than asked for
        said(runs=[(2, SHAPE), (2, SHAPE)]),
        said(runs=[(0, SHAPE), (3, SHAPE)]),
        said(runs=[(-1, SHAPE), (3, SHAPE)]),
        said(runs=[(1, {**SHAPE, "width_mm": 0.0})]),
        said(runs=[(1, {**SHAPE, "height_mm": 1e9})]),
        said(runs=[(1, {**SHAPE, "height_mm": float("nan")})]),
        said(runs=[(1, {**SHAPE, "width_mm": float("inf")})]),
        said(runs=[(1, {**SHAPE, "width_mm": "wide"})]),
        said(runs=[(1, {**SHAPE, "rotation": 45})]),
        said(sheets=[]),
        said(sheets=[2]),  # the layout is of the pages on the sheets
        said(runs=[(2, SHAPE)]),
        b"1\n" + said(sheets=[]),
        b"1\nx\n" + said(),
        said(sheets=[4]),  # more pages than asked for
        said(sheets=[2, 2]),
        said(sheets=[0, 3]),
        said(sheets=[-1, 3]),
        said(width=1569),
        said(height=0),
        said(size_bytes=13),
        said(size_bytes=11),
        said(size_bytes=-1),
        said() + b"x",
        said(data=b"GIF89a......"),
        said(count=0),
        said(count=200_001),
        b'{"failure": "pages", "page_count": 2}\n',  # the pages are right
        b'{"failure": "invented"}\n',
    ],
)
def test_an_answer_that_does_not_add_up_is_a_crash(answer: bytes) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = overview_of(answer, asked())

    assert raised.value.reason == "crashed"


@pytest.mark.parametrize(
    ("answer", "request_"),
    [
        (said(runs=[(1, SHAPE)] * 101, sheets=[], count=200), asked(sheets=False)),
        (said(sheets=[13], count=20), asked()),
        (said(sheets=[1, 1]), asked(max_sheets=1)),
        (said(), asked(max_bytes=11)),
        (said(), asked(sheets=False)),
        (said(runs=[(3, SHAPE)], count=3), asked("4")),
    ],
    ids=["runs", "tiles", "sheets", "bytes", "unasked", "pages"],
)
def test_more_than_a_call_may_have_is_a_crash(answer: bytes, request_: OverviewRequest) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = overview_of(answer, request_)

    assert raised.value.reason == "crashed"


def test_the_most_a_call_may_have_is_taken() -> None:
    full = overview_of(said(runs=[(1, SHAPE)] * 60, sheets=[12] * 5, count=200), asked())
    alone = asked(sheets=False)
    layout = overview_of(said(runs=[(1, SHAPE)] * 100, sheets=[], count=200), alone)

    assert (len(full.runs), len(full.sheets), full.rest[0]) == (60, 5, 61)
    assert (len(layout.runs), layout.layout_rest[0]) == (100, 101)
    assert len(overview_of(said(), asked(max_bytes=12)).sheets) == 1


def test_the_answer_of_a_failure_names_no_text() -> None:
    assert b"damaged" in answer(RenderFailed(reason="damaged"))


# --- the child


@pytest.mark.anyio
async def test_an_overview_is_made_in_a_child_process() -> None:
    content = pdf_of([QUADRANTS] * 13)

    assert await ChildRenderer().inspect(content, asked()) == overview(content, asked(), FONT)


@pytest.mark.anyio
@pytest.mark.skipif(WINDOWS, reason="there are no resource limits on Windows")
async def test_a_child_that_may_open_nothing_numbers_its_pages() -> None:
    renderer = ChildRenderer(command=(sys.executable, "-I", "-c", WITHOUT_FONTS))

    found = await renderer.inspect(pdf_of(["", ""]), asked())

    assert ink(opened(found.sheets[0].data).crop((0, 0, CELL, CELL))) > 10


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("import time\ntime.sleep(60)", "timeout"),
        ("while True: sys.stdout.buffer.write(b'x' * 65536)", "crashed"),
        ("raise SystemExit(73)", "memory"),
    ],
)
async def test_the_limits_of_the_child_hold_for_an_overview(code: str, reason: str) -> None:
    renderer = ChildRenderer(command=(sys.executable, "-I", "-c", READ + code), seconds=2.0)

    with pytest.raises(RenderFailed) as raised:
        _ = await renderer.inspect(b"x", asked())

    assert raised.value.reason == reason


@pytest.mark.anyio
async def test_the_real_child_stops_at_its_memory() -> None:
    # Less than the interpreter with both libraries takes: the watchdog ends it.
    with pytest.raises(RenderFailed) as raised:
        _ = await ChildRenderer(memory_bytes=10_000_000).inspect(pdf_of([""] * 60), asked())

    assert raised.value.reason == "memory"


PROGRESS = "sys.stdout.write('4\\n30\\n')\nsys.stdout.flush()\n"


@pytest.mark.anyio
@pytest.mark.parametrize("end", ["import time\ntime.sleep(60)", "raise SystemExit(74)"])
async def test_a_child_over_its_time_names_the_page_it_was_at(end: str) -> None:
    code = READ + PROGRESS + end
    renderer = ChildRenderer(command=(sys.executable, "-I", "-c", code), seconds=2.0)

    with pytest.raises(RenderFailed) as raised:
        _ = await renderer.inspect(b"x", asked())

    assert (raised.value.reason, raised.value.page) == ("timeout", 30)


@pytest.mark.anyio
async def test_a_child_over_its_time_before_any_page_names_none() -> None:
    code = READ + "import time\ntime.sleep(60)"
    renderer = ChildRenderer(command=(sys.executable, "-I", "-c", code), seconds=2.0)

    with pytest.raises(RenderFailed) as raised:
        _ = await renderer.inspect(b"x", asked())

    assert (raised.value.reason, raised.value.page) == ("timeout", 0)
