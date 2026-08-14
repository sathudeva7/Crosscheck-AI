"""The four checks that compare medicines against each other.

    interaction        a pair listed in interaction_rule
    duplicate_therapy  two drugs of the same WHO family
    allergy_conflict   a drug matching something the patient reacts to
    dosage_conflict    one drug at two different strengths at once

All four work across the whole record, not within one document. A drug
prescribed by a cardiologist in March and one prescribed by a GP in August are
exactly the pair nobody is looking at, and the reason a tool like this is worth
building.
"""

import logging
from itertools import combinations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.checks.base import (
    SEVERITY_FROM_DDINTER,
    Candidate,
    assumption_note,
    atc_families,
    checkable,
    overlaps,
    regimen,
)
from app.db.models import Drug, InteractionRule, TimelineEvent

logger = logging.getLogger(__name__)


def _pair_key(kind: str, a: str, b: str) -> str:
    return f"{kind}:" + ":".join(sorted((a, b)))


def check_interactions(db: Session, events: list[TimelineEvent]) -> list[Candidate]:
    """Every pair of the patient's medicines, looked up in interaction_rule."""
    medicines = checkable(events)
    found: list[Candidate] = []

    for first, second in combinations(medicines, 2):
        if first.rxcui == second.rxcui:
            continue

        together, certain = overlaps(first, second)
        if not together:
            continue

        key = tuple(sorted((first.rxcui, second.rxcui)))
        rule = db.get(InteractionRule, key)
        if rule is None:
            continue

        severity = SEVERITY_FROM_DDINTER.get(rule.severity, "info")
        name_a = first.drug_name or first.drug_raw
        name_b = second.drug_name or second.drug_raw

        graded = rule.severity if rule.severity != "Unknown" else "ungraded"
        found.append(
            Candidate(
                key=_pair_key("interaction", *key),
                finding_type="interaction",
                severity=severity,
                title=f"{name_a.title()} and {name_b.title()} may interact ({graded})",
                evidence={
                    "drugs": [name_a, name_b],
                    "regimens": [regimen(first), regimen(second)],
                    "rxcuis": list(key),
                    "ddinter_severity": rule.severity,
                    "source_names": [rule.name_a, rule.name_b],
                    "source": "DDInter 2.0",
                    "overlap_certain": certain,
                    "assumption": assumption_note(certain),
                },
                event_ids=[first.id, second.id],
                visit_ids=list({first.visit_id, second.visit_id}),
            )
        )

    return found


def check_duplicates(db: Session, events: list[TimelineEvent]) -> list[Candidate]:
    """Two different drugs doing the same job at the same time.

    Not the same drug twice — that is the dosage check. This is two *different*
    ingredients from one therapeutic family, which is how a patient ends up on
    two beta blockers because two clinicians each prescribed one.
    """
    medicines = checkable(events)
    found: list[Candidate] = []

    for first, second in combinations(medicines, 2):
        if first.rxcui == second.rxcui:
            continue

        shared = atc_families(first) & atc_families(second)
        if not shared:
            continue

        together, certain = overlaps(first, second)
        if not together:
            continue

        family = sorted(shared)[0]
        label = _family_name(db, family) or family
        name_a = first.drug_name or first.drug_raw
        name_b = second.drug_name or second.drug_raw

        found.append(
            Candidate(
                key=_pair_key("duplicate", first.rxcui, second.rxcui),
                finding_type="duplicate_therapy",
                severity="warning",
                title=f"{name_a.title()} and {name_b.title()} are both {label.lower()}",
                evidence={
                    "drugs": [name_a, name_b],
                    "regimens": [regimen(first), regimen(second)],
                    "rxcuis": [first.rxcui, second.rxcui],
                    "shared_atc": sorted(shared),
                    "family_name": label,
                    "atc_codes": [first.atc_codes, second.atc_codes],
                    "overlap_certain": certain,
                    "assumption": assumption_note(certain),
                },
                event_ids=[first.id, second.id],
                visit_ids=list({first.visit_id, second.visit_id}),
            )
        )

    return found


_FAMILY_NAME_SQL = text(
    """
    SELECT name
    FROM (
        SELECT unnest(atc_codes) AS code, unnest(atc_names) AS name FROM drug
    ) AS classes
    WHERE code LIKE :prefix || '%' AND name <> ''
    LIMIT 1
    """
)


def _family_name(db: Session, prefix: str) -> str | None:
    """A readable name for an ATC family, from any drug that carries it.

    'C07A' means nothing to a patient; 'beta blocking agents' does. The names
    were loaded alongside the codes in 004, so this needs no lookup service —
    any drug in the family carries the wording.
    """
    return db.scalar(_FAMILY_NAME_SQL, {"prefix": prefix})


