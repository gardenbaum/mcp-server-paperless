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
)
from mcp_server_paperless.services.page_ranges import parse_pages

type Count = Annotated[int, Field(ge=0)]
type Positive = Annotated[int, Field(ge=1)]


class Job(BaseModel):
    """What the child is sent, followed by the file."""

    request: Annotated[RenderRequest | OverviewRequest, Field(discriminator="kind")]
    content_bytes: int
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
    """What the child answers, followed by the images, or why there are none."""

    failure: Reason | None = None
    megapixels: float = 0.0
    # The server walks the pages of a specification: a claim must not make it walk for ever.
    page_count: Annotated[int, Field(ge=0, le=MAX_PAGES)] = 0
    images: tuple[_Image, ...] = ()
    runs: Annotated[tuple[_Run, ...], Field(max_length=MAX_RUNS)] = ()
    sheets: tuple[_Sheet, ...] = ()


def encoded(head: BaseModel) -> bytes:
    """A head as its line."""
    return head.model_dump_json().encode() + b"\n"


def answer(found: Rendered | Overview | RenderFailed) -> bytes:
    """What the child writes."""
    if isinstance(found, RenderFailed):
        head = _Answer(
            failure=found.reason, megapixels=found.megapixels, page_count=found.page_count
        )
        return encoded(head)
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


# What the child writes before its answer: a line for each page it begins to draw.
_ANNOUNCED = re.compile(rb"(?:[0-9]{1,6}\n)*")
# Nothing but such lines, the last of them perhaps not whole.
_DRAWING = re.compile(rb"(?:[0-9]{1,6}\n)*([0-9]{1,6})\n[0-9]{0,6}")


def started_page(output: bytes) -> int:
    """The page the child was at when its output ended; 0 if it was at none, or unknown."""
    found = _DRAWING.fullmatch(output)
    page = int(found[1]) if found else 0
    return page if page <= MAX_PAGES else 0


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
            reason=reason, page_count=head.page_count, megapixels=head.megapixels
        ) from error
    if head.failure:
        reason = "crashed" if head.failure == "pages" else head.failure
        raise RenderFailed(reason=reason, megapixels=head.megapixels)
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
    head, wanted, data = _read(_ANNOUNCED.sub(b"", output, count=1), request.pages)
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
