"""Doctors, by name and specialty, from eChannelling.

Google Places lists no individual doctors in Sri Lanka — search it for a
cardiologist in Kandy and you get buildings. eChannelling lists 213 of them,
with the hospitals they channel at. That is the whole reason this provider
exists; what it cannot tell you is where any of those hospitals are, which is
what `places.py` is for.

The gateway wants an `x-ibm-client-id`. eChannelling publishes theirs in their
own web bundle, so `client_id()` reads the current one from there rather than
pinning a copy: pinned, the feature breaks silently on the day they rotate it,
and 'silently' is the part that matters — a referral search returning nothing
looks exactly like a town with no doctors in it.

What comes back needs cleaning before a patient sees it, and the cleaning is
not cosmetic:

  test records    '- ECHO TESTING A' is in the live cardiologist list
  duplicates      one row per hospital, so a doctor at three appears three times
  paediatric      a paediatric cardiologist is the wrong answer for an adult
  slmcId          sometimes a real council number, sometimes an internal id
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from app.config import (
    ECHANNELLING_BASE_URL,
    ECHANNELLING_CLIENT_ID,
    ECHANNELLING_SITE_URL,
    ECHANNELLING_TIMEOUT,
)

logger = logging.getLogger(__name__)

# The gateway rejects requests that do not look like they came from the site.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

# Every field the search endpoint expects. It wants all of them present, so the
# shape is built from this and only the keys being filtered on are set.
_SEARCH_FIELDS = (
    "hospital_type",
    "doctor_code",
    "doctor_name",
    "specialization",
    "specialization_code",
    "gender",
    "hospital_location",
    "sessiontime",
    "price",
    "hospital_name",
    "hospital_code",
    "session_date",
)

# `r.append("x-ibm-client-id","".concat("fbd72ca4-..."))` in their bundle. The
# separator between the header name and the value is minifier output and will
# not survive a build change, hence the loose `.{0,60}?` rather than a literal.
_CLIENT_ID_PATTERN = re.compile(
    r"x-ibm-client-id.{0,60}?([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
)
_CHUNK_PATTERN = re.compile(r"/_next/static/chunks/[A-Za-z0-9._/%-]+?\.js")

# Pages to read the id out of. /doctor-search first: it pulls in the layout
# chunk, which is where the id has been every time, and the home page does not.
_DISCOVERY_PAGES = ("/doctor-search", "/")

# A name that is a placeholder rather than a person. These are in the live
# data — '- ECHO TESTING A' is returned among the cardiologists — and showing
# one to a patient would be indistinguishable from inventing a doctor.
_JUNK_NAME = re.compile(r"^\s*[-–—]|TEST", re.IGNORECASE)

# Suffixes eChannelling appends to encode a session type rather than a person:
# 'DR A YOGANANDAMOORTHY-EXTRA', 'DR AJANTHA RAJAPAKSE-AUDIO / VIDEO'.
_NAME_SUFFIX = re.compile(
    r"\s*[-–—]\s*(EXTRA|AUDIO\s*/?\s*VIDE?O?|TELE|ONLINE|NEW|ECHO|SCAN|CHANNEL(LING)?|\(.*\))\s*$",
    re.IGNORECASE,
)

# An SLMC registration number is digits. 'D16149' is eChannelling's own doctor
# id wearing the same field name, and presenting it as a council registration
# would be a false credential on screen.
_SLMC_NUMBER = re.compile(r"^\d+$")


class EchannellingUnavailable(Exception):
    """The directory could not be reached. The search degrades, never fails."""


@dataclass
class Hospital:
    """Somewhere a doctor holds sessions. Coordinates are usually absent."""

    hospital_id: str
    name: str
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class Doctor:
    """One doctor, merged across every hospital they appear under."""

    doctor_id: str
    name: str
    specialties: list[str] = field(default_factory=list)
    hospitals: list[Hospital] = field(default_factory=list)
    gender: str | None = None
    slmc_number: str | None = None  # only when it really is one
    is_tele: bool = False

    @property
    def booking_url(self) -> str:
        return f"{ECHANNELLING_SITE_URL}/doctor-search/{self.doctor_id}"


# --- the client id -----------------------------------------------------------

_cached_client_id: str | None = None


def _discover_client_id(client: httpx.Client) -> str | None:
    """Read the id out of eChannelling's own bundle."""
    chunks: list[str] = []
    for path in _DISCOVERY_PAGES:
        try:
            html = client.get(f"{ECHANNELLING_SITE_URL}{path}").text
        except httpx.HTTPError as exc:
            logger.warning("could not load %s for client id discovery: %s", path, exc)
            continue
        found = _CLIENT_ID_PATTERN.search(html)
        if found:
            return found.group(1)
        for chunk in _CHUNK_PATTERN.findall(html):
            if chunk not in chunks:
                chunks.append(chunk)

    # The layout chunk carries it on every build so far; try it before the rest
    # rather than downloading twenty bundles to find out.
    chunks.sort(key=lambda c: 0 if "layout" in c else 1)
    for chunk in chunks:
        try:
            js = client.get(f"{ECHANNELLING_SITE_URL}{chunk}").text
        except httpx.HTTPError:
            continue
        found = _CLIENT_ID_PATTERN.search(js)
        if found:
            logger.info("discovered eChannelling client id in %s", chunk)
            return found.group(1)
    return None


