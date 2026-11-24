# The part of PDFium's C interface the renderer and its tests use.

from typing import Final

from pypdfium2 import PdfBitmap, PdfDocument, PdfFormEnv, PdfPage

FPDFBitmap_BGR: Final[int]
FPDF_ANNOT: Final[int]
FPDF_ANNOT_FILEATTACHMENT: Final[int]

class _Annotation: ...

def FPDFPage_GetAnnotCount(page: PdfPage) -> int: ...
def FPDFPage_GetAnnot(page: PdfPage, index: int) -> _Annotation: ...
def FPDFPage_CloseAnnot(annot: _Annotation) -> None: ...
def FPDFAnnot_GetSubtype(annot: _Annotation) -> int: ...
def FPDFAnnot_HasKey(annot: _Annotation, key: bytes) -> int: ...
def FPDFAnnot_GetAP(annot: _Annotation, mode: int, buffer: object, buflen: int) -> int: ...
def FPDF_GetSecurityHandlerRevision(document: PdfDocument) -> int: ...
def FPDFDoc_GetAttachmentCount(document: PdfDocument) -> int: ...
def FPDF_RenderPageBitmap(
    bitmap: PdfBitmap,
    page: PdfPage,
    start_x: int,
    start_y: int,
    size_x: int,
    size_y: int,
    rotate: int,
    flags: int,
) -> None: ...
def FPDF_FFLDraw(
    hHandle: PdfFormEnv,
    bitmap: PdfBitmap,
    page: PdfPage,
    start_x: int,
    start_y: int,
    size_x: int,
    size_y: int,
    rotate: int,
    flags: int,
) -> None: ...
