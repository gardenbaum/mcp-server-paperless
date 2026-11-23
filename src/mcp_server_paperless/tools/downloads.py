"""download_document and bulk_download for every caller: attached whole or in parts.

This is the variant HTTP clients get, and stdio clients too unless the operator allows saving
(``PAPERLESS_MCP_DOWNLOAD_DIRS``, see ``downloads_stdio``). It has no path parameter and nothing
that writes a file (decision-7, decision-45). The parameters and the wording are shared with the
saving variant.
"""

import base64
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from fastmcp.tools import ToolResult
from mcp.types import BlobResourceContents, ContentBlock, EmbeddedResource, TextContent
from pydantic import Field

from mcp_server_paperless.domain.admin import ArchiveContent, Compression
from mcp_server_paperless.domain.documents import FileVersion
from mcp_server_paperless.services.admin_system import MAX_DOCUMENTS
from mcp_server_paperless.services.transfer import check_part
from mcp_server_paperless.tools._shared import ANNOTATIONS, DOCUMENT_TEXT, boundary, services

if TYPE_CHECKING:
    from pydantic import BaseModel

    from mcp_server_paperless.services.admin_system import SystemAdmin
    from mcp_server_paperless.services.files import FileService

CORE, ADMIN = "core", "admin"
OCTETS = "application/octet-stream"

# The tools' names are the same in both variants; the server registers exactly one of them.
remote = LocalProvider()

type DocumentId = Annotated[int, Field(description="Document id, from search_documents.")]
type Kind = Annotated[
    FileVersion,
    Field(
        description=(
            "original: the file as it was uploaded. archive: the searchable PDF/A "
            "Paperless made from it; not every document has one (text files do not)."
        )
    ),
]
type VersionId = Annotated[
    int | None, Field(description="Version id; default: the newest version.")
]
type Offset = Annotated[
    int | None,
    Field(
        ge=0, description="Read one part from this byte on; 0 for the first. Default: whole file."
    ),
]
type MaxBytes = Annotated[
    int | None,
    Field(ge=1, description="Most bytes of the part; at most (and by default) the download limit."),
]
type Checksum = Annotated[
    str | None,
    Field(description="checksum of the first part's report; every later part needs it."),
]
type DocumentIds = Annotated[
    list[Annotated[int, Field(ge=1)]],
    Field(
        min_length=1,
        max_length=MAX_DOCUMENTS,
        description=f"Document ids from search_documents; at most {MAX_DOCUMENTS}, each once.",
    ),
]
type Content = Annotated[
    ArchiveContent,
    Field(
        description=(
            "archive: the searchable PDF/A files (the original where there is none); "
            "originals: the files as uploaded; both: originals plus the archive files."
        )
    ),
]
type Compressed = Annotated[Compression, Field(description="ZIP compression.")]
type FollowFormatting = Annotated[
    bool, Field(description="Name the files by the storage path format of each document.")
]

FILE_LEAD = (
    "Get the original file of a document, or its archive version, as an embedded resource. "
    "The file comes attached as MCP content with its MIME type and file name (also in the JSON "
    "report before it)"
)
ARCHIVE_LEAD = (
    "Get the files of several documents as one ZIP archive, attached as an embedded resource. "
    "The archive comes as MCP content after a JSON report (file name, size, delivered)"
)
NOT_SAVED = ", not saved on the server's machine. "
FILE_PARTS = (
    "A file over the server's download limit (512 KiB unless the administrator changed it) is "
    "not attached: delivered is false and the note says what to do. With offset you get one part "
    "of a file of up to 2 GiB: start with offset=0, then repeat the arguments that next in the "
    "report names until next_offset is null, and append the parts. Use get_document_content when "
    f"you only need the text. {DOCUMENT_TEXT}"
)
ARCHIVE_PARTS = (
    "An archive over the server's download limit (512 KiB unless the administrator changed it) "
    "is not attached: delivered is false and the note says what to do. With offset you get one "
    "part of an archive of up to 2 GiB: start with offset=0, then repeat the arguments that next "
    "in the report names until next_offset is null, and append the parts. Paperless builds the "
    "archive anew for every part; if a document changed in between, start again. The archive "
    "holds the newest version of each document. The whole request fails if one document is "
    "hidden from the user. Use download_document for one file."
)


@dataclass(frozen=True, slots=True)
class Part:
    """The part a caller asks for; without ``offset`` the whole file."""

    offset: int | None
    max_bytes: int | None
    checksum: str | None

    def __bool__(self) -> bool:
        """Whether any argument of a part was given."""
        return (self.offset, self.max_bytes, self.checksum) != (None, None, None)


