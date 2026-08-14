"""Where a clinic actually is, and how to reach it, from Google Maps.

eChannelling knows which cardiologist channels at 'Asiri Hospital Kandy' and
nothing else about it — no address, no phone, and a latitude of 0. This module
turns that name into a place: an address, coordinates, a rating, a phone
number, and therefore a distance from the patient.

It also stands alone. When eChannelling is unreachable, or for a specialty no
one channels privately, a Places search still returns real hospitals near the
patient. The feature degrades from 'here are 21 cardiologists' to 'here are
hospitals near you that can refer you' — thinner, still true, never empty by
accident.

Nothing is cached beyond the life of one client. Google's terms allow keeping
only a place_id; names, ratings and phone numbers must be fetched live, which
is also the only way a clinic that closed last week stops being recommended.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import httpx

from app.config import GOOGLE_MAPS_API_KEY, GOOGLE_MAPS_TIMEOUT

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
AUTOCOMPLETE_URL = "https://maps.googleapis.com/maps/api/place/autocomplete/json"

# Only what is displayed. Phone and hours are billed at a higher tier than the
# rest, so asking for more than the screen shows costs money for nothing.
DETAIL_FIELDS = "formatted_phone_number,international_phone_number,opening_hours,website,url"

# Searches are biased to a point rather than restricted to one, so Google will
# happily return something 300 km away for a rare specialty. Every result is
# re-checked against the radius here.
EARTH_RADIUS_KM = 6371.0


class PlacesUnavailable(Exception):
    """Google could not be reached, or refused. The search degrades."""


@dataclass
class Place:
    """One real, currently-listed place."""

    name: str
    address: str | None
    latitude: float
    longitude: float
    place_id: str
    rating: float | None = None
    reviews: int | None = None
    phone: str | None = None
    website: str | None = None
    open_now: bool | None = None
    hours: list[str] = field(default_factory=list)
    maps_url: str | None = None
    types: list[str] | None = None

    source: str = "google_places"

    @property
    def source_url(self) -> str:
        """Somewhere a reader can go and check this is a real place."""
        return self.maps_url or f"https://www.google.com/maps/place/?q=place_id:{self.place_id}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance. The number a patient reads as 'how far'.

    Not driving distance, which would need another billed API and another
    round trip. Worth knowing when it is quoted: 3 km here can be 6 km of road.
    """
    first, second = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(first) * math.cos(second) * math.sin(delta_lon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


class PlacesClient:
    """A Google client whose caches live and die with one request.

    The cache is on the instance rather than the module on purpose. Twenty-one
    cardiologists in Kandy sit at three hospitals, so looking each one up once
    turns 21 billed lookups into 3 — but a cache shared between requests would
    outlive the 30 days Google allows and start serving a patient somebody
    else's stale result.
    """

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key or GOOGLE_MAPS_API_KEY
        self._client = client or httpx.Client(timeout=GOOGLE_MAPS_TIMEOUT)
        self._owns_client = client is None
        self._geocoded: dict[str, tuple[float, float] | None] = {}
        self._found: dict[str, Place | None] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "PlacesClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # --- requests ------------------------------------------------------------

    def _get(self, url: str, params: dict) -> dict:
        if not self.enabled:
            raise PlacesUnavailable("no Google Maps API key is configured")
        try:
            response = self._client.get(url, params={**params, "key": self.api_key})
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PlacesUnavailable(str(exc)) from exc

        status = data.get("status")
        if status in ("OK", "ZERO_RESULTS"):
            return data
        # REQUEST_DENIED usually means the API is not enabled on the project or
        # the key is restricted to other referrers; both are worth the log line
        # because both look like 'no doctors found' from the outside.
        raise PlacesUnavailable(f"{status}: {data.get('error_message', '')}".strip(": "))

    # --- geocoding -----------------------------------------------------------

    def geocode(self, place: str, region: str = "lk") -> tuple[float, float] | None:
        """A typed place name to coordinates, or None if it is not a place."""
        key = place.strip().lower()
        if key in self._geocoded:
            return self._geocoded[key]

        data = self._get(GEOCODE_URL, {"address": place, "region": region})
        results = data.get("results") or []
        found = None
        if results:
            location = results[0]["geometry"]["location"]
            found = (location["lat"], location["lng"])
        self._geocoded[key] = found
        return found

    def suggest(self, typed: str, country: str = "lk", limit: int = 6) -> list[str]:
        """Place names matching what the user has typed so far.

        Restricted to regions, so a search for 'kand' offers Kandy and Kandana
        rather than every restaurant in either. Restricted to one country too:
        a patient looking for a doctor near Kandy does not mean Kandy, Ohio.

        Exists so the location is *chosen* rather than typed. A free-text box
        accepts 'Vavunia' and 'vavuniys', which geocode to nothing, and the
        screen then has to explain a failure that was really a typo.
        """
        typed = typed.strip()
        if len(typed) < 2:
            return []
        data = self._get(
            AUTOCOMPLETE_URL,
            {"input": typed, "components": f"country:{country}", "types": "(regions)"},
        )
        return [
            prediction["description"]
            for prediction in (data.get("predictions") or [])[:limit]
            if prediction.get("description")
        ]

    # --- places --------------------------------------------------------------

    def _parse(self, row: dict) -> Place | None:
        if row.get("business_status") == "CLOSED_PERMANENTLY":
            return None  # sending a patient to a closed clinic is worse than nothing
        location = (row.get("geometry") or {}).get("location") or {}
        if "lat" not in location or "lng" not in location:
            return None
        return Place(
            name=row.get("name") or "",
            address=row.get("formatted_address"),
            latitude=location["lat"],
            longitude=location["lng"],
            place_id=row.get("place_id") or "",
            rating=row.get("rating"),
            reviews=row.get("user_ratings_total"),
            open_now=(row.get("opening_hours") or {}).get("open_now"),
            types=row.get("types"),
        )

    def search(
        self,
        query: str,
        latitude: float,
        longitude: float,
        radius_km: float,
        limit: int = 20,
    ) -> list[Place]:
        """Places matching a query, genuinely within the radius.

        Google biases by location rather than restricting to it, so the radius
        is enforced here — otherwise a search for a rare specialty 'near
        Vavuniya' quietly returns Colombo and the widening ladder never widens.
        """
        data = self._get(
            TEXTSEARCH_URL,
            {
                "query": query,
                "location": f"{latitude},{longitude}",
                "radius": int(radius_km * 1000),
            },
        )
        out: list[Place] = []
        for row in data.get("results") or []:
            place = self._parse(row)
            if place is None:
                continue
            if haversine_km(latitude, longitude, place.latitude, place.longitude) > radius_km:
                continue
            out.append(place)
            if len(out) >= limit:
                break
        return out

    def locate(self, name: str, near: tuple[float, float] | None = None) -> Place | None:
        """The real listing behind a clinic name eChannelling gave us.

        Returns None rather than a guess when nothing matches. A doctor whose
        hospital cannot be located is still shown — without a distance, and
        saying so — because dropping them would silently shorten the list.
        """
        key = name.strip().lower()
        if key in self._found:
            return self._found[key]

        params: dict = {"query": f"{name} Sri Lanka"}
        if near:
            params |= {"location": f"{near[0]},{near[1]}", "radius": 50000}

        data = self._get(TEXTSEARCH_URL, params)
        place = None
        for row in data.get("results") or []:
            place = self._parse(row)
            if place is not None:
                break
        self._found[key] = place
        return place

    def details(self, place: Place) -> Place:
        """Fill in phone, hours and the canonical Maps link.

        A separate billed call, so it is made only for places about to be
        shown. Failure is not fatal: a clinic with an address and no phone is
        still worth showing, and the field renders as 'not listed'.
        """
        if not place.place_id:
            return place
        try:
            data = self._get(DETAILS_URL, {"place_id": place.place_id, "fields": DETAIL_FIELDS})
        except PlacesUnavailable as exc:
            logger.info("no details for %s: %s", place.name, exc)
            return place

        result = data.get("result") or {}
        place.phone = result.get("formatted_phone_number") or result.get(
            "international_phone_number"
        )
        place.website = result.get("website")
        place.maps_url = result.get("url")
        hours = result.get("opening_hours") or {}
        if "open_now" in hours:
            place.open_now = hours["open_now"]
        # All seven days, not today's line. Which day it is depends on where the
        # reader is, not where this process runs: 1 am Monday in Colombo is
        # still Sunday evening in UTC, and a server that picked the line would
        # quote the wrong day's hours to anyone up late.
        place.hours = list(hours.get("weekday_text") or [])
        return place
