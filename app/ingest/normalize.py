"""Turn what the document printed into values a computer can compare.

Extraction kept everything exactly as written, on purpose — '500mg', '<0.01',
'27-Apr-2020', '1-0-1'. That is faithful, and useless for comparing. You cannot
sort a timeline by a string, or ask whether creatinine is rising, or notice the
same drug prescribed twice.

This module adds the comparable form beside the printed one. It never replaces
it: `raw` is kept on every result, because when a parse is wrong the only way
to see it is against what the page actually said.

Nothing here calls a model or the network — it is plain parsing, so it is fast,
free and gives the same answer every time. Brand-name resolution, which does
need both, lives in drugs.py.

Where it cannot be sure it says so rather than guessing. `04-07-2019` is 4 July
in most of the world and 7 April in the United States, and no amount of
cleverness settles it from the digits alone: it comes back parsed *and* flagged
ambiguous, so the interface can show the doubt.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.config import DATE_ORDER

logger = logging.getLogger(__name__)

# A year this far ahead is a misread, not a real document date.
FUTURE_YEARS_ALLOWED = 1


# --- dates -------------------------------------------------------------------


@dataclass
class ParsedDate:
    raw: str
    value: date | None = None
    ambiguous: bool = False  # day/month order could not be settled
    reason: str = ""

    def __bool__(self) -> bool:
        return self.value is not None


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_TIME = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b", re.IGNORECASE)
_ISO = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
_NAMED_DMY = re.compile(r"\b(\d{1,2})[-\s/]([A-Za-z]{3,9})[-\s/](\d{2,4})\b")
_NAMED_MDY = re.compile(r"\b([A-Za-z]{3,9})[-\s/](\d{1,2}),?\s*(\d{2,4})\b")


def _year(value: int) -> int:
    """Expand a two-digit year. '12' is 2012, not 1912 — records are recent."""
    if value >= 100:
        return value
    current = date.today().year % 100
    return 2000 + value if value <= current + FUTURE_YEARS_ALLOWED else 1900 + value


def _build(year: int, month: int, day: int, raw: str, ambiguous: bool, reason: str) -> ParsedDate:
    try:
        parsed = date(_year(year), month, day)
    except ValueError:
        return ParsedDate(raw=raw, reason=f"not a real date ({day}/{month}/{year})")

    if parsed.year > date.today().year + FUTURE_YEARS_ALLOWED:
        return ParsedDate(raw=raw, reason=f"{parsed.isoformat()} is too far in the future")

    return ParsedDate(raw=raw, value=parsed, ambiguous=ambiguous, reason=reason)


def parse_date(raw: str | None, prefer: str = DATE_ORDER) -> ParsedDate:
    """Read a date written in any of the formats documents actually use."""
    if not raw or not raw.strip():
        return ParsedDate(raw=raw or "", reason="no date given")

    text = _TIME.sub(" ", unicodedata.normalize("NFKC", raw)).strip()

    match = _ISO.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return _build(year, month, day, raw, False, "ISO order")

    match = _NAMED_DMY.search(text)
    if match and match.group(2)[:3].lower() in _MONTHS:
        day, name, year = match.groups()
        return _build(int(year), _MONTHS[name[:3].lower()], int(day), raw, False, "month named")

    match = _NAMED_MDY.search(text)
    if match and match.group(1)[:3].lower() in _MONTHS:
        name, day, year = match.groups()
        return _build(int(year), _MONTHS[name[:3].lower()], int(day), raw, False, "month named")

    match = _NUMERIC.search(text)
    if match:
        first, second, year = (int(g) for g in match.groups())
        # A number above 12 can only be a day, which settles the order without
        # having to assume anything.
        if first > 12:
            return _build(year, second, first, raw, False, "first number must be the day")
        if second > 12:
            return _build(year, first, second, raw, False, "second number must be the day")

        day_first = prefer.upper().startswith("D")
        day, month = (first, second) if day_first else (second, first)
        return _build(
            year, month, day, raw, True,
            f"day and month both {first}/{second}; read as {prefer}",
        )

    return ParsedDate(raw=raw, reason="no recognisable date")


# --- measurements ------------------------------------------------------------


@dataclass
class Measurement:
    """A printed result turned into something comparable."""

    raw: str
    value: float | None = None
    values: list[float] = field(default_factory=list)  # '120/80' -> [120.0, 80.0]
    unit: str | None = None
    operator: str | None = None  # '<', '>', '<=', '>='
    text: str | None = None  # 'Positive', 'Not detected' — no number at all
    reason: str = ""

    @property
    def comparable(self) -> bool:
        """Whether this can be put on a trend line."""
        return self.value is not None and self.operator is None


_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
_OPERATOR = re.compile(r"^\s*(<=|>=|<|>|≤|≥)\s*")
_FRACTION = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
_VALUE_UNIT = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*([^\d\s].*?)?\s*$")


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def parse_measurement(raw: str | None, unit: str | None = None) -> Measurement:
    """Read a lab value or a vital sign.

    Keeps '<0.01' as a number *and* an operator: the value matters for display,
    the operator matters because 'below the limit of detection' is not the same
    as measuring 0.01 and must not be plotted as if it were.
    """
    if raw is None or not str(raw).strip():
        return Measurement(raw=raw or "", reason="no value given")

    text = unicodedata.normalize("NFKC", str(raw)).strip()
    result = Measurement(raw=str(raw), unit=(unit or None))

    operator = _OPERATOR.match(text)
    if operator:
        symbol = operator.group(1).replace("≤", "<=").replace("≥", ">=")
        result.operator = symbol
        text = text[operator.end():].strip()

    # Blood pressure and anything else written as a pair. A slash in a measured
    # result is always a pair, never a fraction — '120/80' is two readings.
    if "/" in text:
        parts = [p.strip() for p in text.split("/")]
        numbers = [_number(_NUMBER.search(p).group()) for p in parts if _NUMBER.search(p)]
        if len(numbers) == len(parts) and len(numbers) > 1:
            result.values = [n for n in numbers if n is not None]
            result.value = result.values[0]
            result.reason = "a pair of values, e.g. blood pressure"
            return result

    match = _VALUE_UNIT.match(text)
    if match:
        result.value = _number(match.group(1))
        embedded = (match.group(2) or "").strip()
        if embedded and not result.unit:
            result.unit = embedded
        result.reason = "number" + (" with unit" if embedded else "")
        return result

    # Nothing numeric: 'Positive', 'Not detected', 'Normal'
    result.text = text
    result.reason = "not a number"
    return result


@dataclass
class Strength:
    raw: str
    value: float | None = None
    unit: str | None = None
    is_count: bool = False  # '1/2' means half a tablet, not a strength
    components: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


_UNITS = r"mcg|µg|ug|mg|grams?|g|kg|ml|l|iu|units?|%"
# Strict form first: the unit must end at a word boundary. The loose form is
# only tried when that finds nothing, so '50 mgSR' still yields 50 mg without
# letting the loose rule misread cleaner text.
_STRENGTH = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({_UNITS})\b", re.IGNORECASE)
_STRENGTH_LOOSE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({_UNITS})", re.IGNORECASE)
_UNIT_ALIASES = {"gram": "g", "grams": "g", "µg": "mcg", "ug": "mcg", "unit": "units"}


def parse_strength(raw: str | None) -> Strength:
    """Split '500mg' into 500 and 'mg', and spot what is not a strength at all.

    Extraction copies whatever sits beside the drug name, so a tablet count
    such as '1/2' arrives in the strength field. Marking it as a count keeps a
    dose of half a tablet from being stored as a strength of 0.5 of nothing.
    """
    if not raw or not str(raw).strip():
        return Strength(raw=raw or "", reason="no strength given")

    text = unicodedata.normalize("NFKC", str(raw)).strip()

    fraction = _FRACTION.match(text)
    if fraction:
        top, bottom = (int(g) for g in fraction.groups())
        return Strength(
            raw=str(raw),
            value=top / bottom if bottom else None,
            is_count=True,
            reason="a fraction with no unit — a tablet count, not a strength",
        )

    # Extraction copies whatever sits beside the drug name, so the strength is
    # often buried in it: 'CLARITHROMYCIN IP 500MG'. Search the whole string.
    found = _STRENGTH.findall(text) or _STRENGTH_LOOSE.findall(text)
    if found:
        components = [
            {"value": _number(amount), "unit": _UNIT_ALIASES.get(unit.lower(), unit.lower())}
            for amount, unit in found
        ]
        if len(components) == 1:
            return Strength(
                raw=str(raw), value=components[0]["value"], unit=components[0]["unit"],
                reason="number with a dose unit",
            )
        # A combination product — several ingredients, several strengths. No
        # single number describes it, so they are all kept.
        return Strength(
            raw=str(raw), components=components,
            reason=f"combination of {len(components)} strengths",
        )

    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        return Strength(
            raw=str(raw), value=_number(text), is_count=True,
            reason="a bare number with no unit — probably a tablet count",
        )

    return Strength(raw=str(raw), reason="could not be read as a strength")


@dataclass
class ReferenceRange:
    raw: str
    low: float | None = None
    high: float | None = None
    operator: str | None = None
    reason: str = ""

    def flag(self, value: float | None) -> str | None:
        """'H', 'L' or 'N' — our own reading, to check the lab's printed flag."""
        if value is None:
            return None
        if self.low is not None and value < self.low:
            return "L"
        if self.high is not None and value > self.high:
            return "H"
        if self.low is None and self.high is None:
            return None
        return "N"


