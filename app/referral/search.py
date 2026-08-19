"""Turning a specialty and a town into real doctors, or real clinics.

Two searches, kept apart on purpose. `find_doctors` reads eChannelling for
named people you can book; `find_clinics` reads Google for places you can walk
into. They answer different questions and neither is a fallback for the other —
eChannelling lists private channelling only, so every government hospital in
the country is missing from it, and for a patient in Jaffna the teaching
hospital 700 m away matters more than the nearest bookable cardiologist 26 km
off.

They are separate functions rather than one because they are also very
differently priced in time. eChannelling's gateway takes seconds; Google
answers in hundreds of milliseconds. Split, a caller can ask for both at once
and paint the clinics immediately instead of holding the whole screen back for
the slower directory.

Both widen. A search that finds nothing nearby is not finished: for a patient
in Jaffna needing an oncologist the nearest is 245 km away, and 'no results'
would be true and useless. Each radius is tried in turn and every rung is kept
in `attempts`, which is what lets the screen say how far it looked rather than
only that it found nothing.

`find_doctors` also climbs a second ladder, over words. Ask eChannelling for a
pulmonologist and you get nobody; ask for a 'Chest Specialist' and you get a
list, so each specialty carries its own ordered terms.

Where the calls go: eChannelling is asked once, nationally, and everything
after that is arithmetic — cities geocoded once each, distances computed in
memory, the radius ladder a filter over that list rather than six more round
trips. Google is only asked about the handful of rows actually being shown.
"""

from __future__ import annotations

import json
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import (
    DOC990_MAX_HOSPITALS,
    DOC990_TOWN_CACHE_PATH,
    ENABLE_DOC990,
    ENABLE_ECHANNELLING,
    ENABLE_PLACES,
    REFERRAL_ENOUGH,
    REFERRAL_MAX_RESULTS,
    REFERRAL_RADII_KM,
)
from app.referral.providers import doc990, echannelling as ech
from app.referral.providers.places import PlacesClient, PlacesUnavailable, haversine_km
from app.referral.specialty import Specialty

logger = logging.getLogger(__name__)


@dataclass
class Referral:
    """One row a patient can act on."""

    kind: str  # 'doctor' | 'clinic'
    name: str
    specialty: str | None = None
    clinic_name: str | None = None
    address: str | None = None
    city: str | None = None
    distance_km: float | None = None
    rating: float | None = None
    reviews: int | None = None
    phone: str | None = None
    open_now: bool | None = None
    hours: list[str] = field(default_factory=list)
    website: str | None = None
    slmc_number: str | None = None
    other_clinics: list[str] = field(default_factory=list)

    source: str = ""
    source_url: str | None = None
    booking_url: str | None = None


@dataclass
class Attempt:
    """One rung of a ladder, kept so the response can show its working."""

    term: str
    radius_km: int
    found: int
    provider: str = ""  # 'echannelling' | 'google_places'


@dataclass
class ReferralSearch:
    """What was looked for, how hard, and what came back."""

    specialty: str
    location: str
    latitude: float | None = None
    longitude: float | None = None
    resolved_location: str | None = None

    results: list[Referral] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    widened_to_km: int | None = None
    notice: str | None = None
    sources: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    @property
    def found_any(self) -> bool:
        return bool(self.results)


def _city_of(hospital: ech.Hospital) -> str | None:
    """Somewhere to geocode a hospital by, without geocoding the hospital.

    The name usually ends in its town — 'Nawaloka Hospital-Negombo' — which is
    what makes a city-level filter possible before spending a Places lookup on
    anything.
    """
    if hospital.city:
        return hospital.city.strip()
    if "-" in hospital.name:
        tail = hospital.name.rsplit("-", 1)[-1].strip()
        if tail and len(tail) < 40:
            return tail
    return None


