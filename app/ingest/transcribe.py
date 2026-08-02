"""Turn each page into plain text — the "photocopy" the rest of the pipeline reads.

Two paths, chosen by triage:

    digital  -> reuse the embedded text layer. No model call, no cost.
    scanned  -> render the page and have a vision model read it.

This step is deliberately *faithful*: it writes down what the page says and
nothing more. No normalising units, no expanding abbreviations, no filling in
gaps. Interpretation happens later, in extract.py, against the text produced
here. Keeping the two apart means the transcription can be cached and reused
when the extraction schema changes.

The vision call also reports whether the page is handwritten, which is the only
place that can be known — triage cannot tell a photographed handwritten note
from a photographed printed one.
"""

import asyncio
import base64
from dataclasses import dataclass

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import MAX_CONCURRENT_VISION, PAGE_RENDER_DPI, VISION_MODEL
from app.ingest.pages import Page
from app.ingest.triage import TriageResult


class TranscriptionError(Exception):
    """The model could not read a page. Fails one page, not the document."""


SYSTEM_PROMPT = """You transcribe medical documents exactly as they appear.

Rules:
- Reproduce the page verbatim. Do not summarise, correct, reorder or interpret.
- Preserve tables as markdown tables, keeping every column: test name, result,
  unit, reference range and flag.
- Keep every table row on a single line. Never put a line break inside a cell;
  use <br> or a space instead. A broken row misaligns the columns and attaches
  a dose to the wrong drug.
- Keep numbers, units and dates exactly as printed, including leading zeros and
  symbols such as < or >.
- Do not expand abbreviations. Prescription shorthand such as 1-0-1, BD, TDS,
  OD, mane, nocte, PRN and SOS must appear exactly as written.
- If something cannot be read with confidence, write [illegible] in its place.
  Never guess at a drug name, a dose or a number.
- Transcribe struck-through text as ~~struck through~~ so that a cancelled
  drug is not mistaken for an active one.
- Where the original document itself has text blacked out or masked, write
  [redacted]. Transcribe everything that is legible.
- Charts and graphs carry clinical data and must not be skipped. For each one,
  give its title, axis labels and series names, then transcribe its printed
  data point values as a markdown table with one row per point, including the
  date or category each value sits above. If a value is not printed on the
  chart, write [value not printed] - never estimate it from the position of a
  line or bar.
- Never invent a link, a URL or a file path. Describe a picture as
  [image: short description] and transcribe any words inside it.
- Include headers, footers, patient details, clinician names, stamps and
  signatures.
"""


class PageTranscription(BaseModel):
    """What the vision model returns for one page."""

    markdown: str = Field(description="The page transcribed verbatim, tables as markdown.")
    handwritten: bool = Field(
        description="True if any clinical content on the page is handwritten."
    )
    language: str = Field(
        description="Language code(s) of the text, e.g. 'en', 'ta', 'si', 'en+ta'."
    )
    fully_legible: bool = Field(
        description="False if any part had to be marked [illegible]."
    )


@dataclass
class TranscribedPage:
    page_no: int
    transcription: str
    modality: str  # digital | scanned | handwritten
    language: str | None
    fully_legible: bool
    model: str | None  # None when read straight from the text layer


_vision_model = None
_semaphore: asyncio.Semaphore | None = None


def _get_vision_model():
    """Built lazily so importing this module needs no API key."""
    global _vision_model
    if _vision_model is None:
        _vision_model = init_chat_model(VISION_MODEL, temperature=0).with_structured_output(
            PageTranscription
        )
    return _vision_model


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_VISION)
    return _semaphore


def _from_text_layer(page: Page) -> str:
    """Assemble markdown for a digital page without calling a model."""
    parts = [page.text.strip()]
    # find_tables() output is appended rather than substituted: the text layer
    # already contains the cell values, but the markdown version preserves which
    # value belongs to which column.
    parts.extend(table.strip() for table in page.tables_md if table.strip())
    return "\n\n".join(part for part in parts if part)


async def _from_vision(page: Page, dpi: int) -> tuple[str, PageTranscription]:
    """Render the page and have the vision model read it."""
    image_b64 = base64.b64encode(page.image_png(dpi=dpi)).decode()
    message = HumanMessage(
        content=[
            {"type": "text", "text": f"Transcribe page {page.page_no} of this document."},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
            },
        ]
    )

    async with _get_semaphore():
        try:
            result = await _get_vision_model().ainvoke(
                [SystemMessage(content=SYSTEM_PROMPT), message]
            )
        except Exception as exc:  # noqa: BLE001 - provider errors vary widely
            raise TranscriptionError(f"Page {page.page_no} could not be read: {exc}") from exc

    return VISION_MODEL, result


async def transcribe_page(
    page: Page, triage: TriageResult, dpi: int = PAGE_RENDER_DPI
) -> TranscribedPage:
    """Transcribe one page, using the text layer when triage says it is usable."""
    if triage.is_blank:
        return TranscribedPage(
            page_no=page.page_no,
            transcription="",
            modality="digital",
            language=None,
            fully_legible=True,
            model=None,
        )

    if not triage.needs_vision:
        return TranscribedPage(
            page_no=page.page_no,
            transcription=_from_text_layer(page),
            modality="digital",
            language=None,
            fully_legible=True,
            model=None,
        )

    model_name, result = await _from_vision(page, dpi)
    return TranscribedPage(
        page_no=page.page_no,
        transcription=result.markdown,
        # Upgrade scanned -> handwritten: this is the first step that has
        # actually looked at the page, and the distinction drives the
        # confidence prior applied to everything extracted from it.
        modality="handwritten" if result.handwritten else "scanned",
        language=result.language,
        fully_legible=result.fully_legible,
        model=model_name,
    )


async def transcribe_document(
    pages: list[Page], triages: list[TriageResult], dpi: int = PAGE_RENDER_DPI
) -> list[TranscribedPage | TranscriptionError]:
    """Transcribe every page concurrently.

    Returns results in page order. A page that fails comes back as its
    exception rather than raising, so one unreadable page does not lose the
    transcription of the rest.
    """
    results = await asyncio.gather(
        *(transcribe_page(page, triage, dpi) for page, triage in zip(pages, triages)),
        return_exceptions=True,
    )
    return list(results)
