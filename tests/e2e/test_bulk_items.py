"""update_documents and add_notes on the E2E Paperless, on documents of their own (decision-73)."""

import random
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from confirmation import YES, answering
from contract.conftest import delete_and_empty_trash
from fastmcp import Client

from e2e.helpers import consume_text, num, obj, parse, results
from e2e.test_bulk import fields_of, raw, refused, run, server, tags_of
from e2e.test_read_documents import bare_documents, shell

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from e2e.helpers import Json, Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-73")]

PREFIX = "plmcp803-"
LINK, TEXT = "e2e-documentlink", "e2e-string"


@dataclass
class Signed:
    """A temporary user with its token."""

    id: int
    token: str


@pytest.fixture
def user(stack: Stack) -> Iterator[Callable[..., Signed]]:
    """Makes users with just these global permissions, deleted afterwards."""
    made: list[int] = []

    def new(*permissions: str) -> Signed:
        name, password = f"{PREFIX}{uuid.uuid4().hex[:12]}", f"pw-{uuid.uuid4().hex}"
        body = {"username": name, "password": password, "user_permissions": list(permissions)}
        created = stack.admin.post("/api/users/", json=body).raise_for_status()
        number = num(obj(parse(created))["id"])
        made.append(number)
        # Basic auth, not /api/token/: that endpoint is throttled.
        with httpx2.Client(
            base_url=stack.base_url,
            auth=(name, password),
            headers={"Accept": "application/json; version=10"},
        ) as signed_in:
            issued = signed_in.post("/api/profile/generate_auth_token/").raise_for_status()
        return Signed(number, str(parse(issued)))

    yield new
    for number in made:
        _ = stack.admin.delete(f"/api/users/{number}/")


def share(stack: Stack, document: int, viewer: int) -> None:
    """The admin's document, which the user may view but not change."""
    nobody: dict[str, list[int]] = {"users": [], "groups": []}
    view = {"users": [viewer], "groups": list[int]()}
    patch(stack, document, set_permissions={"view": view, "change": nobody})


@pytest.fixture
def documents(stack: Stack) -> Iterator[list[int]]:
    """Three documents, deleted and removed from the trash again."""
    created = [consume_text(stack.admin, f"{PREFIX}{n}-{uuid.uuid4().hex}") for n in range(3)]
    yield created
    delete_and_empty_trash(stack, created)


def field(stack: Stack, name: str) -> int:
    found = stack.admin.get("/api/custom_fields/", params={"name__iexact": name})
    return num(results(found)[0]["id"])


def patch(stack: Stack, document: int, **values: object) -> None:
    _ = stack.admin.patch(f"/api/documents/{document}/", json=values).raise_for_status()


def notes_of(stack: Stack, document: int) -> list[object]:
    answer = stack.admin.get(f"/api/documents/{document}/notes/").raise_for_status()
    return [obj(note)["note"] for note in cast("list[object]", parse(answer))]


def statuses(output: Json) -> list[object]:
    return [obj(row)["status"] for row in cast("list[object]", output["documents"])]


async def test_values_of_their_own_and_one_that_paperless_refuses(
    stack: Stack, documents: list[int]
) -> None:
    first, second, third = documents
    number = random.randrange(1_000_000, 4_000_000_000)  # noqa: S311 -- unique, not secret
    patch(stack, third, archive_serial_number=number)
    items = [
        {"document_id": first, "title": "Own title", "tags_add": ["e2e-invoices"]},
        {"document_id": second, "title": "Not written", "archive_serial_number": number},
    ]

    preview = await run(stack, stack.admin_token, "update_documents", documents=items, dry_run=True)
    unconfirmed = await refused(stack, stack.admin_token, "update_documents", documents=items)
    done = await run(stack, stack.admin_token, "update_documents", documents=items, confirm=True)
    again = await run(
        stack, stack.admin_token, "update_documents", documents=items[:1], confirm=True
    )

    assert statuses(preview) == ["would_change", "would_change"]
    assert "Nothing was changed. Confirming would change 2 documents" in unconfirmed
    assert statuses(done) == ["changed", "failed"]
    assert done["left"] == [second]
    assert "archive serial number" in str(done["message"]).lower()
    assert raw(stack, first)["title"] == "Own title"
    assert {stack.seeded.parent_tag, stack.seeded.child_tag} <= tags_of(stack, first)
    [row] = cast("list[Json]", done["documents"])[:1]
    assert [obj(t)["name"] for t in cast("list[object]", row["implicit_tags"])] == ["e2e-finance"]
    assert raw(stack, second)["title"] != "Not written"
    assert statuses(again) == ["unchanged"]


