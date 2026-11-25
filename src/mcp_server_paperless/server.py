"""Composition root: wires settings, adapter, services and tool providers into the server.

The only module that imports both ``tools`` and ``paperless``. ``create_server`` assembles; the
server's lifespan connects: it owns the one ``httpx2.AsyncClient`` of the process, builds the
services on it and probes Paperless before the first request is served.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from fastmcp.server.transforms.visibility import Visibility
from mcp.server.request_state import RequestStateSecurity

from mcp_server_paperless import __version__
from mcp_server_paperless.domain.errors import InvalidConfiguration
from mcp_server_paperless.observability import CallLogging
from mcp_server_paperless.paperless.admin import PaperlessAdminReader, PaperlessAdminWriter
from mcp_server_paperless.paperless.ai import PaperlessAiReader
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.bulk import PaperlessBulkWriter
from mcp_server_paperless.paperless.client import RetryPolicy, connect
from mcp_server_paperless.paperless.documents import (
    PaperlessDocumentReader,
    PaperlessDocumentWriter,
)
from mcp_server_paperless.paperless.instance import PaperlessInstanceReader
from mcp_server_paperless.paperless.mail import PaperlessMailReader, PaperlessMailWriter
from mcp_server_paperless.paperless.pdf import PaperlessPdfWriter
from mcp_server_paperless.paperless.permissions import (
    PaperlessPermissionReader,
    PaperlessPermissionWriter,
)
from mcp_server_paperless.paperless.raw import PaperlessRawReader
from mcp_server_paperless.paperless.sharing import PaperlessSharingReader, PaperlessSharingWriter
from mcp_server_paperless.paperless.task_lists import (
    PaperlessTaskListReader,
    PaperlessTaskListWriter,
)
from mcp_server_paperless.paperless.tasks import PaperlessTaskReader
from mcp_server_paperless.paperless.taxonomy import PaperlessTaxonomyReader, PaperlessTaxonomyWriter
from mcp_server_paperless.paperless.trash import PaperlessTrashReader, PaperlessTrashWriter
from mcp_server_paperless.paperless.views import PaperlessViewReader, PaperlessViewWriter
from mcp_server_paperless.paperless.workflows import (
    PaperlessWorkflowReader,
    PaperlessWorkflowWriter,
)
from mcp_server_paperless.rendering.limits import protect_server
from mcp_server_paperless.rendering.runner import ChildRenderer
from mcp_server_paperless.services.admin_accounts import AccountAdmin
from mcp_server_paperless.services.admin_system import SystemAdmin
from mcp_server_paperless.services.ai import AiService
from mcp_server_paperless.services.batch_uploads import BatchUploadService
from mcp_server_paperless.services.bulk_documents import BulkDocumentEditor
from mcp_server_paperless.services.bulk_notes import BulkNotes
from mcp_server_paperless.services.bulk_taxonomy import BulkTaxonomyEditor
from mcp_server_paperless.services.bulk_updates import BulkUpdates
from mcp_server_paperless.services.document_edits import DocumentEditor
from mcp_server_paperless.services.documents import DocumentService
from mcp_server_paperless.services.duplicate_resolution import DuplicateResolver
from mcp_server_paperless.services.duplicates import DuplicateFinder
from mcp_server_paperless.services.file_sink import NO_SINK, FileSink
from mcp_server_paperless.services.files import FileService
from mcp_server_paperless.services.instance import InstanceService
from mcp_server_paperless.services.mail import MailService
from mcp_server_paperless.services.notes import NoteService
from mcp_server_paperless.services.pages import PageService
from mcp_server_paperless.services.pdf import PdfService
from mcp_server_paperless.services.pdf_insert import PageInserter
from mcp_server_paperless.services.pdf_parts import Parts
from mcp_server_paperless.services.pdf_replacing import Replacing
from mcp_server_paperless.services.permissions import PermissionService
from mcp_server_paperless.services.processed_mail import ProcessedMailService
from mcp_server_paperless.services.raw import RawApiService
from mcp_server_paperless.services.read_documents import ReadDocuments
from mcp_server_paperless.services.removal import DocumentRemoval
from mcp_server_paperless.services.selection import DocumentSelector
from mcp_server_paperless.services.sharing import SharingService
from mcp_server_paperless.services.task_lists import TaskListService
from mcp_server_paperless.services.tasks import TaskService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.taxonomy_edits import TaxonomyEditor
from mcp_server_paperless.services.text_edits import TextEditor
from mcp_server_paperless.services.trash import TrashService
from mcp_server_paperless.services.trash_scan import TrashScan
from mcp_server_paperless.services.trashed_documents import TrashedDocuments
from mcp_server_paperless.services.uploads import UploadService
from mcp_server_paperless.services.version_uploads import VersionUploadService
from mcp_server_paperless.services.versions import VersionService
from mcp_server_paperless.services.views import ViewService
from mcp_server_paperless.services.workflow_parts import WorkflowParts
from mcp_server_paperless.services.workflow_runs import WorkflowRuns
from mcp_server_paperless.services.workflows import WorkflowService
from mcp_server_paperless.tools import (
    admin,
    ai,
    bulk,
    bulk_stdio,
    core,
    downloads,
    downloads_stdio,
    mail,
    pdf,
    permissions,
    prompts,
    raw,
    sharing,
    trash,
    upload,
    upload_stdio,
    versions,
    versions_stdio,
    views,
    workflows,
)
from mcp_server_paperless.tools import tasks as tasks_toolset
from mcp_server_paperless.tools._shared import (
    ANNOTATIONS,
    DOCUMENT_TEXT,
    GUIDE,
    LOCAL_ANNOTATIONS,
    LOCAL_TOOLSETS,
    SERVICES,
    TOOL_TOOLSETS,
    TOOLSET_REQUIRES,
    TOOLSET_SUMMARIES,
    TOOLSETS,
    GuideFor,
    Services,
)
from mcp_server_paperless.tools.confirmation import DIALOGS, Dialogs
from mcp_server_paperless.tools.selection import (
    guide_text,
    hidden_tools,
    selected_toolsets,
    toolset_guide,
    unavailable_toolsets,
    unchecked_toolsets,
    visible_tools,
)
from mcp_server_paperless.tools.upload_hint import PATH_OFF, UPLOAD_TOOLS, PathRefusal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

    from fastmcp.server.auth import AuthProvider
    from fastmcp.server.providers import Provider
    from mcp.types import ToolAnnotations

    from mcp_server_paperless.domain.instance import InstanceOverview, ToolsetGuide
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
        TaskListReader,
        TaskListWriter,
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
    from mcp_server_paperless.paperless.client import PaperlessClient
    from mcp_server_paperless.settings import PaperlessSettings, ServerSettings

# Seconds per Paperless request; uploads get longer (they carry the file).
REQUEST_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 120.0
# Seconds a taxonomy list is reused before it is read again; this server's writes invalidate it.
TAXONOMY_TTL = 300.0

OVERVIEW = "get_instance_overview"
INTRO = (
    "Tools for a paperless-ngx document archive: find, read and organise documents and their "
    "tags, correspondents, document types and storage paths."
)
START = f"Start with {OVERVIEW} to learn the instance and what the current user may do."

# What the entry points do first, with the one process of the server.
protect_process: Final = protect_server

NO_TOOLS = (
    "No tools are left to offer: PAPERLESS_MCP_TOOLSETS, PAPERLESS_MCP_READ_ONLY,"
    " PAPERLESS_MCP_DISABLED_TOOLS and the capabilities of this Paperless leave no tool."
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Catalogue:
    """Every tool the server can offer and what selection needs to know about them.

    Attributes:
        providers: One FastMCP provider per toolset module.
        remote_providers: Providers added over HTTP: the upload tools in their form without a path.
        local_providers: Providers added instead of ``remote_providers`` when the server runs
            over stdio with an upload allowlist: the same tools in their form with a path.
        stdio_providers: Providers added instead when it runs over stdio without an allowlist:
            the form for HTTP, with the hint how a path is enabled.
        plain_downloads: Providers of the download tools when the server saves nothing.
        saving_downloads: Providers added instead of ``plain_downloads`` when the server runs over
            stdio with download directories and without read-only mode (decision-45).
        annotations: The central annotation table, keyed by tool name.
        tool_toolsets: The toolset of every tool, keyed by tool name.
        toolsets: Every toolset name.
        requires: Toolsets that need a Paperless capability, mapped to it.
        local_annotations: The annotations of the tools only ``local_providers`` have.
        local_tool_toolsets: The toolsets of these tools. In every other mode nothing names them,
            but ``PAPERLESS_MCP_DISABLED_TOOLS`` knows them.
        prompt_specs: What each prompt needs, keyed by prompt name; a prompt is offered only
            while every tool it names can be called (decision-20). A prompt without an entry
            is never offered.
        summaries: What each toolset is for, in a few words, for the guide to the toolsets
            that are off.
    """

    providers: Sequence[Provider]
    annotations: Mapping[str, ToolAnnotations]
    tool_toolsets: Mapping[str, str]
    toolsets: frozenset[str]
    requires: Mapping[str, str]
    remote_providers: Sequence[Provider] = ()
    local_providers: Sequence[Provider] = ()
    stdio_providers: Sequence[Provider] = ()
    plain_downloads: Sequence[Provider] = ()
    saving_downloads: Sequence[Provider] = ()
    local_annotations: Mapping[str, ToolAnnotations] = field(
        default_factory=dict[str, "ToolAnnotations"]
    )
    local_tool_toolsets: Mapping[str, str] = field(default_factory=dict[str, str])
    prompt_specs: Mapping[str, prompts.PromptSpec] = field(
        default_factory=dict[str, prompts.PromptSpec]
    )
    summaries: Mapping[str, str] = field(default_factory=dict[str, str])


CATALOGUE: Final = Catalogue(
    providers=(
        core.provider,
        ai.provider,
        bulk.provider,
        versions.provider,
        views.provider,
        trash.provider,
        pdf.provider,
        sharing.provider,
        permissions.provider,
        mail.provider,
        admin.provider,
        tasks_toolset.provider,
        raw.provider,
        prompts.provider,
        workflows.provider,
    ),
    annotations=ANNOTATIONS,
    tool_toolsets=TOOL_TOOLSETS,
    toolsets=TOOLSETS,
    requires=TOOLSET_REQUIRES,
    remote_providers=(upload.remote, versions.remote),
    local_providers=(upload_stdio.local, versions_stdio.local, bulk_stdio.local),
    local_annotations=LOCAL_ANNOTATIONS,
    local_tool_toolsets=LOCAL_TOOLSETS,
    stdio_providers=(upload.hinted, versions.hinted),
    plain_downloads=(downloads.remote,),
    saving_downloads=(downloads_stdio.local,),
    prompt_specs=prompts.SPECS,
    summaries=TOOLSET_SUMMARIES,
)


@dataclass(frozen=True, slots=True)
class Ports:
    """One implementation of every domain port, one field per port."""

    instance: InstanceReader
    taxonomy: TaxonomyReader
    documents: DocumentReader
    tasks: TaskReader
    document_writes: DocumentWriter
    taxonomy_writes: TaxonomyWriter
    bulk_writes: BulkWriter
    ai: AiReader
    views: ViewReader
    view_writes: ViewWriter
    trash: TrashReader
    trash_writes: TrashWriter
    pdf_writes: PdfWriter
    sharing: SharingReader
    sharing_writes: SharingWriter
    permissions: PermissionReader
    permission_writes: PermissionWriter
    mail: MailReader
    mail_writes: MailWriter
    raw: RawReader
    workflows: WorkflowReader
    workflow_writes: WorkflowWriter
    management: AdminReader
    management_writes: AdminWriter
    task_lists: TaskListReader
    task_list_writes: TaskListWriter


def paperless_ports(client: PaperlessClient) -> Ports:
    """The Paperless adapter for every port, all on ``client``."""
    return Ports(
        instance=PaperlessInstanceReader(client),
        taxonomy=PaperlessTaxonomyReader(client),
        documents=PaperlessDocumentReader(client),
        tasks=PaperlessTaskReader(client),
        document_writes=PaperlessDocumentWriter(client),
        taxonomy_writes=PaperlessTaxonomyWriter(client),
        bulk_writes=PaperlessBulkWriter(client),
        ai=PaperlessAiReader(client),
        views=PaperlessViewReader(client),
        view_writes=PaperlessViewWriter(client),
        trash=PaperlessTrashReader(client),
        trash_writes=PaperlessTrashWriter(client),
        pdf_writes=PaperlessPdfWriter(client),
        sharing=PaperlessSharingReader(client),
        sharing_writes=PaperlessSharingWriter(client),
        permissions=PaperlessPermissionReader(client),
        permission_writes=PaperlessPermissionWriter(client),
        mail=PaperlessMailReader(client),
        mail_writes=PaperlessMailWriter(client),
        raw=PaperlessRawReader(client),
        workflows=PaperlessWorkflowReader(client),
        workflow_writes=PaperlessWorkflowWriter(client),
        management=PaperlessAdminReader(client),
        management_writes=PaperlessAdminWriter(client),
        task_lists=PaperlessTaskListReader(client),
        task_list_writes=PaperlessTaskListWriter(client),
    )


def describe_toolsets(
    catalogue: Catalogue,
    enabled: frozenset[str],
    hidden: frozenset[str],
    *,
    started: bool | None,
    now: bool | None,
) -> tuple[str, ToolsetGuide]:
    """The server instructions and the guide to the toolsets: the one place both are composed.

    ``started`` is whether Paperless AI was on when the server started (None: not known), which
    decides what is on; ``now`` is what Paperless says now, which decides what is off, so that
    nothing is promised that a restart would not deliver. Nothing is described that the
    selection, read-only mode, disabled tools or a missing capability exclude; a capability that
    could not be checked is named as a gap.
    """
    requires = catalogue.requires
    unavailable = unavailable_toolsets(ai_enabled=started, requires=requires)
    guide = toolset_guide(
        catalogue.tool_toolsets,
        catalogue.summaries,
        requires,
        selected=enabled,
        hidden=hidden,
        unavailable=unavailable,
        unchecked=unchecked_toolsets(ai_enabled=now, requires=requires),
        missing_now=unavailable_toolsets(ai_enabled=now, requires=requires),
    )
    overview = OVERVIEW in visible_tools(catalogue.tool_toolsets, enabled - unavailable, hidden)
    parts = (INTRO, START if overview else "", DOCUMENT_TEXT, guide_text(guide, overview=overview))
    return " ".join(part for part in parts if part), guide


def create_server(  # noqa: PLR0913 -- one keyword per seam, all defaulted
    paperless: PaperlessSettings,
    settings: ServerSettings,
    *,
    catalogue: Catalogue = CATALOGUE,
    ports: Callable[[PaperlessClient], Ports] = paperless_ports,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    stdio: bool = False,
    auth: AuthProvider | None = None,
    dialogs: Dialogs | None = None,
    renderer: PageRenderer | None = None,
) -> FastMCP:
    """Assemble the MCP server.

    Args:
        paperless: Where Paperless is and the token to use.
        settings: Settings of this server, including which toolsets and tools to offer.
        catalogue: The tools the server can offer; tests pass a larger one.
        ports: Builds the ports on the connected client; tests pass fakes here.
        sleep: Waits between task polls; tests pass one that does not wait.
        clock: Measures the task wait; tests pass the clock their ``sleep`` advances.
        stdio: Whether the server runs over stdio. Only then, and only with
            ``settings.upload_dirs``, does ``upload_document`` take a path; in every other
            case that parameter is in no schema (decision-7). The same goes for saving downloads
            with ``settings.download_dirs`` (decision-45).
        auth: Authentication of the HTTP endpoint (the HTTP app's static bearer tokens); ``None``
            for stdio and for the flagged unauthenticated loopback mode.
        dialogs: The times and the clock of the confirmation dialogs; tests pass short ones.
        renderer: Draws pages; default: a child process per call. Tests pass a fake.

    Returns:
        The server. Starting it (its lifespan) raises the probe's ``PaperlessMcpError`` when
        Paperless rejects the token or API version 10, and ``InvalidConfiguration`` when the
        selection leaves no tool.

    Raises:
        InvalidConfiguration: A toolset or a disabled tool in the settings does not exist, or
            the settings alone leave no tool.
    """
    # Outlives the lifespan: a state stays used up when the server's last connection closes.
    dialogs = replace(dialogs or Dialogs(), required=settings.require_dialog)
    credentials = StaticTokenProvider(paperless.token.get_secret_value())
    web_url = str(paperless.public_url or paperless.url)
    by_path = stdio and bool(settings.upload_dirs)
    in_process = catalogue.local_providers if by_path else catalogue.stdio_providers
    uploads = in_process if stdio else catalogue.remote_providers
    known_tools = {**catalogue.annotations, **catalogue.local_annotations}
    if by_path:
        toolsets = {**catalogue.tool_toolsets, **catalogue.local_tool_toolsets}
        catalogue = replace(catalogue, tool_toolsets=toolsets)
    path_hint = PATH_OFF if stdio and not by_path else ""
    saving = stdio and bool(settings.download_dirs) and not settings.read_only
    variants = [
        *uploads,
        *(catalogue.saving_downloads if saving else catalogue.plain_downloads),
    ]
    sink = FileSink(settings.download_dirs) if saving else NO_SINK

    # Allowlist the selected toolsets (resources carry the tags too), then take out tools:
    # later visibility rules override earlier ones.
    enabled = selected_toolsets(settings.toolsets, catalogue.toolsets)
    hidden = hidden_tools(
        known_tools, read_only=settings.read_only, disabled=settings.disabled_tools
    )
    selected = visible_tools(catalogue.tool_toolsets, enabled, hidden)
    if not selected:
        raise InvalidConfiguration(NO_TOOLS)
    # The one rule that hides toolsets whose capability is missing; each start replaces its tags.
    capability_rule = Visibility(enabled=False, tags=set())
    # The one rule that hides prompts naming a tool that cannot be called (decision-20).
    prompt_rule = Visibility(enabled=False, names=set(), components={"prompt"})

    @lifespan
    async def start(running: FastMCP) -> AsyncIterator[dict[str, object]]:
        async with connect(
            str(paperless.url),
            credentials,
            timeout=REQUEST_TIMEOUT,
            upload_timeout=UPLOAD_TIMEOUT,
            retry=RetryPolicy(),
        ) as client:
            adapters = ports(client)
            taxonomy = TaxonomyResolver(adapters.taxonomy, credentials, ttl=TAXONOMY_TTL)
            documents = DocumentService(
                adapters.documents, adapters.trash, taxonomy, web_url=web_url
            )
            views = ViewService(adapters.views, adapters.view_writes, taxonomy, documents)
            trash_scan = TrashScan(adapters.trash, adapters.documents, taxonomy)
            duplicates = DuplicateFinder(adapters.documents, adapters.trash, web_url=web_url)
            tasks = TaskService(adapters.tasks, sleep=sleep, clock=clock, duplicates=duplicates)
            uploading = UploadService(
                adapters.documents,
                adapters.document_writes,
                taxonomy,
                tasks,
                duplicates,
                web_url=web_url,
                max_bytes=settings.max_upload_bytes,
                upload_dirs=settings.upload_dirs if by_path else (),
                path_hint=path_hint,
            )
            removal, drawing, turn = (
                DocumentRemoval(adapters.documents, adapters.document_writes, taxonomy),
                renderer or ChildRenderer(),
                asyncio.Semaphore(1),
            )
            edits = DocumentEditor(
                adapters.documents,
                adapters.document_writes,
                adapters.taxonomy_writes,
                taxonomy,
                documents=documents,
            )
            replacing = Replacing(
                adapters.documents, adapters.document_writes, removal, clock=clock
            )
            services = Services(
                instance=InstanceService(adapters.instance),
                taxonomy=taxonomy,
                taxonomy_edits=TaxonomyEditor(
                    adapters.taxonomy, adapters.taxonomy_writes, taxonomy, adapters.documents
                ),
                documents=documents,
                files=FileService(
                    adapters.documents,
                    web_url=web_url,
                    max_bytes=settings.max_download_bytes,
                    sink=sink,
                ),
                pages=PageService(adapters.documents, drawing, web_url=web_url, turn=turn),
                edits=edits,
                notes=NoteService(adapters.documents, adapters.document_writes, web_url=web_url),
                removal=removal,
                tasks=tasks,
                ai=AiService(adapters.ai, taxonomy, web_url=web_url),
                views=views,
                read_documents=ReadDocuments(
                    adapters.documents,
                    DocumentSelector(
                        adapters.documents,
                        documents,
                        views,
                        taxonomy,
                    ),
                    taxonomy,
                    web_url=web_url,
                    clock=clock,
                ),
                pdf=PdfService(
                    adapters.documents,
                    adapters.pdf_writes,
                    tasks,
                    replacing,
                    taxonomy,
                    parts=Parts(
                        adapters.documents,
                        adapters.pdf_writes,
                        edits,
                        taxonomy,
                        replacing,
                        web_url=web_url,
                        sleep=sleep,
                    ),
                    web_url=web_url,
                ),
                insertion=PageInserter(
                    adapters.documents,
                    adapters.document_writes,
                    drawing,
                    tasks,
                    turn,
                    web_url=web_url,
                ),
                sharing=SharingService(
                    adapters.sharing, adapters.sharing_writes, adapters.documents, web_url=web_url
                ),
                mail=MailService(adapters.mail, adapters.mail_writes, taxonomy, adapters.instance),
                processed_mail=ProcessedMailService(adapters.mail),
                raw=RawApiService(adapters.raw),
                workflows=(
                    flows := WorkflowService(adapters.workflows, adapters.workflow_writes, taxonomy)
                ),
                workflow_parts=WorkflowParts(
                    adapters.workflows,
                    adapters.workflow_writes,
                    flows,
                ),
                workflow_runs=WorkflowRuns(
                    adapters.documents,
                    adapters.workflow_writes,
                    flows,
                    DocumentSelector(adapters.documents, documents, views, taxonomy),
                    taxonomy,
                ),
                accounts=AccountAdmin(adapters.management, adapters.management_writes, taxonomy),
                system=SystemAdmin(
                    adapters.management,
                    adapters.management_writes,
                    max_bytes=settings.max_download_bytes,
                    sink=sink,
                ),
                task_lists=TaskListService(
                    adapters.task_lists, adapters.task_list_writes, taxonomy
                ),
                uploads=uploading,
                upload_batches=BatchUploadService(
                    uploading,
                    adapters.document_writes,
                    tasks,
                    duplicates,
                    web_url=web_url,
                    max_bytes=settings.max_upload_bytes,
                    upload_dirs=settings.upload_dirs if by_path else (),
                    clock=clock,
                ),
                versions=VersionService(
                    adapters.documents, adapters.document_writes, taxonomy, web_url=web_url
                ),
                text_edits=TextEditor(
                    adapters.documents, adapters.document_writes, web_url=web_url
                ),
                version_uploads=VersionUploadService(
                    adapters.documents,
                    adapters.document_writes,
                    tasks,
                    duplicates,
                    web_url=web_url,
                    max_bytes=settings.max_upload_bytes,
                    upload_dirs=settings.upload_dirs if by_path else (),
                    path_hint=path_hint,
                ),
                bulk_documents=BulkDocumentEditor(
                    adapters.documents,
                    adapters.bulk_writes,
                    DocumentSelector(adapters.documents, documents, views, taxonomy),
                    taxonomy,
                    web_url=web_url,
                ),
                bulk_updates=BulkUpdates(
                    adapters.documents,
                    adapters.document_writes,
                    edits,
                    taxonomy,
                    web_url=web_url,
                    clock=clock,
                ),
                bulk_notes=BulkNotes(
                    adapters.documents,
                    adapters.document_writes,
                    DocumentSelector(adapters.documents, documents, views, taxonomy),
                    web_url=web_url,
                    clock=clock,
                ),
                bulk_taxonomy=BulkTaxonomyEditor(adapters.taxonomy, adapters.bulk_writes, taxonomy),
                duplicate_resolver=DuplicateResolver(
                    adapters.documents,
                    adapters.document_writes,
                    adapters.bulk_writes,
                    taxonomy,
                    web_url=web_url,
                ),
                trash=TrashService(
                    adapters.trash,
                    adapters.trash_writes,
                    adapters.documents,
                    trash_scan,
                    taxonomy,
                    web_url=web_url,
                ),
                trashed=TrashedDocuments(
                    adapters.trash,
                    adapters.documents,
                    adapters.permissions,
                    documents,
                    trash_scan,
                    web_url=web_url,
                    max_bytes=settings.max_download_bytes,
                ),
                permissions=PermissionService(
                    adapters.permissions,
                    adapters.permission_writes,
                    taxonomy,
                    adapters.instance,
                    web_url=web_url,
                ),
            )
            overview = await services.instance.probe(settings.startup_timeout)
            if overview is None:
                logger.warning("Paperless is not reachable yet; tools report it until it is")
            else:
                logger.info(
                    "Connected to Paperless %s (API version %d)",
                    overview.paperless_version,
                    overview.api_version,
                )
            guide_for = await _hide_unavailable(
                running,
                (capability_rule, prompt_rule),
                catalogue,
                overview,
                enabled=enabled,
                hidden=hidden,
                selected=selected,
            )
            yield {SERVICES: services, GUIDE: guide_for, DIALOGS: dialogs}

    # Until Paperless answers no capability is known; the start replaces these instructions.
    instructions, _ = describe_toolsets(catalogue, enabled, hidden, started=None, now=None)
    server = FastMCP(
        name="mcp-server-paperless",
        instructions=instructions,
        version=__version__,
        providers=[*catalogue.providers, *variants],
        middleware=[
            CallLogging(),
            *([PathRefusal(UPLOAD_TOOLS & selected)] if path_hint else []),
        ],
        lifespan=start,
        auth=auth,
        # Longer than a dialog: one that expired is told so, instead of a refused state.
        request_state_security=RequestStateSecurity.ephemeral(ttl=2 * dialogs.timeout),
        # Only ToolError messages reach the client; any other exception becomes a generic error.
        mask_error_details=True,
    )
    _ = server.enable(tags=set(enabled), only=True)
    if hidden:
        _ = server.disable(names=set(hidden), components={"tool"})
    server.add_transform(capability_rule)
    server.add_transform(prompt_rule)
    return server


async def _hide_unavailable(  # noqa: PLR0913 -- the rules, the catalogue and what the start learned
    server: FastMCP,
    rules: tuple[Visibility, Visibility],
    catalogue: Catalogue,
    overview: InstanceOverview | None,
    *,
    enabled: frozenset[str],
    hidden: frozenset[str],
    selected: frozenset[str],
) -> GuideFor:
    """Hide toolsets whose capability Paperless lacks and the prompts that need what is hidden.

    Tells the instructions what Paperless answered. Fails if no tool is left to offer.

    Returns:
        What builds the guide to the toolsets on and off from what Paperless says about AI at the
        time of a call (what is on stays as the start found it).
    """
    rule, prompt_rule = rules
    ai_enabled = overview.ai_enabled if overview else None
    unavailable = unavailable_toolsets(ai_enabled=ai_enabled, requires=catalogue.requires)
    rule.tags = set(unavailable)
    if unavailable:
        for toolset in sorted(unavailable & enabled):
            logger.warning("Toolset %s is hidden: Paperless does not offer what it needs", toolset)
    if not await server.list_tools():
        raise InvalidConfiguration(NO_TOOLS)
    server.instructions, _ = describe_toolsets(
        catalogue, enabled, hidden, started=ai_enabled, now=ai_enabled
    )
    # From the catalogue, not from a listing: what a caller may list can depend on who asks.
    callable_tools = {name for name in selected if catalogue.tool_toolsets[name] not in unavailable}
    # Reset first: the listing below skips what the previous start hid.
    prompt_rule.names = set()
    offered = {prompt.name for prompt in await server.list_prompts()}
    prompt_rule.names = {
        name
        for name in offered
        if name not in catalogue.prompt_specs
        or not catalogue.prompt_specs[name].tools <= callable_tools
    }

    def guide_for(*, now: bool | None) -> ToolsetGuide:
        return describe_toolsets(catalogue, enabled, hidden, started=ai_enabled, now=now)[1]

    return guide_for
