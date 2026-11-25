"""insert_pages, extract_pages and split_document on Paperless 3.5.1 when a step fails.

Through the assembled server. The steps fail because somebody else acts at a known place of the
call (tests/interleaving.py), so every refusal is Paperless's own. Every document and user is
deleted again at the end.
"""

import asyncio
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, cast, override

import pytest
from contract.conftest import live_in_trash
from fakes.paperless import BROKEN_PDF
from fastmcp import Client
from harness import paperless_settings, server_settings
from interleaving import before

from e2e.helpers import consume, num, pdf_pages, results, settled
from e2e.uploading import Scope, text
from mcp_server_paperless.rendering.runner import ChildRenderer
from mcp_server_paperless.server import Ports, create_server, paperless_ports

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from fastmcp.client.client import CallToolResult

    from e2e.helpers import Stack
    from mcp_server_paperless.domain.rendering import Assembled, AssembleRequest, PageRenderer

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.anyio,
    pytest.mark.decision("decision-67"),
    pytest.mark.decision("decision-68"),
]

type Json = dict[str, object]

RIGHTS = (
    *("view_document", "change_document", "add_document", "delete_document"),
    *("view_uisettings", "view_tag", "view_customfield"),
    *("view_correspondent", "view_documenttype", "view_storagepath"),
)
FOLLOWING = (*RIGHTS, "view_paperlesstask")


class Steps(Scope):
    """The documents and users of one test, and the server with somebody else in between."""

    def pdf(self, pages: int) -> int:
        """A PDF nobody owns, so every user may edit it as far as their permissions go."""
        title = f"plmcp119-{uuid.uuid4().hex}"
        texts = [f"{title} page {n}" for n in range(1, pages + 1)]
        _, made = consume(self.stack.admin, title, pdf_pages(texts))
        assert made is not None
        self.documents.append(made)
        self.free(made)
        return made

    def free(self, document: int) -> None:
        """Let everybody see and edit the document."""
        path = f"/api/documents/{document}/"
        _ = self.stack.admin.patch(path, json={"owner": None}).raise_for_status()

    def take(self, document: int) -> None:
        """The admin makes the document their own; every user of the test may look at it."""
        me = cast("dict[str, Json]", self.stack.admin.get("/api/ui_settings/").json())
        nobody: Json = {"users": [], "groups": []}
        granted = {"view": {"users": self.users, "groups": []}, "change": nobody}
        _ = self.stack.admin.patch(
            f"/api/documents/{document}/",
            json={"owner": me["user"]["id"], "set_permissions": granted},
        ).raise_for_status()

    async def through(
        self,
        tool: str,
        token: str,
        *,
        ports: Callable[[Ports], Ports] = lambda ports: ports,
        renderer: PageRenderer | None = None,
        **arguments: object,
    ) -> CallToolResult:
        server = create_server(
            paperless_settings(self.stack.base_url, token),
            server_settings(toolsets=["core", "pdf"]),
            ports=lambda client: ports(paperless_ports(client)),
            renderer=renderer,
        )
        async with Client(server) as client:
            result = await client.call_tool(tool, arguments, raise_on_error=False)
        made = cast("list[object]", (result.structured_content or {}).get("produced_ids", []))
        self.documents.extend(num(n) for n in made)
        return result

    def record(self, number: int) -> Json:
        answer = self.stack.admin.get(f"/api/documents/{number}/").raise_for_status()
        return cast("Json", answer.json())

    async def appeared(self, title: str, *, besides: int) -> int:
        """The other document with this title, once Paperless has it."""
        for _ in range(60):
            found = results(
                self.stack.admin.get("/api/documents/", params={"title__iexact": title})
            )
            made = [num(row["id"]) for row in found if row["id"] != besides]
            if made:
                self.documents.extend(made)
                return made[0]
            await asyncio.sleep(1)
        pytest.fail(f"Paperless made no other document {title!r}")

    def versions(self, number: int) -> int:
        return len(cast("list[object]", self.record(number)["versions"]))


@pytest.fixture
def steps(stack: Stack) -> Iterator[Steps]:
    made = Steps(stack)
    yield made
    made.clean_up()


def output(result: CallToolResult) -> Json:
    assert result.structured_content is not None, text(result)
    return result.structured_content


class Acting(ChildRenderer):
    """The renderer, after whose work something happens; `instead` replaces what it makes."""

    def __init__(
        self, act: Callable[[], None] = lambda: None, *, instead: bytes | None = None
    ) -> None:
        super().__init__()
        self.act: Callable[[], None] = act
        self.instead: bytes | None = instead

    @override
    async def assemble(self, target: bytes, source: bytes, request: AssembleRequest) -> Assembled:
        made = await super().assemble(target, source, request)
        self.act()
        return made if self.instead is None else made.model_copy(update={"content": self.instead})


async def document_taken(steps: Steps, target: int, source: int) -> None:
    token = steps.user(*FOLLOWING)

    result = await steps.through(
        "insert_pages",
        token,
        renderer=Acting(lambda: steps.take(target)),
        document_id=target,
        source_document_id=source,
        after_page=1,
    )

    assert result.is_error
    assert "may not" in text(result)
    assert (steps.versions(target), steps.versions(source)) == (1, 1)


