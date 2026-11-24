"""upload_documents through the console script over stdio, on Paperless 3.5.1 (decision-50)."""

import os
import threading
from typing import TYPE_CHECKING, cast

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from e2e.helpers import num, results
from e2e.test_stdio import console_script
from e2e.uploading import Json, Scope, matches, output, sha256, text, unique

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastmcp.client.client import CallToolResult

    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio, pytest.mark.decision("decision-50")]

TOOL = "upload_documents"


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    made.clean_up()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "scans"
    root.mkdir()
    return root


def client(stack: Stack, folder: Path) -> Client[StdioTransport]:
    env = {
        "PAPERLESS_URL": stack.base_url,
        "PAPERLESS_TOKEN": stack.admin_token,
        "PAPERLESS_MCP_TOOLSETS": "core,bulk",
        "PAPERLESS_MCP_UPLOAD_DIRS": str(folder),
    }
    return Client(StdioTransport(console_script(), [], env=env, keep_alive=False))


async def call(scope: Scope, folder: Path, **arguments: object) -> CallToolResult:
    """Call the tool; what it uploaded is cleaned up with the scope, also if it was not waited."""
    async with client(scope.stack, folder) as session:
        result = await session.call_tool(TOOL, arguments, raise_on_error=False)
        for file in files(result.structured_content or {"files": []}):
            if file["task_id"] is None:
                continue
            task = await session.call_tool(
                "get_task", {"task_id": file["task_id"], "wait_seconds": 30}
            )
            scope.documents += cast(
                "list[int]", (task.structured_content or {}).get("document_ids", [])
            )
    return result


def files(batch: Json) -> list[Json]:
    return cast("list[Json]", batch["files"])


def statuses(batch: Json) -> dict[str, object]:
    return {os.path.basename(str(f["path"])): f["status"] for f in files(batch)}  # noqa: PTH119


async def test_a_folder_with_every_kind_of_entry(
    scope: Scope, folder: Path, tmp_path: Path
) -> None:
    known, fresh, linked = unique(), unique(), unique()
    document = scope.document(known)
    outside = tmp_path / "outside"
    outside.mkdir()
    _ = (outside / "secret.txt").write_bytes(unique())
    contents = {
        "a.txt": fresh,
        "b-known.txt": known,
        "c-twin.txt": fresh,
        "d-empty.txt": b"",
        "e-hard.txt": linked,
        ".env": unique(),
        "sub/deep.txt": unique(),
        ".git/config": unique(),
    }
    for name, content in contents.items():
        (folder / name).parent.mkdir(exist_ok=True)
        _ = (folder / name).write_bytes(content)
    os.link(folder / "e-hard.txt", outside / "other.txt")
    (folder / "f-out.txt").symlink_to(outside / "secret.txt")
    (folder / "g-dir").symlink_to(outside, target_is_directory=True)
    (folder / "h-in.txt").symlink_to(folder / "sub" / "deep.txt")

    batch = output(await call(scope, folder, directory=str(folder), wait_seconds=30))

    assert statuses(batch) == {
        "a.txt": "uploaded",
        "b-known.txt": "duplicate",
        "c-twin.txt": "duplicate",
        "d-empty.txt": "refused",
        "e-hard.txt": "refused",
        "f-out.txt": "refused",
        "g-dir": "refused",
        "h-in.txt": "uploaded",
    }
    refusals = {f["reason"] for f in files(batch)[4:7]}
    assert refusals == {"No file was found at that path inside the allowed directories."}
    assert matches(files(batch)[1]["duplicates"], "before") == [(document, None, False)]
    assert batch["directories"] == [str(folder / "sub")]
    assert "dot: 2" in str(batch["notes"])
    assert "secret" not in str(batch)
    assert ".env" not in str(batch)
    for name in ("a.txt", "sub/deep.txt"):
        [row] = results(
            scope.stack.admin.get(
                "/api/documents/", params={"checksum__iexact": sha256(contents[name])}
            )
        )
        assert num(row["id"]) in scope.documents


async def test_a_long_folder_is_covered_by_two_calls(scope: Scope, folder: Path) -> None:
    for number in range(24):
        _ = (folder / f"{number:02}.txt").write_bytes(b"")  # refused, but reported
    for name in ("x.txt", "y.txt"):
        _ = (folder / name).write_bytes(unique())

    first = output(await call(scope, folder, directory=str(folder)))
    after = first["next_after"]
    second = output(await call(scope, folder, directory=str(folder), after=after))

    assert len(files(first)) == 25
    assert first["left_out"] == [str(folder / "y.txt")]
    assert statuses(first)["x.txt"] == "uploaded"
    assert statuses(second) == {"y.txt": "uploaded"}
    assert second["next_after"] is None


async def test_paths_recursion_dry_run_and_traversal(
    scope: Scope, folder: Path, tmp_path: Path
) -> None:
    (folder / "sub").mkdir()
    for name in ("a.txt", "sub/b.txt"):
        _ = (folder / name).write_bytes(unique())
    _ = (tmp_path / "secret.txt").write_bytes(unique())

    tried = output(await call(scope, folder, directory=str(folder), recursive=True, dry_run=True))
    given = [str(folder / "a.txt"), f"{folder}/../secret.txt", str(tmp_path / "secret.txt")]
    batch = output(await call(scope, folder, paths=given, wait_seconds=30))
    outside = await call(scope, folder, directory=str(tmp_path))
    parent = await call(scope, folder, directory=f"{folder}/sub/..")
    missing = await call(scope, folder, directory=str(tmp_path / "missing"))

    assert statuses(tried) == {"a.txt": "would_upload", "b.txt": "would_upload"}
    assert [f["status"] for f in files(batch)] == ["uploaded", "refused", "refused"]
    assert files(batch)[1]["reason"] == files(batch)[2]["reason"]
    assert outside.is_error
    assert text(outside) == text(parent) == text(missing)


async def test_a_file_swapped_all_the_time_is_never_read_from_outside(
    scope: Scope, folder: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    _ = secret.write_bytes(b"s" * 999)
    inside, target = tmp_path / "inside.txt", folder / "a.txt"
    _ = inside.write_bytes(unique())
    size = inside.stat().st_size
    done = threading.Event()

    def swap() -> None:
        new = folder / ".new"
        while not done.is_set():
            new.symlink_to(secret)
            _ = new.replace(target)
            _ = new.write_bytes(inside.read_bytes())
            _ = new.replace(target)

    thread = threading.Thread(target=swap)
    thread.start()
    try:
        async with client(scope.stack, folder) as session:
            for _ in range(20):
                result = await session.call_tool(
                    TOOL, {"paths": [str(target)], "dry_run": True}, raise_on_error=False
                )
                [file] = files(output(result))
                assert file["size_bytes"] in {None, size}
    finally:
        done.set()
        thread.join()


async def test_http_callers_never_see_it(scope: Scope, folder: Path) -> None:
    result = await scope.call(TOOL, directory=str(folder))

    assert result.is_error
    assert "Unknown tool" in text(result)
