"""read_documents through the assembled server on Paperless 3.5.1 (decisions 69 and 70).

A selection of a thousand documents is made of bare database rows without files, dated 1990 so that
they sort behind every other document: consuming a thousand files would take half an hour.
"""

import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

import pytest
from contract.conftest import delete_and_empty_trash, live_backend
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume_text, in_container, rebuild_search_index, results
from mcp_server_paperless.domain.documents import DocumentQuery
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from e2e.helpers import Json, Stack

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.anyio,
    pytest.mark.decision("decision-69"),
    pytest.mark.decision("decision-70"),
]

PUBLIC, PRIVATE = "e2e-public-invoice", "e2e-private-note"
SCRATCH = "plmcp80-"


async def ask(stack: Stack, *, token: str | None = None, **arguments: object) -> Json:
    server = create_server(
        paperless_settings(stack.base_url, token or stack.admin_token),
        server_settings(toolsets=["core", "bulk"], read_only=True),
    )
    async with Client(server) as client:
        result = await client.call_tool("read_documents", arguments, raise_on_error=False)
    if result.is_error:
        return {"error": str(result.content)}
    assert result.structured_content is not None
    return result.structured_content


def rows(output: Json) -> list[Json]:
    assert "error" not in output, output
    return cast("list[Json]", output["documents"])


def ids(output: Json) -> list[object]:
    return [row["id"] for row in rows(output)]


@pytest.fixture
def scratch(stack: Stack) -> Iterator[tuple[str, list[int]]]:
    """Two documents of the test with a tag of their own."""
    name = f"{SCRATCH}{uuid.uuid4().hex[:8]}"
    tag = cast(
        "Json", stack.admin.post("/api/tags/", json={"name": name}).raise_for_status().json()
    )
    documents = [consume_text(stack.admin, f"{name}-{n}") for n in (1, 2)]
    for document in documents:
        _ = stack.admin.patch(
            f"/api/documents/{document}/", json={"tags": [tag["id"]]}
        ).raise_for_status()
    yield name, documents
    delete_and_empty_trash(stack, documents)
    _ = stack.admin.delete(f"/api/tags/{tag['id']}/")


def shell(code: str) -> None:
    in_container(
        "python", "manage.py", "shell", "-c", f"from documents.models import Document; {code}"
    )


# No word that another document has: more-like-this would find that one too.
BARE_TEXT = "plmcp80bare plmcp80words plmcp80test"


@contextmanager
def bare_documents(
    count: int, prefix: str = SCRATCH, created: str = "1990-01-01"
) -> Generator[str]:
    """``count`` documents without a file, removed again without the trash."""
    name = f"{prefix}{uuid.uuid4().hex[:8]}"
    made = (
        f"Document(title='{name}', content='{BARE_TEXT}', checksum=f'{name}{{n}}', "
        f"mime_type='text/plain', created='{created}')"
    )
    shell(f"Document.objects.bulk_create([{made} for n in range({count})])")
    try:
        yield name
    finally:
        mine = f"Document.global_objects.filter(title='{name}')"
        shell(f"found = {mine}; found._raw_delete(found.db)")


@pytest.fixture(scope="module")
def indexed(stack: Stack) -> Iterator[str]:
    """A thousand bare documents that are also in the full-text index.

    Their title and date keep them out of the test that counts a thousand and one.
    """
    with bare_documents(1000, "indexed80-", "1991-01-01") as name:
        rebuild_search_index(stack.admin)
        yield name
    rebuild_search_index(stack.admin)


