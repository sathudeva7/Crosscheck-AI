"""Which kind of doctor should read this lab report.

A prescription arrives from a doctor who has already decided something. A lab
report arrives from a machine, and the patient holding it does not know what it
means or who to take it to. That gap is what this answers, at the moment the
report is uploaded rather than after a check has flagged something — most
reports never raise a finding at all, and 'nothing flagged' is not the same as
'nothing to ask about'.

A model decides, not a table. Earlier drafts of this scored the report by hand
-- ten points per abnormal result, three if the panel title named the organ --
and those numbers were invented by the author, which is a poor foundation for
telling someone which doctor to see. Reading thirty results, a multi-part panel
title and a paragraph of pathologist's prose, then judging what the report is
mainly about, is comprehension rather than arithmetic.

What the model cannot do is invent a specialty. It chooses by searching the two
channelling directories' own catalogues through a tool, and every name it
returns is checked against those catalogues before being stored. A model
answering 'Tropical Medicine Physician' from memory -- plausible, and listed by
nobody in Sri Lanka -- would produce a doctor search that returns an empty
screen for a specialty that never existed. Here it is caught and dropped.

Three things stay out of the model's hands:

    the numbers      0.86 against 0.90-1.30 is a comparison, and the values
                     and bounds are already parsed into columns. Re-deriving
                     them from prose costs money to sometimes get it wrong.
    the verdict      on a text result -- 'detected' against 'Not detected' --
                     nothing decides. Both are shown as the report printed
                     them, because an HIV result read backwards is the worst
                     thing this application could do.
    the lab's words  quoted exactly or not at all. A paraphrase stops being
                     the laboratory's statement and becomes ours.

Runs once, during ingest, and is stored on the document. Opening the report
later is a database read.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    MODEL_RETRIES,
    MODEL_TIMEOUT,
    REPORT_MAX_SEARCHES,
    REPORT_MODEL,
)
from app.db.models import Document, TimelineEvent
from app.referral import catalogue

logger = logging.getLogger(__name__)

# Results are summarised for the model, not streamed in full. A blood count is
# thirty rows of which three matter, and the abnormal ones are listed
# individually while the rest are counted.
MAX_NORMAL_LISTED = 40

# More than this and the reader is choosing rather than being helped. Three is
# enough to cover a report spanning two organ systems and still name a general
# doctor.
MAX_SPECIALTIES = 3


# --- what the model is asked to produce ---------------------------------------


class SuggestedSpecialty(BaseModel):
    """One kind of doctor, as the model chose it."""

    specialty: str = Field(
        description=(
            "The specialty name exactly as the search tool returned it. Never "
            "a name you have not seen in a tool result."
        )
    )
    reason: str = Field(
        description=(
            "One sentence, addressed to the patient, saying why this report "
            "goes to this kind of doctor. Plain words, no jargon, no diagnosis."
        )
    )
    confidence: str = Field(description="high, medium or low")


class ReportVerdict(BaseModel):
    """The model's whole answer for one report."""

    about: str = Field(
        description=(
            "What this report looks at, in plain words a patient would use — "
            "'kidney function and a blood count'. Not a diagnosis, and never a "
            "condition the patient might have."
        )
    )
    specialties: list[SuggestedSpecialty] = Field(
        description="Best first. At most three, and include a general doctor last."
    )
    interpretation_is_about_patient: bool = Field(
        description=(
            "True if the laboratory's note discusses this patient's own result. "
            "False if it is methodology, a disclaimer, or a general reference "
            "table that would be printed on any copy of this report."
        )
    )


# --- what gets stored ---------------------------------------------------------


@dataclass
class StoredSpecialty:
    specialty: str
    reason: str
    confidence: str
    directories: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    """The answer as it is written to `document.recommendation_json`."""

    about: str = ""
    specialties: list[StoredSpecialty] = field(default_factory=list)
    interpretation: str | None = None
    panel_names: list[str] = field(default_factory=list)
    result_count: int = 0
    abnormal_count: int = 0
    model: str = ""
    searches: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# --- reading the report -------------------------------------------------------


@dataclass
class Reading:
    """One result, as it will be shown to the model and to the reader."""

    analyte: str
    value: str
    unit: str | None
    reference: str | None
    abnormal: bool
    lab_flagged: bool
    qualitative: bool


def _reference_text(event: TimelineEvent) -> str | None:
    """The normal range, preferring the words the report printed."""
    raw = (event.details or {}).get("reference_range")
    if raw:
        return str(raw)
    if event.ref_low is not None and event.ref_high is not None:
        return f"{event.ref_low} - {event.ref_high}"
    if event.ref_high is not None:
        return f"< {event.ref_high}"
    if event.ref_low is not None:
        return f"> {event.ref_low}"
    return None


