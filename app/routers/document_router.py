"""Document upload and status.

    from app.routers.document_router import router as document_router
    app.include_router(document_router)

Upload returns immediately. Processing happens in the background, so clients
poll GET /documents/{id} until status is 'ready' or 'failed'.
"""

import logging
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
from app.checks.engine import run_checks
from app.config import ALLOWED_MIME_TYPES, MAX_UPLOAD_MB
from app.db.models import Document, Patient
from app.db.session import get_db
from app.ingest.pipeline import process_document
from app.ingest.timeline import rebuild_document
from app.schemas.document import DocumentDetail, DocumentFile, DocumentRead

logger = logging.getLogger(__name__)

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


def _owned(db: Session, document_id: UUID, patient: Patient) -> Document:
    """The document, if it belongs to whoever is asking. 404 otherwise.

    The same 404 for 'no such document' and 'not yours', deliberately. A
    distinct 403 would confirm that a given id exists, which is the one thing
    someone probing ids should not be able to learn.
    """
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.patient_id == patient.id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


@router.get("/{document_id}/file", response_model=DocumentFile)
def get_document_file(
    document_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> DocumentFile:
    """A link to the original, good for a few minutes.

    This is the ownership check, and it happens here rather than in the storage
    bucket. The bucket is private and answers to nobody without a signed link;
    this endpoint decides who gets one. Serving the folder statically would have
    been two lines and would have let anyone holding a patient id read that
    patient's prescriptions — the keys are laid out by patient.

    `url` is null when the file is on the local disk, which cannot mint links.
    The caller falls back to /raw, which is the same bytes through this API.

    A missing original is reported as `available: false` rather than as an
    error. Documents uploaded before object storage existed lost their file to
    a redeploy; the facts read out of them are still on the timeline, and the
    screen should say the page cannot be shown rather than look broken.
    """
    document = _owned(db, document_id, patient)

    url: str | None = None
    available = True
    try:
        url = storage.signed_url(document.storage_path)
    except FileNotFoundError:
        available = False
    except storage.StorageError:
        # Storage being unreachable is not the same as the file being gone, and
        # must not be reported as though the document were lost.
        logger.exception("could not sign %s", document.storage_path)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Document storage is unavailable"
        ) from None

    return DocumentFile(
        url=url,
        available=available,
        filename=document.filename,
        mime_type=document.mime_type,
        page_count=document.page_count,
    )


@router.get("/{document_id}/raw")
def get_document_raw(
    document_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> Response:
    """The original bytes, through this API.

    The fallback for local-disk storage, and the reason the viewer works the
    same in development as it does deployed. Inline rather than as an
    attachment: this is for looking at, not for downloading.
    """
    document = _owned(db, document_id, patient)
    try:
        data = storage.read(document.storage_path)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_410_GONE, "The original file is no longer stored") from None
    except storage.StorageError:
        logger.exception("could not read %s", document.storage_path)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Document storage is unavailable"
        ) from None

    return Response(
        content=data,
        media_type=document.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{document.filename or document_id}"',
            # Private: this is one patient's medical record, and a shared proxy
            # holding a copy is exactly what must not happen.
            "Cache-Control": "private, max-age=300",
        },
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> Response:
    """Remove a document and everything read out of it.

    Genuinely destructive, and the only destructive operation in the API.
    Rejecting a timeline row hides it and keeps it; this does not. It exists
    because the other kind of mistake needs a different remedy: a report
    belonging to someone else, or a duplicate upload, should leave no trace
    rather than sit in a review queue forever.

    The visits and extracted facts go with it by cascade — timeline_visit
    references document ON DELETE CASCADE, and timeline_event references the
    visit the same way, so removing the row at the top removes the branch.

    Findings are then recomputed, because a finding is a statement about facts
    that no longer exist. Leaving them would warn about an interaction between
    two drugs, one of which is no longer on the record.
    """
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.patient_id == patient.id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    stored = document.storage_path
    db.delete(document)
    db.commit()

    # After the commit, and deliberately. A file removed before the row is
    # gone is a file missing from a document that still exists — the failure
    # mode that leaves a page unreadable rather than absent.
    storage.delete(stored)

    await run_checks(db, patient.id)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/rebuild", response_model=DocumentRead)
async def rebuild(
    document_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> Document:
    """Rewrite this document's timeline rows from the extraction already stored.

    No model call, no cost, no network. Everything it needs is in
    `extraction_json`, which is why this exists at all: when the mapping from
    extraction to timeline changes — a field that used to be dropped now
    becoming an event — every document read before the change is missing rows
    it should have. Re-uploading would fix that too, but at the price of
    transcribing and extracting again for facts that were read correctly the
    first time.

    Corrections survive. `rebuild_document` leaves rows a person has confirmed,
    corrected or rejected exactly as they are.
    """
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.patient_id == patient.id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    if not (document.extraction_json or {}).get("groups"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This document has no stored extraction to rebuild from. Upload it again.",
        )

    result = rebuild_document(db, document)
    db.commit()
    logger.info(
        "rebuilt %s: %d new, %d updated, %d left alone",
        document.id,
        result.events,
        result.updated,
        result.skipped,
    )

    # The timeline changed, so findings computed from it are out of date.
    await run_checks(db, patient.id)
    db.commit()

    db.refresh(document)
    return document
