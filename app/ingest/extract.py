"""Turn transcribed text into filled-in forms.

The second pass. Transcribe wrote down what the page says; this reads that text
and fills in the schema from app/schemas/extraction.py, choosing the form from
the type classify assigned. It is the first step whose output is *data* rather
than text — drugs, doses, results, dates — and everything downstream (the
timeline, the cross-checks, the trends) is built on it.

Works from the cached transcription, never the image. Re-running extraction
after a schema change then costs one text call instead of a vision call, which
is what makes the schema safe to iterate on. The price is that anything the
transcription got wrong is inherited here and cannot be checked against the
page.

Extraction runs per *document within the upload*, not per page. A results table
split across two pages must be read as one thing — the second page's rows have
no column headers of their own — so a page and its continuation pages are
joined and sent together. One scanned folder can therefore produce several
extractions from a single upload.

Nothing here is verified, normalised or judged: source_span is checked by
ground.py, units and dates are parsed by normalize.py, and review.py decides
what a human should look at. This step's only job is to read the text, fill the
form, and admit uncertainty honestly.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import EXTRACT_MODEL, MAX_CONCURRENT_EXTRACT, MODEL_RETRIES, MODEL_TIMEOUT
from app.ingest.classify import PageClassification
from app.schemas.extraction import _BaseExtraction, schema_for

logger = logging.getLogger(__name__)

# Below this a group has nothing worth a model call.
MIN_EXTRACTABLE_CHARS = 25

# A group longer than this is sent in several calls and the results merged.
# Generous on purpose: splitting is a last resort, because an item that
# straddles the split can be lost.
MAX_GROUP_CHARS = 24_000


class ExtractionError(Exception):
    """One group could not be extracted. Fails that group, not the document."""


SYSTEM_PROMPT = """You read medical documents and record what they say.

You are given the transcribed text of one document and a form to fill in.

Rules:
- Record only what is written in the text. Never add a fact from your own
  medical knowledge — not a drug class, not a diagnostic code, not a normal
  range the document does not print.
- Copy values exactly as written. Keep '500mg' as '500mg', keep '<0.01' and
  '>200' and 'Positive' as they are, keep dates in the document's own format.
  Do not convert units, expand abbreviations, or reformat anything.
- Keep prescription shorthand as written: 1-0-1, BD, TDS, OD, mane, nocte,
  PRN, SOS.
- For every item, source_span must be text copied character for character from
  the document. If you cannot quote it, do not record the item.
- Where the text says [illegible], leave that field null and set legible to
  false. Never guess a drug name, a dose or a number.
- Text marked ~~struck through~~ was cancelled. Do not record it.
- Text marked [redacted] was masked on the original document. Leave the field
  null rather than inventing a value.
- Free-text directions written in another language — Gujarati, Hindi, Tamil,
  Sinhala, Urdu — are recorded as their English meaning followed by the
  original in brackets: 'on an empty stomach (ભૂખ્યા પેટે)'. This is the one
  place the rules above bend, and only for wording. Translating is not adding
  a fact: it is the same direction the document gave, in a language its reader
  can act on, and a direction nobody can read is a direction nobody follows.
  Never translate a drug name, a number, a unit or a dose — those are copied
  exactly, like everything else. If you are unsure of the meaning, keep the
  original alone rather than guessing at it.
- Set confidence honestly per item: high for clearly printed text, low where
  the transcription is uncertain or the layout makes it unclear which value
  belongs to which label.
- Record every item the document lists, including normal lab results.
- Anything clinically meaningful that no field fits goes into unmapped as a
  label and value, rather than being dropped.
