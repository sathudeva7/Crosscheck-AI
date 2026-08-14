"""Which kind of doctor a finding should be taken to.

The routing rule that matters is not the obvious one. A drug interaction is not
a heart problem because the drugs are heart drugs — it is a *prescribing*
problem, and the person who can settle it is a pharmacist or whoever wrote the
two prescriptions. So `finding_type` is consulted before anything about the
drugs themselves, and the drug's class only breaks ties.

Where the organ genuinely does decide is lab findings. A creatinine trend names
the kidney directly, which is why analyte routing is far tighter than drug
routing and is trusted accordingly.

Three things this deliberately does not do:

- It does not use a model. The mapping is a table a clinician can read and
  argue with, which is the only form in which 'go and see a cardiologist' is
  defensible.
- It does not return one answer. It returns two or three with reasons, and a
  general practitioner is always among them. The app is not claiming to know
  which specialist you need; it is narrowing from every doctor in the country
  to a few sensible ones.
- It does not route on the first letter of an ATC code. `N` covers epilepsy,
  depression and nerve pain; `N03` and `N05A` do not. Every rule here reads at
  least three characters, and the tables are keyed on the longest prefix that
  still means one thing.

Search terms are eChannelling's own words. Their directory says 'Chest
Specialist', not 'pulmonologist', and a ladder that ends in the wrong vocabulary
finds nobody. Each rung is tried in turn, so a specialty they do not list
degrades to one they do rather than to nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# --- what a route looks like -------------------------------------------------


@dataclass
class Specialty:
    """One kind of doctor this finding could reasonably be taken to."""

    label: str  # what the patient reads: 'Cardiologist'
    reason: str  # why this finding routed here
    confidence: str  # 'high' | 'medium' | 'low'
    search_terms: list[str] = field(default_factory=list)  # eChannelling words, best first
    places_query: str | None = None  # what to ask Google when no doctor is listed

    def query_for_places(self, location: str) -> str:
        return f"{self.places_query or self.label} in {location}"


# The floor. Never omitted, never first. A GP can look at anything and refer
# onward, so a search that returns only this is still an answer.
GENERAL_PRACTITIONER = Specialty(
    label="General practitioner",
    reason="A general doctor can review any of this and refer you on if needed.",
    confidence="high",
    search_terms=["General Physician", "Consultant Physician", "Physician"],
    places_query="general practitioner clinic",
)

# The prescribing problems. A pharmacist reconciles medicines for a living and
# is, for most people, the most reachable clinician there is.
PHARMACIST = Specialty(
    label="Pharmacist",
    reason="This is about how two prescriptions fit together, which a pharmacist reviews directly.",
    confidence="high",
    search_terms=[],  # eChannelling channels doctors, not pharmacists
    places_query="pharmacy",
)


# --- lab analytes: the organ is named, so these are the confident routes ------
#
# Keys are matched as whole words against the analyte name, so a key may be the
# canonical name or any of the ways a laboratory actually prints it. Sri Lankan
# reports name the assay as well as the test — 'SERUM CREATININE (mod.Jaffe)',
# 'Platelet Count (Electrical impedance)' — so the spellings live here rather
# than in a separate alias table that would have to be read alongside this one.
NEPHROLOGY = ["Nephrologist", "Nephrologist And Genaral Physician"]
HEPATOLOGY = ["Hepatologists", "Gastroenterologist And Hepatologist", "Gastroenterologist"]
ENDOCRINE = ["Endocrinologist And Diabetologist", "Endocrinologist"]
CARDIOLOGY = ["Cardiologist"]
HAEMATOLOGY = ["Haematologist", "Clinical Haematologist", "Consultant Haematologist"]

ANALYTE_ROUTES: dict[str, tuple[str, list[str], str]] = {
    # kidney
    "creatinine": ("Nephrologist", NEPHROLOGY, "kidney function"),
    "egfr": ("Nephrologist", NEPHROLOGY, "kidney function"),
    "urea": ("Nephrologist", NEPHROLOGY, "kidney function"),
    # 'BUN (Urease/GLDH)'. Until word matching landed this scored as 'urea'
    # because the letters sit inside 'urease' — the right answer for the wrong
    # reason, and only ever an accident of spelling.
    "bun": ("Nephrologist", NEPHROLOGY, "kidney function"),
    "potassium": ("Nephrologist", NEPHROLOGY, "a salt the kidneys control"),
    "sodium": ("Nephrologist", NEPHROLOGY, "a salt the kidneys control"),
    "chloride": ("Nephrologist", NEPHROLOGY, "a salt the kidneys control"),
    "bicarbonate": ("Nephrologist", NEPHROLOGY, "your blood's acid balance"),
    # liver
    "alt": ("Hepatologist", HEPATOLOGY, "liver enzymes"),
    "ast": ("Hepatologist", HEPATOLOGY, "liver enzymes"),
    "sgpt": ("Hepatologist", HEPATOLOGY, "liver enzymes"),
    "sgot": ("Hepatologist", HEPATOLOGY, "liver enzymes"),
    "alp": ("Hepatologist", HEPATOLOGY, "liver enzymes"),
    "ggt": ("Hepatologist", HEPATOLOGY, "liver enzymes"),
    "bilirubin": ("Hepatologist", HEPATOLOGY, "liver function"),
    "albumin": ("Hepatologist", HEPATOLOGY, "a protein the liver makes"),
    # sugar and hormones
    "hba1c": ("Endocrinologist", ENDOCRINE, "blood sugar control"),
    "glucose": ("Endocrinologist", ENDOCRINE, "blood sugar"),
    "tsh": ("Endocrinologist", ENDOCRINE, "thyroid function"),
    "t3": ("Endocrinologist", ENDOCRINE, "thyroid function"),
    "t4": ("Endocrinologist", ENDOCRINE, "thyroid function"),
    # heart and blood fats
    "cholesterol": ("Cardiologist", CARDIOLOGY, "cholesterol"),
    "ldl": ("Cardiologist", CARDIOLOGY, "cholesterol"),
    "hdl": ("Cardiologist", CARDIOLOGY, "cholesterol"),
    "vldl": ("Cardiologist", CARDIOLOGY, "cholesterol"),
    "triglycerides": ("Cardiologist", CARDIOLOGY, "blood fats"),
    "apolipoprotein": ("Cardiologist", CARDIOLOGY, "a marker of heart risk"),
    "apo b": ("Cardiologist", CARDIOLOGY, "a marker of heart risk"),
    "lipoprotein": ("Cardiologist", CARDIOLOGY, "a marker of heart risk"),
    "troponin": ("Cardiologist", CARDIOLOGY, "a heart marker"),
    "hscrp": ("Cardiologist", CARDIOLOGY, "an inflammation marker used to judge heart risk"),
    "hs crp": ("Cardiologist", CARDIOLOGY, "an inflammation marker used to judge heart risk"),
    # Spelled out as well as abbreviated, because longest-key-first is what
    # separates the cardiac assay from plain CRP further down, and the report
    # here prints both words and abbreviation: 'CARDIO C-REACTIVE PROTEIN
    # (hsCRP), SERUM'. Without this key the generic route wins on length alone.
    "cardio c reactive protein": (
        "Cardiologist",
        CARDIOLOGY,
        "an inflammation marker used to judge heart risk",
    ),
    "bp": ("Cardiologist", CARDIOLOGY, "blood pressure"),
    "blood pressure": ("Cardiologist", CARDIOLOGY, "blood pressure"),
    "inr": ("Cardiologist", ["Cardiologist", "Haematologist"], "blood clotting"),
    # blood count. The differential is one panel and one specialty; listing the
    # parts separately is what stops 'Neutrophil Absolute' falling through to a
    # GP while 'Haemoglobin' on the same report routes correctly.
    "haemoglobin": ("Haematologist", HAEMATOLOGY, "your blood count"),
    "hemoglobin": ("Haematologist", HAEMATOLOGY, "your blood count"),
    "haematocrit": ("Haematologist", HAEMATOLOGY, "your blood count"),
    "hematocrit": ("Haematologist", HAEMATOLOGY, "your blood count"),
    "pcv": ("Haematologist", HAEMATOLOGY, "your blood count"),
    "platelet": ("Haematologist", HAEMATOLOGY, "your platelet count"),
    "platelets": ("Haematologist", HAEMATOLOGY, "your platelet count"),
    "mpv": ("Haematologist", HAEMATOLOGY, "your platelet size"),
    "wbc": ("Haematologist", HAEMATOLOGY, "your white cell count"),
    "rbc": ("Haematologist", HAEMATOLOGY, "your red cell count"),
    "mcv": ("Haematologist", HAEMATOLOGY, "the size of your red cells"),
    "mch": ("Haematologist", HAEMATOLOGY, "your red cell measurements"),
    "mchc": ("Haematologist", HAEMATOLOGY, "your red cell measurements"),
    "rdw": ("Haematologist", HAEMATOLOGY, "your red cell measurements"),
    "neutrophil": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "neutrophils": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "lymphocyte": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "lymphocytes": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "monocyte": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "monocytes": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "eosinophil": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "eosinophils": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "basophil": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "basophils": ("Haematologist", HAEMATOLOGY, "a type of white blood cell"),
    "esr": ("Haematologist", HAEMATOLOGY, "a general marker of inflammation"),
    # infection. Both directories list this under sexual health rather than
    # under infectious disease, which is where the national programme sits.
    "hiv": (
        "Venereologist",
        ["Venereologist", "Venereologist (S.T.D)", "Std Hiv Sexual Health"],
        "an HIV test",
    ),
    "human immunodeficiency virus": (
        "Venereologist",
        ["Venereologist", "Venereologist (S.T.D)", "Std Hiv Sexual Health"],
        "an HIV test",
    ),
    # Plain CRP is inflammation anywhere, so it stops at a general doctor. The
    # cardiac-sensitive assay above is a different question and is keyed longer
    # so that it wins whenever a report names it.
    "c reactive protein": (
        "General practitioner",
        ["General Physician", "Consultant Physician"],
        "a marker of inflammation",
    ),
}


# --- drug classes: read deep enough to mean something -------------------------
#
# Longest prefix wins, so a rule can be as specific as it needs to be. Three
# characters is the floor: 'N' alone routes nowhere useful, 'N03' is
# antiepileptics and 'N05A' is antipsychotics.
ATC_ROUTES: dict[str, tuple[str, list[str], str]] = {
    # cardiovascular
    "C01": ("Cardiologist", ["Cardiologist"], "a heart medicine"),
    "C03": ("Cardiologist", ["Cardiologist", "Nephrologist"], "a water tablet"),
    "C07": ("Cardiologist", ["Cardiologist"], "a beta blocker"),
    "C08": ("Cardiologist", ["Cardiologist"], "a blood pressure medicine"),
    "C09": ("Cardiologist", ["Cardiologist"], "a blood pressure medicine"),
    "C10": ("Cardiologist", ["Cardiologist"], "a cholesterol medicine"),
    # blood
    "B01": ("Cardiologist", ["Cardiologist", "Haematologist"], "a blood thinner"),
    "B03": ("Haematologist", ["Haematologist"], "a medicine for anaemia"),
    # metabolism
    "A02": ("Gastroenterologist", ["Gastroenterologist", "Gastrologist"], "a stomach acid medicine"),
    "A10": ("Endocrinologist", ["Endocrinologist And Diabetologist", "Endocrinologist"], "a diabetes medicine"),
    # nervous system — the level that matters
    "N02": ("General practitioner", ["General Physician", "Consultant Physician"], "a painkiller"),
    "N03": ("Neuro Physician", ["Neuro Physician", "Neuro Surgeon"], "a medicine for seizures or nerve pain"),
    "N04": ("Neuro Physician", ["Neuro Physician"], "a Parkinson's medicine"),
    "N05A": ("Psychiatrist", ["Psychiatrist"], "an antipsychotic"),
    "N05B": ("Psychiatrist", ["Psychiatrist"], "a medicine for anxiety"),
    "N05C": ("Psychiatrist", ["Psychiatrist"], "a sleeping medicine"),
    "N06A": ("Psychiatrist", ["Psychiatrist"], "an antidepressant"),
    # musculoskeletal
    "M01": ("General practitioner", ["General Physician", "Rheumatologist"], "an anti-inflammatory"),
    "M04": ("Rheumatologist", ["Rheumatologist"], "a gout medicine"),
    "M05": ("Rheumatologist", ["Rheumatologist", "Endocrinologist"], "a bone medicine"),
    # respiratory
    "R03": ("Chest Specialist", ["Chest Specialist", "Chest Physician"], "an inhaler or asthma medicine"),
    "R06": ("Allergy Specialist", ["Allergy Specialist", "Allergy And Immunology"], "an antihistamine"),
    # anti-infectives, hormones, cancer
    "J01": ("General practitioner", ["General Physician", "Consultant Physician"], "an antibiotic"),
    "H02": ("Endocrinologist", ["Endocrinologist"], "a steroid"),
    "H03": ("Endocrinologist", ["Endocrinologist"], "a thyroid medicine"),
    "L01": ("Oncologist", ["Oncologist", "Consultant Oncologist"], "a cancer medicine"),
    "L04": ("Rheumatologist", ["Rheumatologist"], "a medicine that damps the immune system"),
    # skin, eyes, urinary
    "D": ("Dermatologist", ["Dermatologist", "Dermatologist(Skin)"], "a skin medicine"),
    "S01": ("Eye Surgeon", ["Eye Surgeon"], "an eye medicine"),
    "G04": ("Urologist", ["Urologist"], "a urinary medicine"),
}

# Findings that are about prescribing rather than about an organ.
PRESCRIBING_TYPES = ("interaction", "duplicate_therapy", "dosage_conflict")


# Classes that describe where a medicine is applied rather than what it treats:
# a skin cream, an eye drop, a nasal spray, a mouth gel.
#
# They have to be ranked below systemic classes or they win by accident.
# Prednisolone carries eleven ATC codes because it is sold as tablets, drops,
# cream, spray and suppositories; a finding about the tablet interacting with a
# beta blocker would otherwise be routed to a dermatologist, because one of the
# eleven happened to sort first. Still offered when there is nothing else —
# a patient only on eye drops does want the eye surgeon.
LOCAL_ROUTES = ("D", "S01", "S02", "S03", "R01", "A01", "A07", "C05")

# How many organ specialties to offer alongside the lead. Two is enough to
# cover both drugs in a pair without turning the choice into a menu.
MAX_ORGAN_SPECIALTIES = 2


def _is_local(prefix: str) -> bool:
    return prefix in LOCAL_ROUTES


def _atc_matches(codes: Iterable[str]) -> list[tuple[str, tuple[str, list[str], str]]]:
    """Every rule these ATC codes match, most relevant first.

    All of them, not the first one found. A finding about two drugs involves
    two drugs, and picking one class out of the pile made the answer depend on
    the order the codes happened to arrive in — the same pair routed to
    cardiology or to endocrinology depending on which drug the title named
    first, which is not a medical distinction.

    Ordered by whether the class is systemic, then by how specific it is.
    """
    matched: dict[str, tuple[str, tuple[str, list[str], str]]] = {}

    for code in codes or []:
        code = (code or "").upper().strip()
        if not code:
            continue
        prefix = None
        for length in range(min(len(code), 7), 2, -1):
            if code[:length] in ATC_ROUTES:
                prefix = code[:length]
                break
        # 'D' is the one single-letter rule: dermatologicals are dermatology
        # whatever the subgroup, and no other first letter is that clean.
        if prefix is None and code[:1] == "D":
            prefix = "D"
        if prefix is None:
            continue

        rule = ATC_ROUTES[prefix]
        label = rule[0]
        # One specialty, reached once. Keep the most specific route to it.
        if label not in matched or len(prefix) > len(matched[label][0]):
            matched[label] = (prefix, rule)

    return sorted(
        matched.values(),
        key=lambda item: (_is_local(item[0]), -len(item[0]), item[0]),
    )


def _atc_specialties(
    codes: Iterable[str], confidence: str, lead: str, limit: int = MAX_ORGAN_SPECIALTIES
) -> list[Specialty]:
    """The organ specialties these drugs point at, best first."""
    out: list[Specialty] = []
    for prefix, (label, terms, description) in _atc_matches(codes)[:limit]:
        out.append(
            Specialty(
                label=label,
                reason=f"{lead} {description} (drug class {prefix}).",
                confidence=confidence,
                search_terms=terms,
            )
        )
    return out


_NOT_WORD = re.compile(r"[^a-z0-9]+")


def _words(text: str) -> str:
    """'*eGFR(Calculated)' -> ' egfr calculated '.

    Padded with spaces at both ends so a key can be tested for as ' key ' and
    match only a whole word.
    """
    return f" {_NOT_WORD.sub(' ', text.lower()).strip()} "


# Longest first, so a specific key beats a general one that it contains:
# 'c reactive protein' is answered before 'crp', and 'blood pressure' before
# any single word inside it.
_ANALYTE_KEYS = sorted(ANALYTE_ROUTES, key=lambda key: (-len(key), key))


def _analyte_specialty(analyte: str | None) -> Specialty | None:
    """Which doctor reads this measurement, matched on whole words.

    Whole words, because substring matching quietly sent fasting glucose to a
    liver specialist: 'ast' is the liver enzyme and it sits inside 'f-ast-ing'.
    That kind of mistake is invisible in review — the code looks reasonable and
    the answer is confidently wrong — and it is the reason this does not simply
    ask whether one string appears inside another.
    """
    text = _words(analyte or "")
    if not text.strip():
        return None

    for key in _ANALYTE_KEYS:
        if f" {key} " in text:
            label, terms, description = ANALYTE_ROUTES[key]
            return Specialty(
                label=label,
                reason=f"This result is about {description}.",
                confidence="high",
                search_terms=terms,
            )
    return None


def resolve(
    finding_type: str,
    evidence: dict | None = None,
    atc_codes: Iterable[str] | None = None,
) -> list[Specialty]:
    """Who to take this finding to, best first, with a GP always included.

    `evidence` is the finding's own evidence dictionary; `atc_codes` the drug
    classes involved, which the caller collects from the timeline events the
    finding rests on.
    """
    evidence = evidence or {}
    codes = list(atc_codes or [])
    for entry in evidence.get("atc_codes") or []:
        codes.extend(entry if isinstance(entry, list) else [entry])
    codes.extend(evidence.get("shared_atc") or [])

    found: list[Specialty] = []

    if finding_type in PRESCRIBING_TYPES:
        # The prescribing problem comes first: two interacting heart drugs do
        # not need a cardiologist to notice that they interact.
        found.append(PHARMACIST)
        # Both drugs, not whichever one's codes came back first. A pair of
        # medicines is two medicines, and either prescriber may be the right
        # person to take it to.
        found.extend(_atc_specialties(codes, confidence="medium", lead="One of these is"))

    elif finding_type == "allergy_conflict":
        found.append(
            Specialty(
                label="Allergy Specialist",
                reason="This is about a recorded allergy and a medicine related to it.",
                confidence="high",
                search_terms=["Allergy Specialist", "Allergy And Immunology", "Dermatologist"],
            )
        )

    elif finding_type in ("lab_trend", "critical_value"):
        organ = _analyte_specialty(evidence.get("analyte"))
        if organ:
            found.append(organ)

    else:
        found.extend(_atc_specialties(codes, confidence="medium", lead="This involves"))

    # A drug-class route is still worth offering when nothing above fired.
    if not found:
        found.extend(_atc_specialties(codes, confidence="low", lead="This involves"))

    found.append(GENERAL_PRACTITIONER)

    # Same specialty reached by two routes is one option, not two.
    unique: dict[str, Specialty] = {}
    for specialty in found:
        unique.setdefault(specialty.label, specialty)
    return list(unique.values())