def client_id(client: httpx.Client, refresh: bool = False) -> str:
    """The client id to send, configured or discovered.

    Cached for the process. `refresh` forces a re-read, which is what a 401 or
    403 means: the id we had has been rotated.
    """
    global _cached_client_id
    if ECHANNELLING_CLIENT_ID:
        return ECHANNELLING_CLIENT_ID
    if _cached_client_id and not refresh:
        return _cached_client_id
    found = _discover_client_id(client)
    if not found:
        raise EchannellingUnavailable(
            "could not find the gateway client id in eChannelling's site bundle"
        )
    _cached_client_id = found
    return found


# --- cleaning ----------------------------------------------------------------


def clean_name(raw: str) -> str:
    """A doctor's name without the session-type suffix."""
    name = (raw or "").strip()
    previous = None
    while name != previous:  # '...-EXTRA - (AUDIO)' needs two passes
        previous = name
        name = _NAME_SUFFIX.sub("", name).strip()
    return re.sub(r"\s{2,}", " ", name)


def _is_junk(name: str) -> bool:
    return bool(_JUNK_NAME.search(name or "")) or not (name or "").strip()


def _slmc(value: str | None) -> str | None:
    """The registration number, or None when the field holds an internal id."""
    value = (value or "").strip()
    return value if _SLMC_NUMBER.match(value) else None


def _hospitals(rows) -> list[Hospital]:
    out = []
    for row in rows or []:
        latitude = _coordinate(row.get("latitude"))
        longitude = _coordinate(row.get("longitude"))
        out.append(
            Hospital(
                hospital_id=str(row.get("hospitalId") or ""),
                name=(row.get("hospital") or "").strip(),
                city=((row.get("address") or {}).get("city") or "").strip() or None,
                latitude=latitude,
                longitude=longitude,
            )
        )
    return out


