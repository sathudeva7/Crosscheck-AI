"""Every kind of doctor you can actually book in Sri Lanka, searchable.

This is the menu the recommendation agent must choose from. Its whole purpose
is to make an invented specialty impossible: a model asked 'which specialist?'
in the abstract will happily answer 'Tropical Medicine Physician' or 'Dengue
Specialist', neither of which any Sri Lankan directory lists, and the doctor
search then returns nothing for a specialty that never existed. Answers picked
from here are bookable by construction rather than because someone checked.

It also removes a ceiling. A hand-written table of specialties is only as good
as the day it was written; these catalogues carry 900-odd names each and gain
new ones without us noticing. 'Haemato Oncologist', 'Clinical Immunologist',
'Venereologist (S.T.D) & Sexual Medicine' are all real and all reachable here,
and none of them would have been in a table.

Two things to know about the data:

It is not clean. Alongside the real specialties sit facilities and oddities --
'Liver Centre', 'Accupunturist (Fatty Liver)', 'Hematology Clini' -- because
these lists are dropdown vocabularies maintained by two companies, not a
clinical taxonomy. So a name being present proves it is *bookable*, not that it
is *sensible*. The defence is `in_both`: junk tends to exist in one directory
only, while a specialty carried by both is almost always a real, common one.

It is a vocabulary, not patient data. These are dropdown labels -- the same
list any visitor sees on the two websites -- so holding them briefly in memory
is not the doctor data this application refuses to store. Nothing here reaches
the database, and the cache dies with the process.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from app.config import ENABLE_DOC990, ENABLE_ECHANNELLING
from app.referral.providers import doc990
from app.referral.providers import echannelling as ech

logger = logging.getLogger(__name__)

# How long a fetched catalogue stays usable. These lists change over weeks, not
# minutes -- the counts here moved by two names across a whole day of testing --
# so a short life costs one pair of requests per upload burst and keeps the app
# honest about where the names came from.
CACHE_SECONDS = 900

# Enough to choose between, not so many that the model is reading a phone book.
# A query matching 40 names is a query too vague to be answered from the list.
MAX_MATCHES = 12


@dataclass
class SpecialtyName:
    """One bookable specialty, and who lists it."""

    name: str
    directories: list[str] = field(default_factory=list)

    @property
    def in_both(self) -> bool:
        """Carried by both networks — the strongest signal that it is real.

        The junk in these catalogues is almost always one directory's own: a
        clinic that typed its own name into the specialty box. Two companies
        independently listing the same words is hard to arrive at by accident.
        """
        return len(self.directories) > 1


@dataclass
class _Cached:
    names: list[SpecialtyName]
    fetched_at: float


_cache: _Cached | None = None


def _fetch_echannelling() -> list[str]:
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        return list(ech.specialisations(client).values())


def _fetch_doc990() -> list[str]:
    with doc990.session() as client:
        _, specialities = doc990.catalogue(client)
        return list(specialities.values())


def catalogue(refresh: bool = False) -> list[SpecialtyName]:
    """Every specialty both directories list, merged by name.

    Fetched concurrently: the two are independent, and eChannelling's gateway
    is slow enough that asking it after Doc990 rather than alongside would add
    seconds to every upload.

    A directory that fails is skipped rather than fatal. Half a menu still
    makes invention impossible, which is the point of having one.
    """
    global _cache

    if not refresh and _cache is not None and time.time() - _cache.fetched_at < CACHE_SECONDS:
        return _cache.names

    sources: dict[str, list[str]] = {}
    jobs = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        if ENABLE_ECHANNELLING:
            jobs["echannelling"] = pool.submit(_fetch_echannelling)
        if ENABLE_DOC990:
            jobs["doc990"] = pool.submit(_fetch_doc990)
        for source, job in jobs.items():
            try:
                sources[source] = job.result()
            except Exception as exc:  # provider-specific errors, and httpx's
                logger.warning("specialty catalogue: %s unavailable: %s", source, exc)

    merged: dict[str, SpecialtyName] = {}
    for source, names in sources.items():
        for raw in names:
            name = (raw or "").strip()
            if not name:
                continue
            entry = merged.setdefault(name.lower(), SpecialtyName(name=name))
            if source not in entry.directories:
                entry.directories.append(source)

    found = sorted(merged.values(), key=lambda item: item.name)
    if found:
        _cache = _Cached(names=found, fetched_at=time.time())
    return found


# The catalogue is written in clinical Latin and a lab report is often not.
# Searching it for 'heart' returns nothing at all -- 'Cardiologist' does not
# contain the word -- and 'kidney' finds the dialysis units while missing every
# nephrologist in the country. A caller who searches in plain English and gets
# an empty list learns the wrong thing: that no such doctor exists.
#
# This is stemming, not routing. It only decides which names are *shown*; which
# one is right for a report is decided by the caller from what comes back.
_SYNONYMS = {
    "heart": ("cardio",),
    "cardiac": ("cardio",),
    "kidney": ("nephro", "renal"),
    "renal": ("nephro", "renal"),
    "liver": ("hepat",),
    "hepatic": ("hepat",),
    "blood": ("haemat", "hemat"),
    "bloods": ("haemat", "hemat"),
    "haematology": ("haemat",),
    "sugar": ("diabet", "endocrin"),
    "diabetes": ("diabet", "endocrin"),
    "thyroid": ("endocrin", "thyroid"),
    "hormone": ("endocrin",),
    "lung": ("chest", "respirat", "pulmon"),
    "lungs": ("chest", "respirat", "pulmon"),
    "breathing": ("chest", "respirat"),
    "stomach": ("gastro",),
    "gut": ("gastro",),
    "bowel": ("gastro",),
    "skin": ("dermat",),
    "brain": ("neuro",),
    "nerve": ("neuro",),
    "bone": ("orthop", "rheumat"),
    "joint": ("rheumat", "orthop"),
    "cancer": ("oncolog",),
    "tumour": ("oncolog",),
    "urine": ("urolog", "nephro"),
    "urinary": ("urolog", "nephro"),
    "hiv": ("hiv", "venereolog"),
    "infection": ("infectious", "microbiolog"),
    "eye": ("ophthalm", "eye"),
    "child": ("paediatr", "pediatr"),
    "children": ("paediatr", "pediatr"),
}


def _alternatives(word: str) -> tuple[str, ...]:
    """The forms of one search word worth looking for."""
    return (word,) + _SYNONYMS.get(word, ())


def search(query: str, limit: int = MAX_MATCHES) -> list[SpecialtyName]:
    """Specialties matching every word of the query, in any of its forms.

    Every word rather than any, so 'paediatric cardiologist' does not return
    every cardiologist in the country. Each word may match in plain English or
    its clinical stem, so both 'heart' and 'cardiologist' find the same people.

    Ranked with the names both directories carry first -- the strongest
    available signal that an entry is a real specialty rather than one clinic's
    typing -- then shorter names before longer, because 'Cardiologist' answers
    'cardiologist' and 'Consultant Interventional Cardiologist And
    Electrophysiologist' is a more specific thing nobody asked for.
    """
    words = [word for word in (query or "").lower().split() if word]
    if not words:
        return []

    matches = [
        entry
        for entry in catalogue()
        if all(
            any(form in entry.name.lower() for form in _alternatives(word))
            for word in words
        )
    ]
    matches.sort(key=lambda entry: (not entry.in_both, len(entry.name), entry.name))
    return matches[:limit]


def known(name: str) -> SpecialtyName | None:
    """The catalogue entry for an exact name, or None if nobody lists it.

    The final gate before a specialty is stored. A model that ignores the tool
    and answers from memory gets caught here rather than at the point a patient
    searches and finds nobody.
    """
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    return next((entry for entry in catalogue() if entry.name.lower() == wanted), None)
