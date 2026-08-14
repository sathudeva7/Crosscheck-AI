"""Doctors from Doc990 (doc.lk), Dialog's channelling network.

A second doctor directory, not a replacement for the first. The two networks
overlap but do not coincide: eChannelling found no cardiologist within 50 km of
Vavuniya, and Doc990 lists one at Abishaa Hospital in the town itself. Which
one has your doctor depends on which network the hospital signed with, so
asking only one leaves patients being told to travel who did not have to.

Everything here is server-rendered HTML, parsed deterministically. No model
reads these pages; a page whose markup stops matching yields nothing rather
than something approximate.

The one awkward thing about this site, and the reason for `doctors_at`:

    /search?specialization=4&hospital=121

does not filter by hospital. It returns all 872 cardiologists in the country
whatever hospital you name — but it *reorders* them, moving that hospital's own
doctors to the front. So the hospital's list is the prefix that runs before the
default ordering resumes, and finding that boundary is how a hospital's doctors
are read out. The alignment is checked over several names rather than one, so a
doctor who happens to sort first nationally cannot collapse the boundary to
zero.

Anything actually shown to a patient is then confirmed against
`/channel/{id}`, whose title states the hospital outright. The ordering tells
us where to look; the channel page is what we believe.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from app.config import DOC990_BASE_URL, DOC990_TIMEOUT

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

# How many names must line up before the tail is called 'the default order'.
# One would be brittle: a hospital whose own doctor also sorts first nationally
# would read as a hospital with no doctors at all.
_ALIGN = 5

# The search page is ~3 MB of markup. These read the two fields that matter out
# of it directly rather than building a document tree for the other 99%.
_CARD = re.compile(
    r'data-link="//www\.doc\.lk/channel/(\d+)[^"]*".*?'
    r'class="doctor-name">\s*<a[^>]*>\s*(.*?)\s*</a>.*?'
    r'class="doctor-title">\s*(.*?)\s*</li>',
    re.S,
)
_OPTION = re.compile(r'<option value="(\d+)"[^>]*>\s*(.*?)\s*</option>', re.S)
_SELECT = re.compile(
    r'<select[^>]*id=[\'"]{name}-input[\'"].*?</select>', re.S
)
# 'Channel Dr. H. D. SAMARASINGHE - Aayu by Nawaloka Care PREMIER - Colombo 07 - Doc990'
_TITLE = re.compile(r"Channel\s+(.*?)\s+-\s+(.*?)\s+-\s+Doc990", re.S)
_TAGS = re.compile(r"<[^>]+>")


class Doc990Unavailable(Exception):
    """The directory could not be reached. The search degrades, never fails."""


@dataclass
class Hospital:
    """One hospital on the network. The town is part of its name."""

    hospital_id: str
    name: str

    @property
    def city(self) -> str | None:
        """The town, taken off the end of the name.

        'Asiri Hospital - Kandy', 'Abishaa Hospital - Vavuniya'. Doc990 gives no
        coordinates at all, so this string is the only thing that can place a
        hospital on a map.
        """
        if "-" not in self.name:
            return None
        tail = self.name.rsplit("-", 1)[-1].strip()
        return tail or None


@dataclass
class Doctor:
    """One doctor at one hospital, as Doc990 lists them."""

    name: str
    specialty: str | None
    channel_id: str
    hospital: Hospital | None = None
    confirmed: bool = False  # the channel page named this hospital itself
    other_specialties: list[str] = field(default_factory=list)

    @property
    def booking_url(self) -> str:
        return f"{DOC990_BASE_URL}/channel/{self.channel_id}"


def _clean(text: str) -> str:
    return " ".join(_TAGS.sub(" ", text or "").split())


def session() -> httpx.Client:
    return httpx.Client(
        timeout=DOC990_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": BROWSER_UA},
    )


def _get(client: httpx.Client, path: str) -> str:
    try:
        response = client.get(f"{DOC990_BASE_URL}{path}")
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as exc:
        raise Doc990Unavailable(str(exc)) from exc


def _options(html: str, field_name: str) -> dict[str, str]:
    block = _SELECT.pattern.replace("{name}", field_name)
    found = re.search(block, html, re.S)
    if not found:
        return {}
    return {
        value: _clean(label)
        for value, label in _OPTION.findall(found.group(0))
        if value != "0" and _clean(label)
    }


def catalogue(client: httpx.Client) -> tuple[dict[str, Hospital], dict[str, str]]:
    """Every hospital and every specialty Doc990 knows, from one page.

    217 hospitals and 981 specialties, both with their own ids and their own
    wording — 'Chest Specialist' here too, but numbered differently from
    eChannelling. Nothing may assume the two vocabularies line up.
    """
    html = _get(client, "/search")
    hospitals = {
        hid: Hospital(hospital_id=hid, name=name)
        for hid, name in _options(html, "hospital").items()
    }
    specialities = _options(html, "specialization")
    if not hospitals or not specialities:
        raise Doc990Unavailable("could not read the hospital or specialty lists")
    return hospitals, specialities


def _cards(html: str) -> list[tuple[str, str, str]]:
    """(channel_id, name, specialty) for every doctor card on a page."""
    return [
        (channel_id, _clean(name), _clean(specialty) or None)
        for channel_id, name, specialty in _CARD.findall(html)
    ]


def _search(client: httpx.Client, specialisation_id: str, hospital_id: str = "0") -> list:
    path = (
        f"/search?doctor=&hospital={hospital_id}"
        f"&specialization={specialisation_id}&date="
    )
    return _cards(_get(client, path))


def baseline(client: httpx.Client, specialisation_id: str) -> list[str]:
    """The national ordering for a specialty, against which prefixes are read."""
    return [name for _, name, _ in _search(client, specialisation_id)]


def doctors_at(
    client: httpx.Client,
    hospital: Hospital,
    specialisation_id: str,
    order: list[str],
) -> list[Doctor]:
    """The doctors of one specialty who channel at one hospital.

    Read as the prefix that precedes the national ordering — see the note at
    the top of this module. Returns nothing rather than guessing when the two
    lists never align: an unrecognised page shape must look like no doctors,
    not like the whole country's worth of them.
    """
    if not order:
        return []
    rows = _search(client, specialisation_id, hospital.hospital_id)
    names = [name for _, name, _ in rows]

    head = order[:_ALIGN]
    boundary = next(
        (i for i in range(len(names)) if names[i : i + _ALIGN] == head),
        None,
    )
    if boundary is None:
        logger.info(
            "doc990: ordering for %s never realigned; treating as no doctors",
            hospital.name,
        )
        return []

    return [
        Doctor(name=name, specialty=specialty, channel_id=channel_id, hospital=hospital)
        for channel_id, name, specialty in rows[:boundary]
    ]


def confirm(client: httpx.Client, doctor: Doctor) -> Doctor:
    """Check the channel page agrees about the hospital.

    The ordering told us where to look; this is what we believe. A doctor whose
    own page names a different hospital is dropped by the caller rather than
    shown at an address they do not attend — an inference is a fine way to
    narrow a search and not a thing to print next to a phone number.
    """
    try:
        html = _get(client, f"/channel/{doctor.channel_id}")
    except Doc990Unavailable as exc:
        logger.info("doc990: could not confirm %s: %s", doctor.name, exc)
        return doctor

    found = _TITLE.search(_clean(html[: html.find("</head>") + 1]) if "</head>" in html else html)
    if not found:
        return doctor

    named_hospital = found.group(2).strip()
    if doctor.hospital and _same(named_hospital, doctor.hospital.name):
        doctor.confirmed = True
    else:
        logger.info(
            "doc990: %s is listed at %r, not %r",
            doctor.name,
            named_hospital,
            doctor.hospital.name if doctor.hospital else None,
        )
        doctor.hospital = Hospital(hospital_id="", name=named_hospital)
        doctor.confirmed = True
    return doctor


def _same(a: str, b: str) -> bool:
    """Whether two hospital names are the same place, punctuation aside."""
    flatten = lambda text: " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())  # noqa: E731
    return flatten(a) == flatten(b)
