"""The two checks that look at measurements over time.

    lab_trend       a value moving the wrong way across several readings
    critical_value  a single result far enough outside its range to act on

Both work on numbers, which is the whole reason normalize.py exists. A result
kept as the string '1.8' can be shown but not compared; parsed into 1.8 with a
range of 0.7-1.3, it can be put on a line and watched.
"""

import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from app.checks.base import Candidate
from app.db.models import TimelineEvent
from app.labs import canonical

logger = logging.getLogger(__name__)

# Two readings are two readings. Three is the least that can show a direction.
MIN_TREND_POINTS = 3

# How much a value has to move before the movement is worth mentioning, as a
# share of the normal range. Small wobbles between tests are normal and
# flagging them would bury the real changes.
MIN_TREND_SHIFT = 0.5

# How far outside its range a single result has to be before it stops being
# 'abnormal' and becomes 'ring someone'. Expressed against the width of the
# normal range, so it adapts to the analyte instead of being one fixed number.
CRITICAL_MULTIPLE = 1.0


def _numeric(events: list[TimelineEvent]) -> list[TimelineEvent]:
    """Results that can actually be compared.

    An operator such as '<' disqualifies a value: '<0.01' means the assay could
    not measure it, and plotting it as 0.01 would invent a precision the lab
    explicitly declined to claim.
    """
    return [
        event
        for event in events
        if event.event_type in ("lab_result", "vital_sign")
        and event.review_status != "rejected"
        and event.value_num is not None
        and event.operator is None
        and event.event_date is not None
        and (event.analyte or "").strip()
    ]


def check_lab_trends(db: Session, events: list[TimelineEvent]) -> list[Candidate]:
    """A measurement drifting the wrong way over three or more readings."""
    # Grouped by the same rule the Lab Trends page uses, so a trend the reader
    # can see on a chart is a trend this check can raise. Lowercasing alone
    # split 'BP' from 'Blood Pressure' into two short histories, and a series
    # cut in half never reaches the three readings a direction needs.
    by_analyte: dict[str, list[TimelineEvent]] = defaultdict(list)
    for event in _numeric(events):
        by_analyte[canonical(event.analyte)].append(event)

    found: list[Candidate] = []

    for analyte, readings in by_analyte.items():
        if len(readings) < MIN_TREND_POINTS:
            continue

        readings.sort(key=lambda e: e.event_date)
        first, last = readings[0], readings[-1]

        low = last.ref_low if last.ref_low is not None else first.ref_low
        high = last.ref_high if last.ref_high is not None else first.ref_high
        if low is None and high is None:
            continue

        # Only a value that has *ended up* outside its range is worth raising.
        # A number moving about inside the normal range is not a finding.
        outside = (high is not None and last.value_num > high) or (
            low is not None and last.value_num < low
        )
        if not outside:
            continue

        width = float(high - low) if (low is not None and high is not None) else None
        moved = float(last.value_num - first.value_num)
        if width and abs(moved) < width * MIN_TREND_SHIFT:
            continue

        worsening = (high is not None and moved > 0) or (low is not None and moved < 0)
        if not worsening:
            continue

        direction = "rising" if moved > 0 else "falling"
        name = (last.analyte or analyte).strip()
        severity = "warning" if abs(moved) >= (width or abs(moved)) else "info"

        found.append(
            Candidate(
                key=f"trend:{analyte}",
                finding_type="lab_trend",
                severity=severity,
                title=f"{name} has been {direction} and is now outside the normal range",
                evidence={
                    "analyte": name,
                    "unit": last.unit,
                    "readings": [
                        {"date": str(e.event_date), "value": float(e.value_num)}
                        for e in readings
                    ],
                    "reference_range": {
                        "low": float(low) if low is not None else None,
                        "high": float(high) if high is not None else None,
                    },
                    "direction": direction,
                    "change": round(moved, 3),
                },
                event_ids=[e.id for e in readings],
                visit_ids=list({e.visit_id for e in readings}),
            )
        )

    return found


def check_critical_values(db: Session, events: list[TimelineEvent]) -> list[Candidate]:
    """A single result far enough outside its range to need attention now.

    Distinguished from merely abnormal by how far out it is, measured against
    the width of the normal range. Creatinine of 1.4 against 0.7-1.3 is
    abnormal and already visible on the timeline; 3.0 is a different
    conversation.
    """
    found: list[Candidate] = []

    for event in _numeric(events):
        low, high = event.ref_low, event.ref_high
        if low is None or high is None:
            continue

        width = float(high - low)
        if width <= 0:
            continue

        value = float(event.value_num)
        if value > float(high):
            excess = (value - float(high)) / width
            side = "above"
        elif value < float(low):
            excess = (float(low) - value) / width
            side = "below"
        else:
            continue

        if excess < CRITICAL_MULTIPLE:
            continue

        name = (event.analyte or "").strip()
        found.append(
            Candidate(
                key=f"critical:{name.lower()}:{event.id}",
                finding_type="critical_value",
                severity="danger",
                title=f"{name} of {value:g} is well {side} the normal range",
                evidence={
                    "analyte": name,
                    "value": value,
                    "unit": event.unit,
                    "reference_range": {"low": float(low), "high": float(high)},
                    "date": str(event.event_date),
                    "how_far": f"{excess:.1f} times the width of the normal range {side} it",
                },
                event_ids=[event.id],
                visit_ids=[event.visit_id],
            )
        )

    return found
