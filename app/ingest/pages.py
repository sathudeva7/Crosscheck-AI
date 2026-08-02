"""Turn an uploaded file into pages.

One job: given PDF or image bytes, produce a uniform list of pages carrying
the text layer, any tables, and — on demand — a rendered image. Pure PyMuPDF;
no model calls, no network, no database.

Images become single-page documents with an empty text layer, so a photo of a
prescription and a scanned PDF flow through identical code downstream.

This module *measures*; it does not decide. Whether a page is digital, scanned
or handwritten is triage.py's call, made from the signals reported here.

    with open_document(data, "application/pdf") as doc:
        for page in doc.pages:
            print(page.page_no, page.char_count)
            png = page.image_png()      # rendered only if asked for

Rendering is lazy, which is why this is a context manager: the underlying
MuPDF document must stay open. Pages are unusable once the block exits.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import pymupdf

from app.config import MAX_PAGES, PAGE_RENDER_DPI

_FILETYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
}


class DocumentError(Exception):
    """Base for anything that makes a file unreadable."""


class UnsupportedFileType(DocumentError):
    pass


class PasswordProtected(DocumentError):
    pass


class UnreadableDocument(DocumentError):
    """Corrupt, truncated, or not the format it claims to be."""


class TooManyPages(DocumentError):
    pass


@dataclass
class Page:
    """A single page and the signals triage needs to route it."""

    page_no: int  # 1-based
    text: str  # text layer in reading order; "" for scans
    tables_md: list[str]  # detected tables, as markdown
    width: int
    height: int

    # --- triage signals ---
    char_count: int
    non_alnum_ratio: float  # high => the text layer is junk OCR
    image_area_ratio: float  # ~1.0 => the page is one big picture

    _render: Callable[[int], bytes] = field(repr=False, compare=False)
    _rendered: dict[int, bytes] = field(default_factory=dict, repr=False, compare=False)

    def image_png(self, dpi: int = PAGE_RENDER_DPI) -> bytes:
        """Render this page to PNG, caching per DPI.

        Only valid inside the `open_document` block that produced it.
        """
        if dpi not in self._rendered:
            self._rendered[dpi] = self._render(dpi)
        return self._rendered[dpi]


@dataclass
class LoadedDocument:
    page_count: int
    pages: list[Page]


def _non_alnum_ratio(text: str) -> float:
    """Share of visible characters that are neither letters nor digits.

    Scanners sometimes embed their own bad OCR, which gives a page a text layer
    that looks substantial but is mostly punctuation soup. Character count
    alone would route such a page down the digital path and extract nonsense.
    """
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return 0.0
    return sum(1 for c in visible if not c.isalnum()) / len(visible)


def _image_area_ratio(page: pymupdf.Page) -> float:
    """Share of the page covered by embedded images, capped at 1.0."""
    page_area = abs(page.rect.get_area())
    if not page_area:
        return 0.0
    try:
        covered = sum(abs(pymupdf.Rect(info["bbox"]).get_area()) for info in page.get_image_info())
    except Exception:  # noqa: BLE001 - malformed image dicts should not fail a page
        return 0.0
    return min(covered / page_area, 1.0)


def _tables_markdown(page: pymupdf.Page) -> list[str]:
    """Detected tables as markdown. Best effort — never fails a page."""
    try:
        return [table.to_markdown() for table in page.find_tables().tables]
    except Exception:  # noqa: BLE001
        return []


def _build_page(page: pymupdf.Page, page_no: int) -> Page:
    # sort=True gives reading order rather than internal draw order, which
    # matters on multi-column layouts
    text = page.get_text("text", sort=True)

    def render(dpi: int) -> bytes:
        # alpha=False keeps PNGs smaller; get_pixmap applies page rotation, so
        # sideways scans come out upright
        return page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")

    return Page(
        page_no=page_no,
        text=text,
        tables_md=_tables_markdown(page),
        width=int(page.rect.width),
        height=int(page.rect.height),
        char_count=len(text.strip()),
        non_alnum_ratio=_non_alnum_ratio(text),
        image_area_ratio=_image_area_ratio(page),
        _render=render,
    )


@contextmanager
def open_document(data: bytes, mime_type: str) -> Iterator[LoadedDocument]:
    """Open uploaded bytes and yield its pages.

    Raises a DocumentError subclass for anything a user could act on, so the
    pipeline can record a useful message instead of a stack trace.
    """
    filetype = _FILETYPES.get(mime_type)
    if filetype is None:
        raise UnsupportedFileType(f"Cannot open {mime_type!r}")

    try:
        doc = pymupdf.open(stream=data, filetype=filetype)
    except Exception as exc:  # noqa: BLE001 - pymupdf raises several types here
        raise UnreadableDocument(f"File could not be opened: {exc}") from exc

    try:
        if doc.needs_pass:
            raise PasswordProtected("This document is password protected")
        if doc.page_count > MAX_PAGES:
            raise TooManyPages(
                f"Document has {doc.page_count} pages; the limit is {MAX_PAGES}"
            )

        yield LoadedDocument(
            page_count=doc.page_count,
            pages=[_build_page(page, i + 1) for i, page in enumerate(doc)],
        )
    finally:
        doc.close()
