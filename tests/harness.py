"""Building blocks for tests of the assembled server: settings, injected readers, fake endpoints.

Tool tests reach the server only through the composition root's seam (`create_server(...,
ports=...)`) and the in-memory FastMCP client; the readers below are fakes, not mocks.
"""

import asyncio
import contextlib
import http.server
import json
import socket
import threading
import time
from typing import TYPE_CHECKING, ClassVar, override

import observation
from fakes.accounts import ADMIN
from fakes.paperless import FakePaperless, seeded
from observation import recorded

from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.server import CATALOGUE, Catalogue, Ports, create_server
from mcp_server_paperless.services.document_edits import DocumentEditor
from mcp_server_paperless.services.documents import DocumentService
from mcp_server_paperless.services.pdf import PdfService
from mcp_server_paperless.services.pdf_parts import Parts
from mcp_server_paperless.services.pdf_replacing import Replacing
from mcp_server_paperless.services.removal import DocumentRemoval
from mcp_server_paperless.services.tasks import TaskService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.settings import PaperlessSettings, ServerSettings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator, Sequence

    from fastmcp import FastMCP
    from fastmcp.server.auth import AuthProvider

    from mcp_server_paperless.domain.errors import PaperlessMcpError
    from mcp_server_paperless.domain.instance import InstanceOverview
    from mcp_server_paperless.domain.ports import (
        AdminReader,
        AdminWriter,
        AiReader,
        BulkWriter,
        DocumentReader,
        DocumentWriter,
        InstanceReader,
        MailReader,
        MailWriter,
        PdfWriter,
        PermissionReader,
        PermissionWriter,
        RawReader,
        SharingReader,
        SharingWriter,
        TaskReader,
        TaxonomyReader,
        TaxonomyWriter,
        TrashReader,
        TrashWriter,
        ViewReader,
        ViewWriter,
        WorkflowReader,
        WorkflowWriter,
    )
    from mcp_server_paperless.domain.rendering import PageRenderer
    from mcp_server_paperless.tools.confirmation import Dialogs

URL = "http://paperless.test"
TOKEN = "harness-token-0123456789"


def paperless_settings(url: str = URL, token: str = TOKEN) -> PaperlessSettings:
    return PaperlessSettings.model_validate({"url": url, "token": token})


def server_settings(  # noqa: PLR0913 -- one keyword per setting a test varies
    startup_timeout: float = 2.0,
    *,
    toolsets: Sequence[str] | None = None,
    read_only: bool = False,
    disabled_tools: Sequence[str] = (),
    max_download_bytes: int | None = None,
    max_upload_bytes: int | None = None,
    upload_dirs: Sequence[str] = (),
    download_dirs: Sequence[str] = (),
    require_dialog: bool = False,
) -> ServerSettings:
    """Server settings; `toolsets=None` leaves the setting at its shipped default."""
    given: dict[str, object] = {
        "startup_timeout": startup_timeout,
        "read_only": read_only,
        "disabled_tools": ",".join(disabled_tools),
    }
    if toolsets is not None:
        given["toolsets"] = ",".join(toolsets)
    if max_download_bytes is not None:
        given["max_download_bytes"] = max_download_bytes
    if max_upload_bytes is not None:
        given["max_upload_bytes"] = max_upload_bytes
    if upload_dirs:
        given["upload_dirs"] = ",".join(upload_dirs)
    if download_dirs:
        given["download_dirs"] = ",".join(download_dirs)
    if require_dialog:
        given["require_dialog"] = True
    return ServerSettings.model_validate(given)


def pdf_service(
    fake: FakePaperless,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> PdfService:
    """`PdfService` on the fake, with everything it is built from."""
    web = "https://paperless.example"
    taxonomy = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=3600.0)
    documents = DocumentService(fake, fake, taxonomy, web_url=web)
    editor = DocumentEditor(fake, fake, fake, taxonomy, documents=documents)
    replacing = Replacing(fake, fake, DocumentRemoval(fake, fake, taxonomy), clock=clock)
    return PdfService(
        fake,
        fake,
        TaskService(fake, sleep=sleep, clock=clock),
        replacing,
        taxonomy,
        parts=Parts(fake, fake, editor, taxonomy, replacing, web_url=web, sleep=sleep),
        web_url=web,
    )


