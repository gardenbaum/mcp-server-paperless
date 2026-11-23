# The part of PDFium's C interface the renderer uses.

from typing import Final

from pypdfium2 import PdfBitmap, PdfFormEnv, PdfPage

FPDFBitmap_BGR: Final[int]
FPDF_ANNOT: Final[int]

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
