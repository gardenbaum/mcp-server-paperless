"""Form fields of inserted pages as pictures of what they show.

A field belongs to the form of its file. In another file it would be drawn by some readers only, or
with the value of a field of the same name there. So the fields of a page are taken off, in memory,
and drawn on it again as one stamp: an annotation does not depend on the form, and not on what the
content of the page leaves behind (a clip, invisible text), as more content would.
"""

import ctypes
import re
from collections import Counter
from itertools import combinations, pairwise
from typing import TYPE_CHECKING, Final

import pypdfium2
import pypdfium2.raw as pdfium

from mcp_server_paperless.domain.rendering import RenderFailed

if TYPE_CHECKING:
    from collections.abc import Sequence

# Annotation flags: not shown (the two), and printed.
HIDDEN: Final = 2
NO_VIEW: Final = 32
NO_ROTATE: Final = 16
PRINT: Final = 4
# Printed, and neither changed nor moved by accident: read only, locked, locked contents.
STAMP: Final = PRINT | 64 | 128 | 512

# What a field has in a file that PDFium wrote: names and keys are written as they are (a regular
# character is never escaped), and the kind of every other annotation as a name.
MARK: Final = re.compile(
    rb"/(?:FT|Ff|V|DV|RV|TU|TM|MK|MaxLen|Widget)(?![^\s/()<>\[\]{}%])|/Subtype(?![A-Za-z0-9/])"
)
# Keys of a field. An annotation of no known kind that has one is taken for a field.
FIELD_KEYS: Final = (b"FT", b"Ff", b"V", b"DV", b"RV", b"MK", b"TU", b"MaxLen", b"Parent")
# What tells the value of a field, and the way to the field above it.
VALUES: Final = (b"V", b"DV", b"RV", b"I", b"Parent")
# Which of its appearances a field shows.
STATES: Final = (b"AS",)
# Pointed at, pressed, and last the normal one, with which all go.
APPEARANCES: Final = (1, 2, 0)
NOTHING: Final = (ctypes.c_ushort * 1)()
# Stamps of one page; each draws the whole picture, cut to its rectangle.
MAX_STAMPS: Final = 100

type Box = tuple[float, float, float, float]


def _is_field(note: pdfium.FPDF_ANNOTATION) -> bool:
    """Whether an annotation is a field, for PDFium or for a reader that looks closer.

    PDFium knows a kind that is written as a direct name only; others follow a reference, too.
    """
    kind = pdfium.FPDFAnnot_GetSubtype(note)
    if kind != pdfium.FPDF_ANNOT_UNKNOWN:
        return kind == pdfium.FPDF_ANNOT_WIDGET
    written = pdfium.FPDFAnnot_GetValueType(note, b"Subtype")
    odd = written not in {pdfium.FPDF_OBJECT_UNKNOWN, pdfium.FPDF_OBJECT_NAME}
    return odd or any(pdfium.FPDFAnnot_HasKey(note, key) for key in FIELD_KEYS)


def _annotations(page: pypdfium2.PdfPage) -> list[tuple[int, bool, int]]:
    """Of each annotation of a page its place, whether it is a field, and its flags.

    Last first, so that one can be removed without moving the others.
    """
    found: list[tuple[int, bool, int]] = []
    for place in reversed(range(pdfium.FPDFPage_GetAnnotCount(page))):
        note = pdfium.FPDFPage_GetAnnot(page, place)
        found.append((place, _is_field(note), pdfium.FPDFAnnot_GetFlags(note)))
        pdfium.FPDFPage_CloseAnnot(note)
    return found


def has_fields(page: pypdfium2.PdfPage) -> bool:
    """Whether a page has form fields."""
    return any(field for _, field, _ in _annotations(page))


def marks_in(content: bytes) -> Counter[str] | None:
    """What fields have, in the objects of a file that PDFium wrote; None if it cannot be told.

    Streams are left out: an object ends where the next one of the table begins, and no line break
    stands in an object before its stream (PDFium escapes those of strings).
    """
    start = re.search(rb"startxref\r\n(\d+)\r\n%%EOF\r\n\Z", content[-64:])
    if start is None or not content.startswith(b"xref\r\n", int(start[1])):
        return None
    table = int(start[1])
    places = re.finditer(rb"(\d{10}) \d{5} n", content[table:])
    ends = sorted({table, *(int(place[1]) for place in places)})
    marks = Counter[str]()
    for begin, end in pairwise(ends):
        stream = content.find(b"stream\r\n", begin, end)
        body = content[begin : end if stream < 0 else stream]
        marks.update(found[0].decode() for found in MARK.finditer(body))
    return marks


def remove_fields(page: pypdfium2.PdfPage) -> None:
    """Take the fields off a page; everything else stays."""
    for place, field, _ in _annotations(page):
        if field:
            require(pdfium.FPDFPage_RemoveAnnot(page, place))


def blank_fields(pdf: pypdfium2.PdfDocument) -> None:
    """Empty the fields of every page of a file, because a page can refer to those of others.

    A value that stands in a field above the ones of the pages is out of reach.

    Raises:
        RenderFailed: A page cannot be read, and so its fields cannot be emptied.
    """
    for number in range(len(pdf)):
        try:
            page = pdf[number]
        except pypdfium2.PdfiumError as error:
            raise RenderFailed(reason="fields", of_source=True) from error
        for place, field, _ in _annotations(page):
            if field:
                _empty_field(page, place)
        page.close()


def _empty_field(page: pypdfium2.PdfPage, place: int) -> None:
    """Take the value off a field, and how it looks."""
    note = pdfium.FPDFPage_GetAnnot(page, place)
    _set_empty([note], (*VALUES, *STATES), look=True)
    pdfium.FPDFPage_CloseAnnot(note)


