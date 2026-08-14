"""Finding a real doctor for a finding the cross-checks flagged.

    from app.routers.referral_router import router as referral_router
    app.include_router(referral_router)

Three endpoints. `/for-finding/{id}` answers 'who should look at this?' from
the record alone, so the interface can show the suggestion beside the warning
before asking the patient anything. Then `/doctors` and `/clinics` take the
answers to the two questions — where are you, and when can you go.

Those last two are separate rather than one call, because they read directories
with very different response times and answer different questions. eChannelling
takes seconds and lists people you can book; Google answers in milliseconds and
lists places you can walk into, including the government hospitals eChannelling
does not carry at all. Called together, the clinics paint while the doctors are
still loading, instead of a reader waiting on the slower of the two for
everything.

Nothing about a doctor or a clinic is written to the database at any point. The
directories are read while the request is being served and the results are gone
when it returns, which is what Google's terms require and the only way a clinic
that closed last week stops being recommended.
"""

import logging
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_patient
from app.config import (
    ENABLE_DOC990,
    ENABLE_ECHANNELLING,
    ENABLE_PLACES,
    GOOGLE_MAPS_API_KEY,
)
from app.db.models import Document, Finding, Patient, TimelineEvent
from app.db.session import get_db
from app.referral import report as report_recommendation
from app.referral import search as referral_search
from app.referral.providers import doc990
from app.referral.providers import echannelling as ech
from app.referral.providers.places import PlacesClient, PlacesUnavailable
from app.referral.specialty import Specialty, resolve
from app.schemas.referral import (
    AttemptRead,
    LocationSuggestions,
    ProviderHealth,
    ReferralHealthRead,
    ReferralRead,
    ReferralSearchRead,
    ReferralSearchRequest,
    SpecialtyListRead,
    SpecialtyRead,
)
from app.schemas.report import (
    ReadingRead,
    ReportRecommendationRead,
    SuggestedSpecialtyRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/referrals", tags=["referrals"])

ATTRIBUTION = {
    "echannelling": "Doctor listings from eChannelling",
    "doc990": "Doctor listings from Doc990 (doc.lk)",
    "google_places": "Place data © Google",
}


def _finding(db: Session, patient: Patient, finding_id: UUID) -> Finding:
    finding = db.scalar(
        select(Finding).where(Finding.id == finding_id, Finding.patient_id == patient.id)
    )
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such finding")
    return finding


def _atc_codes(db: Session, finding: Finding) -> list[str]:
    """The drug classes this finding rests on.

    Read from the timeline events rather than from the finding's evidence: the
    evidence carries whichever codes the check happened to record, and the
    events carry all of them.
    """
    if not finding.event_ids:
        return []
    rows = db.scalars(
        select(TimelineEvent).where(TimelineEvent.id.in_(list(finding.event_ids)))
    ).all()
    codes: list[str] = []
    for row in rows:
        codes.extend(row.atc_codes or [])
    return codes


def _specialties(db: Session, finding: Finding) -> list[Specialty]:
    return resolve(
        finding_type=finding.finding_type,
        evidence=finding.evidence or {},
        atc_codes=_atc_codes(db, finding),
    )


@router.get("/for-finding/{finding_id}", response_model=SpecialtyListRead)
def specialties_for_finding(
    finding_id: UUID,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> SpecialtyListRead:
    """Which kinds of doctor could look at this, and why each.

    Needs no location, so the interface can show it beside the warning before
    asking the patient anything. Always more than one, always including a
    general practitioner: the app narrows the choice, it does not make it.
    """
    finding = _finding(db, patient, finding_id)
    return SpecialtyListRead(
        finding_id=str(finding.id),
        finding_type=finding.finding_type,
        severity=finding.severity,
        title=finding.title,
        specialties=[
            SpecialtyRead(
                label=s.label,
                reason=s.reason,
                confidence=s.confidence,
                search_terms=s.search_terms,
            )
            for s in _specialties(db, finding)
        ],
    )


def _chosen(db: Session, patient: Patient, finding_id: UUID, body: ReferralSearchRequest):
    """The finding, and which of its specialties this search is for."""
    finding = _finding(db, patient, finding_id)
    options = _specialties(db, finding)

    if not body.specialty:
        return options[0]

    wanted = body.specialty.strip().lower()
    chosen = next((s for s in options if s.label.lower() == wanted), None)
    if chosen is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{body.specialty!r} is not one of the specialties suggested for this "
            f"finding: {', '.join(s.label for s in options)}",
        )
    return chosen


def _document(db: Session, patient: Patient, document_id: UUID) -> Document:
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.patient_id == patient.id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document")
    return document


def _chosen_for_document(
    db: Session, patient: Patient, document_id: UUID, body: ReferralSearchRequest
) -> Specialty:
    """Which of a report's recommended specialties this search is for.

    The specialty names came from the directories' own catalogues, so each is
    its own search term — no translation table is needed the way it is for a
    finding, where 'Chest Specialist' has to be discovered as eChannelling's
    word for a pulmonologist. The name that came out of the catalogue is the
    name to ask it for.
    """
    document = _document(db, patient, document_id)
    stored = document.recommendation_json or {}
    options = stored.get("specialties") or []
    if not options:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This report has no specialty recommendation to search from.",
        )

    wanted = (body.specialty or "").strip().lower()
    chosen = next(
        (item for item in options if item.get("specialty", "").lower() == wanted),
        options[0] if not wanted else None,
    )
    if chosen is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{body.specialty!r} is not one of the specialties suggested for this "
            f"report: {', '.join(item.get('specialty', '') for item in options)}",
        )

    label = chosen.get("specialty", "")
    return Specialty(
        label=label,
        reason=chosen.get("reason", ""),
        confidence=chosen.get("confidence", "medium"),
        search_terms=[label],
        places_query=label,
    )


