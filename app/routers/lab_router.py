"""Measurements over time.

    from app.routers.lab_router import router as lab_router
    app.include_router(lab_router)

One endpoint. Everything it returns is already on the timeline — this reshapes
those rows into one series per test rather than deriving anything new, so a
value corrected on the review screen is corrected here on the next load.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_patient
from app.db.models import Patient, TimelineEvent
from app.db.session import get_db
from app.labs import Series, build_series
from app.schemas.lab import LabSeries

router = APIRouter(prefix="/labs", tags=["labs"])


@router.get("", response_model=list[LabSeries])
def list_series(
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> list[Series]:
    """Every measurement this patient has, grouped into series.

    Ordered with anything currently outside its range first, then by how much
    history there is. The reason to open this screen is a result that needs
    attention, not an alphabet.
    """
    events = db.scalars(
        select(TimelineEvent).where(
            TimelineEvent.patient_id == patient.id,
            TimelineEvent.event_type.in_(("lab_result", "vital_sign")),
        )
    ).all()

    return build_series(list(events))
