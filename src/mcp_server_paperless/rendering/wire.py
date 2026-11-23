"""What the server and the child process send each other: a line of JSON, then bytes.

The server does not trust the answer: it holds no text, and what does not add up is a crash.
"""

from typing import Annotated

from pydantic import BaseModel, Field, ValidationError

from mcp_server_paperless.domain.errors import Validation
from mcp_server_paperless.domain.rendering import (
    MAX_PAGES,
    PageImage,
    Reason,
    Rendered,
    RenderFailed,
    RenderRequest,
)
from mcp_server_paperless.services.page_ranges import parse_pages

type Count = Annotated[int, Field(ge=0)]


class Job(BaseModel):
    """What the child is sent, followed by the file."""

    request: RenderRequest
    content_bytes: int
    memory_bytes: int
    seconds: float


class _Image(BaseModel):
    page: Count
    width: Count
    height: Count
    size_bytes: Count
    reduced: bool = False


class _Answer(BaseModel):
    """What the child answers, followed by the images, or why there are none."""

    failure: Reason | None = None
    megapixels: float = 0.0
    # The server walks the pages of a specification: a claim must not make it walk for ever.
    page_count: Annotated[int, Field(ge=0, le=MAX_PAGES)] = 0
    images: tuple[_Image, ...] = ()


def encoded(head: BaseModel) -> bytes:
    """A head as its line."""
    return head.model_dump_json().encode() + b"\n"


def answer(found: Rendered | RenderFailed) -> bytes:
    """What the child writes."""
    if isinstance(found, RenderFailed):
        head = _Answer(
            failure=found.reason, megapixels=found.megapixels, page_count=found.page_count
        )
        return encoded(head)
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


def _is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def answer_of(output: bytes, request: RenderRequest) -> Rendered:
    """What the child answered to ``request``.

    Raises:
        RenderFailed: The failure it answered, or the answer does not fit the request.
    """
    line, _, data = output.partition(b"\n")
    try:
        head = _Answer.model_validate_json(line)
    except ValidationError as error:
        raise RenderFailed(reason="crashed") from error
    try:
        wanted = parse_pages(request.pages, page_count=head.page_count)
    except Validation as error:
        # Right if the child says so too; without a page count it is another failure.
        reason = head.failure or "crashed"
        raise RenderFailed(
            reason=reason, page_count=head.page_count, megapixels=head.megapixels
        ) from error
    if head.failure:
        reason = "crashed" if head.failure == "pages" else head.failure
        raise RenderFailed(reason=reason, megapixels=head.megapixels)
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
    images: list[PageImage] = []
    start = 0
    for image in head.images:
        content = data[start : start + image.size_bytes]
        if not _is_webp(content):
            raise RenderFailed(reason="crashed")
        images.append(
            PageImage(
                page=image.page,
                width=image.width,
                height=image.height,
                data=content,
                reduced=image.reduced,
            )
        )
        start += image.size_bytes
    return Rendered(
        page_count=head.page_count, images=tuple(images), rest=tuple(wanted[len(shown) :])
    )
