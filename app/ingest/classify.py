"""Decide what kind of document each page is.

Runs after transcription, so it reads text rather than pictures — the vision
call has already been paid for. Its only customer is extract.py, which uses the
answer to pick the form the model fills in. A prescription read with the lab
report form has nowhere to put a frequency, so this choice decides what gets
kept and what gets silently dropped.

Per page rather than per document, because one upload is often a whole folder
scanned in one go: prescription, then lab report, then discharge summary. The
page after a lab report's first page is usually the rest of its results table
with no header of its own — that is `continuation`, and it inherits the type of
the page before it.

Two paths, cheapest first:

    markers -> distinctive phrases settle the page. No model call.
    model   -> everything else, on the transcribed text only.

Pages are classified in order because each one may need to know what the
previous page was. In practice the markers resolve most pages instantly, so the
sequential model calls are few.

Never raises. A page it cannot place becomes 'unknown', which extract.py maps
to the generic form — fewer specialised fields, but the document is not lost.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import CLASSIFY_MODEL
from app.db.models import PAGE_DOC_TYPES

logger = logging.getLogger(__name__)

# A page with less than this much text has nothing to classify from.
MIN_CLASSIFIABLE_CHARS = 25

# Only the top of a page carries its title, so a marker found there counts for
# more. A discharge summary quoting lab results mentions 'reference range' in
# its body; only a lab report says it near the top.
HEAD_CHARS = 400
HEAD_WEIGHT = 2

# Marker verdicts are accepted only when the winner is clear: enough evidence
# in absolute terms, and well ahead of the runner-up. A discharge summary is
# full of medication and lab phrases, so a narrow win means nothing.
MIN_MARKER_SCORE = 4
MIN_MARKER_MARGIN = 3

# Below this the model is guessing. Treated as 'unknown' so extract.py falls
# back to the generic form rather than trusting a bad type.
MIN_MODEL_CONFIDENCE = 0.5

# Only the text is sent, and only this much of it — a page's identity is
# obvious long before its last line.
MAX_PROMPT_CHARS = 3000

# How much of the previous page's ending the model sees. Without it, it can
# only be told the previous page's *type*, which is not enough to judge whether
# this page genuinely runs on from it — and it then calls anything untitled a
# continuation.
PREVIOUS_TAIL_CHARS = 600


@dataclass
class PageClassification:
    page_no: int
    doc_type: str  # one of PAGE_DOC_TYPES; may be 'continuation'
    resolved_type: str  # what extract.py should use — continuation resolved
    confidence: float
    reason: str  # why this call was made — kept for debugging and demos
    method: str  # markers | model | blank | cached


# --- marker path -------------------------------------------------------------

# Phrases that point at one document type, with how much each is worth. Weights
# are deliberately lopsided: a title phrase such as 'discharge summary' settles
# a page on its own, while 'tablet' is only a hint.
_MARKERS: dict[str, dict[str, int]] = {
    "prescription": {
        "prescription": 4,
        "rx": 3,
        "sig": 2,
        "refill": 3,
        "dispense": 3,
        "take one": 2,
        "before food": 2,
        "after food": 2,
        "tab": 1,
        "cap": 1,
        "syrup": 1,
        "tds": 2,
        "bd": 2,
        "od": 1,
        "prn": 2,
        "sos": 2,
        "nocte": 2,
        "mane": 2,
        "1-0-1": 3,
        "0-0-1": 3,
        "1-1-1": 3,
    },
    "lab_report": {
        "reference range": 4,
        "biological reference": 4,
        "normal range": 3,
        "ref range": 3,
        "specimen": 3,
        "sample collected": 3,
        "laboratory": 3,
        "lab no": 3,
        "test report": 3,
        "investigation report": 3,
        "haemoglobin": 2,
        "hemoglobin": 2,
        "serum": 2,
        "plasma": 2,
        "units": 1,
        "result": 1,
    },
    "discharge_summary": {
        "discharge summary": 6,
        "date of admission": 4,
        "date of discharge": 4,
        "hospital course": 4,
        "condition at discharge": 4,
        "discharge medication": 4,
        "admitted": 2,
        "discharged": 2,
        "ward": 1,
    },
    "radiology_report": {
        "radiology": 4,
        "radiologist": 4,
        "ultrasound": 3,
        "sonography": 3,
        "ct scan": 3,
        "mri": 3,
        "x-ray": 3,
        "xray": 3,
        "impression": 2,
        "contrast": 2,
        "echogenic": 2,
    },
    "pathology_report": {
        "histopathology": 5,
        "cytology": 4,
        "biopsy": 4,
        "gross examination": 4,
        "microscopic examination": 4,
        "specimen received": 3,
        "malignancy": 2,
    },
    "referral_letter": {
        "referral": 4,
        "dear doctor": 4,
        "dear dr": 4,
        "refer this patient": 4,
        "kindly see": 3,
        "thank you for seeing": 3,
        "yours sincerely": 2,
    },
    "vaccination_record": {
        "vaccination": 5,
        "immunisation": 5,
        "immunization": 5,
        "vaccine": 4,
        "booster": 3,
        "batch no": 2,
        "dose 1": 2,
        "dose 2": 2,
    },
    "operative_note": {
        "operative note": 6,
        "operation performed": 5,
        "post-operative": 4,
        "intra-operative": 4,
        "surgeon": 3,
        "anaesthesia": 3,
        "anesthesia": 3,
        "incision": 3,
        "sutured": 2,
    },
    "doctor_note": {
        "chief complaint": 4,
        "history of present illness": 4,
        "on examination": 3,
        "o/e": 3,
        "clinical note": 3,
        "progress note": 3,
        "c/o": 2,
        "follow up": 1,
    },
}


def _compile(phrase: str) -> re.Pattern[str]:
    """Whole-word matcher for a phrase, tolerant of extra whitespace.

    Trailing \\b is only valid after a word character, so 'o/e' and '1-0-1'
    get a leading boundary only.
    """
    body = re.escape(phrase).replace(r"\ ", r"\s+")
    prefix = r"\b" if phrase[0].isalnum() else ""
    suffix = r"\b" if phrase[-1].isalnum() else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


_COMPILED: dict[str, list[tuple[re.Pattern[str], int]]] = {
    doc_type: [(_compile(phrase), weight) for phrase, weight in markers.items()]
    for doc_type, markers in _MARKERS.items()
}


def score_markers(text: str) -> dict[str, int]:
    """How strongly each document type's markers appear in a page."""
    head = text[:HEAD_CHARS]
    scores: dict[str, int] = {}
    for doc_type, patterns in _COMPILED.items():
        total = 0
        for pattern, weight in patterns:
            if pattern.search(head):
                total += weight * HEAD_WEIGHT
            elif pattern.search(text):
                total += weight
        if total:
            scores[doc_type] = total
    return scores


