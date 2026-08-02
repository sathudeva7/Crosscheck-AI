"""Work out what a drug on a prescription actually is.

A prescription gives a name, and a name is only a word. 'Betaloc' and
'Oxprenolol' look nothing like each other, yet both are beta blockers and
taking both is a real problem. Nothing downstream — duplicate detection,
interaction checking, or following one drug across visits under two different
brand names — can work until a name becomes an identity:

    Betaloc  ->  metoprolol  ->  ATC C07AB  ->  beta blocker

Three steps, cheapest first:

1. The cache. A name is looked up once, ever.
2. RxNorm by exact name. Settles generic names ('Cimetidine', 'Oxprenolol')
   with one free call and no model.
3. The model proposes the active ingredient, and RxNorm verifies it exists.

Step 3 is the interesting one. RxNorm is a US database: it has no ingredient
link for Betaloc, Voltaren or Losec, and for the Indian brand 'Crocin' its own
fuzzy search confidently returns 'crocin', a compound found in saffron, rather
than paracetamol. A model knows brands from everywhere; what it cannot be
trusted to do is stop when it does not know. So the model proposes and RxNorm
disposes — an ingredient that is not a real RxNorm concept is rejected.

What this deliberately does not do is guess. A name that cannot be identified
comes back 'unrecognised' and keeps its printed form. It still appears on the
patient's timeline; it is simply excluded from the safety checks, and the
interface must say the check was incomplete rather than implying all is well.

The limit worth stating plainly: verification proves the *ingredient* is real,
not that the brand really contains it. A model that claimed Betaloc was aspirin
would pass, because aspirin exists. That is why every identity carries its
confidence and how it was reached, and why the user can correct it.
"""

import asyncio
import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import (
    DRUG_CACHE_PATH,
    DRUG_MODEL,
    ENABLE_DRUG_LOOKUP,
    MAX_CONCURRENT_RXNAV,
    RXNAV_BASE_URL,
    RXNAV_TIMEOUT,
)

logger = logging.getLogger(__name__)

# Confidence attached to each route, reflecting how much was actually checked.
CONFIDENCE = {
    "rxnorm_exact": 0.95,  # the printed name is itself a known drug concept
    "model_verified": 0.80,  # model named the ingredient, RxNorm confirmed it exists
    "unrecognised": 0.0,
}

# A model that is unsure has still produced a real ingredient name, so RxNorm
# verification cannot catch it — 'Zonalon 250 XR', a name that does not exist,
# came back as a real but unrelated antiseptic at 0.61. Below this the answer is
# discarded: for an unknown product, 'unrecognised' is the correct result.
MIN_INGREDIENT_CONFIDENCE = 0.7

# Dosage forms and packaging words that appear glued to the drug name.
_FORM_PREFIX = re.compile(
    r"^\s*(?:\d+\s*[).\-]\s*)?"  # a list number: '1)' '2.'
    r"(?:tab|tabs|tablet|cap|caps|capsule|syp|syrup|susp|inj|injection|"
    r"oint|ointment|cream|gel|drop|drops|sol|solution|lotion|spray|"
    r"powder|sachet|supp|patch)\b\.?\s*",
    re.IGNORECASE,
)
_STRENGTH_TAIL = re.compile(
    r"\s*\d+(?:[.,]\d+)?\s*(?:mcg|µg|ug|mg|g|kg|ml|l|iu|units?|%)\b.*$", re.IGNORECASE
)
_NOISE = re.compile(r"\b(?:ip|bp|usp|sr|xr|cr|er|la|md|dt|plus|forte)\b\.?", re.IGNORECASE)


