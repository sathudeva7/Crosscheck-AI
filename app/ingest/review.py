"""Decide how far each extracted fact can be trusted, and what a human should check.

Every step before this produced evidence and left it lying around. The model
rated its own work; ground.py checked whether the quote was really on the page;
triage recorded whether the page was typed or handwritten; drugs.py said
whether the medicine could be identified at all. Nobody put it together.

This is the judge, and it goes last because it needs all of them.

Confidence is the product of three independent signals:

    what the model claimed  x  did we find the quote  x  typed or handwritten

Two of the three are ours, which is the point. A model's self-assessment alone
is worthless — it reports 0.99 whether it read the dose correctly or not. The
same drug read off a clean PDF and off a doctor's handwriting must not end up
with the same number.

Then the number is weighed against what the fact *is*. A wrong clinic phone
number is a nuisance; a wrong dose is dangerous. So medicines are held to a
much higher bar than a recorded weight, and a handful of specific warning signs
send an item to review however high it scored.

Nothing is deleted or hidden. 'needs_review' marks an item and puts it in a
queue; it still appears on the patient's timeline. Concealing a fact because we
are unsure of it would be worse than showing it with an honest caveat.

No model call, no network. The same input always gives the same answer, which
matters for something that decides whether a medical warning is shown.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from pydantic import BaseModel

from app.schemas.extraction import Grounded

logger = logging.getLogger(__name__)

# How much to trust a page at all, before anything on it is considered.
# Handwriting is not slightly harder to read than print — it is a different
# problem, and the number says so.
MODALITY_PRIOR = {"digital": 1.0, "scanned": 0.8, "handwritten": 0.55}
DEFAULT_PRIOR = 0.8

# How much of the page penalty a confirmed drug name cancels.
#
# When RxNorm confirms a name is a real medicine, that is independent evidence
# the name was read correctly: a misreading would usually produce something
# that is not a drug at all and would fail the lookup. Ignoring that would
# leave the check one-sided, since an *un*identified drug already forces a
# review while an identified one earns nothing.
#
# Half, not all, because RxNorm confirms the name and nothing else. '50 mg'
# could still have been misread as '500 mg' and no lookup would notice.
IDENTITY_RECOVERY = 0.5

# What kind of mistake each fact would be.
RISK_BY_TYPE = {
    "Medication": "high",
    "Allergy": "high",
    "LabResult": "medium",  # raised to high when the result is abnormal
    "Diagnosis": "medium",
    "Procedure": "medium",
    "VitalSign": "low",
}
DEFAULT_RISK = "low"

# Below this, an item of that risk is sent for review.
THRESHOLDS = {"high": 0.85, "medium": 0.70, "low": 0.50}

ACCEPTED = "accepted"
NEEDS_REVIEW = "needs_review"


@dataclass
class Judgement:
    """The verdict on one extracted fact."""

    path: str
    kind: str
    risk: str
    confidence: float
    status: str
    reasons: list[str] = field(default_factory=list)
    page_no: int | None = None
    signals: dict[str, float] = field(default_factory=dict)

    @property
    def needs_review(self) -> bool:
        return self.status == NEEDS_REVIEW

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "risk": self.risk,
            "confidence": round(self.confidence, 3),
            "status": self.status,
            "reasons": self.reasons,
            "page_no": self.page_no,
            "signals": {k: round(v, 3) for k, v in self.signals.items()},
        }


def _is_abnormal(item: BaseModel, normalized: Mapping[str, Any]) -> bool:
    """Whether a lab result sits outside its reference range.

    An abnormal result is the one a patient acts on, so it is held to the same
    standard as a medicine rather than the standard of a routine normal value.
    """
    if normalized.get("computed_flag") in ("H", "L"):
        return True
    printed = (getattr(item, "flag", None) or "").strip().upper()[:1]
    return printed in ("H", "L", "A")


def judge_item(
    path: str,
    item: Grounded,
    grounding: Mapping[str, Any] | None,
    normalized: Mapping[str, Any],
    drugs: Mapping[str, Any],
    modalities: Mapping[int, str],
    fallback_modality: str,
) -> Judgement:
    """Weigh one extracted fact."""
    kind = type(item).__name__
    risk = RISK_BY_TYPE.get(kind, DEFAULT_RISK)
    reasons: list[str] = []

    # --- the three signals ---
    model_score = float(getattr(item, "confidence", 0.0) or 0.0)

    span_score = float((grounding or {}).get("score") or 0.0)
    match = (grounding or {}).get("match") or "missing"
    page_no = (grounding or {}).get("page_no")

    modality = modalities.get(page_no, fallback_modality) if page_no else fallback_modality
    page_prior = MODALITY_PRIOR.get(modality, DEFAULT_PRIOR)

    # A confirmed drug name is a fourth, independent check on the reading, so
    # it takes back part of the page penalty. Medicines only — nothing else
    # here is verified against an outside source.
    drug = getattr(item, "drug", None) if kind == "Medication" else None
    identity = drugs.get(drug) if drug else None
    identified = bool(identity) and identity.get("status") == "verified"

    modality_prior = page_prior
    if identified:
        modality_prior = page_prior + (1 - page_prior) * IDENTITY_RECOVERY

    confidence = model_score * span_score * modality_prior

    signals = {"model": model_score, "grounding": span_score, "modality": modality_prior}
    if identified and modality_prior > page_prior:
        signals["page_quality"] = page_prior
        reasons.append("the drug name was confirmed against RxNorm")

    if modality == "handwritten":
        reasons.append("read from handwriting")
    elif modality == "scanned":
        reasons.append("read from a scan or photo")

    # --- warning signs that force a review whatever the score ---
    forced = False

    if match in ("not_found", "empty", "missing"):
        reasons.append("the quoted text could not be found on the page")
        forced = True
    elif match == "fuzzy":
        reasons.append("the quoted text only roughly matches the page")

    if getattr(item, "legible", True) is False:
        reasons.append("part of this could not be read clearly")
        forced = True

    if kind == "LabResult" and _is_abnormal(item, normalized):
        risk = "high"
        reasons.append("result is outside the normal range")

    if "flag_conflict" in normalized:
        conflict = normalized["flag_conflict"]
        reasons.append(
            f"the lab printed '{conflict['printed']}' but the range says "
            f"'{conflict['computed']}'"
        )
        forced = True

    for name in ("collected_at", "measured_at", "onset", "performed_at"):
        if normalized.get(name, {}).get("ambiguous"):
            reasons.append(f"the {name.replace('_', ' ')} could be read two ways")
            forced = True

    if kind == "Medication" and not identified:
        if not drug:
            reasons.append("no drug name could be read")
        else:
            reasons.append("this medicine could not be identified, so it was not safety-checked")
        forced = True

    # --- the decision ---
    threshold = THRESHOLDS.get(risk, THRESHOLDS["low"])
    if forced:
        status = NEEDS_REVIEW
    elif confidence < threshold:
        status = NEEDS_REVIEW
        reasons.append(
            f"confidence {confidence:.2f} is below the {threshold:.2f} needed for "
            f"{risk}-risk information"
        )
    else:
        status = ACCEPTED

    return Judgement(
        path=path,
        kind=kind,
        risk=risk,
        confidence=confidence,
        status=status,
        reasons=reasons,
        page_no=page_no,
        signals=signals,
    )


def _walk(value: Any, path: str, out: list[tuple[str, Grounded]]) -> None:
    if isinstance(value, Grounded):
        out.append((path, value))
        return
    if isinstance(value, BaseModel):
        for name, child in value:
            _walk(child, f"{path}.{name}" if path else name, out)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk(child, f"{path}[{index}]", out)


def _worst_modality(modalities: Mapping[int, str], pages: Sequence[int]) -> str:
    """The least reliable page in the group.

    Used when an item was never grounded to a particular page: assuming the
    best page would flatter an item we know least about.
    """
    order = ["digital", "scanned", "handwritten"]
    present = [modalities.get(page) for page in pages if modalities.get(page)]
    return max(present, key=order.index) if present else "scanned"


def review_group(
    data: BaseModel,
    grounding: Sequence[Mapping[str, Any]],
    normalized: Mapping[str, Mapping[str, Any]],
    drugs: Mapping[str, Any],
    modalities: Mapping[int, str],
    pages: Sequence[int],
) -> list[Judgement]:
    """Weigh every fact in one filled-in form.

    All four inputs are keyed the same way — by the path of the item inside the
    extraction — so a value, its verification and its parsed form line up.
    """
    by_path = {entry["path"]: entry for entry in grounding if entry.get("path")}
    fallback = _worst_modality(modalities, pages)

    items: list[tuple[str, Grounded]] = []
    _walk(data, "", items)

    return [
        judge_item(
            path, item, by_path.get(path), normalized.get(path, {}), drugs, modalities, fallback
        )
        for path, item in items
    ]


def summarise(judgements: Sequence[Judgement]) -> dict[str, Any]:
    """Counts for the document record and for what the interface tells the user."""
    total = len(judgements)
    review = [j for j in judgements if j.needs_review]
    return {
        "items": total,
        "accepted": total - len(review),
        "needs_review": len(review),
        "high_risk_needing_review": sum(1 for j in review if j.risk == "high"),
        "mean_confidence": round(sum(j.confidence for j in judgements) / total, 3) if total else 0.0,
        "lowest_confidence": round(min((j.confidence for j in judgements), default=0.0), 3),
    }