def _same_town(city: str | None, location: str) -> bool:
    """Whether a hospital's town is the one the patient named."""
    city = (city or "").strip().lower()
    wanted = location.strip().lower()
    if not city or not wanted:
        return False
    return city == wanted or wanted in city or city in wanted


def _distance_to_hospital(
    hospital: ech.Hospital,
    origin: tuple[float, float],
    location: str,
) -> float | None:
    """How far this hospital is, without asking Google where its town is.

    Three sources, in order, and none of them cost a request:

      the directory's own coordinates, for the one hospital in ten that has them
      the patient's own town, matched by name — a hospital in Kandy is in Kandy
      nothing

    The geocoding this used to do was the single largest cost in the search: 77
    lookups to rank hospitals in Kandy, of which all but a handful were then
    discarded. Hospitals that cannot be placed this way come back None and are
    held for the last rung, where the exact distance is filled in from the
    Google listing that is fetched for them anyway.
    """
    if hospital.latitude is not None and hospital.longitude is not None:
        return haversine_km(origin[0], origin[1], hospital.latitude, hospital.longitude)
    if _same_town(_city_of(hospital), location):
        return 0.0
    return None


def _doctors_for(
    specialty: Specialty,
    codes_by_name: dict[str, str],
    client: httpx.Client,
) -> tuple[list[ech.Doctor], str | None]:
    """Every doctor eChannelling lists under any of this specialty's terms."""
    merged: dict[str, ech.Doctor] = {}
    matched_term = None
    for term in specialty.search_terms:
        code = codes_by_name.get(term.strip().lower())
        if code is None:
            continue
        try:
            found = ech.find_doctors(specialisation_code=code, client=client)
        except ech.EchannellingUnavailable as exc:
            logger.warning("eChannelling search for %r failed: %s", term, exc)
            continue
        if found and matched_term is None:
            matched_term = term
        for doctor in found:
            merged.setdefault(doctor.name.upper(), doctor)
    return list(merged.values()), matched_term


def _start(
    specialty: Specialty,
    location: str,
    places: PlacesClient | None,
) -> tuple[ReferralSearch, tuple[float, float] | None, PlacesClient | None]:
    """The setup both searches share: a result, a client, and a position.

    Google is needed by both, even the one that reads doctors from
    eChannelling: the town has to become coordinates before anything can be
    called near or far.
    """
    search = ReferralSearch(specialty=specialty.label, location=location)

    places = places or (PlacesClient() if ENABLE_PLACES else None)
    if places is not None and not places.enabled:
        search.degraded.append("No Google Maps key: results carry no distance or address.")
        places = None

    origin = None
    if places is not None:
        try:
            origin = places.geocode(location)
        except PlacesUnavailable as exc:
            search.degraded.append(f"Could not look up that location ({exc}).")

    if origin is None:
        search.notice = (
            f"We could not find {location!r} on the map, so we cannot measure "
            "distances. Try a city or district name."
        )
        return search, None, places

    search.latitude, search.longitude = origin
    search.resolved_location = location
    return search, origin, places


