"""The child process: what it gets, what it may cost, and that it ends."""

import asyncio
import hashlib
import os
import sys
import time
from typing import TYPE_CHECKING

import anyio
import pytest
from PIL import Image

from mcp_server_paperless.domain.rendering import PageRenderer, RenderFailed, RenderRequest
from mcp_server_paperless.rendering.runner import ChildRenderer, child_environment
from rendering.files import ENCRYPTED_PDF, QUADRANTS, corners, opened, pdf_of, saved

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-42")]

REQUEST = RenderRequest(mime_type="application/pdf", pages="1", size=400)
WINDOWS = sys.platform == "win32"
# A worker that reads its job and then does what the test is about.
READ = "import sys, json\nhead = json.loads(sys.stdin.buffer.readline())\n"
ANSWER = (
    """sys.stdout.buffer.write(json.dumps({"page_count": %s, "images": [{"page": 1, "width": 1, """
    """"height": 1, "size_bytes": 1}]}).encode() + b"\\nx")\n"""
)


def worker(code: str, **limits: float) -> ChildRenderer:
    """A renderer whose child runs `code` instead of the worker."""
    command = (sys.executable, "-I", "-c", READ + code)
    return ChildRenderer(
        command=command,
        seconds=limits.get("seconds", 20.0),
        memory_bytes=int(limits.get("memory_bytes", 2**30)),
        output_bytes=int(limits.get("output_bytes", 2**20)),
    )


async def reason(renderer: PageRenderer, content: bytes = b"x") -> str:
    with pytest.raises(RenderFailed) as raised:
        _ = await renderer.render(content, REQUEST)
    return raised.value.reason


async def test_a_page_is_rendered_in_a_child_process() -> None:
    found = await ChildRenderer().render(pdf_of([QUADRANTS, ""]), REQUEST)

    [image] = found.images
    assert (found.page_count, image.page, image.width, image.height) == (2, 1, 300, 400)
    assert corners(opened(image.data)) == ["red", "green", "blue", "yellow"]


@pytest.mark.parametrize("kind", ["JPEG", "PNG", "TIFF", "WEBP"])
async def test_every_image_type_is_rendered_in_a_child_process(kind: str) -> None:
    content = saved(Image.new("RGB", (60, 80), (255, 0, 0)), kind)
    request = REQUEST.model_copy(update={"mime_type": f"image/{kind.lower()}"})

    [image] = (await ChildRenderer().render(content, request)).images

    assert corners(opened(image.data)) == ["red"] * 4


async def test_a_failure_of_the_file_comes_back_as_such() -> None:
    assert await reason(ChildRenderer(), ENCRYPTED_PDF) == "encrypted"
    assert await reason(ChildRenderer(), b"nothing") == "damaged"


async def test_the_child_gets_the_job_and_the_whole_file() -> None:
    content = os.urandom(3_000_000)
    digest = hashlib.sha256(content).hexdigest()
    code = (
        "import hashlib\n"
        "content = sys.stdin.buffer.read()\n"
        "got = [len(content), hashlib.sha256(content).hexdigest(), head['request']['pages'], "
        "head['content_bytes'], head['memory_bytes'], head['seconds']]\n"
        f"ok = got == [3000000, '{digest}', '1', 3000000, 5000, 7.0]\n{ANSWER % 'int(ok)'}"
    )

    found = await worker(code, memory_bytes=5000, seconds=7.0).render(content, REQUEST)

    assert found.page_count == 1


@pytest.mark.skipif(WINDOWS, reason="Windows needs SystemRoot, see child_environment")
async def test_the_child_inherits_no_environment_and_no_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERLESS_TOKEN", "must-not-reach-the-child")
    reading, writing = os.pipe()
    os.set_inheritable(reading, True)  # noqa: FBT003 -- the signature of the standard library
    # macOS adds __CF_USER_TEXT_ENCODING to every process and Python LC_CTYPE.
    code = f"""
import os
extra = len(set(os.environ) - {{"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}})
try:
    os.fstat({reading})
    extra += 1
except OSError:
    pass
if not os.path.samestat(os.fstat(2), os.stat(os.devnull)):
    extra += 1
{ANSWER % "extra + 1"}"""

    found = await worker(code).render(b"", REQUEST)

    os.close(reading)
    os.close(writing)
    assert found.page_count == 1


def test_windows_processes_get_the_one_variable_they_cannot_start_without() -> None:
    given = {"SystemRoot": "C:\\Windows", "PAPERLESS_TOKEN": "secret", "PATH": "x"}

    assert child_environment("win32", given) == {"SystemRoot": "C:\\Windows"}
    assert child_environment("win32", {}) == {}
    assert child_environment("linux", given) == {}


async def test_a_child_that_takes_too_long_is_ended() -> None:
    started = time.monotonic()

    assert await reason(worker("import time\ntime.sleep(60)", seconds=0.5)) == "timeout"
    assert time.monotonic() - started < 10


