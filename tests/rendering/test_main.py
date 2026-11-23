"""The program of the child process, with its streams as arguments."""

import io
import json
from typing import override

import pytest

from mcp_server_paperless.domain.rendering import RenderFailed, RenderRequest
from mcp_server_paperless.rendering.limits import MEMORY_EXIT
from mcp_server_paperless.rendering.wire import Job, answer_of, encoded
from mcp_server_paperless.rendering.worker import main
from rendering.files import ENCRYPTED_PDF, QUADRANTS, corners, opened, pdf_of

REQUEST = RenderRequest(mime_type="application/pdf", pages="1-2", size=400)


class Guard:
    """Records what the worker would be bounded by."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, float]] = []

    def __call__(self, memory_bytes: int, seconds: float) -> None:
        self.calls.append((memory_bytes, seconds))


def job(content: bytes, request: RenderRequest = REQUEST, *, claimed: int | None = None) -> bytes:
    size = len(content) if claimed is None else claimed
    head = Job(request=request, content_bytes=size, memory_bytes=1000, seconds=5.0)
    return encoded(head) + content


def run(sent: bytes) -> tuple[int, bytes, Guard]:
    out, guard = io.BytesIO(), Guard()
    return main(io.BytesIO(sent), out, guard), out.getvalue(), guard


def test_the_answer_holds_the_images_of_the_pages() -> None:
    code, out, guard = run(job(pdf_of([QUADRANTS, "", ""])))

    found = answer_of(out, REQUEST)
    assert (code, guard.calls) == (0, [(1000, 5.0)])
    assert (found.page_count, found.rest) == (3, ())
    assert [(i.page, i.width, i.height) for i in found.images] == [(1, 300, 400), (2, 300, 400)]
    assert corners(opened(found.images[0].data)) == ["red", "green", "blue", "yellow"]


def test_the_pages_that_did_not_fit_are_in_the_answer() -> None:
    request = REQUEST.model_copy(update={"max_images": 1})

    assert answer_of(run(job(pdf_of(["", ""]), request))[1], request).rest == (2,)


@pytest.mark.parametrize(
    ("content", "reason", "page_count"),
    [(ENCRYPTED_PDF, "encrypted", 0), (b"nothing", "damaged", 0), (pdf_of([""]), "pages", 1)],
)
def test_a_failure_is_an_answer(content: bytes, reason: str, page_count: int) -> None:
    code, out, _ = run(job(content))

    with pytest.raises(RenderFailed) as raised:
        _ = answer_of(out, REQUEST)
    assert (code, raised.value.reason, raised.value.page_count) == (0, reason, page_count)


def test_a_file_that_ends_early_is_no_answer() -> None:
    content = pdf_of(["", ""])

    assert run(job(content, claimed=len(content) + 1))[:2] == (1, b"")


def test_no_memory_left_is_its_own_exit_code() -> None:
    class Full(Guard):
        @override
        def __call__(self, memory_bytes: int, seconds: float) -> None:
            super().__call__(memory_bytes, seconds)
            raise MemoryError

    out = io.BytesIO()

    assert main(io.BytesIO(job(pdf_of([""]))), out, Full()) == MEMORY_EXIT
    assert out.getvalue() == b""


def answered(
    *pages: int, count: int = 3, data: bytes = b"RIFF....WEBP", side: int = 400, claimed: int = 0
) -> bytes:
    """An answer of the child, as far as it is true."""
    size = claimed or len(data)
    images = [{"page": page, "width": side, "height": side, "size_bytes": size} for page in pages]
    head = json.dumps({"page_count": count, "images": images})
    return head.encode() + b"\n" + data * len(pages)


def test_an_answer_that_adds_up_is_taken_and_the_rest_follows_from_the_request() -> None:
    assert [image.page for image in answer_of(answered(1, 2), REQUEST).images] == [1, 2]
    assert answer_of(answered(1), REQUEST).rest == (2,)
    assert answer_of(answered(1, 2), REQUEST).rest == ()


@pytest.mark.parametrize(
    "answer",
    [
        b"",
        b"not json\n",
        answered(),
        answered(1) * 2,
        answered(1, claimed=13),
        answered(1, claimed=11),
        answered(1, claimed=-1),
        answered(2),  # not the first page asked for
        answered(1, 1),
        answered(1, 2, 3),  # more than asked for
        answered(1, count=1),  # page 2 was asked for and does not exist
        answered(1, count=0),
        answered(1, side=401),
        answered(1, side=0),
        answered(1, data=b"RIFF....WAVE"),
        answered(1, data=b"GIF89a......"),
        answered(1, data=b""),
        answered(1, count=200_001),
        b'{"failure": "pages", "page_count": 200001}\n',
        b'{"failure": "pages", "page_count": 2}\n',  # the pages are right
        b'{"failure": "invented"}\n',
    ],
)
def test_an_answer_that_does_not_add_up_is_a_crash(answer: bytes) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = answer_of(answer, REQUEST)

    assert raised.value.reason == "crashed"


def test_more_images_or_bytes_than_a_call_may_have_are_a_crash() -> None:
    answer = answered(1, 2)
    for limit in ({"max_images": 1}, {"max_bytes": 23}):
        with pytest.raises(RenderFailed) as raised:
            _ = answer_of(answer, REQUEST.model_copy(update=limit))
        assert raised.value.reason == "crashed"
    assert answer_of(answer, REQUEST.model_copy(update={"max_bytes": 24})).rest == ()


def test_a_file_with_very_many_pages_is_shown_and_one_with_too_many_says_so() -> None:
    many = run(job(pdf_with_pages(190_000), REQUEST.model_copy(update={"pages": "1-"})))[1]
    found = answer_of(many, REQUEST.model_copy(update={"pages": "1-"}))
    with pytest.raises(RenderFailed) as raised:
        _ = answer_of(run(job(pdf_with_pages(200_001)))[1], REQUEST)

    assert (found.page_count, len(found.images), len(found.rest)) == (190_000, 5, 189_995)
    assert raised.value.reason == "count"


def pdf_with_pages(count: int) -> bytes:
    """A PDF that lists the same page very often."""
    kids = " ".join(["3 0 R"] * count)
    return pdf_of([""]).replace(
        b"/Kids [3 0 R] /Count 1", f"/Kids [{kids}] /Count {count}".encode()
    )
