"""Uploads of a file Paperless already holds, on Paperless 3.5.1 as it ships (duplicates are kept).

Each test uploads files of its own and deletes what it made, also when it fails.
"""

import base64
from typing import TYPE_CHECKING, cast

import pytest
from confirmation import YES, answering
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from e2e.helpers import num, results
from e2e.test_stdio import console_script
from e2e.uploading import Scope, matches, output, sha256, text, unique
from mcp_server_paperless.tools.upload_hint import PATH_OFF, PATH_REFUSED

if TYPE_CHECKING:
    from collections.abc import Iterator

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-47")]


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    made.clean_up()


async def test_a_new_file_is_uploaded_at_once_with_its_sha256(stack: Stack, scope: Scope) -> None:
    content = unique()

    done = output(await scope.upload(content))

    assert done["state"] == "success"
    check = cast("dict[str, object]", done["duplicates"])
    assert (check["before"], check["after"], check["rejected_for"]) == ([], [], None)
    assert check["not_checked"]
    [row] = results(
        stack.admin.get("/api/documents/", params={"checksum__iexact": sha256(content)})
    )
    assert num(row["id"]) == done["document_id"]


async def test_a_known_file_is_sent_only_with_confirm_and_reported_before_and_after(
    stack: Stack, scope: Scope
) -> None:
    content = unique()
    known = scope.document(content)

    refused = await scope.upload(content)
    untouched = results(
        stack.admin.get("/api/documents/", params={"checksum__iexact": sha256(content)})
    )
    done = output(await scope.upload(content, confirm=True, wait_seconds=0))
    task = output(await scope.call("get_task", task_id=done["task_id"], wait_seconds=30))
    scope.documents += cast("list[int]", task["document_ids"])

    assert refused.is_error
    assert "Nothing was changed. Confirming would upload 'x.txt'" in text(refused)
    assert f"document {known} 'plmcp77-" in text(refused)
    assert [num(row["id"]) for row in untouched] == [known]
    assert matches(done["duplicates"], "before") == [(known, None, False)]
    # Not waited for: what Paperless found is in the task.
    assert task["state"] == "success"
    assert matches(task["duplicates"], "after") == [(known, None, False)]


async def test_versions_and_the_trash_are_found_before_sending(scope: Scope) -> None:
    first, second, third = unique(), unique(), unique()
    document = scope.document(unique())
    version = num(output(await scope.upload(first, document_id=document))["version_id"])
    deleted = num(output(await scope.upload(second, document_id=document))["version_id"])
    _ = output(
        await scope.call(
            "delete_document_version", document_id=document, version_id=deleted, confirm=True
        )
    )
    gone = scope.document(third)
    _ = output(await scope.call("delete_document", document_id=gone, confirm=True))

    assert f"version {version} of document {document} " in text(await scope.upload(first))
    assert f"version {deleted} of document {document} " in text(await scope.upload(second))
    trashed = text(await scope.upload(third))
    assert f"document {gone} " in trashed
    assert "in the trash" in trashed
    done = output(await scope.upload(third, confirm=True))
    assert matches(done["duplicates"], "before") == [(gone, None, True)]
    assert matches(done["duplicates"], "after") == [(gone, None, True)]


async def test_a_version_is_checked_like_a_document(scope: Scope) -> None:
    content = unique()
    known, target = scope.document(content), scope.document(unique())

    refused = await scope.upload(content, document_id=target)
    done = output(await scope.upload(content, document_id=target, confirm=True))
    again = await scope.upload(content, document_id=target)

    assert f"as a new version of document {target} 'plmcp77-" in text(refused)
    assert f"document {known} " in text(refused)
    assert matches(done["duplicates"], "before") == [(known, None, False)]
    assert matches(done["duplicates"], "after") == [(known, None, False)]
    assert f"version {done['version_id']} of document {target} " in text(again)


async def test_a_restricted_user_learns_nothing_of_hidden_documents(scope: Scope) -> None:
    content = unique()
    hidden = scope.document(content)
    token = scope.user("add_document", "view_document", "view_paperlesstask")

    done = output(await scope.upload(content, token))

    assert done["state"] == "success"
    assert matches(done["duplicates"], "before") == []
    assert matches(done["duplicates"], "after") == []
    assert f"'document_id': {hidden}," not in str(done)
    # The admin sees both.
    again = text(await scope.upload(content))
    assert f"document {hidden} " in again
    assert f"document {done['document_id']} " in again


async def test_the_dialog_of_the_console_script_uploads_after_a_yes(
    stack: Stack, scope: Scope
) -> None:
    content = unique()
    known = scope.document(content)
    env = {"PAPERLESS_URL": stack.base_url, "PAPERLESS_TOKEN": stack.admin_token}
    arguments = {
        "content_base64": base64.b64encode(content).decode(),
        "filename": "x.txt",
        "wait_seconds": 30,
    }
    seen: list[str] = []

    async def call(action: str, reply: object) -> dict[str, object]:
        transport = StdioTransport(console_script(), [], env=env, keep_alive=False)
        handler = answering(action, reply, seen=seen)
        async with Client(transport, elicitation_handler=handler) as client:
            result = await client.call_tool("upload_document", arguments, raise_on_error=False)
            path = await client.call_tool(
                "upload_document", {"path": "/srv/secret.pdf"}, raise_on_error=False
            )
        assert text(path) == f"{PATH_REFUSED} Hint: {PATH_OFF}"
        return result.structured_content or {}

    assert await call("decline", None) == {}
    done = await call("accept", YES)
    scope.documents.append(num(done["document_id"]))

    assert f"document {known} " in seen[0]
    assert done["state"] == "success"
