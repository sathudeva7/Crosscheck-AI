"""Turn a document's extraction into timeline rows.

Everything the pipeline produces lands in `document.extraction_json`: what the
model read, whether each item was found on the page, the parsed values, the
identified drugs, and the confidence and review decision for each fact. All of
it correct, all of it unqueryable — you cannot sort a JSON blob by date or join
it to an interaction table.

This is the crossing point. One visit per extracted document, one event per
fact, drawing each row from all five parts at once:

    data        the fact itself      drug, dose, analyte, value
    normalized  the comparable form  100 + 'mg', 2 doses a day, 0.7-1.3
    drugs       the identity         metoprolol, RxNorm 6918, ATC C07AB
    grounding   the evidence         the quote, and the page it was found on
    review      the verdict          0.76, needs_review, and why

Two things are built here that exist nowhere else: `frequency_label` and
`display_text`. A screen cannot render `doses_per_day = 1` — warfarin once
daily and atorvastatin at night are both 1, and read differently. The number is
for comparing; the words are for showing. Building them once at save time keeps
that logic out of the frontend.

Re-running is safe by design. Rows are keyed by (document_id,
extraction_path), so a second run updates rather than duplicates, and any row a
person has confirmed, corrected or rejected is skipped entirely. Losing a
user's correction to a background job would be unforgivable in a medical
record, so the check is here, in the writer, rather than left to a caller.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentPage, TimelineEvent, TimelineVisit
from app.ingest.normalize import parse_date
from app.ingest.review import DEFAULT_PRIOR, MODALITY_PRIOR

logger = logging.getLogger(__name__)

# Which extraction list becomes which kind of event.
EVENT_TYPE_BY_KIND = {
    "Medication": "medication",
    "LabResult": "lab_result",
    "VitalSign": "vital_sign",
    "Diagnosis": "diagnosis",
    "Allergy": "allergy",
    "Procedure": "procedure",
}

# Plain-text fields on an extraction form, and the title they get as events.
#
# These are strings rather than lists of objects, so they carry no `kind` and
# review.py never grades them — which meant the loop below skipped them and
# they reached the database in extraction_json and stopped there. A follow-up
# date printed on the prescription was extracted correctly and then invisible
# to every screen and every tool that reads the timeline.
NOTE_FIELDS = {
    "follow_up": "Follow-up",
    "advice": "Advice",
}


@dataclass
class BuildResult:
    visits: int = 0
    events: int = 0
    updated: int = 0
    skipped: int = 0  # rows a user has decided on
    removed: int = 0


# The only flags the column will take — mirrors the CHECK in 003_timeline.sql.
PRINTED_FLAGS = ("H", "L", "N", "A")


# Symbols labs print beside an abnormal result instead of a letter. They say
# 'this one is off' without saying which way, which is exactly what 'A' means.
_ABNORMAL_MARKS = ("#", "*", "!", "†", "^")


def _printed_flag(raw: Any) -> str | None:
    """The lab's own flag, mapped onto the four the column will take.

    Labs mark an out-of-range result with whatever their software prints, and
    it is not always a letter: the reports here use '#'. Storing that character
    directly violated the CHECK constraint, which aborted the insert, poisoned
    the session, and left the whole document stranded in 'extracting' — one
    unrecognised character losing three pages of results.

    So it is mapped rather than dropped. Dropping it looked free while
    `computed_flag` was doing the work, but computed_flag is derived from the
    reference range, and a range this parser cannot read leaves the reading with
    no verdict at all. The lab's own mark is the only signal that survives a
    range we failed to parse, and the laboratory is a better authority on its
    own result than our arithmetic is.

    Anything unrecognised is still dropped. A flag column is read as a clinical
    statement, and a value invented from an unfamiliar symbol would be one.
    """
    text = (str(raw) if raw is not None else "").strip()
    if not text:
        return None
    if text[:1] in _ABNORMAL_MARKS:
        return "A"
    letter = text.upper()[:1]
    return letter if letter in PRINTED_FLAGS else None


# --- reading a path back out of the extraction --------------------------------

_INDEX = re.compile(r"^(.*?)\[(\d+)\]$")


def resolve_path(data: dict[str, Any], path: str) -> Any:
    """Fetch the item a review entry points at, e.g. 'medications[0]'."""
    current: Any = data
    for part in path.split("."):
        match = _INDEX.match(part)
        if match:
            name, index = match.group(1), int(match.group(2))
            current = (current or {}).get(name)
            if not isinstance(current, list) or index >= len(current):
                return None
            current = current[index]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        if current is None:
            return None
    return current


# --- wording ------------------------------------------------------------------

_COUNT_WORDS = {1: "once daily", 2: "twice daily", 3: "three times daily", 4: "four times daily"}
_SLOT_NAMES = ["in the morning", "at midday", "at night"]
_SLOT_NAMES_4 = ["in the morning", "at midday", "in the evening", "at night"]
_ABBREVIATION_WORDS = {
    "hs": "nightly", "qhs": "nightly", "nocte": "nightly",
    "mane": "in the morning",
    "od": "once daily", "qd": "once daily", "daily": "once daily",
    "bd": "twice daily", "bid": "twice daily", "bds": "twice daily",
    "tds": "three times daily", "tid": "three times daily",
    "qid": "four times daily", "qds": "four times daily",
    "q4h": "every 4 hours", "q6h": "every 6 hours",
    "q8h": "every 8 hours", "q12h": "every 12 hours",
}
_SLOTS = re.compile(r"^\s*\d+(?:\s*/\s*\d+)?(?:\s*[-–]\s*\d+(?:\s*/\s*\d+)?){1,3}\s*$")
_PRN_WORDS = re.compile(
    r"\b(prn|sos|as\s+needed|as\s+and\s+when\s+required|if\s+required|when\s+required)\b",
    re.IGNORECASE,
)


def frequency_label(raw: str | None, per_day: float | None, prn: bool) -> str | None:
    """Turn a dosing instruction into words a patient would recognise.

    Deliberately separate from the number. '1-0-1' and 'BD' both mean twice a
    day, but 'morning and night' says something the number does not; and HS and
    OD are both once a day while only one of them means at bedtime.
    """
    text = (raw or "").strip()
    label: str | None = None

    if text:
        word = re.sub(r"[^a-z0-9]", "", text.lower())
        if word in _ABBREVIATION_WORDS:
            label = _ABBREVIATION_WORDS[word]
        elif _SLOTS.match(text):
            slots = [s.strip() for s in re.split(r"[-–]", text)]
            names = _SLOT_NAMES_4 if len(slots) == 4 else _SLOT_NAMES
            taken = [
                names[i] for i, slot in enumerate(slots)
                if i < len(names) and slot not in ("0", "0.0")
            ]
            if len(taken) == 1:
                label = taken[0]
            elif len(taken) > 1:
                # 'in the morning' + 'at night' reads better as 'morning and night'
                short = [name.split()[-1] for name in taken]
                label = " and ".join([", ".join(short[:-1]), short[-1]]) if len(short) > 2 \
                    else " and ".join(short)
                label = f"{label}"

    if label is None and per_day:
        label = _COUNT_WORDS.get(int(per_day)) if float(per_day).is_integer() else None
        if label is None:
            label = f"{per_day:g} times daily"

    if label is None and text:
        # Fall back to what the document said — unless all it said was that the
        # drug is taken as needed, which the prn wording below already covers.
        remainder = _PRN_WORDS.sub("", text).strip(" ,()")
        label = remainder or None

    if prn:
        label = f"{label}, when needed" if label else "when needed"

    return label


# Above this, a bare number beside a drug name is not a tablet count. 'Zoclar
# 500' is a brand strength; '1/2' and '2' are how many tablets to take.
MAX_TABLET_COUNT = 4
_FRACTIONS = {0.25: "¼", 0.5: "½", 0.75: "¾"}


def _strength_text(value: float | None, unit: str | None, raw: str | None, title: str) -> str:
    """The strength as it should read on a card, or '' when it adds nothing.

    Many brand names already carry their strength — 'ZOCLAR 500', 'Dolo 650'.
    Repeating it gives 'ZOCLAR 500 500'. If every number in the strength is
    already in the name, the strength is saying nothing new.
    """
    text = f"{value:g} {unit}" if value is not None and unit else (raw or "").strip()
    if not text:
        return ""

    numbers = re.findall(r"\d+", text)
    if numbers and all(number in title for number in numbers):
        return ""
    return text


def _dose_text(count: float | None) -> str:
    """'half a tablet', written the way a label writes it."""
    if count is None:
        return ""
    shown = _FRACTIONS.get(round(float(count), 2)) or f"{float(count):g}"
    return f"{shown} tablet" if float(count) <= 1 else f"{shown} tablets"


def _value_text(value: str, unit: str | None) -> str:
    """'6.8%' but '1.8 mg/dL' — no space before a percent sign."""
    if not unit:
        return value
    return f"{value}{unit}" if unit.strip() in ("%",) else f"{value} {unit}"


# --- building one event -------------------------------------------------------


def _number(values: dict[str, Any], key: str = "value") -> float | None:
    value = (values or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _parse_date(values: dict[str, Any] | None) -> date | None:
    raw = (values or {}).get("value")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except (TypeError, ValueError):
        return None


def _medication_fields(
    item: dict[str, Any], normalized: dict[str, Any], identity: dict[str, Any] | None
) -> dict[str, Any]:
    strength = normalized.get("strength", {})
    frequency = normalized.get("frequency", {})
    duration = normalized.get("duration", {})

    parsed = _number(strength)
    # A bare number is only a tablet count when it is small enough to be one.
    is_count = bool(strength.get("is_count")) and parsed is not None and parsed <= MAX_TABLET_COUNT
    strength_value = None if strength.get("is_count") else parsed
    dose_count = parsed if is_count else None

    per_day = frequency.get("per_day")
    prn = bool(item.get("prn") or frequency.get("prn"))
    label = frequency_label(item.get("frequency"), per_day, prn)

    # The printed name with its packaging stripped: 'TAB. CLARITHROMYCIN IP
    # 500MG' is not something to put on a card.
    title = (identity or {}).get("cleaned") or item.get("drug") or "Unnamed medicine"

    strength_text = _strength_text(
        strength_value, strength.get("unit"), None if is_count else item.get("strength"), title
    )
    dose_text = _dose_text(dose_count)

    display = " ".join(p for p in (title, strength_text) if p)
    tail = ", ".join(p for p in (dose_text, label) if p)
    if tail:
        display = f"{display} — {tail}"

    return {
        "title": title,
        "display_text": display,
        "drug_raw": item.get("drug"),
        "drug_name": ((identity or {}).get("ingredients") or [None])[0],
        "rxcui": ((identity or {}).get("rxcuis") or [None])[0],
        "atc_codes": [a["code"] for a in (identity or {}).get("atc", []) if a.get("code")] or None,
        "strength_value": strength_value,
        "strength_unit": strength.get("unit"),
        "dose_count": dose_count,
        "doses_per_day": per_day,
        "frequency_raw": item.get("frequency"),
        "frequency_label": label,
        "prn": prn,
        "duration_days": duration.get("days"),
        "details": {
            key: item[key]
            for key in ("route", "form", "quantity", "refills", "instructions")
            if item.get(key)
        },
    }


def _lab_fields(item: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    measurement = normalized.get("value", {})
    reference = normalized.get("reference_range", {})

    analyte = item.get("analyte") or item.get("name")
    printed = item.get("value") or measurement.get("text") or ""
    display = f"{analyte} {_value_text(printed, item.get('unit'))}".strip() if analyte else printed

    return {
        "title": analyte,
        "display_text": display,
        "analyte": analyte,
        "value_num": measurement.get("value"),
        "value_text": measurement.get("text"),
        "unit": item.get("unit") or measurement.get("unit"),
        "operator": measurement.get("operator"),
        "ref_low": reference.get("low"),
        "ref_high": reference.get("high"),
        "flag": _printed_flag(item.get("flag")),
        "computed_flag": normalized.get("computed_flag"),
        "details": {
            key: item[key]
            for key in ("specimen", "method", "reference_range", "values")
            if item.get(key)
        },
    }


def _simple_fields(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    if kind == "Allergy":
        title = item.get("substance")
        display = " — ".join(p for p in (title, item.get("reaction")) if p)
        return {
            "title": title, "display_text": display or title,
            "substance": title, "reaction": item.get("reaction"),
            "severity_text": item.get("severity"),
            "details": {},
        }

    description = item.get("description")
    return {
        "title": description,
        "display_text": description,
        "description": description,
        "code": item.get("code"),
        "details": {k: item[k] for k in ("category",) if item.get(k)},
    }


def refresh_display(event: Any) -> None:
    """Rebuild an event's card text from its own columns.

    Used after a correction. The builder above works from the extraction; once
    a person edits a row, the row itself is the truth, so the wording has to be
    regenerated from the columns rather than from the original extraction.
    """
    if event.event_type == "medication":
        title = event.title or event.drug_raw or event.drug_name or "Unnamed medicine"
        strength = _strength_text(
            float(event.strength_value) if event.strength_value is not None else None,
            event.strength_unit,
            None,
            title,
        )
        dose = _dose_text(float(event.dose_count) if event.dose_count is not None else None)
        label = event.frequency_label or frequency_label(
            event.frequency_raw,
            float(event.doses_per_day) if event.doses_per_day is not None else None,
            bool(event.prn),
        )
        event.title = title
        event.frequency_label = label
        text = " ".join(p for p in (title, strength) if p)
        tail = ", ".join(p for p in (dose, label) if p)
        event.display_text = f"{text} — {tail}" if tail else text
        return

    if event.event_type in ("lab_result", "vital_sign"):
        title = event.title or event.analyte
        printed = (
            f"{float(event.value_num):g}" if event.value_num is not None else (event.value_text or "")
        )
        if event.operator and printed:
            printed = f"{event.operator}{printed}"
        event.title = title
        event.display_text = f"{title} {_value_text(printed, event.unit)}".strip() if title else printed
        return

    title = event.title or event.substance or event.description
    parts = (title, event.reaction) if event.event_type == "allergy" else (title,)
    event.title = title
    event.display_text = " — ".join(p for p in parts if p) or title


def _event_date(kind: str, normalized: dict[str, Any], visit_date: date | None) -> date | None:
    """The date this fact belongs to.

    A chart plots ten dated temperature readings; each one belongs on its own
    date, not on the date of the prescription that happened to contain it.
    """
    for field in ("collected_at", "measured_at", "performed_at", "onset"):
        parsed = _parse_date(normalized.get(field))
        if parsed:
            return parsed
    return visit_date


def _fields_for(
    kind: str,
    item: dict[str, Any],
    normalized: dict[str, Any],
    identity: dict[str, Any] | None,
) -> dict[str, Any]:
    if kind == "Medication":
        return _medication_fields(item, normalized, identity)
    if kind in ("LabResult", "VitalSign"):
        return _lab_fields(item, normalized)
    return _simple_fields(kind, item)


# --- writing ------------------------------------------------------------------


def _apply(event: TimelineEvent, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(event, key, value)


def _build_visit(
    db: Session, document: Document, group_no: int, group: dict[str, Any]
) -> TimelineVisit:
    """Create or refresh the card for one extracted document."""
    visit = db.scalar(
        select(TimelineVisit).where(
            TimelineVisit.document_id == document.id, TimelineVisit.group_no == group_no
        )
    )
    if visit is None:
        visit = TimelineVisit(
            patient_id=document.patient_id, document_id=document.id, group_no=group_no
        )
        db.add(visit)

    header = (group.get("data") or {}).get("header") or {}
    when = group.get("date") or {}

    visit.visit_date = _parse_date(when)
    visit.date_ambiguous = bool(when.get("ambiguous"))
    visit.date_raw = when.get("raw")
    visit.doc_type = group.get("doc_type") or "unknown"
    visit.provider = header.get("provider")
    visit.clinician_name = header.get("clinician_name")
    visit.clinician_specialty = header.get("clinician_specialty")
    visit.summary = header.get("summary")
    return visit


def _build_notes(
    db: Session,
    document: Document,
    visit: TimelineVisit,
    group: dict[str, Any],
    data: dict[str, Any],
    existing: dict[str, TimelineEvent],
    seen: set[str],
    key_for: Callable[[str], str],
) -> tuple[int, int, int]:
    """Turn the form's free-text fields into note events. Returns (new, updated, skipped).

    Confidence comes from how the page was read and nothing else. There is no
    grounding check to lean on: ground.py verifies a `source_span` quoted back
    by the model, and these fields have none — the extractor copied a line of
    text rather than filling in a structured item. Claiming a score derived
    from checks that did not run would be worse than claiming none, so what is
    recorded is the honest floor for 'a model read this off a page of this
    kind'.

    A follow-up date is given its own date so it lands where it belongs on the
    timeline. Advice keeps the visit's date: it was said at the visit.
    """
    created = updated = skipped = 0
    pages = group.get("pages") or []
    page_no = pages[0] if pages else None
    modality = _page_modality(db, document, page_no)

    for field, title in NOTE_FIELDS.items():
        text = (data.get(field) or "").strip() if isinstance(data.get(field), str) else ""
        if not text:
            continue

        key = key_for(field)
        seen.add(key)
        event = existing.get(key)

        if event is not None and event.user_decided:
            skipped += 1
            continue

        if event is None:
            event = TimelineEvent(
                patient_id=document.patient_id,
                document_id=document.id,
                extraction_path=key,
            )
            db.add(event)
            created += 1
        else:
            updated += 1

        when = parse_date(text).value if field == "follow_up" else None

        event.visit_id = visit.id
        event.event_type = "note"
        event.event_date = when or visit.visit_date
        event.page_no = page_no
        event.source_span = text
        event.title = title
        event.description = text
        event.display_text = f"{title}: {text}"
        event.confidence = MODALITY_PRIOR.get(modality or "", DEFAULT_PRIOR)
        event.review_status = "accepted"
        event.review_reasons = None
        event.risk = "low"
        event.signals = {"free_text": True, "field": field}
        event.source = "ai"

    return created, updated, skipped


def _page_modality(db: Session, document: Document, page_no: int | None) -> str | None:
    """How the page this text came from was read — digital, scanned, handwritten."""
    if page_no is None:
        return None
    return db.scalar(
        select(DocumentPage.modality).where(
            DocumentPage.document_id == document.id, DocumentPage.page_no == page_no
        )
    )


def rebuild_document(db: Session, document: Document) -> BuildResult:
    """Rewrite this document's timeline rows from its extraction.

    Rows a user has confirmed, corrected or rejected are left exactly as they
    are — a background re-run must never undo a person's decision.
    """
    result = BuildResult()
    groups = (document.extraction_json or {}).get("groups") or []

    # An extraction path is relative to its group — 'results[0]', 'advice' —
    # while the unique key it is stored under is (document_id, extraction_path).
    # For a one-group document those agree. For a document that split into
    # several, every group offers a 'results[0]' and the second one collides,
    # which is why a multi-page report could never finish: the insert failed,
    # the session broke, and the row sat in 'extracting' for ever.
    #
    # Qualified only when there is more than one group, deliberately. Rows
    # already stored by a single-group document keep the exact key they have,
    # so nothing is orphaned and no row a user has corrected is duplicated.
    def key_for(group_no: int, path: str) -> str:
        return f"groups[{group_no}].{path}" if len(groups) > 1 else path

    existing = {
        event.extraction_path: event
        for event in db.scalars(
            select(TimelineEvent).where(TimelineEvent.document_id == document.id)
        )
        if event.extraction_path
    }
    seen: set[str] = set()

    # One card per visit, not per group. A 19-page lab report is split into as
    # many groups as one model call can hold, and every one of them carries the
    # same date and the same type — so without this the timeline showed a single
    # morning at the lab as four separate visits, each a partial list of the
    # same results. Groups are a limit of the extractor; they are not something
    # that happened to the patient.
    # Worked out before anything is created. Creating a card and deleting it a
    # moment later is not the same thing: timeline_event.visit_id cascades, so
    # removing a redundant visit takes every event already hanging off it —
    # measured, once, as 86 events becoming 34.
    owner: dict[int, int] = {}
    first_seen: dict[tuple[Any, str], int] = {}
    for group_no, group in enumerate(groups):
        when = group.get("date") or {}
        key = (_parse_date(when), group.get("doc_type") or "unknown")
        if key[0] is not None and key in first_seen:
            owner[group_no] = first_seen[key]
        else:
            first_seen.setdefault(key, group_no)
            owner[group_no] = group_no

    built: dict[int, TimelineVisit] = {}

    for group_no, group in enumerate(groups):
        holder = owner[group_no]
        if holder not in built:
            built[holder] = _build_visit(db, document, holder, groups[holder])
            db.flush()  # need visit.id for the events below
            result.visits += 1
        visit = built[holder]

        data = group.get("data") or {}
        normalized_all = group.get("normalized") or {}
        drugs = group.get("drugs") or {}
        grounding = {g["path"]: g for g in group.get("grounding") or [] if g.get("path")}

        for verdict in group.get("review") or []:
            path, kind = verdict.get("path"), verdict.get("kind")
            event_type = EVENT_TYPE_BY_KIND.get(kind)
            if not path or not event_type:
                continue

            item = resolve_path(data, path)
            if not isinstance(item, dict):
                logger.warning("%s: %s not found in extraction data", document.id, path)
                continue

            key = key_for(group_no, path)
            seen.add(key)
            event = existing.get(key)

            if event is not None and event.user_decided:
                result.skipped += 1
                continue

            if event is None:
                event = TimelineEvent(
                    patient_id=document.patient_id,
                    document_id=document.id,
                    extraction_path=key,
                )
                db.add(event)
                result.events += 1
            else:
                result.updated += 1

            normalized = normalized_all.get(path) or {}
            identity = drugs.get(item.get("drug")) if kind == "Medication" else None
            found = grounding.get(path) or {}

            event.visit_id = visit.id
            event.event_type = event_type
            event.event_date = _event_date(kind, normalized, visit.visit_date)
            event.page_no = found.get("page_no")
            event.source_span = found.get("source_span")

            _apply(event, _fields_for(kind, item, normalized, identity))

            if event_type == "medication":
                event.start_date = event.event_date
                days = event.duration_days
                event.end_date = (
                    event.start_date + timedelta(days=float(days) - 1)
                    if event.start_date and days and float(days) >= 1
                    else None
                )

            event.confidence = verdict.get("confidence")
            event.review_status = verdict.get("status") or "needs_review"
            event.review_reasons = verdict.get("reasons") or None
            event.risk = verdict.get("risk") or "low"
            event.signals = verdict.get("signals") or {}
            event.source = "ai"

        # The plain-text fields, which the loop above cannot reach.
        result_note = _build_notes(
            db, document, visit, group, data, existing, seen,
            lambda field: key_for(group_no, field),
        )
        result.events += result_note[0]
        result.updated += result_note[1]
        result.skipped += result_note[2]

    # Facts that disappeared from the extraction — usually because the document
    # was re-read and the model grouped things differently.
    for path, event in existing.items():
        if path not in seen and not event.user_decided:
            db.delete(event)
            result.removed += 1

    # Visits this run did not use, left over from a previous one. Tested against
    # the set actually built rather than against len(groups): several groups can
    # now share one card, so the unused numbers are no longer only the ones off
    # the end.
    for visit in db.scalars(
        select(TimelineVisit).where(TimelineVisit.document_id == document.id)
    ):
        if visit.group_no in built:
            continue
        if any(event.user_decided for event in visit.events):
            continue  # someone has worked on this; leave it alone
        db.delete(visit)

    db.commit()
    return result
