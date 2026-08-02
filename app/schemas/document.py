"""Request/response schemas for uploaded documents."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

DocType = Literal[
    "prescription",
    "lab_report",
    "discharge_summary",
    "doctor_note",
    "radiology_report",
    "pathology_report",
    "referral_letter",
    "vaccination_record",
    "operative_note",
    "mixed",
    "other",
    "unknown",
]
DocStatus = Literal["uploaded", "parsing", "transcribing", "extracting", "ready", "failed"]
Modality = Literal["digital", "scanned", "handwritten"]


class DocumentPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_no: int
    doc_type: str
    modality: Modality | None
    language: str | None
    transcription: str | None
    transcribed_at: datetime | None


class DocumentRead(BaseModel):
    """A document and its processing state. Poll this until status is 'ready'."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doc_type: DocType
    filename: str | None
    mime_type: str
    size_bytes: int | None
    page_count: int | None

    doc_date: date | None
    provider: str | None
    clinician_name: str | None
    clinician_specialty: str | None
    summary: str | None
    patient_name_extracted: str | None

    status: DocStatus
    error_message: str | None
    processing_started_at: datetime | None
    processing_completed_at: datetime | None

    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentRead):
    """DocumentRead plus the per-page transcriptions."""

    pages: list[DocumentPageRead] = []
