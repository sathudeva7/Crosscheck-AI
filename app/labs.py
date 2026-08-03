"""Measurements over time, grouped into series a person can read.

One job, done once: deciding that 'BP' and 'Blood Pressure' are the same test.
Both this module's series and the trend checks in app/checks/labs.py go through
`canonical`, because two parts of the app disagreeing about what counts as one
test is worse than either grouping rule on its own — the page would show a
history the finding never saw.

Three kinds of reading come out of a document, and they are not
interchangeable:

    a number          6.8 %          plottable, comparable, trendable
    a qualified number  <0.01        shown, never plotted -- see `plottable`
    words             'positive for S. aureus'   shown, never plotted

Nothing here decides whether a result is bad. A value is called high or low
only against a reference range the document itself supplied; where there is no
range there is no verdict, because inventing one would be diagnosis.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from app.db.models import TimelineEvent

# Names for the same test, as they turn up on real documents. Matched against
# the *whole* normalised name, never as a substring: 'hb' is haemoglobin and
# 'hba1c' is not, and a substring rule would quietly merge them.
ALIASES: dict[str, str] = {
    "bp": "Blood Pressure",
    "blood pressure": "Blood Pressure",
    "b p": "Blood Pressure",
    "temp": "Temperature",
    "temperature": "Temperature",
    "wt": "Weight",
    "weight": "Weight",
    "body weight": "Weight",
    "ht": "Height",
    "height": "Height",
    "hr": "Heart Rate",
    "pulse": "Heart Rate",
    "heart rate": "Heart Rate",
    "spo2": "Oxygen Saturation",
    "o2 sat": "Oxygen Saturation",
    "oxygen saturation": "Oxygen Saturation",
    "hba1c": "HbA1c",
    "hb a1c": "HbA1c",
    "a1c": "HbA1c",
    "glycated haemoglobin": "HbA1c",
    "hb": "Haemoglobin",
    "hgb": "Haemoglobin",
    "haemoglobin": "Haemoglobin",
    "hemoglobin": "Haemoglobin",
    "inr": "INR",
    "creatinine": "Creatinine",
    "s creatinine": "Creatinine",
    "serum creatinine": "Creatinine",
    "fbs": "Fasting Blood Sugar",
    "fasting blood sugar": "Fasting Blood Sugar",
    "rbs": "Random Blood Sugar",
    "random blood sugar": "Random Blood Sugar",
    "wbc": "White Cell Count",
    "white cell count": "White Cell Count",
    "plt": "Platelets",
    "platelets": "Platelets",
    "tsh": "TSH",
    "ldl": "LDL Cholesterol",
    "hdl": "HDL Cholesterol",
    "total cholesterol": "Total Cholesterol",
    "cholesterol": "Total Cholesterol",
}

# Units the same measurement is written in. Only spelling is unified here --
# never the magnitude, because converting mg/dL to mmol/L needs a factor per
# analyte and getting one wrong would move a reading across its range.
UNITS: dict[str, str] = {
    "cm": "cm",
    "cms": "cm",
    "centimeter": "cm",
    "centimetre": "cm",
    "kg": "kg",
    "kgs": "kg",
    "kilogram": "kg",
    "deg": "°C",
    "deg c": "°C",
    "c": "°C",
    "celsius": "°C",
    "mmhg": "mmHg",
    "bpm": "bpm",
    "%": "%",
}

# Below this the first and last readings are called steady rather than moving.
# A share of the starting value, so it means the same thing for a weight in
# kilograms and an INR.
STEADY_WITHIN = 0.02


def _squash(name: str) -> str:
    """Lowercase, strip punctuation, collapse spaces. 'S. Creatinine' -> 's creatinine'."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", name.lower())).strip()


def canonical(analyte: str | None) -> str:
    """The grouping key: one name per test, whatever the document called it."""
    squashed = _squash(analyte or "")
    return ALIASES.get(squashed, squashed)


def unit_of(raw: str | None) -> str | None:
    """The unit as it should be shown. Unknown units pass through untouched."""
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip()
    return UNITS.get(_squash(cleaned) or cleaned.lower(), cleaned)


@dataclass
class Reading:
    """One result, and whether it can be compared with the others."""

    event_id: str
    date: date | None
    value: float | None
    # What the document printed, when it was words rather than a number.
    text: str | None
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    # '<' or '>' — the lab declining to state a value precisely.
    operator: str | None
    document_id: str | None
    page_no: int | None
    # 'high' | 'low' | 'normal', and None whenever no range was recorded.
    status: str | None
    # Whether this point may go on a line.
    plottable: bool
    needs_review: bool


