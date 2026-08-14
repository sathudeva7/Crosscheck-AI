"""Run every cross-check for one patient and record what was found.

Runs over the patient's whole record, not the document just uploaded. That is
the point of the thing: a prescription added today has to be checked against
one from three years ago, because the two clinicians who wrote them never saw
each other's work.

Repeating it is safe. Every finding carries a fingerprint of what it is about,
so a second run updates the same row instead of adding another, and an
explanation already written is never paid for twice. A finding the user
dismissed stays dismissed, and is kept even after its cause disappears — a
warning waved away once must not come back because a document was re-read.

The counts it returns matter as much as the findings. Silence about a medicine
that could not be identified must never be read as 'nothing wrong', so the
engine reports how many medicines it was actually able to check.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.checks.base import Candidate, worst
from app.checks.drugs import (
    check_allergies,
    check_dosage_conflicts,
    check_duplicates,
    check_interactions,
)
from app.checks.explain import explain
from app.checks.labs import check_critical_values, check_lab_trends
from app.db.models import Finding, InteractionRule, TimelineEvent, TimelineVisit

logger = logging.getLogger(__name__)

CHECKS = (
    check_interactions,
    check_duplicates,
    check_allergies,
    check_dosage_conflicts,
    check_lab_trends,
    check_critical_values,
)


@dataclass
class CheckResult:
    """What was checked, and what came of it.

    `medicines_checked` against `medicines_total` is what lets the interface
    say 'checked 2 of 4' rather than showing a clean result that quietly
    excluded half the prescription.
    """

    findings: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    medicines_total: int = 0
    medicines_checked: int = 0
    rules_available: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.medicines_total == self.medicines_checked


def _now() -> datetime:
    return datetime.now(UTC)


async def run_checks(
    db: Session,
    patient_id: UUID,
    explain_new: bool = True,
    rewrite_explanations: bool = False,
) -> CheckResult:
    """Check everything, write the findings, colour the timeline.

    `rewrite_explanations` reworks the wording of findings that already have
    some. Off by default and deliberately so — see `_write_explanations` — but
    needed after the evidence a finding carries has changed shape, since the
    text was written against the old one and nothing else would ever replace it.
    """
    events = list(
        db.scalars(select(TimelineEvent).where(TimelineEvent.patient_id == patient_id))
    )

    candidates: list[Candidate] = []
    for check in CHECKS:
        try:
            candidates.extend(check(db, events))
        except Exception:  # noqa: BLE001 - one broken check must not lose the rest
            logger.exception("check %s failed for patient %s", check.__name__, patient_id)

    # Two checks can describe the same pair — an interaction and a duplicate
    # therapy — and both are worth showing, but neither should appear twice.
    unique: dict[str, Candidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.key, candidate)

    result = _write(db, patient_id, unique)
    if explain_new:
        await _write_explanations(db, patient_id, unique, rewrite=rewrite_explanations)

    colour_visits(db, patient_id)
    db.commit()

    medicines = [e for e in events if e.event_type == "medication" and e.review_status != "rejected"]
    result.medicines_total = len(medicines)
    result.medicines_checked = sum(1 for e in medicines if e.rxcui)
    result.rules_available = _rule_count(db)
    return result


_rule_total: int | None = None


def _rule_count(db: Session) -> int:
    """How many interaction pairs the check ran against.

    Counted once per process. It is reference data that only changes when the
    seed script runs, and counting 215,000 rows on every upload to print one
    number in a status line would be silly.
    """
    global _rule_total
    if _rule_total is None:
        _rule_total = db.scalar(select(func.count()).select_from(InteractionRule)) or 0
    return _rule_total


def _write(db: Session, patient_id: UUID, candidates: dict[str, Candidate]) -> CheckResult:
    """Store the findings, keeping decisions the user has already made."""
    result = CheckResult()
    existing = {
        finding.key: finding
        for finding in db.scalars(select(Finding).where(Finding.patient_id == patient_id))
    }

    for key, candidate in candidates.items():
        finding = existing.get(key)
        if finding is None:
            finding = Finding(patient_id=patient_id, key=key, first_seen_at=_now())
            db.add(finding)
            result.created += 1
        else:
            result.updated += 1

        finding.finding_type = candidate.finding_type
        finding.severity = candidate.severity
        finding.title = candidate.title
        finding.evidence = candidate.evidence
        finding.event_ids = candidate.event_ids
        finding.visit_ids = candidate.visit_ids
        finding.last_seen_at = _now()

        result.by_severity[candidate.severity] = result.by_severity.get(candidate.severity, 0) + 1

    # Findings whose cause has gone: the drug was rejected, or the document was
    # deleted. Dismissed ones are kept as tombstones so they cannot return.
    for key, finding in existing.items():
        if key in candidates:
            continue
        if finding.status == "dismissed":
            continue
        db.delete(finding)
        result.removed += 1

    db.flush()
    result.findings = len(candidates)
    return result


async def _write_explanations(
    db: Session,
    patient_id: UUID,
    candidates: dict[str, Candidate],
    rewrite: bool = False,
) -> None:
    """Fill in the wording for findings that do not have any yet.

    Only new ones by default. An explanation is written once and kept:
    rewording a warning on every upload would be both wasteful and unsettling
    to read — the same danger, phrased differently each time you open the page,
    reads as a system changing its mind.

    `rewrite` takes in the ones already written too. It is for the case that
    rule cannot cover: when the evidence a finding carries has gained a field,
    every existing explanation was written without it and will never be
    revisited otherwise.
    """
    query = select(Finding).where(Finding.patient_id == patient_id)
    if not rewrite:
        query = query.where(Finding.detail.is_(None))
    pending = list(db.scalars(query))
    for finding in pending:
        candidate = candidates.get(finding.key)
        if candidate is None:
            continue
        finding.detail, finding.detail_source = await explain(candidate)
    if pending:
        db.flush()


def colour_visits(db: Session, patient_id: UUID) -> None:
    """Set each visit's severity to the worst finding that touches it.

    This is what turns the timeline dots red. Recomputed from scratch every
    run, so a visit whose finding was dismissed goes back to plain.
    """
    visits = {
        visit.id: visit
        for visit in db.scalars(
            select(TimelineVisit).where(TimelineVisit.patient_id == patient_id)
        )
    }
    severities: dict[UUID, list[str]] = {visit_id: [] for visit_id in visits}

    for finding in db.scalars(
        select(Finding).where(Finding.patient_id == patient_id, Finding.status == "active")
    ):
        for visit_id in finding.visit_ids or []:
            if visit_id in severities:
                severities[visit_id].append(finding.severity)

    for visit_id, visit in visits.items():
        visit.severity = worst(severities[visit_id])
