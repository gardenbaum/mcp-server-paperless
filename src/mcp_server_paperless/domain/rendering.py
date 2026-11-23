"""Pages of a document as images: what is asked of the renderer and what it answers.

The renderer is not a port: it talks to a child process, not to Paperless.
"""

from typing import ClassVar, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict

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
# The long side of an image: 2576 pixels is the most Claude reads without scaling down, 1568 the
# most of its standard tier.
MIN_SIZE: Final = 256
MAX_SIZE: Final = 2576
DEFAULT_SIZE: Final = 1568

# Left, top, right and bottom edge, as fractions of the page as shown.
type Region = tuple[float, float, float, float]
type Reason = Literal["encrypted", "damaged", "pixels", "pages", "timeout", "memory", "crashed"]


class RenderRequest(BaseModel):
    """Which pages to render, and how.

    Attributes:
        mime_type: One of ``RENDERABLE``; it decides how the file is read.
        pages: Page specification (see ``services.page_ranges``).
        size: Pixels of the long side of each image.
        region: The part of each page to show, else the whole page.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

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


class RenderFailed(Exception):  # noqa: N818 -- an outcome, as the names of domain/errors.py
    """The file could not be rendered.

    Attributes:
        reason: Why; the caller turns it into a message.
        page_count: How many pages the file has, if the page specification is wrong.
        megapixels: How large the image is, if it is too large.
    """

    def __init__(self, *, reason: Reason, page_count: int = 0, megapixels: float = 0.0) -> None:
        """Create the failure. No text in it: the file and the child that reads it are untrusted."""
        super().__init__(reason)
        self.reason: Reason = reason
        self.page_count: int = page_count
        self.megapixels: float = megapixels


class PageRenderer(Protocol):
    """Renders pages of one file."""

    async def render(self, content: bytes, request: RenderRequest) -> Rendered:
        """The pages ``request`` asks for, as far as they fit its limits.

        Raises:
            RenderFailed: The file or the page specification cannot be used, or a limit was hit.
        """
        ...