async def test_a_serial_number_moves_within_a_call(stack: Stack, documents: list[int]) -> None:
    first, second, _ = documents
    number = random.randrange(1_000_000, 4_000_000_000)  # noqa: S311 -- unique, not secret
    patch(stack, first, archive_serial_number=number)
    release = {"document_id": first, "clear": ["archive_serial_number"]}
    take = {"document_id": second, "archive_serial_number": number}

    wrong = await refused(
        stack, stack.admin_token, "update_documents", documents=[take, release], confirm=True
    )
    done = await run(
        stack, stack.admin_token, "update_documents", documents=[release, take], confirm=True
    )

    assert "release it there first" in wrong
    assert statuses(done) == ["changed", "changed"]
    assert raw(stack, second)["archive_serial_number"] == number


async def test_links_inside_a_call_are_expected_and_others_are_named(
    stack: Stack, documents: list[int]
) -> None:
    first, second, third = documents
    link, text = field(stack, LINK), field(stack, TEXT)
    items = [
        {"document_id": first, "custom_fields": {LINK: [second]}},
        {"document_id": second, "custom_fields": {LINK: [first, third], TEXT: "x"}},
    ]

    done = await run(stack, stack.admin_token, "update_documents", documents=items, confirm=True)
    wrong = await refused(
        stack,
        stack.admin_token,
        "update_documents",
        documents=[
            {"document_id": first, "custom_fields": {LINK: [second, third]}},
            {"document_id": second, "remove_custom_fields": [LINK]},
        ],
        confirm=True,
    )

    assert statuses(done) == ["changed", "changed"]
    assert [obj(r)["id"] for r in cast("list[object]", done["also_changed"])] == [third]
    assert fields_of(stack, first) == {link: [second]}
    assert set(cast("list[int]", fields_of(stack, second)[link])) == {first, third}
    assert fields_of(stack, second)[text] == "x"
    assert fields_of(stack, third) == {link: [second]}
    assert f"It links to document {second}" in wrong


async def test_an_owner_change_needs_the_confirmation_and_is_named(
    stack: Stack, documents: list[int]
) -> None:
    items = [{"document_id": documents[0], "owner": "e2e-reader"}]

    unconfirmed = await refused(stack, stack.admin_token, "update_documents", documents=items)
    before = raw(stack, documents[0])["owner"]
    done = await run(stack, stack.admin_token, "update_documents", documents=items, confirm=True)

    assert "to 'e2e-reader' (id " in unconfirmed
    assert "you may lose access to it" in unconfirmed
    assert before != raw(stack, documents[0])["owner"]
    assert statuses(done) == ["changed"]


async def test_a_value_changed_under_the_dialog_asks_again(
    stack: Stack, documents: list[int]
) -> None:
    seen: list[str] = []

    async def others() -> None:
        if len(seen) == 1:
            patch(stack, documents[0], title="Someone else's")

    items = [{"document_id": documents[0], "title": "Mine"}]
    handler = answering("accept", YES, seen=seen, before=others)
    async with Client(server(stack, stack.admin_token), elicitation_handler=handler) as client:
        result = await client.call_tool("update_documents", {"documents": items})

    assert len(seen) == 2
    assert "Someone else's" in seen[1]
    assert result.structured_content is not None
    assert raw(stack, documents[0])["title"] == "Mine"


