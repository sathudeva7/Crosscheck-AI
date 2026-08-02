"""Check that every extracted item was really read off the page.

Extraction reports its own confidence, and a model that misreads a dose reports
0.99 as cheerfully as one that reads it correctly. Self-assessment is a claim,
not evidence. This module is the independent check.

Every extracted item carries a `source_span` — the text the model says it read
the item from. Here we go looking for that text in the transcription. Found
means the model quoted the document; not found means it wrote down something
the document never said, which is the one failure a medical tool cannot ship.

Two things come out of it: a score that review.py folds into the confidence
actually shown to the user, and the page number the item was found on, which is
what lets the timeline cite its source and the UI link back to the document.

What it cannot do: it compares the extraction against the *transcription*, not
against the paper. If transcribe misread '10 mg' as '100 mg', the span matches
perfectly and nothing here notices. It catches invention, not misreading — the
review queue exists for the rest.

No model call, no cost, no network. Just string matching.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from pydantic import BaseModel

from app.schemas.extraction import Grounded

logger = logging.getLogger(__name__)

# Below this, a fuzzy candidate is a different piece of text, not a tidied
# version of the same one.
FUZZY_MIN_RATIO = 0.75

# Spans shorter than this prove very little — '1.8' appears all over a lab
# report, so finding it is not evidence the item was read from the right row.
MIN_SPAN_CHARS = 8
SHORT_SPAN_PENALTY = 0.7

# A quoted span may run across a line break in the transcription, so candidates
# are built from runs of consecutive lines up to this length.
MAX_WINDOW_LINES = 3

_MATCH_SCORES = {
    "exact": 1.0,
    "normalized": 0.95,
    # fuzzy is scored from its ratio; the others are fixed
    "not_found": 0.0,
    "empty": 0.0,
}


@dataclass
class ItemGrounding:
    """The verdict on one extracted item."""

    path: str  # where the item sits, e.g. 'medications[0]'
    source_span: str
    match: str  # exact | normalized | fuzzy | not_found | empty
    score: float
    page_no: int | None
    matched_text: str | None = None

    @property
    def grounded(self) -> bool:
        return self.match not in ("not_found", "empty")

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "match": self.match,
            "score": round(self.score, 3),
            "page_no": self.page_no,
            "source_span": self.source_span,
        }


# --- text normalisation ------------------------------------------------------

_MARKUP = re.compile(r"<br\s*/?>|~~|\*\*|\*|`|\||_{2,}")
# The vision model escapes markdown punctuation, writing '\(Before Food\)'.
# The extracting model quotes it back unescaped, so the two never match.
_ESCAPES = re.compile(r"\\([^\w\s])")
_DASHES = re.compile(r"[‐-―−]")
_QUOTES = re.compile(r"[‘’“”]")
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Strip away everything that differs without changing the meaning.

    The transcription writes a lab row as a markdown table:

        | Creatinine | 1.8 | mg/dL | 0.7 - 1.3 | H |

    and the model quotes it back as plain text:

        Creatinine 1.8 mg/dL 0.7 - 1.3 H

    Same content, different punctuation. Without this every table row in the
    document would look like a hallucination.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _DASHES.sub("-", text)
    text = _QUOTES.sub("'", text)
    text = _ESCAPES.sub(r"\1", text)
    text = _MARKUP.sub(" ", text)
    return _SPACE.sub(" ", text).strip().lower()


@dataclass
class _PreparedPage:
    """A page's text in the forms matching needs, computed once per page."""

    page_no: int
    raw: str
    norm: str
    windows: list[str]


def _prepare(page_no: int, text: str) -> _PreparedPage:
    lines = [normalize(line) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]

    windows: list[str] = []
    for size in range(1, MAX_WINDOW_LINES + 1):
        windows.extend(
            " ".join(lines[i : i + size]) for i in range(len(lines) - size + 1)
        )

    return _PreparedPage(page_no=page_no, raw=text or "", norm=normalize(text or ""), windows=windows)