"""


@dataclass
class PageGroup:
    """Pages that belong to one document, read together."""

    pages: list[int]
    doc_type: str
    text: str

    @property
    def page_span(self) -> str:
        return f"page {self.pages[0]}" if len(self.pages) == 1 else (
            f"pages {self.pages[0]}-{self.pages[-1]}"
        )


@dataclass
class ExtractedGroup:
    pages: list[int]
    doc_type: str
    schema_name: str
    data: _BaseExtraction
    model: str
    parts: int = 1  # how many calls were merged; >1 means the group was split

    def to_json(self) -> dict[str, Any]:
        """The record kept in document.extraction_json — never edited later."""
        return {
            "pages": self.pages,
            "doc_type": self.doc_type,
            "schema": self.schema_name,
            "model": self.model,
            "parts": self.parts,
            "data": self.data.model_dump(),
        }


# --- grouping ----------------------------------------------------------------


# Types that are one long document rather than several short ones stacked up.
#
# A 19-page lab report repeats its letterhead on every page, so classify gives
# each page a real type instead of 'continuation' and the grouping below splits
# it into nineteen. Measured: 18 separate model calls, 37 of the upload's 42
# seconds. Rejoining them is a straight win — one call instead of eighteen, and
# the model sees the reference ranges from the header alongside the values on
# later pages.
#
# Prescriptions are deliberately absent, and so is doctor_note. Those are the
# short documents people scan back to back, and merging two of them would give
# one header, one date, and two visits collapsed into one. Over-splitting costs
# time; under-splitting loses a visit. Only the first is worth trading.
MERGEABLE_TYPES = frozenset(
    {
        "lab_report",
        "discharge_summary",
        "radiology_report",
        "pathology_report",
        "operative_note",
    }
)


def group_pages(
    classifications: Sequence[PageClassification],
    transcriptions: dict[int, str],
) -> list[PageGroup]:
    """Join each page with the continuation pages that follow it.

    A page with a type of its own starts a new group; a 'continuation' page is
    appended to the open one. Two prescriptions scanned back to back therefore
    stay two groups, which is right — they are two documents.

    Then neighbouring pages of a long-form type are rejoined, up to the size one
    call can take. See MERGEABLE_TYPES for why that is safe for a lab report and
    not for a prescription.
    """
    groups: list[PageGroup] = []

    for item in classifications:
        text = (transcriptions.get(item.page_no) or "").strip()
        if not text:
            continue

        if item.doc_type == "continuation" and groups:
            groups[-1].pages.append(item.page_no)
            groups[-1].text += "\n\n" + text
            continue

        groups.append(
            PageGroup(pages=[item.page_no], doc_type=item.resolved_type, text=text)
        )

    groups = _rejoin(groups)
    return [g for g in groups if len(g.text) >= MIN_EXTRACTABLE_CHARS]


def _rejoin(groups: list[PageGroup]) -> list[PageGroup]:
    """Merge neighbouring groups that are really one document.

    Three conditions, all required: the same long-form type, pages that actually
    run on from one another, and a combined size one call can still take. The
    size ceiling is the same MAX_GROUP_CHARS that `_split` would otherwise undo
    a moment later.
    """
    merged: list[PageGroup] = []

    for group in groups:
        last = merged[-1] if merged else None
        joinable = (
            last is not None
            and group.doc_type == last.doc_type
            and group.doc_type in MERGEABLE_TYPES
            and group.pages[0] == last.pages[-1] + 1
            and len(last.text) + len(group.text) + 2 <= MAX_GROUP_CHARS
        )
        if joinable:
            last.pages.extend(group.pages)
            last.text += "\n\n" + group.text
            continue
        merged.append(group)

    return merged


def _split(text: str, limit: int = MAX_GROUP_CHARS) -> list[str]:
    """Cut an oversized group at paragraph breaks, as few times as possible."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = limit  # one unbroken block; nothing better than a hard cut
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


# --- merging -----------------------------------------------------------------

_EMPTY = (None, "", {}, [])


def _merge(parts: list[_BaseExtraction]) -> _BaseExtraction:
    """Combine the extractions of a split group back into one.

    Lists join end to end — every part sees different items. Single values and
    header fields take the first non-empty answer, because the header is
    normally on the first part and later parts leave it null.
    """
    schema = type(parts[0])
    merged: dict[str, Any] = parts[0].model_dump()

    for part in parts[1:]:
        for key, value in part.model_dump().items():
            current = merged.get(key)
            if isinstance(value, list):
                merged[key] = (current or []) + value
            elif isinstance(value, dict):
                base = dict(current or {})
                for inner_key, inner_value in value.items():
                    if base.get(inner_key) in _EMPTY and inner_value not in _EMPTY:
                        base[inner_key] = inner_value
                merged[key] = base
            elif current in _EMPTY and value not in _EMPTY:
                merged[key] = value

    return schema.model_validate(merged)


# --- model -------------------------------------------------------------------

_models: dict[str, Any] = {}
_semaphore: asyncio.Semaphore | None = None


def _get_model(schema: type[_BaseExtraction]):
    """One client per form, built lazily so importing needs no API key.

    `method="function_calling"` rather than the default strict JSON schema:
    strict mode demands that every property be required, and these forms are
    optional by design — a prescription with no duration must still extract.
    Pydantic validates the result either way.
    """
    key = schema.__name__
    if key not in _models:
        _models[key] = init_chat_model(
            EXTRACT_MODEL,
            temperature=0,
            timeout=MODEL_TIMEOUT,
            max_retries=MODEL_RETRIES,
        ).with_structured_output(
            schema, method="function_calling"
        )
    return _models[key]


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXTRACT)
    return _semaphore


async def _fill(schema: type[_BaseExtraction], text: str, group: PageGroup) -> _BaseExtraction:
    prompt = (
        f"This document is a {group.doc_type.replace('_', ' ')} "
        f"({group.page_span}). Fill in the form from its text:\n\n{text}"
    )
    async with _get_semaphore():
        try:
            return await _get_model(schema).ainvoke(
                [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
            )
        except Exception as exc:  # noqa: BLE001 - provider errors vary widely
            raise ExtractionError(f"{group.page_span} could not be extracted: {exc}") from exc


async def extract_group(group: PageGroup) -> ExtractedGroup:
    """Fill one form from one group of pages."""
    schema = schema_for(group.doc_type)
    chunks = _split(group.text)

    if len(chunks) > 1:
        logger.info("%s is %d chars; extracting in %d parts", group.page_span, len(group.text), len(chunks))

    filled = [await _fill(schema, chunk, group) for chunk in chunks]

    return ExtractedGroup(
        pages=list(group.pages),
        doc_type=group.doc_type,
        schema_name=schema.__name__,
        data=filled[0] if len(filled) == 1 else _merge(filled),
        model=EXTRACT_MODEL,
        parts=len(filled),
    )


async def extract_document(
    groups: Sequence[PageGroup],
) -> list[ExtractedGroup | ExtractionError]:
    """Extract every group concurrently.

    Results come back in group order. A group that fails comes back as its
    exception rather than raising, so one unreadable prescription does not lose
    the lab report scanned behind it.
    """
    results = await asyncio.gather(
        *(extract_group(group) for group in groups), return_exceptions=True
    )
    return list(results)
