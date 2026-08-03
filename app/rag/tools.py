"""The tools the agent can call.

Built per request by `build_tools`, which captures the database session and the
patient id in a closure. That is the security boundary of this module: the
schema the model receives has no `patient_id` field, so there is nothing for a
prompt injection hidden in an uploaded document to set. Every query filters on
the captured id inside its WHERE clause, never as a check afterwards.

Three conventions hold across every tool:

  * results are compact JSON, never prose — the model writes the sentence, and
    for anything safety-related the data decided long before it ran;
  * every row carries what the next call needs (`document_id`, `event_id`) and
    what an answer must cite (`page_no`, `source_span`, `confidence`);
  * an empty result says what to try instead. That `hint` is what drives the
    agent's retry, and it costs nothing because the tool already knows it found
    nothing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from langchain_core.tools import BaseTool, tool
from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.checks.base import ATC_PREFIX, SEVERITY_FROM_DDINTER, window
from app.checks.drugs import _substance_families
from app.checks.fallback import class_signal_between, label_mention
from app.db.models import (
    Document,
    DocumentPage,
    Finding,
    InteractionRule,
    TimelineEvent,
    TimelineVisit,
)
from app.ingest.drugs import resolve
from app.rag.textsearch import search_pages

# Rejected rows are kept so a mistake can be undone, but they are not part of
# the record an answer should be built from.
HIDDEN_STATUSES = ("rejected",)

# Enough to answer with, small enough not to flood the context.
DEFAULT_LIMIT = 50

# Above this, read_document refuses and asks for a page range instead. Twelve
# pages of transcription is already ~25k characters.
MAX_PAGES_AT_ONCE = 12

# Search returns extracts rather than rows, so a page of results costs far more
# context than a page of medications. Eight extracts is about 2.5k characters.
SEARCH_LIMIT = 8
SEARCH_LIMIT_MAX = 20

# The vocabulary the flag columns actually use — 'H' high, 'L' low, 'A'
# abnormal, 'N' normal. Anything but N (or nothing at all) is worth a mention.
ABNORMAL_FLAGS = ("H", "L", "A")


def _abnormal(event: TimelineEvent) -> bool:
    """Whether a reading fell outside its range.

    `computed_flag` is what the reference range says; `flag` is what the lab
    printed. The computed one wins, because a lab that prints the wrong flag is
    exactly the kind of thing this app exists to catch.
    """
    return (event.computed_flag or event.flag) in ABNORMAL_FLAGS


def _num(value: Decimal | float | None) -> float | None:
    """Decimal is not JSON-serialisable, and the model reads numbers better
    than it reads strings that look like numbers."""
    return None if value is None else float(value)


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """Drop empty fields.

    Every null costs tokens and tells the model nothing. A medication row has
    forty columns and typically eight useful ones.
    """
    return {k: v for k, v in row.items() if v not in (None, "", [], {})}


def _event(event: TimelineEvent, *, span: bool = True) -> dict[str, Any]:
    """One extracted fact, in the shape an answer needs to cite it."""
    row = {
        "event_id": str(event.id),
        "type": event.event_type,
        "date": _iso(event.event_date),
        "text": event.display_text or event.title,
        # Medication
        "drug": event.drug_name or event.drug_raw,
        "rxcui": event.rxcui,
        "atc": event.atc_codes,
        "strength": (
            f"{_num(event.strength_value):g} {event.strength_unit}".strip()
            if event.strength_value is not None
            else None
        ),
        "doses_per_day": _num(event.doses_per_day),
        "frequency": event.frequency_label,
        "prn": event.prn or None,
        "start_date": _iso(event.start_date),
        "end_date": _iso(event.end_date),
        # Lab / vital
        "analyte": event.analyte,
        "value": _num(event.value_num) if event.value_num is not None else event.value_text,
        "unit": event.unit,
        "ref_low": _num(event.ref_low),
        "ref_high": _num(event.ref_high),
        "flag": event.computed_flag or event.flag,
        # Allergy / diagnosis / procedure
        "substance": event.substance,
        "reaction": event.reaction,
        "description": event.description,
        # Provenance — the part an answer has to be able to point at
        "confidence": _num(event.confidence),
        "review_status": event.review_status,
        "document_id": str(event.document_id) if event.document_id else None,
        "page_no": event.page_no,
    }
    if span:
        row["source_span"] = event.source_span
    return _clean(row)


def _visible(query):
    """Exclude rows the user rejected."""
    return query.where(TimelineEvent.review_status.not_in(HIDDEN_STATUSES))


def _empty(hint: str) -> dict[str, Any]:
    """Nothing found, and what to do about it.

    Returned rather than raised: an empty result is an answer, and the agent
    needs to see the suggestion to act on it.
    """
    return {"results": [], "count": 0, "hint": hint}


def build_tools(db: Session, patient_id: UUID) -> list[BaseTool]:
    """Every tool, bound to one patient for the life of one request."""

    # --- orientation ----------------------------------------------------

    @tool
    def list_documents(
        doc_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict:
        """List the patient's uploaded documents, newest first.

        Use this first when you do not yet know which document holds the answer.
        Every row includes a document_id you can pass to read_document.

        doc_type: optional filter, e.g. 'prescription', 'lab_report',
            'discharge_summary', 'doctor_note'.
        since / until: optional ISO dates (YYYY-MM-DD) on the document's date.
        """
        query = select(Document).where(Document.patient_id == patient_id)
        if doc_type:
            query = query.where(Document.doc_type == doc_type)
        if since:
            query = query.where(Document.doc_date >= date.fromisoformat(since))
        if until:
            query = query.where(Document.doc_date <= date.fromisoformat(until))

        rows = db.scalars(query.order_by(Document.doc_date.desc().nullslast())).all()
        if not rows:
            return _empty(
                "no documents match. Call list_documents() with no filters to see "
                "everything the patient has uploaded."
            )

        return {
            "count": len(rows),
            "results": [
                _clean(
                    {
                        "document_id": str(d.id),
                        "title": d.summary or d.filename,
                        "doc_type": d.doc_type,
                        "date": _iso(d.doc_date),
                        "provider": d.provider,
                        "clinician": d.clinician_name,
                        "pages": d.page_count,
                        # Anything not 'ready' has not been fully read yet, and
                        # an answer drawn from it would be incomplete.
                        "status": d.status if d.status != "ready" else None,
                    }
                )
                for d in rows
            ],
        }

    # --- the timeline ---------------------------------------------------

    @tool
    def get_timeline(
        since: str | None = None,
        until: str | None = None,
        event_type: str | None = None,
        newest_first: bool = False,
        limit: int = 20,
    ) -> dict:
        """The patient's visits in date order, each with the facts extracted from it.

        This is the backbone: use it for "what happened in June", "when was my
        last visit", or to get oriented before reading a document. Visits are
        ordered by the date of the visit itself, not by when the file was
        uploaded.

        event_type: optional filter — 'medication', 'lab_result', 'vital_sign',
            'diagnosis', 'allergy', 'procedure', 'note'.
        newest_first: set true for "my last visit".
        """
        query = select(TimelineVisit).where(TimelineVisit.patient_id == patient_id)
        if since:
            query = query.where(TimelineVisit.visit_date >= date.fromisoformat(since))
        if until:
            query = query.where(TimelineVisit.visit_date <= date.fromisoformat(until))

        order = (
            TimelineVisit.visit_date.desc() if newest_first else TimelineVisit.visit_date.asc()
        )
        visits = db.scalars(
            query.order_by(order.nullslast(), TimelineVisit.created_at).limit(
                min(limit, DEFAULT_LIMIT)
            )
        ).all()

        if not visits:
            return _empty(
                "no visits in that range. Call get_timeline() with no filters, or "
                "list_documents() to see what has been uploaded."
            )

        results = []
        for visit in visits:
            events = [
                e
                for e in visit.events
                if e.review_status not in HIDDEN_STATUSES
                and (event_type is None or e.event_type == event_type)
            ]
            events.sort(key=lambda e: (e.event_type, e.event_date or date.min))
            results.append(
                _clean(
                    {
                        "visit_id": str(visit.id),
                        "visit_date": _iso(visit.visit_date),
                        "doc_type": visit.doc_type,
                        "provider": visit.provider,
                        "summary": visit.summary,
                        "document_id": str(visit.document_id) if visit.document_id else None,
                        # Spans are long; at timeline level the facts are enough.
                        # read_document gives the full text when it is needed.
                        "events": [_event(e, span=False) for e in events],
                    }
                )
            )

        return {"count": len(results), "results": results}

    # --- medications ----------------------------------------------------

    @tool
    def find_medications(
        name: str | None = None,
        atc_class: str | None = None,
        active_on: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict:
        """Every medication on the record, with dose, frequency and dates.

        Returns all matches, not a sample — use this rather than searching text
        for any question about what the patient is taking.

        name: matches the drug name or how it was written on the document
            (brand or generic).
        atc_class: WHO class prefix, for questions about a kind of drug rather
            than a named one. 'B01' blood thinners, 'C07' beta blockers,
            'M01A' anti-inflammatories, 'J01' antibiotics, 'A10' diabetes.
        active_on: ISO date — only medicines likely being taken that day.
            Where a prescription states no duration, 90 days is assumed.
        """
        query = _visible(
            select(TimelineEvent).where(
                TimelineEvent.patient_id == patient_id,
                TimelineEvent.event_type == "medication",
            )
        )
        if name:
            like = f"%{name.strip()}%"
            query = query.where(
                TimelineEvent.drug_name.ilike(like)
                | TimelineEvent.drug_raw.ilike(like)
                | TimelineEvent.title.ilike(like)
            )
        if since:
            query = query.where(TimelineEvent.event_date >= date.fromisoformat(since))
        if until:
            query = query.where(TimelineEvent.event_date <= date.fromisoformat(until))

        rows = db.scalars(query.order_by(TimelineEvent.event_date.desc().nullslast())).all()

        # Filtered in Python, not SQL: atc_codes is an array and a prefix match
        # against it is clearer here than in the query.
        if atc_class:
            wanted = atc_class.strip().upper()
            rows = [r for r in rows if any((c or "").upper().startswith(wanted) for c in r.atc_codes or [])]

        if active_on:
            when = date.fromisoformat(active_on)
            rows = [r for r in rows if (w := window(r)) and w[0] <= when <= w[1]]

        if not rows:
            hint = "no medications match."
            if name:
                hint += (
                    f" '{name}' may be a brand name or a drug class rather than an "
                    "ingredient — try find_medications(atc_class=...) or check_drug() "
                    "to resolve the name."
                )
            else:
                hint += " Call find_medications() with no filters to see everything."
            return _empty(hint)

        # A drug with no rxcui was never identified, so it is invisible to the
        # interaction rules. An answer that does not say so is misleading.
        unidentified = [r.drug_raw or r.title for r in rows if not r.rxcui]

        return _clean(
            {
                "count": len(rows),
                "results": [_event(r) for r in rows],
                "unidentified": unidentified or None,
                "note": (
                    f"{len(unidentified)} of these could not be matched to a known drug, "
                    "so safety checks could not cover them."
                    if unidentified
                    else None
                ),
            }
        )

    # --- labs -----------------------------------------------------------

    @tool
    def get_lab_series(
        analyte: str,
        since: str | None = None,
        until: str | None = None,
    ) -> dict:
        """Every reading of one lab test or vital sign, oldest first.

        Use this for any question about a trend or a change over time — the
        readings come back in order with their reference ranges, so a direction
        can be stated rather than guessed.

        analyte: the test name, e.g. 'HbA1c', 'INR', 'creatinine',
            'blood pressure'. Matched loosely, so partial names work.
        """
        query = _visible(
            select(TimelineEvent).where(
                TimelineEvent.patient_id == patient_id,
                TimelineEvent.event_type.in_(("lab_result", "vital_sign")),
                TimelineEvent.analyte.ilike(f"%{analyte.strip()}%"),
            )
        )
        if since:
            query = query.where(TimelineEvent.event_date >= date.fromisoformat(since))
        if until:
            query = query.where(TimelineEvent.event_date <= date.fromisoformat(until))

        rows = db.scalars(
            query.order_by(TimelineEvent.event_date.asc().nullslast())
        ).all()

        if not rows:
            known = db.scalars(
                _visible(
                    select(TimelineEvent.analyte)
                    .where(
                        TimelineEvent.patient_id == patient_id,
                        TimelineEvent.event_type.in_(("lab_result", "vital_sign")),
                        TimelineEvent.analyte.is_not(None),
                    )
                    .distinct()
                )
            ).all()
            return _empty(
                f"no readings for '{analyte}'. "
                + (
                    f"Tests on record: {', '.join(sorted(known))}."
                    if known
                    else "This patient has no lab results at all."
                )
            )

        dated = [r for r in rows if r.event_date and r.value_num is not None]
        direction = None
        if len(dated) >= 2:
            first, last = _num(dated[0].value_num), _num(dated[-1].value_num)
            direction = "rising" if last > first else "falling" if last < first else "unchanged"

        return _clean(
            {
                "analyte": rows[0].analyte,
                "count": len(rows),
                "results": [_event(r) for r in rows],
                # Stated from the numbers rather than left to the model to
                # eyeball, so the wording cannot contradict the data.
                "direction": direction,
                "abnormal_count": sum(1 for r in rows if _abnormal(r)) or None,
            }
        )

    # --- allergies ------------------------------------------------------

    @tool
    def get_allergies() -> dict:
        """Every allergy on the record, with the reaction and where it came from.

        Call this before answering any question about whether something is safe
        to take. A handful of rows, and the most consequential ones on the
        timeline.
        """
        rows = db.scalars(
            _visible(
                select(TimelineEvent).where(
                    TimelineEvent.patient_id == patient_id,
                    TimelineEvent.event_type == "allergy",
                )
            ).order_by(TimelineEvent.event_date.asc().nullslast())
        ).all()

        if not rows:
            return _empty(
                "no allergies were extracted from this patient's documents. That is not "
                "the same as having none — an allergy written only in prose may have been "
                "missed. Say so rather than stating the patient has no allergies."
            )
        return {"count": len(rows), "results": [_event(r) for r in rows]}

    # --- findings -------------------------------------------------------

    @tool
    def get_findings(severity: str | None = None, include_dismissed: bool = False) -> dict:
        """The safety cross-checks already run across every document.

        Interactions, duplicate prescriptions, conflicting doses, allergy
        contradictions and lab trends. These were computed from the drug
        database, not by a model — report them, do not re-derive them.

        severity: optional — 'high', 'moderate', 'low' or 'info'.
        """
        query = select(Finding).where(Finding.patient_id == patient_id)
        if not include_dismissed:
            # A dismissal is the user saying "I know, stop telling me".
            query = query.where(Finding.status == "active")
        if severity:
            query = query.where(Finding.severity == severity)

        rank = case({"high": 0, "moderate": 1, "low": 2}, value=Finding.severity, else_=3)
        rows = db.scalars(query.order_by(rank, Finding.first_seen_at.desc())).all()

        if not rows:
            return _empty(
                "no active findings. Before concluding the patient is in the clear, check "
                "find_medications() for drugs that could not be identified — those are "
                "invisible to the interaction rules."
            )

        return {
            "count": len(rows),
            "results": [
                _clean(
                    {
                        "finding_id": str(f.id),
                        "type": f.finding_type,
                        "severity": f.severity,
                        "title": f.title,
                        "detail": f.detail,
                        "evidence": f.evidence,
                        "event_ids": [str(i) for i in f.event_ids or []],
                        "status": f.status if f.status != "active" else None,
                    }
                )
                for f in rows
            ],
        }

    # --- "can I take this?" ---------------------------------------------

    @tool
    async def check_drug(name: str) -> dict:
        """Whether a drug is safe alongside what the patient already takes.

        Answers "can I take X?". Checks two things: interactions against the
        patient's current medicines, and whether the drug belongs to a family
        they are allergic to.

        The verdict comes from the drug database, never from your own knowledge
        of pharmacology. If this returns nothing, say nothing was found — do not
        supply an interaction you happen to know about.

        name: brand or generic, e.g. 'ibuprofen', 'Crocin', 'Zoclar 500'.
        """
        # Same resolver ingestion uses: cache, then RxNorm exact, then the model
        # proposes and RxNorm verifies. Handles brands and misspellings.
        identity = await resolve(name)

        if not identity.verified or not identity.rxcuis:
            return _clean(
                {
                    "drug": name,
                    "identified": False,
                    "reason": identity.reason or "not found in RxNorm",
                    "hint": (
                        f"'{name}' could not be matched to a known drug, so no safety check "
                        "was possible. Say that plainly — do not answer from general "
                        "knowledge. Suggest confirming the spelling or asking a pharmacist."
                    ),
                }
            )

        rxcui = identity.rxcuis[0]
        ingredient = identity.ingredients[0]
        families = {code[:ATC_PREFIX] for a in identity.atc if (code := a.get("code", ""))}

        current = db.scalars(
            _visible(
                select(TimelineEvent).where(
                    TimelineEvent.patient_id == patient_id,
                    TimelineEvent.event_type == "medication",
                )
            )
        ).all()

        # --- interactions, matched by rxcui and never by name ---
        #
        # Three lanes, weakest last. A graded rule is a fact about these two
        # drugs; a class signal is a fact about the drug's family; a label
        # mention is a sentence someone else wrote. They are reported apart so
        # an answer can say which kind of evidence it has.
        interactions = []
        class_warnings = []
        label_notes = []

        for med in current:
            if not med.rxcui or med.rxcui == rxcui:
                continue
            name = med.drug_name or med.drug_raw

            # The table stores each pair once, with rxcui_a < rxcui_b.
            low, high = sorted((rxcui, med.rxcui))
            rule = db.scalar(
                select(InteractionRule).where(
                    InteractionRule.rxcui_a == low, InteractionRule.rxcui_b == high
                )
            )
            if rule:
                interactions.append(
                    _clean(
                        {
                            "with": name,
                            "severity": rule.severity,
                            "risk": SEVERITY_FROM_DDINTER.get(rule.severity),
                            "since": _iso(med.event_date),
                            "event_id": str(med.id),
                        }
                    )
                )
                continue

            # No rule for this exact pair. Does either drug's class have one?
            signal = class_signal_between(db, rxcui, med.rxcui)
            if signal:
                class_warnings.append(
                    {
                        "with": name,
                        "severity": signal.severity,
                        "basis": (
                            f"{signal.interacting} of {signal.total} drugs in "
                            f"{signal.atc_class} ({signal.class_name}) — the class "
                            f"{signal.subject} belongs to — interact with {signal.against}"
                        ),
                        "examples": signal.examples,
                        "note": "class-level — not a rule for this specific drug",
                        "event_id": str(med.id),
                    }
                )
                continue

            # Nothing in either. Does the FDA label name it?
            mention = await label_mention(ingredient, name)
            if mention:
                label_notes.append(
                    {
                        "with": name,
                        "basis": f"the FDA label for {mention.subject} mentions {mention.mentions}",
                        "quote": mention.snippet,
                        "source": mention.source,
                        "note": "label text, not a graded interaction",
                        "event_id": str(med.id),
                    }
                )

        # --- allergies, matched by shared drug family ---
        allergy_conflicts = []
        allergies = db.scalars(
            _visible(
                select(TimelineEvent).where(
                    TimelineEvent.patient_id == patient_id,
                    TimelineEvent.event_type == "allergy",
                )
            )
        ).all()
        for allergy in allergies:
            if not allergy.substance:
                continue
            covered, _ = _substance_families(db, allergy.substance)
            shared = families & covered
            if shared:
                allergy_conflicts.append(
                    {
                        "allergy": allergy.substance,
                        "reaction": allergy.reaction,
                        "shared_class": sorted(shared)[0],
                        "recorded": _iso(allergy.event_date),
                        "event_id": str(allergy.id),
                    }
                )

        unchecked = [m.drug_raw or m.title for m in current if not m.rxcui]

        return _clean(
            {
                "drug": name,
                "identified": True,
                "ingredient": ingredient,
                "rxcui": rxcui,
                "interactions": interactions,
                "class_warnings": class_warnings or None,
                "label_mentions": label_notes or None,
                "allergy_conflicts": allergy_conflicts,
                "checked_against": len(current) - len(unchecked),
                "unchecked": unchecked or None,
                "note": (
                    f"{len(unchecked)} of the patient's medicines could not be identified "
                    "and were not covered by this check."
                    if unchecked
                    else None
                ),
                "hint": (
                    "nothing found in the interaction database, in the drug's class, or in "
                    "the FDA labels. That is not the same as safe — say so plainly, and "
                    "recommend checking the full medicine list with a pharmacist."
                    if not (interactions or class_warnings or label_notes or allergy_conflicts)
                    else None
                ),
            }
        )

    # --- reading a document ---------------------------------------------

    @tool
    def read_document(document_id: str, pages: str | None = None) -> dict:
        """The full text of one document, page by page.

        Use this when the answer is prose the extraction never turned into a
        row — advice, reasons, instructions, narrative. Get the document_id from
        list_documents() or get_timeline().

        pages: optional range like '1-5' or '3'. Required for long documents.
        """
        try:
            doc_uuid = UUID(document_id)
        except ValueError:
            return {"error": "not a valid document id", "hint": "call list_documents() first"}

        # Patient filter is part of the lookup, so another patient's id returns
        # 'not found' rather than 'forbidden' — its existence is not theirs to learn.
        document = db.scalar(
            select(Document).where(Document.id == doc_uuid, Document.patient_id == patient_id)
        )
        if document is None:
            return {
                "error": "no such document for this patient",
                "hint": "call list_documents() to see what exists",
            }

        query = select(DocumentPage).where(DocumentPage.document_id == doc_uuid)
        wanted: range | None = None
        if pages:
            try:
                first, _, last = pages.partition("-")
                wanted = range(int(first), int(last or first) + 1)
            except ValueError:
                return {"error": f"could not read page range {pages!r}", "hint": "use '1-5' or '3'"}
            query = query.where(DocumentPage.page_no.in_(list(wanted)))

        rows = db.scalars(query.order_by(DocumentPage.page_no)).all()

        # Without a cap a long discharge summary drops eighty thousand
        # characters into the context in a single call.
        if wanted is None and len(rows) > MAX_PAGES_AT_ONCE:
            return {
                "error": f"document has {len(rows)} pages",
                "hint": (
                    f"too long to read at once — call read_document(document_id, "
                    f"pages='1-{MAX_PAGES_AT_ONCE}') or use search_text() to find the "
                    "relevant part first"
                ),
                "document": _clean(
                    {
                        "title": document.summary or document.filename,
                        "doc_type": document.doc_type,
                        "date": _iso(document.doc_date),
                        "provider": document.provider,
                        "pages": len(rows),
                    }
                ),
            }

        if not rows:
            return _empty(
                "that document has no readable pages yet — it may still be processing."
            )

        return {
            "document_id": str(document.id),
            "title": document.summary or document.filename,
            "doc_type": document.doc_type,
            "date": _iso(document.doc_date),
            "provider": document.provider,
            "pages": [
                _clean(
                    {
                        "page_no": p.page_no,
                        # How the page was read is where an answer's confidence
                        # comes from: digital 1.0, scanned 0.8, handwritten 0.55.
                        "modality": p.modality,
                        "language": p.language,
                        "text": p.transcription,
                    }
                )
                for p in rows
            ],
        }

    # --- searching the text ----------------------------------------------

    @tool
    def search_text(
        query: str,
        doc_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = SEARCH_LIMIT,
    ) -> dict:
        """Search the text of every page for words, and return the parts that match.

        Use this for anything the other tools have no column for — advice,
        instructions, reasons, symptoms, what a doctor wrote in a note. Prefer
        it over read_document when you know roughly what was said but not which
        document said it.

        Search for the words that would appear in the document, not the words
        of the question: for 'what diet did the doctor advise', search
        'diet advice food'. Matching is by word and by spelling, never by
        meaning — 'exercise' will not find 'walk daily'. If a search comes back
        empty, try the words a clinician would have written instead.

        Each result names its document_id and page_no, so read_document can
        then read the whole page for context.

        query: the words to look for. Quoted "exact phrases", OR, and -excluded
            terms all work.
        doc_type: optional filter, e.g. 'prescription', 'discharge_summary'.
        since / until: optional ISO dates (YYYY-MM-DD) on the document's date.
        limit: how many pages to return; the default is usually right.
        """
        hits = search_pages(
            db,
            patient_id,
            query,
            doc_type=doc_type,
            since=date.fromisoformat(since) if since else None,
            until=date.fromisoformat(until) if until else None,
            limit=min(limit, SEARCH_LIMIT_MAX),
        )
        if not hits:
            return _empty(
                f"no page contains {query!r}. Search does not understand meaning, only "
                "words — try the terms a clinician would have written, or call "
                "list_documents() and read the likely one."
            )

        return {
            "count": len(hits),
            "results": [
                _clean(
                    {
                        "document_id": str(hit.document.id),
                        "title": hit.document.summary or hit.document.filename,
                        "doc_type": hit.document.doc_type,
                        "date": _iso(hit.document.doc_date),
                        "page_no": hit.page.page_no,
                        # A handwritten page that matched is still a handwritten
                        # page; anything quoted from it inherits that doubt.
                        "modality": hit.page.modality,
                        "language": hit.page.language,
                        "text": hit.snippet,
                    }
                )
                for hit in hits
            ],
            "hint": (
                "these are extracts, not whole pages — call read_document(document_id, "
                "pages='N') before relying on anything the extract cuts off."
            ),
        }

    # --- what the app is unsure about ------------------------------------

    @tool
    def get_uncertain_items(risk: str | None = None) -> dict:
        """Facts the app extracted but is not confident it read correctly.

        Use this to qualify an answer. If a claim rests on one of these, say so
        and give the confidence — an answer built on a shaky reading should not
        sound certain.

        risk: optional — 'high', 'medium' or 'low'.
        """
        query = _visible(
            select(TimelineEvent).where(
                TimelineEvent.patient_id == patient_id,
                TimelineEvent.review_status == "needs_review",
            )
        )
        if risk:
            query = query.where(TimelineEvent.risk == risk)

        # Same ordering as the review queue endpoint, so this tool and the
        # Review screen agree on what is most worrying.
        rank = case({"high": 0, "medium": 1, "low": 2}, value=TimelineEvent.risk, else_=3)
        rows = db.scalars(
            query.order_by(rank, TimelineEvent.confidence.asc().nullsfirst()).limit(DEFAULT_LIMIT)
        ).all()

        if not rows:
            return _empty("nothing is awaiting review — every extracted fact cleared its bar.")

        return {
            "count": len(rows),
            "results": [
                _clean({**_event(r), "risk": r.risk, "why": r.review_reasons}) for r in rows
            ],
        }

    return [
        list_documents,
        get_timeline,
        find_medications,
        get_lab_series,
        get_allergies,
        get_findings,
        check_drug,
        read_document,
        search_text,
        get_uncertain_items,
    ]
