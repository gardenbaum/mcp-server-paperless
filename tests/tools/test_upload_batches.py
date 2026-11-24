"""upload_documents through the in-memory client: where it exists, its schema, its reports."""

import os
import threading
import time
from typing import TYPE_CHECKING, Literal, cast

import pytest
from e2e.helpers import pdf
from fakes.paperless import FakeDocument, FakePaperless, seeded
from fastmcp import Client, FastMCP
from fastmcp.server.context import (
    _current_transport,  # pyright: ignore[reportPrivateUsage] -- the seam FastMCP itself sets
)
from harness import admin, paperless_settings, serve

from mcp_server_paperless.app import create_app
from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.services.batch_uploads import cursor
from mcp_server_paperless.settings import HttpSettings, ServerSettings
from mcp_server_paperless.tools._shared import (
    ANNOTATIONS,
    LOCAL_ANNOTATIONS,
    LOCAL_TOOLSETS,
    TOOL_TOOLSETS,
    TOOLSETS,
)

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.client.client import CallToolResult

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-7"),
    pytest.mark.decision("decision-50"),
]

TOOL = "upload_documents"
type Json = dict[str, object]


def writing() -> FakePaperless:
    return FakePaperless(seeded(), user="admin", allow_writes=True)


def local(
    root: Path, fake: FakePaperless | None = None, *, toolsets: list[str] | None = None
) -> FastMCP:
    return serve(
        fake or writing(),
        toolsets=toolsets or ["core", "bulk"],
        stdio=True,
        upload_dirs=[str(root)],
    )


async def call(root: Path, fake: FakePaperless | None = None, **arguments: object) -> Json:
    async with Client(local(root, fake)) as client:
        result = await client.call_tool(TOOL, arguments)
    return result.structured_content or {}


async def refusal(root: Path, fake: FakePaperless | None = None, **arguments: object) -> str:
    async with Client(local(root, fake)) as client:
        result: CallToolResult = await client.call_tool(TOOL, arguments, raise_on_error=False)
    assert result.is_error
    return "".join(getattr(block, "text", "") for block in result.content)


def files(output: Json) -> list[Json]:
    return cast("list[Json]", output["files"])


# --- where it exists -----------------------------------------------------------------------------


async def everything_told(server: FastMCP) -> str:
    """All a client is told: tools, resources, prompts, instructions and the overview."""
    async with Client(server) as client:
        listed = [
            *await client.list_tools(),
            *await client.list_resources(),
            *await client.list_resource_templates(),
            *await client.list_prompts(),
        ]
        overview = await client.call_tool("get_instance_overview")
        return f"{client.instructions}{listed}{overview.structured_content}"


@pytest.mark.parametrize("toolsets", [["core"], ["all"], ["core", "bulk"]])
async def test_nothing_names_it_without_stdio_and_an_allowlist(
    tmp_path: Path, toolsets: list[str]
) -> None:
    fake = admin(ai_enabled=True)
    servers = [
        serve(fake, toolsets=toolsets),
        serve(fake, toolsets=toolsets, upload_dirs=[str(tmp_path)]),
        serve(fake, toolsets=toolsets, stdio=True),
        serve(fake, toolsets=toolsets, stdio=True, download_dirs=[str(tmp_path)]),
    ]

    for server in servers:
        assert TOOL not in await everything_told(server)
    # The check can find it.
    everywhere = serve(fake, toolsets=toolsets, stdio=True, upload_dirs=[str(tmp_path)])
    assert TOOL in await everything_told(everywhere)


async def test_the_http_entrypoint_never_offers_it(tmp_path: Path) -> None:
    app = create_app(
        paperless_settings(),
        ServerSettings.model_validate({"toolsets": "all", "upload_dirs": str(tmp_path)}),
        HttpSettings.model_validate({"allow_unauthenticated_loopback": True}),
    )
    server = cast("FastMCP", app.state.fastmcp_server)

    assert TOOL not in {tool.name for tool in await server.list_tools()}


async def test_bulk_is_whole_in_every_mode(tmp_path: Path) -> None:
    """Without it, bulk is not said to be cut down; with it, the guide lists it."""
    fake = admin()
    plain = serve(fake, toolsets=["core"])
    by_path = serve(fake, toolsets=["core"], stdio=True, upload_dirs=[str(tmp_path)])

    entries: list[Json] = []
    for server in (plain, by_path):
        async with Client(server) as client:
            overview = await client.call_tool("get_instance_overview")
        guide = cast("Json", (overview.structured_content or {})["toolsets"])
        off = cast("list[Json]", guide["off"])
        entries += [entry for entry in off if entry["toolset"] == "bulk"]

    assert entries[0]["what"] == entries[1]["what"] != "some of its tools"
    shared = ["bulk_edit_documents", "bulk_edit_taxonomy", "resolve_duplicates"]
    assert entries[0]["tools"] == shared
    assert entries[1]["tools"] == [*shared, TOOL]