_RANGE = re.compile(
    r"^\s*(-?\d+(?:[.,]\d+)?)\s*(?:-|–|—|to)\s*(-?\d+(?:[.,]\d+)?)\s*$", re.IGNORECASE
)

# Analyser software prints the range inside brackets — '[0.90-1.30]', '[>60.0]'.
# Both patterns below are anchored, so without stripping these the bracket alone
# made the range unreadable. That is not a cosmetic failure: a result with no
# bounds cannot be called high or low, so it can never raise a finding and never
# reaches the referral routing. On the reports here it cost 69 of 70 readings.
_WRAPPERS = "[](){}"


def parse_range(raw: str | None) -> ReferenceRange:
    """Read '0.7 - 1.3', '[0.90-1.30]' or '> 90' into bounds we can compare against."""
    if not raw or not str(raw).strip():
        return ReferenceRange(raw=raw or "", reason="no range given")

    text = unicodedata.normalize("NFKC", str(raw)).strip().strip(_WRAPPERS).strip()

    match = _RANGE.match(text)
    if match:
        return ReferenceRange(
            raw=str(raw), low=_number(match.group(1)), high=_number(match.group(2)),
            reason="two bounds",
        )

    operator = _OPERATOR.match(text)
    if operator:
        symbol = operator.group(1).replace("≤", "<=").replace("≥", ">=")
        number = _NUMBER.search(text[operator.end():])
        if number:
            value = _number(number.group())
            bounds = {"<": ("high", value), "<=": ("high", value),
                      ">": ("low", value), ">=": ("low", value)}
            side, bound = bounds[symbol]
            return ReferenceRange(
                raw=str(raw), operator=symbol, reason="one bound",
                **{side: bound},
            )

    return ReferenceRange(raw=str(raw), reason="could not be read as a range")


