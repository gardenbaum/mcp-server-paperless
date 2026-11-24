"""Form fields of inserted pages as pictures of what they show.

A field belongs to the form of its file. In another file it would be drawn by some readers only, or
with the value of a field of the same name there. So the fields of a page are taken off, in memory,
and drawn on it again as one stamp: an annotation does not depend on the form, and not on what the
content of the page leaves behind (a clip, invisible text), as more content would.
"""

import ctypes
from typing import TYPE_CHECKING, Final

import pypdfium2
import pypdfium2.raw as pdfium

from mcp_server_paperless.domain.rendering import RenderFailed

if TYPE_CHECKING:
    from collections.abc import Sequence

# Annotation flags: not shown (the two), and printed.
HIDDEN: Final = 2
NO_VIEW: Final = 32
PRINT: Final = 4
# Printed, and neither changed nor moved by accident: read only, locked, locked contents.
STAMP: Final = PRINT | 64 | 128 | 512

# What tells the value of a field, and the way to the field above it.
VALUES: Final = (b"V", b"DV", b"RV", b"I", b"AS", b"Parent")
# Pointed at, pressed, and last the normal one, with which all go.
APPEARANCES: Final = (1, 2, 0)
# Stamps of one page; each draws the whole picture, cut to its rectangle.
MAX_STAMPS: Final = 100

type Box = tuple[float, float, float, float]


def _annotations(page: pypdfium2.PdfPage) -> list[tuple[int, bool, int]]:
    """Of each annotation of a page its place, whether it is a field, and its flags.

    Last first, so that one can be removed without moving the others.
    """
    found: list[tuple[int, bool, int]] = []
    for place in reversed(range(pdfium.FPDFPage_GetAnnotCount(page))):
        note = pdfium.FPDFPage_GetAnnot(page, place)
        kind, flags = pdfium.FPDFAnnot_GetSubtype(note), pdfium.FPDFAnnot_GetFlags(note)
        found.append((place, kind == pdfium.FPDF_ANNOT_WIDGET, flags))
        pdfium.FPDFPage_CloseAnnot(note)
    return found


def has_fields(page: pypdfium2.PdfPage) -> bool:
    """Whether a page has form fields."""
    return any(field for _, field, _ in _annotations(page))


def remove_fields(page: pypdfium2.PdfPage) -> None:
    """Take the fields off a page; everything else stays."""
    for place, field, _ in _annotations(page):
        if field:
            require(pdfium.FPDFPage_RemoveAnnot(page, place))


def blank_fields(page: pypdfium2.PdfPage) -> None:
    """Empty the fields of a page, because links and comments of other pages can refer to them.

    A value that stands in a field above the ones of the pages is out of reach.
    """
    nothing = (ctypes.c_ushort * 1)()
    for place, field, _ in _annotations(page):
        if field:
            note = pdfium.FPDFPage_GetAnnot(page, place)
            done = [pdfium.FPDFAnnot_SetStringValue(note, key, nothing) for key in VALUES]
            _ = [pdfium.FPDFAnnot_SetAP(note, mode, None) for mode in APPEARANCES]
            pdfium.FPDFPage_CloseAnnot(note)
            require(all(done))


def draw_fields(
    source: bytes, into: pypdfium2.PdfDocument, places: Sequence[tuple[int, int]]
) -> None:
    """Draw the fields of pages (from 0) of ``source`` on the pages they became in ``into``.

    The form is loaded, so PDFium draws a field as a reader of the source would where nothing is
    stored or the file asks for that. No script runs: the builds have no V8.

    Raises:
        RenderFailed: The fields of a page cannot be drawn.
        pypdfium2.PdfiumError: PDFium could not do it.
    """
    if not places:
        return
    forms = _opened(source)
    for number, place in places:
        if not has_fields(forms[number]):
            forms = _opened(source)  # two pages are one object, whose fields are used up
        page = into[place]
        box = _ordered(*page.get_mediabox())
        rectangles = _flattened(forms, number, box)
        if rectangles:
            _stamp(forms, number, page, into, box, rectangles)
        page.close()


def _opened(source: bytes) -> pypdfium2.PdfDocument:
    """A copy whose form is loaded."""
    forms = pypdfium2.PdfDocument(source)
    _ = forms.init_forms()
    return forms


def require(result: object) -> None:
    """Check the answer of PDFium, which is 0 or none where it did not do it.

    Raises:
        pypdfium2.PdfiumError: It did not.
    """
    if not result:
        message = "Failed to change a page."
        raise pypdfium2.PdfiumError(message)


