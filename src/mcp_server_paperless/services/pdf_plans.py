"""What a PDF operation makes, keeps and loses, in words (probed on 3.5.1, PLMCP-79)."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.pdf import PdfPlan
from mcp_server_paperless.services.removal import WAY_BACK

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.documents import DocumentRecord
    from mcp_server_paperless.domain.pdf import Output

METADATA: Final = (
    "title, correspondent, document type, storage path, tags, created date, custom field values "
    "and view and change grants"
)
NEVER_COPIED: Final = (
    "notes, history, earlier versions, share links, corrections of the text and the back links of "
    "document links are not copied"
)
NEW_DOCUMENT: Final = (
    "a new document belongs to the calling user, gets the inbox tags and is processed like an "
    "upload: workflows, matching and barcodes can change it, split it again or stop it"
)
REBUILT: Final = (
    "bookmarks, attachments, working form fields and signatures of the PDF are not in the result"
)
UNSIGNED: Final = "a signature in the PDF is no longer valid"
READ_AGAIN: Final = "the text is read again, so corrections of it are not in the new version"
MOVES: Final = (
    "archive serial number {number} of document {id} moves to the new document, unless that has "
    "one already; workflows for changed documents run for both"
)
BLOCKED: Final = (
    "archive serial number {number} of document {id} stays with the trashed original: restoring "
    "brings it back, and it is blocked until the trash is emptied"
)
STAYS: Final = "archive serial number {number} stays with document {id}"


@dataclass(frozen=True, slots=True)
class Numbers:
    """What becomes of the archive serial numbers of the documents."""

    keeps: tuple[str, ...]
    loses: tuple[str, ...]


def numbers(
    documents: Sequence[DocumentRecord], moves: DocumentRecord | None, *, trashes: bool
) -> Numbers:
    """The lines for the numbers: that of ``moves`` goes to the new document."""

    def line(text: str, document: DocumentRecord) -> str:
        return text.format(number=document.archive_serial_number, id=document.id)

    held = [d for d in documents if d.archive_serial_number is not None]
    if not trashes:
        return Numbers(tuple(line(STAYS, d) for d in held), ())
    return Numbers(
        tuple(line(MOVES, d) for d in held if d is moves),
        tuple(line(BLOCKED, d) for d in held if d is not moves),
    )


def without(source: str) -> str:
    """What new documents lack that take no metadata."""
    return f"new documents take nothing of {METADATA} of {source}"


def document_plan(  # noqa: PLR0913 -- what the words depend on
    produces: str,
    source: str,
    serials: Numbers,
    *,
    loses: tuple[str, ...] = (),
    keep_metadata: bool = True,
    trashes: bool = False,
    metadata_of: str | None = None,
    title: str = "",
) -> PdfPlan:
    """The plan of an operation that makes new documents from ``source``.

    ``metadata_of`` names what the metadata is taken from where it is not ``source``; ``title``
    adds what becomes of the title.
    """
    takes = f"new documents take {METADATA} of {metadata_of or source}{title}"
    kept = [takes] if keep_metadata else []
    lost = [*loses, *(() if keep_metadata else (without(source),)), NEVER_COPIED, NEW_DOCUMENT]
    lost += serials.loses
    if trashes:
        trashed = (
            f"{source}: moved to the Paperless trash once everything that was made is checked, "
            f"and each {WAY_BACK.rstrip('.')}"
        )
        lost.append(trashed)
    else:
        kept.append(f"{source}: unchanged")
    kept += serials.keeps
    return PdfPlan(produces=produces, keeps=tuple(kept), loses=tuple(lost), needs_confirm=trashes)


def version_plan(
    produces: str, source: str, *, loses: tuple[str, ...] = (), needs_confirm: bool = False
) -> PdfPlan:
    """The plan of an operation that adds a version to ``source``."""
    metadata = (
        f"{source}: title, tags, notes, custom fields, archive serial number, owner, grants and "
        "every other metadata"
    )
    return PdfPlan(
        produces=produces,
        keeps=(metadata, "every earlier version, until it is deleted"),
        loses=(*loses, READ_AGAIN),
        needs_confirm=needs_confirm,
    )


def outputs(count: int, output: Output) -> str:
    """What is made: new documents, or a version."""
    if output == "new_version":
        return "a new version of the document"
    return "1 new document" if count == 1 else f"{count} new documents"
