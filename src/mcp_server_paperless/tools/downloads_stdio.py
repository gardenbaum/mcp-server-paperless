"""download_document and bulk_download for stdio with download directories: attached or saved.

Registered instead of the plain variant (``downloads``) only when the server runs over stdio, the
operator named directories in ``PAPERLESS_MCP_DOWNLOAD_DIRS`` and read-only mode is off
(decision-45); over HTTP these tools do not exist, so their ``save_dir`` is in no schema a remote
caller sees. The service writes one new file inside an allowed directory, or replaces one after
``confirm``, and answers every other directory the same way.
"""

from pathlib import Path
from typing import Annotated

from fastmcp import Context
from fastmcp.server.providers import LocalProvider
from fastmcp.tools import ToolResult
from pydantic import Field

from mcp_server_paperless.domain.errors import Validation
from mcp_server_paperless.tools._shared import (
    SAVING_ANNOTATIONS,
    Confirm,
    boundary,
    check_transport,
    services,
)
from mcp_server_paperless.tools.downloads import (
    ADMIN,
    ARCHIVE_LEAD,
    ARCHIVE_PARTS,
    CORE,
    FILE_LEAD,
    FILE_PARTS,
    Checksum,
    Compressed,
    Content,
    DocumentId,
    DocumentIds,
    FollowFormatting,
    Kind,
    MaxBytes,
    Offset,
    Part,
    VersionId,
    read_archive,
    read_file,
    text,
)

local = LocalProvider()

type SaveDir = Annotated[
    Path | None,
    Field(
        description=(
            "Absolute path of an existing directory on the machine this server runs on, inside "
            "the directories the operator allowed; the file is saved there instead of attached."
        )
    ),
]
type SaveAs = Annotated[
    str | None,
    Field(
        description=(
            "File name (no path) to save under, with the ending of its type; default: the name "
            "Paperless proposes."
        )
    ),
]

SAVED = (
    "; with save_dir it is saved in that directory instead, and only the report comes back. "
    "An existing file is replaced only with confirm=true; ask the user first. "
)
HTTP_HINT = "Read the file in parts with offset."


def check_saving(
    save_dir: Path | None,
    save_as: str | None,
    *,
    confirm: bool,
    part: Part,
    transport: str | None,
) -> str | None:
    """The directory to save into, as it was given; None to attach.

    Raises:
        Validation: Arguments that do not go together, or a directory over HTTP.
    """
    if save_dir is None:
        if save_as is not None or confirm:
            message = "save_as and confirm are for saving a file."
            raise Validation(message, hint="Give save_dir, too, or leave them out.")
        return None
    if part:
        message = "save_dir does not go with offset, max_bytes or checksum."
        hint = "A file is saved whole; leave them out, or leave out save_dir to read it in parts."
        raise Validation(message, hint=hint)
    check_transport(transport, hint=HTTP_HINT)
    return str(save_dir)


@local.tool(
    tags={CORE},
    annotations=SAVING_ANNOTATIONS["download_document"],
    description=FILE_LEAD + SAVED + FILE_PARTS,
)
@boundary
async def download_document(  # noqa: PLR0913 -- the file, the part of it and where it goes
    ctx: Context,
    document_id: DocumentId,
    kind: Kind = "original",
    *,
    version_id: VersionId = None,
    offset: Offset = None,
    max_bytes: MaxBytes = None,
    checksum: Checksum = None,
    save_dir: SaveDir = None,
    save_as: SaveAs = None,
    confirm: Confirm = False,
) -> ToolResult:
    """Attach a file or a part of it, or save it; the tool description is above."""
    files, part = services(ctx).files, Part(offset, max_bytes, checksum)
    directory = check_saving(save_dir, save_as, confirm=confirm, part=part, transport=ctx.transport)
    if directory is None:
        return await read_file(files, document_id, kind, version_id, part)
    report = await files.save(
        document_id,
        kind,
        version_id=version_id,
        directory=directory,
        save_as=save_as,
        confirm=confirm,
    )
    return ToolResult(content=[text(report)])


@local.tool(
    tags={ADMIN},
    annotations=SAVING_ANNOTATIONS["bulk_download"],
    description=ARCHIVE_LEAD + SAVED + ARCHIVE_PARTS,
)
@boundary
async def bulk_download(  # noqa: PLR0913 -- the archive, the part of it and where it goes
    ctx: Context,
    document_ids: DocumentIds,
    *,
    content: Content = "archive",
    compression: Compressed = "none",
    follow_formatting: FollowFormatting = False,
    offset: Offset = None,
    max_bytes: MaxBytes = None,
    checksum: Checksum = None,
    save_dir: SaveDir = None,
    save_as: SaveAs = None,
    confirm: Confirm = False,
) -> ToolResult:
    """Attach an archive or a part of it, or save it; the tool description is above."""
    system, part = services(ctx).system, Part(offset, max_bytes, checksum)
    directory = check_saving(save_dir, save_as, confirm=confirm, part=part, transport=ctx.transport)
    if directory is None:
        return await read_archive(
            system, document_ids, content, compression, part, follow_formatting=follow_formatting
        )
    report = await system.save(
        document_ids,
        content=content,
        compression=compression,
        follow_formatting=follow_formatting,
        directory=directory,
        save_as=save_as,
        confirm=confirm,
    )
    return ToolResult(content=[text(report)])
