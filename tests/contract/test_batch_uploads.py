"""Several files in one call on the fake and on Paperless 3.5.1 (decision-50).

It needs no port method of its own; what it relies on is that the checksum filter, the trash and the
tasks of both answer alike while one call uses them for many files.
"""

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from contract.test_document_upload import CONTRACT
from contract.test_document_versions import add_version
from contract.test_duplicates import unique, uploaded
from mcp_server_paperless.domain.documents import UploadMetadata
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.batch_uploads import BatchUploadService, Limits
from mcp_server_paperless.services.duplicates import DuplicateFinder
from mcp_server_paperless.services.tasks import TaskService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.uploads import UploadService

if TYPE_CHECKING:
    from contract.conftest import Backend, Ports

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-50")]

WEB = "https://paperless.example"


def service(ports: Ports, root: Path, limits: Limits | None = None) -> BatchUploadService:
    finder = DuplicateFinder(ports.documents, ports.trash, web_url=WEB)
    tasks = TaskService(ports.tasks, duplicates=finder)
    uploads = UploadService(
        ports.documents,
        ports.document_writes,
        TaxonomyResolver(ports.taxonomy, StaticTokenProvider("t"), ttl=60.0),
        tasks,
        finder,
        web_url=WEB,
        max_bytes=10_000,
    )
    return BatchUploadService(
        uploads,
        ports.document_writes,
        tasks,
        finder,
        web_url=WEB,
        max_bytes=10_000,
        upload_dirs=[str(root)],
        limits=limits,
    )


@pytest.mark.contract(CONTRACT)
async def test_mixed_outcomes_of_one_call(backend: Backend, tmp_path: Path) -> None:
    root = tmp_path / "scans"
    root.mkdir()
    known, version, trashed = unique(), unique(), unique()
    document = await uploaded(backend, known)
    version_id = await add_version(backend, document, version.decode())
    gone = await uploaded(backend, trashed)
    await backend.admin.document_writes.delete_document(gone)
    fresh = unique()
    contents = {
        "a-new.txt": fresh,
        "b-known.txt": known,
        "c-version.txt": version,
        "d-trash.txt": trashed,
        "e-twin.txt": fresh,
        "f-empty.txt": b"",
        "g-linked.txt": unique(),
        "i-new.txt": unique(),
        ".hidden.txt": unique(),
    }
    for name, content in contents.items():
        _ = (root / name).write_bytes(content)
    os.link(root / "g-linked.txt", tmp_path / "other-name.txt")
    _ = (tmp_path / "secret.txt").write_bytes(unique())
    (root / "h-out.txt").symlink_to(tmp_path / "secret.txt")

    batch = await service(backend.admin, root).from_directory(
        str(root), UploadMetadata(), wait_seconds=30
    )

    assert [f.status for f in batch.files] == [
        *("uploaded", "duplicate", "duplicate", "duplicate", "duplicate"),
        *("refused", "refused", "refused", "uploaded"),
    ]
    matches = [
        [(m.document_id, m.version_id, m.in_trash) for m in f.duplicates.before]
        for f in batch.files[1:4]
        if f.duplicates is not None
    ]
    assert matches == [
        [(document, None, False)],
        [(document, version_id, False)],
        [(gone, None, True)],
    ]
    sent = [f for f in batch.files if f.status == "uploaded"]
    assert [f.state for f in sent] == ["success", "success"]
    for file, content in zip(sent, (contents["a-new.txt"], contents["i-new.txt"]), strict=True):
        assert file.document_id is not None
        stored = await backend.admin.documents.get_document(file.document_id)
        assert stored.original_file_name == Path(file.path).name
        assert len(content) == file.size_bytes
    assert any("dot: 1" in note for note in batch.notes)


@pytest.mark.contract(CONTRACT)
async def test_two_calls_cover_a_directory_and_a_third_finds_all_of_it(
    backend: Backend, tmp_path: Path
) -> None:
    for name in ("a", "b", "c"):
        _ = (tmp_path / f"{name}.txt").write_bytes(unique())
    batches = service(backend.admin, tmp_path, Limits(files=2))

    first = await batches.from_directory(str(tmp_path), UploadMetadata(), wait_seconds=30)
    second = await batches.from_directory(
        str(tmp_path), UploadMetadata(), after=first.next_after, wait_seconds=30
    )
    again = await batches.from_directory(str(tmp_path), UploadMetadata(), wait_seconds=0)

    done = [*first.files, *second.files]
    assert [f.status for f in done] == ["uploaded"] * 3
    assert len({f.document_id for f in done}) == 3
    assert [f.status for f in again.files] == ["duplicate", "duplicate"]


@pytest.mark.contract(CONTRACT)
async def test_a_user_who_may_not_add_documents_gets_a_report_for_every_file(
    backend: Backend, tmp_path: Path
) -> None:
    for name in ("a", "b"):
        _ = (tmp_path / f"{name}.txt").write_bytes(uuid.uuid4().hex.encode())

    batch = await service(backend.reader, tmp_path).from_directory(str(tmp_path), UploadMetadata())

    assert [f.status for f in batch.files] == ["failed", "failed"]
    assert {f.reason for f in batch.files} == {"The Paperless user may not perform this action."}