def _set_empty(
    notes: Sequence[pdfium.FPDF_ANNOTATION], keys: Sequence[bytes], *, look: bool = False
) -> None:
    """Empty entries of annotations; with ``look`` also how they look."""
    for note in notes:
        for key in keys:
            require(pdfium.FPDFAnnot_SetStringValue(note, key, NOTHING))
        for mode in APPEARANCES if look else ():
            _ = pdfium.FPDFAnnot_SetAP(note, mode, None)  # 0: there was none


def cut_parents(page: pypdfium2.PdfPage) -> None:
    """Take the way to a parent off the annotations of an imported page.

    PDFium does not translate it: its number is that of another object here.
    """
    for place, _, _ in _annotations(page):
        note = pdfium.FPDFPage_GetAnnot(page, place)
        if pdfium.FPDFAnnot_HasKey(note, b"Parent"):
            require(pdfium.FPDFAnnot_SetStringValue(note, b"Parent", NOTHING))
        pdfium.FPDFPage_CloseAnnot(note)


def draw_fields(
    source: bytes,
    into: pypdfium2.PdfDocument,
    places: Sequence[tuple[int, int]],
    *,
    blank: bool,
) -> None:
    """Draw the fields of pages (from 0) of ``source`` on the pages they became in ``into``.

    The form is loaded, so PDFium draws a field as a reader of the source would where nothing is
    stored or the file asks for that. No script runs: the builds have no V8. With ``blank`` nothing
    but the picture comes along.

    Raises:
        RenderFailed: The fields of a page cannot be drawn.
        pypdfium2.PdfiumError: PDFium could not do it.
    """
    if not places:
        return
    forms = _opened(source)
    for number, place in places:
        # A copy for each page if it is emptied: pages can share their fields. And two pages can be
        # one object, whose fields are used up.
        if blank or not has_fields(forms[number]):
            forms = _opened(source)
        page = into[place]
        box = _ordered(*page.get_mediabox())
        rectangles = _flattened(forms, number, box, blank=blank)
        if rectangles:
            if blank:
                # The resources of a page come along with its picture, and what they refer to.
                blank_fields(forms)
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


def _flattened(forms: pypdfium2.PdfDocument, number: int, box: Box, *, blank: bool) -> list[Box]:
    """Make the fields of a page its only content; the rectangles of those that are shown.

    Shown is what a screen shows, also where it would not be printed.
    """
    page = forms[number]
    shown: list[Box] = []
    for place, field, flags in _annotations(page):
        rectangle = _rectangle(page, place)
        if not field or flags & (HIDDEN | NO_VIEW) or _empty(rectangle):
            if field and blank:
                _empty_field(page, place)  # the resources of the page can refer to it
            require(pdfium.FPDFPage_RemoveAnnot(page, place))
            continue
        if flags & NO_ROTATE and page.get_rotation():
            # A picture turns with its page.
            raise RenderFailed(reason="fields", of_source=True)
        shown.append(rectangle)
        note = pdfium.FPDFPage_GetAnnot(page, place)
        if pdfium.FPDFAnnot_GetSubtype(note) != pdfium.FPDF_ANNOT_WIDGET:
            # Other readers show it, PDFium cannot draw it.
            raise RenderFailed(reason="fields", of_source=True)
        # Only printed ones are flattened, but a value on the screen must not be lost.
        require(pdfium.FPDFAnnot_SetFlags(note, PRINT))
        pdfium.FPDFPage_CloseAnnot(note)
    if not shown:
        return []
    for place in reversed(range(pdfium.FPDFPage_CountObjects(page))):
        part = pdfium.FPDFPage_GetObject(page, place)
        require(pdfium.FPDFPage_RemoveObject(page, part))
        pdfium.FPDFPageObj_Destroy(part)
    require(pdfium.FPDFPage_GenerateContent(page))
    page.set_rotation(0)
    # Not before: loaded again, a field would be drawn without its value.
    places = _annotations(page) if blank else []
    notes = [pdfium.FPDFPage_GetAnnot(page, place) for place, _, _ in places]
    _set_empty(notes, VALUES)
    flattened = pdfium.FPDFPage_Flatten(page, pdfium.FLAT_PRINT)
    # And these not before: how it looks is in the picture now, and must not be drawn anew there
    # when another page with the same field is loaded.
    _set_empty(notes, STATES, look=True)
    for note in notes:
        pdfium.FPDFPage_CloseAnnot(note)
    require(flattened == pdfium.FLATTEN_SUCCESS)
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


def _rectangle(page: pypdfium2.PdfPage, place: int) -> Box:
    """Where an annotation is."""
    note, at = pdfium.FPDFPage_GetAnnot(page, place), pdfium.FS_RECTF()
    _ = pdfium.FPDFAnnot_GetRect(note, at)  # without one it stays empty
    pdfium.FPDFPage_CloseAnnot(note)
    return _ordered(at.left, at.bottom, at.right, at.top)


def _empty(box: Box) -> bool:
    return box[0] == box[2] or box[1] == box[3]


def _overlap(one: Box, other: Box) -> bool:
    return one[0] < other[2] and other[0] < one[2] and one[1] < other[3] and other[1] < one[3]


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
    stamp for each field, and what is between them stays reachable; else one for all, as where
    fields overlap (they would be drawn twice).
    """
    cuts = rectangles
    if (
        not pdfium.FPDFPage_GetAnnotCount(page)
        or len(cuts) > MAX_STAMPS
        or any(_overlap(one, other) for one, other in combinations(cuts, 2))
    ):
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