def _radii(body: ReferralSearchRequest) -> list[int] | None:
    if not body.radius_km:
        return None
    # Starting wider is a user asking to skip ahead, not to search only there —
    # the rungs beyond it still apply.
    return [r for r in referral_search.REFERRAL_RADII_KM if r >= body.radius_km] or [
        body.radius_km
    ]


@router.post("/for-finding/{finding_id}/doctors", response_model=ReferralSearchRead)
def doctors_for_finding(
    finding_id: UUID,
    body: ReferralSearchRequest,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> ReferralSearchRead:
    """Named doctors you can book, from eChannelling.

    Separate from `/clinics` because eChannelling is slow — seconds, against
    Google's milliseconds — and the two are independent questions. Call both at
    once and paint whichever returns first; a reader should not wait on the
    doctor directory to be told there is a hospital down the road.
    """
    chosen = _chosen(db, patient, finding_id, body)
    found = referral_search.find_doctors(chosen, body.location, radii_km=_radii(body))
    return _to_read(found, availability=body.availability)


@router.post("/for-finding/{finding_id}/doc990", response_model=ReferralSearchRead)
def doc990_for_finding(
    finding_id: UUID,
    body: ReferralSearchRequest,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> ReferralSearchRead:
    """Named doctors you can book, from Doc990 — the other channelling network.

    Asked separately from `/doctors` rather than merged into it. The two
    networks sign different hospitals, so a patient is served by whichever
    theirs joined: eChannelling lists no cardiologist within 50 km of Vavuniya,
    and Doc990 has one in the town.
    """
    chosen = _chosen(db, patient, finding_id, body)
    found = referral_search.find_doc990_doctors(chosen, body.location, radii_km=_radii(body))
    return _to_read(found, availability=body.availability)


@router.post("/for-finding/{finding_id}/clinics", response_model=ReferralSearchRead)
def clinics_for_finding(
    finding_id: UUID,
    body: ReferralSearchRequest,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> ReferralSearchRead:
    """Hospitals and clinics near the patient, from Google.

    Not a fallback for `/doctors`. eChannelling lists private channelling, so
    the government hospitals are absent from it entirely — and those are where
    most people are seen, and where the answer is for anyone the private
    directory places 200 km away.
    """
    chosen = _chosen(db, patient, finding_id, body)
    found = referral_search.find_clinics(chosen, body.location, radii_km=_radii(body))
    return _to_read(found, availability=body.availability)


def _to_read(found: referral_search.ReferralSearch, availability: str | None) -> ReferralSearchRead:
    return ReferralSearchRead(
        specialty=found.specialty,
        location=found.location,
        resolved_location=found.resolved_location,
        latitude=found.latitude,
        longitude=found.longitude,
        availability=availability,
        results=[
            ReferralRead(
                kind=row.kind,
                name=row.name,
                specialty=row.specialty,
                clinic_name=row.clinic_name,
                address=row.address,
                city=row.city,
                distance_km=row.distance_km,
                rating=row.rating,
                reviews=row.reviews,
                phone=row.phone,
                open_now=row.open_now,
                hours=row.hours,
                website=row.website,
                slmc_number=row.slmc_number,
                other_clinics=row.other_clinics,
                source=row.source,
                source_url=row.source_url,
                booking_url=row.booking_url,
            )
            for row in found.results
        ],
        attempts=[
            AttemptRead(
                term=a.term, radius_km=a.radius_km, found=a.found, provider=a.provider
            )
            for a in found.attempts
        ],
        widened_to_km=found.widened_to_km,
        notice=found.notice,
        sources=found.sources,
        degraded=found.degraded,
        attribution=sorted({ATTRIBUTION[s] for s in found.sources if s in ATTRIBUTION}),
    )


@router.post("/for-document/{document_id}/doctors", response_model=ReferralSearchRead)
def doctors_for_document(
    document_id: UUID,
    body: ReferralSearchRequest,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> ReferralSearchRead:
    """Doctors from eChannelling for a specialty this report points to."""
    chosen = _chosen_for_document(db, patient, document_id, body)
    found = referral_search.find_doctors(chosen, body.location, radii_km=_radii(body))
    return _to_read(found, availability=body.availability)


@router.post("/for-document/{document_id}/doc990", response_model=ReferralSearchRead)
def doc990_for_document(
    document_id: UUID,
    body: ReferralSearchRequest,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> ReferralSearchRead:
    """The same, from Doc990.

    A specialty one network lists and the other does not is normal rather than
    an error — 'Venereologist' is eChannelling's word and Doc990 writes
    'Venereologist (S.T.D)'. The empty tab says so.
    """
    chosen = _chosen_for_document(db, patient, document_id, body)
    found = referral_search.find_doc990_doctors(chosen, body.location, radii_km=_radii(body))
    return _to_read(found, availability=body.availability)


@router.post("/for-document/{document_id}/clinics", response_model=ReferralSearchRead)
def clinics_for_document(
    document_id: UUID,
    body: ReferralSearchRequest,
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> ReferralSearchRead:
    """Hospitals and clinics near the patient, for a report's specialty."""
    chosen = _chosen_for_document(db, patient, document_id, body)
    found = referral_search.find_clinics(chosen, body.location, radii_km=_radii(body))
    return _to_read(found, availability=body.availability)


@router.get("/for-document/{document_id}", response_model=ReportRecommendationRead)
def recommendation_for_document(
    document_id: UUID,
    refresh: bool = Query(
        default=False,
        description="Ask the model again rather than reading the stored answer.",
    ),
    db: Session = Depends(get_db),
    patient: Patient = Depends(current_patient),
) -> ReportRecommendationRead:
    """Which kind of doctor usually reads this lab report.

    The answer to the question a patient actually has when results come back.
    Unlike `/for-finding`, this needs no finding — most reports never raise one,
    and 'nothing was flagged' is not the same as 'nothing to ask about'. A
    borderline creatinine and an HIV result reported in words both produce
    nothing on the findings page and both belong on this card.

    Read from the document rather than decided here. The model runs once during
    ingest, so opening a report is a database read; `refresh` re-asks it, which
    is worth having while the prompt is still being tuned.
    """
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.patient_id == patient.id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document")

    stored = document.recommendation_json
    if refresh or not stored:
        found = report_recommendation.recommend(db, document)
        document.recommendation_json = found.to_json()
        db.commit()
        stored = document.recommendation_json

    readings = report_recommendation.readings_for(db, document)

    return ReportRecommendationRead(
        document_id=str(document.id),
        filename=document.filename,
        doc_type=document.doc_type,
        provider=document.provider,
        doc_date=str(document.doc_date) if document.doc_date else None,
        panel_names=stored.get("panel_names") or [],
        about=stored.get("about") or "",
        result_count=stored.get("result_count") or len(readings),
        abnormal_count=stored.get("abnormal_count") or 0,
        readings=[
            ReadingRead(
                analyte=r.analyte,
                value=r.value,
                unit=r.unit,
                reference=r.reference,
                abnormal=r.abnormal,
                lab_flagged=r.lab_flagged,
                qualitative=r.qualitative,
            )
            for r in readings
        ],
        interpretation=stored.get("interpretation"),
        specialties=[
            SuggestedSpecialtyRead(
                specialty=item.get("specialty", ""),
                reason=item.get("reason", ""),
                confidence=item.get("confidence", "medium"),
                directories=item.get("directories") or [],
            )
            for item in (stored.get("specialties") or [])
        ],
        error=stored.get("error"),
    )


@router.get("/locations", response_model=LocationSuggestions)
def locations(
    q: str = Query(description="What the user has typed so far.", min_length=1),
    patient: Patient = Depends(current_patient),
) -> LocationSuggestions:
    """Places matching what is being typed, so the location can be picked.

    Proxied rather than called from the browser so the Maps key stays on the
    server, where it is restricted to this host's address. A key shipped to the
    page is a key anyone can spend.
    """
    if not ENABLE_PLACES or not GOOGLE_MAPS_API_KEY:
        return LocationSuggestions(suggestions=[])
    try:
        with PlacesClient() as places:
            return LocationSuggestions(suggestions=places.suggest(q))
    except PlacesUnavailable as exc:
        # A dead autocomplete must not block the search: the typed text is
        # still a perfectly good guess at a place name.
        logger.info("location autocomplete unavailable: %s", exc)
        return LocationSuggestions(suggestions=[])


@router.get("/health", response_model=ReferralHealthRead)
def health() -> ReferralHealthRead:
    """Whether the directories are answering.

    Worth calling before a demo rather than during one. A referral search with
    both providers down returns no doctors, which on screen is very hard to
    tell apart from a town that has none.
    """
    providers: list[ProviderHealth] = []

    reachable = False
    detail = None
    if ENABLE_ECHANNELLING:
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                count = len(ech.specialisations(client))
            reachable = count > 0
            detail = f"{count} specialties listed"
        except ech.EchannellingUnavailable as exc:
            detail = str(exc)
    else:
        detail = "switched off"
    providers.append(
        ProviderHealth(
            name="echannelling", configured=ENABLE_ECHANNELLING, reachable=reachable, detail=detail
        )
    )

    doc_ok = False
    doc_detail = None
    if ENABLE_DOC990:
        try:
            with doc990.session() as client:
                hospitals, specialities = doc990.catalogue(client)
            doc_ok = bool(hospitals and specialities)
            doc_detail = f"{len(hospitals)} hospitals, {len(specialities)} specialties"
        except doc990.Doc990Unavailable as exc:
            doc_detail = str(exc)
    else:
        doc_detail = "switched off"
    providers.append(
        ProviderHealth(
            name="doc990", configured=ENABLE_DOC990, reachable=doc_ok, detail=doc_detail
        )
    )

    places_ok = False
    places_detail = None
    if ENABLE_PLACES and GOOGLE_MAPS_API_KEY:
        try:
            with PlacesClient() as places:
                places_ok = places.geocode("Colombo") is not None
                places_detail = "geocoding answered" if places_ok else "geocoding returned nothing"
        except PlacesUnavailable as exc:
            places_detail = str(exc)
    else:
        places_detail = "no API key" if ENABLE_PLACES else "switched off"
    providers.append(
        ProviderHealth(
            name="google_places",
            configured=bool(ENABLE_PLACES and GOOGLE_MAPS_API_KEY),
            reachable=places_ok,
            detail=places_detail,
        )
    )

    return ReferralHealthRead(
        providers=providers, usable=any(p.reachable for p in providers)
    )
