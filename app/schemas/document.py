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


class DocumentFile(BaseModel):
    """Where to fetch the original, and whether there is one.

    `url` is a signed link that expires in minutes — good enough to open a scan
    and stale by the time it could be shared. It is null when the file lives on
    the local disk, which has no such thing; the caller then reads the bytes
    from /documents/{id}/raw instead.

    `available` is false when the document row survives but its file does not.
    That is a real state, not an error: documents uploaded before object storage
    existed lost their originals to a container redeploy, while everything read
    out of them is still on the timeline. The screen says the page cannot be
    shown; it does not pretend the document was never there.
    """

    url: str | None = None
    available: bool = True
    filename: str | None = None
    mime_type: str
    page_count: int | None = None