def _flattened(forms: pypdfium2.PdfDocument, number: int, box: Box) -> list[Box]:
    """Make the fields of a page its only content; the rectangles of those that are shown.

    Shown is what a screen shows, also where it would not be printed.
    """
    page = forms[number]
    shown: list[Box] = []
    for place, field, flags in _annotations(page):
        if field and not flags & (HIDDEN | NO_VIEW):
            # Only printed ones are flattened, but a value on the screen must not be lost.
            note = pdfium.FPDFPage_GetAnnot(page, place)
            require(pdfium.FPDFAnnot_SetFlags(note, PRINT))
            at = pdfium.FS_RECTF()
            require(pdfium.FPDFAnnot_GetRect(note, at))
            shown.append(_ordered(at.left, at.bottom, at.right, at.top))
            pdfium.FPDFPage_CloseAnnot(note)
        else:
            require(pdfium.FPDFPage_RemoveAnnot(page, place))
    if not shown:
        return []
    for place in reversed(range(pdfium.FPDFPage_CountObjects(page))):
        part = pdfium.FPDFPage_GetObject(page, place)
        require(pdfium.FPDFPage_RemoveObject(page, part))
        pdfium.FPDFPageObj_Destroy(part)
    require(pdfium.FPDFPage_GenerateContent(page))
    page.set_rotation(0)
    page.close()
    page = forms[number]
    require(pdfium.FPDFPage_Flatten(page, pdfium.FLAT_PRINT) == pdfium.FLATTEN_SUCCESS)
    page.set_mediabox(*box)
    page.set_cropbox(*box)
    page.close()
    page = forms[number]
    if not _alone(page):
        raise RenderFailed(reason="fields", of_source=True)
    page.close()
    return shown


def _ordered(left: float, bottom: float, right: float, top: float) -> Box:
    """A rectangle whose corners may be exchanged."""
    return min(left, right), min(bottom, top), max(left, right), max(bottom, top)


def _alone(page: pypdfium2.PdfPage) -> bool:
    """Whether a page is its flattened fields: what was left of its content could hide them."""
    if pdfium.FPDFPage_CountObjects(page) != 1:
        return False
    part = pdfium.FPDFPage_GetObject(page, 0)
    at = pdfium.FS_MATRIX()
    clips = pdfium.FPDFClipPath_CountPaths(pdfium.FPDFPageObj_GetClipPath(part))
    return (
        pdfium.FPDFPageObj_GetType(part) == pdfium.FPDF_PAGEOBJ_FORM
        and bool(pdfium.FPDFPageObj_GetMatrix(part, at))
        and (at.a, at.b, at.c, at.d) == (1, 0, 0, 1)
        and clips <= 0
    )


def _stamp(  # noqa: PLR0913, PLR0917 -- a page and what is put on which other
    forms: pypdfium2.PdfDocument,
    number: int,
    page: pypdfium2.PdfPage,
    into: pypdfium2.PdfDocument,
    box: Box,
    rectangles: list[Box],
) -> None:
    """Put a page of ``forms`` on ``page``, a page of ``into``, as stamps.

    PDFium can only add a stamp above the other annotations. So where there are some, there is a
    stamp for each field, and what is between them stays reachable; else one for all.
    """
    cuts = list(dict.fromkeys(rectangles))
    if not pdfium.FPDFPage_GetAnnotCount(page) or len(cuts) > MAX_STAMPS:
        lefts, bottoms, rights, tops = zip(*cuts, strict=True)
        cuts = [(min(lefts), min(bottoms), max(rights), max(tops))]
    picture = pdfium.FPDF_NewXObjectFromPage(into, forms, number)
    require(picture)
    for left, bottom, right, top in cuts:
        part = pdfium.FPDF_NewFormObjectFromXObject(picture)
        # The picture of a page begins at its corner.
        pdfium.FPDFPageObj_Transform(part, 1, 0, 0, 1, box[0], box[1])
        note = pdfium.FPDFPage_CreateAnnot(page, pdfium.FPDF_ANNOT_STAMP)
        require(note)
        # First the rectangle: what is appended gets its box from it.
        done = [
            pdfium.FPDFAnnot_SetRect(note, pdfium.FS_RECTF(left, top, right, bottom)),
            pdfium.FPDFAnnot_AppendObject(note, part),
            pdfium.FPDFAnnot_SetFlags(note, STAMP),
        ]
        pdfium.FPDFPage_CloseAnnot(note)
        require(all(done))
    pdfium.FPDF_CloseXObject(picture)
