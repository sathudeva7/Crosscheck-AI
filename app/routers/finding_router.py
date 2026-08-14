"""What the cross-checks found, and dismissing findings.

    from app.routers.finding_router import router as finding_router
    app.include_router(finding_router)

The listing always reports how many medicines could actually be checked. A
result with no findings is only reassuring if everything was looked at, and a
prescription where nothing could be identified must not read the same as one
that came back clean.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_patient
from app.checks.engine import colour_visits, run_checks
from app.db.models import Finding, InteractionRule, Patient, TimelineEvent
from app.db.session import get_db
from app.schemas.finding import (
    DismissRequest,
    FindingListRead,
    FindingRead,
    CheckCoverage,
)

router = APIRouter(prefix="/findings", tags=["findings"])

SEVERITY_RANK = {"danger": 0, "warning": 1, "info": 2}


@router.get("", response_model=FindingListRead)
def list_findings(
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
    include_dismissed: bool = Query(False),
    severity: str | None = Query(None, description="'danger', 'warning' or 'info'"),
) -> FindingListRead:
    """Everything found across the record, most serious first."""
    query = select(Finding).where(Finding.patient_id == patient.id)
    if not include_dismissed:
        query = query.where(Finding.status == "active")
    if severity:
        query = query.where(Finding.severity == severity)

    findings = sorted(
        db.scalars(query),
        key=lambda f: (SEVERITY_RANK.get(f.severity, 9), f.title),
    )

    scores = _confidences(db, findings)

    return FindingListRead(
        coverage=_coverage(db, patient.id),
        counts={
            level: sum(1 for f in findings if f.severity == level)
            for level in ("danger", "warning", "info")
        },
        findings=[
            FindingRead.model_validate(f).model_copy(update={"confidence": scores.get(f.id)})
            for f in findings
        ],
    )


def _confidences(db: Session, findings: list[Finding]) -> dict[UUID, float]:
    """How well each finding's underlying rows were read.

    A finding is as sound as the shakiest fact it stands on: an interaction
    between two drugs, one of them read off a handwritten prescription, cannot
    be reported as firmly as the same pair read from two printed ones. Taking
    the minimum is the same rule the answer confidence uses in app/rag/agent.py,
    and it must stay the same — two numbers on the same subject that disagree
    are worse than one.

    This is not a probability that the finding is real. Whether it exists was
    decided by SQL against the interaction rules; this says only how well we
    read the drugs it is about.
    """
    wanted = {event_id for finding in findings for event_id in finding.event_ids}
    if not wanted:
        return {}

    read = dict(
        db.execute(
            select(TimelineEvent.id, TimelineEvent.confidence).where(
                TimelineEvent.id.in_(wanted)
            )
        ).all()
    )

    scores: dict[UUID, float] = {}
    for finding in findings:
        known = [
            read[event_id]
            for event_id in finding.event_ids
            if read.get(event_id) is not None
        ]
        if known:
            scores[finding.id] = float(min(known))
    return scores


def _coverage(db: Session, patient_id: UUID) -> CheckCoverage:
    """How much of the record the checks were able to see."""
    medicines = list(
        db.scalars(
            select(TimelineEvent).where(
                TimelineEvent.patient_id == patient_id,
                TimelineEvent.event_type == "medication",
                TimelineEvent.review_status != "rejected",
            )
        )
    )
    checked = [m for m in medicines if m.rxcui]
    return CheckCoverage(
        medicines_total=len(medicines),
        medicines_checked=len(checked),
        unchecked=[m.display_text or m.drug_raw or "unnamed" for m in medicines if not m.rxcui],
        rules_available=db.scalar(select(func.count()).select_from(InteractionRule)) or 0,
    )


@router.post("/recheck", response_model=FindingListRead)
async def recheck(
    rewrite: bool = Query(
        False,
        description=(
            "Also rewrite explanations that already exist. Off by default: a "
            "warning is worded once and kept, so that the same finding does not "
            "read differently each time the page is opened. Turn it on after the "
            "evidence findings carry has changed, since text written against the "
            "old shape is never revisited otherwise."
        ),
    ),
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> FindingListRead:
    """Run the checks again.

    Normally unnecessary — ingestion runs them after every upload. Useful after
    correcting a drug name by hand, since identifying a medicine is exactly
    what lets it be checked at all.
    """
    await run_checks(db, patient.id, rewrite_explanations=rewrite)
    # Passed explicitly: called as a plain function, the Query(...) defaults
    # would arrive as Query objects rather than values.
    return list_findings(db=db, patient=patient, include_dismissed=False, severity=None)


@router.post("/{finding_id}/dismiss", response_model=FindingRead)
def dismiss_finding(
    finding_id: UUID,
    body: DismissRequest | None = None,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> Finding:
    """Set a finding aside.

    Kept rather than deleted, and kept even once its cause disappears: a
    warning considered and waved away must not reappear because a document was
    re-read.
    """
    finding = _get(db, patient, finding_id)
    finding.status = "dismissed"
    finding.dismissed_at = func.now()
    if body and body.reason:
        finding.dismissed_reason = body.reason
    # Dismissing the last finding on a visit has to clear its dot, or the
    # timeline keeps showing a warning with nothing behind it.
    colour_visits(db, patient.id)
    db.commit()
    db.refresh(finding)
    return finding


@router.post("/{finding_id}/restore", response_model=FindingRead)
def restore_finding(
    finding_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> Finding:
    """Undo a dismissal."""
    finding = _get(db, patient, finding_id)
    finding.status = "active"
    finding.dismissed_at = None
    finding.dismissed_reason = None
    colour_visits(db, patient.id)
    db.commit()
    db.refresh(finding)
    return finding


def _get(db: Session, patient: Patient, finding_id: UUID) -> Finding:
    finding = db.scalar(
        select(Finding).where(Finding.id == finding_id, Finding.patient_id == patient.id)
    )
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such finding")
    return finding
