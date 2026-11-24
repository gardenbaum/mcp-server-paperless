"""Every call a result of the pdf tools names in `next` exists, with these arguments."""

import ast
import re
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import admin, pdf_service, serve
from jsonschema import validate
from services.test_pdf_parts import YES, extract, split
from services.test_pdf_replacing import NUMBER, dated, merge, paperless, shorten

from mcp_server_paperless.domain.errors import PermissionDenied, UpstreamTimeout
from mcp_server_paperless.services.pdf import Options

if TYPE_CHECKING:
    from collections.abc import Callable

    from services.test_pdf_replacing import Paperless

    from mcp_server_paperless.domain.pdf import PdfResult

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-67")]


def calls(text: str) -> list[ast.Call]:
    """The calls in a text; placeholders like `<the new document>` count as a number."""
    text = re.sub(r"<[a-z ]+>", "1", text)
    found: list[ast.Call] = []
    for start in re.finditer(r"\b[a-z]+(?:_[a-z]+)+\(", text):
        ends = [n for n, letter in enumerate(text) if letter == ")" and n > start.start()]
        for end in ends:
            try:
                node = ast.parse(text[start.start() : end + 1], mode="eval").body
            except SyntaxError:
                continue
            assert isinstance(node, ast.Call)
            found.append(node)
            break
    return found


def part_metadata(number: int, _: int | None) -> bool:
    return number == 52


def first_part(number: int) -> bool:
    return number == 52


async def replaced(fake: Paperless, options: Options) -> PdfResult:
    return await split(fake, output="replace_original", options=options)


async def removed(fake: Paperless, options: Options) -> PdfResult:
    return await extract(fake, options=options)


async def merged(fake: Paperless, _: Options) -> PdfResult:
    return await merge(fake)


def stop(_: int) -> bool:
    raise RuntimeError


def first_waits(fake: Paperless) -> None:
    fake.states = {0: "started"}


def second_waits(fake: Paperless) -> None:
    fake.states = {1: "started"}


def hidden(fake: Paperless) -> None:
    fake.hide_tasks = True


def doubted(fake: Paperless) -> None:
    fake.doubt = "somebody else"


def no_metadata(fake: Paperless) -> None:
    fake.refuse = part_metadata


def unreadable(fake: Paperless) -> None:
    fake.unreadable = first_part


def refused(fake: Paperless) -> None:
    fake.refuse_edit = {"new_version": PermissionDenied("No.")}


def unanswered(fake: Paperless) -> None:
    fake.refuse_edit = {"new_version": UpstreamTimeout("No.")}


def few(fake: Paperless) -> None:
    fake.few_pages = True


def lost(fake: Paperless) -> None:
    fake.lose_version = True


def deletion(fake: Paperless) -> None:
    fake.time_out = {50}


def kept(fake: Paperless) -> None:
    fake.refuse_deletes = {50}


def with_date(fake: Paperless) -> None:
    dated(fake, 50)


def changed(fake: Paperless) -> None:
    def newer() -> None:
        _ = fake.add_version(50, 3)

    fake.after_start = newer


def reread(fake: Paperless) -> None:
    def gone() -> None:
        fake.unreadable = original

    fake.after_start = gone


def original(number: int) -> bool:
    return number == 50


def late(fake: Paperless) -> None:
    def wait() -> None:
        fake.now += 46

    fake.after_start = wait


def stopped(fake: Paperless) -> None:
    def broken() -> None:
        fake.unreadable = stop

    fake.after_start = broken


def stopped_hidden(fake: Paperless) -> None:
    dated(fake, 50)
    fake.copies_error = RuntimeError()


def stopped_trashing(fake: Paperless) -> None:
    fake.delete_error = RuntimeError()


def stopped_removing(fake: Paperless) -> None:
    def shows(number: int) -> bool:
        return number == 50 and len(fake.edits) == 2 and stop(number)

    fake.unreadable = shows


CASES = (
    *(first_waits, second_waits, hidden, doubted, no_metadata, unreadable, refused, unanswered),
    *(few, lost, deletion, kept, with_date, changed, reread, late),
    *(stopped, stopped_hidden, stopped_trashing, stopped_removing),
)


@pytest.mark.parametrize("change", CASES)
async def test_every_next_call_names_a_tool_and_its_arguments(
    change: Callable[[Paperless], None],
) -> None:
    async with Client(serve(admin(), toolsets=["all"])) as client:
        tools = {tool.name: tool.input_schema for tool in await client.list_tools()}
    texts: list[str] = []
    for options in (YES, Options(confirm=True, wait_seconds=0)):
        for run in (replaced, removed, shorten, merged):
            fake = paperless(NUMBER)
            change(fake)
            result = await run(fake, options)
            texts += [t for t in (result.next, *(p.next for p in result.parts)) if t]

    made = [call for text in texts for call in calls(text)]

    assert made
    for call in made:
        assert isinstance(call.func, ast.Name)
        schema = cast("dict[str, dict[str, object]]", tools[call.func.id])
        assert not call.args
        given = {k.arg: cast("object", ast.literal_eval(k.value)) for k in call.keywords}
        assert given.keys() <= set(schema["properties"])
        validate(given, schema)


async def test_the_next_calls_of_a_stopped_rotation_exist() -> None:
    async with Client(serve(admin(), toolsets=["all"])) as client:
        tools = {tool.name: tool.input_schema for tool in await client.list_tools()}
    fake = paperless()
    dated(fake, 50)
    fake.break_fields = True

    result = await pdf_service(fake).rotate([50], 90)

    [call] = calls(result.next or "")
    assert isinstance(call.func, ast.Name)
    given = {k.arg: cast("object", ast.literal_eval(k.value)) for k in call.keywords}
    validate(given, tools[call.func.id])
    assert given == {"document_id": 50, "include": ["versions"]}


def test_calls_are_found_with_parentheses_in_their_texts() -> None:
    text = 'then update_document(document_id=<the new document>, title="a (b)", tags_set=[1])'

    [call] = calls(text)

    assert [k.arg for k in call.keywords] == ["document_id", "title", "tags_set"]