def _coordinate(value) -> float | None:
    """A coordinate, or None for the '0' that means 'not recorded'.

    Most hospitals come back at 0,0 — the Gulf of Guinea. Treating that as a
    real position would put every unlocated clinic 8,000 km away and sort them
    to the bottom, which at least fails loudly; passing None instead lets the
    caller geocode the name and get a real answer.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number == 0 else number


# --- the API -----------------------------------------------------------------


def _post(client: httpx.Client, path: str, payload: dict) -> dict:
    """One call, re-reading the client id once if the old one was rejected."""
    for refresh in (False, True):
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": ECHANNELLING_SITE_URL,
            "Referer": f"{ECHANNELLING_SITE_URL}/",
            "User-Agent": BROWSER_UA,
            "x-ibm-client-id": client_id(client, refresh=refresh),
        }
        try:
            response = client.post(
                f"{ECHANNELLING_BASE_URL}{path}", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            raise EchannellingUnavailable(str(exc)) from exc

        if response.status_code in (401, 403) and not refresh:
            logger.info("eChannelling rejected the client id; re-reading it")
            continue
        if response.status_code >= 400:
            raise EchannellingUnavailable(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise EchannellingUnavailable("gateway did not return JSON") from exc
    raise EchannellingUnavailable("client id was rejected twice")


def _get(client: httpx.Client, path: str) -> dict:
    for refresh in (False, True):
        headers = {
            "Accept": "*/*",
            "Origin": ECHANNELLING_SITE_URL,
            "Referer": f"{ECHANNELLING_SITE_URL}/",
            "User-Agent": BROWSER_UA,
            "x-ibm-client-id": client_id(client, refresh=refresh),
        }
        try:
            response = client.get(f"{ECHANNELLING_BASE_URL}{path}", headers=headers)
        except httpx.HTTPError as exc:
            raise EchannellingUnavailable(str(exc)) from exc
        if response.status_code in (401, 403) and not refresh:
            continue
        if response.status_code >= 400:
            raise EchannellingUnavailable(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise EchannellingUnavailable("gateway did not return JSON") from exc
    raise EchannellingUnavailable("client id was rejected twice")


def _session() -> httpx.Client:
    return httpx.Client(timeout=ECHANNELLING_TIMEOUT, follow_redirects=True)


def specialisations(client: httpx.Client | None = None) -> dict[str, str]:
    """Every specialty eChannelling recognises, code -> name.

    Around 910 of them, and the vocabulary is their own: 'Chest Specialist',
    not 'pulmonologist'. Anything searching this directory has to speak it,
    which is why `specialty.py` maps drug classes onto these codes rather than
    onto textbook specialty names.
    """
    own = client is None
    client = client or _session()
    try:
        data = _get(client, "/v1/specializations")
        return {str(k): str(v) for k, v in (data.get("data") or data).items()}
    finally:
        if own:
            client.close()


def hospitals(client: httpx.Client | None = None) -> dict[str, str]:
    """Every hospital, code -> name."""
    own = client is None
    client = client or _session()
    try:
        data = _get(client, "/hospitals")
        return {str(k): str(v) for k, v in (data.get("data") or data).items()}
    finally:
        if own:
            client.close()


def find_doctors(
    specialisation_code: str | None = None,
    hospital_code: str | None = None,
    include_paediatric: bool = False,
    client: httpx.Client | None = None,
) -> list[Doctor]:
    """Doctors of one specialty, cleaned and merged.

    `include_paediatric` is off by default because these findings come from an
    adult's record. A paediatric cardiologist is a real doctor and a wrong
    answer, and the two are hard to tell apart on a results screen.
    """
    own = client is None
    client = client or _session()
    try:
        payload = {key: "" for key in _SEARCH_FIELDS}
        if specialisation_code:
            payload["specialization_code"] = str(specialisation_code)
        if hospital_code:
            payload["hospital_code"] = str(hospital_code)

        data = _post(client, "/search-results", payload)
        rows = (data.get("data") or {}).get("results") or []
        return _merge(rows, include_paediatric=include_paediatric)
    finally:
        if own:
            client.close()


def _merge(rows: list[dict], include_paediatric: bool) -> list[Doctor]:
    """Collapse the raw rows into one entry per doctor.

    eChannelling returns a row per doctor *per hospital*, so DR K SUNTHARESAN
    arrives twice under two ids. Merging on the cleaned name keeps him once
    with both venues; merging on the id would leave the list looking like two
    different cardiologists with the same name, which is worse than a duplicate
    because a reader cannot tell it is one.
    """
    merged: dict[str, Doctor] = {}

    for row in rows:
        raw_name = (row.get("doctorName") or "").strip()
        if _is_junk(raw_name):
            continue
        name = clean_name(raw_name)
        if not name:
            continue

        specialties = sorted(
            {
                (s.get("specialization") or "").strip()
                for s in (row.get("specializations") or [])
                if (s.get("specialization") or "").strip()
            }
        )
        if not include_paediatric and any("PAEDIATRIC" in s.upper() for s in specialties):
            continue

        key = name.upper()
        doctor = merged.get(key)
        if doctor is None:
            doctor = Doctor(
                doctor_id=str(row.get("doctorID") or ""),
                name=name,
                gender=(row.get("gender") or "").strip() or None,
                slmc_number=_slmc(row.get("slmcId")),
                is_tele=bool(row.get("isTele")),
            )
            merged[key] = doctor

        for specialty in specialties:
            if specialty not in doctor.specialties:
                doctor.specialties.append(specialty)

        known = {h.hospital_id for h in doctor.hospitals}
        for hospital in _hospitals(row.get("hospitals")):
            if hospital.hospital_id not in known and hospital.name:
                doctor.hospitals.append(hospital)
                known.add(hospital.hospital_id)

        # A registration number on any of a doctor's rows is the registration
        # number; the rows that carry an internal id instead are not evidence
        # against it.
        doctor.slmc_number = doctor.slmc_number or _slmc(row.get("slmcId"))

    return sorted(merged.values(), key=lambda d: d.name)