async def not_consumed(steps: Steps, target: int, source: int) -> None:
    result = output(
        await steps.through(
            "insert_pages",
            steps.user(*FOLLOWING),
            renderer=Acting(instead=BROKEN_PDF),
            document_id=target,
            source_document_id=source,
            after_page=1,
            wait_seconds=30,
        )
    )

    assert (result["state"], result["produced_ids"]) == ("failure", [])
    assert f"Document {target} shows the file it showed before." in str(result["message"])
    assert (steps.versions(target), steps.versions(source)) == (1, 1)


async def insertion_nobody_can_follow(steps: Steps, target: int, source: int) -> None:
    result = output(
        await steps.through(
            "insert_pages",
            steps.user(*RIGHTS),
            document_id=target,
            source_document_id=source,
            after_page=1,
            wait_seconds=1,
        )
    )

    [task] = cast("list[Json]", result["tasks"])
    assert (result["state"], result["produced_ids"], task["state"]) == ("unknown", [], "unknown")
    assert result["next"] == f'get_document(document_id={target}, include=["versions"])'
    assert "Look before repeating the call." in str(result["message"])
    version = settled(steps.stack.admin, str(task["task_id"]), "the new version")
    assert version is not None
    steps.documents.append(version)
    assert steps.versions(target) == 2


async def test_every_way_an_insertion_ends_early_leaves_both_documents_until_one_succeeds(
    steps: Steps,
) -> None:
    """One pair of documents for all of them: each has to leave it as it was for the next."""
    target, source = steps.pdf(1), steps.pdf(1)

    await document_taken(steps, target, source)
    steps.free(target)
    await not_consumed(steps, target, source)
    await insertion_nobody_can_follow(steps, target, source)


async def test_a_split_whose_metadata_is_refused_keeps_the_original(steps: Steps) -> None:
    source = steps.pdf(2)
    title = steps.record(source)["title"]

    async def taken(document: int, _: object) -> None:
        steps.take(document)

    result = output(
        await steps.through(
            "split_document",
            steps.user(*FOLLOWING),
            ports=lambda ports: replace(
                ports, document_writes=before(ports.document_writes, "update_document", taken)
            ),
            document_id=source,
            parts=["1", "2"],
            part_metadata=[{"title": "plmcp119 first"}, {"title": "plmcp119 second"}],
            output="replace_original",
            confirm=True,
        )
    )

    parts = cast("list[Json]", result["parts"])
    assert [(p["state"], p["metadata"]) for p in parts] == [
        ("success", "not_set"),
        ("success", "set"),
    ], result
    first, second = (num(p["document_id"]) for p in parts)
    assert f"update_document(document_id={first}" in str(parts[0]["next"])
    assert (result["original"], result["trashed_ids"]) == ("kept", [])
    assert "the metadata of part 1 is not set" in str(result["message"])
    assert f"delete_document(document_id={source})" in str(result["next"])
    assert not live_in_trash(steps.stack, source)
    assert (steps.record(first)["title"], steps.record(second)["title"]) == (
        *(title, "plmcp119 second"),
    )


async def removal_refused(steps: Steps, source: int) -> None:
    async def taken(document: int, _: object) -> None:
        steps.take(document)

    result = output(
        await steps.through(
            "extract_pages",
            steps.user(*FOLLOWING),
            ports=lambda ports: replace(
                ports, pdf_writes=before(ports.pdf_writes, "edit_pdf", taken, at=2)
            ),
            document_id=source,
            pages="2",
            remove=True,
            metadata={"title": "plmcp119 page"},
            confirm=True,
        )
    )

    [product] = cast("list[int]", result["produced_ids"])
    assert result["original"] == "kept", result
    assert "may not" in str(result["message"])
    assert f"delete_document(document_id={product}) to drop" in str(result["next"])
    made, kept = steps.record(product), steps.record(source)
    assert (made["title"], made["page_count"]) == ("plmcp119 page", 1)
    assert (kept["page_count"], steps.versions(source)) == (2, 1)


async def extraction_nobody_can_follow(steps: Steps, source: int) -> None:
    title = str(steps.record(source)["title"])

    result = output(
        await steps.through(
            "extract_pages",
            steps.user(*RIGHTS),
            document_id=source,
            pages="2",
            remove=True,
            metadata={"title": "plmcp119 page"},
            confirm=True,
        )
    )

    [part] = cast("list[Json]", result["parts"])
    assert (part["state"], part["metadata"]) == ("unknown", "not_set")
    assert 'title="plmcp119 page"' in str(part["next"])
    assert (result["state"], result["original"], result["produced_ids"]) == ("unknown", "kept", [])
    assert steps.record(await steps.appeared(title, besides=source))["page_count"] == 1
    assert (steps.record(source)["page_count"], steps.versions(source)) == (2, 1)


async def test_every_way_an_extraction_ends_early_leaves_the_pages(steps: Steps) -> None:
    source = steps.pdf(2)

    await extraction_nobody_can_follow(steps, source)
    await removal_refused(steps, source)
