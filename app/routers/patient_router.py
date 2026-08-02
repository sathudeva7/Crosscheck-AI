"""Patient (account) registration and profile.

    from app.routers.patient_router import router as patient_router
    app.include_router(patient_router)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import current_clerk_user_id, current_patient
from app.db.models import Patient
from app.db.session import get_db
from app.schemas.patient import PatientCreate, PatientRead

router = APIRouter(prefix="/patients", tags=["patients"])


@router.post("", response_model=PatientRead, status_code=status.HTTP_201_CREATED)
def create_patient(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(current_clerk_user_id),
) -> Patient:
    """Register the signed-in account as a patient. One profile per account."""
    existing = db.scalar(select(Patient).where(Patient.clerk_user_id == clerk_user_id))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A patient profile already exists for this account",
        )

    patient = Patient(clerk_user_id=clerk_user_id, **payload.model_dump())
    db.add(patient)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race against a concurrent create; the unique index held.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A patient profile already exists for this account",
        ) from None

    db.refresh(patient)  # pick up server-generated id/created_at
    return patient


@router.get("/me", response_model=PatientRead)
def read_current_patient(patient: Patient = Depends(current_patient)) -> Patient:
    """Profile of the signed-in account. 404 until registration completes."""
    return patient