def clean_name(raw: str) -> str:
    """Strip the packaging off a printed drug name.

    'TAB. CLARITHROMYCIN IP 500MG' is not a drug name; 'clarithromycin' is.
    Removing the form, the strength and the pharmacopoeia initials is what
    makes a lookup possible at all.
    """
    text = (raw or "").strip()
    previous = None
    while text != previous:  # '1) TAB. X' needs two passes
        previous = text
        text = _FORM_PREFIX.sub("", text)
    text = _STRENGTH_TAIL.sub("", text)
    text = _NOISE.sub(" ", text)
    text = re.sub(r"[^\w\s\-+/]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class DrugIdentity:
    """What we could establish about one printed drug name."""

    raw: str
    cleaned: str = ""
    ingredients: list[str] = field(default_factory=list)
    rxcuis: list[str] = field(default_factory=list)
    atc: list[dict[str, str]] = field(default_factory=list)  # [{code, name}]
    status: str = "unrecognised"  # verified | unrecognised | unavailable
    source: str = ""  # cache | rxnorm_exact | model_verified
    confidence: float = 0.0
    reason: str = ""

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def atc_prefixes(self, length: int = 3) -> set[str]:
        """The class codes used to spot two drugs of the same kind.

        'C07AB' and 'C07AA' are different codes but the same family; comparing
        the first three characters is what makes metoprolol and oxprenolol
        register as both being beta blockers.
        """
        return {entry["code"][:length] for entry in self.atc if entry.get("code")}

    def to_json(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "cleaned": self.cleaned,
            "ingredients": self.ingredients,
            "rxcuis": self.rxcuis,
            "atc": self.atc,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "DrugIdentity":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# --- cache -------------------------------------------------------------------

_cache: dict[str, dict[str, Any]] | None = None


def _cache_key(name: str) -> str:
    return clean_name(name).lower()


def _load_cache() -> dict[str, dict[str, Any]]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(DRUG_CACHE_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
    return _cache


def _save_cache() -> None:
    """Write the whole cache atomically, so a crash cannot corrupt it."""
    if _cache is None:
        return
    DRUG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=DRUG_CACHE_PATH.parent, delete=False, encoding="utf-8"
    ) as handle:
        json.dump(_cache, handle, indent=2, ensure_ascii=False, sort_keys=True)
        temporary = Path(handle.name)
    temporary.replace(DRUG_CACHE_PATH)


# --- RxNorm ------------------------------------------------------------------

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_RXNAV)
    return _semaphore


class RxNavUnavailable(Exception):
    """RxNav could not be reached. Identification is skipped, not failed."""


async def _get(client: httpx.AsyncClient, path: str, **params) -> dict[str, Any]:
    async with _get_semaphore():
        try:
            response = await client.get(f"{RXNAV_BASE_URL}{path}", params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise RxNavUnavailable(str(exc)) from exc


async def rxcui_for_name(client: httpx.AsyncClient, name: str) -> str | None:
    """Exact-name lookup. No fuzzy matching — fuzzy is how 'Crocin' becomes
    a saffron compound."""
    if not name.strip():
        return None
    data = await _get(client, "/rxcui.json", name=name, search=0)
    ids = (data.get("idGroup") or {}).get("rxnormId") or []
    return ids[0] if ids else None


async def ingredients_for_rxcui(client: httpx.AsyncClient, rxcui: str) -> list[str]:
    """The active ingredients of a concept, if RxNorm links any."""
    data = await _get(client, f"/rxcui/{rxcui}/related.json", tty="IN")
    groups = (data.get("relatedGroup") or {}).get("conceptGroup") or []
    return [
        entry["name"]
        for group in groups
        for entry in (group.get("conceptProperties") or [])
    ]


async def atc_for_rxcui(client: httpx.AsyncClient, rxcui: str) -> list[dict[str, str]]:
    """WHO ATC classes for an ingredient.

    ATC is an international standard, which is what makes class-based checks
    work outside the United States even though the lookup is a US service.
    """
    data = await _get(
        client, "/rxclass/class/byRxcui.json", rxcui=rxcui, relaSource="ATC"
    )
    entries = (data.get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or []
    seen: dict[str, str] = {}
    for entry in entries:
        concept = entry.get("rxclassMinConceptItem") or {}
        if concept.get("classId"):
            seen[concept["classId"]] = concept.get("className", "")
    return [{"code": code, "name": name} for code, name in sorted(seen.items())]


# --- the model ---------------------------------------------------------------

SYSTEM_PROMPT = """You identify the active ingredients of a medicine.

You are given a medicine name taken from a prescription. It may be a brand name
from any country, a generic name, or a local product name.

Reply with the active ingredient(s) using their international generic (INN)
names, in lowercase, e.g. 'metoprolol', 'paracetamol', 'amoxicillin'.

Rules:
- If you do not recognise the name, return an empty list. This is the correct
  answer for an unknown product and is always better than a guess.
- Never invent an ingredient to fill the gap.
- A combination product has several ingredients; list them all.
- Give the ingredient, not the brand: 'Betaloc' is 'metoprolol'.
- Set confidence low if the name is close to several different medicines.
"""


class IngredientGuess(BaseModel):
    ingredients: list[str] = Field(
        description="Active ingredient INN names, lowercase. Empty if unknown."
    )
    confidence: float = Field(ge=0, le=1, description="How sure you are, 0 to 1.")
    note: str = Field(description="One short phrase: where it is sold, or why unsure.")


_model = None


def _get_model():
    global _model
    if _model is None:
        _model = init_chat_model(DRUG_MODEL, temperature=0).with_structured_output(
            IngredientGuess, method="function_calling"
        )
    return _model


async def _propose(name: str) -> IngredientGuess:
    try:
        return await _get_model().ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=f"Medicine: {name}")]
        )
    except Exception as exc:  # noqa: BLE001 - provider errors vary widely
        logger.warning("ingredient lookup failed for %r: %s", name, exc)
        return IngredientGuess(ingredients=[], confidence=0.0, note=f"model unavailable: {exc}")


# --- resolution --------------------------------------------------------------


async def _classify(client: httpx.AsyncClient, rxcuis: Sequence[str]) -> list[dict[str, str]]:
    results = await asyncio.gather(
        *(atc_for_rxcui(client, rxcui) for rxcui in rxcuis), return_exceptions=True
    )
    seen: dict[str, str] = {}
    for result in results:
        if isinstance(result, BaseException):
            continue
        for entry in result:
            seen[entry["code"]] = entry["name"]
    return [{"code": code, "name": name} for code, name in sorted(seen.items())]


