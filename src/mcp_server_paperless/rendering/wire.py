"""What the server and the child process send each other: a line of JSON, then bytes.

The server does not trust the answer: it holds no text, and what does not add up is a crash.
"""

import re
from itertools import accumulate
from typing import Annotated

from pydantic import BaseModel, Field, ValidationError

from mcp_server_paperless.domain.errors import Validation
from mcp_server_paperless.domain.rendering import (
    DEFAULT_SIZE,
    MAX_PAGES,
    MAX_RUNS,
    TILES,
    Assembled,
    AssembleRequest,
    ContactSheet,
    Overview,
    OverviewRequest,
    PageImage,
    PageRun,
    PageShape,
    Reason,
    Rendered,
    RenderFailed,
    RenderRequest,
    SlowPage,
)
from mcp_server_paperless.services.page_ranges import parse_pages

type Count = Annotated[int, Field(ge=0)]
type Positive = Annotated[int, Field(ge=1)]


class Job(BaseModel):
    """What the child is sent, followed by the file, or the target and the source of a new one."""

    request: Annotated[
        RenderRequest | OverviewRequest | AssembleRequest, Field(discriminator="kind")
    ]
    content_bytes: int
    first_bytes: int = 0
    memory_bytes: int
    seconds: float


class _Image(BaseModel):
    page: Count
    width: Count
    height: Count
    size_bytes: Count
    reduced: bool = False


class _Run(BaseModel):
    count: Positive
    shape: PageShape | None


class _Sheet(BaseModel):
    count: Annotated[int, Field(ge=1, le=TILES)]
    width: Annotated[int, Field(ge=1, le=DEFAULT_SIZE)]
    height: Annotated[int, Field(ge=1, le=DEFAULT_SIZE)]
    size_bytes: Count
    reduced: bool = False


class _Answer(BaseModel):
    """What the child answers, followed by the images or the file, or why there are none."""

    failure: Reason | None = None
    of_source: bool = False
    megapixels: float = 0.0
    # The server walks the pages of a specification: a claim must not make it walk for ever.
    page_count: Annotated[int, Field(ge=0, le=MAX_PAGES)] = 0
    images: tuple[_Image, ...] = ()
    runs: Annotated[tuple[_Run, ...], Field(max_length=MAX_RUNS)] = ()
    sheets: tuple[_Sheet, ...] = ()


def encoded(head: BaseModel) -> bytes:
    """A head as its line."""
    return head.model_dump_json().encode() + b"\n"


def answer(found: Rendered | Overview | Assembled | RenderFailed) -> bytes:
    """What the child writes."""
    if isinstance(found, RenderFailed):
        head = _Answer(
            failure=found.reason,
            megapixels=found.megapixels,
            page_count=found.page_count,
            of_source=found.of_source,
        )
        return encoded(head)
    if isinstance(found, Assembled):
        return encoded(_Answer(page_count=found.source_pages)) + found.content
    if isinstance(found, Overview):
        head = _Answer(
            page_count=found.page_count,
            runs=tuple(_Run(count=len(run.pages), shape=run.shape) for run in found.runs),
            sheets=tuple(
                _Sheet(
                    count=len(sheet.pages),
                    width=sheet.width,
                    height=sheet.height,
                    size_bytes=len(sheet.data),
                    reduced=sheet.reduced,
                )
                for sheet in found.sheets
            ),
        )
        return encoded(head) + b"".join(sheet.data for sheet in found.sheets)
    head = _Answer(
        page_count=found.page_count,
        images=tuple(
            _Image(
                page=image.page,
                width=image.width,
                height=image.height,
                size_bytes=len(image.data),
                reduced=image.reduced,
            )
            for image in found.images
        ),
    )
    return encoded(head) + b"".join(image.data for image in found.images)


# What the child writes before the answer to an ``OverviewRequest``: how many pages the file has,
# then a mark when it starts to draw the next of the pages asked for, or to read its size only, and
# one when it is at none.
STARTED = b"."
READING = b","
DONE = b"-"
_ANNOUNCED = re.compile(rb"[0-9]{1,6}:[.,-]*\n")
_AT_A_PAGE = re.compile(rb"([0-9]{1,6}):([.,-]*[.,])")


def started_page(output: bytes, request: OverviewRequest) -> SlowPage | None:
    """The page the child was at when its output ended; None if it was at none, or it is unknown.

    The child counts, the server names: it can be at no other page than one it was asked for.
    """
    found = _AT_A_PAGE.fullmatch(output)
    if found is None or not 0 < int(found[1]) <= MAX_PAGES:
        return None
    try:
        wanted = parse_pages(request.pages, page_count=int(found[1]))
    except Validation:
        return None
    marks = found[2]
    place = marks.count(STARTED) + marks.count(READING)
    if place > len(wanted):
        return None
    return SlowPage(wanted[place - 1], drawing=marks.endswith(STARTED), alone=len(wanted) == 1)


