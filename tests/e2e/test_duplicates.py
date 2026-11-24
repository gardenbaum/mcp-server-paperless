"""Finding and resolving duplicates on Paperless 3.5.1, on documents of the tests' own."""

from typing import TYPE_CHECKING, cast

import pytest

from e2e.helpers import lose_file, num, results, seq
from e2e.uploading import Scope, output, text, unique

if TYPE_CHECKING:
    from collections.abc import Iterator

    from e2e.helpers import Stack
    from e2e.uploading import Json

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-51")]

LIMITED = ("view_document", "change_document", "delete_document", "view_paperlesstask")


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    made.clean_up()


async def with_duplicates(scope: Scope, title: str, token: str | None = None) -> set[int]:
    """The documents of this exact title that have duplicates."""
    found = output(
        await scope.call("search_documents", token, title_contains=title, has_duplicates=True)
    )
    return {num(d["id"]) for d in cast("list[Json]", found["documents"])}


async def duplicates(scope: Scope, document: int, token: str | None = None) -> Json:
    read = output(
        await scope.call("get_document", token, document_id=document, include=["duplicates"])
    )
    return cast("Json", read["duplicates"])


def listed(found: Json) -> list[Json]:
    return cast("list[Json]", found["documents"])


async def versions(scope: Scope, document: int) -> list[int]:
    read = output(await scope.call("list_document_versions", document_id=document))
    return [num(v["id"]) for v in cast("list[Json]", read["versions"])]


def in_trash(stack: Stack, document: int) -> bool:
    answer = stack.admin.get(f"/api/documents/{document}/")
    rows = results(stack.admin.get("/api/trash/", params={"page_size": 100_000}))
    ids = {num(row["id"]) for row in rows}
    return answer.status_code == 404 and document in ids


def share(stack: Stack, document: int, user: int, *, change: bool = False) -> None:
    """Let a user see a document of the admin, or change it too."""
    users: list[int] = [user] if change else []
    grants: Json = {
        "view": {"users": [user], "groups": []},
        "change": {"users": users, "groups": []},
    }
    _ = stack.admin.patch(
        f"/api/documents/{document}/", json={"set_permissions": grants}
    ).raise_for_status()


async def test_duplicates_are_found_and_told_apart_also_in_the_trash(scope: Scope) -> None:
    content = unique()
    keep, copy, gone = (scope.document(content) for _ in range(3))
    alone = scope.document(unique())
    title = str(output(await scope.call("get_document", document_id=copy))["title"])
    _ = output(await scope.call("add_note", document_id=copy, note="plmcp86"))
    _ = output(await scope.call("delete_document", document_id=gone, confirm=True))

    assert await with_duplicates(scope, title) == {copy}
    alone_title = str(output(await scope.call("get_document", document_id=alone))["title"])
    assert await with_duplicates(scope, alone_title) == set()
    found = await duplicates(scope, keep)

    assert (found["total"], found["truncated"]) == (2, False)
    live, trashed = listed(found)
    assert (live["id"], live["in_trash"], live["match"]) == (copy, False, "same_file")
    assert live["notes_count"] == 1
    assert {"title", "notes_count"} <= set(seq(live["differs"]))
    assert (trashed["id"], trashed["in_trash"], trashed["match"]) == (gone, True, "same_file")
    assert trashed["created"] is not None
    assert trashed["notes_count"] is None
    # The copy in the trash alone makes the filter true.
    _ = output(await scope.call("delete_document", document_id=copy, confirm=True))
    kept = str(output(await scope.call("get_document", document_id=keep))["title"])
    assert await with_duplicates(scope, kept) == {keep}


async def test_duplicates_go_to_the_trash_and_can_be_restored(stack: Stack, scope: Scope) -> None:
    content = unique()
    keep, copy, other = (scope.document(content) for _ in range(3))
    version = num(output(await scope.upload(unique(), document_id=copy))["version_id"])
    call: Json = {"keep_document_id": keep, "duplicate_ids": [copy], "action": "trash"}

    preview = output(await scope.call("resolve_duplicates", None, **call, dry_run=True))
    refused = await scope.call("resolve_duplicates", None, **call)
    assert not in_trash(stack, copy)
    done = output(await scope.call("resolve_duplicates", None, **call, confirm=True))

    assert (preview["dry_run"], preview["applied"]) == (True, False)
    assert refused.is_error
    assert f"Document {copy} has 0 notes" in text(refused)
    assert "2 versions, its newest file is not the one that was compared" in text(refused)
    assert "can be restored until the trash is emptied" in text(refused)
    assert [d["id"] for d in cast("list[Json]", done["resolved"])] == [copy]
    assert (done["not_resolved"], done["unconfirmed_task_id"]) == ([], None)
    assert [o["document_id"] for o in cast("list[Json]", done["other_duplicates"])] == [other]
    assert in_trash(stack, copy)
    _ = output(await scope.call("restore_from_trash", document_ids=[copy]))
    assert await versions(scope, copy) == [version, copy]