async def test_notes_are_added_once_by_ids_and_by_filter(
    stack: Stack, documents: list[int]
) -> None:
    title = str(raw(stack, documents[0])["title"])
    patch(stack, documents[1], title=f"{title} two")
    note = f"  paid {uuid.uuid4().hex}\n"
    chosen = {"filters": {"title_contains": title}, "note": note}

    first = await run(
        stack,
        stack.admin_token,
        "add_notes",
        document_ids=[documents[0]],
        note=note,
        confirm=True,
    )
    alone = await refused(stack, stack.admin_token, "add_notes", **chosen, confirm=True)
    rest = await run(
        stack,
        stack.admin_token,
        "add_notes",
        **chosen,
        document_ids=[documents[1]],
        confirm=True,
    )
    again = await run(stack, stack.admin_token, "add_notes", **chosen, confirm=True)
    each = [
        {"document_id": documents[2], "note": "one"},
        {"document_id": documents[2], "note": "b"},
    ]
    own = await run(stack, stack.admin_token, "add_notes", notes=each, confirm=True)

    assert first["counts"] == {"added": 1}
    assert f"document_ids=[{documents[1]}] and confirm=true" in alone
    assert rest["counts"] == {"added": 1}
    assert again["counts"] == {"already_there": 2}
    assert own["counts"] == {"added": 2}
    assert notes_of(stack, documents[0]) == notes_of(stack, documents[1]) == [note.strip()]
    assert set(notes_of(stack, documents[2])) == {"one", "b"}


async def test_a_restricted_user_is_told_what_is_missing_and_changes_nothing(
    stack: Stack, documents: list[int]
) -> None:
    public = stack.seeded.public_document
    before = raw(stack, public)["title"]
    item = {"document_id": public, "title": "Not allowed"}

    hidden = await refused(
        stack,
        stack.reader_token,
        "update_documents",
        documents=[item, {"document_id": documents[0], "title": "x"}],
        confirm=True,
    )
    denied = await refused(
        stack, stack.reader_token, "update_documents", documents=[item], confirm=True
    )
    notes = await refused(
        stack, stack.reader_token, "add_notes", document_ids=[public], note="x", confirm=True
    )

    assert f"document {documents[0]}: It does not exist or is hidden from you." in hidden
    # The global permission to change documents is not visible beforehand: a refusal per document.
    assert "No document is known to be changed" in denied
    assert "may not view notes" in notes
    assert raw(stack, public)["title"] == before


async def test_a_link_target_that_may_not_be_changed_is_refused_before_anything_is_sent(
    stack: Stack, documents: list[int], user: Callable[..., Signed]
) -> None:
    first, second, third = documents
    changer = user("view_document", "change_document", "view_customfield", "view_uisettings")
    patch(stack, first, owner=None)
    patch(stack, third, owner=None)
    share(stack, second, changer.id)
    items = [
        {"document_id": third, "custom_fields": {TEXT: "x"}},
        {"document_id": first, "custom_fields": {LINK: [second]}},
    ]

    wrong = await refused(stack, changer.token, "update_documents", documents=items, confirm=True)
    done = await run(stack, changer.token, "update_documents", documents=items[:1], confirm=True)

    assert f"links to document {second}, which you cannot see or may not change" in wrong
    assert statuses(done) == ["changed"]
    assert fields_of(stack, first) == {}
    assert fields_of(stack, second) == {}


async def test_an_owner_change_by_another_is_refused_beforehand_and_one_by_the_owner_works(
    stack: Stack, documents: list[int], user: Callable[..., Signed]
) -> None:
    mine, theirs, _ = documents
    owner = user("view_document", "change_document", "view_uisettings", "view_user")
    patch(stack, mine, owner=owner.id)
    both: dict[str, list[int]] = {"users": [owner.id], "groups": []}
    patch(stack, theirs, set_permissions={"view": both, "change": both})
    give = {"document_id": mine, "owner": "admin"}
    take = {"document_id": theirs, "owner": owner.id}

    wrong = await refused(
        stack, owner.token, "update_documents", documents=[give, take], confirm=True
    )
    done = await run(stack, owner.token, "update_documents", documents=[give], confirm=True)
    again = await refused(stack, owner.token, "update_documents", documents=[give], confirm=True)

    assert f"document {theirs}: Only its owner may change the owner." in wrong
    assert f"document {mine}:" not in wrong
    assert statuses(done) == ["changed"]
    # What was given away cannot be seen any more, so the same item again is refused.
    assert f"document {mine}: It does not exist or is hidden from you." in again


