"""Form fields of inserted pages as pictures of what they show.

A field belongs to the form of its file. In another file it would be drawn by some readers only, or
with the value of a field of the same name there. So the fields of a page are taken off, in memory,
and drawn on it again as one stamp: an annotation does not depend on the form, and not on what the
content of the page leaves behind (a clip, invisible text), as more content would.
"""

from typing import TYPE_CHECKING, Final

import pypdfium2
import pypdfium2.raw as pdfium

from mcp_server_paperless.domain.rendering import RenderFailed

if TYPE_CHECKING:
    from collections.abc import Sequence

# Annotation flags: not shown on a screen, and shown on paper.
HIDDEN: Final = 2
NO_VIEW: Final = 32
PRINT: Final = 4
# Printed, and neither changed nor moved by accident: read only, locked, locked contents.
STAMP: Final = PRINT | 64 | 128 | 512

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
            _done(pdfium.FPDFPage_RemoveAnnot(page, place))


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
        left, bottom, right, top = page.get_mediabox()
        box = (min(left, right), min(bottom, top), max(left, right), max(bottom, top))
        if _flattened(forms, number, box):
            _stamp(forms, number, page, into, box)
        page.close()


def _opened(source: bytes) -> pypdfium2.PdfDocument:
    """A copy whose form is loaded."""
    forms = pypdfium2.PdfDocument(source)
    _ = forms.init_forms()
    return forms


def _done(result: object) -> None:
    """What PDFium answers, if it did what it was asked for."""
    if not result:
        message = "Failed to change a page."
        raise pypdfium2.PdfiumError(message)


def _flattened(forms: pypdfium2.PdfDocument, number: int, box: Box) -> bool:
    """Make the fields of a page its only content; False if none of them is shown.

    Shown is what is neither hidden nor left out on paper.
    """
    page = forms[number]
    shown = False
    for place, field, flags in _annotations(page):
        if field and flags & PRINT and not flags & (HIDDEN | NO_VIEW):
            shown = True
        else:
            _done(pdfium.FPDFPage_RemoveAnnot(page, place))
    if not shown:
        return False
    for place in reversed(range(pdfium.FPDFPage_CountObjects(page))):
        part = pdfium.FPDFPage_GetObject(page, place)
        _done(pdfium.FPDFPage_RemoveObject(page, part))
        pdfium.FPDFPageObj_Destroy(part)
    _done(pdfium.FPDFPage_GenerateContent(page))
    page.set_rotation(0)
    page.close()
    page = forms[number]
    _done(pdfium.FPDFPage_Flatten(page, pdfium.FLAT_PRINT) == pdfium.FLATTEN_SUCCESS)
    page.set_mediabox(*box)
    page.set_cropbox(*box)
    page.close()
    page = forms[number]
    if not _alone(page):
        raise RenderFailed(reason="damaged", of_source=True)
    page.close()
    return True


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


def _stamp(
    forms: pypdfium2.PdfDocument,
    number: int,
    page: pypdfium2.PdfPage,
    into: pypdfium2.PdfDocument,
    box: Box,
) -> None:
    """Put a page of ``forms`` on ``page``, a page of ``into``, as a stamp as large as ``box``."""
    left, bottom, right, top = box
    picture = pdfium.FPDF_NewXObjectFromPage(into, forms, number)
    _done(picture)
    part = pdfium.FPDF_NewFormObjectFromXObject(picture)
    pdfium.FPDF_CloseXObject(picture)
    # The picture of a page begins at its corner.
    pdfium.FPDFPageObj_Transform(part, 1, 0, 0, 1, left, bottom)
    note = pdfium.FPDFPage_CreateAnnot(page, pdfium.FPDF_ANNOT_STAMP)
    _done(note)
    # First the rectangle: what is appended gets its box from it.
    done = [
        pdfium.FPDFAnnot_SetRect(note, pdfium.FS_RECTF(left, top, right, bottom)),
        pdfium.FPDFAnnot_AppendObject(note, part),
        pdfium.FPDFAnnot_SetFlags(note, STAMP),
    ]
    pdfium.FPDFPage_CloseAnnot(note)
    _done(all(done))
