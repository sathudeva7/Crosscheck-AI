"""What to do when the interaction table has no rule for a pair.

Two lanes, tried in order, and neither of them lets a model decide anything.

  1. The drug's *class*. If every other beta blocker we know of is dangerous
     with verapamil, that is worth saying about a beta blocker we have no rule
     for. Derived from `drug` and `interaction_rule` — the same data the graded
     findings come from, read a different way.

  2. The FDA's own label text, from openFDA. Free, no key, and quotable, which
     matters: this lane returns a sentence someone can check rather than a
     verdict they have to trust.

Both are reported as weaker than a graded rule and never merged into one. A
class-level warning is a reason to ask a pharmacist, not a finding about the
patient's actual drug.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.checks.base import ATC_PREFIX
from app.config import ENABLE_OPENFDA, OPENFDA_BASE_URL, OPENFDA_TIMEOUT
from app.db.models import Drug, InteractionRule

logger = logging.getLogger(__name__)

# --- class inference ---------------------------------------------------------

# One class-mate agreeing is a coincidence. Three is a pattern.
MIN_CLASSMATES = 3

# How much of the class has to agree. Beta blockers against verapamil came back
# 16 of 16; eye drops against warfarin came back 12 of 20, which is the shape of
# noise rather than a class effect.
MIN_SHARE = 0.7

# 'Unknown' means DDInter lists the pair without grading it. A class built from
# ungraded pairs says nothing, and every eye-drop match was Unknown.
GRADED = ("Major", "Moderate", "Minor")


@dataclass
class ClassSignal:
    """A warning inferred from a drug's class rather than from the drug itself."""

    subject: str
    """The drug whose class carried the signal — the wording depends on which
    of the two it was, and getting that backwards makes the sentence a lie."""

    against: str
    atc_class: str
    class_name: str
    total: int
    interacting: int
    severity: str
    examples: list[str]

    @property
    def share(self) -> float:
        return self.interacting / self.total


def _rule(db: Session, rxcui_a: str, rxcui_b: str) -> InteractionRule | None:
    """The table stores each pair once, with rxcui_a < rxcui_b."""
    low, high = sorted((rxcui_a, rxcui_b))
    return db.scalar(
        select(InteractionRule).where(
            InteractionRule.rxcui_a == low, InteractionRule.rxcui_b == high
        )
    )


def class_signal(db: Session, rxcui: str, against: str) -> ClassSignal | None:
    """Whether this drug's class is known to interact with the other drug.

    Returns None unless the class is large enough, agrees strongly enough, and
    agrees on a graded severity — the three guards that separate 'beta blockers
    interact with verapamil' from 'these are both eye drops'.
    """
    drug = db.scalar(select(Drug).where(Drug.rxcui == rxcui))
    if drug is None or not drug.atc_codes:
        return None

    families = {code[:ATC_PREFIX] for code in drug.atc_codes if len(code) >= ATC_PREFIX}
    if not families:
        return None

    best: ClassSignal | None = None

    for family in sorted(families):
        peers = [
            other
            for other in db.scalars(select(Drug)).all()
            if other.rxcui != rxcui
            and any((code or "").startswith(family) for code in other.atc_codes or [])
        ]
        if len(peers) < MIN_CLASSMATES:
            continue

        hits = [(p, r) for p in peers if (r := _rule(db, p.rxcui, against))]
        graded = [(p, r) for p, r in hits if r.severity in GRADED]
        if not graded or len(graded) / len(peers) < MIN_SHARE:
            continue

        # The class is only as reassuring as its worst member.
        severity = min((r.severity for _, r in graded), key=GRADED.index)
        other = db.scalar(select(Drug).where(Drug.rxcui == against))
        signal = ClassSignal(
            subject=drug.name,
            against=other.name if other else against,
            atc_class=family,
            class_name=(drug.atc_names or [family])[0],
            total=len(peers),
            interacting=len(graded),
            severity=severity,
            examples=[p.name for p, _ in graded[:4]],
        )
        if best is None or GRADED.index(signal.severity) < GRADED.index(best.severity):
            best = signal

    return best


def class_signal_between(db: Session, rxcui_a: str, rxcui_b: str) -> ClassSignal | None:
    """The strongest class signal in either direction.

    Both readings are legitimate and only one may exist: 'every beta blocker
    interacts with verapamil' is a fact about beta blockers, and whether the
    unknown drug is the beta blocker or the verapamil depends which one the
    patient is already taking. Checking one direction only silently loses half
    the cases — which is exactly what happened the first time this ran.
    """
    candidates = [
        signal
        for signal in (class_signal(db, rxcui_a, rxcui_b), class_signal(db, rxcui_b, rxcui_a))
        if signal is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda s: (GRADED.index(s.severity), -s.share))


# --- the FDA's own label -----------------------------------------------------

# How much text either side of the match to quote back.
SNIPPET_CHARS = 220


@dataclass
class LabelMention:
    """A drug named in another drug's FDA label."""

    subject: str
    mentions: str
    snippet: str
    source: str = "FDA label via openFDA"


def _snippet(text: str, needle: str) -> str | None:
    """The sentence around a mention, rather than six thousand characters."""
    match = re.search(rf"\b{re.escape(needle)}\w*", text, re.IGNORECASE)
    if match is None:
        return None
    start = max(0, match.start() - SNIPPET_CHARS // 2)
    end = min(len(text), match.end() + SNIPPET_CHARS // 2)
    return ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else "")


async def _interactions_text(client: httpx.AsyncClient, generic: str) -> str:
    """The Drug Interactions section of a drug's label, or empty."""
    response = await client.get(
        f"{OPENFDA_BASE_URL}/drug/label.json",
        params={"search": f'openfda.generic_name:"{generic}"', "limit": 1},
    )
    # 404 is openFDA's answer for 'no such drug', which is information, not a
    # failure — every other status is a problem worth logging.
    if response.status_code == 404:
        return ""
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        return ""
    return " ".join(results[0].get("drug_interactions") or [])


async def label_mention(first: str, second: str) -> LabelMention | None:
    """Whether either drug's FDA label names the other.

    Checked both ways: warfarin's label lists NSAIDs, and ibuprofen's lists
    anticoagulants, and only one of the two may mention the other by name.

    Never raises. openFDA being unreachable means one lane is unavailable, not
    that the answer is 'safe' — the caller reports the gap either way.
    """
    if not ENABLE_OPENFDA:
        return None

    try:
        async with httpx.AsyncClient(timeout=OPENFDA_TIMEOUT) as client:
            for subject, other in ((first, second), (second, first)):
                text = await _interactions_text(client, subject)
                if not text:
                    continue
                snippet = _snippet(text, other)
                if snippet:
                    return LabelMention(subject=subject, mentions=other, snippet=snippet)
    except (httpx.HTTPError, ValueError) as problem:
        logger.info("openFDA lookup failed for %s/%s: %s", first, second, problem)
        return None

    return None