def _from_markers(page_no: int, text: str) -> PageClassification | None:
    """Settle the page from its markers, or None if the evidence is not clear."""
    scores = score_markers(text)
    if not scores:
        return None

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_type, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0

    if best_score < MIN_MARKER_SCORE or best_score - runner_up < MIN_MARKER_MARGIN:
        return None

    return PageClassification(
        page_no=page_no,
        doc_type=best_type,
        resolved_type=best_type,
        # Scaled by how far ahead the winner is, and capped: markers are strong
        # evidence but never proof.
        confidence=min(0.95, 0.6 + 0.05 * (best_score - runner_up)),
        reason=f"marker score {best_score} vs {runner_up} for the next best type",
        method="markers",
    )


# --- model path --------------------------------------------------------------

PageDocType = Literal[
    "prescription",
    "lab_report",
    "discharge_summary",
    "doctor_note",
    "radiology_report",
    "pathology_report",
    "referral_letter",
    "vaccination_record",
    "operative_note",
    "continuation",
    "other",
    "unknown",
]

# The literal above is what the model is allowed to answer; PAGE_DOC_TYPES is
# what the database accepts. They must not drift apart.
assert set(PageDocType.__args__) == set(PAGE_DOC_TYPES), (
    "PageDocType is out of sync with PAGE_DOC_TYPES in app/db/models.py"
)

SYSTEM_PROMPT = """You identify what kind of medical document a page is.

You are given the transcribed text of one page, and the type of the page before
it if there was one.

Choose exactly one type:
- prescription: medicines prescribed to a patient, with doses or directions.
- lab_report: measured test results, usually with units and reference ranges.
- discharge_summary: an account of a hospital admission through to discharge.
- doctor_note: consultation or progress notes, complaints and examination.
- radiology_report: imaging findings — x-ray, ultrasound, CT, MRI.
- pathology_report: examination of a tissue or fluid specimen.
- referral_letter: one clinician writing to another about a patient.
- vaccination_record: vaccines given, with dates or batch numbers.
- operative_note: an account of a surgical procedure.
- continuation: this page carries on the previous page. Use it only when the
  page has no title or header of its own AND its content visibly runs on from
  where the previous page stopped — more rows of the same table, the rest of
  the same sentence or list. A page that starts new content is not a
  continuation even if it is untitled; choose the type that content belongs to,
  or 'other'.
- other: clearly a medical document, but none of the above.
- unknown: not enough on the page to tell.

Judge only what is on the page. A discharge summary quotes lab results and
lists medicines — it is still a discharge summary, not a lab report or a
prescription. Decide by what the document as a whole is for, not by which
words appear most.

Set confidence low when the page is short, largely illegible, or could
reasonably be two of these types. Do not guess confidently.
"""