def prepare_pages(transcriptions: dict[int, str]) -> list[_PreparedPage]:
    """Prepare every page of a group once, before grounding its items."""
    return [_prepare(page_no, transcriptions[page_no]) for page_no in sorted(transcriptions)]


# --- matching ----------------------------------------------------------------


@lru_cache(maxsize=4096)
def _ratio(span: str, candidate: str) -> float:
    return SequenceMatcher(None, span, candidate).ratio()


def _best_fuzzy(span_norm: str, pages: list[_PreparedPage]) -> tuple[float, int, str] | None:
    """The closest line, or run of lines, to the span across all pages."""
    best: tuple[float, int, str] | None = None
    for page in pages:
        for window in page.windows:
            # Length filter first: comparing a 20-char span with a 400-char
            # window can never beat the threshold, and the ratio is the
            # expensive part.
            if not (0.5 <= len(window) / max(len(span_norm), 1) <= 2.0):
                continue
            ratio = _ratio(span_norm, window)
            if best is None or ratio > best[0]:
                best = (ratio, page.page_no, window)
    return best


def ground_span(span: str, pages: list[_PreparedPage], path: str = "") -> ItemGrounding:
    """Look for one span in the pages of its own document."""
    span = (span or "").strip()
    if not span:
        return ItemGrounding(path, "", "empty", 0.0, None)

    span_norm = normalize(span)

    for page in pages:
        if span in page.raw:
            return _scored(path, span, "exact", _MATCH_SCORES["exact"], page.page_no, span)

    for page in pages:
        if span_norm and span_norm in page.norm:
            return _scored(
                path, span, "normalized", _MATCH_SCORES["normalized"], page.page_no, span_norm
            )

    best = _best_fuzzy(span_norm, pages)
    if best is not None and best[0] >= FUZZY_MIN_RATIO:
        ratio, page_no, window = best
        # 0.75 -> 0.5, 1.0 -> 0.9. Never as good as a real match, because a
        # near-match means the model altered the text it claims to have copied.
        score = 0.5 + (ratio - FUZZY_MIN_RATIO) / (1 - FUZZY_MIN_RATIO) * 0.4
        return _scored(path, span, "fuzzy", score, page_no, window)

    return ItemGrounding(path, span, "not_found", 0.0, None)


def _scored(
    path: str, span: str, match: str, score: float, page_no: int, matched: str
) -> ItemGrounding:
    if len(span) < MIN_SPAN_CHARS:
        score *= SHORT_SPAN_PENALTY
    return ItemGrounding(path, span, match, score, page_no, matched)


# --- walking an extraction ---------------------------------------------------


def _walk(value: Any, path: str, pages: list[_PreparedPage], out: list[ItemGrounding]) -> None:
    """Ground every Grounded item anywhere in the extraction."""
    if isinstance(value, Grounded):
        out.append(ground_span(value.source_span, pages, path))
        return

    if isinstance(value, BaseModel):
        for name, child in value:
            _walk(child, f"{path}.{name}" if path else name, pages, out)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk(child, f"{path}[{index}]", pages, out)


def ground_extraction(
    data: BaseModel, transcriptions: dict[int, str]
) -> list[ItemGrounding]:
    """Ground every item of one filled-in form against its own pages."""
    pages = prepare_pages(transcriptions)
    out: list[ItemGrounding] = []
    _walk(data, "", pages, out)
    return out


def summarise(groundings: list[ItemGrounding]) -> dict[str, Any]:
    """Counts for logging and for the document's stored record."""
    counts: dict[str, int] = {}
    for item in groundings:
        counts[item.match] = counts.get(item.match, 0) + 1
    total = len(groundings)
    grounded = sum(1 for item in groundings if item.grounded)
    return {
        "items": total,
        "grounded": grounded,
        "ungrounded": total - grounded,
        "by_match": counts,
        "mean_score": round(sum(i.score for i in groundings) / total, 3) if total else 0.0,
    }
