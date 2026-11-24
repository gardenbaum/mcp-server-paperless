"""Uploads on a Paperless that rejects duplicates (PAPERLESS_CONSUMER_DELETE_DUPLICATES).

The setting changes every upload of a stack, so these tests run on a stack of their own
(`just e2e-duplicates`, marker `duplicates_e2e`) and skip elsewhere.
"""

import os
from typing import TYPE_CHECKING, cast

import pytest

from e2e.helpers import num, results
from e2e.uploading import Scope, matches, output, unique
from mcp_server_paperless.services.duplicates import HIDDEN_REJECTED

if TYPE_CHECKING:
    from collections.abc import Iterator

    from e2e.helpers import Stack
    from e2e.uploading import Json

REJECTS = os.environ.get("PAPERLESS_E2E_DUPLICATES") == "1"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.duplicates_e2e,
    pytest.mark.anyio,
    pytest.mark.decision("decision-47"),
    pytest.mark.skipif(
        not REJECTS,
        reason="This Paperless keeps duplicates; `just e2e-duplicates` tests one that does not.",
    ),
]


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    made.clean_up()


def rejected_for(done: Json) -> tuple[int, int | None, bool]:
    check = cast("Json", done["duplicates"])
    [match] = matches({"all": [check["rejected_for"]]}, "all")
    return match


async def test_a_rejected_copy_names_the_existing_document_only(stack: Stack, scope: Scope) -> None:
    content = unique()
    known = scope.document(content)

    done = output(await scope.upload(content, confirm=True))
    task = output(await scope.call("get_task", task_id=done["task_id"]))
    listed = output(await scope.call("list_tasks", state="failure"))

    [wire] = results(stack.admin.get("/api/tasks/", params={"task_id": str(done["task_id"])}))
    assert wire["status"] == "failure"
    assert wire["related_document_ids"] == [known]
    assert cast("Json", wire["result_data"])["duplicate_of"] == known
    for report in (done, task):
        assert report["state"] == "failure"
        assert f"document {known} 'plmcp77-" in str(report["message"])
        assert rejected_for(report) == (known, None, False)
    assert (done["document_id"], task["document_ids"]) == (None, [])
    [row] = [t for t in cast("list[Json]", listed["tasks"]) if t["task_id"] == done["task_id"]]
    assert (row["document_ids"], "already holds" in str(row["message"])) == ([], True)


async def test_a_copy_of_something_in_the_trash_and_of_a_version_is_rejected(scope: Scope) -> None:
    trashed, newer = unique(), unique()
    gone, document = scope.document(trashed), scope.document(unique())
    _ = output(await scope.call("delete_document", document_id=gone, confirm=True))
    version = num(output(await scope.upload(newer, document_id=document))["version_id"])

    of_trash = output(await scope.upload(trashed, confirm=True))
    of_version = output(await scope.upload(newer, confirm=True))

    assert rejected_for(of_trash) == (gone, None, True)
    assert "in the trash" in str(of_trash["message"])
    assert rejected_for(of_version) == (document, version, False)


async def test_a_version_is_rejected_too(scope: Scope) -> None:
    content = unique()
    known, target = scope.document(content), scope.document(unique())

    done = output(await scope.upload(content, document_id=target, confirm=True))

    assert (done["state"], done["version_id"]) == ("failure", None)
    assert rejected_for(done) == (known, None, False)


async def test_a_copy_of_a_hidden_document_is_rejected_without_naming_it(scope: Scope) -> None:
    content = unique()
    title = f"plmcp77-hidden-{unique().decode()[-12:]}"
    hidden = scope.document(content, title)
    token = scope.user("add_document", "view_document", "view_paperlesstask")

    done = output(await scope.upload(content, token))
    task = output(await scope.call("get_task", token, task_id=done["task_id"]))

    for report in (done, task):
        assert (report["state"], report["message"]) == ("failure", HIDDEN_REJECTED)
        check = cast("Json", report["duplicates"])
        assert check["rejected_for"] is None
        assert title not in str(report)
        assert f"'document_id': {hidden}," not in str(report)
