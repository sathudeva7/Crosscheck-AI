"""Finding a sentence in the patient's documents.

Two lanes over the indexes from migration 006, fused into one ranking:

  * **words** — Postgres full-text search. Understands that 'prescribed' and
    'prescribing' are the same word, ignores 'the' and 'was', and scores by how
    many of the query's terms a page contains and how close together they are.

  * **characters** — a substring match on the raw text. Catches what the
    stemmer mangles ('diet' is not a prefix of 'dietari'), names glued to their
    strength ('Zoclar-500'), and the one-character damage that scanning does.

Neither lane alone is enough and neither is reliably better, so the results are
merged by *rank* rather than by score. Reciprocal rank fusion is the standard
way to do that: a page's worth is the sum of 1/(k + its position) in each lane
it appears in. It needs no tuning and no comparable scales, which matters here
because a full-text rank and a substring hit are not measured in the same
units and never will be.

The same fusion is where an embedding lane would slot in later — a third list
of page ids in rank order, merged the same way. Nothing above it changes.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Select, case, desc, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Document, DocumentPage

# How deep into each lane to look before fusing. Deeper costs almost nothing —
# these are index scans returning ids — and shallow lanes are how a page that
# one lane ranks 11th and the other ranks 12th vanishes from a merge entirely.
LANE_DEPTH = 40

# The constant in reciprocal rank fusion. 60 is the value from the original
# paper and is used unchanged nearly everywhere; it flattens the difference
# between 1st and 2nd place so a single confident lane cannot dominate a page
# that both lanes agree on.
RRF_K = 60

# Terms shorter than this are not worth a substring scan: 'mg' matches half the
# corpus, and the words lane already handles short words properly.
MIN_TERM_CHARS = 4

# Words a medical question is full of and no document is distinguished by.
# Deliberately tiny — this is not a stop-word list, it is the handful of terms
# that would otherwise make the character lane match every page.
NOISE = frozenset(
    """
    what when where which who whom whose why how does did was were
    have has had the and for with from that this these those there
    patient doctor take taken taking tell show give tell about
    """.split()
)

SNIPPET_CHARS = 320


@dataclass
class Hit:
    """One page that matched, and the part of it that did."""

    page: DocumentPage
    document: Document
    snippet: str
    lanes: list[str] = field(default_factory=list)
    score: float = 0.0


def _wordish(char: str) -> bool:
    """Whether a character belongs to the word around it.

    Not `\\w`, which drops combining marks: the vowel signs in बुखार are marks,
    so `\\w` shreds a five-character Hindi word into three one-character
    fragments and every length test then throws them away. That is not a
    theoretical case — it is what the first run of this module did to the one
    Hindi page in the corpus.
    """
    return char.isalnum() or char == "'" or unicodedata.category(char).startswith("M")


def tokens(text: str) -> list[str]:
    """Words, in any script.

    Punctuation ends a word rather than being stripped out of it, which is what
    splits 'Zoclar-500' into a name and a strength. Stripping instead would
    produce 'zoclar500', a string that appears in no document.
    """
    found: list[str] = []
    current: list[str] = []
    for char in text:
        if _wordish(char):
            current.append(char)
        elif current:
            found.append("".join(current))
            current = []
    if current:
        found.append("".join(current))
    return found


def terms(query: str) -> list[str]:
    """The words worth hunting for as raw characters.

    Possessives lose their ending first, so that "the patient's diet" drops
    'patient' as noise rather than hunting for the literal string "patient's".
    """
    found = [t.removesuffix("'s") for t in tokens(query.lower())]
    return [t for t in found if len(t) >= MIN_TERM_CHARS and t not in NOISE]


def _base(patient_id: UUID, doc_type: str | None, since, until) -> Select:
    """Page ids for this patient, with the caller's filters applied.

    The patient filter lives here, in the WHERE clause of every lane, rather
    than as a check on the results — the same rule as the rest of the tools.
    """
    query = (
        select(DocumentPage.id)
        .join(Document, Document.id == DocumentPage.document_id)
        .where(Document.patient_id == patient_id)
        # A page with no text is not a miss, it is a page that has not been
        # read yet, and it cannot match anything.
        .where(DocumentPage.transcription.isnot(None))
    )
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if since:
        query = query.where(Document.doc_date >= since)
    if until:
        query = query.where(Document.doc_date <= until)
    return query


def _words_lane(db: Session, base: Select, query: str) -> list[UUID]:
    """Full-text hits, best first.

    `websearch_to_tsquery` rather than `plainto_tsquery` because it accepts
    what people actually type — quoted phrases, OR, a leading minus — and,
    unlike `to_tsquery`, never raises on punctuation. A model writing a search
    query will eventually produce a stray quote, and that must not be an error.
    """
    tsquery = func.websearch_to_tsquery("english", query)
    ranked = base.add_columns(
        func.ts_rank_cd(DocumentPage.search_vector, tsquery).label("rank")
    ).where(DocumentPage.search_vector.op("@@")(tsquery))
    rows = db.execute(ranked.order_by(desc("rank")).limit(LANE_DEPTH)).all()
    return [row[0] for row in rows]


def _chars_lane(db: Session, base: Select, wanted: list[str]) -> list[UUID]:
    """Substring hits, most distinct terms matched first.

    There is no meaningful relevance score for a substring match, so pages are
    ordered by how many of the query's terms they contain — which is the only
    signal available and, for a two- or three-term question, a good one.
    """
    if not wanted:
        return []

    hits = [DocumentPage.transcription.ilike(f"%{term}%") for term in wanted]
    matched = sum(
        (case((hit, 1), else_=0) for hit in hits[1:]),
        start=case((hits[0], 1), else_=0),
    )
    ranked = (
        base.add_columns(matched.label("matched"))
        .where(or_(*hits))
        .order_by(desc("matched"))
        .limit(LANE_DEPTH)
    )
    return [row[0] for row in db.execute(ranked).all()]


def _fuse(lanes: dict[str, list[UUID]]) -> dict[UUID, tuple[float, list[str]]]:
    """Reciprocal rank fusion: score by position, not by each lane's own units."""
    fused: dict[UUID, tuple[float, list[str]]] = {}
    for name, ids in lanes.items():
        for position, page_id in enumerate(ids, start=1):
            score, seen = fused.get(page_id, (0.0, []))
            fused[page_id] = (score + 1 / (RRF_K + position), [*seen, name])
    return fused