def text(report: BaseModel) -> TextContent:
    """The JSON report, the first block of every result."""
    return TextContent(type="text", text=report.model_dump_json())


def _result(
    report: BaseModel, data: bytes | None, uri: str, mime_type: str, filename: str | None
) -> ToolResult:
    content: list[ContentBlock] = [text(report)]
    if data is not None:
        blob = BlobResourceContents(
            uri=uri,
            mime_type=mime_type,
            blob=base64.b64encode(data).decode(),
            _meta=None if filename is None else {"filename": filename},
        )
        content.append(EmbeddedResource(type="resource", resource=blob))
    return ToolResult(content=content)


async def read_file(
    files: FileService,
    document_id: int,
    kind: FileVersion,
    version_id: int | None,
    part: Part,
) -> ToolResult:
    """A file attached whole (no ``offset``), or one part of it."""
    offset, max_bytes, checksum = part.offset, part.max_bytes, part.checksum
    check_part(offset, max_bytes, checksum)
    base = f"paperless://documents/{document_id}"
    if offset is None:
        delivery = await files.download(document_id, kind, version_id=version_id)
        uri = f"{base}/{kind}" if version_id is None else f"{base}/versions/{version_id}/{kind}"
        report = delivery.report
        return _result(report, delivery.data, uri, report.mime_type, report.filename)
    found = await files.part(
        document_id,
        kind,
        version_id=version_id,
        offset=offset,
        max_bytes=max_bytes,
        checksum=checksum,
    )
    window = f"?offset={offset}&length={found.report.length}"
    uri = f"{base}/versions/{found.report.version_id}/{kind}{window}"
    return _result(found.report, found.data or None, uri, OCTETS, found.report.filename)


async def read_archive(  # noqa: PLR0913 -- the tool's parameters
    system: SystemAdmin,
    document_ids: list[int],
    content: ArchiveContent,
    compression: Compression,
    part: Part,
    *,
    follow_formatting: bool,
) -> ToolResult:
    """A ZIP archive attached whole (no ``offset``), or one part of it."""
    offset, max_bytes, checksum = part.offset, part.max_bytes, part.checksum
    check_part(offset, max_bytes, checksum)
    selection = f"{content}:{','.join(map(str, document_ids))}".encode()
    base = f"paperless://documents/archive/{hashlib.sha256(selection).hexdigest()[:16]}"
    if offset is None:
        delivery = await system.download(
            document_ids,
            content=content,
            compression=compression,
            follow_formatting=follow_formatting,
        )
        report = delivery.report
        uri = f"{base}/{report.filename}"
        return _result(report, delivery.data, uri, report.mime_type, report.filename)
    found = await system.part(
        document_ids,
        content=content,
        compression=compression,
        follow_formatting=follow_formatting,
        offset=offset,
        max_bytes=max_bytes,
        checksum=checksum,
    )
    uri = f"{base}/{found.report.filename}?offset={offset}&length={found.report.length}"
    return _result(found.report, found.data or None, uri, OCTETS, found.report.filename)


@remote.tool(
    tags={CORE},
    annotations=ANNOTATIONS["download_document"],
    description=FILE_LEAD + NOT_SAVED + FILE_PARTS,
)
@boundary
async def download_document(  # noqa: PLR0913 -- the file and the part of it
    ctx: Context,
    document_id: DocumentId,
    kind: Kind = "original",
    *,
    version_id: VersionId = None,
    offset: Offset = None,
    max_bytes: MaxBytes = None,
    checksum: Checksum = None,
) -> ToolResult:
    """Attach a file or a part of it; the tool description is above."""
    return await read_file(
        services(ctx).files, document_id, kind, version_id, Part(offset, max_bytes, checksum)
    )


@remote.tool(
    tags={ADMIN},
    annotations=ANNOTATIONS["bulk_download"],
    description=ARCHIVE_LEAD + NOT_SAVED + ARCHIVE_PARTS,
)
@boundary
async def bulk_download(  # noqa: PLR0913 -- the archive and the part of it
    ctx: Context,
    document_ids: DocumentIds,
    *,
    content: Content = "archive",
    compression: Compressed = "none",
    follow_formatting: FollowFormatting = False,
    offset: Offset = None,
    max_bytes: MaxBytes = None,
    checksum: Checksum = None,
) -> ToolResult:
    """Attach an archive or a part of it; the tool description is above."""
    return await read_archive(
        services(ctx).system,
        document_ids,
        content,
        compression,
        Part(offset, max_bytes, checksum),
        follow_formatting=follow_formatting,
    )
