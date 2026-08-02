"""Decide how each page has to be read.

`pages.py` measures; this module decides. It answers one question per page:
can we trust the embedded text layer, or does a vision model have to look at
the picture?

    digital  -> the text layer is usable; no vision call needed
    scanned  -> nothing usable in the file; render it and read the image

`handwritten` is a third modality in the database, but it cannot be detected
from these signals — a photographed handwritten note and a photographed
printed one look identical to a PDF parser. Triage sends both down the vision
path as `scanned`, and `transcribe.py` upgrades the modality once the model
has actually looked at the page.
"""

from dataclasses import dataclass

from app.ingest.pages import Page

# Below this, a text layer is too thin to be the page's real content.
MIN_TEXT_CHARS = 40

# Share of visible characters that may be non-alphanumeric before we treat the
# text layer as junk. Clean prose and lab tables sit around 0.05-0.15; scanner
# OCR gone wrong is punctuation soup well above this.
JUNK_NON_ALNUM_RATIO = 0.35

# Below this much image coverage, there is no picture worth looking at, so
# whatever the text layer holds is all the page has.
IMAGE_PRESENT_RATIO = 0.10

Modality = str  # 'digital' | 'scanned' | 'handwritten'


@dataclass
class TriageResult:
    page_no: int
    modality: Modality
    needs_vision: bool
    is_blank: bool
    reason: str  # why this call was made — kept for debugging and demos


def triage_page(page: Page) -> TriageResult:
    """Classify one page from the signals pages.py reported."""
    has_text = page.char_count > 0
    has_image = page.image_area_ratio >= IMAGE_PRESENT_RATIO

    # A scanner that embedded its own bad OCR gives a page a text layer that
    # looks substantial but is unusable. Caught before the length check,
    # because such pages are usually long.
    if has_text and page.non_alnum_ratio >= JUNK_NON_ALNUM_RATIO:
        return TriageResult(
            page_no=page.page_no,
            modality="scanned",
            needs_vision=True,
            is_blank=False,
            reason=f"text layer looks like junk OCR "
            f"(non-alphanumeric ratio {page.non_alnum_ratio:.2f})",
        )

    if page.char_count >= MIN_TEXT_CHARS:
        return TriageResult(
            page_no=page.page_no,
            modality="digital",
            needs_vision=False,
            is_blank=False,
            reason=f"usable text layer ({page.char_count} characters)",
        )

    # Thin text layer and no picture: there is nothing for a vision model to
    # find that the text layer missed, so don't spend a call on it.
    if not has_image:
        return TriageResult(
            page_no=page.page_no,
            modality="digital",
            needs_vision=False,
            is_blank=not has_text,
            reason="blank page" if not has_text else "sparse page with no image content",
        )

    return TriageResult(
        page_no=page.page_no,
        modality="scanned",
        needs_vision=True,
        is_blank=False,
        reason=f"page is {page.image_area_ratio:.0%} image with "
        f"{page.char_count} characters of text",
    )


def triage_document(pages: list[Page]) -> list[TriageResult]:
    """Classify every page of a document."""
    return [triage_page(page) for page in pages]