def find_doctors(
    specialty: Specialty,
    location: str,
    places: PlacesClient | None = None,
    radii_km: list[int] | None = None,
    enough: int = REFERRAL_ENOUGH,
    limit: int = REFERRAL_MAX_RESULTS,
) -> ReferralSearch:
    """Named doctors of one specialty, from eChannelling, nearest first.

    Deliberately knows nothing about `find_clinics`. The two run against
    directories with very different response times — eChannelling's gateway
    takes seconds where Google answers in hundreds of milliseconds — and
    keeping them apart lets a caller ask for both at once and show whichever
    arrives first, rather than making every reader wait for the slower one.
    """
    radii = radii_km or REFERRAL_RADII_KM
    own_places = places is None
    search, origin, places = _start(specialty, location, places)

    try:
        if origin is None:
            return search

        doctors: list[ech.Doctor] = []
        if not ENABLE_ECHANNELLING:
            search.degraded.append("The doctor directory is switched off.")
        elif not specialty.search_terms:
            # Pharmacists, for one: the directory channels doctors, so there is
            # nothing to ask it for. Not a failure — the clinics search is where
            # that specialty is answered.
            search.notice = (
                f"{specialty.label} is not something this directory lists. Look at "
                "nearby places instead."
            )
            return search
        else:
            try:
                with httpx.Client(timeout=40, follow_redirects=True) as client:
                    codes = ech.specialisations(client)
                    by_name = {name.strip().lower(): code for code, name in codes.items()}
                    doctors, _ = _doctors_for(specialty, by_name, client)
                search.sources.append("echannelling")
            except ech.EchannellingUnavailable as exc:
                logger.warning("eChannelling unavailable: %s", exc)
                search.degraded.append(
                    "The doctor directory was unavailable, so no named doctors could "
                    "be listed. Nearby hospitals and clinics are unaffected."
                )
                return search

        if doctors:
            _ladder_over_doctors(search, doctors, origin, places, radii, enough, limit)

        _finish_doctors(search, specialty, radii)
        return search
    finally:
        if own_places and places is not None:
            places.close()


def _doc990_specialisation(specialty: Specialty, catalogue: dict[str, str]) -> str | None:
    """This specialty's id in Doc990's own numbering.

    Matched by name, not carried in the routing table. Doc990 and eChannelling
    both call it 'Cardiologist' and number it differently — 4 here, 01 there —
    so a table holding either set of ids would be wrong for the other directory
    and silently wrong the day one of them renumbers.
    """
    by_name = {name.strip().lower(): code for code, name in catalogue.items()}
    for term in specialty.search_terms:
        code = by_name.get(term.strip().lower())
        if code:
            return code
    return None


# Sri Lanka, generously boxed. Doc990's town field is not always a town — it
# holds '07', 'Breast Care Clinic' and 'Borella by Dr. Senadhi Bangamuarachchi'
# among the real place names, and Google will cheerfully geocode those to
# somewhere in the world. A coordinate outside this box is a wrong answer to a
# question about a Sri Lankan hospital, and dropping it costs only the ranking
# of a hospital that was mislabelled anyway.
_LK_BOUNDS = (5.5, 10.2, 79.2, 82.2)  # lat_min, lat_max, lon_min, lon_max

# town -> coordinates, or None for 'asked, and it is not a place'. None is
# cached as deliberately as a hit: without it, every search re-asks Google
# about 'Breastfeeding Clinic' forever.
_towns: dict[str, tuple[float, float] | None] | None = None


def _load_towns() -> dict[str, tuple[float, float] | None]:
    global _towns
    if _towns is None:
        try:
            raw = json.loads(DOC990_TOWN_CACHE_PATH.read_text())
            _towns = {
                key: (tuple(value) if value else None) for key, value in raw.items()
            }
        except (FileNotFoundError, json.JSONDecodeError):
            _towns = {}
    return _towns


def _save_towns() -> None:
    """Written atomically, so an interrupted search cannot corrupt the file."""
    if not _towns:
        return
    try:
        DOC990_TOWN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=DOC990_TOWN_CACHE_PATH.parent, delete=False, encoding="utf-8"
        ) as handle:
            json.dump(_towns, handle, indent=2, ensure_ascii=False, sort_keys=True)
            temporary = Path(handle.name)
        temporary.replace(DOC990_TOWN_CACHE_PATH)
    except OSError as exc:
        # A read-only filesystem must not fail a search. The cache is an
        # optimisation; the coordinates are already in memory for this process.
        logger.info("could not write the town cache: %s", exc)