def readings_for(db: Session, document: Document) -> list[Reading]:
    """Every measurement on this document, with whether it is out of range.

    The abnormal decision is made here, from the parsed number and bounds, and
    is not asked of the model. A text result such as 'detected' is carried
    through as qualitative with no verdict attached: the reader is shown it
    beside what the report said to expect, and draws the obvious conclusion
    without either a rule or a model having ruled on an HIV test.
    """
    events = db.scalars(
        select(TimelineEvent).where(
            TimelineEvent.document_id == document.id,
            TimelineEvent.event_type.in_(("lab_result", "vital_sign")),
            TimelineEvent.review_status != "rejected",
        )
    ).all()

    out: list[Reading] = []
    for event in events:
        qualitative = event.value_num is None and bool(event.value_text)
        value = (
            f"{float(event.value_num):g}" if event.value_num is not None else (event.value_text or "")
        )
        if not value:
            continue
        out.append(
            Reading(
                analyte=(event.analyte or "").strip(),
                value=value,
                unit=event.unit,
                reference=_reference_text(event),
                abnormal=event.computed_flag in ("H", "L") or event.flag == "A",
                lab_flagged=event.flag == "A",
                qualitative=qualitative,
            )
        )
    return out


def _panels_and_notes(document: Document) -> tuple[list[str], str | None]:
    """The report's own title and the pathologist's note, if it wrote one."""
    panels: list[str] = []
    notes: list[str] = []
    for group in (document.extraction_json or {}).get("groups", []):
        data = group.get("data") or {}
        name = (data.get("panel_name") or "").strip()
        if name and name not in panels:
            panels.append(name)
        note = (data.get("interpretation") or "").strip()
        if note and note not in notes:
            notes.append(note)
    return panels, "\n\n".join(notes) or None


def _report_text(panels: list[str], notes: str | None, readings: list[Reading]) -> str:
    """The report as the model reads it."""
    lines: list[str] = []
    lines.append("PANEL TITLE: " + ("; ".join(panels) or "(none printed)"))

    abnormal = [r for r in readings if r.abnormal]
    qualitative = [r for r in readings if r.qualitative]
    normal = [r for r in readings if not r.abnormal and not r.qualitative]

    lines.append(f"\n{len(readings)} results, {len(abnormal)} outside the normal range.")

    if abnormal:
        lines.append("\nOUTSIDE THE NORMAL RANGE:")
        for r in abnormal:
            mark = "  (the laboratory flagged this too)" if r.lab_flagged else ""
            lines.append(
                f"  {r.analyte}: {r.value} {r.unit or ''} "
                f"(normal {r.reference or 'not printed'}){mark}"
            )

    if qualitative:
        lines.append("\nRESULTS REPORTED IN WORDS (no verdict has been computed):")
        for r in qualitative:
            lines.append(
                f"  {r.analyte}: {r.value}  (the report says to expect: {r.reference or 'not printed'})"
            )

    if normal:
        lines.append(f"\nWITHIN RANGE ({len(normal)}):")
        lines.append("  " + ", ".join(r.analyte for r in normal[:MAX_NORMAL_LISTED]))

    if notes:
        lines.append("\nNOTE PRINTED ON THE REPORT BY THE LABORATORY:")
        lines.append(notes[:2000])

    return "\n".join(lines)


# --- the agent ----------------------------------------------------------------

SYSTEM = """You help a patient in Sri Lanka work out which kind of doctor should read their lab report.

You will be given one report: its panel title, its results, and any note the laboratory printed.

HOW TO CHOOSE
Use the search_specialties tool to find which kinds of doctor can actually be booked. Search for what the report is about — 'kidney', 'blood', 'liver', 'hiv' all work, as do clinical names like 'nephrologist'. Search more than once if the first try is not right.

You may ONLY name a specialty that appeared in a tool result. Never answer from your own knowledge of medicine: a specialty that sounds right but is not in these directories cannot be booked, and the patient gets an empty screen. If nothing suitable comes back, use a general physician.

Prefer specialties marked as listed by both directories — the others are sometimes one clinic's own wording rather than a real specialty. Prefer the common, general form over a narrow one: 'Cardiologist' rather than 'Consultant Interventional Cardiologist And Electrophysiologist'. Do not choose a paediatric specialty unless the report is clearly a child's.

Give at most three, best first, and make the last one a general physician — any report can be reviewed by a general doctor.

WHAT YOU MUST NOT DO
Do not name, guess at or hint at a disease, condition or diagnosis. You are saying which kind of doctor reads reports like this — nothing more. 'This looks at kidney function' is right. 'This suggests kidney disease' is not, and is not yours to say.

Do not recalculate whether a result is inside its range. That has been done and is given to you.
Do not decide what a result reported in words means. It is shown to the patient exactly as printed.

Write reasons in short, plain sentences addressed to the patient. No jargon."""


_agent = None
_answerer = None


