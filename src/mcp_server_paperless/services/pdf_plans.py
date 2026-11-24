"""What a PDF operation makes, keeps and loses, in words (probed on 3.5.1, PLMCP-79)."""

from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.pdf import PdfPlan

if TYPE_CHECKING:
    from mcp_server_paperless.domain.pdf import Output

METADATA: Final = (
    "title, correspondent, document type, storage path, tags, created date, custom field values "
    "and view and change grants"
)
NEVER_COPIED: Final = (
    "notes, history, earlier versions, share links and corrections of the text are not copied"
)
NEW_DOCUMENT: Final = (
    "a new document belongs to the calling user and is processed like an upload: workflows, "
    "matching and barcodes can change it, split it again or stop it"
)
READ_AGAIN: Final = "the text is read again, so corrections of it are not in the new version"
NUMBER_BLOCKED: Final = (
    "an archive serial number stays with the trashed original: restoring brings it back, and it "
    "is blocked until the trash is emptied"
)
NUMBER_MOVES: Final = "the archive serial number, if there is one, moves to the new document"
NUMBER_KEPT: Final = "an archive serial number stays with the original"


def document_plan(  # noqa: PLR0913 -- what the words depend on
    produces: str,
    source: str,
    *,
    loses: tuple[str, ...] = (),
    keep_metadata: bool = True,
    trashes: bool = False,
    number_moves: bool = False,
    metadata_of: str | None = None,
    title: str = "",
) -> PdfPlan:
    """The plan of an operation that makes new documents from ``source``.

    ``metadata_of`` names what the metadata is taken from where it is not ``source``; ``title``
    adds what becomes of the title.
    """
    metadata = f"{METADATA} of {metadata_of or source}{title if keep_metadata else ''}"
    kept = [f"new documents take {metadata}"] if keep_metadata else []
    without = () if keep_metadata else (f"new documents take nothing of {metadata}",)
    lost = [*loses, *without, NEVER_COPIED, NEW_DOCUMENT]
    if trashes:
        trashed = (
            f"{source}: moved to the Paperless trash once everything that was made is checked; "
            "can be restored from there"
        )
        (kept if number_moves else lost).append(NUMBER_MOVES if number_moves else NUMBER_BLOCKED)
        lost.append(trashed)
    else:
        kept += [f"{source}: unchanged", NUMBER_KEPT]
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
