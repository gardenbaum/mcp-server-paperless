"""Every call a result of the pdf tools names in `next` exists, with these arguments."""

import ast
import re
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from harness import admin, serve
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


def broken(*waits: int) -> Callable[[Paperless], None]:
    def change(fake: Paperless) -> None:
        fake.states = dict.fromkeys(waits, "started")

    return change


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


def changes() -> dict[str, Callable[[Paperless], None]]:
    def set_to(**values: object) -> Callable[[Paperless], None]:
        def change(fake: Paperless) -> None:
            for name, value in values.items():
                setattr(fake, name, value)

        return change

    def meanwhile(fake: Paperless) -> None:
        def newer() -> None:
            _ = fake.add_version(50, 3)

        fake.after_start = newer

    def reread(fake: Paperless) -> None:
        def unreadable() -> None:
            fake.unreadable = lambda number: number == 50

        fake.after_start = unreadable

    def late(fake: Paperless) -> None:
        def wait() -> None:
            fake.now += 46

        fake.after_start = wait

    return {
        "first waits": broken(0),
        "second waits": broken(1),
        "hidden": set_to(hide_tasks=True),
        "doubt": set_to(doubt="somebody else"),
        "metadata": set_to(refuse=part_metadata),
        "unreadable": set_to(unreadable=first_part),
        "refused": set_to(refuse_edit={"new_version": PermissionDenied("No.")}),
        "no answer": set_to(refuse_edit={"new_version": UpstreamTimeout("No.")}),
        "few": set_to(few_pages=True),
        "lost": set_to(lose_version=True),
        "deletion": set_to(time_out={50}),
        "kept": set_to(refuse_deletes={50}),
        "dated": lambda fake: dated(fake, 50),
        "changed": meanwhile,
        "reread": reread,
        "late": late,
    }


@pytest.mark.parametrize("name", changes())
async def test_every_next_call_names_a_tool_and_its_arguments(name: str) -> None:
    async with Client(serve(admin(), toolsets=["all"])) as client:
        tools = {tool.name: tool.input_schema for tool in await client.list_tools()}
    texts: list[str] = []
    for options in (YES, Options(confirm=True, wait_seconds=0)):
        for run in (replaced, removed, shorten, merged):
            fake = paperless(NUMBER)
            changes()[name](fake)
            result = await run(fake, options)
            texts += [t for t in (result.next, *(p.next for p in result.parts)) if t]

    made = [call for text in texts for call in calls(text)]

    assert made
    for call in made:
        assert isinstance(call.func, ast.Name)
        schema = cast("dict[str, dict[str, object]]", tools[call.func.id])
        assert not call.args
        given = {keyword.arg for keyword in call.keywords}
        assert given <= set(schema["properties"])
        assert set(schema.get("required", ())) <= given
        for keyword in call.keywords:
            value = cast("object", ast.literal_eval(keyword.value))
            assert value is not None


def test_calls_are_found_with_parentheses_in_their_texts() -> None:
    text = 'then update_document(document_id=<the new document>, title="a (b)", tags_set=[1])'

    [call] = calls(text)

    assert [k.arg for k in call.keywords] == ["document_id", "title", "tags_set"]