def _place_towns(
    hospitals: list[doc990.Hospital],
    places: PlacesClient | None,
) -> dict[str, tuple[float, float]]:
    """Where each Doc990 town is, geocoded once and remembered.

    This is what makes a radius mean kilometres for a directory that publishes
    no coordinates. It is affordable because it is per *town*: 216 hospitals
    share 111 towns, the answers are cached to disk, and towns do not move — so
    the second search of a town costs nothing and the second run of the process
    costs nothing either.

    Geocoding per hospital, which this replaces, was the thing that could not
    be afforded: 111 lookups on one search, every search, to rank places that
    were mostly discarded.
    """
    if places is None:
        return {}

    known = _load_towns()
    wanted = {
        (hospital.city or "").strip()
        for hospital in hospitals
        if (hospital.city or "").strip()
    }
    missing = sorted(town for town in wanted if town.lower() not in known)

    if missing:
        logger.info("geocoding %d new Doc990 town(s)", len(missing))

        def _look(town: str) -> tuple[str, tuple[float, float] | None]:
            try:
                return town, places.geocode(town)
            except PlacesUnavailable as exc:
                logger.info("could not place %r: %s", town, exc)
                return town, None

        # Independent lookups, and the first search after a deploy has all 111
        # of them to do. In series that is the sixteen seconds this function
        # exists to avoid.
        with ThreadPoolExecutor(max_workers=8) as pool:
            for town, found in pool.map(_look, missing):
                lat_min, lat_max, lon_min, lon_max = _LK_BOUNDS
                if found and not (lat_min <= found[0] <= lat_max and lon_min <= found[1] <= lon_max):
                    logger.info("ignoring %r: geocoded outside Sri Lanka to %s", town, found)
                    found = None
                known[town.lower()] = found
        _save_towns()

    return {town: known[town.lower()] for town in wanted if known.get(town.lower())}


def _hospitals_near(
    hospitals: list[doc990.Hospital],
    location: str,
    origin: tuple[float, float],
    radius_km: int,
    radii_last: int,
    towns: dict[str, tuple[float, float]] | None = None,
    want: int = 1,
) -> list[tuple[float, doc990.Hospital]]:
    """Doc990 hospitals within a radius, nearest first.

    Two ways a hospital gets a distance, cheapest first:

      the patient's own town, matched by name — no lookup at all, and 0 km
      its town's coordinates, geocoded once by `_place_towns` and cached

    The radius is a real one. It used to be a label: hospitals were sorted into
    'in the town the patient typed' and 'everywhere else', nothing was measured,
    and the second pile was only ever offered when the first was empty. A
    patient in Jaffna therefore never saw the cardiologist at Abishaa Hospital
    in Vavuniya, 114 km away and well inside the 150 km rung — because Jaffna
    has two hospitals of its own, neither with a cardiologist, and their mere
    existence pinned every rung to them. 'None within 400 km' was printed by
    code that had measured nothing.
    """
    towns = towns or {}
    found: list[tuple[float, doc990.Hospital]] = []
    unplaced: list[doc990.Hospital] = []

    for hospital in hospitals:
        city = (hospital.city or "").strip()
        if not city:
            continue
        if _same_town(city, location):
            found.append((0.0, hospital))
            continue

        where = towns.get(city)
        if where is None:
            # No coordinates for this town — a name Google could not place, or
            # no Google at all. Held back rather than dropped: the last rung
            # still offers it, which is what this function did for every
            # out-of-town hospital before towns were geocoded.
            unplaced.append(hospital)
            continue

        distance = haversine_km(origin[0], origin[1], where[0], where[1])
        if distance <= radius_km:
            found.append((distance, hospital))

    if found:
        return sorted(found, key=lambda row: row[0])[: max(want, 1) * 4]

    if radius_km < radii_last:
        # Nothing within this rung. Say so honestly and let the ladder widen —
        # the next rung is a larger circle over the same, already-cached,
        # coordinates and costs nothing but the arithmetic.
        return []

    # The ladder is out of rungs and nothing could be placed near the patient.
    # Offering hospitals whose towns are unknown, unranked, beats offering
    # nothing: the distance for the few actually shown is filled in later from
    # their Google listing, which is fetched for the address regardless.
    return [(float("inf"), hospital) for hospital in unplaced[: max(want, 1) * 4]]