class PageTypeVerdict(BaseModel):
    """What the classifying model returns for one page."""

    doc_type: PageDocType = Field(description="The single best type for this page.")
    confidence: float = Field(
        ge=0, le=1, description="How certain you are, from 0 to 1."
    )
    reason: str = Field(description="One short sentence on what decided it.")


_model = None


def _get_model():
    """Built lazily so importing this module needs no API key."""
    global _model
    if _model is None:
        _model = init_chat_model(CLASSIFY_MODEL, temperature=0).with_structured_output(
            PageTypeVerdict
        )
    return _model


async def _from_model(
    page_no: int,
    text: str,
    previous: PageClassification | None,
    previous_tail: str = "",
) -> PageClassification:
    if previous is None:
        context = "This is the first page of the document."
    else:
        context = (
            f"The previous page was classified as: {previous.doc_type}.\n"
            f"It ended with:\n{previous_tail[-PREVIOUS_TAIL_CHARS:]}"
        )
    prompt = f"{context}\n\nPage {page_no} text:\n\n{text[:MAX_PROMPT_CHARS]}"

    try:
        verdict = await _get_model().ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        )
    except Exception as exc:  # noqa: BLE001 - provider errors vary widely
        # Classification is not worth failing a document over: the generic form
        # still extracts from the page.
        logger.warning("classification failed for page %s: %s", page_no, exc)
        return PageClassification(
            page_no=page_no,
            doc_type="unknown",
            resolved_type="unknown",
            confidence=0.0,
            reason=f"classifier unavailable: {exc}",
            method="model",
        )

    if verdict.confidence < MIN_MODEL_CONFIDENCE:
        return PageClassification(
            page_no=page_no,
            doc_type="unknown",
            resolved_type="unknown",
            confidence=verdict.confidence,
            reason=f"too uncertain to use ({verdict.doc_type} at "
            f"{verdict.confidence:.2f}): {verdict.reason}",
            method="model",
        )

    return PageClassification(
        page_no=page_no,
        doc_type=verdict.doc_type,
        resolved_type=_resolve(verdict.doc_type, previous),
        confidence=verdict.confidence,
        reason=verdict.reason,
        method="model",
    )


# --- orchestration -----------------------------------------------------------


def _resolve(doc_type: str, previous: PageClassification | None) -> str:
    """What a page's content should be extracted as.

    A continuation page holds the rest of the previous page's document, so it
    is extracted with that page's form. Chains resolve because every
    classification already carries its own resolved type.
    """
    if doc_type != "continuation":
        return doc_type
    if previous is None:
        return "unknown"
    return previous.resolved_type


async def classify_page(
    page_no: int,
    transcription: str,
    previous: PageClassification | None = None,
    previous_tail: str = "",
) -> PageClassification:
    """Identify one page. Markers first, model only if they are inconclusive.

    `previous_tail` is the end of the previous page's transcription — what the
    model needs to tell a real continuation from a merely untitled page.
    """
    text = (transcription or "").strip()

    if len(text) < MIN_CLASSIFIABLE_CHARS:
        return PageClassification(
            page_no=page_no,
            doc_type="unknown",
            resolved_type="unknown",
            confidence=1.0,
            reason="page has no readable text to classify",
            method="blank",
        )

    from_markers = _from_markers(page_no, text)
    if from_markers is not None:
        return from_markers

    return await _from_model(page_no, text, previous, previous_tail)


async def classify_document(
    pages: Sequence[tuple[int, str]],
    known: Mapping[int, str] | None = None,
) -> list[PageClassification]:
    """Identify every page, in order.

    `pages` is (page_no, transcription) in page order. `known` holds page types
    already decided on an earlier run; those pages are reused rather than
    re-classified, but still count as context for the pages after them.
    """
    known = known or {}
    results: list[PageClassification] = []
    previous: PageClassification | None = None
    previous_tail = ""

    for page_no, transcription in pages:
        cached = known.get(page_no)
        if cached and cached != "unknown":
            current = PageClassification(
                page_no=page_no,
                doc_type=cached,
                resolved_type=_resolve(cached, previous),
                confidence=1.0,
                reason="classified on an earlier run",
                method="cached",
            )
        else:
            current = await classify_page(page_no, transcription, previous, previous_tail)

        results.append(current)
        # Blank pages are not evidence of anything, so they do not become the
        # context a following continuation page inherits from.
        if current.method != "blank":
            previous = current
            previous_tail = (transcription or "").strip()

    return results


def document_type(classifications: Sequence[PageClassification]) -> str:
    """The type for the document as a whole.

    One type if every identifiable page agrees, 'mixed' if a single upload
    holds several kinds of document, 'unknown' if nothing could be placed.
    """
    kinds = {
        item.resolved_type
        for item in classifications
        if item.resolved_type not in ("unknown", "continuation")
    }
    if not kinds:
        return "unknown"
    if len(kinds) == 1:
        return next(iter(kinds))
    return "mixed"
