"""The patient's merged timeline, and the queue of things to check.

    from app.routers.timeline_router import router as timeline_router
    app.include_router(timeline_router)

Both endpoints read `timeline_visit` and `timeline_event`, which ingestion
fills in. Nothing here reads `extraction_json` — that stays as the record of
what the model said, and is not what the screen is built from.

Every query is filtered by the signed-in patient. The filter is applied here,
in the query, rather than checked after loading: a row belonging to someone
else must never be fetched at all.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import current_patient
from app.db.models import Patient, TimelineEvent, TimelineVisit
from app.db.session import get_db
from app.ingest.drugs import resolve
from app.ingest.timeline import refresh_display
from app.schemas.timeline import (
    RejectRequest,
    ReviewItemRead,
    ReviewQueueRead,
    TimelineCounts,
    TimelineEventCreate,
    TimelineEventRead,
    TimelineEventUpdate,
    TimelineRead,
    TimelineVisitRead,
)


def _now() -> datetime:
    return datetime.now(UTC)

router = APIRouter(prefix="/timeline", tags=["timeline"])

# Rejected rows are kept so a mistaken rejection can be undone, but they are
# not part of the record the patient reads.
HIDDEN = ("rejected",)


@router.get("", response_model=TimelineRead)
def get_timeline(
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
    since: date | None = Query(None, description="Only visits on or after this date"),
    until: date | None = Query(None, description="Only visits on or before this date"),
    newest_first: bool = Query(False, description="Reverse the order"),
    include_rejected: bool = Query(False, description="Include rows you rejected"),
) -> TimelineRead:
    """Every visit in date order, with its facts.

    Visits with no date sort last: an undated document is real and must still
    be reachable, but it cannot be placed on the timeline.
    """
    query = (
        select(TimelineVisit)
        .options(selectinload(TimelineVisit.events))
        .where(TimelineVisit.patient_id == patient.id)
    )
    if since is not None:
        query = query.where(TimelineVisit.visit_date >= since)
    if until is not None:
        query = query.where(TimelineVisit.visit_date <= until)

    order = TimelineVisit.visit_date.desc() if newest_first else TimelineVisit.visit_date.asc()
    query = query.order_by(order.nullslast(), TimelineVisit.created_at)

    visits = []
    for visit in db.scalars(query):
        events = [
            event
            for event in visit.events
            if include_rejected or event.review_status not in HIDDEN
        ]
        # Within a card, group by kind and then run in date order — a chart's
        # ten readings should read as a series, not in extraction order.
        events.sort(key=lambda e: (e.event_type, e.event_date or date.min, e.title or ""))
        model = TimelineVisitRead.model_validate(visit)
        model.events = [TimelineEventRead.model_validate(event) for event in events]
        visits.append(model)

    counts = _counts(db, patient.id, include_rejected)
    counts.visits = len(visits)
    return TimelineRead(counts=counts, visits=visits)


def _counts(db: Session, patient_id: UUID, include_rejected: bool) -> TimelineCounts:
    """The figures across the top, counted in the database rather than in Python."""
    query = select(TimelineEvent.event_type, TimelineEvent.review_status, func.count()).where(
        TimelineEvent.patient_id == patient_id
    )
    if not include_rejected:
        query = query.where(TimelineEvent.review_status.not_in(HIDDEN))
    rows = db.execute(query.group_by(TimelineEvent.event_type, TimelineEvent.review_status)).all()

    return TimelineCounts(
        visits=0,  # filled in by the caller, which knows the date filter
        medications=sum(n for kind, _, n in rows if kind == "medication"),
        lab_values=sum(n for kind, _, n in rows if kind in ("lab_result", "vital_sign")),
        needs_review=sum(n for _, status, n in rows if status == "needs_review"),
    )


@router.get("/review", response_model=ReviewQueueRead)
def get_review_queue(
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
    risk: str | None = Query(None, description="Only 'high', 'medium' or 'low'"),
    limit: int = Query(100, ge=1, le=500),
) -> ReviewQueueRead:
    """What still needs a human eye, least certain first.

    High-risk items lead regardless of score: a medicine at 0.70 matters more
    than a recorded weight at 0.50.
    """
    query = (
        select(TimelineEvent, TimelineVisit)
        .join(TimelineVisit, TimelineEvent.visit_id == TimelineVisit.id)
        .where(
            TimelineEvent.patient_id == patient.id,
            TimelineEvent.review_status == "needs_review",
        )
    )
    if risk:
        query = query.where(TimelineEvent.risk == risk)

    # Sorting on the column itself would order high, low, medium alphabetically.
    risk_rank = case({"high": 0, "medium": 1, "low": 2}, value=TimelineEvent.risk, else_=3)
    rows = db.execute(
        query.order_by(risk_rank, TimelineEvent.confidence.asc().nullsfirst()).limit(limit)
    ).all()

    items = []
    for event, visit in rows:
        item = ReviewItemRead.model_validate(event)
        item.visit_date = visit.visit_date
        item.visit_doc_type = visit.doc_type
        item.visit_provider = visit.provider
        items.append(item)

    # `total` counts what this request is looking at, so it can be paged
    # against; `high_risk` always counts the whole queue, because it is the
    # badge that tells someone there is urgent work whatever they are viewing.
    counted = select(func.count()).select_from(TimelineEvent).where(
        TimelineEvent.patient_id == patient.id,
        TimelineEvent.review_status == "needs_review",
    )
    total = db.scalar(counted.where(TimelineEvent.risk == risk) if risk else counted)
    high_risk = db.scalar(
        select(func.count())
        .select_from(TimelineEvent)
        .where(
            TimelineEvent.patient_id == patient.id,
            TimelineEvent.review_status == "needs_review",
            TimelineEvent.risk == "high",
        )
    )
    return ReviewQueueRead(total=total or 0, high_risk=high_risk or 0, items=items)


# --- reviewing ----------------------------------------------------------------


def _get_event(db: Session, patient: Patient, event_id: UUID) -> TimelineEvent:
    """Fetch one of this patient's events, or 404.

    The patient filter is part of the lookup, so another patient's id returns
    'not found' rather than 'forbidden' — which is also the correct answer,
    since its existence is not theirs to learn.
    """
    event = db.scalar(
        select(TimelineEvent).where(
            TimelineEvent.id == event_id, TimelineEvent.patient_id == patient.id
        )
    )
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item on your timeline")
    return event


def _record_edits(event: TimelineEvent, changes: dict[str, Any]) -> dict[str, Any]:
    """Note what changed and what it was, before overwriting it.

    Kept on the row so a correction can be explained or undone, and so the
    accuracy of extraction can be measured against what people actually fix.
    """
    history = dict(event.edited_fields or {})
    for field, value in changes.items():
        previous = getattr(event, field, None)
        if previous == value:
            continue
        history[field] = {
            "from": str(previous) if previous is not None else None,
            "to": str(value) if value is not None else None,
        }
    return history


@router.patch("/{event_id}", response_model=TimelineEventRead)
async def correct_event(
    event_id: UUID,
    changes: TimelineEventUpdate,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> TimelineEvent:
    """Fix a value the app got wrong.

    Only the fields you send are touched. The row is marked 'corrected' and
    owned by you from then on, so re-processing the document will never
    overwrite it.
    """
    event = _get_event(db, patient, event_id)
    values = changes.model_dump(exclude_unset=True)
    note = values.pop("note", None)

    if not values and note is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to change")

    event.edited_fields = _record_edits(event, values)
    for field, value in values.items():
        setattr(event, field, value)

    # Correcting the drug name is the whole point of the review queue: it turns
    # an unidentified medicine into one the safety checks can actually use.
    if "drug_raw" in values or "drug_name" in values:
        if "drug_raw" in values and "title" not in values:
            event.title = values["drug_raw"]
        identity = await resolve(values.get("drug_name") or values.get("drug_raw") or "")
        if identity.verified:
            event.drug_name = identity.ingredients[0]
            event.rxcui = identity.rxcuis[0]
            event.atc_codes = [a["code"] for a in identity.atc if a.get("code")] or None
        else:
            event.rxcui = None
            event.atc_codes = None

    if note:
        event.details = {**(event.details or {}), "correction_note": note}

    refresh_display(event)
    event.review_status = "corrected"
    event.source = "user"
    event.reviewed_at = _now()
    db.commit()
    db.refresh(event)
    return event


@router.post("/{event_id}/approve", response_model=TimelineEventRead)
def approve_event(
    event_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> TimelineEvent:
    """Confirm the app read this correctly.

    A person's confirmation outranks any score, so the confidence is left as it
    was rather than being rewritten to 1 — the number stays an honest record of
    how the fact was read, and the status says it was checked.
    """
    event = _get_event(db, patient, event_id)
    event.review_status = "confirmed"
    event.reviewed_at = _now()
    db.commit()
    db.refresh(event)
    return event


@router.post("/{event_id}/reject", response_model=TimelineEventRead)
def reject_event(
    event_id: UUID,
    body: RejectRequest | None = None,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> TimelineEvent:
    """Take an item off the timeline: not the patient's, or cancelled.

    Hidden rather than deleted, so a mistaken rejection can be undone and so a
    cancelled drug stays visible as history if it is ever needed.
    """
    event = _get_event(db, patient, event_id)
    event.review_status = "rejected"
    event.reviewed_at = _now()
    if body and body.reason:
        event.details = {**(event.details or {}), "rejected_reason": body.reason}
    db.commit()
    db.refresh(event)
    return event


@router.post("/{event_id}/reopen", response_model=TimelineEventRead)
def reopen_event(
    event_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> TimelineEvent:
    """Undo a decision and put the item back in the queue.

    A row you edited stays yours: the correction is kept, and re-processing
    still will not overwrite it.
    """
    event = _get_event(db, patient, event_id)
    event.review_status = "needs_review"
    event.reviewed_at = None
    db.commit()
    db.refresh(event)
    return event


@router.post("", response_model=TimelineEventRead, status_code=status.HTTP_201_CREATED)
async def add_event(
    body: TimelineEventCreate,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> TimelineEvent:
    """Add something the app missed.

    A medicine that was never extracted is worse than one extracted wrongly:
    nothing flags an absence, and every safety check silently skips a drug it
    does not know about.
    """
    values = body.model_dump(exclude_unset=True)
    for key in ("event_type", "visit_id", "visit_date", "note"):
        values.pop(key, None)

    visit = _visit_for(db, patient, body)

    event = TimelineEvent(
        patient_id=patient.id,
        visit_id=visit.id,
        document_id=visit.document_id,
        event_type=body.event_type,
        event_date=body.event_date or visit.visit_date,
        source="user",
        # Typed in by the person whose record it is: there is nothing to review.
        review_status="confirmed",
        risk="high" if body.event_type in ("medication", "allergy") else "low",
        reviewed_at=_now(),
        details={"correction_note": body.note} if body.note else {},
    )
    for field, value in values.items():
        setattr(event, field, value)

    if event.event_type == "medication" and (event.drug_name or event.drug_raw):
        identity = await resolve(event.drug_name or event.drug_raw)
        if identity.verified:
            event.drug_name = identity.ingredients[0]
            event.rxcui = identity.rxcuis[0]
            event.atc_codes = [a["code"] for a in identity.atc if a.get("code")] or None

    refresh_display(event)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _visit_for(db: Session, patient: Patient, body: TimelineEventCreate) -> TimelineVisit:
    """The card a hand-added fact belongs to, creating one if needed."""
    if body.visit_id is not None:
        visit = db.scalar(
            select(TimelineVisit).where(
                TimelineVisit.id == body.visit_id, TimelineVisit.patient_id == patient.id
            )
        )
        if visit is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such visit on your timeline")
        return visit

    when = body.visit_date or body.event_date
    # One manual card per date, so adding three forgotten drugs from the same
    # appointment does not produce three cards.
    visit = db.scalar(
        select(TimelineVisit).where(
            TimelineVisit.patient_id == patient.id,
            TimelineVisit.document_id.is_(None),
            TimelineVisit.visit_date == when,
        )
    )
    if visit is None:
        visit = TimelineVisit(
            patient_id=patient.id,
            visit_date=when,
            doc_type="other",
            summary="Added by hand",
            source="user",
        )
        db.add(visit)
        db.flush()
    return visit