async def _resolve_uncached(client: httpx.AsyncClient, raw: str) -> DrugIdentity:
    cleaned = clean_name(raw)
    identity = DrugIdentity(raw=raw, cleaned=cleaned)

    if not cleaned:
        identity.reason = "nothing left after removing form and strength"
        return identity

    # 1. The printed name may itself be a known concept. Free, and settles
    #    every generic name without involving a model.
    rxcui = await rxcui_for_name(client, cleaned)
    if rxcui:
        ingredients = await ingredients_for_rxcui(client, rxcui)
        # A concept with no ingredient link is a foreign brand RxNorm knows the
        # name of but nothing else about — fall through to the model.
        if ingredients or (await _is_ingredient(client, rxcui)):
            identity.ingredients = ingredients or [cleaned.lower()]
            identity.rxcuis = [rxcui]
            identity.atc = await _classify(client, [rxcui])
            identity.status = "verified"
            identity.source = "rxnorm_exact"
            identity.confidence = CONFIDENCE["rxnorm_exact"]
            identity.reason = f"'{cleaned}' is a known drug concept in RxNorm"
            return identity

    # 2. Ask the model what is in it, then check every name it gives back.
    guess = await _propose(cleaned)
    if not guess.ingredients:
        identity.reason = f"not recognised ({guess.note})" if guess.note else "not recognised"
        return identity

    if guess.confidence < MIN_INGREDIENT_CONFIDENCE:
        identity.reason = (
            f"proposed {guess.ingredients} but only {guess.confidence:.2f} sure — "
            f"too uncertain to record"
        )
        return identity

    verified: list[str] = []
    rxcuis: list[str] = []
    for ingredient in guess.ingredients:
        found = await rxcui_for_name(client, ingredient)
        if found:
            verified.append(ingredient.lower())
            rxcuis.append(found)

    if not verified:
        identity.reason = (
            f"proposed {guess.ingredients} but none exist in RxNorm — rejected as invented"
        )
        return identity

    identity.ingredients = verified
    identity.rxcuis = rxcuis
    identity.atc = await _classify(client, rxcuis)
    identity.status = "verified"
    identity.source = "model_verified"
    # The model's own certainty caps the score: a hesitant identification stays
    # hesitant even though RxNorm confirmed the ingredient is a real drug.
    identity.confidence = round(CONFIDENCE["model_verified"] * guess.confidence, 3)
    identity.reason = f"identified as {', '.join(verified)}, each confirmed in RxNorm"
    return identity


async def _is_ingredient(client: httpx.AsyncClient, rxcui: str) -> bool:
    """Whether a concept is itself an ingredient, e.g. 'oxprenolol'."""
    try:
        data = await _get(client, f"/rxcui/{rxcui}/property.json", propName="TTY")
        properties = (data.get("propConceptGroup") or {}).get("propConcept") or []
        return any(p.get("propValue") in ("IN", "PIN", "MIN") for p in properties)
    except RxNavUnavailable:
        return False


async def resolve_many(names: Sequence[str], use_cache: bool = True) -> dict[str, DrugIdentity]:
    """Identify a list of printed drug names, keyed by the name given.

    Never raises. If RxNav cannot be reached, every name comes back
    'unavailable' and ingestion carries on — an unidentified drug is a
    reduced feature, not a failed document.
    """
    unique = {name for name in names if name and name.strip()}
    out: dict[str, DrugIdentity] = {}
    if not unique:
        return out

    cache = _load_cache() if use_cache else {}
    pending: list[str] = []
    for name in unique:
        cached = cache.get(_cache_key(name))
        if cached is not None:
            identity = DrugIdentity.from_json(cached)
            identity.raw = name
            identity.source = "cache"
            out[name] = identity
        else:
            pending.append(name)

    if not pending:
        return out

    if not ENABLE_DRUG_LOOKUP:
        for name in pending:
            out[name] = DrugIdentity(
                raw=name, cleaned=clean_name(name), status="unavailable",
                reason="drug identification is switched off",
            )
        return out

    async with httpx.AsyncClient(timeout=RXNAV_TIMEOUT) as client:
        results = await asyncio.gather(
            *(_resolve_uncached(client, name) for name in pending), return_exceptions=True
        )

    changed = False
    for name, result in zip(pending, results):
        if isinstance(result, BaseException):
            logger.warning("could not identify %r: %s", name, result)
            out[name] = DrugIdentity(
                raw=name, cleaned=clean_name(name), status="unavailable",
                reason=f"lookup unavailable: {result}",
            )
            continue
        out[name] = result
        if use_cache:
            # 'unavailable' is a network problem, not an answer — caching it
            # would make a temporary outage permanent.
            if result.status in ("verified", "unrecognised"):
                cache[_cache_key(name)] = result.to_json()
                changed = True

    if changed:
        _save_cache()

    return out


async def resolve(name: str) -> DrugIdentity:
    """Identify one printed drug name."""
    results = await resolve_many([name])
    return results.get(name, DrugIdentity(raw=name, cleaned=clean_name(name)))