@dataclass
class Series:
    """Every reading of one test, oldest first."""

    key: str
    name: str
    unit: str | None
    readings: list[Reading]
    ref_low: float | None
    ref_high: float | None
    latest: Reading | None
    # 'rising' | 'falling' | 'steady', and None with fewer than two points.
    direction: str | None
    change: float | None
    # The document spellings this series was assembled from. Shown on the card,
    # because a merge the reader cannot see is a merge they cannot correct.
    source_names: list[str] = field(default_factory=list)

    @property
    def plottable(self) -> list[Reading]:
        return [r for r in self.readings if r.plottable]

    @property
    def chartable(self) -> bool:
        """Two points make a line. One is a reading, not a trend."""
        return len(self.plottable) >= 2


def _status(value: float | None, low: float | None, high: float | None) -> str | None:
    """Called high or low only against a range the document supplied."""
    if value is None or (low is None and high is None):
        return None
    if high is not None and value > high:
        return "high"
    if low is not None and value < low:
        return "low"
    return "normal"


def _reading(event: TimelineEvent) -> Reading:
    value = float(event.value_num) if event.value_num is not None else None
    low = float(event.ref_low) if event.ref_low is not None else None
    high = float(event.ref_high) if event.ref_high is not None else None

    return Reading(
        event_id=str(event.id),
        date=event.event_date,
        value=value,
        text=event.value_text,
        unit=unit_of(event.unit),
        ref_low=low,
        ref_high=high,
        operator=event.operator,
        document_id=str(event.document_id) if event.document_id else None,
        page_no=event.page_no,
        status=_status(value, low, high),
        # A qualified value is excluded for the same reason app/checks/labs.py
        # excludes it: '<0.01' means the assay could not measure it, and
        # drawing it at 0.01 invents a precision the lab declined to claim.
        plottable=value is not None and event.operator is None and event.event_date is not None,
        needs_review=event.review_status == "needs_review",
    )


def _direction(points: list[Reading]) -> tuple[str | None, float | None]:
    """Which way it has moved, first reading to last."""
    if len(points) < 2:
        return None, None

    first, last = points[0].value, points[-1].value
    assert first is not None and last is not None  # plottable guarantees it
    moved = last - first

    scale = abs(first) or 1.0
    if abs(moved) / scale < STEADY_WITHIN:
        return "steady", round(moved, 4)
    return ("rising" if moved > 0 else "falling"), round(moved, 4)


def _commonest(values: Iterable[str | None]) -> str | None:
    """The unit most readings used. Series are labelled by what they mostly are."""
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        if value:
            counts[value] += 1
    return max(counts, key=lambda unit: counts[unit]) if counts else None


def build_series(events: list[TimelineEvent]) -> list[Series]:
    """Group measurements into one series per test, most recently seen first.

    Rejected rows are dropped — a reading a person has already marked wrong
    should not sit in a chart of their history. Rows still awaiting review are
    kept and flagged, because hiding them would hide exactly the misread values
    a chart is best at exposing.
    """
    grouped: dict[str, list[TimelineEvent]] = defaultdict(list)
    for event in events:
        if event.event_type not in ("lab_result", "vital_sign"):
            continue
        if event.review_status == "rejected":
            continue
        if not (event.analyte or "").strip():
            continue
        grouped[canonical(event.analyte)].append(event)

    series: list[Series] = []

    for key, rows in grouped.items():
        rows.sort(key=lambda e: (e.event_date is None, e.event_date))
        readings = [_reading(event) for event in rows]
        points = [r for r in readings if r.plottable]

        # The newest range wins: a lab that revised its reference interval
        # means the older one no longer describes what is being measured.
        ranged = [r for r in readings if r.ref_low is not None or r.ref_high is not None]
        low = ranged[-1].ref_low if ranged else None
        high = ranged[-1].ref_high if ranged else None

        direction, change = _direction(points)
        spellings = sorted({(e.analyte or "").strip() for e in rows if (e.analyte or "").strip()})

        series.append(
            Series(
                key=key,
                # Title case from the alias table where we have one, otherwise
                # whatever the most recent document called it.
                name=ALIASES.get(_squash(rows[-1].analyte or ""), (rows[-1].analyte or key).strip()),
                unit=_commonest(r.unit for r in readings),
                readings=readings,
                ref_low=low,
                ref_high=high,
                latest=readings[-1] if readings else None,
                direction=direction,
                change=change,
                source_names=spellings,
            )
        )

    # Something out of range first, then whatever has the most history: the
    # reason to open this page is a result that needs attention.
    def rank(item: Series) -> tuple[int, int, str]:
        alarming = 0 if (item.latest and item.latest.status in ("high", "low")) else 1
        return (alarming, -len(item.readings), item.name.lower())

    return sorted(series, key=rank)