def serve(  # noqa: PLR0913 -- one keyword per seam of the server
    reader: InstanceReader,
    *,
    taxonomy: TaxonomyReader | None = None,
    documents: DocumentReader | None = None,
    tasks: TaskReader | None = None,
    document_writes: DocumentWriter | None = None,
    taxonomy_writes: TaxonomyWriter | None = None,
    bulk_writes: BulkWriter | None = None,
    ai: AiReader | None = None,
    views: ViewReader | None = None,
    view_writes: ViewWriter | None = None,
    trash: TrashReader | None = None,
    trash_writes: TrashWriter | None = None,
    pdf_writes: PdfWriter | None = None,
    sharing: SharingReader | None = None,
    sharing_writes: SharingWriter | None = None,
    permissions: PermissionReader | None = None,
    permission_writes: PermissionWriter | None = None,
    mail: MailReader | None = None,
    mail_writes: MailWriter | None = None,
    raw: RawReader | None = None,
    workflows: WorkflowReader | None = None,
    workflow_writes: WorkflowWriter | None = None,
    management: AdminReader | None = None,
    management_writes: AdminWriter | None = None,
    renderer: PageRenderer | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    startup_timeout: float = 2.0,
    catalogue: Catalogue = CATALOGUE,
    toolsets: Sequence[str] | None = None,
    read_only: bool = False,
    disabled_tools: Sequence[str] = (),
    max_download_bytes: int | None = None,
    max_upload_bytes: int | None = None,
    upload_dirs: Sequence[str] = (),
    download_dirs: Sequence[str] = (),
    require_dialog: bool = False,
    stdio: bool = False,
    auth: AuthProvider | None = None,
    dialogs: Dialogs | None = None,
) -> FastMCP:
    """The assembled server, with `reader` in place of the Paperless adapter.

    `taxonomy`, `documents`, `tasks`, `ai`, `views`, `trash`, `sharing`, `mail`, `workflows` and the
    writers default to `reader` if that is a fake, else to the seeded admin's fake (which refuses
    writes unless created to allow them).
    `renderer` replaces the child process that draws pages.
    `sleep` and `clock` replace the real ones the task wait uses. `toolsets`, `read_only` and
    `disabled_tools` go to the server settings; `catalogue` replaces the tools the server
    offers, to try toolsets that do not exist yet. `max_download_bytes` replaces the download
    limit. `max_upload_bytes` and `upload_dirs` are the upload settings; `stdio` says the server
    runs over stdio (the only mode that offers uploads by path, and then only with `upload_dirs`,
    and saving downloads, and then only with `download_dirs` and without read-only mode).
    `require_dialog` is the setting of that name. `dialogs` replaces the confirmation dialogs'
    times and clock.
    """
    fake = reader if isinstance(reader, FakePaperless) else admin()
    server = create_server(
        paperless_settings(),
        server_settings(
            startup_timeout,
            toolsets=toolsets,
            read_only=read_only,
            disabled_tools=disabled_tools,
            max_download_bytes=max_download_bytes,
            max_upload_bytes=max_upload_bytes,
            upload_dirs=upload_dirs,
            download_dirs=download_dirs,
            require_dialog=require_dialog,
        ),
        catalogue=catalogue,
        stdio=stdio,
        auth=auth,
        dialogs=dialogs,
        ports=lambda _client: recorded(
            Ports(
                instance=reader,
                taxonomy=taxonomy or fake,
                documents=documents or fake,
                tasks=tasks or fake,
                document_writes=document_writes or fake,
                taxonomy_writes=taxonomy_writes or fake,
                bulk_writes=bulk_writes or fake,
                ai=ai or fake,
                views=views or fake,
                view_writes=view_writes or fake,
                trash=trash or fake,
                trash_writes=trash_writes or fake,
                pdf_writes=pdf_writes or fake,
                sharing=sharing or fake,
                sharing_writes=sharing_writes or fake,
                permissions=permissions or fake,
                permission_writes=permission_writes or fake,
                mail=mail or fake,
                mail_writes=mail_writes or fake,
                raw=raw or fake,
                workflows=workflows or fake,
                workflow_writes=workflow_writes or fake,
                management=management or fake,
                management_writes=management_writes or fake,
            )
        ),
        sleep=sleep,
        clock=clock,
        renderer=renderer,
    )
    if observation.RECORDING:  # the coverage test records which tool makes which port call
        server.add_middleware(observation.ToolMarker())
    return server


def admin(*, ai_enabled: bool = False) -> FakePaperless:
    """The seeded admin's fake; `ai_enabled` is what Paperless reports for AI."""
    state = seeded()
    state.ai_enabled = ai_enabled
    return FakePaperless(state, user=ADMIN[0])


class FailsAfterProbe:
    """Answers the startup probe like the seeded fake, then fails every call with `error`."""

    def __init__(self, error: Exception) -> None:
        self.error: Exception = error
        self._probed: bool = False

    async def get_overview(self) -> InstanceOverview:
        if not self._probed:
            self._probed = True
            return await admin().get_overview()
        raise self.error


class Recorded:
    """Paperless as it answered once: the same overview on every call, nothing else."""

    def __init__(self, overview: InstanceOverview) -> None:
        self.overview: InstanceOverview = overview

    async def get_overview(self) -> InstanceOverview:
        return self.overview


class FailingReader:
    """Every call fails with `error`, the startup probe included."""

    def __init__(self, error: PaperlessMcpError) -> None:
        self.error: PaperlessMcpError = error

    async def get_overview(self) -> InstanceOverview:
        raise self.error


class HangingReader:
    """Paperless never answers."""

    async def get_overview(self) -> InstanceOverview:
        _ = await asyncio.Event().wait()
        raise AssertionError


def refused_url() -> str:
    """A local URL on which nothing listens, so connecting is refused at once."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])  # pyright: ignore[reportAny] -- (host, port) tuple
    return f"http://127.0.0.1:{port}"


class _Answer(http.server.BaseHTTPRequestHandler):
    status: ClassVar[int] = 200

    def do_GET(self) -> None:
        body = json.dumps({"detail": "stub"}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _ = self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Keep the stub quiet."""


@contextlib.contextmanager
def paperless_answering(status: int) -> Generator[str]:
    """A local HTTP endpoint that answers every GET with `status`; yields its URL."""
    handler = type("Answer", (_Answer,), {"status": status})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