async def test_the_same_text_as_markdown_is_another_note(
    stack: Stack, documents: list[int]
) -> None:
    chosen = {"document_ids": documents[:2], "note": f"**{uuid.uuid4().hex}**"}
    admin = stack.admin_token

    plain = await run(stack, admin, "add_notes", **chosen, confirm=True)
    markdown = await run(stack, admin, "add_notes", **chosen, format="markdown", confirm=True)
    again = await run(stack, admin, "add_notes", **chosen, format="markdown", confirm=True)

    assert plain["counts"] == markdown["counts"] == {"added": 2}
    assert again["counts"] == {"already_there": 2}
    formats = [str(n["format"]) for n in cast("list[Json]", raw(stack, documents[0])["notes"])]
    assert sorted(formats) == ["markdown", "plain"]


async def test_notes_need_the_right_to_add_them_and_to_change_the_document(
    stack: Stack, documents: list[int], user: Callable[..., Signed]
) -> None:
    first, second, _ = documents
    viewer = user("view_document", "change_document", "view_note")
    adder = user("view_document", "change_document", "view_note", "add_note")
    patch(stack, first, owner=None)
    share(stack, second, adder.id)
    arguments = {"note": "x", "confirm": True}

    no_add = await refused(stack, viewer.token, "add_notes", document_ids=[first], **arguments)
    no_change = await refused(
        stack, adder.token, "add_notes", document_ids=[first, second], **arguments
    )
    added = await run(stack, adder.token, "add_notes", document_ids=[first], **arguments)

    assert "No note is known to be added" in no_add
    assert f"You may not change 1 document ({second} " in no_change
    assert added["counts"] == {"added": 1}
    assert notes_of(stack, first) == ["x"]
    assert notes_of(stack, second) == []


async def test_one_hundred_documents_take_a_few_calls_and_each_ends_in_time(
    stack: Stack, capsys: pytest.CaptureFixture[str]
) -> None:
    def reasons(output: Json) -> set[object]:
        return {obj(row)["why"] for row in cast("list[object]", output["documents"])}

    def told(line: str) -> None:
        with capsys.disabled():
            print(f"\n{line}")  # noqa: T201 -- the measured time, for the task notes

    with bare_documents(100) as title:
        found = await run(
            stack,
            stack.admin_token,
            "read_documents",
            filters={"title_contains": title},
            fields=[],
            page_size=100,
        )
        ids = [num(n) for n in cast("list[object]", found["selected_ids"])]
        assert len(ids) == 100
        # As for a consumed document, the name Paperless gives a file: without one it takes a path
        # that answers a note with the list from before.
        name = "Document.objects.filter(pk=n).update(filename=f'{n:07}.txt')"
        shell(f"[{name} for n in {ids}]")
        try:
            left = ids
            for _ in range(10):
                begun = time.monotonic()
                output = await run(
                    stack,
                    stack.admin_token,
                    "update_documents",
                    documents=[
                        {"document_id": n, "custom_fields": {TEXT: f"own {n}"}} for n in left
                    ],
                    confirm=True,
                )
                took = time.monotonic() - begun
                assert took < 60
                left = [num(n) for n in cast("list[object]", output["left"])]
                told(f"update_documents: {output['counts']} in {took:.1f} s")
                if not left:
                    break
            assert left == []
            notes = [{"document_id": n, "note": f"own {n}"} for n in ids]
            output: Json = {"left": ids}
            for _ in range(10):
                begun = time.monotonic()
                output = await run(stack, stack.admin_token, "add_notes", notes=notes, confirm=True)
                took = time.monotonic() - begun
                assert took < 60
                told(f"add_notes: {output['counts']} in {took:.1f} s; {reasons(output)}")
                # No answer was lost: the normal path is measured.
                assert reasons(output) == {None}
                if not output["left"]:
                    break
            assert output["left"] == []
            assert notes_of(stack, ids[0]) == [f"own {ids[0]}"]
            assert fields_of(stack, ids[-1]) == {field(stack, TEXT): f"own {ids[-1]}"}
        finally:
            # What was added would keep the bare rows from being removed.
            rows = "(q := m.global_objects.filter(document_id__in=ids))._raw_delete(q.db)"
            models = "from documents.models import CustomFieldInstance, Note"
            shell(f"{models}; ids = {ids}; [{rows} for m in (CustomFieldInstance, Note)]")