async def test_a_child_that_answers_but_does_not_end_is_ended() -> None:
    code = ANSWER % 1 + "sys.stdout.flush()\nimport time, os\nos.close(1)\ntime.sleep(60)"

    assert await reason(worker(code, seconds=1.0)) == "timeout"


async def test_a_child_that_does_not_read_its_file_is_no_hang() -> None:
    assert await reason(worker("raise SystemExit(1)"), b"x" * 5_000_000) == "crashed"
    assert await reason(worker("import time\ntime.sleep(60)", seconds=0.5), b"x" * 5_000_000) == (
        "timeout"
    )


async def test_a_child_that_floods_its_output_is_ended() -> None:
    code = "while True: sys.stdout.buffer.write(b'x' * 65536)"

    assert await reason(worker(code)) == "crashed"


async def test_an_answer_of_exactly_the_limit_is_read() -> None:
    code = (
        "head = json.dumps({'page_count': 1, 'images': [{'page': 1, 'width': 1, 'height': 1, "
        "'size_bytes': 99800}]}).ljust(199) + '\\n'\n"
        "sys.stdout.buffer.write(head.encode() + b'x' * 99800)"
    )

    found = await worker(code, output_bytes=100_000).render(b"", REQUEST)

    assert len(found.images[0].data) == 99_800
    assert await reason(worker(code, output_bytes=99_999)) == "crashed"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("raise SystemExit(3)", "memory"),
        ("raise SystemExit(4)", "timeout"),
        ("raise SystemExit(9)", "crashed"),
        ("import os\nos.abort()", "crashed"),
        ("raise ValueError('secret text of the document')", "crashed"),
        ("pass", "crashed"),
        (ANSWER % 1 + "sys.stdout.flush()\nimport os\nos._exit(7)", "crashed"),
    ],
)
async def test_how_a_child_ends_is_the_reason(code: str, expected: str) -> None:
    assert await reason(worker(code)) == expected


async def test_the_real_child_stops_at_its_memory() -> None:
    # Half of what the decoded picture takes.
    content = saved(Image.new("RGB", (9_000, 9_000)), "PNG")
    request = REQUEST.model_copy(update={"mime_type": "image/png"})

    with pytest.raises(RenderFailed) as raised:
        _ = await ChildRenderer(memory_bytes=150_000_000).render(content, request)

    assert raised.value.reason == "memory"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def gone(pid: int) -> bool:
    for _ in range(500):
        if not alive(pid):
            return True
        await asyncio.sleep(0.02)
    return False


def written(file: Path) -> str:
    return file.read_text() if file.exists() else ""


async def started(pid_file: Path) -> int:
    """The process id the child writes, once it has."""
    for _ in range(500):
        text = written(pid_file)
        if text.endswith("."):
            return int(text[:-1])
        await asyncio.sleep(0.02)
    message = "the child did not start"
    raise AssertionError(message)


@pytest.mark.skipif(WINDOWS, reason="os.kill(pid, 0) is not a test on Windows")
@pytest.mark.parametrize("content", [b"x", b"x" * 5_000_000], ids=["reading", "writing"])
async def test_a_cancelled_call_ends_its_child(tmp_path: Path, content: bytes) -> None:
    file = tmp_path / "pid"
    code = f"import os, time\nopen({str(file)!r}, 'w').write(f'{{os.getpid()}}.')\ntime.sleep(60)"
    call = asyncio.create_task(worker(code).render(content, REQUEST))
    pid = await started(file)

    _ = call.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await call

    assert await gone(pid)


@pytest.mark.skipif(WINDOWS, reason="os.kill(pid, 0) is not a test on Windows")
async def test_a_call_cancelled_by_the_mcp_sdk_ends_its_child(tmp_path: Path) -> None:
    # The SDK cancels through anyio, which cancels every further await of the call, too.
    file = tmp_path / "pid"
    code = f"import os, time\nopen({str(file)!r}, 'w').write(f'{{os.getpid()}}.')\ntime.sleep(60)"
    pids: list[int] = []
    async with anyio.create_task_group() as group:
        _ = group.start_soon(worker(code).render, b"x" * 5_000_000, REQUEST)
        pids.append(await started(file))
        group.cancel_scope.cancel()

    assert await gone(pids[0])


@pytest.mark.skipif(WINDOWS, reason="os.kill(pid, 0) is not a test on Windows")
async def test_a_child_over_its_time_is_gone(tmp_path: Path) -> None:
    file = tmp_path / "pid"
    code = f"import os, time\nopen({str(file)!r}, 'w').write(f'{{os.getpid()}}.')\ntime.sleep(60)"

    assert await reason(worker(code, seconds=2.0)) == "timeout"
    assert await gone(await started(file))


async def test_the_event_loop_stays_free_while_a_page_is_drawn() -> None:
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    _ = await ChildRenderer().render(pdf_of([QUADRANTS]) + b" " * 20_000_000, REQUEST)
    _ = ticker.cancel()

    assert ticks > 5