def find_doc990_doctors(
    specialty: Specialty,
    location: str,
    places: PlacesClient | None = None,
    radii_km: list[int] | None = None,
    enough: int = REFERRAL_ENOUGH,
    limit: int = REFERRAL_MAX_RESULTS,
) -> ReferralSearch:
    """Named doctors from Doc990, the other channelling network.

    Asked separately from eChannelling rather than merged, because the two sign
    different hospitals and a patient is served by whichever their local one
    joined. Vavuniya is the case that proves it: eChannelling has no
    cardiologist within 50 km, Doc990 has one in the town.

    Works hospital by hospital because Doc990's search cannot filter by place —
    so the ladder widens over hospitals near the patient rather than over the
    doctors themselves.
    """
    radii = radii_km or REFERRAL_RADII_KM
    own_places = places is None
    search, origin, places = _start(specialty, location, places)

    try:
        if origin is None:
            return search
        if not ENABLE_DOC990:
            search.degraded.append("Doc990 is switched off.")
            return search
        if not specialty.search_terms:
            search.notice = (
                f"{specialty.label} is not something this directory lists. Look at "
                "nearby places instead."
            )
            return search

        city_coords: dict[str, tuple[float, float] | None] = {}
        try:
            with doc990.session() as client:
                hospitals, catalogue = doc990.catalogue(client)
                code = _doc990_specialisation(specialty, catalogue)
                if code is None:
                    search.notice = (
                        f"Doc990 does not list {specialty.label.lower()} as a specialty."
                    )
                    return search

                order = doc990.baseline(client, code)
                search.sources.append("doc990")

                # Each hospital is asked about once per request, not once per
                # rung. Widening 5 -> 10 -> 25 km re-includes every hospital
                # already inside the smaller circle, and each of those questions
                # costs a 3 MB page — without this the Vavuniya ladder fetched
                # thirty of them to look at six hospitals.
                asked: dict[str, list[doc990.Doctor]] = {}

                # Once per search, not once per rung: the rungs are circles
                # over the same coordinates, and widening 5 -> 10 km must not
                # cost a second round of geocoding.
                placed = _place_towns(list(hospitals.values()), places)

                for radius in radii:
                    near = _hospitals_near(
                        list(hospitals.values()),
                        location,
                        origin,
                        radius,
                        radii[-1],
                        towns=placed,
                        want=DOC990_MAX_HOSPITALS,
                    )[:DOC990_MAX_HOSPITALS]

                    pending = [h for _, h in near if h.hospital_id not in asked]
                    if pending:
                        # Independent 3 MB fetches, so asked for at once. Six in
                        # series was twenty-six seconds of a sixty-seven second
                        # search.
                        with ThreadPoolExecutor(max_workers=DOC990_MAX_HOSPITALS) as pool:
                            for hospital, doctors in zip(
                                pending,
                                pool.map(
                                    lambda h: doc990.doctors_at(client, h, code, order),
                                    pending,
                                ),
                            ):
                                asked[hospital.hospital_id] = doctors

                    found: list[tuple[float, doc990.Doctor]] = []
                    for distance, hospital in near:
                        for doctor in asked.get(hospital.hospital_id, []):
                            found.append((distance, doctor))

                    search.attempts.append(
                        Attempt(
                            term=specialty.label,
                            radius_km=radius,
                            found=len(found),
                            provider="doc990",
                        )
                    )
                    if len(found) >= enough or (found and radius >= radii[-1]):
                        search.widened_to_km = radius
                        found.sort(key=lambda row: row[0])
                        search.results = _doc990_rows(
                            found[:limit], origin, places, client, city_coords
                        )
                        break
        except doc990.Doc990Unavailable as exc:
            logger.warning("Doc990 unavailable: %s", exc)
            search.degraded.append(
                "Doc990 was unavailable, so no doctors from that network could be "
                "listed. The other directories are unaffected."
            )
            return search

        _finish_doctors(search, specialty, radii)
        return search
    finally:
        if own_places and places is not None:
            places.close()