# --- dosing ------------------------------------------------------------------


@dataclass
class Frequency:
    raw: str
    per_day: float | None = None
    prn: bool = False
    reason: str = ""


# Latin abbreviations still used on prescriptions everywhere
_ABBREVIATIONS = {
    "od": 1, "qd": 1, "daily": 1, "mane": 1, "nocte": 1, "hs": 1, "qhs": 1,
    "bd": 2, "bid": 2, "bds": 2,
    "tds": 3, "tid": 3, "tt": 3,
    "qid": 4, "qds": 4,
    "q4h": 6, "q6h": 4, "q8h": 3, "q12h": 2, "q24h": 1,
}
_PRN = re.compile(r"\b(prn|sos|as\s+needed|if\s+required|when\s+required)\b", re.IGNORECASE)
_SLOTS = re.compile(r"^\s*(\d+(?:\s*/\s*\d+)?)([-–](\d+(?:\s*/\s*\d+)?)){1,3}\s*$")
_LEADING_NUMBER = re.compile(r"^\s*(\d+(?:\s*/\s*\d+)?|\d+(?:[.,]\d+)?)")


def _as_number(text: str) -> float | None:
    fraction = _FRACTION.match(text)
    if fraction:
        top, bottom = (int(g) for g in fraction.groups())
        return top / bottom if bottom else None
    return _number(text)