def _is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _read(output: bytes, pages: str) -> tuple[_Answer, list[int], bytes]:
    """The head of an answer, the pages that were asked for, and the bytes after the head.

    Raises:
        RenderFailed: The failure it answered, or the answer does not fit the pages.
    """
    line, _, data = output.partition(b"\n")
    try:
        head = _Answer.model_validate_json(line)
    except ValidationError as error:
        raise RenderFailed(reason="crashed") from error
    try:
        wanted = parse_pages(pages, page_count=head.page_count)
    except Validation as error:
        # Right if the child says so too; without a page count it is another failure.
        reason = head.failure or "crashed"
        raise RenderFailed(
            reason=reason,
            page_count=head.page_count,
            megapixels=head.megapixels,
            of_source=head.of_source,
        ) from error
    if head.failure:
        reason = "crashed" if head.failure == "pages" else head.failure
        raise RenderFailed(reason=reason, megapixels=head.megapixels, of_source=head.of_source)
    return head, wanted, data


def _images(data: bytes, sizes: list[int]) -> list[bytes]:
    """The images that ``data`` holds one after the other."""
    ends = list(accumulate(sizes))
    images = [data[end - size : end] for size, end in zip(sizes, ends, strict=True)]
    if not all(_is_webp(image) for image in images):
        raise RenderFailed(reason="crashed")
    return images


def answer_of(output: bytes, request: RenderRequest) -> Rendered:
    """What the child answered to ``request``.

    Raises:
        RenderFailed: The failure it answered, or the answer does not fit the request.
    """
    head, wanted, data = _read(output, request.pages)
    shown = [image.page for image in head.images]
    sizes = [image.size_bytes for image in head.images]
    sides = [side for image in head.images for side in (image.width, image.height)]
    if not (
        1 <= len(shown) <= request.max_images
        and shown == wanted[: len(shown)]
        and sum(sizes) == len(data) <= request.max_bytes
        and all(1 <= side <= request.size for side in sides)
    ):
        raise RenderFailed(reason="crashed")
    images = tuple(
        PageImage(
            page=image.page,
            width=image.width,
            height=image.height,
            data=content,
            reduced=image.reduced,
        )
        for image, content in zip(head.images, _images(data, sizes), strict=True)
    )
    return Rendered(page_count=head.page_count, images=images, rest=tuple(wanted[len(shown) :]))


def overview_of(output: bytes, request: OverviewRequest) -> Overview:
    """What the child answered to ``request``, after the pages it began to draw.

    The child counts pages, the server names them: it cannot answer others than it was asked for.

    Raises:
        RenderFailed: The failure it answered, or the answer does not fit the request.
    """
    announced = _ANNOUNCED.match(output)
    head, wanted, data = _read(output[announced.end() if announced else 0 :], request.pages)
    described = list(accumulate(run.count for run in head.runs))
    shown = list(accumulate(sheet.count for sheet in head.sheets))
    sizes = [sheet.size_bytes for sheet in head.sheets]
    if not (
        described
        and described[-1] <= len(wanted)
        and len(shown) <= request.max_sheets
        and (not shown or shown[-1] <= len(wanted))
        and bool(shown) is request.sheets
        and sum(sizes) == len(data) <= request.max_bytes
    ):
        raise RenderFailed(reason="crashed")
    return Overview(
        page_count=head.page_count,
        runs=tuple(
            PageRun(pages=tuple(wanted[end - run.count : end]), shape=run.shape)
            for run, end in zip(head.runs, described, strict=True)
        ),
        layout_rest=tuple(wanted[described[-1] :]),
        sheets=tuple(
            ContactSheet(
                pages=tuple(wanted[end - sheet.count : end]),
                width=sheet.width,
                height=sheet.height,
                data=image,
                reduced=sheet.reduced,
            )
            for sheet, end, image in zip(head.sheets, shown, _images(data, sizes), strict=True)
        ),
        rest=tuple(wanted[shown[-1] :]) if shown else (),
    )


def assembled_of(output: bytes, request: AssembleRequest) -> Assembled:
    """What the child answered to ``request``; whether it holds its pages is another question.

    Raises:
        RenderFailed: The failure it answered, or the answer does not fit the request.
    """
    head, wanted, data = _read(output, request.pages)
    if not data.startswith(b"%PDF-") or len(data) > request.max_bytes:
        raise RenderFailed(reason="crashed")
    return Assembled(content=data, source_pages=head.page_count, inserted=tuple(wanted))