async def test_duplicates_are_merged_as_versions_for_good(stack: Stack, scope: Scope) -> None:
    content = unique()
    keep, copy = scope.document(content), scope.document(content)
    number = keep * 1000 + 7
    _ = output(await scope.call("update_document", document_id=copy, archive_serial_number=number))
    call: Json = {"keep_document_id": keep, "duplicate_ids": [copy], "action": "merge_as_versions"}

    refused = await scope.call("resolve_duplicates", None, **call)
    done = output(await scope.call("resolve_duplicates", None, **call, confirm=True))

    assert "vanishes for good, not even into the trash" in text(refused)
    assert f"document {keep} takes the archive serial number {number}" in text(refused)
    assert [d["id"] for d in cast("list[Json]", done["resolved"])] == [copy]
    assert await versions(scope, keep) == [copy, keep]
    assert not in_trash(stack, copy)
    read = output(await scope.call("get_document", document_id=keep))
    assert read["archive_serial_number"] == number


async def test_nothing_is_sent_for_what_is_no_live_duplicate(stack: Stack, scope: Scope) -> None:
    content = unique()
    keep, copy, gone = (scope.document(content) for _ in range(3))
    other = scope.document(unique())
    _ = output(await scope.call("delete_document", document_id=gone, confirm=True))

    refused = await scope.call(
        "resolve_duplicates",
        keep_document_id=keep,
        duplicate_ids=[copy, other, gone],
        action="trash",
        confirm=True,
    )

    assert refused.is_error
    assert f"document {other}: Paperless does not take it for a duplicate" in text(refused)
    assert f"document {gone}: it is in the trash already" in text(refused)
    assert not in_trash(stack, copy)


async def test_a_restricted_user_learns_nothing_of_hidden_copies_and_changes_nothing_in_part(
    stack: Stack, scope: Scope
) -> None:
    content = unique()
    keep, shared, hidden = (scope.document(content) for _ in range(3))
    token = scope.user(*LIMITED)
    [user] = scope.users
    share(stack, keep, user, change=True)
    share(stack, shared, user)

    async def resolve(*ids: int, action: str = "trash") -> str:
        return text(
            await scope.call(
                "resolve_duplicates",
                token,
                keep_document_id=keep,
                duplicate_ids=list(ids),
                action=action,
                confirm=True,
            )
        )

    found = await duplicates(scope, keep, token)
    texts = {
        (await resolve(hidden)).replace(str(hidden), "N"),
        (await resolve(999_999)).replace("999999", "N"),
    }
    blocked = await resolve(shared)
    # A change grant is not enough: the documents are not the user's own.
    share(stack, shared, user, change=True)
    denied = [await resolve(shared, action=a) for a in ("trash", "merge_as_versions")]

    assert [d["id"] for d in listed(found)] == [shared]
    assert len(texts) == 1
    assert f"document {shared} " in blocked
    assert "this user may not change it" in blocked
    assert all("nothing was changed" in answer for answer in denied)
    for document in (keep, shared, hidden):
        assert stack.admin.get(f"/api/documents/{document}/").status_code == 200


async def test_the_trash_of_others_is_listed_by_title_only(scope: Scope, stack: Stack) -> None:
    content = unique()
    keep, gone = scope.document(content), scope.document(content)
    token = scope.user("view_document")
    [user] = scope.users
    share(stack, keep, user)
    share(stack, gone, user)
    _ = output(await scope.call("delete_document", document_id=gone, confirm=True))

    found = await duplicates(scope, keep, token)

    [trashed] = listed(found)
    assert (trashed["id"], trashed["in_trash"], trashed["match"]) == (gone, True, None)
    assert any(str(gone) in gap for gap in cast("list[str]", found["not_compared"]))


@pytest.mark.decision("decision-63")
@pytest.mark.parametrize("action", ["trash", "merge_as_versions"])
async def test_the_copy_whose_file_exists_is_the_one_to_keep(
    stack: Stack, scope: Scope, action: str
) -> None:
    content = unique()
    lost, whole = scope.document(content), scope.document(content)
    lose_file(stack.admin, lost, "original")
    call: Json = {"keep_document_id": lost, "duplicate_ids": [whole], "action": action}
    other: Json = {"keep_document_id": whole, "duplicate_ids": [lost], "action": action}

    preview = output(await scope.call("resolve_duplicates", None, **call, dry_run=True))
    refused = await scope.call("resolve_duplicates", None, **call, confirm=True)
    assert stack.admin.get(f"/api/documents/{whole}/").status_code == 200
    done = output(await scope.call("resolve_duplicates", None, **other, confirm=True))

    why = f"Paperless cannot find its file, and document {whole} still has it"
    assert [d["why"] for d in cast("list[Json]", preview["not_resolved"])] == [why]
    assert refused.is_error
    assert why in text(refused)
    assert [d["id"] for d in cast("list[Json]", done["resolved"])] == [lost]
    assert "WARNING" not in str(done["message"])


@pytest.mark.decision("decision-63")
async def test_a_file_no_copy_has_is_named_before_and_after(stack: Stack, scope: Scope) -> None:
    content = unique()
    keep, copy = scope.document(content), scope.document(content)
    for document in (keep, copy):
        lose_file(stack.admin, document, "original")
    call: Json = {"keep_document_id": keep, "duplicate_ids": [copy], "action": "trash"}

    preview = output(await scope.call("resolve_duplicates", None, **call, dry_run=True))
    asked = await scope.call("resolve_duplicates", None, **call)
    done = output(await scope.call("resolve_duplicates", None, **call, confirm=True))

    for message in (str(preview["message"]), text(asked), str(done["message"])):
        assert f"WARNING: Paperless cannot find the original of document {keep}." in message
        assert f"Paperless cannot find the original of document {copy}." in message
    assert in_trash(stack, copy)