def parse_frequency(raw: str | None) -> Frequency:
    """Work out how many doses a day, from however it was written.

    Handles the three conventions that actually appear: slot notation
    ('1-0-1'), Latin abbreviations ('BD', 'TDS'), and a list of timed doses
    ('1 Morning, 1 Night').

    The list form is read by adding up the number at the front of each part,
    which does not depend on the language: '1 सुबह, 1 दोपहर, 1 शाम, 1 रात्री'
    gives 4 the same way '1 Morning, 1 Aft, 1 Eve, 1 Night' does. The words are
    never translated — only the numbers are used.
    """
    if not raw or not str(raw).strip():
        return Frequency(raw=raw or "", reason="no frequency given")

    text = unicodedata.normalize("NFKC", str(raw)).strip()
    result = Frequency(raw=str(raw), prn=bool(_PRN.search(text)))

    core = _PRN.sub("", text).strip(" ,()")

    if _SLOTS.match(core):
        parts = [p for p in re.split(r"[-–]", core) if p.strip()]
        numbers = [_as_number(p) for p in parts]
        if all(n is not None for n in numbers):
            result.per_day = sum(numbers)
            result.reason = f"slot notation, {len(parts)} slots"
            return result

    word = re.sub(r"[^a-z0-9]", "", core.lower())
    if word in _ABBREVIATIONS:
        result.per_day = _ABBREVIATIONS[word]
        result.reason = f"'{core}' means {_ABBREVIATIONS[word]} a day"
        return result

    parts = [p.strip() for p in re.split(r"[,;+]| and ", core) if p.strip()]
    numbers = [_as_number(m.group(1).replace(" ", "")) for p in parts
               if (m := _LEADING_NUMBER.match(p))]
    if numbers and len(numbers) == len(parts) and all(n is not None for n in numbers):
        result.per_day = sum(numbers)
        result.reason = f"{len(parts)} timed doses added up"
        return result

    result.reason = "could not be read"
    return result


@dataclass
class Duration:
    raw: str
    days: float | None = None
    reason: str = ""


_DURATION_UNITS = {
    "day": 1, "days": 1, "d": 1,
    "week": 7, "weeks": 7, "wk": 7, "wks": 7, "w": 7,
    "month": 30, "months": 30, "mon": 30, "mo": 30, "m": 30,
    "year": 365, "years": 365, "yr": 365, "yrs": 365,
}
_DURATION = re.compile(r"(\d+(?:[.,]\d+)?)\s*([A-Za-z]+)")


def parse_duration(raw: str | None) -> Duration:
    """'10 days', '2 weeks', '1 month' into a number of days.

    Only English unit words are recognised. '10 दिन' returns no value rather
    than a wrong one — the raw text is still shown to the user.
    """
    if not raw or not str(raw).strip():
        return Duration(raw=raw or "", reason="no duration given")

    match = _DURATION.search(unicodedata.normalize("NFKC", str(raw)))
    if not match:
        return Duration(raw=str(raw), reason="no number and unit found")

    amount, word = _number(match.group(1)), match.group(2).lower()
    if word not in _DURATION_UNITS or amount is None:
        return Duration(raw=str(raw), reason=f"unit '{match.group(2)}' not recognised")

    return Duration(
        raw=str(raw), days=amount * _DURATION_UNITS[word],
        reason=f"{amount} x {word}",
    )


