"""Pages of a document as images: what is asked of the renderer and what it answers.

The renderer is not a port: it talks to a child process, not to Paperless.
"""

from typing import Annotated, ClassVar, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# What the renderer reads: the MIME types Paperless has for a PDF and for the images it can open.
RENDERABLE: Final = frozenset(
    {"application/pdf", "image/jpeg", "image/png", "image/tiff", "image/webp"}
)

# Image bytes of one call. A single event of the MCP SDK's HTTP client may hold 1 MiB: 720 KiB are
# 960 KiB in base64 (measured: 720 KiB pass, 768 KiB in one image fail).
MAX_IMAGE_BYTES: Final = 720 * 1024
# 25 000 tokens is the default output limit of Claude Code; an image of 2576 pixels costs 4784.
MAX_IMAGES: Final = 5
# Pixels of an image original; 117 megapixels of PNG took 568 MB to decode.
MAX_PIXELS: Final = 100_000_000
# Pages of a file: more than any real document has. The page specification is walked one by one.
MAX_PAGES: Final = 200_000
# The long side of an image: 2576 pixels is the most Claude reads without scaling down, 1568 the
# most of its standard tier.
MIN_SIZE: Final = 256
MAX_SIZE: Final = 2576
DEFAULT_SIZE: Final = 1568
# Contact sheets: pages of one (4 x 3), and seconds after which the rest is left to another call.
TILES: Final = 12
SHEET_SECONDS: Final = 20.0
# When the last tile has to be drawn: what is left of the 30 seconds of the child is for writing
# the sheets, for starting it and for the pipe.
DRAWN_SECONDS: Final = 24.0
# Runs of equal pages in one layout.
MAX_RUNS: Final = 100
# The longest side of a page that is reported: ten kilometres.
MAX_SIDE_MM: Final = 10_000_000.0

# Left, top, right and bottom edge, as fractions of the page as shown.
type Region = tuple[float, float, float, float]
type Reason = Literal[
    "encrypted", "damaged", "pixels", "pages", "count", "timeout", "memory", "crashed"
]


class RenderRequest(BaseModel):
    """Which pages to render, and how.

    Attributes:
        mime_type: One of ``RENDERABLE``; it decides how the file is read.
        pages: Page specification (see ``services.page_ranges``).
        size: Pixels of the long side of each image.
        region: The part of each page to show, else the whole page.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    kind: Literal["pages"] = "pages"
    mime_type: str
    pages: str
    size: int
    region: Region | None = None
    max_images: int = MAX_IMAGES
    max_bytes: int = MAX_IMAGE_BYTES
    max_pixels: int = MAX_PIXELS


class PageImage(BaseModel):
    """One rendered page: a WebP image.

    Attributes:
        reduced: The page did not fit into the bytes of a call as asked, so it has a lower quality
            or fewer pixels.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    page: int
    width: int
    height: int
    data: bytes
    reduced: bool = False


class Rendered(BaseModel):
    """The rendered pages, in the order asked for, and the pages that did not fit any more."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    page_count: int
    images: tuple[PageImage, ...]
    rest: tuple[int, ...] = ()


class OverviewRequest(BaseModel):
    """Which pages of a PDF to describe, and within which limits.

    Attributes:
        pages: Page specification (see ``services.page_ranges``).
        sheets: Whether contact sheets are drawn.
        seconds: When to stop and leave the rest to another call.
        drawn_seconds: When the last page has to be drawn, if it is as slow as the slowest.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    kind: Literal["overview"] = "overview"
    pages: str
    sheets: bool = True
    max_sheets: int = MAX_IMAGES
    max_bytes: int = MAX_IMAGE_BYTES
    seconds: float = SHEET_SECONDS
    drawn_seconds: float = DRAWN_SECONDS


type Side = Annotated[float, Field(gt=0, le=MAX_SIDE_MM)]


class PageShape(BaseModel):
    """The size of a page as shown, and the rotation stored in the file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    width_mm: Side
    height_mm: Side
    rotation: Literal[0, 90, 180, 270]


class PageRun(BaseModel):
    """Pages after one another with the same shape; None if they cannot be read."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    pages: tuple[int, ...]
    shape: PageShape | None