async def test_the_default_profile_does_not_have_it(tmp_path: Path) -> None:
    server = serve(admin(ai_enabled=True), stdio=True, upload_dirs=[str(tmp_path)])

    tools = await server.list_tools()

    assert TOOL not in {tool.name for tool in tools}
    assert len(tools) == 20


async def test_read_only_mode_and_disabling_take_it_out_and_its_name_is_always_known(
    tmp_path: Path,
) -> None:
    dirs = [str(tmp_path)]
    read_only = serve(admin(), toolsets=["all"], stdio=True, upload_dirs=dirs, read_only=True)
    disabled = serve(admin(), toolsets=["all"], stdio=True, upload_dirs=dirs, disabled_tools=[TOOL])

    for server in (read_only, disabled, serve(admin(), toolsets=["all"], disabled_tools=[TOOL])):
        assert TOOL not in {tool.name for tool in await server.list_tools()}
    with pytest.raises(InvalidConfiguration, match="Did you mean upload_documents"):
        _ = serve(admin(), disabled_tools=["uplod_documents"])


async def test_a_selection_of_this_tool_alone_is_empty_over_http(tmp_path: Path) -> None:
    others = [name for name, toolset in TOOL_TOOLSETS.items() if toolset == "bulk"]
    server = serve(
        admin(), toolsets=["bulk"], disabled_tools=others, stdio=True, upload_dirs=[str(tmp_path)]
    )

    assert [tool.name for tool in await server.list_tools()] == [TOOL]
    with pytest.raises(InvalidConfiguration, match="No tools are left"):
        _ = serve(admin(), toolsets=["bulk"], disabled_tools=others)


async def test_its_annotations_and_toolset(tmp_path: Path) -> None:
    [tool] = [t for t in await local(tmp_path).list_tools() if t.name == TOOL]

    assert tool.annotations == LOCAL_ANNOTATIONS[TOOL]
    assert tool.annotations is not None
    hints = tool.annotations
    assert (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint) == (
        False,
        False,
        False,
    )
    assert tool.tags == {LOCAL_TOOLSETS[TOOL]}
    assert set(LOCAL_ANNOTATIONS) == set(LOCAL_TOOLSETS)
    assert set(LOCAL_TOOLSETS.values()) <= TOOLSETS
    assert set(LOCAL_TOOLSETS).isdisjoint({*TOOL_TOOLSETS, *ANNOTATIONS})
    assert "confirm" not in cast("Json", tool.parameters["properties"])


@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
@pytest.mark.parametrize("how", ["paths", "directory"])
async def test_the_second_lock_refuses_a_request_over_http(
    tmp_path: Path, transport: Literal["sse", "streamable-http"], how: str
) -> None:
    _ = (tmp_path / "scan.txt").write_bytes(b"text")
    fake = writing()
    arguments: Json = {"directory": str(tmp_path)}
    if how == "paths":
        arguments = {"paths": [str(tmp_path / "scan.txt")]}
    token = _current_transport.set(transport)
    try:
        message = await refusal(tmp_path, fake, **arguments)
    finally:
        _current_transport.reset(token)

    assert "only be used when the server runs over stdio" in message
    assert fake.state.tasks == []


# --- calls ---------------------------------------------------------------------------------------


async def test_mixed_outcomes_have_one_report_each(tmp_path: Path) -> None:
    fake = writing()
    fake.state.documents.append(FakeDocument(id=50, title="Known", owner=2, original=pdf("known")))
    _ = (tmp_path / "a.pdf").write_bytes(pdf("a"))
    _ = (tmp_path / "b.pdf").write_bytes(pdf("known"))
    _ = (tmp_path / "c.pdf").write_bytes(b"")
    os.link(tmp_path / "a.pdf", tmp_path.parent / f"{tmp_path.name}-other.pdf")
    _ = (tmp_path / "d.pdf").write_bytes(pdf("d"))

    output = await call(tmp_path, fake, directory=str(tmp_path), correspondent="e2e-acme")

    assert [f["status"] for f in files(output)] == ["refused", "duplicate", "refused", "uploaded"]
    assert output["counts"] == {"refused": 2, "duplicate": 1, "uploaded": 1}
    [task] = fake.state.tasks
    assert files(output)[3]["task_id"] == task.task_id
    [made] = [d for d in fake.state.documents if d.id in task.document_ids]
    assert made.correspondent == 1
    duplicates = cast("Json", files(output)[1]["duplicates"])
    assert [m["document_id"] for m in cast("list[Json]", duplicates["before"])] == [50]
    assert "confirm=true" in str(output["notes"])


