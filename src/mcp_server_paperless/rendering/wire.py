"""What the server and the child process send each other: a line of JSON, then bytes.

The server does not trust the answer: it holds no text, and what does not add up is a crash.
"""

from typing import Annotated

from pydantic import BaseModel, Field, ValidationError

from mcp_server_paperless.domain.rendering import (
    PageImage,
    Reason,
    Rendered,
    RenderFailed,
    RenderRequest,
)

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
    page_count: Count = 0
    images: tuple[_Image, ...] = ()
    rest: tuple[Count, ...] = ()


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
        rest=found.rest,
    )
    return encoded(head) + b"".join(image.data for image in found.images)


def answer_of(output: bytes) -> Rendered:
    """What the child answered.

    Raises:
        RenderFailed: The failure it answered, or the answer does not add up.
    """
    line, _, data = output.partition(b"\n")
    try:
        head = _Answer.model_validate_json(line)
    except ValidationError as error:
        raise RenderFailed(reason="crashed") from error
    if head.failure:
        raise RenderFailed(
            reason=head.failure, page_count=head.page_count, megapixels=head.megapixels
        )
    if not head.images or sum(image.size_bytes for image in head.images) != len(data):
        raise RenderFailed(reason="crashed")
    images: list[PageImage] = []
    start = 0
    for image in head.images:
        end = start + image.size_bytes
        images.append(
            PageImage(
                page=image.page,
                width=image.width,
                height=image.height,
                data=data[start:end],
                reduced=image.reduced,
            )
        )
        start = end
    return Rendered(page_count=head.page_count, images=tuple(images), rest=head.rest)
