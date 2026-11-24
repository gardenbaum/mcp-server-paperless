"""correct_document_text through the assembled server on the E2E Paperless (decision-48).

On documents of its own, titles start with `plmcp75-`; everything a test made is deleted again and
its trash entry emptied. The documents are text files, so reprocessing restores their text.
"""

import base64
import time
import uuid
from typing import TYPE_CHECKING

import anyio
import pytest
from contract.conftest import delete_and_empty_trash
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume, num, obj, parse, results
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    import httpx2
    from fastmcp import FastMCP

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-48")]

type Json = dict[str, object]

TOOL = "correct_document_text"
SETTLE_SECONDS = 90.0


class Scope:
    """The documents of one test: every id it made, deleted again at its end."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.ids: list[int] = []

    def document(self, text: str) -> int:
        title = f"plmcp75-{uuid.uuid4().hex}"
        _, created = consume(
            self.stack.admin, title, text.encode(), filename=f"{title}.txt", mime_type="text/plain"
        )
        assert created is not None
        self.ids.append(created)
        return created


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    delete_and_empty_trash(stack, made.ids)


def word() -> str:
    """A word no other document holds."""
    return f"plmcp75{uuid.uuid4().hex}"


def server(stack: Stack, token: str) -> FastMCP:
    return create_server(
        paperless_settings(stack.base_url, token), server_settings(toolsets=["core", "versions"])
    )


async def run(stack: Stack, tool: str, **arguments: object) -> Json:
    async with Client(server(stack, stack.admin_token)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def refused_for(stack: Stack, user: str, tool: str, arguments: Json) -> str:
    async with Client(server(stack, user)) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    return "".join(getattr(block, "text", "") for block in result.content)


async def refused(stack: Stack, tool: str, **arguments: object) -> str:
    return await refused_for(stack, stack.admin_token, tool, arguments)


async def read(stack: Stack, document: int, **arguments: object) -> Json:
    return await run(stack, "get_document_content", document_id=document, **arguments)


async def text_of(stack: Stack, document: int, **arguments: object) -> str:
    return str((await read(stack, document, **arguments))["content"])


async def state_of(stack: Stack, document: int, **arguments: object) -> str:
    return str((await read(stack, document, **arguments))["text_state"])


def found(stack: Stack, term: str) -> list[int]:
    rows = results(stack.admin.get("/api/documents/", params={"query": term}).raise_for_status())
    return [num(row["id"]) for row in rows]


async def becomes(check: Callable[[], Awaitable[bool]]) -> bool:
    """Whether ``check`` is true within ``SETTLE_SECONDS``."""
    deadline = time.monotonic() + SETTLE_SECONDS
    while not await check():
        if time.monotonic() > deadline:
            return False
        await anyio.sleep(0.5)
    return True


async def finds(stack: Stack, term: str, expected: list[int]) -> bool:
    async def check() -> bool:
        return found(stack, term) == expected

    return await becomes(check)


async def add_version(stack: Stack, scope: Scope, document: int, text: str) -> int:
    uploaded = await run(
        stack,
        "upload_document_version",
        document_id=document,
        content_base64=base64.b64encode(text.encode()).decode(),
        filename="plmcp75.txt",
        wait_seconds=30,
    )
    version = num(uploaded["version_id"])
    scope.ids.append(version)
    return version


async def test_a_corrected_passage_is_found_and_reprocessing_brings_the_old_text_back(
    stack: Stack, scope: Scope
) -> None:
    wrong, right = word(), word()
    document = scope.document(f"Invoice total {wrong} EUR, due in May.")
    before = await text_of(stack, document)
    swap: Json = {
        "document_id": document,
        "text_state": await state_of(stack, document),
        "passage": wrong,
        "replacement": right,
    }
    assert await finds(stack, wrong, [document])

    unconfirmed = await refused(stack, TOOL, **swap)
    assert "Nothing was changed. Confirming would replace the text of version" in unconfirmed
    assert right in unconfirmed
    preview = await run(stack, TOOL, **swap, dry_run=True)
    assert preview["dry_run"] is True
    assert await text_of(stack, document) == before

    done = await run(stack, TOOL, **swap, confirm=True)

    assert (done["version_id"], done["is_newest"]) == (document, True)
    assert await text_of(stack, document) == before.replace(wrong, right)
    assert done["text_state"] == await state_of(stack, document)
    assert await finds(stack, right, [document])
    assert await finds(stack, wrong, [])
    assert "changed since it was read" in await refused(stack, TOOL, **swap, confirm=True)

    _ = stack.admin.post(
        "/api/documents/reprocess/", json={"documents": [document]}
    ).raise_for_status()

    async def restored() -> bool:
        return await text_of(stack, document) == before

    assert await becomes(restored)
    assert await finds(stack, wrong, [document])


async def test_the_whole_text_is_replaced(stack: Stack, scope: Scope) -> None:
    document, typed = scope.document(word()), word()
    state = await state_of(stack, document)

    _ = await run(
        stack, TOOL, document_id=document, text_state=state, text=f" {typed}\n", confirm=True
    )

    assert await text_of(stack, document) == typed  # the root's text is stripped
    assert await finds(stack, typed, [document])


async def test_an_older_version_is_corrected_and_search_does_not_find_it(
    stack: Stack, scope: Scope
) -> None:
    document, old = scope.document(f"root {word()}"), word()
    newest = await add_version(stack, scope, document, f"newest {word()}")
    shown = await text_of(stack, document)
    state = await state_of(stack, document, version_id=document)
    correction: Json = {
        "document_id": document,
        "version_id": document,
        "text_state": state,
        "text": old,
    }

    assert "search does not find it" in await refused(stack, TOOL, **correction)
    done = await run(stack, TOOL, **correction, confirm=True)

    assert (done["version_id"], done["is_newest"]) == (document, False)
    assert "not the newest version" in str(done["message"])
    assert await text_of(stack, document, version_id=document) == old
    assert await text_of(stack, document) == shown
    assert (await read(stack, document))["version_id"] == newest
    # Written after it: once search finds this, it has seen the older version's text, too.
    later = word()
    _ = await run(
        stack,
        TOOL,
        document_id=document,
        text_state=await state_of(stack, document),
        text=later,
        confirm=True,
    )
    assert await finds(stack, later, [document])
    assert found(stack, old) == []


async def test_a_missing_or_ambiguous_passage_changes_nothing(stack: Stack, scope: Scope) -> None:
    document = scope.document(f"twice twice {word()}")
    before = await text_of(stack, document)
    state = await state_of(stack, document)

    for passage, message in ((word(), "does not occur"), ("twice", "occurs more than once")):
        said = await refused(
            stack,
            TOOL,
            document_id=document,
            text_state=state,
            passage=passage,
            replacement="",
            confirm=True,
        )
        assert message in said
    assert await text_of(stack, document) == before


async def test_a_change_between_preview_and_confirmation_is_not_overwritten(
    stack: Stack, scope: Scope
) -> None:
    document = scope.document(f"first {word()}")
    correction: Json = {
        "document_id": document,
        "text_state": await state_of(stack, document),
        "text": "ours",
    }
    assert "Confirming would" in await refused(stack, TOOL, **correction)
    _ = stack.admin.patch(
        f"/api/documents/{document}/", json={"content": "theirs"}
    ).raise_for_status()

    said = await refused(stack, TOOL, **correction, confirm=True)

    assert "changed since it was read" in said
    assert await text_of(stack, document) == "theirs"


async def test_a_user_who_may_only_view_reads_the_text_but_does_not_correct_it(
    stack: Stack, scope: Scope
) -> None:
    document = scope.document(f"shared {word()}")

    def user_id(api: httpx2.Client) -> int:
        settings = obj(parse(api.get("/api/ui_settings/").raise_for_status()))
        return num(obj(settings["user"])["id"])

    reader, admin = user_id(stack.reader), user_id(stack.admin)
    _ = stack.admin.patch(
        f"/api/documents/{document}/",
        json={
            "owner": admin,
            "set_permissions": {
                "view": {"users": [reader], "groups": []},
                "change": {"users": [], "groups": []},
            },
        },
    ).raise_for_status()
    state = await state_of(stack, document)
    before = await text_of(stack, document)

    arguments: Json = {
        "document_id": document,
        "text_state": state,
        "text": "no",
        "confirm": True,
    }
    said = await refused_for(stack, stack.reader_token, TOOL, arguments)

    assert f"may not change document {document}" in said
    assert await text_of(stack, document) == before