@pytest.mark.parametrize("search", ["title", "text", "similar"])
async def test_a_thousand_matches_are_read_well_within_the_timeout(
    stack: Stack, indexed: str, search: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with live_backend(stack) as live:
        documents = live.admin.documents
        mine = DocumentQuery(title_contains=indexed)
        [one, *_] = (await documents.search_documents(mine, page=1, page_size=1)).hits
        query = {
            "title": mine,
            "text": DocumentQuery(query="plmcp80bare"),
            "similar": DocumentQuery(more_like=one.id),
        }[search]
        started = time.monotonic()
        page = await documents.select_documents(query, limit=1001, max_bytes=64 * 1024 * 1024)
        took = time.monotonic() - started

    with capsys.disabled():
        print(f"\nselect_documents {search}: {page.count} matches in {took:.2f} s")  # noqa: T201 -- the measurement of decision-69, read from the log
    assert len(page.hits) == page.count
    # more-like-this decides itself how many are similar.
    assert page.count == 1000 if search != "similar" else page.count <= 1000
    assert took < 30  # the read times out after 60 s


async def test_documents_are_read_by_filter_by_view_and_by_ids(stack: Stack) -> None:
    by_filter = await ask(
        stack, filters={"tags_all": ["e2e-finance"], "title_contains": "e2e-p"}, fields=["title"]
    )
    by_view = await ask(stack, saved_view="e2e-view", fields=["title"])

    assert [row["title"] for row in rows(by_filter)] == [PUBLIC]
    assert PUBLIC in {row["title"] for row in rows(by_view)}
    chosen = cast("list[int]", by_filter["selected_ids"])
    assert by_filter["matched"] == 1
    by_ids = await ask(stack, document_ids=[999_999_999, *chosen])
    assert (ids(by_ids), by_ids["missing"]) == (chosen, [999_999_999])
    assert cast("Json", rows(by_ids)[0]["correspondent"])["name"] == "e2e-acme"


async def test_text_and_notes_come_when_asked_for(stack: Stack) -> None:
    found = await ask(
        stack,
        filters={"title_contains": PUBLIC},
        fields=["content", "notes", "custom_fields", "owner"],
        content_chars=5,
    )

    [row] = rows(found)
    assert len(str(row["content"])) == 5
    assert row["content_next"] == 5
    assert cast("int", row["content_total"]) > 5
    assert [note["user"] for note in cast("list[Json]", row["notes"])] == ["admin"]
    assert "EUR 12.50" in [field["value"] for field in cast("list[Json]", row["custom_fields"])]
    assert "owner" not in row


async def test_a_document_that_stops_matching_refuses_the_call(
    stack: Stack, scratch: tuple[str, list[int]]
) -> None:
    name, documents = scratch
    filters = {"tags_all": [name]}
    shown = await ask(stack, filters=filters, fields=[])
    assert set(ids(shown)) == set(documents)

    again = await ask(stack, document_ids=documents, filters=filters, fields=[])
    assert ids(again) == documents
    one = await ask(stack, document_ids=documents[:1], filters=filters, fields=[])
    assert one["also_matching"] == documents[1:]

    _ = stack.admin.patch(f"/api/documents/{documents[1]}/", json={"tags": []}).raise_for_status()
    refused = await ask(stack, document_ids=documents, filters=filters, fields=[])

    assert f"1 of the named documents no longer matches: {documents[1]}." in str(refused["error"])


async def test_a_saved_view_with_a_deleted_tag_matches_nothing(stack: Stack) -> None:
    name = f"contract-view-e2e-{uuid.uuid4().hex[:8]}"
    tag = cast(
        "Json", stack.admin.post("/api/tags/", json={"name": name}).raise_for_status().json()
    )
    rules = [{"rule_type": 6, "value": str(tag["id"])}]
    _ = stack.admin.post(
        "/api/saved_views/", json={"name": name, "filter_rules": rules}
    ).raise_for_status()
    _ = stack.admin.delete(f"/api/tags/{tag['id']}/").raise_for_status()

    found = await ask(stack, saved_view=name, fields=[])

    assert (found.get("matched"), ids(found)) == (0, [])
    for view in results(stack.admin.get("/api/saved_views/", params={"page_size": 100})):
        if view["name"] == name:
            _ = stack.admin.delete(f"/api/saved_views/{view['id']}/")


async def test_a_thousand_documents_may_match_and_not_one_more(stack: Stack) -> None:
    with bare_documents(1000) as name:
        found = await ask(stack, filters={"title_contains": name}, page_size=100)
        last = await ask(stack, filters={"title_contains": name}, page=10, page_size=100)
        async with live_backend(stack) as live:
            query = DocumentQuery(title_contains=name)
            hundred = await live.admin.documents.search_documents(query, page=1, page_size=100)

        assert (found.get("matched"), found.get("next_page")) == (1000, 2)
        named = cast("list[int]", found["selected_ids"])
        assert len(set(named)) == 1000
        # One date, so the id decides: no gaps and no repeats over the pages.
        assert named == sorted(named, reverse=True)
        assert [hit.id for hit in hundred.hits] == named[:100] == ids(found)
        assert (ids(last), last["next_page"]) == (named[-100:], None)

        with bare_documents(1) as extra:
            # The same prefix: 1000 of one name and 1 of the other.
            refused = await ask(
                stack, filters={"title_contains": SCRATCH, "created_to": "1990-01-01"}
            )
            assert extra != name
    assert "1001 documents match; at most 1000 may." in str(refused.get("error"))


async def test_a_restricted_user_reads_only_what_it_may_see(stack: Stack) -> None:
    mine = {"title_contains": "e2e-p"}
    everything = await ask(stack, filters=mine, fields=["title"], ordering="title")
    assert [row["title"] for row in rows(everything)] == [PRIVATE, PUBLIC]
    private, public = cast("list[int]", everything["selected_ids"])
    reader = stack.reader_token

    found = await ask(stack, token=reader, filters=mine, fields=["title", "content"])
    by_ids = await ask(stack, token=reader, document_ids=[private, public], fields=["owner"])
    changed = await ask(stack, token=reader, document_ids=[private, public], filters=mine)
    notes = await ask(stack, token=reader, filters=mine, fields=["notes"])
    blind = await ask(stack, token=stack.blind_token, filters=mine)

    assert (ids(found), found["matched"]) == ([public], 1)
    assert "acme" in str(rows(found)[0]["content"]).lower()
    assert (ids(by_ids), by_ids["missing"]) == ([public], [private])
    assert f"no longer matches: {private}." in str(changed.get("error"))
    assert "may not view notes" in str(notes.get("error"))
    assert "may not" in str(blind.get("error"))