async def test_every_argument_reaches_the_call(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    for name in ("a.pdf", "b.pdf", "sub/c.pdf"):
        _ = (tmp_path / name).write_bytes(pdf(name))
    fake = writing()

    output = await call(
        tmp_path,
        fake,
        directory=str(tmp_path),
        recursive=True,
        after=cursor(str(tmp_path / "a.pdf"), str(tmp_path), recursive=True),
        document_type=1,
        storage_path=1,
        tags=["e2e-inbox"],
        created="2026-01-02",
        custom_fields={"e2e-string": "x"},
        wait_seconds=1,
        dry_run=True,
    )

    assert [f["path"] for f in files(output)] == [str(tmp_path / n) for n in ("b.pdf", "sub/c.pdf")]
    assert {f["status"] for f in files(output)} == {"would_upload"}
    assert output["dry_run"] is True
    assert fake.state.tasks == []


async def test_paths_upload_in_the_order_given(tmp_path: Path) -> None:
    for name in ("a.pdf", "b.pdf"):
        _ = (tmp_path / name).write_bytes(pdf(name))
    given = [str(tmp_path / name) for name in ("b.pdf", "a.pdf", "../x.pdf")]

    output = await call(tmp_path, paths=given)

    assert [f["path"] for f in files(output)] == given
    assert [f["status"] for f in files(output)] == ["uploaded", "uploaded", "refused"]


async def test_the_arguments_must_go_together(tmp_path: Path) -> None:
    one = [str(tmp_path / "a.pdf")]

    assert "exactly one of paths and directory" in await refusal(tmp_path)
    assert "exactly one" in await refusal(tmp_path, paths=one, directory=str(tmp_path))
    assert "with directory only" in await refusal(tmp_path, paths=one, recursive=True)
    assert "with directory only" in await refusal(tmp_path, paths=one, after="x")
    assert "at most 25" in await refusal(tmp_path, paths=[f"/x/{n}" for n in range(26)])


async def test_a_directory_outside_says_nothing_about_it(tmp_path: Path) -> None:
    inside, outside = tmp_path / "in", tmp_path / "out"
    inside.mkdir()
    outside.mkdir()

    there = await refusal(inside, directory=str(outside))
    missing = await refusal(inside, directory=str(tmp_path / "nothing"))

    assert there == missing
    assert str(outside) not in there


async def test_unknown_metadata_sends_nothing(tmp_path: Path) -> None:
    _ = (tmp_path / "a.pdf").write_bytes(pdf("a"))
    fake = writing()

    message = await refusal(tmp_path, fake, directory=str(tmp_path), tags=["no such tag"])

    assert "no such tag" in message
    assert fake.state.tasks == []


async def test_a_file_swapped_all_the_time_is_never_read_from_outside(tmp_path: Path) -> None:
    root, secret = tmp_path / "scans", tmp_path / "secret.txt"
    root.mkdir()
    _ = secret.write_bytes(b"s" * 999)
    target, done = root / "a.txt", threading.Event()

    def swap() -> None:
        new = root / ".new"
        while not done.is_set():
            new.symlink_to(secret)
            _ = new.replace(target)
            _ = new.write_bytes(b"text")
            _ = new.replace(target)
            _ = done.wait(0.0015)  # long enough to be read whole on a slow machine

    thread = threading.Thread(target=swap)
    thread.start()
    seen: set[object] = set()
    try:
        async with Client(local(root)) as client:
            rounds, deadline = 0, time.monotonic() + 30
            while rounds < 100 or (seen < {None, 4} and time.monotonic() < deadline):
                rounds += 1
                result = await client.call_tool(TOOL, {"directory": str(root), "dry_run": True})
                seen |= {f["size_bytes"] for f in files(result.structured_content or {})}
    finally:
        done.set()
        thread.join()

    assert seen <= {None, 4}
    assert 4 in seen