# --- applying it to a whole extraction ---------------------------------------


def _clean(obj: Any) -> dict[str, Any]:
    """A dataclass as a dict, dropping empties and making dates JSON-safe."""
    out: dict[str, Any] = {}
    for key, value in vars(obj).items():
        if value in (None, "", [], False):
            continue
        out[key] = value.isoformat() if isinstance(value, date) else value
    return out


def normalize_item(item: BaseModel) -> dict[str, Any]:
    """Parse whichever fields this kind of item happens to have."""
    out: dict[str, Any] = {}
    fields = item.model_fields.keys()

    if "strength" in fields:
        strength = parse_strength(getattr(item, "strength", None))
        if strength.value is not None or strength.reason:
            out["strength"] = _clean(strength)
    if "frequency" in fields:
        frequency = parse_frequency(getattr(item, "frequency", None))
        if frequency.per_day is not None or frequency.prn:
            out["frequency"] = _clean(frequency)
    if "duration" in fields:
        duration = parse_duration(getattr(item, "duration", None))
        if duration.days is not None:
            out["duration"] = _clean(duration)

    if "value" in fields:
        measurement = parse_measurement(
            getattr(item, "value", None), getattr(item, "unit", None)
        )
        if measurement.value is not None or measurement.text:
            out["value"] = _clean(measurement)

        if "reference_range" in fields:
            reference = parse_range(getattr(item, "reference_range", None))
            if reference.low is not None or reference.high is not None:
                out["reference_range"] = _clean(reference)
                computed = reference.flag(measurement.value)
                if computed:
                    out["computed_flag"] = computed
                    printed = (getattr(item, "flag", None) or "").strip().upper()[:1]
                    # Disagreement means one of the two was misread; worth a
                    # human eye rather than silently trusting either.
                    if printed and printed in ("H", "L", "N") and printed != computed:
                        out["flag_conflict"] = {"printed": printed, "computed": computed}

    for name in ("collected_at", "measured_at", "onset", "performed_at"):
        if name in fields:
            parsed = parse_date(getattr(item, name, None))
            if parsed.value:
                out[name] = _clean(parsed)

    return out


def _walk(value: Any, path: str, out: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, BaseModel):
        parsed = normalize_item(value)
        if parsed and path:
            out[path] = parsed
        for name, child in value:
            _walk(child, f"{path}.{name}" if path else name, out)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk(child, f"{path}[{index}]", out)


def normalize_extraction(data: BaseModel) -> dict[str, dict[str, Any]]:
    """Every parseable field of a filled-in form, keyed by the same paths
    ground.py uses, so a value and its verification line up."""
    out: dict[str, dict[str, Any]] = {}
    _walk(data, "", out)
    return out


# Where each kind of document keeps the date that actually matters. A lab
# report is filed under when the sample was taken, not when it was typed up.
_DATE_FIELDS = {
    "LabReportExtraction": ("collected_at", "reported_at"),
    "DischargeSummaryExtraction": ("discharge_date", "admission_date"),
}


def document_date(data: BaseModel) -> ParsedDate:
    """The one date this document should sit under on the timeline."""
    for name in _DATE_FIELDS.get(type(data).__name__, ()):
        parsed = parse_date(getattr(data, name, None))
        if parsed.value:
            return parsed

    parsed = parse_date(getattr(data.header, "document_date", None))
    if parsed.value:
        return parsed

    # Last resort: the earliest date found anywhere in the document.
    candidates = [
        p for values in normalize_extraction(data).values()
        for key, value in values.items()
        if key in ("collected_at", "measured_at", "performed_at", "onset")
        and (p := parse_date(value.get("raw"))).value
    ]
    if candidates:
        best = min(candidates, key=lambda p: p.value)
        best.reason = f"earliest date found in the document ({best.reason})"
        return best

    return ParsedDate(raw="", reason="no date anywhere in the document")