def _get_agent():
    """Built lazily so importing this module needs no API key."""
    global _agent
    if _agent is None:
        _agent = init_chat_model(
            REPORT_MODEL, temperature=0, timeout=MODEL_TIMEOUT, max_retries=MODEL_RETRIES
        ).bind_tools([_search_tool()])
    return _agent


def _get_answerer():
    global _answerer
    if _answerer is None:
        _answerer = init_chat_model(
            REPORT_MODEL, temperature=0, timeout=MODEL_TIMEOUT, max_retries=MODEL_RETRIES
        ).with_structured_output(ReportVerdict)
    return _answerer


def _search_tool():
    from langchain_core.tools import tool

    @tool
    def search_specialties(query: str) -> str:
        """Find kinds of doctor that can actually be booked in Sri Lanka.

        Searches the live catalogues of both national channelling directories.
        Accepts plain words ('heart', 'kidney', 'blood') or clinical names
        ('nephrologist'). Returns matching specialty names; 'both directories'
        marks the ones most likely to be a real, commonly used specialty.
        """
        found = catalogue.search(query)
        if not found:
            return f"No bookable specialty matches {query!r}. Try another word."
        return "\n".join(
            f"- {entry.name}"
            + (" (both directories)" if entry.in_both else " (one directory only)")
            for entry in found
        )

    return search_specialties


def _run_agent(report: str) -> tuple[ReportVerdict | None, list[str]]:
    """Let the model search the directories, then state its answer.

    Two phases on purpose. The first lets it explore with a tool; the second
    asks for a structured answer given everything it found. Combining tool
    calling and structured output in one request is supported unevenly across
    providers, and a malformed final message here would cost the whole upload.
    """
    tool = _search_tool()
    messages: list[Any] = [SystemMessage(SYSTEM), HumanMessage(report)]
    searched: list[str] = []

    for _ in range(REPORT_MAX_SEARCHES):
        reply: AIMessage = _get_agent().invoke(messages)
        messages.append(reply)
        calls = getattr(reply, "tool_calls", None) or []
        if not calls:
            break
        for call in calls:
            query = (call.get("args") or {}).get("query", "")
            searched.append(query)
            try:
                result = tool.invoke({"query": query})
            except Exception as exc:  # a directory being down must not end ingest
                logger.warning("specialty search %r failed: %s", query, exc)
                result = "The directory could not be reached."
            messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

    messages.append(
        HumanMessage(
            "Now give your final answer, using only specialty names that appeared "
            "in the tool results above."
        )
    )
    verdict = _get_answerer().invoke(messages)
    return verdict, searched


def _confirm(chosen: list[SuggestedSpecialty]) -> tuple[list[StoredSpecialty], list[str]]:
    """Keep only specialties the directories actually list.

    The last gate. The prompt tells the model to answer from tool results, and
    mostly it does; this is what happens when it does not. A name nobody lists
    is dropped rather than shown, because the cost of keeping it is a patient
    searching for a doctor who cannot exist.
    """
    kept: list[StoredSpecialty] = []
    dropped: list[str] = []
    for item in chosen[:MAX_SPECIALTIES]:
        entry = catalogue.known(item.specialty)
        if entry is None:
            logger.info("dropping unlisted specialty %r", item.specialty)
            dropped.append(item.specialty)
            continue
        if any(k.specialty.lower() == entry.name.lower() for k in kept):
            continue
        kept.append(
            StoredSpecialty(
                specialty=entry.name,
                reason=item.reason.strip(),
                confidence=(item.confidence or "medium").strip().lower(),
                directories=list(entry.directories),
            )
        )
    return kept, dropped


def recommend(db: Session, document: Document) -> Recommendation:
    """Read one lab report and decide who should look at it.

    Never raises. A recommendation is a helpful extra on top of a document that
    has already been read and stored, and losing an upload because a directory
    timed out would trade something valuable for something optional.
    """
    readings = readings_for(db, document)
    panels, notes = _panels_and_notes(document)

    result = Recommendation(
        panel_names=panels,
        result_count=len(readings),
        abnormal_count=sum(1 for r in readings if r.abnormal),
        model=REPORT_MODEL,
    )

    if not readings and not panels:
        result.error = "This document has no results to read."
        return result

    try:
        verdict, searched = _run_agent(_report_text(panels, notes, readings))
    except Exception as exc:
        logger.warning("report recommendation failed for %s: %s", document.id, exc)
        result.error = "The recommendation could not be produced."
        return result

    result.searches = searched
    if verdict is None:
        result.error = "The recommendation could not be produced."
        return result

    result.about = (verdict.about or "").strip()
    result.specialties, result.dropped = _confirm(verdict.specialties)
    # Kept only when the model judged it to be about this patient. The rest is
    # methodology and disclaimers, printed identically on every copy of the
    # report, and showing it would bury the one note that does say something.
    if notes and verdict.interpretation_is_about_patient:
        result.interpretation = notes

    if not result.specialties:
        result.error = "No bookable specialty could be matched to this report."
    return result
