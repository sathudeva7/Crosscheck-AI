"""Run a document through ingestion.

Owns the order of steps and the status transitions; the steps themselves know
nothing about each other or about the database.

    uploaded -> parsing -> transcribing -> extracting -> ready | failed

Runs in the background after upload, so it opens its own database session —
the request's session is already closed by the time this starts.

Transcriptions are reused on re-runs unless `force=True`. Reading a scanned
page costs a vision call, and re-running the pipeline after a code change
should not pay for it twice.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import storage
from app.db.models import Document, DocumentPage
from app.db.session import SessionLocal
from app.ingest.classify import PageClassification, classify_document, document_type
from app.ingest.extract import ExtractedGroup, extract_document, group_pages
from app.ingest.drugs import resolve_many
from app.ingest.ground import ground_extraction, summarise
from app.ingest.normalize import ParsedDate, document_date, normalize_extraction
from app.ingest.review import review_group
from app.checks.engine import run_checks
from app.ingest.timeline import rebuild_document
from app.ingest.review import summarise as review_summary
from app.ingest.pages import DocumentError, open_document
from app.ingest.transcribe import TranscribedPage, TranscriptionError, transcribe_document
from app.ingest.triage import TriageResult, triage_document

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _set_status(db: Session, document: Document, status: str, **fields) -> None:
    """Commit a status change immediately so pollers see progress."""
    document.status = status
    for key, value in fields.items():
        setattr(document, key, value)
    db.commit()


def _cached_page(existing: DocumentPage) -> TranscribedPage:
    return TranscribedPage(
        page_no=existing.page_no,
        transcription=existing.transcription or "",
        modality=existing.modality or "digital",
        language=existing.language,
        fully_legible=True,
        model=existing.transcription_model,
    )


def _save_page(
    db: Session,
    document: Document,
    existing: dict[int, DocumentPage],
    triage: TriageResult,
    result: TranscribedPage | None,
    char_count: int,
    non_alnum_ratio: float,
) -> None:
    """Insert or update one document_page row."""
    page = existing.get(triage.page_no)
    if page is None:
        page = DocumentPage(document_id=document.id, page_no=triage.page_no)
        db.add(page)
        existing[triage.page_no] = page

    page.char_count = char_count
    page.non_alnum_ratio = non_alnum_ratio

    if result is not None:
        page.modality = result.modality
        page.language = result.language
        page.transcription = result.transcription
        page.transcription_model = result.model
        page.transcribed_at = _now()
    else:
        # transcription failed; keep the triage verdict so a retry knows the
        # page still needs vision
        page.modality = triage.modality


async def _classify(
    db: Session,
    document: Document,
    results: dict[int, TranscribedPage | None],
    pages: dict[int, DocumentPage],
    force: bool,
) -> list[PageClassification]:
    """Work out what kind of document each page is, and the document as a whole.

    Best-effort: a page that cannot be placed is left 'unknown', which
    extraction reads as "use the generic form".
    """
    ordered = sorted(pages)
    transcriptions = [
        (page_no, results[page_no].transcription if results.get(page_no) else "")
        for page_no in ordered
    ]
    known = None if force else {page_no: pages[page_no].doc_type for page_no in ordered}

    classifications = await classify_document(transcriptions, known)

    for item in classifications:
        pages[item.page_no].doc_type = item.doc_type
        logger.debug(
            "page %s of %s -> %s (%s, %.2f)",
            item.page_no,
            document.id,
            item.doc_type,
            item.method,
            item.confidence,
        )
    document.doc_type = document_type(classifications)
    db.commit()
    return classifications


async def _extract(
    db: Session,
    document: Document,
    classifications: list[PageClassification],
    results: dict[int, TranscribedPage | None],
) -> list[int]:
    """Fill in the extraction forms and record what the model returned.

    The raw result is stored as-is and never edited afterwards: later
    corrections change the timeline built from it, not this record of what the
    model actually said. Returns the pages of any group that failed.
    """
    transcriptions = {
        page_no: (result.transcription if result else "") for page_no, result in results.items()
    }
    # How readable each page was — the third confidence signal, and the only
    # one that comes from the page rather than from the extraction.
    modalities = {
        page_no: result.modality for page_no, result in results.items() if result
    }
    groups = group_pages(classifications, transcriptions)
    if not groups:
        return []

    outcomes = await extract_document(groups)

    extracted: list[ExtractedGroup] = []
    failed: list[int] = []
    for group, outcome in zip(groups, outcomes):
        if isinstance(outcome, BaseException):
            logger.warning("extraction failed for %s of %s: %s", group.page_span, document.id, outcome)
            failed.extend(group.pages)
        else:
            extracted.append(outcome)

    payloads = []
    dates: list[ParsedDate] = []
    for item in extracted:
        # Check the model quoted the pages it claims to have read, and record
        # which page each item came from.
        groundings = ground_extraction(
            item.data, {page_no: transcriptions.get(page_no, "") for page_no in item.pages}
        )
        stats = summarise(groundings)
        if stats["ungrounded"]:
            logger.warning(
                "%s of %s: %s of %s items are not in the transcription",
                item.doc_type,
                document.id,
                stats["ungrounded"],
                stats["items"],
            )

        # Parse the printed values into comparable ones. Keyed by the same
        # paths as the grounding, so a value and its verification line up.
        parsed = normalize_extraction(item.data)
        when = document_date(item.data)

        # Turn each printed drug name into an identity: ingredient, RxNorm id
        # and WHO class. Without it two brand names of one drug look like two
        # different medicines.
        identities = await resolve_many(
            [m.drug for m in getattr(item.data, "medications", []) if m.drug]
        )

        payload = item.to_json()
        payload["grounding"] = [g.to_json() for g in groundings]
        payload["grounding_summary"] = stats
        payload["normalized"] = parsed
        payload["drugs"] = {name: identity.to_json() for name, identity in identities.items()}

        # Weigh all of the above into one confidence per fact, and decide what
        # a human should check. Last, because it needs every other step's work.
        judgements = review_group(
            item.data,
            payload["grounding"],
            parsed,
            payload["drugs"],
            modalities,
            item.pages,
        )
        payload["review"] = [j.to_json() for j in judgements]
        payload["review_summary"] = review_summary(judgements)
        payload["date"] = {
            "value": when.value.isoformat() if when.value else None,
            "raw": when.raw,
            "ambiguous": when.ambiguous,
            "reason": when.reason,
        }
        payloads.append(payload)
        dates.append(when)

    document.extraction_json = {
        "extracted_at": _now().isoformat(),
        "groups": payloads,
        "failed_pages": failed,
    }

    # Mirror the first group's header onto the document so it can be listed and
    # sorted without opening the JSON.
    if extracted:
        header = extracted[0].data.header
        document.provider = header.provider
        document.clinician_name = header.clinician_name
        document.clinician_specialty = header.clinician_specialty
        document.patient_name_extracted = header.patient_name
        document.summary = header.summary

    # An upload holding several documents gets the earliest date of the lot, so
    # it lands on the timeline where its oldest content belongs.
    parsed_dates = [d.value for d in dates if d.value]
    document.doc_date = min(parsed_dates) if parsed_dates else None

    db.commit()
    return failed


def _others_still_processing(db: Session, document: Document) -> bool:
    """Whether another of this patient's documents is still being read.

    Used to defer the cross-check to whichever upload finishes last. The
    document being processed right now is excluded — its own status is not
    'ready' yet, because that is set after this runs.

    Racy by nature and harmlessly so. Two documents finishing in the same
    instant can both see the other as done and both run the check; the result
    is one redundant pass, which is what happened on every upload before. The
    opposite mistake — both deferring and neither running — is the one that
    would matter, and it cannot happen here: a document only counts as still
    processing while its status says so, and that status is cleared after this
    point in its own run.
    """
    return db.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.patient_id == document.patient_id,
            Document.id != document.id,
            Document.status.notin_(("ready", "failed")),
        )
    ) > 0


async def process_document(
    document_id: UUID, force: bool = False, retranscribe: bool = False
) -> None:
    """Parse, triage, transcribe and classify, updating status as it goes.

    `force` re-runs a document that is already 'ready'. `retranscribe` also
    throws away its cached transcriptions — the only part that costs vision
    calls, so it is off by default: re-running after a change to classification
    or extraction should not pay for reading the pages again.
    """
    db = SessionLocal()
    try:
        document = db.get(Document, document_id)
        if document is None:
            logger.warning("process_document: %s not found", document_id)
            return

        force = force or retranscribe
        if document.status == "ready" and not force:
            return

        _set_status(db, document, "parsing", processing_started_at=_now(), error_message=None)

        try:
            data = storage.read(document.storage_path)
        except FileNotFoundError:
            _set_status(
                db, document, "failed", error_message="The uploaded file is no longer available"
            )
            return

        try:
            with open_document(data, document.mime_type) as loaded:
                triages = triage_document(loaded.pages)
                document.page_count = loaded.page_count
                db.commit()

                existing = {
                    page.page_no: page
                    for page in db.scalars(
                        select(DocumentPage).where(DocumentPage.document_id == document.id)
                    )
                }

                # Skip pages already transcribed — the expensive part
                pending = [
                    (page, triage)
                    for page, triage in zip(loaded.pages, triages)
                    if retranscribe or existing.get(page.page_no) is None
                    or existing[page.page_no].transcription is None
                ]

                if pending:
                    _set_status(db, document, "transcribing")
                    outcomes = await transcribe_document(
                        [page for page, _ in pending], [triage for _, triage in pending]
                    )
                else:
                    outcomes = []

                results: dict[int, TranscribedPage | None] = {
                    page_no: _cached_page(page)
                    for page_no, page in existing.items()
                    if page.transcription is not None and not retranscribe
                }
                failures: list[int] = []
                unextracted: list[int] = []
                for (page, _), outcome in zip(pending, outcomes):
                    if isinstance(outcome, BaseException):
                        logger.warning(
                            "page %s of %s failed: %s", page.page_no, document.id, outcome
                        )
                        results[page.page_no] = None
                        failures.append(page.page_no)
                    else:
                        results[page.page_no] = outcome

                for page, triage in zip(loaded.pages, triages):
                    _save_page(
                        db,
                        document,
                        existing,
                        triage,
                        results.get(page.page_no),
                        page.char_count,
                        page.non_alnum_ratio,
                    )
                db.commit()

                _set_status(db, document, "extracting")
                classifications = await _classify(db, document, results, existing, force)
                if force or document.extraction_json is None:
                    unextracted = await _extract(db, document, classifications, results)

                # Turn the extraction into timeline rows. Safe to repeat: rows
                # are keyed by extraction path, and any the user has decided on
                # are left untouched.
                if document.extraction_json:
                    built = rebuild_document(db, document)
                    logger.info("timeline for %s: %s", document.id, built)

                    # Re-check the whole record, not just this upload: a
                    # prescription added today may conflict with one from
                    # years ago, which is the pair nobody is looking at.
                    #
                    # Once per batch, not once per document. The check reads
                    # the patient's entire record either way, so running it
                    # after each of five uploads does the same work five times
                    # and throws four of them away — measured at 18.6s a run,
                    # which was close to half the wait on a five-file upload.
                    # Whichever document finishes last is the one that runs it,
                    # and by then it has everything to check.
                    if _others_still_processing(db, document):
                        logger.info(
                            "checks for %s deferred: another document is still processing",
                            document.id,
                        )
                    else:
                        checks = await run_checks(db, document.patient_id)
                        logger.info(
                            "checks for %s: %s findings, %s/%s medicines checked",
                            document.patient_id, checks.findings,
                            checks.medicines_checked, checks.medicines_total,
                        )

        except DocumentError as exc:
            # Password protected, corrupt, too many pages — all messages a
            # user can act on
            _set_status(db, document, "failed", error_message=str(exc))
            return

        if failures and len(failures) == len(loaded.pages):
            _set_status(
                db,
                document,
                "failed",
                error_message="No page of this document could be read",
                processing_completed_at=_now(),
            )
            return

        # Both are partial failures, not fatal ones: an unreadable page still
        # leaves the rest of the document usable, and a page that was read but
        # not extracted still has its transcription for search.
        notes = []
        if failures:
            notes.append(f"Pages {', '.join(map(str, failures))} could not be read")
        if unextracted:
            notes.append(f"No data could be extracted from pages {', '.join(map(str, unextracted))}")

        _set_status(
            db,
            document,
            "ready",
            processing_completed_at=_now(),
            error_message="; ".join(notes) or None,
        )

    except Exception:  # noqa: BLE001 - never let a background task die silently
        logger.exception("ingestion failed for %s", document_id)
        document = db.get(Document, document_id)
        if document is not None:
            db.rollback()
            _set_status(
                db,
                document,
                "failed",
                error_message="Processing failed unexpectedly",
                processing_completed_at=_now(),
            )
    finally:
        db.close()
