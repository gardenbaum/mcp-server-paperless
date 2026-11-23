"""The program of the child process, with its streams as arguments."""

import io
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

    found = answer_of(out)
    assert (code, guard.calls) == (0, [(1000, 5.0)])
    assert (found.page_count, found.rest) == (3, ())
    assert [(i.page, i.width, i.height) for i in found.images] == [(1, 300, 400), (2, 300, 400)]
    assert corners(opened(found.images[0].data)) == ["red", "green", "blue", "yellow"]


def test_the_pages_that_did_not_fit_are_in_the_answer() -> None:
    request = REQUEST.model_copy(update={"max_images": 1})

    assert answer_of(run(job(pdf_of(["", ""]), request))[1]).rest == (2,)


@pytest.mark.parametrize(
    ("content", "reason", "page_count"),
    [(ENCRYPTED_PDF, "encrypted", 0), (b"nothing", "damaged", 0), (pdf_of([""]), "pages", 1)],
)
def test_a_failure_is_an_answer(content: bytes, reason: str, page_count: int) -> None:
    code, out, _ = run(job(content))

    with pytest.raises(RenderFailed) as raised:
        _ = answer_of(out)
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


ONE_IMAGE = (
    b'{"page_count": 1, "images": [{"page": 1, "width": 1, "height": 1, "size_bytes": %d}]}\n'
)


@pytest.mark.parametrize(
    "answer",
    [
        b"",
        b"not json\n",
        b'{"page_count": 1}\n' * 1000,
        ONE_IMAGE % 5 + b"four",
        ONE_IMAGE % 5 + b"six666",
        ONE_IMAGE % -1,
    ],
)
def test_an_answer_that_does_not_add_up_is_a_crash(answer: bytes) -> None:
    with pytest.raises(RenderFailed) as raised:
        _ = answer_of(answer)

    assert raised.value.reason == "crashed"