def _doc990_rows(
    found: list[tuple[float, doc990.Doctor]],
    origin: tuple[float, float],
    places: PlacesClient | None,
    client,
    city_coords: dict[str, tuple[float, float] | None],
) -> list[Referral]:
    """Confirm each doctor's hospital, then attach its address and phone.

    The confirmation is not optional. Which hospital a doctor belongs to was
    inferred from the order the search page returned them in, and an inference
    is a reasonable way to narrow a list and not a thing to print beside a phone
    number. `/channel/{id}` states the hospital itself, and that is what gets
    shown.
    """
    # Confirmed concurrently: twenty independent page loads, and in series they
    # were the single largest cost in the search.
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda pair: doc990.confirm(client, pair[1]), found))

    rows: list[Referral] = []
    for distance, doctor in found:
        hospital = doctor.hospital
        row = Referral(
            kind="doctor",
            name=doctor.name,
            specialty=doctor.specialty,
            clinic_name=hospital.name if hospital else None,
            city=hospital.city if hospital else None,
            distance_km=round(distance, 1),
            source="doc990",
            source_url=doctor.booking_url,
            booking_url=doctor.booking_url,
        )
        if places is not None and hospital is not None:
            city = hospital.city
            anchor = city_coords.get(city) if city else None
            try:
                place = places.locate(hospital.name, near=anchor or origin)
                if place is not None and _is_same_place(place, anchor):
                    places.details(place)
                    row.address = place.address
                    row.rating = place.rating
                    row.reviews = place.reviews
                    row.phone = place.phone
                    row.open_now = place.open_now
                    row.hours = place.hours
                    row.website = place.website
                    row.distance_km = round(
                        haversine_km(origin[0], origin[1], place.latitude, place.longitude), 1
                    )
            except PlacesUnavailable as exc:
                logger.info("no Google listing for %r: %s", hospital.name, exc)
        rows.append(row)

    rows.sort(key=lambda r: (r.distance_km is None, r.distance_km or 0.0))
    return rows


def find_clinics(
    specialty: Specialty,
    location: str,
    places: PlacesClient | None = None,
    radii_km: list[int] | None = None,
    enough: int = REFERRAL_ENOUGH,
    limit: int = REFERRAL_MAX_RESULTS,
) -> ReferralSearch:
    """Hospitals and clinics near the patient, from Google.

    Not a fallback for `find_doctors` — an answer in its own right.
    eChannelling lists private channelling only, so the government hospitals
    are missing from it entirely. For a patient in Jaffna the nearest bookable
    cardiologist is 26 km away and the teaching hospital is 700 m away, and the
    second of those is the more useful thing to know.
    """
    radii = radii_km or REFERRAL_RADII_KM
    own_places = places is None
    search, origin, places = _start(specialty, location, places)

    try:
        if origin is None:
            return search
        if places is None:
            search.degraded.append("The map directory is unavailable.")
            return search

        _ladder_over_places(search, specialty, origin, places, radii, enough, limit)
        _finish_clinics(search, specialty, radii)
        return search
    finally:
        if own_places and places is not None:
            places.close()


