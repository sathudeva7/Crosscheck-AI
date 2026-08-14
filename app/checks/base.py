"""Shared pieces for the cross-checks.

The one idea worth stating up front: a check never decides anything from a
model. Whether two drugs interact is decided by a join against
`interaction_rule`; whether two are the same kind of drug is decided by their
WHO classes. A model is asked only to phrase a finding that already exists.
That is what makes a warning reproducible — the same record checked twice gives
the same answer, which is the least you can ask of something medical.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from app.db.models import TimelineEvent

# What a prescription with no stated duration is assumed to cover.
#
# Most prescriptions do not say how long to take the drug, so without an
# assumption almost nothing would ever be checked against anything. Ninety days
# covers repeat prescriptions and chronic medicines without dragging in
# records from years ago — and every finding that relies on it says so, so the
# reader can judge it for themselves.
ASSUMED_DURATION_DAYS = 90

# How much of an ATC code has to match for two drugs to count as the same kind.
#
# Four characters is the therapeutic subgroup: C07A is 'beta blocking agents',
# which catches metoprolol (C07AB) and oxprenolol (C07AA). Three would be C07,
# broad enough to be noise; five would be C07AB, so narrow that two beta
# blockers of different subtypes would slip through.
ATC_PREFIX = 4

# DDInter grades interactions; the timeline colours dots. 'Unknown' means the
# pair is listed but ungraded, which is not the same as safe.
SEVERITY_FROM_DDINTER = {
    "Major": "danger",
    "Moderate": "warning",
    "Minor": "info",
    "Unknown": "info",
}

SEVERITY_ORDER = ("none", "info", "warning", "danger")


@dataclass
class Candidate:
    """A problem a check has found, before it is written to the database."""

    key: str  # fingerprint; the same problem always produces the same key
    finding_type: str
    severity: str
    title: str
    evidence: dict[str, Any] = field(default_factory=dict)
    event_ids: list[UUID] = field(default_factory=list)
    visit_ids: list[UUID] = field(default_factory=list)


def worst(severities) -> str:
    """The highest of several severities."""
    found = [s for s in severities if s in SEVERITY_ORDER]
    return max(found, key=SEVERITY_ORDER.index) if found else "none"


def window(event: TimelineEvent) -> tuple[date, date] | None:
    """When a medicine was probably being taken.

    Uses the stated duration where there is one, and the assumption above where
    there is not. Returns None for an event with no date at all, which cannot
    be placed against anything.
    """
    start = event.start_date or event.event_date
    if start is None:
        return None
    if event.end_date:
        return start, event.end_date
    return start, start + timedelta(days=ASSUMED_DURATION_DAYS)


def overlaps(a: TimelineEvent, b: TimelineEvent) -> tuple[bool, bool]:
    """Whether two medicines were taken together, and whether that is certain.

    Two drugs on the same document were prescribed together — no assumption is
    involved. Anything else is judged by date ranges, and if either range came
    from the assumed duration the answer is marked uncertain so the finding can
    say so.
    """
    if a.document_id is not None and a.document_id == b.document_id:
        return True, True

    first, second = window(a), window(b)
    if first is None or second is None:
        return False, False

    together = first[0] <= second[1] and second[0] <= first[1]
    certain = bool(a.end_date and b.end_date)
    return together, certain


def assumption_note(certain: bool) -> str | None:
    """The sentence that has to accompany an assumed overlap."""
    if certain:
        return None
    return (
        f"Neither prescription said how long to take the medicine, so we assumed "
        f"{ASSUMED_DURATION_DAYS} days. If you have already stopped one of them, "
        f"this may not apply to you."
    )


def atc_families(event: TimelineEvent) -> set[str]:
    """The drug families this medicine belongs to."""
    return {code[:ATC_PREFIX] for code in (event.atc_codes or []) if len(code) >= ATC_PREFIX}


def regimen(event: TimelineEvent) -> dict[str, Any]:
    """One medicine as this patient's record actually holds it.

    Findings carry this so that the wording written from them can name a real
    dose on a real date. Without it the evidence is two drug names and a
    severity, and an explanation built from that has nothing patient-specific
    to say — it reaches for what the model knows about the drugs in general,
    which is exactly what a record-driven warning is not supposed to do.

    `display_text` is the source rather than the strength and frequency
    columns reassembled here. It is what the timeline already shows, and
    `_rebuild` in ingest/timeline.py rewrites it from the columns whenever a
    person corrects a row, so the two cannot drift apart. Assembling a second
    phrasing in this module would eventually contradict the first.

    `document.extraction_json` is deliberately not the source either. It keeps
    what the model said on the first read; a dose the user has since corrected
    still sits there at its old value, and a warning quoting it would
    contradict the timeline printed beside it.
    """
    started = event.start_date or event.event_date
    printed = (event.source_span or event.drug_raw or "").strip()
    fields = {
        "drug": event.drug_name or event.drug_raw,
        "display": event.display_text,
        # Only when it adds something: for a typed prescription the printed
        # line and the parsed one are usually the same sentence twice.
        "as_printed": printed if printed and printed != event.display_text else None,
        "started": str(started) if started else None,
        "until": str(event.end_date) if event.end_date else None,
    }
    return {key: value for key, value in fields.items() if value}


def checkable(events) -> list[TimelineEvent]:
    """Medicines that can actually be checked: identified, and not rejected.

    A medicine with no rxcui is invisible to every check here. That is why the
    engine reports how many of the patient's medicines it was able to check —
    silence about an unidentified drug must never read as 'nothing wrong'.
    """
    return [
        event
        for event in events
        if event.event_type == "medication"
        and event.rxcui
        and event.review_status != "rejected"
    ]
