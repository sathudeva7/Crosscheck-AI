"""Document upload and status.

    from app.routers.document_router import router as document_router
    app.include_router(document_router)

Upload returns immediately. Processing happens in the background, so clients
poll GET /documents/{id} until status is 'ready' or 'failed'.
"""

from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import storage
from app.auth import current_patient
from app.config import ALLOWED_MIME_TYPES, MAX_UPLOAD_MB
from app.db.models import Document, Patient
from app.db.session import get_db
from app.ingest.pipeline import process_document
from app.schemas.document import DocumentDetail, DocumentRead

router = APIRouter(prefix="/documents", tags=["documents"])

_MAX_BYTES = MAX_UPLOAD_MB * 1024 * 1024


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    response: Response,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> Document:
    """Store an uploaded document and queue it for processing."""
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type {file.content_type!r}. "
            f"Accepted: {', '.join(sorted(ALLOWED_MIME_TYPES))}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {MAX_UPLOAD_MB} MB limit",
        )

    sha256 = storage.compute_sha256(data)

    # Same bytes already uploaded: hand back the existing document rather than
    # processing it twice and duplicating the timeline.
    existing = db.scalar(
        select(Document).where(
            Document.patient_id == patient.id, Document.sha256 == sha256
        )
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    storage_path = storage.save(patient.id, sha256, file.content_type, data)

    document = Document(
        patient_id=patient.id,
        filename=file.filename,
        mime_type=file.content_type,
        storage_path=storage_path,
        sha256=sha256,
        size_bytes=len(data),
        status="uploaded",
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    background.add_task(process_document, document.id)

    return document


@router.get("", response_model=list[DocumentRead])
def list_documents(
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> list[Document]:
    """All documents for the signed-in patient, newest first."""
    return list(
        db.scalars(
            select(Document)
            .where(Document.patient_id == patient.id)
            .order_by(Document.created_at.desc())
        )
    )


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> Document:
    """One document with its pages. Poll this for processing status."""
    document = db.scalar(
        select(Document)
        .options(selectinload(Document.pages))
        .where(Document.id == document_id, Document.patient_id == patient.id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document