class ContactSheet(BaseModel):
    """Small numbered pictures of pages, row by row: a WebP image.

    Attributes:
        reduced: It did not fit into the bytes of a call, so it has a lower quality or fewer pixels.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    pages: tuple[int, ...]
    width: int
    height: int
    data: bytes
    reduced: bool = False


class Overview(BaseModel):
    """The layout and the sheets, in the order asked for, and the pages they leave out.

    With sheets the layout is of the pages on them.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    page_count: int
    runs: tuple[PageRun, ...]
    layout_rest: tuple[int, ...] = ()
    sheets: tuple[ContactSheet, ...] = ()
    rest: tuple[int, ...] = ()


class RenderFailed(Exception):  # noqa: N818 -- an outcome, as the names of domain/errors.py
    """The file could not be rendered.

    Attributes:
        reason: Why; the caller turns it into a message.
        page_count: How many pages the file has, if the page specification is wrong.
        megapixels: How large the image is, if it is too large.
        page: The page it was at when the time was over, if it is known.
    """

    def __init__(
        self, *, reason: Reason, page_count: int = 0, megapixels: float = 0.0, page: int = 0
    ) -> None:
        """Create the failure. No text in it: the file and the child that reads it are untrusted."""
        super().__init__(reason)
        self.reason: Reason = reason
        self.page_count: int = page_count
        self.megapixels: float = megapixels
        self.page: int = page


class PageRenderer(Protocol):
    """Renders pages of one file."""

    async def render(self, content: bytes, request: RenderRequest) -> Rendered:
        """The pages ``request`` asks for, as far as they fit its limits.

        Raises:
            RenderFailed: The file or the page specification cannot be used, or a limit was hit.
        """
        ...

    async def inspect(self, content: bytes, request: OverviewRequest) -> Overview:
        """Sizes, rotation and contact sheets of the pages of a PDF, as far as they fit.

        Raises:
            RenderFailed: See ``render``.
        """
        ...


class ShownPage(BaseModel):
    """One page among the images of a result."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    page: int
    width: int
    height: int
    size_bytes: int
    reduced: bool


class PagesReport(BaseModel):
    """What a call of get_document_pages shows, and how to go on.

    Attributes:
        not_shown: The pages asked for that did not fit, in words.
        next_pages: The page specification that asks for them.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    version_id: int
    source: Literal["original", "archive"]
    filename: str | None
    mime_type: str
    page_count: int
    region: Region | None
    images: tuple[ShownPage, ...]
    not_shown: str | None
    next_pages: str | None
    web_url: str
    note: str


class LayoutRun(BaseModel):
    """Pages after one another of the same size and rotation.

    Attributes:
        pages: Their page specification.
        rotation: As stored in the file; null with the size if the pages cannot be read.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    pages: str
    width_mm: float | None
    height_mm: float | None
    rotation: int | None


class ShownSheet(BaseModel):
    """One contact sheet among the images of a result: the page of each tile, row by row."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    pages: tuple[int, ...]
    width: int
    height: int
    size_bytes: int
    reduced: bool


class InspectionReport(BaseModel):
    """What a call of inspect_pdf found, and how to go on.

    Attributes:
        layout_rest: The page specification of the pages the layout does not cover any more.
        next_pages: The same for the sheets.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    document_id: int
    version_id: int
    source: Literal["original", "archive"]
    filename: str | None
    page_count: int
    layout: tuple[LayoutRun, ...]
    layout_rest: str | None
    sheets: tuple[ShownSheet, ...]
    next_pages: str | None
    web_url: str
    note: str


class Inspection(BaseModel):
    """The report and the sheets it lists, in the same order."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    report: InspectionReport
    sheets: tuple[ContactSheet, ...]


class DocumentPages(BaseModel):
    """The report and the images it lists, in the same order."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    report: PagesReport
    images: tuple[PageImage, ...]