def _snippet(text: str, wanted: list[str], query: str) -> str:
    """The part of the page worth showing.

    Anchored on the first query term that actually appears, so the model sees
    the sentence that matched rather than the top of the page. Falls back to
    the opening of the page when the match was a stem the raw text does not
    contain literally — which is a real case: searching 'prescribing' matches a
    page that says 'prescribed', and neither word is a substring of the other.
    """
    hunt = wanted or [w for w in tokens(query.lower()) if len(w) > 2]
    lowered = text.lower()

    at = next((found for term in hunt if (found := lowered.find(term)) != -1), -1)
    if at == -1:
        cut = " ".join(text[:SNIPPET_CHARS].split())
        return cut + ("…" if len(text) > SNIPPET_CHARS else "")

    start = max(0, at - SNIPPET_CHARS // 3)
    end = min(len(text), at + (SNIPPET_CHARS * 2) // 3)
    return ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else "")


def search_pages(
    db: Session,
    patient_id: UUID,
    query: str,
    *,
    doc_type: str | None = None,
    since=None,
    until=None,
    limit: int = 8,
) -> list[Hit]:
    """Both lanes, fused, hydrated into pages with their documents."""
    base = _base(patient_id, doc_type, since, until)
    wanted = terms(query)

    fused = _fuse(
        {
            "words": _words_lane(db, base, query),
            "characters": _chars_lane(db, base, wanted),
        }
    )
    if not fused:
        return []

    top = sorted(fused.items(), key=lambda item: item[1][0], reverse=True)[:limit]
    order = {page_id: position for position, (page_id, _) in enumerate(top)}

    # Eager-loaded: every hit reports its document, and lazy loading would mean
    # one extra query per result.
    pages = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.id.in_(list(order)))
        .options(joinedload(DocumentPage.document))
    ).all()

    hits = [
        Hit(
            page=page,
            document=page.document,
            snippet=_snippet(page.transcription or "", wanted, query),
            lanes=fused[page.id][1],
            score=fused[page.id][0],
        )
        for page in pages
    ]
    hits.sort(key=lambda hit: order[hit.page.id])
    return hits