def _ladder_over_doctors(
    search: ReferralSearch,
    doctors: list[ech.Doctor],
    origin: tuple[float, float],
    places: PlacesClient | None,
    radii: list[int],
    enough: int,
    limit: int,
) -> None:
    """Widen over a list already in memory. No API call per rung."""
    # Each doctor's nearest hospital, computed once.
    placed: list[tuple[float, ech.Doctor, ech.Hospital]] = []
    for doctor in doctors:
        best: tuple[float, ech.Hospital] | None = None
        for hospital in doctor.hospitals:
            distance = _distance_to_hospital(hospital, origin, search.location)
            if distance is None:
                continue
            if best is None or distance < best[0]:
                best = (distance, hospital)
        if best is None and doctor.hospitals:
            # Nowhere to put them without a lookup, so they wait for the final
            # rung and get a real distance from their Google listing there.
            best = (float("inf"), doctor.hospitals[0])
        if best is not None:
            placed.append((best[0], doctor, best[1]))
    placed.sort(key=lambda row: row[0])

    for radius in radii:
        limitless = radius >= radii[-1]
        within = [row for row in placed if row[0] <= radius or (limitless and row[0] == float("inf"))]
        search.attempts.append(
            Attempt(
                term=search.specialty,
                radius_km=radius,
                found=len(within),
                provider="echannelling",
            )
        )
        if len(within) >= enough or (within and radius >= radii[-1]):
            search.widened_to_km = radius
            search.results = _rows_for(within[:limit], origin, places)
            return

    # Nothing inside any radius, but the directory does list this specialty
    # somewhere. Saying how far away is far more use than saying 'none found'.
    if placed:
        search.widened_to_km = radii[-1]
        search.results = _rows_for(placed[:limit], origin, places)


# How far a Google match may sit from the town eChannelling put the clinic in
# before it is treated as a different place with a similar name. Chains here
# reuse one name across branches — 'Ceymed Healthcare Services' exists in both
# Nugegoda and Jaffna — and a search biased to the patient will happily return
# the wrong one, which is how a doctor 300 km away comes to be listed at 600 m.
SAME_PLACE_KM = 25.0


def _is_same_place(found, anchor: tuple[float, float] | None) -> bool:
    """Whether a Google match is plausibly the clinic we asked about.

    With no anchor there is nothing to check against, and the match is taken —
    a hospital whose town we never learned is no worse off for it.
    """
    if anchor is None:
        return True
    return haversine_km(anchor[0], anchor[1], found.latitude, found.longitude) <= SAME_PLACE_KM


def _rows_for(
    placed: list[tuple[float, ech.Doctor, ech.Hospital]],
    origin: tuple[float, float],
    places: PlacesClient | None,
    city_coords: dict[str, tuple[float, float] | None] | None = None,
) -> list[Referral]:
    """Attach the address, rating and phone to the doctors being shown.

    The distance is recomputed when Google finds the clinic, because the figure
    carried in was measured to the middle of a town. Three kilometres across
    Kandy is the difference between walking and not. It is only trusted when
    the match is plausibly the same building — see `SAME_PLACE_KM`.
    """
    city_coords = city_coords if city_coords is not None else {}
    rows: list[Referral] = []
    for distance, doctor, hospital in placed:
        row = Referral(
            kind="doctor",
            name=doctor.name,
            specialty=", ".join(doctor.specialties) or None,
            clinic_name=hospital.name,
            city=hospital.city,
            distance_km=round(distance, 1),
            slmc_number=doctor.slmc_number,
            other_clinics=[h.name for h in doctor.hospitals if h.name != hospital.name],
            source="echannelling",
            source_url=doctor.booking_url,
            booking_url=doctor.booking_url,
        )
        if places is not None:
            # Bias the lookup to the clinic's own town, not the patient's. A
            # chain name searched near the patient returns the patient's
            # nearest branch, which is a different clinic with the same sign.
            city = _city_of(hospital)
            anchor = city_coords.get(city) if city else None
            try:
                found = places.locate(hospital.name, near=anchor or origin)
                if found is not None and _is_same_place(found, anchor):
                    places.details(found)
                    row.address = found.address
                    row.rating = found.rating
                    row.reviews = found.reviews
                    row.phone = found.phone
                    row.open_now = found.open_now
                    row.hours = found.hours
                    row.website = found.website
                    row.distance_km = round(
                        haversine_km(origin[0], origin[1], found.latitude, found.longitude), 1
                    )
                elif found is not None:
                    # A real listing, but not the branch meant. Better to show
                    # the doctor with the town we were told and no address than
                    # to print someone else's street and phone number.
                    logger.info(
                        "ignoring Google match %r for %r: too far from %s",
                        found.name,
                        hospital.name,
                        city,
                    )
            except PlacesUnavailable as exc:
                logger.info("no Google listing for %r: %s", hospital.name, exc)
        rows.append(row)

    # Re-sorted because the order coming in was by city-level distance and the
    # exact one has just replaced it. Across Kandy that is the difference
    # between the clinic 500 m away and the one 3 km away leading the list.
    rows.sort(key=lambda r: (r.distance_km is None, r.distance_km or 0.0))
    return rows