def check_allergies(db: Session, events: list[TimelineEvent]) -> list[Candidate]:
    """A prescribed medicine matching something the patient reacts to.

    Compared by drug family, not by name, because the names rarely match: a
    penicillin allergy and a prescription for amoxicillin share no letters at
    all, and only their ATC family (J01C) connects them.
    """
    allergies = [
        event
        for event in events
        if event.event_type == "allergy"
        and event.review_status != "rejected"
        and (event.substance or "").strip()
    ]
    if not allergies:
        return []

    medicines = checkable(events)
    found: list[Candidate] = []

    for allergy in allergies:
        substance = allergy.substance.strip()
        families, rxcui = _substance_families(db, substance)
        if not families:
            continue

        for medicine in medicines:
            shared = families & atc_families(medicine)
            if not shared:
                continue
            # A drug recorded as an allergy and then prescribed is the same
            # ingredient, not merely the same family — still a conflict.
            name = medicine.drug_name or medicine.drug_raw
            found.append(
                Candidate(
                    key=f"allergy:{substance.lower()}:{medicine.rxcui}",
                    finding_type="allergy_conflict",
                    severity="danger",
                    title=f"{name.title()} may be unsafe with your {substance.lower()} allergy",
                    evidence={
                        "allergy": substance,
                        "allergy_reaction": allergy.reaction,
                        "allergy_recorded": str(allergy.event_date)
                        if allergy.event_date
                        else None,
                        "drug": name,
                        "regimen": regimen(medicine),
                        "rxcui": medicine.rxcui,
                        "shared_atc": sorted(shared),
                        "matched_by": "same drug family"
                        if medicine.rxcui != rxcui
                        else "same ingredient",
                    },
                    event_ids=[allergy.id, medicine.id],
                    visit_ids=list({allergy.visit_id, medicine.visit_id}),
                )
            )

    return found


# Allergies are usually recorded as a family, not as an ingredient. Nobody
# writes 'allergic to benzylpenicillin' — they write 'penicillin', which is not
# a drug name at all and appears nowhere in the ingredient table. Without this
# map the single most commonly recorded drug allergy would silently never match
# anything.
#
# Kept deliberately short: only the families patients actually report, each
# mapped to the ATC group it covers.
ALLERGY_FAMILIES: dict[str, set[str]] = {
    "penicillin": {"J01C"},
    "penicillins": {"J01C"},
    "amoxicillin": {"J01C"},
    "cephalosporin": {"J01D"},
    "cephalosporins": {"J01D"},
    "sulfa": {"J01E"},
    "sulpha": {"J01E"},
    "sulfonamide": {"J01E"},
    "sulphonamide": {"J01E"},
    "macrolide": {"J01F"},
    "macrolides": {"J01F"},
    "erythromycin": {"J01F"},
    "tetracycline": {"J01A"},
    "quinolone": {"J01M"},
    "quinolones": {"J01M"},
    "fluoroquinolone": {"J01M"},
    "nsaid": {"M01A"},
    "nsaids": {"M01A"},
    "ibuprofen": {"M01A"},
    "opioid": {"N02A"},
    "opioids": {"N02A"},
    "opiate": {"N02A"},
    "codeine": {"N02A"},
    "morphine": {"N02A"},
    "statin": {"C10A"},
    "statins": {"C10A"},
    "sulfonylurea": {"A10B"},
    "ace inhibitor": {"C09A"},
}


def _substance_families(db: Session, substance: str) -> tuple[set[str], str | None]:
    """The drug families a recorded allergy covers.

    Three attempts, narrowing as they go: the family map above, then the
    ingredient by name, then a name containing it — 'penicillin' is the tail of
    'benzylpenicillin'.

    A substance that is not a drug at all — a food, a dye, latex — matches
    nothing and correctly produces no finding rather than a guess.
    """
    from app.checks.base import ATC_PREFIX

    key = substance.lower().strip()
    if key in ALLERGY_FAMILIES:
        return set(ALLERGY_FAMILIES[key]), None

    drug = db.scalar(select(Drug).where(Drug.name == key))
    if drug is None:
        drug = db.scalar(select(Drug).where(Drug.name.like(f"%{key}%")).limit(1))
    if drug is None:
        return set(), None

    return (
        {code[:ATC_PREFIX] for code in (drug.atc_codes or []) if len(code) >= ATC_PREFIX},
        drug.rxcui,
    )


def check_dosage_conflicts(db: Session, events: list[TimelineEvent]) -> list[Candidate]:
    """The same drug prescribed at two different strengths at the same time.

    Usually a deliberate dose change, sometimes two clinicians unaware of each
    other. Reported as information rather than a warning, and worded so it does
    not accuse anyone of a mistake.
    """
    medicines = [event for event in checkable(events) if event.strength_value is not None]
    found: list[Candidate] = []

    for first, second in combinations(medicines, 2):
        if first.rxcui != second.rxcui:
            continue
        if first.strength_unit != second.strength_unit:
            continue
        if first.strength_value == second.strength_value:
            continue

        together, certain = overlaps(first, second)
        if not together:
            continue

        name = first.drug_name or first.drug_raw
        low, high = sorted(
            (
                (first.strength_value, first.event_date),
                (second.strength_value, second.event_date),
            ),
            key=lambda pair: pair[0],
        )
        found.append(
            Candidate(
                key=f"dosage:{first.rxcui}:{low[0]:g}:{high[0]:g}",
                finding_type="dosage_conflict",
                severity="info",
                title=f"{name.title()} appears at two different strengths",
                evidence={
                    "drug": name,
                    "regimens": [regimen(first), regimen(second)],
                    "rxcui": first.rxcui,
                    "strengths": [
                        f"{low[0]:g} {first.strength_unit} on {low[1]}",
                        f"{high[0]:g} {first.strength_unit} on {high[1]}",
                    ],
                    "overlap_certain": certain,
                    "assumption": assumption_note(certain),
                },
                event_ids=[first.id, second.id],
                visit_ids=list({first.visit_id, second.visit_id}),
            )
        )

    return found