def _ladder_over_places(
    search: ReferralSearch,
    specialty: Specialty,
    origin: tuple[float, float],
    places: PlacesClient,
    radii: list[int],
    enough: int,
    limit: int,
) -> None:
    """Hospitals and clinics near the patient, alongside any named doctors.

    Kept apart from the doctor rows rather than merged into one ranked list.
    They are different kinds of thing: one is a person to book an appointment
    with, the other a place to walk into, and the actions a reader takes on
    them differ. Sorting them together also puts a hospital directly beside the
    doctors who work in it, which reads as the same entry twice.
    """
    for radius in radii:
        query = specialty.query_for_places(search.location)
        try:
            found = places.search(query, origin[0], origin[1], radius, limit=limit)
        except PlacesUnavailable as exc:
            search.degraded.append(f"The map directory was unavailable ({exc}).")
            return

        search.attempts.append(
            Attempt(term=query, radius_km=radius, found=len(found), provider="google_places")
        )
        if len(found) < enough and radius < radii[-1]:
            continue

        rows: list[Referral] = []
        for place in found:
            places.details(place)
            rows.append(
                Referral(
                    kind="clinic",
                    name=place.name,
                    clinic_name=place.name,
                    address=place.address,
                    distance_km=round(
                        haversine_km(origin[0], origin[1], place.latitude, place.longitude), 1
                    ),
                    rating=place.rating,
                    reviews=place.reviews,
                    phone=place.phone,
                    open_now=place.open_now,
                    hours=place.hours,
                    website=place.website,
                    source="google_places",
                    source_url=place.source_url,
                )
            )

        if rows:
            rows.sort(key=lambda r: r.distance_km or 0.0)
            search.results.extend(rows[:limit])
            if "google_places" not in search.sources:
                search.sources.append("google_places")
        return


def _nearest(search: ReferralSearch) -> float | None:
    return min((r.distance_km for r in search.results if r.distance_km is not None), default=None)


def _finish_doctors(search: ReferralSearch, specialty: Specialty, radii: list[int]) -> None:
    """The sentence explaining an empty or a widened list of doctors."""
    if not search.results:
        search.notice = (
            f"No {specialty.label.lower()} is listed for booking within {radii[-1]} km "
            f"of {search.location}. Nearby hospitals may still be able to see you or "
            "refer you."
        )
        return

    nearest = _nearest(search)
    if nearest is None or nearest <= radii[0]:
        return
    search.notice = (
        f"The nearest {specialty.label.lower()} you can book is {nearest:g} km from "
        f"{search.location}, so we widened the search to {search.widened_to_km} km."
    )


def _finish_clinics(search: ReferralSearch, specialty: Specialty, radii: list[int]) -> None:
    """The same, for places rather than people."""
    if not search.results:
        search.notice = (
            f"We found no hospitals or clinics within {radii[-1]} km of "
            f"{search.location}. Try a nearby city."
        )
        return

    nearest = _nearest(search)
    if nearest is None or nearest <= radii[0]:
        return
    search.notice = (
        f"Nothing was listed within {radii[0]} km of {search.location}, so we widened "
        f"the search to {search.widened_to_km} km. The nearest is {nearest:g} km away."
    )
